"""App Streamlit — Planning Urgences CHR Haute Senne.

Flux : 1) Désiderata (LLM Prompt 1) → 2) Génération (solveur CP-SAT) → 3) Résultats (LLM Prompt 2 + export).
Lancement : streamlit run app.py
Le LLM n'intervient jamais dans le calcul de la grille, uniquement en amont (structuration)
et en aval (explication).
"""
from __future__ import annotations

import json

import pandas as pd
import streamlit as st

import config
import exporter
import llm
import solveur

st.set_page_config(page_title="Planning Urgences — CHR Haute Senne", layout="wide", page_icon="🏥")

FICHIERS = {"personnel": "data/personnel.json", "effectifs_min": "data/effectifs_min.json"}


def charger() -> tuple[list, dict]:
    with open(FICHIERS["personnel"], encoding="utf-8") as f:
        personnel = json.load(f)
    with open(FICHIERS["effectifs_min"], encoding="utf-8") as f:
        effectifs_min = json.load(f)
    return personnel, effectifs_min


# ── État de session ─────────────────────────────────────────────────────
if "personnel" not in st.session_state:
    st.session_state.personnel, st.session_state.effectifs_min = charger()
    st.session_state.desiderata: dict[str, dict] = {}
    st.session_state.planning: dict | None = None
    st.session_state.explication: dict | None = None

st.title("Planning Urgences — CHR Haute Senne Soignies")

with st.sidebar:
    st.header("Paramètres")
    mois = st.text_input("Mois (AAAA-MM)", config.MOIS_DEFAUT)
    seuil_ecart = st.number_input("Seuil écart heures (h)", min_value=0.0, value=8.0, step=1.0)
    time_limit = st.number_input("Temps de résolution max (s)", min_value=5, value=config.TIME_LIMIT_S, step=5)
    st.caption("⚠️ Les bases légales (repos 11h, nuit 8h en 21h-6h, repos hebdo) sont indicatives — "
               "à valider avec RH / CCT 46 / CP330 avant mise en production.")
    if st.button("↺ Réinitialiser la session"):
        st.session_state.desiderata = {}
        st.session_state.planning = None
        st.session_state.explication = None
        st.rerun()

noms = [p["nom"] for p in st.session_state.personnel]
tab1, tab2, tab3 = st.tabs(["1 · Désiderata", "2 · Génération", "3 · Résultats & export"])

legende_codes = {code: meta["libelle"] for code, meta in config.POSTES.items()}


def legende_etendu() -> dict:
    return {**legende_codes, "IC": "infirmière-cheffe (à confirmer)", "🌴": "congé"}


# ────────────────────────────────────────────────────────────────────────
# 1 · DÉSIDERATA
# ────────────────────────────────────────────────────────────────────────
with tab1:
    st.subheader("Analyse des desiderata (Prompt 1)")
    nom = st.selectbox("Personne", noms, key="s1_nom")
    agent = next(p for p in st.session_state.personnel if p["nom"] == nom)
    prealable = st.session_state.desiderata.get(nom, {})
    texte = st.text_area(
        "Desiderata (texte libre)",
        value=agent.get("desiderata_brutes", ""),
        key=f"s1_texte_{nom}",
        height=120,
    )
    if not texte.strip():
        st.warning("Saisir le texte libre de la personne pour l'analyser.")
    c1, c2 = st.columns(2)
    with c1:
        if st.button("🤖 Analyser (LLM local)", type="primary", disabled=not texte.strip()):
            with st.spinner("Appel LM Studio (Prompt 1)..."):
                try:
                    res = llm.analyser_desiderata(mois, legende_etendu(), agent, texte)
                except llm.ErreurLLM as e:
                    st.error(str(e))
                else:
                    st.session_state.desiderata[nom] = {
                        "structurees": res.get("desiderata_structurees", []),
                        "a_clarifier": res.get("a_clarifier", []),
                        "conflits_detectes": res.get("conflits_detectes", []),
                    }
                    st.success("Desiderata structurées et stockées.")
                    st.rerun()
    with c2:
        st.markdown("**Ajout manuel (sans LLM)**")
        with st.form(f"form_{nom}", clear_on_submit=True):
            f_date = st.date_input("Date", value=None)
            f_cat = st.selectbox("Catégorie", ["indisponibilite", "impératif", "souhait_positif", "souhait_negatif"])
            f_poste = st.selectbox("Poste", ["indifferent", "matin", "soir", "nuit", "12h"])
            f_prio = st.selectbox("Priorité", ["haute", "moyenne", "basse"])
            f_motif = st.text_input("Motif (court, factuel)")
            if st.form_submit_button("Ajouter la ligne"):
                bloc = st.session_state.desiderata.setdefault(
                    nom, {"structurees": [], "a_clarifier": [], "conflits_detectes": []})
                bloc["structurees"].append({
                    "date": f_date.isoformat(),
                    "categorie": f_cat,
                    "poste_concerne": None if f_poste == "indifferent" else f_poste,
                    "priorite": f_prio,
                    "motif_resume": f_motif or "ajout manuel",
                })
                st.toast("Ligne ajoutée.")
                st.rerun()

    # Aperçu des desiderata structurées
    st.markdown("#### Desiderata structurées à ce jour")
    lignes = []
    for n, bloc in st.session_state.desiderata.items():
        for des in bloc.get("structurees", []):
            lignes.append({"personne": n, "date": des["date"], "categorie": des["categorie"],
                           "poste": des.get("poste_concerne"), "priorite": des.get("priorite"),
                           "motif": des.get("motif_resume")})
        for q in bloc.get("a_clarifier", []):
            lignes.append({"personne": n, "date": q.get("date_ou_periode"), "categorie": "⚠️ à clarifier",
                           "poste": None, "priorite": None, "motif": q.get("question")})
        for c in bloc.get("conflits_detectes", []):
            lignes.append({"personne": n, "date": None, "categorie": "⚠️ conflit", "poste": None,
                           "priorite": None, "motif": c.get("description")})
    if lignes:
        df = pd.DataFrame(lignes)
        st.dataframe(df, width="stretch", height=260)
        for n, bloc in st.session_state.desiderata.items():
            if st.button("🗑 Purger", key=f"purge_{n}"):
                del st.session_state.desiderata[n]
                st.rerun()
    else:
        st.info("Aucune desiderata structurée pour le moment.")


# ────────────────────────────────────────────────────────────────────────
# 2 · GÉNÉRATION
# ────────────────────────────────────────────────────────────────────────
with tab2:
    st.subheader("Calcul du planning (solveur CP-SAT)")
    n_des = sum(len(b.get("structurees", [])) for b in st.session_state.desiderata.values())
    st.write(f"- **Personnel** : {len(st.session_state.personnel)} agents")
    st.write(f"- **Effectifs min/jour** : {st.session_state.effectifs_min}")
    st.write(f"- **Desiderata structurées** : {n_des}")
    if st.button("⚙️ Calculer le planning", type="primary"):
        with st.spinner("Résolution du modèle CP-SAT..."):
            try:
                result = solveur.resoudre(
                    mois=mois,
                    personnel=st.session_state.personnel,
                    effectifs_min=st.session_state.effectifs_min,
                    desiderata=st.session_state.desiderata,
                    time_limit_s=time_limit,
                )
            except solveur.ErreurConfig as e:
                st.error(f"Configuration invalide : {e}")
            else:
                st.session_state.planning = result
                st.session_state.explication = None
                st.rerun()

    pl = st.session_state.planning
    if pl:
        (ok := pl["statut"] in ("OPTIMAL", "FEASIBLE"))
        (st.success if ok else st.error)(f"Statut solveur : **{pl['statut']}** — {pl.get('info_solveur', '')}")
        for w in pl.get("warnings", []):
            st.warning(w)
        if not ok:
            st.info("Pistes : vérifier les dates impératives, les effectifs minimums vs effectif présent, "
                    "et les affectations fixes (code inconnu → absence forcée).")


# ────────────────────────────────────────────────────────────────────────
# 3 · RÉSULTATS & EXPORT
# ────────────────────────────────────────────────────────────────────────
with tab3:
    st.subheader("Résultats")
    pl = st.session_state.planning
    if not pl or pl["statut"] not in ("OPTIMAL", "FEASIBLE"):
        st.info("Aucun planning valide — passer par l'onglet 2.")
        st.stop()

    # Grille
    grille_df = exporter.grille_dataframe(pl, noms)
    st.dataframe(grille_df, width="stretch", height=420)

    # Heures
    st.markdown("#### Heures planifiées vs heures dues")
    dfh = exporter.heures_dataframe(pl, noms)
    st.dataframe(dfh, width="stretch")

    ecart_max = dfh["ecart_h"].apply(abs).max()
    if ecart_max > seuil_ecart:
        st.warning(f"Écart max {ecart_max} h dépasse le seuil de {seuil_ecart} h — l'explication LLM (ci-dessous) doit en rendre compte.")

    # Explication LLM
    st.markdown("#### Explication & arbitrages (Prompt 2)")
    b1, b2, b3, b4 = st.columns(4)
    if b1.button("🤖 Générer l'explication", type="primary"):
        with st.spinner("Appel LM Studio (Prompt 2)..."):
            try:
                st.session_state.explication = llm.expliquer_planning(
                    mois, legende_etendu(), pl, st.session_state.desiderata,
                    st.session_state.personnel, seuil_ecart_h=seuil_ecart)
                st.rerun()
            except llm.ErreurLLM as e:
                st.error(str(e))
    if st.session_state.explication:
        ex = st.session_state.explication
        st.markdown(f"**Résumé** : {ex.get('resume_global', '')}")
        st.json(ex)
    else:
        st.info("Cliquez sur « Générer l'explication » (nécessite LM Studio en local).")

    # Export
    st.markdown("#### Export (ressaisie manuelle PEP's)")
    e1, e2 = st.columns(2)
    if e1.download_button("⬇ Télécharger CSV", exporter.csv_bytes(grille_df),
                          file_name=f"planning_{mois}.csv", mime="text/csv"):
        pass
    if e2.download_button("⬇ Télécharger Excel",
                          exporter.excel_bytes(grille_df, dfh),
                          file_name=f"planning_{mois}.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"):
        pass
