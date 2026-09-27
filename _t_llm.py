import json, sys
sys.stdout.reconfigure(encoding="utf-8")
import llm

print("URL :", llm.LMSTUDIO_URL)
print("Modele :", llm.MODELE)
agent = {"nom": "P.K.", "role": "siamu", "affectations_fixes": []}
texte = "Congé validé le 06/10. Pas de nuit le week-end du 17-18/10 (anniversaire de la famille)."
res = llm.analyser_desiderata("2026-10", {"N":"nuit","M":"matin","S":"soir","12":"12h"}, agent, texte)
print("Clé du résultat :", type(res).__name__)
print(json.dumps(res, ensure_ascii=False, indent=1)[:900])
