"""Test smoke du solveur sur les données d'exemple (sans LLM).

Lance : python test_solveur.py
"""
import json
import sys

sys.stdout.reconfigure(encoding="utf-8")

import config
import solveur

with open("data/personnel.json", encoding="utf-8") as f:
    personnel = json.load(f)
with open("data/effectifs_min.json", encoding="utf-8") as f:
    effectifs_min = json.load(f)

# Desiderata structurées d'exemple (format = sortie du Prompt 1)
desiderata = {
    "P.K.": {
        "structurees": [
            {"date": "2026-10-06", "categorie": "impératif", "poste_concerne": None,
             "priorite": "haute", "motif_resume": "congé validé"},
            {"date": "2026-10-17", "categorie": "souhait_negatif", "poste_concerne": "nuit",
             "priorite": "moyenne", "motif_resume": "anniversaire famille"},
        ],
        "a_clarifier": [], "conflits_detectes": [],
    },
    "V.L.": {
        "structurees": [
            {"date": "2026-10-22", "categorie": "indisponibilite", "poste_concerne": None,
             "priorite": "haute", "motif_resume": "RDV médical"},
            {"date": "2026-10-12", "categorie": "souhait_positif", "poste_concerne": "matin",
             "priorite": "moyenne", "motif_resume": "préfère les matins"},
        ],
        "a_clarifier": [], "conflits_detectes": [],
    },
    "N.M.": {
        "structurees": [
            {"date": "2026-10-08", "categorie": "impératif", "poste_concerne": None,
             "priorite": "haute", "motif_resume": "formation SIAU obligatoire"},
        ],
        "a_clarifier": [], "conflits_detectes": [],
    },
    "D.E.": {
        "structurees": [
            {"date": "2026-10-03", "categorie": "indisponibilite", "poste_concerne": None,
             "priorite": "haute", "motif_resume": "garde enfant malade"},
        ],
        "a_clarifier": [], "conflits_detectes": [],
    },
    "G.A.": {
        "structurees": [
            {"date": "2026-10-09", "categorie": "indisponibilite", "poste_concerne": "matin",
             "priorite": "moyenne", "motif_resume": "RDV dentiste le matin"},
        ],
        "a_clarifier": [], "conflits_detectes": [],
    },
    "M.J.": {
        "structurees": [
            {"date": "2026-10-21", "categorie": "impératif", "poste_concerne": None,
             "priorite": "haute", "motif_resume": "congé validé"},
        ],
        "a_clarifier": [], "conflits_detectes": [],
    },
}

print(f"Mois : {config.MOIS_DEFAUT} — {len(personnel)} agents, {len(solveur.jours_du_mois(config.MOIS_DEFAUT))} jours")
result = solveur.resoudre(
    mois=config.MOIS_DEFAUT,
    personnel=personnel,
    effectifs_min=effectifs_min,
    desiderata=desiderata,
    time_limit_s=config.TIME_LIMIT_S,
)

print(f"Statut : {result['statut']}")
print(f"Info   : {result.get('info_solveur')}")
for w in result.get("warnings", []):
    print("AVERT  :", w)
if result["statut"] in ("OPTIMAL", "FEASIBLE"):
    print("\nHeures planifiées vs cible :")
    for nom, s in result["stats"].items():
        print(f"  {nom:22s} {s['heures']:5.0f} h / cible {s['cible']:5.1f} h | nuits={s['nuits']} wknd={s['jours_weekend']}")
    # Vérifications ponctuelles
    grille = result["grille"]
    assert grille["2026-10-06"].get("P.K.", "x") == "", "congé 06/10 non respecté"
    assert grille["2026-10-08"].get("N.M.", "x") == "", "formation 08/10 non respectée"
    assert grille["2026-10-04"].get("S.T.", "x") == "", "affectation fixe 04/10 absente"
    print("\nVérifications ponctuelles : OK")
else:
    print(result.get("info_solveur"))
    raise SystemExit(1)
