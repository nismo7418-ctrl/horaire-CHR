"""Tests arbitre.py — boucle d'arbitrage P3 (fonctions pures, testables sans Streamlit/LLM).

Lance : python test_arbitre.py

Cas couverts :
  (1) fusion_extras         : un seul poste forcé par (agent, date) — la 2e liste gagne ;
  (2) verifier_propositions : KO plancher (repos), KO rôle inéligible (échange D7),
                              KO type inconnu, OK repos + OK échange → extras construits ;
  (3) arbitrer — LLM lève   : premier résultat conservé, erreur documentée ;
  (4) arbitrer — 0 prop     : premier résultat conservé, aucune erreur ;
  (5) arbitrer — refus      : proposition refusée (plancher d'effectif) → premier résultat,
                              extras initiaux inchangés, motif dans erreurs ;
  (6) arbitrer — re-solve   : proposition valide → re-solve, extras fusionnés,
                              agent bien au repos dans la grille finale ;
  (7) arbitrer — infeasible : re-solve infeasible (solveur mocké) → premier résultat
                              conservé, extras initiaux inchangés.

(1)–(2) et (7) sont synthétiques (aucun solve réel). (3)–(6) font un solve réel
chaque (config.MOIS_DEFAUT) — ~30 s/solve. Les cas (3)–(6) forcent des
souhaits_non_honores non vides via un souhait structurellement infaisable
(D7 : aide_soignant ne peut jamais tenir N) — l'appel LLM est donc systématique.
"""
import json
import sys

sys.stdout.reconfigure(encoding="utf-8")

import arbitre
import config
import solveur

MOIS = config.MOIS_DEFAUT  # 2026-10
D = "2026-10-15"  # jeudi, dans le mois


def charger():
    with open("data/personnel.json", encoding="utf-8") as f:
        personnel = json.load(f)
    with open("data/effectifs_min.json", encoding="utf-8") as f:
        effectifs_min = json.load(f)
    return personnel, effectifs_min


def desiderata_snh(personnel: list) -> dict:
    """Souhaits non honorés garantis : aide_soignant qui veut N (D7 — interdit dur)."""
    aso = next(p for p in personnel if p["role"] == "aide_soignant")
    return {aso["nom"]: {
        "structurees": [{"categorie": "souhait_positif", "date": D, "poste_concerne": "N",
                         "priorite": "haute", "motif_resume": "test — N interdit à ce rôle"}],
        "a_clarifier": [], "conflits_detectes": [],
    }}


# ── (1) fusion_extras ─────────────────────────────────────────────────────
def test_fusion_extras():
    ex = [{"agent": "A", "date": D, "code": "M"}, {"agent": "B", "date": D, "code": "S"}]
    nou = [{"agent": "A", "date": D, "code": "S"}, {"agent": "C", "date": "2026-10-16", "code": "N"}]
    res = arbitre.fusion_extras(ex, nou)
    par = {(e["agent"], e["date"]): e["code"] for e in res}
    assert par[("A", D)] == "S", par          # conflit → la 2e liste gagne
    assert par[("B", D)] == "S", par          # distinct → conservé
    assert par[("C", "2026-10-16")] == "N", par
    assert len(res) == 3, res
    # listes vides / None
    assert arbitre.fusion_extras(None, None) == []
    print("(1) fusion_extras : OK")


# ── (2) verifier_propositions ─────────────────────────────────────────────
def test_verifier_propositions():
    personnel = [
        {"nom": "A", "role": "siamu", "affectations_fixes": []},
        {"nom": "B", "role": "siamu", "affectations_fixes": []},
    ]
    em = {
        "_groupes_roles": {"infirmier_tous": ["siamu"]},
        "_groupes_postes": {"jour": ["M", "S", "12"]},
        "infirmier_tous": {"jour": 1},
    }
    # KO : A seul du groupe « jour » (plancher 1) ; inconnu ; type inconnu
    grille = {D: {"A": "M"}}
    extras, erreurs = arbitre.verifier_propositions(
        MOIS, personnel, em, grille,
        [{"type": "repos", "agent": "A", "date": D},
         {"type": "repos", "agent": "Z", "date": D},
         {"type": "teleportation", "agent": "A"}])
    assert extras == [], extras
    assert len(erreurs) == 3, erreurs
    assert any("Plancher" in e for e in erreurs), erreurs
    assert any("inconnu" in e.lower() for e in erreurs), erreurs

    # OK : repos (2 sur M, plancher 1) + échange (M ↔ S)
    grille2 = {D: {"A": "M", "B": "S"}}
    extras, erreurs = arbitre.verifier_propositions(
        MOIS, personnel, em, grille2,
        [{"type": "repos", "agent": "A", "date": D},
         {"type": "echange", "agent_a": "A", "agent_b": "B", "date": D}])
    assert erreurs == [], erreurs
    # Ordre : d'abord le repos de A, puis l'échange (A→S, B→M). Si les deux
    # propositions portent sur (A, D), la fusion ultérieure (fusion_extras) garde
    # la dernière — ici on vérifie la liste brute, non fusionnée.
    assert extras == [{"agent": "A", "date": D, "code": "repos"},
                      {"agent": "A", "date": D, "code": "S"},
                      {"agent": "B", "date": D, "code": "M"}], extras
    print("(2) verifier_propositions : OK")


# ── (3) arbitrer — LLM indisponible ───────────────────────────────────────
def test_arbitrer_llm_leve():
    personnel, em = charger()
    def proposer(snh, grille):
        raise RuntimeError("LM Studio injoignable (simulé)")
    res, extras, erreurs = arbitre.arbitrer(
        MOIS, personnel, em, desiderata_snh(personnel), [],
        config.TIME_LIMIT_S, proposer)
    assert res["statut"] in ("OPTIMAL", "FEASIBLE"), res["statut"]
    assert res["souhaits_non_honores"], "le test exige des souhaits non honorés"
    assert extras == [], extras
    assert erreurs and "indisponibles" in erreurs[0].lower(), erreurs
    print("(3) arbitrer LLM indisponible : OK —", erreurs[0][:80])


# ── (4) arbitrer — aucune proposition ─────────────────────────────────────
def test_arbitrer_0_propositions():
    personnel, em = charger()
    res, extras, erreurs = arbitre.arbitrer(
        MOIS, personnel, em, desiderata_snh(personnel), [],
        config.TIME_LIMIT_S, lambda snh, grille: {"propositions": []})
    assert res["statut"] in ("OPTIMAL", "FEASIBLE"), res["statut"]
    assert extras == [] and erreurs == [], (extras, erreurs)
    print("(4) arbitrer 0 propositions : OK")


# ── (5) arbitrer — proposition refusée (plancher) ─────────────────────────
def test_arbitrer_refus_plancher():
    personnel, em = charger()
    # Candidat choisi dans LA grille que `arbitrer` transmet (son propre premier solve —
    # non déterministe entre deux runs) : premier repos infeasible au plancher selon
    # le vérificateur. (Le solve est ici le seul solve du cas.)
    cible: dict = {}
    def proposer(snh, grille):
        for d_iso in sorted(grille):
            for nom, poste in (grille[d_iso] or {}).items():
                if not poste or poste in solveur.REPOS_CODES:
                    continue
                ok, errs = solveur.verifier_repos(MOIS, personnel, em, grille, nom, d_iso)
                if not ok and any("Plancher" in e for e in errs):
                    cible["c"] = (nom, d_iso)
                    return {"propositions": [{"type": "repos", "agent": nom, "date": d_iso}]}
        return {"propositions": []}
    res, extras, erreurs = arbitre.arbitrer(
        MOIS, personnel, em, desiderata_snh(personnel), [],
        config.TIME_LIMIT_S, proposer)
    assert res["statut"] in ("OPTIMAL", "FEASIBLE"), res["statut"]
    assert cible, "aucun repos infeasible (plancher) trouvé — le test exige un refus"
    assert extras == [], extras
    assert erreurs and any("Plancher" in e for e in erreurs), erreurs
    print(f"(5) arbitrer refus plancher : OK — {erreurs[0][:80]}")


# ── (6) arbitrer — re-solve avec repos valide ─────────────────────────────
def test_arbitrer_re_solve_repos():
    personnel, em = charger()
    # Premier (agent, jour) libérable selon le vérificateur, dans la grille que
    # `arbitrer` transmet (son propre premier solve — unique solve du cas).
    cible: dict = {}
    def proposer(snh, grille):
        for d_iso in sorted(grille):
            for nom, poste in (grille[d_iso] or {}).items():
                if not poste or poste in solveur.REPOS_CODES:
                    continue
                if solveur.verifier_repos(MOIS, personnel, em, grille, nom, d_iso)[0]:
                    cible["c"] = (nom, d_iso)
                    return {"propositions": [{"type": "repos", "agent": nom, "date": d_iso}]}
        return {"propositions": []}
    res, extras, erreurs = arbitre.arbitrer(
        MOIS, personnel, em, desiderata_snh(personnel), [],
        config.TIME_LIMIT_S, proposer)
    assert res["statut"] in ("OPTIMAL", "FEASIBLE"), res["statut"]
    assert cible, "aucun repos libérable trouvé — le test exige un re-solve"
    nom, d_iso = cible["c"]
    assert erreurs == [], erreurs
    assert {"agent": nom, "date": d_iso, "code": "repos"} in extras, extras
    poste_finale = (res["grille"].get(d_iso) or {}).get(nom, "ABSENT")
    assert poste_finale in ("", None) or poste_finale in solveur.REPOS_CODES, \
        f"{nom} n'est pas au repos le {d_iso} : poste = '{poste_finale}'"
    print(f"(6) arbitrer re-solve repos : OK ({nom} au repos le {d_iso})")


# ── (7) arbitrer — re-solve infeasible (solveur mocké, aucun solve réel) ──
def test_arbitrer_re_solve_infeasible():
    personnel = [
        {"nom": "A", "role": "siamu", "affectations_fixes": []},
        {"nom": "B", "role": "siamu", "affectations_fixes": []},
    ]
    em = {
        "_groupes_roles": {"infirmier_tous": ["siamu"]},
        "_groupes_postes": {"jour": ["M", "S", "12"]},
        "infirmier_tous": {"jour": 1},
    }
    premiers = {"statut": "FEASIBLE",
                "grille": {D: {"A": "M", "B": "M"}},
                "souhaits_non_honores": [{"agent": "A", "date": D, "categorie": "souhait_positif",
                                          "poste_concerne": "S", "priorite": "haute",
                                          "situation": "test", "motif": "test"}],
                "info_solveur": "mock"}
    infeasible = {"statut": "INFISAT", "grille": {}, "souhaits_non_honores": [],
                  "info_solveur": "mock"}
    appels = {"n": 0}
    def fake_resoudre(**kw):
        appels["n"] += 1
        return premiers if appels["n"] == 1 else infeasible

    init = [{"agent": "X", "date": "2026-10-01", "code": "M"}]
    old = solveur.resoudre
    solveur.resoudre = fake_resoudre
    try:
        res, extras, erreurs = arbitre.arbitrer(
            MOIS, personnel, em, {}, list(init), config.TIME_LIMIT_S,
            lambda snh, grille: {"propositions": [{"type": "repos", "agent": "A", "date": D}]})
    finally:
        solveur.resoudre = old
    assert appels["n"] == 2, appels
    assert res is premiers, "le premier résultat doit être conservé"
    assert extras == init, f"extras initiaux inchangés attendus, obtenu : {extras}"
    assert erreurs and any("infeasible" in e for e in erreurs), erreurs
    print("(7) arbitrer re-solve infeasible : OK —", erreurs[0][:80])


if __name__ == "__main__":
    test_fusion_extras()
    test_verifier_propositions()
    test_arbitrer_llm_leve()
    test_arbitrer_0_propositions()
    test_arbitrer_refus_plancher()
    test_arbitrer_re_solve_repos()
    test_arbitrer_re_solve_infeasible()
    print("\nTESTS ARBITRE : OK (1–7)")
