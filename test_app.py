"""Exécute app.py de bout en bout (sans LLM) via le harnais de test Streamlit.

Lance : python test_app.py
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")

import auth

# Compte de test (les variables d'environnement priment sur secrets.toml)
os.environ["APP_IDENTIFIANT"] = "test"
os.environ["APP_MDP_STOCKAGE"] = auth.hacher("mdp-test")

from streamlit.testing.v1 import AppTest

at = AppTest.from_file("app.py", default_timeout=120)
at.run()
assert not at.exception, f"Exceptions : {at.exception}"

# Non connecté → formulaire de connexion, pas de grille
assert any("Connexion" in (t.value or "") for t in at.title), "formulaire de connexion attendu"
assert not at.tabs, "grille invisible avant connexion"

# Connexion
at.text_input[0].input("test")
at.text_input[1].input("mdp-test")
at.button[0].click().run()
assert not at.exception, f"Exceptions après connexion : {at.exception}"
assert at.title[0].value == "Planning — Service des urgences"
assert len(at.tabs) == 3

# Onglet 2 : vérifier le bouton de calcul
btn = next(b for t in at.tabs for b in t.button if "Calculer" in b.label)
btn.click().run()
assert not at.exception, f"Exceptions après calcul : {at.exception}"
# Onglet 3 : grille + export présents
tab3 = at.tabs[2]
assert any("Résultats" in (s.value or "") for s in tab3.subheader)
assert len(tab3.dataframe) >= 1
print("TEST APP : OK (authentification + 3 onglets + solveur)")
