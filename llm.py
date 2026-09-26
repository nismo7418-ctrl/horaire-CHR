"""Client LM Studio (API compatible OpenAI) + les deux prompts système.

LLM local typique : http://localhost:1234/v1/chat/completions
Température basse (0.1) : fiabilité structurelle > créativité.
"""
from __future__ import annotations

import json
import os

import requests

LMSTUDIO_URL = os.environ.get("LMSTUDIO_URL", "http://localhost:1234/v1/chat/completions")
MODELE = os.environ.get("LMSTUDIO_MODELE", "qwen3.8-27b")  # nom exact du modèle chargé dans LM Studio


class ErreurLLM(Exception):
    pass


PROMPT_ANALYSTE = """Tu es un analyste de planification hospitalière. Ton unique rôle est de
transformer les desiderata en texte libre du personnel des urgences en données structurées,
JAMAIS de produire un planning toi-même.

RÈGLES STRICTES :
1. Tu ne connais QUE les informations fournies dans le contexte (mois, légende des codes,
   liste du personnel, desiderata brutes). N'invente jamais une date hors du mois concerné,
   un code absent de la légende fournie, ou une personne absente de la liste.
2. Pour chaque desiderata, classe-la dans exactement une catégorie :
   - "impératif" : congé déjà posé/validé, formation obligatoire, code fixe existant
   - "souhait_positif" : "je voudrais travailler le X" / "je préfère les matins"
   - "souhait_negatif" : "pas de nuit le X" / "éviter le week-end du Y"
   - "indisponibilite" : incapacité totale ce jour-là (rendez-vous médical, garde d'enfant...)
   - "ambigu" : formulation qui ne permet pas de déterminer la date, le poste ou l'intention
     avec certitude
3. Attribue une priorité : "haute" (impératif ou raison médicale/familiale explicite),
   "moyenne" (préférence exprimée clairement), "basse" (souhait vague ou secondaire).
4. Si une desiderata est classée "ambigu", NE LA DEVINE PAS. Place-la dans "a_clarifier"
   avec une question précise à reposer à la personne.
5. Signale toute incohérence détectée (ex: desiderata portant sur une date où la personne
   a déjà une affectation_fixe) dans "conflits_detectes".
6. Réponds UNIQUEMENT en JSON valide, sans texte avant/après, selon le schéma :

{
  "personnel_traite": "NOM Prénom",
  "desiderata_structurees": [
    {
      "date": "YYYY-MM-DD",
      "categorie": "impératif|souhait_positif|souhait_negatif|indisponibilite|ambigu",
      "poste_concerne": "matin|soir|nuit|12h|indifferent|null",
      "priorite": "haute|moyenne|basse",
      "motif_resume": "reformulation courte, factuelle, sans interprétation ajoutée"
    }
  ],
  "a_clarifier": [{"date_ou_periode": "...", "question": "..."}],
  "conflits_detectes": [{"description": "..."}]
}"""

PROMPT_ARBITRE = """Tu es l'assistant du responsable de planification du service des urgences.
Le planning lui-même a été calculé par un solveur de contraintes (résultat fourni en entrée).
Ton rôle est d'expliquer ce résultat en langage clair et de proposer des arbitrages —
JAMAIS de modifier les affectations toi-même.

CONTEXTE FOURNI : planning généré (grille personne/jour/poste), liste des contraintes
dures violées ou impossibles à satisfaire simultanément (si le solveur est en
infaisabilité partielle), desiderata structurées (sortie du prompt 1),
soldes/temps dû par personne.

RÈGLES STRICTES :
1. Tu ne recalcules jamais une affectation. Tu commentes et expliques uniquement le résultat fourni.
2. Pour chaque desiderata "haute" priorité non respectée dans le planning : explique pourquoi
   (quelle contrainte dure l'en a empêché — effectif minimum, repos légal, skill-mix SIAMU)
   et propose 1 à 3 pistes concrètes de résolution (ex: échange avec une autre personne
   disponible ce jour-là, report sur une autre date).
3. Signale toute personne dont l'écart entre temps_du_mois_h et heures effectivement
   planifiées dépasse le seuil fourni, en expliquant la cause.
4. Vérifie et signale explicitement toute rotation inéquitable détectable dans les données
   fournies (ex: une personne cumule nettement plus de nuits ou de week-ends que la moyenne
   du groupe de même rôle) — sans supposer de cause que les données ne montrent pas.
5. Ne fais jamais de recommandation qui contredirait une contrainte dure marquée comme telle
   dans les données (repos légal, effectif minimum).
6. Réponds en JSON selon le schéma, avec des champs texte en français clair, factuel, sans
   emphase inutile :

{
  "resume_global": "synthèse en 3-4 phrases maximum",
  "desiderata_non_satisfaites": [
    {"personne": "...", "date": "...", "raison": "...", "pistes_resolution": ["...", "..."]}
  ],
  "ecarts_temps_signales": [{"personne": "...", "ecart_h": 0, "cause_probable": "..."}],
  "alertes_equite": [{"description": "..."}]
}"""


def _extraire_json(texte: str) -> dict:
    """Parse le JSON d'une réponse LLM (tolère les ```json fences et le texte parasite)."""
    t = texte.strip()
    if t.startswith("```"):
        t = t.strip("`")
        if t.lower().startswith("json"):
            t = t[4:]
    debut, fin = t.find("{"), t.rfind("}")
    if debut == -1 or fin == -1:
        raise ErreurLLM(f"Aucun JSON trouvé dans la réponse : {texte[:200]!r}")
    return json.loads(t[debut:fin + 1])


def _appeler(system_prompt: str, contexte: dict, max_tokens: int = 2000) -> dict:
    try:
        r = requests.post(
            LMSTUDIO_URL,
            json={
                "model": MODELE,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": json.dumps(contexte, ensure_ascii=False)},
                ],
                "temperature": 0.1,
                "max_tokens": max_tokens,
            },
            timeout=180,
        )
        r.raise_for_status()
        return _extraire_json(r.json()["choices"][0]["message"]["content"])
    except requests.RequestException as e:
        raise ErreurLLM(f"LM Studio injoignable sur {LMSTUDIO_URL} : {e}") from e
    except (KeyError, json.JSONDecodeError) as e:
        raise ErreurLLM(f"Réponse du modèle invalide : {e}") from e


def analyser_desiderata(mois: str, legende_codes: dict,
                        agent: dict, texte: str) -> dict:
    """Prompt 1 — structure les desiderata d'UN agent. Retourne le dict JSON."""
    contexte = {
        "mois": mois,
        "legende_codes": legende_codes,
        "personnel": [{k: agent[k] for k in ("nom", "role", "affectations_fixes") if k in agent}],
        "desiderata_brutes": texte,
    }
    return _appeler(PROMPT_ANALYSTE, contexte)


def expliquer_planning(mois: str, legende_codes: dict,
                       planning_result: dict, desiderata: dict,
                       personnel: list, seuil_ecart_h: float = 8.0) -> dict:
    """Prompt 2 — explique le planning produit par le solveur. Retourne le dict JSON."""
    contexte = {
        "mois": mois,
        "legende_codes": legende_codes,
        "seuil_ecart_heures_h": seuil_ecart_h,
        "statut_solveur": planning_result.get("statut"),
        "planning": planning_result.get("grille", {}),
        "heures_planifiees": planning_result.get("heures", {}),
        "stats_equite": planning_result.get("stats", {}),
        "desiderata_structurees": desiderata,
        "personnel": [
            {k: p.get(k) for k in ("nom", "role", "taux_occupation", "temps_du_mois_h", "solde_reporte_h") if k in p}
            for p in personnel
        ],
        "contraintes_dures": {
            "repos_quotidien_h": 11,
            "semaine_max_jours_travailles": 5,
            "nuit_21h_6h_max_h": 8,
            "effectifs_min": "voir données d'entrée (non modifiables)",
        },
    }
    return _appeler(PROMPT_ARBITRE, contexte, max_tokens=3000)
