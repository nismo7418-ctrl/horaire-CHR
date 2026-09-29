"""Export du planning — fonctions pures, sans Streamlit.

Importables et testables hors UI :
    from exporter import grille_dataframe, heures_dataframe, csv_bytes, excel_bytes
"""
from __future__ import annotations

import pandas as pd


def grille_dataframe(planning: dict, noms: list[str]) -> pd.DataFrame:
    """Grille personne × jour (colonnes JJ/MM), valeurs = code poste."""
    jours = sorted(planning["grille"])
    cols = [f"{d[-2:]}/{d[5:7]}" for d in jours]
    data = [[planning["grille"].get(d, {}).get(n, "") for d in jours] for n in noms]
    return pd.DataFrame(data, index=noms, columns=cols)


def heures_dataframe(planning: dict, noms: list[str]) -> pd.DataFrame:
    """Heures planifiées vs dues, nuits, week-ends, jours travaillés."""
    rows = []
    for n in noms:
        s = planning.get("stats", {}).get(n, {})
        cible, plan = s.get("cible", 0), s.get("heures", 0)
        rows.append({
            "personne": n,
            "heures_planifiees": plan,
            "cible": cible,
            "ecart_h": round(plan - cible, 1),
            "nuits": s.get("nuits", 0),
            "jours_weekend": s.get("jours_weekend", 0),
            "jours_travailles": s.get("jours_travailles", 0),
        })
    return pd.DataFrame(rows)


def desiderata_dataframe(desiderata: dict) -> pd.DataFrame:
    """Table plate des desiderata (structurees + à clarifier + conflits), toutes personnes."""
    lignes = []
    for n, bloc in (desiderata or {}).items():
        for des in bloc.get("structurees", []):
            lignes.append({"personne": n, "date": des.get("date"), "categorie": des.get("categorie"),
                           "poste": des.get("poste_concerne"), "priorite": des.get("priorite"),
                           "motif": des.get("motif_resume")})
        for q in bloc.get("a_clarifier", []):
            lignes.append({"personne": n, "date": q.get("date_ou_periode"), "categorie": "à clarifier",
                           "poste": None, "priorite": None, "motif": q.get("question")})
        for c in bloc.get("conflits_detectes", []):
            lignes.append({"personne": n, "date": None, "categorie": "conflit détecté",
                           "poste": None, "priorite": None, "motif": c.get("description")})
    return pd.DataFrame(lignes, columns=["personne", "date", "categorie", "poste", "priorite", "motif"])


def csv_bytes(df: pd.DataFrame) -> bytes:
    return df.to_csv().encode("utf-8-sig")


def excel_bytes(grille_df: pd.DataFrame, dfh: pd.DataFrame) -> bytes:
    buf = pd.io.common.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        grille_df.to_excel(w, sheet_name="planning")
        dfh.to_excel(w, sheet_name="heures", index=False)
    return buf.getvalue()
