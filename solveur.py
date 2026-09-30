"""Solveur CP-SAT — planification du service des urgences.

N'importe quelle valeur métier vient de config.py. Entrées :
  mois          "2026-10"
  personnel     liste de dicts (voir data/personnel.json)
  effectifs_min {"siamu": {"M": 3, "S": 3, "N": 2}, ...}
  desiderata    {"initiales": {"structurees": [...], "a_clarifier": [...]}}  (ex: "P.K.")
                (sortie du Prompt 1, champ "desiderata_structurees")

Sortie : dict {statut, grille, heures, stats, warnings, info_solveur}
Le solveur ne fait JAMAIS appel au LLM.
"""
from __future__ import annotations

import calendar
from datetime import date

from ortools.sat.python import cp_model

import config


class ErreurConfig(Exception):
    """Config incohérente (horaires, effectifs, codes) — à corriger avant résolution."""


# ── Aides ───────────────────────────────────────────────────────────────

def jours_du_mois(mois: str) -> list[date]:
    parts = mois.split("-")[:2]
    try:
        an, m = int(parts[0]), int(parts[1])
    except (IndexError, ValueError):
        raise ErreurConfig(f"Mois invalide : '{mois}' (attendu : AAAA-MM, ex '2026-10').")
    if not (1 <= m <= 12 and 1900 <= an <= 2100):
        raise ErreurConfig(f"Mois invalide : '{mois}' (attendu : AAAA-MM, ex '2026-10').")
    nb = calendar.monthrange(an, m)[1]
    return [date(an, m, d) for d in range(1, nb + 1)]


JOUR_MIN = 1440  # minutes dans une journée
TRANCHE_NUIT = (21 * 60, 30 * 60)  # 21h→6h, en minutes


def _duree(code: str, jour) -> int:
    """Durée du poste en minutes (entier, compatible CP-SAT). Dépend du jour pour N."""
    s, e = config.horaires_poste(code, jour)
    return e - s


def _tranche_nuit(code: str, jour) -> int:
    """Part du poste qui tombe dans la tranche légale 21h-6h (minutes)."""
    s, f = config.horaires_poste(code, jour)
    a, b = max(s, TRANCHE_NUIT[0]), min(f, TRANCHE_NUIT[1])
    return max(0, b - a)


def _repos_entre(c1: str, jour_d, c2: str, jour_d1) -> int:
    """Minutes de repos entre la fin du poste c1 (jour d) et le début de c2 (jour d+1)."""
    fin_d = config.horaires_poste(c1, jour_d)[1]
    deb_d1 = JOUR_MIN + config.horaires_poste(c2, jour_d1)[0]
    return deb_d1 - fin_d


def _postes_autorises(role) -> set[str] | None:
    """Postes autorisés pour un rôle. None = tous les postes."""
    m = config.POSTES_AUTORISES_PAR_ROLE.get(role)
    return None if m is None else set(m)


def _postes_derogables(role) -> set[str]:
    """Postes dérogables pour un rôle (interdits par défaut, autorisés moyennant pénalité)."""
    return set(config.POSTES_DEROGABLES_PAR_ROLE.get(role, []))


def _postes_accesibles(role):
    """Postes accessibles à un rôle : autorisés (durs) + dérogables (souples).
    None = tous les postes. Sert aux affectations explicites (D2/D3/contraintes extra)."""
    autorises = _postes_autorises(role)
    if autorises is None:
        return None
    return autorises | _postes_derogables(role)


def _normaliser_poste(poste) -> str | None:
    """Mappe les libellés du Prompt 1 ('matin', '12h', ...) vers les codes config.POSTES."""
    if not poste:
        return None
    if poste in config.POSTES:
        return poste
    return {"matin": "M", "soir": "S", "nuit": "N", "12h": "12", "ic": "IC",
            "infirmiere_en_chef": "IC"}.get(poste.lower())


def _effectif_du_jour(n, jour) -> int:
    """Effectif minimum du jour : int (tous les jours) ou {'semaine': a, 'weekend': b}."""
    if isinstance(n, dict):
        cle = "weekend" if jour.weekday() >= 5 else "semaine"
        return int(n.get(cle, 0))
    return int(n)


def _effectif_valide(n) -> bool:
    """True si n est un entier >= 0 ou {'semaine': int >= 0, 'weekend': int >= 0}."""
    if isinstance(n, bool):
        return False
    if isinstance(n, int):
        return n >= 0
    if isinstance(n, dict) and n:
        return all(
            k in ("semaine", "weekend") and isinstance(v, int) and not isinstance(v, bool) and v >= 0
            for k, v in n.items())
    return False


def avertissements_planchers(effectifs_min: dict, personnel: list) -> list[str]:
    """Contrôle planchers vs effectif réel par groupe de rôles — SOURCE UNIQUE.

    Partagée par le solveur (`_valider_config`) et l'UI (app.py) pour éviter deux
    implémentations divergentes (ex: gestion du `n_max` boolien).
      - effectif < plancher → « a priori infaisable » ;
      - effectif = plancher → « aucune marge » (un congé suffit à casser la journée).
    Les valeurs invalides (bool, négatif, dict partiel) sont ignorées ici :
    `_valider_config` les signale en ErreurConfig avant la résolution.
    """
    avertissements: list[str] = []
    groupes_roles = effectifs_min.get("_groupes_roles", {})
    for cle, postes in effectifs_min.items():
        if str(cle).startswith("_"):
            continue  # métadonnées (_note, _historique, _groupes_*)
        roles_cle = groupes_roles.get(cle, [cle])
        n_agents = sum(1 for p in personnel if p.get("role") in roles_cle)
        for cle_poste, n in postes.items():
            if not _effectif_valide(n):
                continue
            n_max = max(n.values()) if isinstance(n, dict) else n
            if n_max <= 0:
                continue
            if n_agents < n_max:
                avertissements.append(
                    f"Plancher « {cle} / {cle_poste} = {n} » : seulement {n_agents} agent(s) "
                    f"dans les rôles {roles_cle} — a priori infaisable.")
            elif n_agents == n_max:
                avertissements.append(
                    f"Plancher « {cle} / {cle_poste} = {n} » = effectif total du groupe — "
                    f"aucune marge (un congé suffit à rendre la journée infaisable).")
    return avertissements


def _valider_config(effectifs_min: dict, personnel: list, jours: list[date]) -> list[str]:
    roles_connus = {p.get("role") for p in personnel}
    groupes_roles = effectifs_min.get("_groupes_roles", {})
    groupes_postes = effectifs_min.get("_groupes_postes", {})
    for cle, postes in effectifs_min.items():
        if str(cle).startswith("_"):
            continue  # métadonnées (_note, _historique, _groupes_*)
        roles_cle = groupes_roles.get(cle, [cle])
        for r in roles_cle:
            if r not in config.ROLES:
                raise ErreurConfig(f"Rôle inconnu dans effectifs_min : '{r}' (groupe '{cle}', attendu : {config.ROLES})")
        for cle_poste, n in postes.items():
            if not _effectif_valide(n):
                raise ErreurConfig(
                    f"Effectif minimum invalide « {cle}/{cle_poste} » : {n!r} "
                    f"(attendu : entier >= 0, ou {{'semaine': n, 'weekend': n}}).")
            codes_poste = groupes_postes.get(cle_poste, [cle_poste])
            for code in codes_poste:
                if code not in config.POSTES:
                    raise ErreurConfig(
                        f"Code poste inconnu '{code}' (groupe '{cle_poste}') pour '{cle}' "
                        f"(attendu : {list(config.POSTES)})")
    for p in personnel:
        if p.get("role") not in config.ROLES:
            raise ErreurConfig(f"Rôle inconnu pour '{p.get('nom')}' : '{p.get('role')}'")
    for code in config.POSTES:
        if code in config.POSTES_EXEMPTS_NUIT:
            continue
        # Vérifier chaque jour du mois : certains postes (ex: nuits de jours fériés)
        # ont des horaires qui varient d'un jour à l'autre.
        for j in jours:
            s, f = config.horaires_poste(code, j)
            tranche = max(0, min(f, TRANCHE_NUIT[1]) - max(s, TRANCHE_NUIT[0]))
            if tranche > config.NIGHT_HOURS_MAX:
                raise ErreurConfig(
                    f"Poste '{code}' le {j.isoformat()} : inclut {tranche // 60}h entre 21h et 6h "
                    f"(> {config.NIGHT_HOURS_MAX // 60}h, loi 17/02/1997). "
                    f"Soit ajuster les horaires dans config.POSTES, soit l'ajouter à config.POSTES_EXEMPTS_NUIT "
                    f"si la CCT/règlement de travail le permet."
                )
    # Prévenir (sans bloquer) si un effectif minimum dépasse l'effectif réel du groupe.
    return avertissements_planchers(effectifs_min, personnel)


# ── Résolution ──────────────────────────────────────────────────────────

def resoudre(mois: str,
             personnel: list,
             effectifs_min: dict,
             desiderata: dict | None = None,
             contraintes_extras: list | None = None,
             time_limit_s: int = config.TIME_LIMIT_S,
             hint_grille: dict | None = None) -> dict:
    """Résout le modèle. Retourne statut/grille/heures/stats/warnings. Ne lève pas en cas d'INFISAT.

    hint_grille (P4 — warm start) : grille précédente {date ISO : {nom : code poste}}.
    Servit uniquement d'indice au solveur (AddHint) — n'affecte jamais la faisabilité.
    """
    jours = jours_du_mois(mois)
    nb = len(jours)
    codes = list(config.POSTES)
    P = list(range(len(personnel)))
    desiderata = desiderata or {}
    warnings: list[str] = list(_valider_config(effectifs_min, personnel, jours))

    model = cp_model.CpModel()

    # x[p, d, c] = 1 si l'agent p travaille au poste c le jour d
    x = {(p, d, c): model.NewBoolVar(f"x[{personnel[p]['nom']}|{jours[d].isoformat()}|{c}]")
         for p in P for d in range(nb) for c in codes}
    # t[p, d] = 1 si l'agent p travaille au moins 1 poste le jour d
    t = {(p, d): model.NewBoolVar(f"t[{personnel[p]['nom']}|{jours[d].isoformat()}]") for p in P for d in range(nb)}

    def _idx(date_iso: str) -> int | None:
        for i, j in enumerate(jours):
            if j.isoformat() == date_iso:
                return i
        return None

    # ── P4 : warm start — la grille précédente sert d'indice (vitesse seulement) ──
    if hint_grille:
        Pnom = {personnel[p]["nom"]: p for p in P}
        for date_iso, par_nom in hint_grille.items():
            d = _idx(date_iso)
            if d is None:
                continue
            for nom, code in (par_nom or {}).items():
                pi = Pnom.get(nom)
                if pi is None or code not in codes:
                    continue
                model.AddHint(x[pi, d, code], 1)

    # ── D1 : 1 poste max / jour ─────────────────────────────────────────
    for p in P:
        for d in range(nb):
            model.Add(sum(x[p, d, c] for c in codes) <= 1)
            model.Add(t[p, d] == sum(x[p, d, c] for c in codes))

    # ── D7 : élégibilité rôle → poste (config.POSTES_AUTORISES_PAR_ROLE) ──
    # Autorisés → OK. Dérogables (POSTES_DEROGABLES_PAR_ROLE) → OK mais pénalisés
    # (derog_terms). Ni l'un ni l'autre → interdits durs.
    derog_terms = []
    for p in P:
        autorises = _postes_autorises(personnel[p].get("role"))
        if autorises is None:
            continue
        derogables = _postes_derogables(personnel[p].get("role"))
        for c in codes:
            if c in autorises:
                continue
            if c in derogables:
                for d in range(nb):
                    derog_terms.append(x[p, d, c])
                continue
            for d in range(nb):
                model.Add(x[p, d, c] == 0)

    # ── D2 : affectations fixes déclarées (codes connus → poste ; inconnus → absence) ──
    for p in P:
        for aff in personnel[p].get("affectations_fixes") or []:
            d = _idx(aff.get("date", ""))
            if d is None:
                warnings.append(f"{personnel[p]['nom']} : affectation fixe hors du mois ignorée ({aff.get('date')})")
                continue
            code = aff.get("code")
            accessibles = _postes_accesibles(personnel[p].get("role"))
            if code in config.POSTES and (accessibles is None or code in accessibles):
                for c2 in codes:
                    model.Add(x[p, d, c2] == (1 if c2 == code else 0))
            elif code in config.POSTES:
                warnings.append(
                    f"{personnel[p]['nom']} : affectation fixe '{code}' non autorisée pour le rôle "
                    f"'{personnel[p].get('role')}' le {aff.get('date')} → journée marquée absence")
                for c in codes:
                    model.Add(x[p, d, c] == 0)
            else:
                warnings.append(f"{personnel[p]['nom']} : code fixe '{code}' non reconnu le {aff.get('date')} → journée marquée absence")
                for c in codes:
                    model.Add(x[p, d, c] == 0)

    # ── Contraintes dures additionnelles (boucle d'arbitrage, P3) ────────
    # contraintes_extras = [{"agent": initiales, "date": ISO, "code": poste}, ...]
    # Chaque entrée validée devient une affectation dure (comme affectations_fixes).
    for ce in contraintes_extras or []:
        nom = ce.get("agent")
        pi = next((i for i in P if personnel[i]["nom"] == nom), None)
        if pi is None:
            warnings.append(f"contrainte additionnelle ignorée : agent inconnu '{nom}'")
            continue
        d = _idx(ce.get("date", ""))
        if d is None:
            warnings.append(f"contrainte additionnelle ignorée : date hors du mois ({ce.get('date')})")
            continue
        # code absent / « repos » / « absence » → journée de repos forcée (P3 arbitrages)
        if ce.get("code") in (None, "", "repos", "absence", "REPOS"):
            for c in codes:
                model.Add(x[pi, d, c] == 0)
            continue
        code = _normaliser_poste(ce.get("code"))
        if code is None or code not in config.POSTES:
            warnings.append(f"contrainte additionnelle ignorée : poste inconnu '{ce.get('code')}'")
            continue
        accessibles = _postes_accesibles(personnel[pi].get("role"))
        if accessibles is not None and code not in accessibles:
            warnings.append(
                f"contrainte additionnelle '{nom}' le {ce.get('date')} : poste '{code}' non "
                f"accessible pour le rôle '{personnel[pi].get('role')}' → journée marquée absence")
            for c in codes:
                model.Add(x[pi, d, c] == 0)
        else:
            for c in codes:
                model.Add(x[pi, d, c] == (1 if c == code else 0))

    # ── D3 : desiderata dures ───────────────────────────────────────────
    for nom, bloc in desiderata.items():
        pi = next((i for i in P if personnel[i]["nom"] == nom), None)
        if pi is None:
            warnings.append(f"Desiderata pour '{nom}' ignorées : personne absente de la liste.")
            continue
        for des in bloc.get("structurees") or []:
            d = _idx(des.get("date", ""))
            if d is None:
                warnings.append(f"{nom} : desiderata du {des.get('date')} hors du mois → ignorée")
                continue
            cat = des.get("categorie")
            poste = _normaliser_poste(des.get("poste_concerne"))
            if cat in ("indisponibilite", "impératif") and poste is None:
                for c in codes:
                    model.Add(x[pi, d, c] == 0)
            elif cat == "impératif" and poste is not None:
                accessibles = _postes_accesibles(personnel[pi].get("role"))
                if accessibles is not None and poste not in accessibles:
                    warnings.append(
                        f"{nom} : impératif du {des.get('date')} : poste '{poste}' non autorisé pour le rôle "
                        f"'{personnel[pi].get('role')}' → journée marquée absence")
                    for c in codes:
                        model.Add(x[pi, d, c] == 0)
                else:
                    for c in codes:
                        model.Add(x[pi, d, c] == (1 if c == poste else 0))

    # ── D4 : effectifs minimums par groupe de rôles / groupe de postes / jour ──
    # Valeurs : entier (tous les jours) ou {'semaine': n, 'weekend': n}.
    groupes_roles = effectifs_min.get("_groupes_roles", {})
    groupes_postes = effectifs_min.get("_groupes_postes", {})
    for cle, postes in effectifs_min.items():
        if str(cle).startswith("_"):
            continue  # métadonnées
        roles_cle = groupes_roles.get(cle, [cle])
        groupe = [i for i in P if personnel[i].get("role") in roles_cle]
        for d in range(nb):
            for cle_poste, n in postes.items():
                codes_poste = groupes_postes.get(cle_poste, [cle_poste])
                n_jour = _effectif_du_jour(n, jours[d])
                if n_jour and groupe:
                    model.Add(sum(x[i, d, c] for i in groupe for c in codes_poste) >= n_jour)

    # ── D5 : repos quotidien 11h (matrice de compatibilité précalculée) ──
    for p in P:
        for d in range(nb - 1):
            for c1 in codes:
                for c2 in codes:
                    if _repos_entre(c1, jours[d], c2, jours[d + 1]) < config.REPOS_QUOTIDIEN_H:
                        model.Add(x[p, d, c1] + x[p, d + 1, c2] <= 1)

    # ── D6 : repos hebdomadaire (approx. 5 jours travaillés / 7) ────────
    for p in P:
        w = config.SEMAINE_FENETRE_J  # fenêtre glissante de 7 jours
        for d in range(nb - w + 1):
            model.Add(sum(t[p, d + i] for i in range(w)) <= config.SEMAINE_MAX_JOURS_TRAVAILLES)

    # ── S1 : respect des heures dues (en minutes) ─────────────────────────
    heures_min = {p: sum(x[p, d, c] * _duree(c, jours[d]) for d in range(nb) for c in codes) for p in P}
    ecarts = []
    for p in P:
        cible = (personnel[p].get("temps_du_mois_h", 0) * personnel[p].get("taux_occupation", 100) / 100.0)
        cible_i = int(round(cible * 60))
        e = model.NewIntVar(0, 999999, f"ecart[{personnel[p]['nom']}]")
        model.Add(e >= heures_min[p] - cible_i)
        model.Add(e >= cible_i - heures_min[p])
        ecarts.append(e)

    # ── S2 : souhait_negatif → minimiser les jours/postes concernés ────
    pos_terms, neg_terms = [], []
    pneg = config.POIDS["souhait_negatif"]
    ppos = config.POIDS["souhait_positif"]
    for nom, bloc in desiderata.items():
        pi = next((i for i in P if personnel[i]["nom"] == nom), None)
        if pi is None:
            continue
        for des in bloc.get("structurees") or []:
            d = _idx(des.get("date", ""))
            if d is None:
                continue
            cat, poste, prio = des.get("categorie"), _normaliser_poste(des.get("poste_concerne")), des.get("priorite", "moyenne")
            if cat == "souhait_negatif":
                cible_var = [x[pi, d, poste]] if poste else [x[pi, d, c] for c in codes]
                neg_terms.append(sum(cible_var) * pneg.get(prio, 100))
            elif cat == "souhait_positif":
                if poste:
                    pos_terms.append(x[pi, d, poste] * ppos.get(prio, 50))
                else:
                    pos_terms.append(t[pi, d] * ppos.get(prio, 50))

    # ── S4 : habitudes horaires (bonus si l'agent tient son poste habituel) ─
    # "habitudes" de l'agent : {jour de semaine : code poste | [codes] | libellé français}
    for p in P:
        hab = personnel[p].get("habitudes")
        if not hab:
            continue
        par_jour: dict[int, list[str]] = {}
        for cle, val in hab.items():
            j = config.JOURS_SEMAINE.get(str(cle).lower())
            if j is None:
                warnings.append(f"{personnel[p]['nom']} : jour d'habitude inconnu '{cle}' ignoré")
                continue
            codes_hab = val if isinstance(val, list) else [val]
            for _c in codes_hab:
                code = _c if _c in config.POSTES else _normaliser_poste(_c)
                if code:
                    par_jour.setdefault(j, []).append(code)
        for d in range(nb):
            codes_jour = par_jour.get(jours[d].weekday())
            if codes_jour:
                for c in set(codes_jour):
                    pos_terms.append(x[p, d, c] * config.POIDS["habitude"])

    # ── S3 : équité des nuits et des week-ends au sein de chaque rôle ───
    equite_terms = []
    for role in {p_["role"] for p_ in personnel if p_.get("role") in config.ROLES}:
        groupe = [i for i in P if personnel[i].get("role") == role]
        nuits = {i: sum(x[i, d, c] for d in range(nb) for c in codes if c == "N") for i in groupe}
        wknds = {i: sum(t[i, d] for d in range(nb) if jours[d].weekday() >= 5) for i in groupe}
        max_n = model.NewIntVar(0, nb, f"maxnuit[{role}]")
        max_w = model.NewIntVar(0, nb, f"maxwknd[{role}]")
        for i in groupe:
            model.Add(max_n >= nuits[i])
            model.Add(max_w >= wknds[i])
        equite_terms += [max_n, max_w]

    # ── Objectif ─────────────────────────────────────────────────────────
    # equite_terms = [max_n_role1, max_w_role1, max_n_role2, max_w_role2, ...]
    equite_nuits_terms = equite_terms[0::2]
    equite_wknd_terms = equite_terms[1::2]
    # ── S5 : soldes — éviter de finir le mois avec un solde négatif ─────
    # solde fin de mois (min) = solde_reporte + heures planifiées - heures dues ;
    # pénaliser max(0, -solde_fin) pousse le solveur à couvrir d'abord les soldes négatifs.
    soldes_neg = []
    for p in P:
        solde0_min = int(round((personnel[p].get("solde_reporte_h", 0) or 0) * 60))
        cible_i = int(round(
            personnel[p].get("temps_du_mois_h", 0) * personnel[p].get("taux_occupation", 100) / 100.0 * 60))
        # e = max(0, -X) avec X = solde0 + heures - cible ; e >= 0, e >= -X, e <= -X + BIG
        BIG = 999999
        X = solde0_min + (heures_min[p] - cible_i)
        e = model.NewIntVar(0, BIG, f"solde_neg[{personnel[p]['nom']}]")
        model.Add(e >= -X)
        model.Add(e <= -X + BIG)
        soldes_neg.append(e)

    model.Minimize(
        config.POIDS["heures"] * sum(ecarts)
        + config.POIDS["solde_negatif"] * sum(soldes_neg)
        + sum(neg_terms)
        - sum(pos_terms)
        + config.POIDS["equite_nuits"] * sum(equite_nuits_terms)
        + config.POIDS["equite_weekends"] * sum(equite_wknd_terms)
        + config.POIDS.get("derogation", 0) * sum(derog_terms)
    )

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit_s
    solver.parameters.num_search_workers = config.WORKERS
    status = solver.Solve(model)
    statut = solver.StatusName(status)
    if statut not in ("OPTIMAL", "FEASIBLE"):
        return {
            "statut": statut,
            "grille": {}, "heures": {}, "stats": {}, "warnings": warnings,
            "souhaits_non_honores": [], "derogations": [],
            "info_solveur": "Modèle infeasible (contraintes dures contradictoires). "
                            "Vérifier : effectifs min vs personnel disponible, dates de desiderata "
                            "impératives, affectations fixes, repos 11h/35h.",
        }

    grille, heures_par_p, stats = {}, {}, {}
    for p in P:
        nom = personnel[p]["nom"]
        heures_min_p = sum(
            (1 if solver.Value(x[p, d, c]) else 0) * _duree(c, jours[d]) for d in range(nb) for c in codes)
        heures_par_p[nom] = heures_min_p
        solde_fin_h = (personnel[p].get("solde_reporte_h", 0) or 0) \
            + heures_min_p / 60 - personnel[p].get("temps_du_mois_h", 0) * personnel[p].get("taux_occupation", 100) / 100.0
        stats[nom] = {
            "heures": round(heures_min_p / 60, 2),
            "cible": personnel[p].get("temps_du_mois_h", 0) * personnel[p].get("taux_occupation", 100) / 100.0,
            "solde_fin_de_mois_h": round(solde_fin_h, 2),
            "nuits": sum(1 for d in range(nb) if solver.Value(x[p, d, "N"])),
            "jours_weekend": sum(1 for d in range(nb) if jours[d].weekday() >= 5 and solver.Value(t[p, d])),
            "jours_travailles": sum(1 for d in range(nb) if solver.Value(t[p, d])),
        }
        for d in range(nb):
            poste = next((c for c in codes if solver.Value(x[p, d, c])), "")
            grille.setdefault(jours[d].isoformat(), {})[nom] = poste

    # ── P3.2 : diagnostic — souhaits souples non honorés + dérogations utilisées ──
    # (indisponibilite / impératif = contraintes dures : respectées par construction)
    souhaits_non_honores = []
    derogations = []
    for p in P:
        nom_p = personnel[p]["nom"]
        autor = _postes_autorises(personnel[p].get("role"))
        der = _postes_derogables(personnel[p].get("role"))
        if autor is None or not der:
            continue
        for d in range(nb):
            poste = next((c for c in codes if solver.Value(x[p, d, c])), "")
            if poste in der:
                derogations.append({"agent": nom_p, "date": jours[d].isoformat(), "poste": poste})
    for nom_p, bloc in desiderata.items():
        pi = next((i for i in P if personnel[i]["nom"] == nom_p), None)
        if pi is None:
            continue
        for des in bloc.get("structurees") or []:
            d = _idx(des.get("date", ""))
            if d is None:
                continue
            cat = des.get("categorie")
            if cat not in ("souhait_positif", "souhait_negatif"):
                continue
            poste_souhaite = _normaliser_poste(des.get("poste_concerne"))
            poste_reelle = next((c for c in codes if solver.Value(x[pi, d, c])), "")
            if cat == "souhait_negatif":
                viole = (poste_souhaite == poste_reelle) if poste_souhaite else bool(poste_reelle)
            else:  # souhait_positif
                viole = (poste_souhaite != poste_reelle) if poste_souhaite else not poste_reelle
            if not viole:
                continue
            real = config.POSTES[poste_reelle]["libelle"] if poste_reelle else "au repos"
            souhaits_non_honores.append({
                "agent": nom_p,
                "date": jours[d].isoformat(),
                "categorie": cat,
                "poste_concerne": poste_souhaite,
                "priorite": des.get("priorite", "moyenne"),
                "situation": (f"travaille « {real} » ce jour" if poste_reelle else "ne travaille pas ce jour"),
                "motif": des.get("motif_resume", ""),
            })
    souhaits_non_honores.sort(key=lambda s: (0 if s["priorite"] == "haute" else 1, s["agent"], s["date"]))

    return {
        "statut": statut,
        "grille": grille,
        "heures": heures_par_p,
        "stats": stats,
        "warnings": warnings,
        "souhaits_non_honores": souhaits_non_honores,
        "derogations": derogations,
        "info_solveur": f"{statut} en {solver.WallTime():.1f}s, bound={solver.BestObjectiveBound()}",
    }


# ── P3.2 : vérification déterministe des arbitrages (repos / échange) ─────────
# Le LLM ne propose QUE ces deux actions ; avant application, on vérifie ici, sur la
# grille courante, que l'action ne viole aucune contrainte dure (D2/D4/D5/D7).
# Le re-solve avec contraintes_extras reste la vérification ultime.

REPOS_CODES = ("", None, "repos", "absence", "REPOS")


def verifier_repos(mois: str, personnel: list, effectifs_min: dict, grille: dict,
                   nom: str, date_iso: str) -> tuple[bool, list[str]]:
    """Peut-on libérer `nom` le `date_iso` sans violer de contrainte dure ?"""
    jours = jours_du_mois(mois)
    d = next((i for i, j in enumerate(jours) if j.isoformat() == date_iso), None)
    if d is None:
        return False, [f"date {date_iso} hors du mois {mois}"]
    p = next((p for p in personnel if p["nom"] == nom), None)
    if p is None:
        return False, [f"agent inconnu « {nom} »"]
    poste = (grille.get(date_iso) or {}).get(nom, "")
    if not poste or poste in REPOS_CODES:
        return False, [f"{nom} est déjà au repos le {date_iso} — rien à faire."]
    if next((a for a in p.get("affectations_fixes") or [] if a.get("date") == date_iso), None):
        return False, [f"{nom} a une affectation fixe le {date_iso} (D2) — ne peut pas être libéré."]
    groupes_roles = effectifs_min.get("_groupes_roles", {})
    groupes_postes = effectifs_min.get("_groupes_postes", {})
    for cle, postes in effectifs_min.items():
        if str(cle).startswith("_"):
            continue  # métadonnées
        roles_cle = groupes_roles.get(cle, [cle])
        if p.get("role") not in roles_cle:
            continue
        for cle_poste, n in postes.items():
            codes_poste = groupes_postes.get(cle_poste, [cle_poste])
            if poste not in codes_poste:
                continue
            n_jour = _effectif_du_jour(n, jours[d])
            if not n_jour:
                continue
            jour = grille.get(date_iso) or {}
            compteur = sum(1 for q in personnel
                           if q.get("role") in roles_cle and jour.get(q["nom"], "") in codes_poste)
            if compteur - 1 < n_jour:
                return False, [f"Plancher « {cle}/{cle_poste} = {n_jour} » le {date_iso} : "
                               f"{nom} fait partie des {compteur} nécessaires — impossible sans renfort."]
    return True, []


def verifier_echange(mois: str, personnel: list, effectifs_min: dict, grille: dict,
                     nom_a: str, nom_b: str, date_iso: str) -> tuple[bool, list[str]]:
    """Peut-on échanger les postes de A et B le `date_iso` sans violer de contrainte dure ?"""
    jours = jours_du_mois(mois)
    d = next((i for i, j in enumerate(jours) if j.isoformat() == date_iso), None)
    if d is None:
        return False, [f"date {date_iso} hors du mois {mois}"]
    pa = next((p for p in personnel if p["nom"] == nom_a), None)
    pb = next((p for p in personnel if p["nom"] == nom_b), None)
    if pa is None or pb is None:
        return False, [f"agent(s) inconnu(s) : {nom_a} / {nom_b}"]
    poste_a = (grille.get(date_iso) or {}).get(nom_a, "")
    poste_b = (grille.get(date_iso) or {}).get(nom_b, "")
    if not poste_a or not poste_b or poste_a in REPOS_CODES or poste_b in REPOS_CODES:
        return False, ["L'un des deux agents est au repos ce jour-là — utiliser « repos » au lieu d'un échange."]
    if poste_a == poste_b:
        return False, ["Même poste pour les deux agents — l'échange n'aurait aucun effet."]
    erreurs: list[str] = []
    # D7 : éligibilité du rôle (dérogation comprise ; inaccessible dur → refus)
    for p, poste, nom in ((pa, poste_b, nom_a), (pb, poste_a, nom_b)):
        accessibles = _postes_accesibles(p.get("role"))
        if accessibles is not None and poste not in accessibles:
            erreurs.append(f"{nom} : poste « {poste} » inaccessible pour le rôle « {p.get('role')} ».")
    # D2 : affectations fixes du jour
    for p, nom in ((pa, nom_a), (pb, nom_b)):
        if next((a for a in p.get("affectations_fixes") or [] if a.get("date") == date_iso), None):
            erreurs.append(f"{nom} a une affectation fixe le {date_iso} (D2).")
    # D5 : repos 11h avec les jours voisins (les voisins ne changent pas)
    for p, nom, nouveau in ((pa, nom_a, poste_b), (pb, nom_b, poste_a)):
        if d > 0:
            prev = (grille.get(jours[d - 1].isoformat()) or {}).get(nom, "")
            if prev and _repos_entre(prev, jours[d - 1], nouveau, jours[d]) < config.REPOS_QUOTIDIEN_H:
                erreurs.append(f"{nom} : repos < 11h entre « {prev} » ({jours[d - 1].isoformat()}) "
                               f"et « {nouveau} » ({date_iso}).")
        if d < len(jours) - 1:
            nxt = (grille.get(jours[d + 1].isoformat()) or {}).get(nom, "")
            if nxt and _repos_entre(nouveau, jours[d], nxt, jours[d + 1]) < config.REPOS_QUOTIDIEN_H:
                erreurs.append(f"{nom} : repos < 11h entre « {nouveau} » ({date_iso}) "
                               f"et « {nxt} » ({jours[d + 1].isoformat()}).")
    # D4 : effectifs minimums du jour (delta des deux postes échangés)
    groupes_roles = effectifs_min.get("_groupes_roles", {})
    groupes_postes = effectifs_min.get("_groupes_postes", {})
    for cle, postes in effectifs_min.items():
        if str(cle).startswith("_"):
            continue  # métadonnées
        roles_cle = groupes_roles.get(cle, [cle])
        if pa.get("role") not in roles_cle and pb.get("role") not in roles_cle:
            continue
        for cle_poste, n in postes.items():
            codes_poste = groupes_postes.get(cle_poste, [cle_poste])
            n_jour = _effectif_du_jour(n, jours[d])
            if not n_jour:
                continue
            jour = grille.get(date_iso) or {}
            compteur = sum(1 for q in personnel
                           if q.get("role") in roles_cle and jour.get(q["nom"], "") in codes_poste)
            delta = (1 if pb.get("role") in roles_cle and poste_b in codes_poste else 0) \
                - (1 if pa.get("role") in roles_cle and poste_a in codes_poste else 0)
            if not delta:
                continue
            if compteur + delta < n_jour:
                erreurs.append(
                    f"Plancher « {cle}/{cle_poste} = {n_jour} » le {date_iso} : le compte passe de "
                    f"{compteur} à {compteur + delta} — infaisable sans renfort.")
    if erreurs:
        return False, erreurs
    return True, []
