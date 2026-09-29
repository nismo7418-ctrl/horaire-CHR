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

# Fuite de timing : verifie() doit S'EXÉCUTER même quand l'identifiant est faux
# (le `and` court-circuité d'avant laissait l'attaquant distinguer « identifiant
# bon/mauvais » au temps de réponse — PBKDF2 ~dizaines de ms).
import os
os.environ["APP_IDENTIFIANT"] = "alice"
os.environ["APP_MDP_STOCKAGE"] = auth.hacher("secret123")
appels = {"n": 0}
_orig_verifie = auth.verifie
def _espion(mdp, stock):
    appels["n"] += 1
    return _orig_verifie(mdp, stock)
auth.verifie = _espion
ok, _ = auth.connecter("alice", "mdp-incorrect")
assert not ok, "bon identifiant + mauvais mdp → refus"
ok, _ = auth.connecter("mallory", "n'importe-quoi")
assert not ok, "mauvais identifiant + mdp quelconque → refus"
assert appels["n"] == 2, f"verifie() doit tourner sur les 2 tentatives (vu {appels['n']})"
auth.verifie = _orig_verifie
auth._echecs.clear()
del os.environ["APP_IDENTIFIANT"], os.environ["APP_MDP_STOCKAGE"]

# _ip_client : DERNIER maillon de x-forwarded-for (proxy de confiance), pas le
# premier (contrôlé par le client → contournement du verrouillage par IP fictive).
import sys, types
class _H:
    def __init__(self, d): self.d = d
    def get(self, k, dflt=None): return self.d.get(k, dflt)
_fake = types.ModuleType("streamlit")
_mem = sys.modules.get("streamlit")
sys.modules["streamlit"] = _fake
_fake.context = types.SimpleNamespace(headers=_H({"x-forwarded-for": "1.2.3.4, 5.6.7.8"}))
assert auth._ip_client() == "5.6.7.8", "dernier maillon XFF attendu"
_fake.context = types.SimpleNamespace(headers=_H({"x-real-ip": "9.8.7.6"}))
assert auth._ip_client() == "9.8.7.6", "repli x-real-ip sans XFF"
_fake.context = types.SimpleNamespace(headers=_H({}))
assert auth._ip_client() == "local"
if _mem is not None:
    sys.modules["streamlit"] = _mem
else:
    del sys.modules["streamlit"]

# _purger : une IP dont les échecs sont tous vieillis est supprimée (fuite mémoire)
import time as _t
auth._echecs.clear()
auth._echecs["old.ip"] = [_t.monotonic() - (auth.FENETRE_ECHECS_S + auth.DEBLOCAGE_S + 10)]
auth._purger()
assert "old.ip" not in auth._echecs, "IP vieillis purgée"

# Connecter sans compte configuré → refus propre (pas d'exception)
import app_secrets as cfg
for cle in ("APP_IDENTIFIANT", "APP_MDP_STOCKAGE"):
    import os
    os.environ.pop(cle, None)
ok, msg = auth.connecter("x", "y")
assert not ok and "configuré" in (msg or ""), msg
print("AUTH : OK")
