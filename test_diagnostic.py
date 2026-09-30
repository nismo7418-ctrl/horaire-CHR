"""Tests du diagnostic déterministe (phase « intelligence »).

Lance : python test_diagnostic.py

Cas couverts :
  1. nuits consécutives      : 3 d'affilée → alerte ; 2 → pas d'alerte ;
  2. équité                   : écart nuits ≥ seuil → alerte ; écart < seuil → pas d'alerte ;
  3. mémoire inter-mois       : cumul 2 mois ≥ seuil → alerte (fichier 2026-09 temporaire) ;
                                pas de fichier mois précédent → memoire_disponible = False ;
  4. soldes                   : solde fin de mois négatif → alerte ;
  5. stabilité habitudes      : ratio < 0.7 → alerte info ;
  6. grille/stats vides       : dict vide complet, aucune exception ;
  7. ordre des alertes        : warnings avant infos ;
  8. _mois_precedent          : passage d'année ('2026-01' → '2025-12').
"""
import sys

sys.stdout.reconfigure(encoding="utf-8")

import diagnostic
import state

MOIS = "2026-10"


def _grille_nuits(nom: str, dates: list[str], code="N"):
    """Grille minimale : `nom` sur `code` aux dates données, absent ailleurs."""
    return {d: {nom: code} for d in dates}


# ── 1. Nuits consécutives ────────────────────────────────────────────────
def test_nuits_consecutives():
    grille = _grille_nuits("A", ["2026-10-05", "2026-10-06", "2026-10-07"])
    stats = {"A": {"nuits": 3, "jours_weekend": 0, "solde_fin_de_mois_h": 0.0}}
    diag = diagnostic.diagnostiquer(grille, stats, [{"nom": "A", "role": "siamu"}], MOIS)
    assert diag["nuits_consecutives"] and diag["nuits_consecutives"][0]["plus_longue_rainure"] == 3
    assert any("3 nuits consécutives" in a["texte"] for a in diag["alertes"])

    # 2 nuits d'affilée = seuil honoré → pas d'alerte
    grille2 = _grille_nuits("A", ["2026-10-05", "2026-10-06"])
    stats2 = {"A": {"nuits": 2, "jours_weekend": 0, "solde_fin_de_mois_h": 0.0}}
    diag2 = diagnostic.diagnostiquer(grille2, stats2, [{"nom": "A", "role": "siamu"}], MOIS)
    assert not diag2["nuits_consecutives"] and not diag2["alertes"], diag2["alertes"]


# ── 2. Équité ─────────────────────────────────────────────────────────────
def test_equite():
    stats = {
        "A": {"nuits": 6, "jours_weekend": 6, "solde_fin_de_mois_h": 0.0},
        "B": {"nuits": 1, "jours_weekend": 2, "solde_fin_de_mois_h": 0.0},
    }
    personnel = [{"nom": "A", "role": "siamu"}, {"nom": "B", "role": "siamu"}]
    diag = diagnostic.diagnostiquer({}, stats, personnel, MOIS)
    ind = {e["indicateur"]: e for e in diag["equite"]}
    assert ind["equite_nuits"]["ecart"] == 5 and ind["equite_nuits"]["max"]["agent"] == "A"
    assert ind["equite_weekends"]["ecart"] == 4
    assert any("Équité nuits" in a["texte"] for a in diag["alertes"])
    assert any("Équité jours week-end" in a["texte"] for a in diag["alertes"])

    # Écarts sous les seuils → aucune alerte d'équité
    stats_ok = {
        "A": {"nuits": 3, "jours_weekend": 3, "solde_fin_de_mois_h": 0.0},
        "B": {"nuits": 2, "jours_weekend": 1, "solde_fin_de_mois_h": 0.0},
    }
    diag_ok = diagnostic.diagnostiquer({}, stats_ok, personnel, MOIS)
    assert not diag_ok["equite"] and not diag_ok["alertes"], diag_ok["alertes"]

    # Rôles distincts → jamais comparés entre eux
    personnel_melange = [{"nom": "A", "role": "siamu"}, {"nom": "B", "role": "infirmier"}]
    diag_mix = diagnostic.diagnostiquer({}, stats, personnel_melange, MOIS)
    assert not diag_mix["equite"] and not diag_mix["alertes"]


# ── 3. Mémoire inter-mois ─────────────────────────────────────────────────
def test_memoire_inter_mois():
    stats = {"A": {"nuits": 5, "jours_weekend": 4, "solde_fin_de_mois_h": 0.0}}
    personnel = [{"nom": "A", "role": "siamu"}]

    # Sans fichier mois précédent → mémoire indisponible, pas d'alerte de cumul
    assert state.charger_resultat("2026-09") == ({}, {})
    diag = diagnostic.diagnostiquer({}, stats, personnel, MOIS)
    assert not diag["memoire_disponible"] and diag["memoire_mois"] is None
    assert not diag["memore_inter_mois"] and not diag["alertes"]

    # Avec mois précédent persisté (10 nuits + 5 wkd) → cumul 15/9 ≥ seuils 14/8
    try:
        state.sauver("2026-09", {}, [],
                     stats={"A": {"nuits": 10, "jours_weekend": 5, "solde_fin_de_mois_h": 0.0}})
        diag2 = diagnostic.diagnostiquer({}, stats, personnel, MOIS)
        assert diag2["memoire_disponible"] and diag2["memoire_mois"] == "2026-09"
        assert diag2["memore_inter_mois"][0]["nuits"]["cumul"] == 15
        assert diag2["memore_inter_mois"][0]["jours_weekend"]["cumul"] == 9
        assert any("cumul 2 mois" in a["texte"] for a in diag2["alertes"])
    finally:
        state.effacer("2026-09")


# ── 4. Soldes ─────────────────────────────────────────────────────────────
def test_soldes():
    stats = {
        "A": {"nuits": 0, "jours_weekend": 0, "solde_fin_de_mois_h": -12.5},
        "B": {"nuits": 0, "jours_weekend": 0, "solde_fin_de_mois_h": 8.0},
    }
    personnel = [{"nom": "A", "role": "infirmier"}, {"nom": "B", "role": "infirmier"}]
    diag = diagnostic.diagnostiquer({}, stats, personnel, MOIS)
    assert diag["soldes"]["plus_negatif"] == {"agent": "A", "heures": -12.5}
    assert diag["soldes"]["plus_positif"] == {"agent": "B", "heures": 8.0}
    assert any("solde négatif de -12.5" in a["texte"] for a in diag["alertes"])

    # Solde tous positifs → pas d'alerte
    stats_pos = {"A": {"nuits": 0, "jours_weekend": 0, "solde_fin_de_mois_h": 2.0}}
    diag_pos = diagnostic.diagnostiquer({}, stats_pos, [{"nom": "A", "role": "infirmier"}], MOIS)
    assert diag_pos["soldes"]["plus_negatif"]["heures"] == 2.0
    assert not diag_pos["alertes"]


# ── 5. Stabilité habitudes ────────────────────────────────────────────────
def test_stabilite():
    # Lundis d'octobre 2026 : 5, 12, 19, 26. A fait M sur 2, N sur 2 → ratio 0.5 < 0.7
    grille = {
        "2026-10-05": {"A": "M"}, "2026-10-12": {"A": "M"},
        "2026-10-19": {"A": "N"}, "2026-10-26": {"A": "N"},
    }
    stats = {"A": {"nuits": 2, "jours_weekend": 0, "solde_fin_de_mois_h": 0.0}}
    personnel = [{"nom": "A", "role": "infirmier", "habitudes": {"lun": "M"}}]
    diag = diagnostic.diagnostiquer(grille, stats, personnel, MOIS)
    s = diag["stabilite"][0]
    assert s["agent"] == "A" and s["ratio"] == 0.5 and not s["conforme"]
    assert any("poste habituel honoré sur 2/4" in a["texte"] for a in diag["alertes"])

    # Habitude totalement honorée → conforme, pas d'alerte
    grille_ok = {"2026-10-05": {"A": "M"}, "2026-10-12": {"A": "M"},
                 "2026-10-19": {"A": "M"}, "2026-10-26": {"A": "M"}}
    diag_ok = diagnostic.diagnostiquer(grille_ok, stats, personnel, MOIS)
    assert diag_ok["stabilite"][0]["conforme"] and not diag_ok["alertes"]

    # Clé numérique dans JOURS_SEMAINE ("0" = lundi) — même détection
    personnel_num = [{"nom": "A", "role": "infirmier", "habitudes": {"0": "M"}}]
    diag_num = diagnostic.diagnostiquer(grille, stats, personnel_num, MOIS)
    assert not diag_num["stabilite"][0]["conforme"]


# ── 6. Entrées vides ──────────────────────────────────────────────────────
def test_entrées_vides():
    diag = diagnostic.diagnostiquer(None, None, None, MOIS)
    assert diag["alertes"] == [] and diag["equite"] == [] and diag["soldes"] == {}
    assert diag["nuits_consecutives"] == [] and diag["memore_inter_mois"] == []
    assert not diag["memoire_disponible"]


# ── 7. Ordre des alertes (warnings avant infos) ──────────────────────────
def test_ordre_alertes():
    grille = {"2026-10-05": {"A": "N"}, "2026-10-12": {"A": "N"},
              "2026-10-19": {"A": "N"}, "2026-10-26": {"A": "N"}}
    stats = {"A": {"nuits": 4, "jours_weekend": 0, "solde_fin_de_mois_h": -1.0}}
    personnel = [{"nom": "A", "role": "infirmier", "habitudes": {"lun": "M"}}]
    diag = diagnostic.diagnostiquer(grille, stats, personnel, MOIS)
    niveaux = [a["niveau"] for a in diag["alertes"]]
    assert "warning" in niveaux and "info" in niveaux
    assert niveaux == sorted(niveaux, key=lambda n: n != "warning"), niveaux


# ── 8. _mois_precedent ────────────────────────────────────────────────────
def test_mois_precedent():
    assert diagnostic._mois_precedent("2026-10") == "2026-09"
    assert diagnostic._mois_precedent("2026-01") == "2025-12"
    assert diagnostic._mois_precedent("2026-03") == "2026-02"


if __name__ == "__main__":
    test_nuits_consecutives()
    test_equite()
    test_memoire_inter_mois()
    test_soldes()
    test_stabilite()
    test_entrées_vides()
    test_ordre_alertes()
    test_mois_precedent()
    print("TEST DIAGNOSTIC : OK (8 cas)")
