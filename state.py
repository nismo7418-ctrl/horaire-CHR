"""Persistance de l'état de session (P4) — desiderata + contraintes_extras, par mois.

Fichier : data/state/{mois}.json (initiales + motifs courts, comme data/personnel.json —
aucune donnée nominative). Le fichier est recréé à la demande et effacé par
« Réinitialiser la session » dans l'app.

Depuis la phase « intelligence » : le résultat final (grille + stats) est aussi persisté
si fourni — il alimente la mémoire inter-mois de diagnostic.py (mois précédent).

Usage :
    import state
    etat = state.charger("2026-10")   # {"desiderata": {...}, "contraintes_extras": [...]}
    state.sauver("2026-10", desiderata, contraintes_extras, grille=..., stats=...)
    grille_prev, stats_prev = state.charger_resultat("2026-09")
    state.effacer("2026-10")
"""
from __future__ import annotations

import json
import os
from datetime import datetime

DOSSIER = os.path.join("data", "state")


def _chemin(mois: str) -> str:
    return os.path.join(DOSSIER, f"{mois}.json")


def _charger_brut(mois: str) -> dict:
    """Lit le fichier brut du mois (toutes clés). {} si absent/invalide."""
    try:
        with open(_chemin(mois), encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    return d if isinstance(d, dict) else {}


def charger(mois: str) -> dict:
    """Lit l'état persisté du mois. Retourne des structures vides si absent/invalide."""
    d = _charger_brut(mois)
    return {
        "desiderata": d.get("desiderata") or {},
        "contraintes_extras": d.get("contraintes_extras") or [],
    }


def charger_resultat(mois: str) -> tuple[dict, dict]:
    """Résultat final persisté (grille + stats) — ({}, {}) si le mois n'en a pas."""
    d = _charger_brut(mois)
    return (d.get("grille") or {}, d.get("stats") or {})


def sauver(mois: str, desiderata: dict, contraintes_extras: list,
          grille: dict | None = None, stats: dict | None = None) -> None:
    """Écrit l'état en fusionnant avec le fichier existant : grille/stats déjà persistés
    (résultat du dernier solve) survivent aux sauvegardes ultérieures de desiderata."""
    d = _charger_brut(mois)
    d["mois"] = mois
    d["maj"] = datetime.now().isoformat(timespec="seconds")
    d["desiderata"] = desiderata or {}
    d["contraintes_extras"] = contraintes_extras or []
    if grille is not None:
        d["grille"] = grille
    if stats is not None:
        d["stats"] = stats
    os.makedirs(DOSSIER, exist_ok=True)
    with open(_chemin(mois), "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)


def effacer(mois: str) -> bool:
    try:
        os.remove(_chemin(mois))
        return True
    except OSError:
        return False
