"""Test de l'export (importable hors Streamlit) : grille, heures, CSV, Excel.

Lance : python test_export.py
"""
import json
import sys

sys.stdout.reconfigure(encoding="utf-8")

import exporter
import solveur


def main():
    with open("data/personnel.json", encoding="utf-8") as f:
        personnel = json.load(f)
    with open("data/effectifs_min.json", encoding="utf-8") as f:
        effectifs_min = json.load(f)

    res = solveur.resoudre(
        mois="2026-10", personnel=personnel, effectifs_min=effectifs_min,
        desiderata={}, time_limit_s=30,
    )
    assert res["statut"] in ("OPTIMAL", "FEASIBLE"), f"statut={res['statut']}"

    noms = [p["nom"] for p in personnel]
    g = exporter.grille_dataframe(res, noms)
    h = exporter.heures_dataframe(res, noms)
    assert list(g.columns) and len(g) == len(noms)
    assert set(h.columns) >= {"personne", "heures_planifiees", "cible", "ecart_h"}

    x = exporter.excel_bytes(g, h)
    assert x[:2] == b"PK", "fichier xlsx invalide"
    c = exporter.csv_bytes(g)
    assert b"," in c and c.startswith(b"\xef\xbb\xbf")
    print(f"Grille : {g.shape[0]} agents × {g.shape[1]} jours | Excel : {len(x)} octets | CSV : {len(c)} octets")
    print("TEST EXPORT : OK")


if __name__ == "__main__":
    main()
