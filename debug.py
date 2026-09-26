"""Débogage : identifie quelle contrainte dure rend le modèle infeasible. python debug.py"""
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
import config
import solveur

with open("data/personnel.json", encoding="utf-8") as f:
    personnel = json.load(f)
with open("data/effectifs_min.json", encoding="utf-8") as f:
    em = json.load(f)


def test(**patch):
    for k, v in patch.items():
        setattr(config, k, v)
    r = solveur.resoudre(mois=config.MOIS_DEFAUT, personnel=personnel,
                         effectifs_min=em, desiderata={}, time_limit_s=20)
    statut = r["statut"]
    extra = ""
    if statut in ("OPTIMAL", "FEASIBLE"):
        extra = f" | heures: {r['heures']}"
    print(f"{patch} -> {statut}{extra}")


print("== Baseline ==")
test()
print("== Sans D6 (repos hebdo) ==")
test(SEMAINE_MAX_JOURS_TRAVAILLES=7)
print("== Sans D5 (repos 11h) ==")
test(REPOS_QUOTIDIEN_H=1)
print("== Sans D5 et D6 ==")
test(REPOS_QUOTIDIEN_H=1, SEMAINE_MAX_JOURS_TRAVAILLES=7)
print("== Sans D4 (effectifs min = 0, baseline D5/D6) ==")
config.REPOS_QUOTIDIEN_H = 11
config.SEMAINE_MAX_JOURS_TRAVAILLES = 5
em0 = {k: {c: 0 for c in v} for k, v in em.items()}
r = solveur.resoudre(mois=config.MOIS_DEFAUT, personnel=personnel,
                     effectifs_min=em0, desiderata={}, time_limit_s=20)
print("effectifs=0 (baseline D5/D6) ->", r["statut"])
