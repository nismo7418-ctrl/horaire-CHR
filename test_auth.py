"""Tests de l'authentification (hachage, verrouillage) — sans session Streamlit.

Lance : python test_auth.py
"""
import sys

sys.stdout.reconfigure(encoding="utf-8")

import auth

# Hachage / vérification
stock = auth.hacher("mdp-test-123")
assert stock.startswith("pbkdf2_sha256$"), stock
assert auth.verifie("mdp-test-123", stock), "bon mdp doit passer"
assert not auth.verifie("mdp-autre", stock), "mauvais mdp doit échouer"
assert not auth.verifie("x", "valeur.invalide"), "stockage invalide → False"
# Deux hachages du même mdp → sel différent
assert auth.hacher("a") != auth.hacher("a"), "sel aléatoire attendu"

# Verrouillage brute-force : ECHEC_MAX échecs → verrouillé, autre IP non concernée
ip = "9.9.9.9"
for _ in range(auth.ECHEC_MAX):
    auth._enregistrer_echec(ip)
assert auth._verrouille(ip), "IP verrouillée après 5 échecs"
assert not auth._verrouille("8.8.8.8"), "autre IP non concernée"
auth._echecs.clear()

# Connecter sans compte configuré → refus propre (pas d'exception)
import app_secrets as cfg
for cle in ("APP_IDENTIFIANT", "APP_MDP_STOCKAGE"):
    import os
    os.environ.pop(cle, None)
ok, msg = auth.connecter("x", "y")
assert not ok and "configuré" in (msg or ""), msg
print("AUTH : OK")
