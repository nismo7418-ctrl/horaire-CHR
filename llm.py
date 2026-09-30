"""Client LM Studio (API compatible OpenAI) + les deux prompts système.

LLM local typique : http://localhost:1234/v1/chat/completions
Température basse (0.1) : fiabilité structurelle > créativité.
"""
from __future__ import annotations

import json
import time
from urllib.parse import urlsplit

import requests

import app_secrets as _cfg

LMSTUDIO_URL = _cfg.secret("LMSTUDIO_URL", "http://localhost:1234/v1/chat/completions")
MODELE = _cfg.secret("LMSTUDIO_MODELE", "qwen3.8-27b")  # nom exact du modèle chargé


def _timeout_s() -> int:
    """Timeout HTTP (secondes). Un 27B local peut mettre des minutes sur un JSON long."""
    try:
        return int(_cfg.secret("LMSTUDIO_TIMEOUT_S", "600"))
    except ValueError:
        return 600


def _origine(url: str) -> str:
    """Origine (schéma+host) seulement — jamais le chemin complet dans un message d'erreur."""
    p = urlsplit(url)
    return f"{p.scheme}://{p.netloc}" if p.netloc else "endpoint inconnu"


def endpoint_est_local() -> bool:
    return urlsplit(LMSTUDIO_URL).netloc.split(":")[0] in ("localhost", "127.0.0.1", "::1")


def statut_endpoint(timeout_s: float = 4.0) -> tuple[bool, str]:
    """État du service LLM pour le badge d'interface.

    Retourne (ok, detail) : ok=True si l'endpoint répond ET que le modèle
    attendu est chargé. En cas d'erreur réseau, ok=False avec la raison.
    """
    # Racine du serveur : LMSTUDIO_URL peut être l'endpoint complet
    # (…/v1/chat/completions) ou juste la racine — ne retirer que le préfixe /v1/.
    server = LMSTUDIO_URL.split("/v1/", 1)[0].rstrip("/")
    try:
        r = requests.get(f"{server}/v1/models", timeout=timeout_s)
        r.raise_for_status()
        ids = [m.get("id", "") for m in r.json().get("data", [])]
    except requests.RequestException as e:
        return False, f"injoignable ({_origine(LMSTUDIO_URL)}) : {type(e).__name__}"
    if MODELE not in ids and not any(i.endswith("/" + MODELE) for i in ids):
        return False, f"modèle « {MODELE} » absent de la liste : {', '.join(ids[:3]) or 'aucun'}"
    # API de gestion LM Studio : état de chargement exact — certaines versions
    # listent aussi les modèles DÉCHARGÉS dans /v1/models, le badge ne doit pas
    # les compter comme prêts.
    try:
        ra = requests.get(f"{server}/api/v0/models", timeout=timeout_s)
        if ra.ok:
            states = {m.get("id", ""): m.get("state") for m in ra.json().get("data", [])}
            etat = states.get(MODELE) or next(
                (s for i, s in states.items() if i.endswith("/" + MODELE)), None)
            if etat == "loaded":
                return True, f"modèle « {MODELE} » chargé"
            if etat is not None:
                return False, f"modèle « {MODELE} » non chargé (état : {etat})"
    except requests.RequestException:
        pass
    return True, f"modèle « {MODELE} » listé (état de chargement non vérifiable)"


class ErreurLLM(Exception):
    pass


PROMPT_ANALYSTE = """Tu es un analyste de planification hospitalière. Ton unique rôle est de
transformer les desiderata en texte libre du personnel des urgences en données structurées,
JAMAIS de produire un planning toi-même.

RÈGLES STRICTES :
1. Tu ne connais QUE les informations fournies dans le contexte (mois, légende des codes,
   liste du personnel, desiderata brutes). N'invente jamais une date hors du mois concerné,
   un code absent de la légende fournie, ou une personne absente de la liste.
   Les agents sont désignés UNIQUEMENT par leurs initiales (telles que fournies dans le
   contexte, ex: « P.K. »). N'écris jamais un nom complet, ne tente jamais de le deviner,
   et ne reproduis jamais une information personnelle hors du motif factuel du desiderata.
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
  "personnel_traite": "initiales (ex: P.K.)",
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
habitudes horaires hebdomadaires par agent (contrainte souple — bonus « poste habituel »),
soldes/temps dû par personne.

RÈGLES STRICTES :
0. Les agents sont désignés UNIQUEMENT par leurs initiales (telles que fournies dans le
   contexte, ex: « P.K. »). N'écris jamais un nom complet et ne tente jamais de le deviner.
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
5. Si le champ « diagnostic_deterministe » est fourni : c'est une analyse FACTUELLE du planning
   (chiffres mesurés par le logiciel, pas des suppositions). Tes explications doivent s'appuyer
   dessus : reprends dans « alertes_equite » les alertes « equite » et « memore_inter_mois »
   avec leurs chiffres exacts (écarts, cumulés 2 mois, initiales des agents) et leur nature de
   fait mesuré — ne les reformule jamais en « cause probable ». Les alertes « nuits_consecutives »
   vont dans « resume_global » ou « alertes_equite » selon leur poids ; celles de « soldes » dans
   « ecarts_temps_signales » (cause : solde reporté + heures planifiées).
   (les règles 5 et 6 ci-dessous deviennent 6 et 7)
6. Ne fais jamais de recommandation qui contredirait une contrainte dure marquée comme telle
   dans les données (repos légal, effectif minimum).
7. Réponds en JSON selon le schéma, avec des champs texte en français clair, factuel, sans
   emphase inutile :

{
  "resume_global": "synthèse en 3-4 phrases maximum",
  "desiderata_non_satisfaites": [
    {"personne": "initiales", "date": "...", "raison": "...", "pistes_resolution": ["...", "..."]}
  ],
  "ecarts_temps_signales": [{"personne": "initiales", "ecart_h": 0, "cause_probable": "..."}],
  "alertes_equite": [{"description": "..."}]
}"""


PROMPT_ARBITRAGE = """Tu es l'assistant du responsable de planification du service des urgences.
Des desiderata souples (souhaits) n'ont pas été honorées par le planning calculé par un solveur
contraintes. Ton rôle : proposer des arbitrages CONCRETS et EXACTEMENT de deux types, afin que le
solveur puisse les recalculer. Tu ne modifies JAMAIS toi-même le planning.

CONTEXTE FOURNI : mois, légende des postes, souhaits non honorés (avec la situation réelle du
jour concerné), grille des jours ± 1 autour de chaque souhait (agent → code poste), éligibilité
des rôles (postes autorisés / dérogables), planchers d'effectifs minimums par groupe et par jour.

RÈGLES STRICTES :
0. Les agents sont désignés UNIQUEMENT par leurs initiales (telles que fournies). N'invente jamais
   une personne absente de la grille, une date hors du mois, ou un code absent de la légende.
1. Chaque proposition doit être EXACTEMENT l'une de ces deux actions :
   - "repos" : libérer l'agent le jour indiqué (résout un souhait_negatif) ;
   - "echange" : échanger les postes de deux agents LE MÊME JOUR (résout un souhait_positif ou
     un souhait_negatif si l'un des deux tient le poste indésirable).
2. Ne propose JAMAIS une action qui violerait un plancher d'effectif minimum fourni
   (ex: si l'agent est l'un des seuls du poste requis ce jour-là, ne propose pas son repos ;
   trouve un collègue du même groupe qui tient un poste compatible à l'échange).
3. Cible d'abord les souhaits de priorité "haute". Maximum 5 propositions, de la plus sûre à la
   plus risquée. Si aucune action sûre n'existe pour un souhait, ne le propose pas.
4. Pour chaque proposition : un motif en une phrase, factuel, qui renvoie au souhait adressé.
5. Réponds UNIQUEMENT en JSON valide, sans texte avant/après, selon le schéma :

{
  "propositions": [
    {"type": "repos", "agent": "P.K.", "date": "AAAA-MM-DD", "motif": "..."},
    {"type": "echange", "agent_a": "P.K.", "agent_b": "N.M.", "date": "AAAA-MM-DD", "motif": "..."}
  ]
}"""

PROMPT_AJUSTEMENT = """Tu es un analyste de planification hospitalière. L'utilisateur (responsable de la
planification) formule un ajustement en langage naturel sur le planning (ex: « P.K. pas de nuit le
17/10 »). Transforme-le en desiderata structurées, UNE PAR UNE. Tu ne produis JAMAIS de planning.

RÈGLES STRICTES :
0. Les agents sont désignés UNIQUEMENT par leurs initiales. N'invente jamais une personne absente
du contexte, une date hors du mois fourni, ou un code absent de la légende.
1. Chaque ligne doit contenir : personne (initiales), date (AAAA-MM-DD), categorie
   (indisponibilite|impératif|souhait_positif|souhait_negatif), poste_concerne
   (matin|soir|nuit|12h|infirmiere_en_chef|null), priorite (haute|moyenne|basse),
   motif_resume (court, factuel).
2. « pas de X le J » → souhait_negatif ; « je voudrais X le J » → souhait_positif ;
   « X est posé/validé/obligatoire le J » → impératif ; « X ne peut pas travailler le J » →
   indisponibilite. Si l'agent est concerné quel que soit le poste → poste_concerne null.
3. Une date relative (« le 17 ») s'entend dans le mois du contexte. Si la date, la personne ou
   l'intention reste indéterminée : place l'item dans "a_clarifier" avec une question précise.
4. Réponds UNIQUEMENT en JSON valide, sans texte avant/après, selon le schéma :

{
  "ajustements": [
    {"personne": "P.K.", "date": "AAAA-MM-DD", "categorie": "souhait_negatif",
     "poste_concerne": "nuit", "priorite": "haute", "motif_resume": "..."}
  ],
  "a_clarifier": [{"question": "..."}]
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
    body = {
        "model": MODELE,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(contexte, ensure_ascii=False)},
        ],
        "temperature": 0.1,
        "max_tokens": max_tokens,
        # Modèles à réflexion (Qwen3) : sans cette option, tout le budget de
        # tokens part en « thinking » et `content` revient vide. Ignorée par
        # les modèles non concernés.
        "chat_template_kwargs": {"enable_thinking": False},
    }
    def _http() -> str:
        try:
            r = requests.post(LMSTUDIO_URL, json=body, timeout=_timeout_s())
        except requests.ConnectionError:
            # LM Studio peut mettre quelques secondes à réagir après chargement du
            # modèle (compilation du KV cache) — une nouvelle tentative suffit.
            time.sleep(2)
            r = requests.post(LMSTUDIO_URL, json=body, timeout=_timeout_s())
        r.raise_for_status()
        msg = r.json()["choices"][0]["message"]
        texte = (msg.get("content") or "").strip()
        if not texte:
            # Filet de sécurité si le modèle a tout mis dans la réflexion.
            texte = (msg.get("reasoning_content") or "").strip()
        return texte

    try:
        texte = _http()
        try:
            return _extraire_json(texte)
        except json.JSONDecodeError:
            # Même prompt, température 0.1 : le modèle reste stochastique et peut
            # émettre un JSON tronqué/invalide — une nouvelle tentative suffit.
            time.sleep(1)
            return _extraire_json(_http())
    except requests.RequestException as e:
        raise ErreurLLM(f"LLM injoignable ({_origine(LMSTUDIO_URL)}) : {e}") from e
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


def proposer_arbitrages(mois: str, legende_codes: dict, souhaits_non_honores: list,
                        grille: dict, personnel: list, effectifs_min: dict) -> dict:
    """Prompt 3 — propose des arbitrages (repos / échanges) pour les souhaits non honorés.

    Le LLM ne fait que PROPOSER : chaque proposition est vérifiée de manière déterministe
    (solveur.verifier_repos / verifier_echange) avant application, puis le solveur recalcule.
    """
    from datetime import date, timedelta
    import config as _cfg

    def _fenetre(date_iso: str) -> list[str]:
        try:
            y, m, d = (int(v) for v in date_iso.split("-"))
            j0 = date(y, m, d)
        except (ValueError, TypeError):
            return []
        out = []
        for off in (-1, 0, 1):
            j = j0 + timedelta(days=off)
            if (j.year, j.month) == (y, m):
                out.append(j.isoformat())
        return out

    contexte_grille: dict = {}
    for s in souhaits_non_honores:
        for d in _fenetre(s.get("date", "")):
            if d in grille:
                contexte_grille[d] = grille[d]
    contexte = {
        "mois": mois,
        "legende_codes": legende_codes,
        "souhaits_non_honores": souhaits_non_honores,
        "grille_jours_concernes": contexte_grille,
        "eligibilite_roles": {
            "postes_autorises": _cfg.POSTES_AUTORISES_PAR_ROLE,
            "postes_derogables": _cfg.POSTES_DEROGABLES_PAR_ROLE,
        },
        "effectifs_min": {k: v for k, v in effectifs_min.items()},
        "personnel": [{k: p.get(k) for k in ("nom", "role") if k in p} for p in personnel],
    }
    return _appeler(PROMPT_ARBITRAGE, contexte)


def structurer_ajustement(mois: str, legende_codes: dict, initiales: list[str], texte: str) -> dict:
    """P3.4 — ajustement conversationnel : « P.K. pas de nuit le 17/10 » → desiderata structurées.

    L'ajustement est ensuite fusionné dans les desiderata de session et le solveur recalcule.
    """
    contexte = {
        "mois": mois,
        "legende_codes": legende_codes,
        "personnel_autorise": initiales,
        "ajustement_libre": texte,
    }
    return _appeler(PROMPT_AJUSTEMENT, contexte)


def expliquer_planning(mois: str, legende_codes: dict,
                       planning_result: dict, desiderata: dict,
                       personnel: list, seuil_ecart_h: float = 8.0,
                       diagnostic: dict | None = None) -> dict:
    """Prompt 2 — explique le planning produit par le solveur. Retourne le dict JSON.

    diagnostic (phase « intelligence ») : sortie de diagnostic.diagnostiquer() —
    analyse déterministe (nuits consécutives, équité, cumuls inter-mois, soldes,
    stabilité d'habitudes) injectée dans le contexte pour des explications exactes.
    """
    contexte = {
        "mois": mois,
        "legende_codes": legende_codes,
        "seuil_ecart_heures_h": seuil_ecart_h,
        "statut_solveur": planning_result.get("statut"),
        "planning": planning_result.get("grille", {}),
        "heures_planifiees": planning_result.get("heures", {}),
        "stats_equite": planning_result.get("stats", {}),
        "desiderata_structurees": desiderata,
        "souhaits_non_honores": planning_result.get("souhaits_non_honores", []),
        "derogations": planning_result.get("derogations", []),
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
    if diagnostic:
        contexte["diagnostic_deterministe"] = diagnostic
    return _appeler(PROMPT_ARBITRE, contexte, max_tokens=3000)
