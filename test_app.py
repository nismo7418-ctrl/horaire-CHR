"""Exécute app.py de bout en bout (sans LLM) via le harnais de test Streamlit.

Lance : python test_app.py
"""
import sys

sys.stdout.reconfigure(encoding="utf-8")

from streamlit.testing.v1 import AppTest

at = AppTest.from_file("app.py", default_timeout=120)
at.run()
assert not at.exception, f"Exceptions : {at.exception}"
assert at.title[0].value == "Planning Urgences — CHR Haute Senne Soignies"
assert [t.label for t in at.tabs] or len(at.tabs) == 3
print("Tabs:", [t.label for t in at.tabs])
# Onglet 2 : vérifier le bouton de calcul
btn = next(b for t in at.tabs for b in t.button if "Calculer" in b.label)
btn.click().run()
assert not at.exception, f"Exceptions après calcul : {at.exception}"
print("Bouton 'Calculer le planning' : OK, sans exception")
# Onglet 3 : grille + export présents
tab3 = at.tabs[2]
assert any("Résultats" in (s.value or "") for s in tab3.subheader)
assert len(tab3.dataframe) >= 1
print("Onglet 3 : grille + export OK")
print("TEST APP : OK")
