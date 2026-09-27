"""Accès unifié aux secrets applicatifs (déploiement cloud).

Priorité : variable d'environnement > `.streamlit/secrets.toml` > valeur par défaut.
Le fichier `secrets.toml` réel n'est jamais commité (cf. `.gitignore`) ; voir
`.streamlit/secrets.toml.example` pour le modèle.
"""
from __future__ import annotations

import os

try:
    import streamlit as st
    _st_secrets = getattr(st, "secrets", None)
except Exception:  # exécuté hors Streamlit (tests unitaires simples)
    _st_secrets = None


def secret(cle: str, defaut: str | None = None) -> str | None:
    """Retourne la valeur du secret `cle` (env > secrets.toml) ou `defaut`."""
    v = os.environ.get(cle)
    if not v and _st_secrets is not None:
        try:
            v = _st_secrets[cle]
        except Exception:
            v = None
    return v or defaut
