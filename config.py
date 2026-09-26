"""Paramètres métier du planning — à adapter au CHR Haute Senne Soignies.

TOUS les horaires / codes / effectifs vivent ici. Le solveur (solveur.py)
et l'app Streamlit (app.py) ne contiennent aucune valeur métier en dur.
"""

MOIS_DEFAUT = "2026-10"

# ⚠️ À VALIDER AVEC LE SERVICE PLANIFICATION — horaires réels du service.
# start_h : heure de début (0-23), duree_h : durée réelle en heures.
# Un poste "casse" le jour si start_h + duree_h > 24 (ex: nuit 19h→7h).
POSTES = {
    "M":  {"libelle": "Matin",     "start_h": 7,  "duree_h": 8},
    "S":  {"libelle": "Soir",      "start_h": 15, "duree_h": 8},
    "N":  {"libelle": "Nuit",      "start_h": 19, "duree_h": 12},
    "12": {"libelle": "Poste 12h", "start_h": 7,  "duree_h": 12},
}

# Rôles reconnus par le solveur (champ "role" des agents).
ROLES = ["siamu", "infirmier", "aide_soignant", "logistique"]

# Repos quotidien minimal entre la fin d'un poste et le début du suivant (loi 16/03/1971).
REPOS_QUOTIDIEN_H = 11

# Repos hebdomadaire 35h — APPROXIMATION CP-SAT : max SEMAINE_MAX_JOURS_TRAVAILLES
# jours travaillés sur toute fenêtre glissante de SEMAINE_FENETRE_J jours. À valider avec RH (CCT 46 / CP330).
SEMAINE_FENETRE_J = 7
SEMAINE_MAX_JOURS_TRAVAILLES = 5

# Travail de nuit (21h→6h) limité à 8h (loi 17/02/1997).
NIGHT_HOURS_MAX = 8
# Postes dont la tranche 21h-6h dépasse 8h mais est admise par le règlement
# de travail / CCT sectorielle. ⚠️ À VALIDER AVEC RH — si ce liste est vide,
# un poste comme "N" (12h dont 9h en 21h-6h) rendrait le modèle infeasible.
POSTES_EXEMPTS_NUIT = ["N"]

# Poids de la fonction objective (contraintes souples).
POIDS = {
    "heures": 100,  # écart |heures planifiées - heures dues|
    "souhait_negatif": {"haute": 200, "moyenne": 100, "basse": 50},
    "souhait_positif": {"haute": 100, "moyenne": 50,  "basse": 25},
    "equite_nuits": 10,    # minimise le max de nuits par personne dans un rôle
    "equite_weekends": 5,  # minimise le max de jours week-end travaillés par rôle
}

# Paramètres de résolution.
TIME_LIMIT_S = 30
WORKERS = 8
