"""Paramètres métier du planning — à adapter au CHR Haute Senne Soignies.

TOUS les horaires / codes / effectifs vivent ici. Le solveur (solveur.py)
et l'app Streamlit (app.py) ne contiennent aucune valeur métier en dur.
"""

MOIS_DEFAUT = "2026-10"

# Horaires réels du service (d'après la grille d'octobre 2026). start_min : heure de
# début en minutes depuis minuit, duree_min : durée en minutes (ENTIERS — CP-SAT
# n'accepte que des coefficients entiers). Un poste "casse" le jour si
# start_min + duree_min > 1440 (ex: nuit 19h→7h).
# CONFIRMÉS : M = 6h54→15h00 (8h06), S = 13h15→21h00 (7h45), 12 = 9h00→21h00 (12h).
# N (nuit) a des horaires VARIABLES selon le jour → voir NUIT_PAR_SEMAINE.
POSTES = {
    "M":  {"libelle": "Matin",     "start_min": 414, "duree_min": 486},   # 6h54→15h00 (8h06)
    "S":  {"libelle": "Soir",      "start_min": 795, "duree_min": 465},   # 13h15→21h00 (7h45)
    "N":  {"libelle": "Nuit"},                                            # horaires variables → NUIT_PAR_SEMAINE
    "12": {"libelle": "Poste 12h", "start_min": 540, "duree_min": 720},   # 9h00→21h00 (12h)
}

# Nuits : horaires variables selon le jour de la semaine (confirmés par le service).
# Clé = weekday python (0=lun … 6=dim), "default" = lundi-jeudi. Valeurs = (début, fin)
# en minutes depuis minuit du jour du poste ; fin > 1440 si la nuit casse le lendemain.
NUIT_PAR_SEMAINE = {
    "default": (1260, 1854),  # 21h00 → 6h54 (9h54) — lundi à jeudi
    4: (1260, 1920),          # vendredi   21h00 → 8h00 (11h00)
    5: (1200, 1920),          # samedi     20h00 → 8h00 (12h00)
    6: (1200, 1875),          # dimanche   20h00 → 7h15 (11h15)
}

# Jours fériés du mois (dates ISO, ex "2026-10-01") sur lesquels des règles
# particulières s'appliquent. ⚠️ À COMPLÉTER (à confirmer) : poste de jour "JF"
# 8h00→20h00 ; nuit de jour férié qui commence à 20h00 (durée à préciser).
# Octobre 2026 : aucun jour férié belge → liste vide, la logique est en place.
JOURS_FERIES = []


def horaires_poste(code, jour):
    """Retourne (start_min, end_min) du poste `code` le jour `jour`.

    end peut dépasser 1440 (poste qui casse le lendemain, ex: nuit).
    M/S/12 ont des horaires fixes ; N dépend du jour de la semaine.
    """
    if code == "N":
        if jour.isoformat() in JOURS_FERIES:
            # Nuit de jour férié : début 20h00, fin selon jour
            #   semaine (lun-ven) → 7h15, week-end (sam-dim) → 8h00
            fin = 1440 + (7 * 60 + 15) if jour.weekday() < 5 else 1440 + (8 * 60)
            return 20 * 60, fin
        s, e = NUIT_PAR_SEMAINE.get(jour.weekday(), NUIT_PAR_SEMAINE["default"])
        return s, e
    p = POSTES[code]
    return p["start_min"], p["start_min"] + p["duree_min"]

# Sous-index vus en dessous des cases dans la grille réelle (annotations, PAS des
# postes — le solveur ne les modélise pas). Servent de contexte à la légende LLM.
ANNOTATIONS = {
    "SR": "affectation SMUR sur le poste (confirmé)",
    "HP": "poste à l'hospitalisation provisoire (vert/bleu = deux postes distincts) — confirmé",
    "HP_mauve": "postes 12h spécifiques : 9h00-13h15 HP puis 13h15-21h SMUR — confirmé",
    "ino": "à confirmer (inopérante / indispo ?)",
    "SB": "à confirmer",
}

# Codes d'absence / statut vus dans la grille (non modélisés comme postes). À confirmer.
CODES_ABSANCE = {
    "dp": "dispense de prestation (journée)",
    "DDI": "à confirmer (détachement ?)",
    "FO": "journée formation (obligatoire)",
    "U": "à confirmer",
    "IC": "infirmière en chef (poste dédié, confirmé par le rôle)",
    "🌴": "congé / vacances",
    "❓ (fond jaune)": "journée formation (généralement — cf. FO)",
    "❓ (fond rouge)": "maladie (dans certains cas)",
}

# Rôles reconnus par le solveur (champ "role" des agents).
ROLES = ["siamu", "infirmier", "aide_soignant", "logistique"]

# Repos quotidien minimal entre la fin d'un poste et le début du suivant (loi 16/03/1971).
# En minutes (11h = 660).
REPOS_QUOTIDIEN_H = 660

# Repos hebdomadaire 35h — APPROXIMATION CP-SAT : max SEMAINE_MAX_JOURS_TRAVAILLES
# jours travaillés sur toute fenêtre glissante de SEMAINE_FENETRE_J jours. À valider avec RH (CCT 46 / CP330).
SEMAINE_FENETRE_J = 7
SEMAINE_MAX_JOURS_TRAVAILLES = 5

# Travail de nuit (21h→6h) limité à 8h (loi 17/02/1997). En minutes (8h = 480).
NIGHT_HOURS_MAX = 480
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
    "habitude": 60,        # bonus si l'agent fait son poste habituel le jour concerné (S4)
}

# Noms de jours acceptés dans "habitudes" des agents (personnel.json) → weekday python.
JOURS_SEMAINE = {"lun": 0, "mar": 1, "mer": 2, "jeu": 3, "ven": 4, "sam": 5, "dim": 6,
                 "0": 0, "1": 1, "2": 2, "3": 3, "4": 4, "5": 5, "6": 6}

# Paramètres de résolution.
TIME_LIMIT_S = 30
WORKERS = 8
