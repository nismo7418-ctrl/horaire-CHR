# Planning Urgences — CHR Haute Senne Soignies

Architecture en 3 briques, un seul processus Python :

```
[1] Désiderata (texte libre)  ──Prompt 1──▶  LLM local (LM Studio) ──▶ JSON structuré
[2] Génération  ──────────────▶  Solveur CP-SAT (OR-Tools)          ──▶ Grille + statut
[3] Résultats / Explication   ──Prompt 2──▶  LLM local (LM Studio) ──▶ Explications, arbitrages
```

**Le LLM ne génère jamais la grille** — il structure les desiderata en amont et explique
le résultat en aval. Une hallucination du modèle ne peut pas corrompre une affectation.

**Anonymisation** — les agents sont désignés UNIQUEMENT par leurs initiales (ex: « P.K. »)
dans les données, les prompts LLM, la grille et les exports. Aucun nom complet n'est
stocké ni reproduit (les prompts l'interdisent explicitement au modèle).

## Fichiers

| Fichier | Rôle |
|---|---|
| `config.py` | **Toute la donnée métier** : horaires des postes, repos 11h, repos hebdo, nuit 8h, poids des contraintes souples |
| `solveur.py` | Modèle CP-SAT : variables, contraintes dures (D1-D6), souples (S1-S3), extraction |
| `llm.py` | Client LM Studio + les 2 prompts système (température 0.1) |
| `app.py` | App Streamlit (3 onglets) : saisie → analyse LLM → résolution → grille/heures/export CSV+Excel |
| `data/personnel.json` | 12 agents d'exemple (rôles, taux, heures dues, desiderata brutes) |
| `data/effectifs_min.json` | Effectifs minimums par rôle/poste/jour |
| `test_solveur.py` | Test smoke du solveur sans LLM |
| `debug.py` | Décomposition des contraintes dures (identifie laquelle bloque) |

## Démarrage

```bash
pip install -r requirements.txt
python test_solveur.py          # vérifie le solveur seul (pas besoin de LLM)
streamlit run app.py            # http://localhost:8501
```

Pour les prompts LLM, LM Studio doit exposer `http://localhost:1234` (API compatible
OpenAI) avec le modèle chargé. Nom du modèle configurable via la variable d'environnement
`LMSTUDIO_MODELE` (défaut `qwen3.8-27b`) — adapter au nom exact du modèle chargé.

## Contraintes dures (solveur)

- **D1** — 1 poste max par agent et par jour
- **D2** — Affectations fixes (`affectations_fixes`) : code reconnu → poste imposé ; code inconnu → absence + avertissement
- **D3** — Desiderata dures : `impératif` (congé/formation) et `indisponibilite`
- **D4** — Effectifs minimums par rôle/poste/jour
- **D5** — Repos quotidien 11h (matrice de compatibilité précalculée d'après `config.POSTES`)
- **D6** — Repos hebdomadaire ≈ max 5 jours travaillés / fenêtre glissante de 7 jours

Contraintes souples (objectives pondérées, cf. `config.POIDS`) : respect des heures dues,
souhait_negatif (minimiser), souhait_positif (maximiser), équité des nuits et des
week-ends au sein de chaque rôle.

## ⚠️ À VALIDER AVANT MISE EN PRODUCTION

1. **Horaires réels des postes** — M/S/12h et nuits par jour confirmés ; nuit de jour férié
   (20h00→7h15 semaine / 8h00 WE) confirmée. `config.JOURS_FERIES` à compléter mois par mois.
2. **Légende des codes** — confirmés : FO (formation), dp (dispense de prestation),
   ❓ jaune (formation), ❓ rouge (maladie), 🌴 (congé), SR/HP (annotations SMUR / hospitalisation
   provisoire). Restent à confirmer : `DDI`, `U` ; un code non reconnu en `affectations_fixes`
   force une absence + avertissement.
3. **Bases légales** (repos 11h — loi 16/03/1971 ; nuit 8h en 21h-6h — loi 17/02/1997 ;
   repos hebdo 35h) sont des **approximations CP-SAT** à valider avec RH / CCT 46 / CP330.
   La règle 21h-6h ≤ 8h est appliquée via `config.NIGHT_HOURS_MAX` et
   `config.POSTES_EXEMPTS_NUIT` (liste blanche CCT — à justifier par écrit).
4. **Effectifs minimums** (`data/effectifs_min.json`) — doivent être cohérents avec
   l'effectif réel (le solveur devient infeasible sinon ; voir `debug.py`).
5. **Export** : CSV/Excel pour ressaisie manuelle dans PEP's (pas d'API PEP's).
