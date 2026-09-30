"""Diagnostic déterministe du planning — analyse « comme un humain » (phase intelligence, points 1-2).

Pas de LLM, pas de solveur : lit uniquement
  - la grille + les stats produites par solveur.py,
  - les habitudes hebdomadaires du personnel (data/personnel.json),
  - la mémoire inter-mois (state.charger_resultat du mois précédent).

Seuils : config.py — NUITS_CONSECUTIVES_MAX, EQUITE_ECART_MAX, EQUITE_WKND_ECART_MAX,
NUITS_CUMULEES_MAX, WKND_CUMULES_MAX.

Usage :
    import diagnostic
    diag = diagnostic.diagnostiquer(grille, stats, personnel, "2026-10")
    diag["alertes"]  # [{"niveau": "warning"|"info", "texte": "..."}, ...] — prêtes à
                     # afficher (app.py) et à injecter dans le contexte LLM (llm.py)
"""
from __future__ import annotations

from datetime import date

import config
import state

RATIO_HABITUDE_MIN = 0.7  # >= 70 % des jours « poste habituel » → rotation considérée stable


def _mois_precedent(mois: str) -> str:
    """'2026-01' → '2025-12' (lève ValueError si format invalide)."""
    an, m = int(mois[:4]), int(mois[5:7])
    if m == 1:
        return f"{an - 1:04d}-12"
    return f"{an:04d}-{m - 1:02d}"


def _weekday(date_iso: str) -> int:
    y, m, d = (int(v) for v in date_iso.split("-")[:3])
    return date(y, m, d).weekday()


def _plus_longue_rainure_nuit(grille: dict, nom: str) -> tuple[int, list]:
    """(longueur, dates de la plus longue séquence de nuits consécutives) de `nom`."""
    meilleure, meilleures_dates = 0, []
    courante = []
    for jour in sorted(grille):
        if (grille.get(jour) or {}).get(nom) == "N":
            courante.append(jour)
            if len(courante) > meilleure:
                meilleure, meilleures_dates = len(courante), courante[:]
        else:
            courante = []
    return meilleure, meilleures_dates


def _top_bottom(valeurs: dict) -> tuple[str, int, str, int]:
    """(nom_max, max, nom_min, min) — premier nom en cas d'égalité."""
    if not valeurs:
        raise ValueError("valeurs vides")
    nom_max = max(valeurs, key=lambda n: (valeurs[n], n))
    nom_min = min(valeurs, key=lambda n: (valeurs[n], n))
    return nom_max, valeurs[nom_max], nom_min, valeurs[nom_min]


def diagnostiquer(grille: dict, stats: dict, personnel: list, mois: str) -> dict:
    """Analyse déterministe du planning. Retourne le dict documenté dans le module.

    Totalement défensif : aucune exception attendue — une donnée manquante dégrade
    simplement la section concernée (ex: pas de mémoire inter-mois le premier mois).
    """
    grille = grille or {}
    stats = stats or {}
    agents = [p.get("nom") for p in (personnel or []) if p.get("nom")]
    role_de = {p.get("nom"): p.get("role") for p in (personnel or []) if p.get("role")}

    diag: dict = {
        "mois": mois,
        "nuits_consecutives": [],
        "equite": [],
        "memore_inter_mois": [],
        "memoire_disponible": False,
        "memoire_mois": None,
        "soldes": {},
        "stabilite": [],
        "alertes": [],
    }

    # ── 1. Nuits consécutives (fatigue / rotation) ─────────────────────────
    for nom in agents:
        if nom not in stats:
            continue
        longueur, dates = _plus_longue_rainure_nuit(grille, nom)
        if longueur >= config.NUITS_CONSECUTIVES_MAX + 1:
            diag["nuits_consecutives"].append(
                {"agent": nom, "plus_longue_rainure": longueur, "dates": dates})
            diag["alertes"].append({
                "niveau": "warning",
                "texte": (f"{nom} : {longueur} nuits consécutives (du {dates[0]} au "
                          f"{dates[-1]}) — au-dessus du seuil de {config.NUITS_CONSECUTIVES_MAX}."),
            })
    diag["nuits_consecutives"].sort(key=lambda a: -a["plus_longue_rainure"])

    # ── 2. Équité au sein de chaque rôle (écarts max/min) ──────────────────
    par_role: dict = {}
    for nom, role in role_de.items():
        if nom in stats:
            par_role.setdefault(role, []).append(nom)
    for role in sorted(par_role):
        noms = par_role[role]
        if len(noms) < 2:
            continue
        nuits = {n: stats.get(n, {}).get("nuits", 0) for n in noms}
        wknd = {n: stats.get(n, {}).get("jours_weekend", 0) for n in noms}
        for libelle, valeurs, seuil, cle in (
            ("nuits", nuits, config.EQUITE_ECART_MAX, "equite_nuits"),
            ("jours week-end", wknd, config.EQUITE_WKND_ECART_MAX, "equite_weekends"),
        ):
            n_max, v_max, n_min, v_min = _top_bottom(valeurs)
            ecart = v_max - v_min
            if ecart >= seuil:
                diag["equite"].append({
                    "role": role, "indicateur": cle, "ecart": ecart,
                    "max": {"agent": n_max, "valeur": v_max},
                    "min": {"agent": n_min, "valeur": v_min}, "seuil": seuil,
                })
                diag["alertes"].append({
                    "niveau": "warning",
                    "texte": (f"Équité {libelle} (rôle {role}) : {n_max} = {v_max} vs "
                              f"{n_min} = {v_min} (écart {ecart} ≥ seuil {seuil})."),
                })

    # ── 3. Mémoire inter-mois (cumuls avec le mois précédent) ───────────────
    try:
        mois_prev = _mois_precedent(mois)
        _grille_prev, stats_prev = state.charger_resultat(mois_prev)
    except (ValueError, OSError):
        mois_prev, stats_prev = None, {}
    stats_prev = stats_prev or {}
    if stats_prev:
        diag["memoire_disponible"] = True
        diag["memoire_mois"] = mois_prev
        for nom in agents:
            s_now, s_prev = stats.get(nom) or {}, stats_prev.get(nom)
            if not isinstance(s_prev, dict):
                continue
            nuits_cum = s_now.get("nuits", 0) + s_prev.get("nuits", 0)
            wknd_cum = s_now.get("jours_weekend", 0) + s_prev.get("jours_weekend", 0)
            if nuits_cum >= config.NUITS_CUMULEES_MAX or wknd_cum >= config.WKND_CUMULES_MAX:
                diag["memore_inter_mois"].append({
                    "agent": nom,
                    "nuits": {"ce_mois": s_now.get("nuits", 0),
                              "mois_prev": s_prev.get("nuits", 0), "cumul": nuits_cum},
                    "jours_weekend": {"ce_mois": s_now.get("jours_weekend", 0),
                                      "mois_prev": s_prev.get("jours_weekend", 0),
                                      "cumul": wknd_cum},
                })
                diag["alertes"].append({
                    "niveau": "warning",
                    "texte": (f"{nom} : cumul 2 mois = {nuits_cum} nuits "
                              f"({s_prev.get('nuits', 0)} en {mois_prev} + {s_now.get('nuits', 0)}) "
                              f"et {wknd_cum} jours de week-end — seuils {config.NUITS_CUMULEES_MAX} nuits / "
                              f"{config.WKND_CUMULES_MAX} week-ends."),
                })

    # ── 4. Soldes fin de mois ───────────────────────────────────────────────
    solde_agents = [(n, stats[n].get("solde_fin_de_mois_h", 0)) for n in agents if n in stats]
    if solde_agents:
        n_neg, v_neg = min(solde_agents, key=lambda x: (x[1], x[0]))
        n_pos, v_pos = max(solde_agents, key=lambda x: (x[1], x[0]))
        diag["soldes"] = {
            "plus_negatif": {"agent": n_neg, "heures": v_neg},
            "plus_positif": {"agent": n_pos, "heures": v_pos},
        }
        if v_neg < 0:
            diag["alertes"].append({
                "niveau": "warning",
                "texte": f"{n_neg} finit le mois avec un solde négatif de {v_neg} h "
                         f"(à rattraper le mois prochain).",
            })

    # ── 5. Stabilité par rapport aux habitudes hebdomadaires (S4) ──────────
    for p in personnel or []:
        nom, hab = p.get("nom"), p.get("habitudes") or {}
        if not nom or not hab:
            continue
        total = conforms = 0
        for jour_semaine, code_attendu in hab.items():
            wd = config.JOURS_SEMAINE.get(str(jour_semaine))
            if wd is None:
                continue
            for date_iso in grille:
                if _weekday(date_iso) == wd:
                    total += 1
                    if (grille.get(date_iso) or {}).get(nom) == code_attendu:
                        conforms += 1
        if total:
            ratio = round(conforms / total, 2)
            diag["stabilite"].append(
                {"agent": nom, "habitudes": hab, "ratio": ratio,
                 "conforme": ratio >= RATIO_HABITUDE_MIN})
            if ratio < RATIO_HABITUDE_MIN:
                diag["alertes"].append({
                    "niveau": "info",
                    "texte": (f"{nom} : poste habituel honoré sur {conforms}/{total} jours "
                              f"(ratio {ratio}) — rotation décalée par rapport à son habitude."),
                })

    # warnings d'abord, infos ensuite (tri stable — ordre d'analyse conservé à niveau égal)
    diag["alertes"].sort(key=lambda a: 0 if a.get("niveau") == "warning" else 1)
    return diag
