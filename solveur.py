"""Solveur CP-SAT — planification du service des urgences.

N'importe quelle valeur métier vient de config.py. Entrées :
  mois          "2026-10"
  personnel     liste de dicts (voir data/personnel.json)
  effectifs_min {"siamu": {"M": 3, "S": 3, "N": 2}, ...}
  desiderata    {"NOM Prénom": {"structurees": [...], "a_clarifier": [...]}}
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
    an, m = (int(x) for x in mois.split("-")[:2])
    nb = calendar.monthrange(an, m)[1]
    return [date(an, m, d) for d in range(1, nb + 1)]


def _duree(code: str) -> int:
    return config.POSTES[code]["duree_h"]


def _tranche_nuit(code: str) -> float:
    """Part du poste qui tombe dans la tranche légale 21h-6h."""
    p = config.POSTES[code]
    s, f = p["start_h"], p["start_h"] + p["duree_h"]
    a, b = max(s, 21), min(f, 30)
    return max(0.0, b - a)


def _repos_entre(c1: str, c2: str) -> float:
    """Heures de repos entre la fin du poste c1 (jour d) et le début de c2 (jour d+1)."""
    fin_d = config.POSTES[c1]["start_h"] + config.POSTES[c1]["duree_h"]
    deb_d1 = 24 + config.POSTES[c2]["start_h"]
    return deb_d1 - fin_d


def _normaliser_poste(poste) -> str | None:
    """Mappe les libellés du Prompt 1 ('matin', '12h', ...) vers les codes config.POSTES."""
    if not poste:
        return None
    if poste in config.POSTES:
        return poste
    return {"matin": "M", "soir": "S", "nuit": "N", "12h": "12"}.get(poste.lower())


def _valider_config(effectifs_min: dict, personnel: list) -> None:
    roles_connus = {p.get("role") for p in personnel}
    for role, postes in effectifs_min.items():
        if role not in config.ROLES:
            raise ErreurConfig(f"Rôle inconnu dans effectifs_min : '{role}' (attendu : {config.ROLES})")
        for code in postes:
            if code not in config.POSTES:
                raise ErreurConfig(f"Code poste inconnu '{code}' pour le rôle '{role}' (attendu : {list(config.POSTES)})")
    for p in personnel:
        if p.get("role") not in config.ROLES:
            raise ErreurConfig(f"Rôle inconnu pour '{p.get('nom')}' : '{p.get('role')}'")
    for code, meta in config.POSTES.items():
        if _tranche_nuit(code) > config.NIGHT_HOURS_MAX and code not in config.POSTES_EXEMPTS_NUIT:
            raise ErreurConfig(
                f"Poste '{code}' : {meta['start_h']}h + {meta['duree_h']}h inclut "
                f"{_tranche_nuit(code):.0f}h entre 21h et 6h (> {config.NIGHT_HOURS_MAX}h, loi 17/02/1997). "
                f"Soit ajuster les horaires dans config.POSTES, soit l'ajouter à config.POSTES_EXEMPTS_NUIT "
                f"si la CCT/règlement de travail le permet."
            )
    # Prévenir (sans bloquer) si un effectif minimum n'a aucun agent pour l'assurer.
    for role, postes in effectifs_min.items():
        n_agents = sum(1 for p in personnel if p.get("role") == role)
        for code, n in postes.items():
            if n > 0 and n_agents == 0:
                print(f"[AVERT] {role}/{code} : effectif min {n} mais aucun agent de ce rôle dans la liste.")


# ── Résolution ──────────────────────────────────────────────────────────

def resoudre(mois: str,
             personnel: list,
             effectifs_min: dict,
             desiderata: dict | None = None,
             time_limit_s: int = config.TIME_LIMIT_S) -> dict:
    """Résout le modèle. Retourne statut/grille/heures/stats/warnings. Ne lève pas en cas d'INFISAT."""
    _valider_config(effectifs_min, personnel)
    jours = jours_du_mois(mois)
    nb = len(jours)
    codes = list(config.POSTES)
    P = list(range(len(personnel)))
    desiderata = desiderata or {}
    warnings: list[str] = []

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

    # ── D1 : 1 poste max / jour ─────────────────────────────────────────
    for p in P:
        for d in range(nb):
            model.Add(sum(x[p, d, c] for c in codes) <= 1)
            model.Add(t[p, d] == sum(x[p, d, c] for c in codes))

    # ── D2 : affectations fixes déclarées (codes connus → poste ; inconnus → absence) ──
    for p in P:
        for aff in personnel[p].get("affectations_fixes") or []:
            d = _idx(aff.get("date", ""))
            if d is None:
                warnings.append(f"{personnel[p]['nom']} : affectation fixe hors du mois ignorée ({aff.get('date')})")
                continue
            code = aff.get("code")
            if code in config.POSTES:
                for c in codes:
                    model.Add(x[p, d, c] == (1 if c == code else 0))
            else:
                warnings.append(f"{personnel[p]['nom']} : code fixe '{code}' non reconnu le {aff.get('date')} → journée marquée absence")
                for c in codes:
                    model.Add(x[p, d, c] == 0)

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
                for c in codes:
                    model.Add(x[pi, d, c] == (1 if c == poste else 0))

    # ── D4 : effectifs minimums par rôle/poste/jour ─────────────────────
    for role, postes in effectifs_min.items():
        groupe = [i for i in P if personnel[i].get("role") == role]
        for d in range(nb):
            for code, n in postes.items():
                if n and groupe:
                    model.Add(sum(x[i, d, code] for i in groupe) >= n)

    # ── D5 : repos quotidien 11h (matrice de compatibilité précalculée) ──
    for p in P:
        for d in range(nb - 1):
            for c1 in codes:
                for c2 in codes:
                    if _repos_entre(c1, c2) < config.REPOS_QUOTIDIEN_H:
                        model.Add(x[p, d, c1] + x[p, d + 1, c2] <= 1)

    # ── D6 : repos hebdomadaire (approx. 5 jours travaillés / 7) ────────
    for p in P:
        w = config.SEMAINE_FENETRE_J  # fenêtre glissante de 7 jours
        for d in range(nb - w + 1):
            model.Add(sum(t[p, d + i] for i in range(w)) <= config.SEMAINE_MAX_JOURS_TRAVAILLES)

    # ── S1 : respect des heures dues ────────────────────────────────────
    heures = {(p): sum(x[p, d, c] * _duree(c) for d in range(nb) for c in codes) for p in P}
    ecarts = []
    for p in P:
        cible = (personnel[p].get("temps_du_mois_h", 0) * personnel[p].get("taux_occupation", 100) / 100.0)
        cible_i = int(round(cible))
        e = model.NewIntVar(0, 9999, f"ecart[{personnel[p]['nom']}]")
        model.Add(e >= heures[p] - cible_i)
        model.Add(e >= cible_i - heures[p])
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

    # ── S3 : équité des nuits et des week-ends au sein de chaque rôle ───
    equite_terms = []
    for role in {p_["role"] for p_ in personnel if p_.get("role") in config.ROLES}:
        groupe = [i for i in P if personnel[i].get("role") == role]
        nuits = {i: sum(x[i, d, c] for d in range(nb) for c in codes if c == "N") for i in groupe}
        wknds = {i: sum(t[i, d] for d in range(nb) if jours[d].weekday() >= 5) for i in groupe}
        for i in groupe:
            m_n = model.NewIntVar(0, nb, f"maxnuit[{role}|{personnel[i]['nom']}]")
            model.Add(m_n >= nuits[i])
            equite_terms.append(m_n)
            m_w = model.NewIntVar(0, nb, f"maxwknd[{role}|{personnel[i]['nom']}]")
            model.Add(m_w >= wknds[i])
            equite_terms.append(m_w)

    # ── Objectif ─────────────────────────────────────────────────────────
    model.Minimize(
        config.POIDS["heures"] * sum(ecarts)
        + sum(neg_terms)
        - sum(pos_terms)
        + config.POIDS["equite_nuits"] * sum(equite_terms[0::2])
        + config.POIDS["equite_weekends"] * sum(equite_terms[1::2])
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
            "info_solveur": "Modèle infeasible (contraintes dures contradictoires). "
                            "Vérifier : effectifs min vs personnel disponible, dates de desiderata "
                            "impératives, affectations fixes, repos 11h/35h.",
        }

    grille, heures_par_p, stats = {}, {}, {}
    for p in P:
        nom = personnel[p]["nom"]
        heures_par_p[nom] = sum(
            (1 if solver.Value(x[p, d, c]) else 0) * _duree(c) for d in range(nb) for c in codes)
        stats[nom] = {
            "heures": heures_par_p[nom],
            "cible": personnel[p].get("temps_du_mois_h", 0) * personnel[p].get("taux_occupation", 100) / 100.0,
            "nuits": sum(1 for d in range(nb) if solver.Value(x[p, d, "N"])),
            "jours_weekend": sum(1 for d in range(nb) if jours[d].weekday() >= 5 and solver.Value(t[p, d])),
            "jours_travailles": sum(1 for d in range(nb) if solver.Value(t[p, d])),
        }
        for d in range(nb):
            poste = next((c for c in codes if solver.Value(x[p, d, c])), "")
            grille.setdefault(jours[d].isoformat(), {})[nom] = poste

    return {
        "statut": statut,
        "grille": grille,
        "heures": heures_par_p,
        "stats": stats,
        "warnings": warnings,
        "info_solveur": f"{statut} en {solver.WallTime():.1f}s, bound={solver.BestObjectiveBound()}",
    }
