"""App Streamlit — Planning du service des urgences.

Flux : 1) Désiderata (LLM Prompt 1) → 2) Génération (solveur CP-SAT) → 3) Résultats (LLM Prompt 2 + export).
Boucle P3 : le LLM propose des arbitrages (Prompt 3) ou structure un ajustement libre (Prompt 4) ;
chaque action est vérifiée de manière déterministe (solveur.verifier_repos / verifier_echange),
matérialisée en contraintes_extras (affectations dures) puis le solveur recalcule. Le LLM ne
modifie JAMAIS la grille.
Lancement : streamlit run app.py
Le LLM n'intervient jamais dans le calcul de la grille, uniquement en amont (structuration)
et en aval (explication).
"""
from __future__ import annotations

import json
import re
import time

import pandas as pd
import streamlit as st

import arbitre
import auth
import config
import diagnostic
import exporter
import llm
import solveur
import state

st.set_page_config(page_title="Planning Urgences", layout="wide", page_icon="🏥")

# ── Authentification (déploiement cloud) ───────────────────────────────
if not auth.veillee():
    st.title("Planning Urgences — Connexion")
    st.caption("Accès réservé au responsable de la planification. Les données ne vivent "
               "qu'en mémoire de session et sont effacées à la déconnexion — rien n'est stocké.")
    with st.form("connexion", clear_on_submit=True):
        u = st.text_input("Identifiant")
        p = st.text_input("Mot de passe", type="password")
        submit = st.form_submit_button("Se connecter", type="primary")
    if submit:
        ok, msg = auth.connecter(u or "", p or "")
        if ok:
            auth.ouvrir_session(u)
            st.rerun()
        else:
            st.error(msg or "Identifiants invalides.")
    st.stop()

FICHIERS = {"personnel": "data/personnel.json", "effectifs_min": "data/effectifs_min.json"}


def charger() -> tuple[list, dict]:
    with open(FICHIERS["personnel"], encoding="utf-8") as f:
        personnel = json.load(f)
    with open(FICHIERS["effectifs_min"], encoding="utf-8") as f:
        effectifs_min = json.load(f)
    return personnel, effectifs_min


def _precheck(effectifs_min: dict, personnel: list) -> list[str]:
    """Contrôle préalable : planchers vs effectif réel par groupe de rôles.

    Source unique : solveur.avertissements_planchers — la même logique que le
    solveur (aucune divergence possible entre l'UI et la résolution).
    """
    return solveur.avertissements_planchers(effectifs_min, personnel)


# ── État de session ─────────────────────────────────────────────────────
if "personnel" not in st.session_state:
    st.session_state.personnel, st.session_state.effectifs_min = charger()
    st.session_state.desiderata: dict[str, dict] = {}
    st.session_state.planning: dict | None = None
    st.session_state.explication: dict | None = None
    # P3 — boucle d'arbitrage
    st.session_state.contraintes_extras: list = []  # arbitrages appliqués (contraintes dures)
    st.session_state.propositions: list = []        # propositions LLM en attente (onglet 3)
    st.session_state.erreurs_arbitrage: list = []   # motifs de refus des dernières propositions
    st.session_state.diagnostic: dict | None = None  # phase « intelligence » — analyse déterministe du planning
    st.session_state._etat_mois: str | None = None  # P4 — mois pour lequel l'état persisté a été chargé

st.title("Planning — Service des urgences")

st.caption("Conforme RGPD : anonymisé par initiales, aucun nom d'établissement, "
           "aucune donnée nominative stockée. LLM en instance privée (aucune transmission tierce).")

with st.sidebar:
    st.header("Paramètres")
    mois = st.text_input("Mois (AAAA-MM)", config.MOIS_DEFAUT)
    mois_ok = bool(re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", (mois or "").strip()))
    if not mois_ok:
        st.error(f"Mois invalide : « {mois} » — format attendu AAAA-MM (ex : 2026-10).")

    # P4 — persistance : recharge l'état (desiderata + arbitrages) quand le mois change
    mois_key = (mois or "").strip()
    if mois_ok and st.session_state.get("_etat_mois") != mois_key:
        _etat = state.charger(mois_key)
        st.session_state.desiderata = _etat["desiderata"]
        st.session_state.contraintes_extras = _etat["contraintes_extras"]
        # Résultats rattachés à l'ancien mois → obsolètes (un export les nommait du mauvais mois)
        st.session_state.planning = None
        st.session_state.explication = None
        st.session_state.propositions = []
        st.session_state.erreurs_arbitrage = []
        st.session_state.diagnostic = None
        st.session_state.duree_reso_s = None
        st.session_state._etat_mois = mois_key
        if _etat["desiderata"] or _etat["contraintes_extras"]:
            st.caption(f"↺ Session rechargée pour {mois_key} (desiderata + arbitrages persistés).")
    seuil_ecart = st.number_input("Seuil écart heures (h)", min_value=0.0, value=8.0, step=1.0)
    time_limit = st.number_input("Temps de résolution max (s)", min_value=5, value=config.TIME_LIMIT_S, step=5)
    st.caption("⚠️ Les bases légales (repos 11h, nuit 8h en 21h-6h, repos hebdo) sont indicatives — "
               "à valider avec RH / CCT 46 / CP330 avant mise en production.")

    llm_ok, llm_detail = st.session_state.get("llm_statut", (None, "état non vérifié"))
    icone = "🟢" if llm_ok else ("🔴" if llm_ok is False else "⚪")
    st.caption(f"{icone} LM Studio : {llm_detail}")
    if st.button("🔍 Vérifier LM Studio"):
        st.session_state.llm_statut = llm.statut_endpoint()
        st.rerun()

    if st.button("↺ Réinitialiser la session"):
        st.session_state.desiderata = {}
        st.session_state.planning = None
        st.session_state.explication = None
        st.session_state.contraintes_extras = []
        st.session_state.propositions = []
        st.session_state.erreurs_arbitrage = []
        st.session_state.diagnostic = None
        st.session_state.duree_reso_s = None
        st.session_state._etat_mois = None            # prochain run : recharge (fichier effacé → vide)
        if mois_ok:
            state.effacer((mois or "").strip())       # P4 — supprime data/state/{mois}.json
        st.rerun()

    if llm.endpoint_est_local():
        st.caption("⚠️ Endpoint LLM en `localhost` — pour le déploiement cloud, pointer "
                   "`LMSTUDIO_URL` vers l'hôte de modèle dédié (réseau privé, HTTPS).")

    st.markdown("---")
    st.caption(f"👤 Connecté : **{st.session_state.auth['user']}** (session {auth.SESSION_TIMEOUT_S // 60} min)")
    if st.button("⏻ Se déconnecter"):
        auth.deconnecter()
        st.rerun()

    with st.expander("🔒 Confidentialité & RGPD"):
        st.markdown(
            "- **Données** : initiales des agents uniquement — aucun nom complet, aucun nom "
            "d'établissement, aucune donnée de santé détaillée (motifs courts et factuels).\n"
            "- **Stockage** : aucune base de données. Les données vivent en mémoire de session "
            "seulement et sont **effacées à la déconnexion** ; `data/*.json` ne contient que des "
            "initiales et des volumes horaires.\n"
            "- **LLM** : instance privée (LM Studio) sur infrastructure dédiée — aucune "
            "transmission vers un service tiers. Avant d'utiliser un endpoint externe, "
            "informer le DPO / responsable de la protection des données.\n"
            "- **Durée de conservation** : le planning est un document de travail — ne le "
            "diffuser qu'au service de planification (finalité exclusive).\n"
            "- **Droits des personnes** : accès, rectification, effacement à exercer auprès du "
            "responsable de la planification (DPO de l'établissement).\n"
            "- **Décision** : le planning généré est une proposition technique — la décision "
            "d'affectation relève du service.")

noms = [p["nom"] for p in st.session_state.personnel]
tab1, tab2, tab3 = st.tabs(["1 · Désiderata", "2 · Génération", "3 · Résultats & export"])

legende_codes = {code: meta["libelle"] for code, meta in config.POSTES.items()}


def legende_etendu() -> dict:
    return {**legende_codes, "IC": "infirmière-cheffe (à confirmer)", "🌴": "congé"}


# ────────────────────────────────────────────────────────────────────────
# P3 — Boucle d'arbitrage : fonctions partagées entre les onglets 2 et 3
# ────────────────────────────────────────────────────────────────────────
def _sauver_etat():
    """P4 — persiste desiderata + contraintes_extras + résultat (grille/stats)
    dans data/state/{mois}.json — la grille/stats alimentent la mémoire inter-mois."""
    if mois_ok:
        pl = st.session_state.planning or {}
        state.sauver((mois or "").strip(),
                     st.session_state.desiderata, st.session_state.contraintes_extras,
                     grille=pl.get("grille"), stats=pl.get("stats"))


def _diagnostiquer_resultat():
    """Phase « intelligence » — analyse déterministe du planning courant (pas de LLM)."""
    pl = st.session_state.planning or {}
    if pl.get("statut") not in ("OPTIMAL", "FEASIBLE"):
        st.session_state.diagnostic = None
        return
    try:
        st.session_state.diagnostic = diagnostic.diagnostiquer(
            pl.get("grille") or {}, pl.get("stats") or {},
            st.session_state.personnel, (mois or "").strip())
    except Exception:
        st.session_state.diagnostic = None


def _lancer_reso(contraintes_extras: list | None = None,
                 spinner: str = "Résolution du modèle CP-SAT..."):
    """Résout avec l'état de session et stocke le résultat.

    contraintes_extras=None → reprend ceux de la session. Relance l'app après stockage.
    """
    if contraintes_extras is None:
        contraintes_extras = st.session_state.contraintes_extras
    t0 = time.monotonic()
    with st.spinner(spinner):
        try:
            result = solveur.resoudre(
                mois=mois,
                personnel=st.session_state.personnel,
                effectifs_min=st.session_state.effectifs_min,
                desiderata=st.session_state.desiderata,
                contraintes_extras=contraintes_extras,
                time_limit_s=time_limit,
                hint_grille=(st.session_state.planning or {}).get("grille"),  # P4 — warm start
            )
        except solveur.ErreurConfig as e:
            st.error(f"Configuration invalide : {e}")
            return
    st.session_state.planning = result
    st.session_state.explication = None
    st.session_state.propositions = []          # obsolètes après re-solve (grille changée)
    st.session_state.erreurs_arbitrage = []
    st.session_state.contraintes_extras = list(contraintes_extras or [])
    st.session_state.duree_reso_s = round(time.monotonic() - t0, 1)
    _diagnostiquer_resultat()
    _sauver_etat()
    st.rerun()


def _fusion_extras(existants: list, nouveaux: list) -> list:
    """Fusionne deux listes de contraintes_extras — implémentation : arbitre.fusion_extras."""
    return arbitre.fusion_extras(existants, nouveaux)


def _extras_depuis_propositions(propositions: list) -> tuple[list, list[str]]:
    """Vérifie chaque proposition LLM (repos / échange) — implémentation :
    arbitre.verifier_propositions (grille lue depuis st.session_state.planning)."""
    pl = st.session_state.planning or {}
    return arbitre.verifier_propositions(
        mois, st.session_state.personnel, st.session_state.effectifs_min,
        pl.get("grille") or {}, propositions)


def _autopilot():
    """P3.3 — « Tout faire » : arbitre.arbitrer (solve → LLM propose → vérification
    déterministe → re-solve) → explication. La boucle vit dans arbitre.py (testable).
    Le LLM (injecté via `proposer`) ne fait que proposer ; il ne touche jamais la grille."""
    t0 = time.monotonic()
    st.session_state.propositions = []
    st.session_state.erreurs_arbitrage = []

    def proposer(snh: list, grille: dict) -> dict:
        ar = llm.proposer_arbitrages(
            mois, legende_etendu(), snh, grille,
            st.session_state.personnel, st.session_state.effectifs_min)
        # Retenues pour l'affichage onglet 3 (arbitre.py re-filtre indépendamment).
        st.session_state.propositions = [
            p for p in (ar.get("propositions") or [])
            if p.get("type") in ("repos", "echange")][:5]
        return ar

    with st.spinner("🚀 Étapes 1–3/4 — solve → arbitrages (LLM) → re-solve..."):
        try:
            res, extras_finales, erreurs = arbitre.arbitrer(
                mois=mois,
                personnel=st.session_state.personnel,
                effectifs_min=st.session_state.effectifs_min,
                desiderata=st.session_state.desiderata,
                contraintes_extras=st.session_state.contraintes_extras,
                time_limit_s=time_limit,
                proposer=proposer,
                hint_grille=(st.session_state.planning or {}).get("grille"),  # P4 — warm start
            )
        except solveur.ErreurConfig as e:
            st.error(f"Configuration invalide : {e}")
            st.rerun()
            return
    st.session_state.planning = res
    st.session_state.contraintes_extras = extras_finales
    st.session_state.explication = None
    st.session_state.erreurs_arbitrage = erreurs
    if res["statut"] not in ("OPTIMAL", "FEASIBLE"):
        st.session_state.duree_reso_s = round(time.monotonic() - t0, 1)
        st.rerun()
        return
    for e in erreurs:
        st.warning(e)

    # Étape 4 : explication
    try:
        with st.spinner("🚀 Étape 4/4 — Explication (LLM local)..."):
            st.session_state.explication = llm.expliquer_planning(
                mois, legende_etendu(), res,
                st.session_state.desiderata, st.session_state.personnel,
                seuil_ecart_h=seuil_ecart,
                diagnostic=st.session_state.get("diagnostic"))
    except llm.ErreurLLM as e:
        st.error(f"Explication LLM impossible : {e}")
    st.session_state.duree_reso_s = round(time.monotonic() - t0, 1)
    _diagnostiquer_resultat()
    _sauver_etat()
    st.rerun()


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
                    _sauver_etat()
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
                if f_date is None:
                    st.warning("Choisir une date avant d'ajouter la ligne.")
                    st.stop()
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
                _sauver_etat()
                st.rerun()

    # Aperçu des desiderata structurées
    st.markdown("#### Desiderata structurées à ce jour")
    df_des = exporter.desiderata_dataframe(st.session_state.desiderata)
    if not df_des.empty:
        st.dataframe(df_des, width="stretch", height=260)
        st.download_button("⬇ Export CSV desiderata", exporter.csv_bytes(df_des),
                          file_name=f"desiderata_{mois}.csv", mime="text/csv")
        for n in list(st.session_state.desiderata):
            if st.button("🗑 Purger", key=f"purge_{n}"):
                del st.session_state.desiderata[n]
                _sauver_etat()
                st.rerun()
    else:
        st.info("Aucune desiderata structurée pour le moment.")


# ────────────────────────────────────────────────────────────────────────
# 2 · GÉNÉRATION
# ────────────────────────────────────────────────────────────────────────
with tab2:
    st.subheader("Calcul du planning (solveur CP-SAT)")
    _avert_pre = _precheck(st.session_state.effectifs_min, st.session_state.personnel)
    for a in _avert_pre:
        st.warning(a)
    n_des = sum(len(b.get("structurees", [])) for b in st.session_state.desiderata.values())
    eff_aff = {k: v for k, v in st.session_state.effectifs_min.items() if not str(k).startswith("_")}
    st.write(f"- **Personnel** : {len(st.session_state.personnel)} agents")
    st.write(f"- **Effectifs min/jour** : {eff_aff}")
    st.write(f"- **Desiderata structurées** : {n_des}")

    # ── Habitudes hebdomadaires (contrainte souple S4) ─────────────────────
    st.markdown("#### Habitudes horaires du personnel (contrainte souple — bonus « poste habituel »)")
    st.caption("Pour chaque agent : le poste tenu habituellement chaque jour de semaine. "
               "« Indifférent » = aucune préférence ce jour-là. Enregistré dans `data/personnel.json`.")
    JOURS_UI = ["lun", "mar", "mer", "jeu", "ven", "sam", "dim"]
    OPTIONS_UI = ["Indifférent", "M (matin)", "S (soir)", "N (nuit)", "12 (12h)", "IC", "SMUR"]
    code_par_option = {"Indifférent": None, "M (matin)": "M", "S (soir)": "S",
                       "N (nuit)": "N", "12 (12h)": "12", "IC": "IC", "SMUR": "SMUR"}
    for agent in st.session_state.personnel:
        hab = agent.get("habitudes") or {}
        cols = st.columns([2] + [1] * 7)
        with cols[0]:
            st.write(agent["nom"])
        for j, nom_j in enumerate(JOURS_UI):
            with cols[j + 1]:
                cur = hab.get(nom_j)
                cur = cur if isinstance(cur, str) and cur in code_par_option.values() else None
                idx = next((i for i, c in enumerate(code_par_option.values()) if c == cur), 0)
                st.selectbox(nom_j, OPTIONS_UI, index=idx, key=f"hab_{agent['nom']}_{j}")
    c3, c4 = st.columns(2)
    with c3:
        if st.button("💾 Enregistrer les habitudes (personnel.json)"):
            for agent in st.session_state.personnel:
                hab = {}
                for j, nom_j in enumerate(JOURS_UI):
                    v = code_par_option[st.session_state.get(f"hab_{agent['nom']}_{j}", "Indifférent")]
                    if v:
                        hab[nom_j] = v
                agent["habitudes"] = hab
            with open(FICHIERS["personnel"], "w", encoding="utf-8") as f:
                json.dump(st.session_state.personnel, f, ensure_ascii=False, indent=1)
            st.success("Habitudes enregistrées dans data/personnel.json.")
            st.rerun()
    with c4:
        if st.button("↺ Recharger depuis le fichier"):
            st.session_state.personnel, st.session_state.effectifs_min = charger()
            st.rerun()

    # ── P3 — arbitrages déjà appliqués (contraintes dures) ──────────────
    ce = st.session_state.contraintes_extras or []
    if ce:
        st.markdown("#### Arbitrages appliqués (P3 — contraintes dures)")
        st.dataframe(pd.DataFrame(ce), width="stretch", height=min(260, 60 * len(ce) + 40))
        if st.button("↺ Retirer les arbitrages & recalculer"):
            _lancer_reso(contraintes_extras=[])

    b_solve, b_auto = st.columns([1, 1])
    with b_solve:
        if st.button("⚙️ Calculer le planning", type="primary", disabled=not mois_ok):
            _lancer_reso()
    with b_auto:
        if st.button("🚀 Tout faire (solve → arbitrer → expliquer)", disabled=not mois_ok):
            _autopilot()

    pl = st.session_state.planning
    if pl:
        (ok := pl["statut"] in ("OPTIMAL", "FEASIBLE"))
        (st.success if ok else st.error)(f"Statut solveur : **{pl['statut']}** — {pl.get('info_solveur', '')}")
        if st.session_state.get("duree_reso_s") is not None:
            st.caption(f"Temps de résolution : {st.session_state.duree_reso_s} s")
        for w in pl.get("warnings", []):
            if w in _avert_pre:
                continue  # déjà affiché en tête d'onglet (pré-check) — source unique solveur.py
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

    # ── Phase « intelligence » — diagnostic déterministe (sans LLM) ──────
    diag = st.session_state.get("diagnostic") or {}
    if diag:
        st.markdown("#### Diagnostic (analyse déterministe — seuils dans `config.py`)")
        if diag["alertes"]:
            for a in diag["alertes"]:
                (st.warning if a.get("niveau") == "warning" else st.info)(a.get("texte", ""))
        else:
            st.success("Aucune alerte : nuits consécutives, équité, cumuls inter-mois, "
                       "soldes et habitudes — ce planning passe tous les seuils.")
        if not diag.get("memoire_disponible"):
            st.caption("Pas de mémoire inter-mois (résultat du mois précédent non persisté) — "
                       "le cumul 2 mois est indisponible pour ce mois.")

    # ── P3 — boucle d'arbitrage ──────────────────────────────────────────
    st.markdown("#### Arbitrages (P3 — souhaits non honorés)")
    for e in st.session_state.get("erreurs_arbitrage") or []:
        st.warning("⛔ " + e)
    snh = pl.get("souhaits_non_honores") or []
    der = pl.get("derogations") or []
    if snh:
        st.dataframe(pd.DataFrame(snh), width="stretch", height=min(320, 60 * len(snh) + 40))
    else:
        st.success("Tous les souhaits souples sont honorés par le planning.")
    if der:
        st.caption(f"⚠️ {len(der)} dérogation(s) hors rôle utilisée(s) (pénalisées dans la fonction objective) :")
        st.dataframe(pd.DataFrame(der), width="stretch", height=min(240, 60 * len(der) + 40))

    props = st.session_state.propositions or []
    if snh and not props:
        if st.button("🤖 Proposer des arbitrages (LLM local)"):
            with st.spinner("Appel LM Studio (Prompt 3)..."):
                try:
                    ar = llm.proposer_arbitrages(
                        mois, legende_etendu(), snh, pl["grille"],
                        st.session_state.personnel, st.session_state.effectifs_min)
                except llm.ErreurLLM as e:
                    st.error(str(e))
                else:
                    props = [p for p in (ar.get("propositions") or [])
                             if p.get("type") in ("repos", "echange")][:5]
                    st.session_state.propositions = props
                    st.success(f"{len(props)} proposition(s) reçue(s).")
                    st.rerun()
    if props:
        dfp = pd.DataFrame([{
            "Appliquer": False,
            "Type": p.get("type"),
            "Agent(s)": p.get("agent") or f"{p.get('agent_a')} ↔ {p.get('agent_b')}",
            "Date": p.get("date"),
            "Motif": p.get("motif", ""),
        } for p in props])
        ed = st.data_editor(dfp, hide_index=True, height=min(320, 60 * len(props) + 40),
                            column_config={
                                "Appliquer": st.column_config.CheckboxColumn("Appliquer"),
                                "Motif": st.column_config.TextColumn(width="large"),
                            })
        idx_cochees = [i for i, row in ed.iterrows() if bool(row["Appliquer"])]
        p1, p2 = st.columns([1, 3])
        with p1:
            if st.button("✅ Appliquer la sélection", type="primary", disabled=not idx_cochees):
                extras, erreurs = _extras_depuis_propositions([props[i] for i in idx_cochees])
                st.session_state.erreurs_arbitrage = erreurs
                if extras:
                    merged = _fusion_extras(st.session_state.contraintes_extras, extras)
                    _lancer_reso(contraintes_extras=merged)
                elif erreurs:
                    st.error("Aucune proposition applicable — motifs de refus ci-dessus.")
        with p2:
            if st.button("🗑 Purger les propositions"):
                st.session_state.propositions = []
                st.session_state.erreurs_arbitrage = []   # motifs de refus obsolètes
                st.rerun()

    # ── P3.4 — ajustement libre (langage naturel) ─────────────────────────
    st.markdown("#### Ajustement libre (P3.4)")
    st.caption("Formulez un ajustement (ex : « P.K. pas de nuit le 17/10 »). Le LLM le structure, "
               "il est fusionné dans les desiderata, puis le solveur recalcule.")
    aj_texte = st.text_area("Ajustement", key="aj_texte", height=70,
                            placeholder="P.K. pas de nuit le 17/10")
    if st.button("🔧 Structurer & recalculer", disabled=not aj_texte.strip()):
        try:
            with st.spinner("Appel LM Studio (Prompt 4)..."):
                aj = llm.structurer_ajustement(mois, legende_etendu(), noms, aj_texte)
        except llm.ErreurLLM as e:
            st.error(str(e))
            st.stop()
        ajustements = aj.get("ajustements") or []
        for c in aj.get("a_clarifier") or []:
            st.info(c.get("question", ""))
        if not ajustements:
            st.warning("Aucun ajustement structuré — préciser la personne, la date et l'intention.")
            st.stop()
        st.markdown("##### Ajustements structurés")
        st.dataframe(pd.DataFrame(ajustements), width="stretch")
        ajoutes = 0
        for a in ajustements:
            nom = a.get("personne")
            if nom not in noms:
                st.warning(f"Agent inconnu « {nom} » ignoré.")
                continue
            bloc = st.session_state.desiderata.setdefault(
                nom, {"structurees": [], "a_clarifier": [], "conflits_detectes": []})
            deja = any(
                x.get("date") == a.get("date")
                and x.get("categorie") == a.get("categorie")
                and (x.get("poste_concerne") or None) == (a.get("poste_concerne") or None)
                for x in bloc["structurees"])
            if deja:
                continue
            bloc["structurees"].append({
                "date": a.get("date"),
                "categorie": a.get("categorie"),
                "poste_concerne": a.get("poste_concerne"),
                "priorite": a.get("priorite", "moyenne"),
                "motif_resume": a.get("motif_resume") or "ajustement libre",
            })
            ajoutes += 1
        if ajoutes:
            st.success(f"{ajoutes} ajustement(s) ajouté(s) aux desiderata — recalcul...")
            _lancer_reso()
        else:
            st.info("Ces ajustements sont déjà dans les desiderata — recalculez depuis l'onglet 2 si besoin.")

    # Explication LLM
    st.markdown("#### Explication & arbitrages (Prompt 2)")
    b1, b2, b3, b4 = st.columns(4)
    if b1.button("🤖 Générer l'explication", type="primary"):
        with st.spinner("Appel LM Studio (Prompt 2)..."):
            try:
                st.session_state.explication = llm.expliquer_planning(
                    mois, legende_etendu(), pl, st.session_state.desiderata,
                    st.session_state.personnel, seuil_ecart_h=seuil_ecart,
                    diagnostic=st.session_state.get("diagnostic"))
                st.rerun()
            except llm.ErreurLLM as e:
                st.error(str(e))
    ex = st.session_state.explication
    if ex:
        st.markdown(f"**Résumé** : {ex.get('resume_global', '')}")
        d = ex.get("desiderata_non_satisfaites") or []
        if d:
            st.markdown("##### Désiderata non satisfaites")
            st.dataframe(pd.DataFrame([
                {"personne": r.get("personne"), "date": r.get("date"), "raison": r.get("raison"),
                 "pistes_resolution": " ; ".join(r.get("pistes_resolution") or [])} for r in d]),
                width="stretch")
        e = ex.get("ecarts_temps_signales") or []
        if e:
            st.markdown("##### Écarts de temps signalés")
            st.dataframe(pd.DataFrame([
                {"personne": r.get("personne"), "ecart_h": r.get("ecart_h"),
                 "cause_probable": r.get("cause_probable")} for r in e]),
                width="stretch")
        a = ex.get("alertes_equite") or []
        if a:
            st.markdown("##### Alertes équité")
            st.dataframe(pd.DataFrame([{"description": r.get("description")} for r in a]),
                         width="stretch")
        with st.expander("JSON brut"):
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
