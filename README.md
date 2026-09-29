# Planning du service des urgences

Architecture en 3 briques, un seul processus Python :

```
[1] Désiderata (texte libre)  ──Prompt 1──▶  LLM local (LM Studio) ──▶ JSON structuré
[2] Génération  ──────────────▶  Solveur CP-SAT (OR-Tools)          ──▶ Grille + statut
[3] Résultats / Explication   ──Prompt 2──▶  LLM local (LM Studio) ──▶ Explications, arbitrages
```

**Le LLM ne génère jamais la grille** — il structure les desiderata en amont et explique
le résultat en aval. Une hallucination du modèle ne peut pas corrompre une affectation.

**Anonymisation / RGPD** —
- les agents sont désignés UNIQUEMENT par leurs initiales (ex: « P.K. ») dans les données,
  les prompts LLM, la grille et les exports ; aucun nom complet n'est stocké ni reproduit
  (les prompts l'interdisent explicitement au modèle) ;
- aucun nom d'établissement dans le code, les exports ou les fichiers ;
- LLM 100% local (LM Studio) — aucune donnée envoyée à un service externe ;
- les `desiderata_brutes` contiennent des motifs libres (ex: « RDV médical ») : ne pas y
  écrire de données de santé détaillées ; les exports sont partagés uniquement avec le
  service de planification concerné (finalité exclusive).

## Fichiers

| Fichier | Rôle |
|---|---|
| `config.py` | **Toute la donnée métier** : horaires des postes, repos 11h, repos hebdo, nuit 8h, poids des contraintes souples |
| `solveur.py` | Modèle CP-SAT : variables, contraintes dures (D1-D7), souples (S1-S5), extraction |
| `llm.py` | Client LM Studio + les 2 prompts système (température 0.1) |
| `app.py` | App Streamlit (3 onglets) : saisie → analyse LLM → résolution → grille/heures/export CSV+Excel |
| `data/personnel.json` | 18 agents d'exemple (rôles, taux, heures dues, desiderata brutes ; 2 infirmières en chef) |
| `data/historique/` | Grilles historiques transcrites (JSON) — mémoire du service, backtest (P2) |
| `data/effectifs_min.json` | Effectifs minimums par groupe de rôles / groupe de postes, avec différenciation semaine / week-end |
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
- **D4** — Effectifs minimums par rôle/poste/jour (valeur : entier, ou `{"semaine": n, "weekend": n}`)
- **D5** — Repos quotidien 11h (matrice de compatibilité précalculée d'après `config.POSTES`)
- **D6** — Repos hebdomadaire ≈ max 5 jours travaillés / fenêtre glissante de 7 jours
- **D7** — Élégibilité rôle → poste (`config.POSTES_AUTORISES_PAR_ROLE` ; ex: IC réservé
  aux `infirmiere_en_chef`). Affectation fixe / impératif sur un poste non autorisé →
  avertissement + journée marquée absence.

Contraintes souples (objectives pondérées, cf. `config.POIDS`) : respect des heures dues,
**S5 — soldes** (pénalise un solde fin de mois négatif : `solde_reporte_h + heures planifiées
- heures dues`), souhait_negatif (minimiser), souhait_positif (maximiser),
**S4 — habitudes horaires** (bonus si l'agent tient son poste habituel le jour concerné ;
saisie dans `data/personnel.json`, champ `habitudes`, ou dans l'onglet 2 de l'app),
équité des nuits et des week-ends au sein de chaque rôle.

## ⚠️ À VALIDER AVANT MISE EN PRODUCTION

1. **Horaires réels des postes** — M/S/12h et nuits par jour confirmés ; nuit de jour férié
   (20h00→7h15 semaine / 8h00 WE) confirmée. `config.JOURS_FERIES` à compléter mois par mois.
   IC (infirmière en chef) : 6h54→15h00 (8h06) — **confirmé** par le service.
2. **Légende des codes** — confirmés : FO (formation), dp (dispense de prestation),
   ❓ jaune (formation), ❓ rouge (maladie), 🌴 (congé), SR/HP (annotations SMUR / hospitalisation
   provisoire), **C (fond vert) = congé sans solde (CSS)**, **E (fond rouge) = maladie prolongée**.
   Restent à confirmer : `DDI`, `U`, et les variantes horaires des cases (M1/M6, S1/S5/S6/S8,
   N1/N4/N8/N12 — non modélisées, le solveur traite M/S/N/12 comme des postes uniques) ;
   un code non reconnu en `affectations_fixes` force une absence + avertissement.
   **Élégibilité D7 — confirmée par le service** : SMUR réservé aux siamu ; aide-soignant
   jours uniquement (M/S/12) ; logistique nuits ("rarement jour" → dérogation souple P3) ;
   IC réservé aux `infirmiere_en_chef`. Poste `SMUR` modélisé (13h15→21h00 d'après l'annotation
   HP_mauve — horaires à confirmer), non encore requis par `effectifs_min.json`.
   ⚠️ Connaître : le souhait "pas de nuit" d'un agent logistique (V.G. dans les données
   d'exemple) est structurellement incompatible avec la règle logistique = nuits.
   ⚠️ Cible : export CSV/Excel pour ressaisie dans **PEPS** (https://peps.me/) — pas d'API.
3. **Bases légales** (repos 11h — loi 16/03/1971 ; nuit 8h en 21h-6h — loi 17/02/1997 ;
   repos hebdo 35h) sont des **approximations CP-SAT** à valider avec RH / CCT 46 / CP330.
   La règle 21h-6h ≤ 8h est appliquée via `config.NIGHT_HOURS_MAX` et
   `config.POSTES_EXEMPTS_NUIT` (liste blanche CCT — à justifier par écrit).
4. **Effectifs minimums** (`data/effectifs_min.json`) — doivent être cohérents avec
   l'effectif réel (le solveur devient infeasible sinon ; voir `debug.py`).
   Format : entier (tous les jours) ou `{"semaine": n, "weekend": n}` (ex :
   `"jour": {"semaine": 5, "weekend": 3}`). Valeurs actuelles calées sur les grilles
   historiques 2023-2024 (Total semaine ≈ 12-17, week-end ≈ 8-10, IC = 1 en semaine / 0 WE).
5. **Variantes horaires** M1/M6, S1/S5/S6/S8, N1/N4/N8/N12 : horaires exacts à
   confirmer (non modélisées).
