"""Smoke test LLM — les deux prompts doivent répondre avec finish_reason='stop' et un content non vide.

Verrouille contre une régression silencieuse si un futur modèle réactive le « thinking »
par défaut (content vide, tout dans reasoning_content) — `chat_template_kwargs`
désactivant la réflexion doit rester effectif.

Saut proprement (exit 0) si LM Studio est injoignable ou si le modèle n'est pas chargé :
l'environnement peut tourner sans le serveur, le test doit alors se taire, pas échouer.
"""
from __future__ import annotations

import json
import sys

import requests

import llm


def _contenu_cru(system_prompt: str, contexte: dict, max_tokens: int = 2000) -> tuple[str, str]:
    """Appel minimal direct (hors `_appeler`) : retourne (finish_reason, content).

    Budget aligné sur les appels de production (2000/3000) : le modèle « qwen35 »
    pense avant de répondre (~200-300 tokens de réflexion) — un budget trop faible
    le coupe en `length` avec content vide, ce qui serait un faux positif.
    """
    r = requests.post(
        llm.LMSTUDIO_URL,
        json={
            "model": llm.MODELE,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(contexte, ensure_ascii=False)},
            ],
            "temperature": 0.1,
            "max_tokens": max_tokens,
            "chat_template_kwargs": {"enable_thinking": False},
        },
        timeout=llm._timeout_s(),
    )
    r.raise_for_status()
    choix = r.json()["choices"][0]
    return (choix.get("finish_reason") or "", (choix["message"].get("content") or ""))


def main() -> int:
    ok, detail = llm.statut_endpoint()
    if not ok:
        print(f"SKIP — LM Studio non prêt ({detail}). Smoke test LLM ignoré.")
        return 0

    ctx1 = {
        "mois": "2026-10",
        "legende_codes": {"M": "matin", "S": "soir", "N": "nuit", "12": "poste 12h"},
        "personnel": [{"nom": "X.Y.", "role": "infirmier", "affectations_fixes": []}],
        "desiderata_brutes": "pas de nuit le 12 octobre, congé souhaité du 20 au 22",
    }
    ctx2 = {
        "mois": "2026-10",
        "legende_codes": {"M": "matin", "S": "soir", "N": "nuit", "12": "poste 12h"},
        "planning": {"statut": "FEASIBLE", "grille": {"2026-10-12": {"X.Y.": "M"}}},
        "infaisabilites": [],
        "desiderata": {"X.Y.": {"structurees": [], "a_clarifier": [], "conflits_detectes": []}},
        "personnel": [{"nom": "X.Y.", "role": "infirmier", "temps_du_mois_h": 167.2}],
        "seuil_ecart_h": 8.0,
    }

    echecs = []
    for nom_prompt, prompt, ctx in (
        ("analyste (Prompt 1)", llm.PROMPT_ANALYSTE, ctx1),
        ("arbitre (Prompt 2)", llm.PROMPT_ARBITRE, ctx2),
    ):
        try:
            finish, content = _contenu_cru(prompt, ctx)
        except Exception as e:  # noqa: BLE001 — le test doit signaler tout échec d'appel
            echecs.append(f"{nom_prompt} : erreur d'appel — {type(e).__name__} : {e}")
            continue
        if finish != "stop":
            echecs.append(f"{nom_prompt} : finish_reason={finish!r} (attendu 'stop')")
        if not content.strip():
            echecs.append(f"{nom_prompt} : content vide (réflexion active ?)")

    if echecs:
        print("ÉCHEC smoke test LLM :")
        for e in echecs:
            print("  -", e)
        return 1
    print("OK — les deux prompts répondent en JSON non vide (finish_reason='stop').")
    return 0


if __name__ == "__main__":
    sys.exit(main())
