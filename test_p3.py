"""Tests P3 — boucle d'arbitrage : vérification déterministe + re-solve avec contraintes_extras.

Lance : python test_p3.py

Cas couverts (handoff P3) :
  (a) verifier_repos      : KO plancher (agent unique du poste), KO déjà au repos,
                            KO date hors mois, KO agent inconnu, KO affectation fixe (D2),
                            OK (2 sur le poste, plancher 1) ;
  (d) verifier_echange    : KO même poste, KO un agent au repos, KO rôle inéligible (D7),
                            KO affectation fixe (D2), KO repos < 11h avec le jour voisin (D5,
                            via seuil relevé à 1000 min — les horaires réels donnent ≥ 934 min),
                            OK (M ↔ S, plancher 1, 2 agents) ;
  (b) re-solve repos      : contraintes_extras=[{agent, date, code:"repos"}] → agent au
                            repos ce jour-là dans la grille ;
  (c) re-solve échange    : 2 entrées croisées (a→poste de b, b→poste de a) → les deux
                            postes sont bien échangés dans la grille.

(a)/(d) sont synthétiques (aucun solve). (b)/(c) font un solve de base puis un re-solve
chaque (~30 s / solve).
"""
import json
import sys

sys.stdout.reconfigure(encoding="utf-8")

import config
import solveur

MOIS = config.MOIS_DEFAUT  # 2026-10


def charger():
    with open("data/personnel.json", encoding="utf-8") as f:
        personnel = json.load(f)
    with open("data/effectifs_min.json", encoding="utf-8") as f:
        effectifs_min = json.load(f)
    return personnel, effectifs_min


# ── (a) verifier_repos ─────────────────────────────────────────────────────
def test_verifier_repos():
    personnel = [
        {"nom": "A", "role": "siamu", "affectations_fixes": []},
        {"nom": "B", "role": "siamu", "affectations_fixes": []},
    ]
    em = {
        "_groupes_roles": {"infirmier_tous": ["siamu"]},
        "_groupes_postes": {"jour": ["M", "S", "12"]},
        "infirmier_tous": {"jour": 1},
    }
    D = "2026-10-15"

    # KO : A est le seul du poste M ce jour (plancher 1)
    grille = {D: {"A": "M"}}
    ok, errs = solveur.verifier_repos(MOIS, personnel, em, grille, "A", D)
    assert not ok and any("Plancher" in e for e in errs), (ok, errs)

    # KO : déjà au repos
    ok, errs = solveur.verifier_repos(MOIS, personnel, em, grille, "B", D)
    assert not ok and any("déjà au repos" in e for e in errs), (ok, errs)

    # KO : date hors du mois
    ok, errs = solveur.verifier_repos(MOIS, personnel, em, grille, "A", "2026-11-01")
    assert not ok, (ok, errs)

    # KO : agent inconnu
    ok, errs = solveur.verifier_repos(MOIS, personnel, em, grille, "Z", D)
    assert not ok, (ok, errs)

    # OK : 2 sur M avec plancher 1 → libérer l'un d'eux est possible
    grille2 = {D: {"A": "M", "B": "M"}}
    ok, errs = solveur.verifier_repos(MOIS, personnel, em, grille2, "A", D)
    assert ok and not errs, (ok, errs)

    # KO : affectation fixe (D2)
    personnel[0]["affectations_fixes"] = [{"date": D, "code": "M"}]
    ok, errs = solveur.verifier_repos(MOIS, personnel, em, grille2, "A", D)
    assert not ok and any("affectation fixe" in e for e in errs), (ok, errs)
    personnel[0]["affectations_fixes"] = []

    print("(a) verifier_repos : OK")


# ── (d) verifier_echange ───────────────────────────────────────────────────
def test_verifier_echange():
    personnel = [
        {"nom": "A", "role": "siamu", "affectations_fixes": []},
        {"nom": "B", "role": "siamu", "affectations_fixes": []},
    ]
    em = {
        "_groupes_roles": {"infirmier_tous": ["siamu"]},
        "_groupes_postes": {"jour": ["M", "S", "12"]},
        "infirmier_tous": {"jour": 1},
    }
    D = "2026-10-15"

    # KO : même poste pour les deux
    ok, errs = solveur.verifier_echange(MOIS, personnel, em, {D: {"A": "M", "B": "M"}}, "A", "B", D)
    assert not ok and any("Même poste" in e for e in errs), (ok, errs)

    # KO : un des deux est au repos
    ok, errs = solveur.verifier_echange(MOIS, personnel, em, {D: {"A": "M", "B": ""}}, "A", "B", D)
    assert not ok and any("repos" in e for e in errs), (ok, errs)

    # KO : rôle inéligible (D7) — aide_soignant ne peut pas prendre l'IC
    perso_d7 = [
        {"nom": "A", "role": "siamu", "affectations_fixes": []},
        {"nom": "B", "role": "aide_soignant", "affectations_fixes": []},
    ]
    ok, errs = solveur.verifier_echange(MOIS, perso_d7, em, {D: {"A": "IC", "B": "N"}}, "A", "B", D)
    assert not ok and any("inaccessible" in e for e in errs), (ok, errs)

    # KO : affectation fixe (D2)
    personnel[0]["affectations_fixes"] = [{"date": D, "code": "M"}]
    ok, errs = solveur.verifier_echange(MOIS, personnel, em, {D: {"A": "M", "B": "S"}}, "A", "B", D)
    assert not ok and any("affectation fixe" in e for e in errs), (ok, errs)
    personnel[0]["affectations_fixes"] = []

    # KO D5 (seuil par défaut 660) : 12h (9h→21h) suivie de M (6h54) le lendemain
    # → repos = 1440+414-1260 = 594 min < 11h → refus.
    grille_d5 = {"2026-10-01": {"A": "12"}, "2026-10-02": {"A": "S", "B": "M"}}
    D5 = "2026-10-02"
    ok, errs = solveur.verifier_echange(MOIS, personnel, em, grille_d5, "A", "B", D5)
    assert not ok and any("repos" in e for e in errs), (ok, errs)

    # D5 seuil : M du lun 05/10 (6h54→15h00) suivie de S (13h15) le mar 06/10
    # → repos = 1440+795-900 = 1335 min. Seuil 660 → OK ; seuil 1500 → refus.
    grille_d5b = {"2026-10-05": {"A": "M"}, "2026-10-06": {"A": "M", "B": "S"}}
    D5b = "2026-10-06"
    ok, errs = solveur.verifier_echange(MOIS, personnel, em, grille_d5b, "A", "B", D5b)
    assert ok and not errs, (ok, errs)
    ancien = config.REPOS_QUOTIDIEN_H
    try:
        config.REPOS_QUOTIDIEN_H = 1500
        ok, errs = solveur.verifier_echange(MOIS, personnel, em, grille_d5b, "A", "B", D5b)
        assert not ok and any("repos" in e for e in errs), (ok, errs)
    finally:
        config.REPOS_QUOTIDIEN_H = ancien

    # OK : M ↔ S, plancher 1 (2 agents sur des postes du groupe), sans voisin problématique
    ok, errs = solveur.verifier_echange(MOIS, personnel, em, {D: {"A": "M", "B": "S"}}, "A", "B", D)
    assert ok and not errs, (ok, errs)

    print("(d) verifier_echange : OK")


# ── (b) re-solve avec repos forcé ──────────────────────────────────────────
def test_re_solve_repos():
    personnel, em = charger()
    r1 = solveur.resoudre(mois=MOIS, personnel=personnel, effectifs_min=em,
                          desiderata={}, time_limit_s=config.TIME_LIMIT_S)
    assert r1["statut"] in ("OPTIMAL", "FEASIBLE"), (r1["statut"], r1.get("info_solveur"))
    grille = r1["grille"]

    # Premier (agent, jour) libérable selon le vérificateur déterministe.
    cible = None
    for d_iso in sorted(grille):
        for nom, poste in (grille[d_iso] or {}).items():
            if not poste or poste in solveur.REPOS_CODES:
                continue
            ok, _ = solveur.verifier_repos(MOIS, personnel, em, grille, nom, d_iso)
            if ok:
                cible = (nom, d_iso)
                break
        if cible:
            break
    assert cible, "aucun candidat « repos » valide trouvé dans la grille de base"
    nom, d_iso = cible
    print(f"(b) repos forcé : {nom} le {d_iso} (poste de base : {grille[d_iso].get(nom)})")

    r2 = solveur.resoudre(
        mois=MOIS, personnel=personnel, effectifs_min=em, desiderata={},
        contraintes_extras=[{"agent": nom, "date": d_iso, "code": "repos"}],
        time_limit_s=config.TIME_LIMIT_S)
    assert r2["statut"] in ("OPTIMAL", "FEASIBLE"), (r2["statut"], r2.get("info_solveur"))
    poste_finale = (r2["grille"].get(d_iso) or {}).get(nom, "ABSENT")
    assert poste_finale in ("", None) or poste_finale in solveur.REPOS_CODES, \
        f"{nom} n'est pas au repos le {d_iso} : poste = '{poste_finale}'"
    print(f"(b) re-solve repos : OK ({nom} au repos le {d_iso})")


# ── (c) re-solve avec échange ──────────────────────────────────────────────
def test_re_solve_echange():
    personnel, em = charger()
    r1 = solveur.resoudre(mois=MOIS, personnel=personnel, effectifs_min=em,
                          desiderata={}, time_limit_s=config.TIME_LIMIT_S)
    assert r1["statut"] in ("OPTIMAL", "FEASIBLE"), (r1["statut"], r1.get("info_solveur"))
    grille = r1["grille"]

    # Première paire (a, b) échangeable selon le vérificateur déterministe.
    echange = None
    for d_iso in sorted(grille):
        lignes = grille.get(d_iso) or {}
        postes = {nom: p for nom, p in lignes.items() if p and p not in solveur.REPOS_CODES}
        noms = sorted(postes)
        for i in range(len(noms)):
            for j in range(i + 1, len(noms)):
                a, b = noms[i], noms[j]
                if postes[a] == postes[b]:
                    continue
                ok, _ = solveur.verifier_echange(MOIS, personnel, em, grille, a, b, d_iso)
                if ok:
                    echange = (a, b, d_iso, postes[a], postes[b])
                    break
            if echange:
                break
        if echange:
            break
    assert echange, "aucune paire « échange » valide trouvée dans la grille de base"
    a, b, d_iso, pa, pb = echange
    print(f"(c) échange forcé : {a} ({pa}) ↔ {b} ({pb}) le {d_iso}")

    r3 = solveur.resoudre(
        mois=MOIS, personnel=personnel, effectifs_min=em, desiderata={},
        contraintes_extras=[
            {"agent": a, "date": d_iso, "code": pb},
            {"agent": b, "date": d_iso, "code": pa},
        ],
        time_limit_s=config.TIME_LIMIT_S)
    assert r3["statut"] in ("OPTIMAL", "FEASIBLE"), (r3["statut"], r3.get("info_solveur"))
    g3 = r3["grille"].get(d_iso) or {}
    assert g3.get(a) == pb, f"{a} attendu sur {pb}, grille : '{g3.get(a)}'"
    assert g3.get(b) == pa, f"{b} attendu sur {pa}, grille : '{g3.get(b)}'"
    print(f"(c) re-solve échange : OK ({a}→{pb}, {b}→{pa})")


if __name__ == "__main__":
    test_verifier_repos()
    test_verifier_echange()
    test_re_solve_repos()
    test_re_solve_echange()
    print("\nTESTS P3 : OK (a, d unitaires + b, c intégration)")
