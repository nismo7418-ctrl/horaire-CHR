"""Boucle d'arbitrage P3 — fonctions pures, testables sans Streamlit ni LLM.

Séparation des rôles (principe fondamental du projet) :
  - le LLM (llm.py) ne fait que PROPOSER des arbitrages (repos / échanges) ;
  - arbitre.py VÉRIFIE chaque proposition de manière déterministe
    (solveur.verifier_repos / solveur.verifier_echange) et la matérialise en
    contraintes_extras (affectations dures) ;
  - le solveur (solveur.py) recalcule la grille.

Le LLM ne touche JAMAIS la grille.
"""
from __future__ import annotations

import solveur


def fusion_extras(existants: list, nouveaux: list) -> list:
    """Fusionne deux listes de contraintes_extras — un seul poste forcé par (agent, date).

    En cas de conflit, la seconde liste gagne (ordre : existants puis nouveaux).
    """
    m: dict = {}
    for e in list(existants or []) + list(nouveaux or []):
        m[(e.get("agent"), e.get("date"))] = e
    return list(m.values())


def verifier_propositions(mois: str, personnel: list, effectifs_min: dict,
                          grille: dict, propositions: list) -> tuple[list, list[str]]:
    """Vérifie chaque proposition LLM (repos / échange) et construit les contraintes_extras.

    Retourne (extras, erreurs) : les propositions refusées arrivent dans `erreurs` avec le
    motif déterministe (plancher, affectation fixe, repos < 11h, rôle inéligible).
    """
    grille = grille or {}
    extras: list = []
    erreurs: list[str] = []
    for prop in propositions or []:
        t = prop.get("type")
        if t == "repos":
            ok, errs = solveur.verifier_repos(
                mois, personnel, effectifs_min,
                grille, prop.get("agent", ""), prop.get("date", ""))
            if not ok:
                erreurs.append(f"repos « {prop.get('agent')} » {prop.get('date')} : " + " ; ".join(errs))
                continue
            extras.append({"agent": prop["agent"], "date": prop["date"], "code": "repos"})
        elif t == "echange":
            a, b, d = prop.get("agent_a", ""), prop.get("agent_b", ""), prop.get("date", "")
            ok, errs = solveur.verifier_echange(
                mois, personnel, effectifs_min, grille, a, b, d)
            if not ok:
                erreurs.append(f"échange « {a} ↔ {b} » {d} : " + " ; ".join(errs))
                continue
            jour = grille.get(d) or {}
            extras.append({"agent": a, "date": d, "code": jour.get(b, "")})
            extras.append({"agent": b, "date": d, "code": jour.get(a, "")})
        else:
            erreurs.append(f"Proposition inconnue ignorée : {prop}")
    return extras, erreurs


def arbitrer(mois: str, personnel: list, effectifs_min: dict,
             desiderata: dict, contraintes_extras: list, time_limit_s: int,
             proposer, hint_grille: dict | None = None) -> tuple[dict, list, list[str]]:
    """P3 — boucle d'arbitrage complète : solve → propositions (LLM injecté) →
    vérification déterministe → re-solve.

    proposer(souhaits_non_honores, grille) -> dict {"propositions": [...]} —
    l'appel LLM (llm.proposer_arbitrages) est passé en paramètre : mockable en test,
    arbitre.py n'importe jamais llm.py.

    Retourne (resultat, contraintes_extras_finales, erreurs) :
      - souhaits non honorés + propositions applicables → re-solve avec les extras fusionnés ;
      - re-solve infeasible → premier résultat conservé, contraintes_extras inchangées
        (un motif explicite arrive dans `erreurs`) ;
      - LLM indisponible / aucune proposition → premier résultat, `erreurs` documente.
    """
    res = solveur.resoudre(
        mois=mois, personnel=personnel, effectifs_min=effectifs_min,
        desiderata=desiderata, contraintes_extras=contraintes_extras,
        time_limit_s=time_limit_s, hint_grille=hint_grille)
    extras_finales = list(contraintes_extras or [])
    if res["statut"] not in ("OPTIMAL", "FEASIBLE"):
        return res, extras_finales, []
    snh = res.get("souhaits_non_honores") or []
    if not snh:
        return res, extras_finales, []

    try:
        ar = proposer(snh, res["grille"])
    except Exception as e:
        return res, extras_finales, [f"Propositions LLM indisponibles : {e}"]
    props = [p for p in (ar.get("propositions") or [])
             if p.get("type") in ("repos", "echange")][:5]
    if not props:
        return res, extras_finales, []

    extras, erreurs = verifier_propositions(mois, personnel, effectifs_min, res["grille"], props)
    if not extras:
        return res, extras_finales, erreurs
    merged = fusion_extras(contraintes_extras, extras)
    res2 = solveur.resoudre(
        mois=mois, personnel=personnel, effectifs_min=effectifs_min,
        desiderata=desiderata, contraintes_extras=merged,
        time_limit_s=time_limit_s, hint_grille=res.get("grille"))
    if res2["statut"] in ("OPTIMAL", "FEASIBLE"):
        return res2, merged, erreurs
    erreurs = list(erreurs) + ["Re-solve avec arbitrages infeasible — planning initial conservé."]
    return res, extras_finales, erreurs
