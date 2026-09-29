"""Backtest P2.3 — calage des effectifs_min sur le roster 2026 (18 agents).

Méthode
  1. Référence : effectif journalier moyen du historique 2024-10 (roster 37),
     ajusté au ratio de roster (18/37) → objectif d'effectif moyen/jour.
  2. Simulation : solveur CP-SAT **sans desiderata** (dimensionnement brut),
     15 s par combinaison.
  3. Score (par ordre de priorité) :
       a. FEASIBLE/OPTIMAL ;
       b. le moins de dérogations ;
       c. solde moyen de fin de mois ≥ 0 (puis le plus élevé) ;
       d. écart minimal avec l'effectif moyen de référence.
  4. --apply : écrit data/effectifs_min.json (l'ancien contenu est sauvegardé
     sous la clé « _historique » pour pouvoir revenir en arrière).

Usage :
  python backtest.py          # tableau comparatif uniquement
  python backtest.py --apply  # + écrit le meilleur dans data/effectifs_min.json
"""
from __future__ import annotations

import copy
import json
import sys
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8")

import config
import solveur

F_PERSONNEL = "data/personnel.json"
F_HISTO = "data/historique/2024-10.json"
F_EFF = "data/effectifs_min.json"
TIME_LIMIT_S = 15

# (libellé, jour_sem, jour_we, nuit_sem, nuit_we) — seul le groupe infirmier_tous varie ;
# les lignes IC (1 sem / 0 WE) et logistique (nuit 1/1) restent identiques.
CANDIDATS = [
    ("A (actuel) 5/3 · 3/1", 5, 3, 3, 1),
    ("B          4/3 · 3/1", 4, 3, 3, 1),
    ("C          4/2 · 2/1", 4, 2, 2, 1),
    ("D          3/2 · 2/1", 3, 2, 2, 1),
    ("E          4/3 · 2/1", 4, 3, 2, 1),
    ("F (sans min) 0/0 · 0/0", 0, 0, 0, 0),
]


def construire_em(base: dict, jour_sem: int, jour_we: int, nuit_sem: int, nuit_we: int) -> dict:
    em = copy.deepcopy(base)
    em["infirmier_tous"] = {
        "jour": {"semaine": jour_sem, "weekend": jour_we},
        "nuit": {"semaine": nuit_sem, "weekend": nuit_we},
    }
    return em


def metrics(r: dict, nb_jours: int) -> dict:
    grille = r.get("grille") or {}
    pj = sum(len(v) for v in grille.values())
    stats = r.get("stats") or {}
    soldes = [s.get("solde_fin_de_mois_h", 0) for s in stats.values()]
    return {
        "statut": r["statut"],
        "person_jours": pj,
        "eff_moyen_j": round(pj / max(1, nb_jours), 1),
        "derogations": len(r.get("derogations") or []),
        "solde_moyen_h": round(sum(soldes) / max(1, len(soldes)), 1),
        "solde_min_h": round(min(soldes, default=0), 1),
        "heures_moyennes_h": round(sum(s.get("heures", 0) for s in stats.values()) / max(1, len(stats)), 1),
    }


def score(m: dict, ref_eff: float) -> tuple:
    feasible = m["statut"] in ("OPTIMAL", "FEASIBLE")
    return (
        0 if feasible else 1,
        m["derogations"],
        0 if m["solde_moyen_h"] >= 0 else 1,
        -m["solde_moyen_h"],
        abs(m["eff_moyen_j"] - ref_eff),
    )


def main(apply: bool) -> int:
    with open(F_PERSONNEL, encoding="utf-8") as f:
        personnel = json.load(f)
    with open(F_EFF, encoding="utf-8") as f:
        base = json.load(f)
    with open(F_HISTO, encoding="utf-8") as f:
        histo = json.load(f)

    nb_jours = len(solveur.jours_du_mois(config.MOIS_DEFAUT))
    ref_brut = sum(histo["effectifs_par_jour"]) / len(histo["effectifs_par_jour"])
    nb_anciens = len(histo.get("roster") or [1])
    ref = ref_brut * len(personnel) / nb_anciens
    print(f"Référence : {ref_brut:.1f} pers./j (roster {nb_anciens} agents) "
          f"→ ajusté roster {len(personnel)} : {ref:.1f} pers./j")
    print(f"Mois simulé : {config.MOIS_DEFAUT} ({nb_jours} jours) — "
          f"{len(personnel)} agents, sans desiderata, {TIME_LIMIT_S} s/combo\n")

    lignes = []
    best = None
    for lib, js, jw, ns, nw in CANDIDATS:
        em = construire_em(base, js, jw, ns, nw)
        r = solveur.resoudre(
            mois=config.MOIS_DEFAUT,
            personnel=personnel,
            effectifs_min=em,
            desiderata={},
            time_limit_s=TIME_LIMIT_S,
        )
        m = metrics(r, nb_jours)
        sc = score(m, ref)
        print(f"{lib:26s} → {m}")
        lignes.append((lib, em, m, sc))
        if best is None or sc < best[0]:
            best = (sc, lib, em, m)

    _, lib, em, m = best
    print(f"\nMeilleure combinaison : {lib} → {m}")

    if apply:
        em_out = {
            "_note": (f"Calibré par backtest.py le {datetime.now().isoformat(timespec='seconds')} "
                      f"sur roster {len(personnel)} agents — combinaison {lib} : {m}. "
                      f"L'ancienne version est dans « _historique »."),
            "_historique": base,
            **em,
        }
        with open(F_EFF, "w", encoding="utf-8") as f:
            json.dump(em_out, f, ensure_ascii=False, indent=1)
        print(f"Écrit dans {F_EFF} (ancienne version → clé « _historique »).")
    else:
        print("Mode lecture — relancer avec --apply pour écrire data/effectifs_min.json.")
    return 0


if __name__ == "__main__":
    sys.exit(main("--apply" in sys.argv))
