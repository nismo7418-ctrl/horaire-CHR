"""Persistance de l'état de session (P4) — desiderata + contraintes_extras, par mois.

Fichier : data/state/{mois}.json (initiales + motifs courts, comme data/personnel.json —
aucune donnée nominative). Le fichier est recréé à la demande et effacé par
« Réinitialiser la session » dans l'app.

Usage :
    import state
    etat = state.charger("2026-10")   # {"desiderata": {...}, "contraintes_extras": [...]}
    state.sauver("2026-10", desiderata, contraintes_extras)
    state.effacer("2026-10")
"""
from __future__ import annotations

import json
import os
from datetime import datetime

DOSSIER = os.path.join("data", "state")


def _chemin(mois: str) -> str:
    return os.path.join(DOSSIER, f"{mois}.json")


def charger(mois: str) -> dict:
    """Lit l'état persisté du mois. Retourne des structures vides si absent/invalide."""
    try:
        with open(_chemin(mois), encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {"desiderata": {}, "contraintes_extras": []}
    return {
        "desiderata": d.get("desiderata") or {},
        "contraintes_extras": d.get("contraintes_extras") or [],
    }


def sauver(mois: str, desiderata: dict, contraintes_extras: list) -> None:
    os.makedirs(DOSSIER, exist_ok=True)
    with open(_chemin(mois), "w", encoding="utf-8") as f:
        json.dump(
            {
                "mois": mois,
                "maj": datetime.now().isoformat(timespec="seconds"),
                "desiderata": desiderata or {},
                "contraintes_extras": contraintes_extras or [],
            },
            f, ensure_ascii=False, indent=1,
        )


def effacer(mois: str) -> bool:
    try:
        os.remove(_chemin(mois))
        return True
    except OSError:
        return False
