"""Authentification de l'app (déploiement cloud) — session en mémoire uniquement, rien n'est écrit sur disque.

- Mot de passe : PBKDF2-SHA256 (390 000 itérations) + sel aléatoire 16 octets,
  comparaison en temps constant (hmac.compare_digest) — pas d'attaque par timing.
  Générer la valeur de stockage : `python -c "import auth; print(auth.hacher('VOTRE_MDP'))"`
- Brute-force : 5 échecs consécutifs (par IP client) → verrouillage 5 minutes.
  Le compteur est en mémoire de process : il n'est pas partagé entre workers —
  à garder en tête si l'app passe en multi-instances (alors filtrer au proxy).
- Session : `st.session_state` uniquement (rien de persistant), expiration
  automatique après 30 min d'inactivité (env `APP_SESSION_TIMEOUT_S`).
- Déconnexion = effacement de toutes les données de session (droit à l'effacement).
"""
from __future__ import annotations

import hashlib
import hmac
import os
import time

import app_secrets as cfg

ITERS = 390_000
ECHEC_MAX = 5
FENETRE_ECHECS_S = 120      # fenêtre glissante pour compter les échecs
DEBLOCAGE_S = 300           # durée du verrouillage après ECHEC_MAX échecs
SESSION_TIMEOUT_S = int(os.environ.get("APP_SESSION_TIMEOUT_S", "1800"))

_echecs: dict[str, list[float]] = {}


def hacher(mdp: str) -> str:
    """Produit la chaîne de stockage `pbkdf2_sha256$<iters>$<sel>$<digest>`."""
    sel = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", mdp.encode("utf-8"), sel, ITERS).hex()
    return f"pbkdf2_sha256${ITERS}${sel.hex()}${digest}"


def verifie(mdp: str, stockage: str) -> bool:
    """Vérifie un mot de passe contre une chaîne de stockage (temps constant)."""
    try:
        algo, iters, sel_hex, digest = stockage.split("$", 3)
        if algo != "pbkdf2_sha256":
            return False
        calcul = hashlib.pbkdf2_hmac(
            "sha256", mdp.encode("utf-8"), bytes.fromhex(sel_hex), int(iters)).hex()
    except (ValueError, AttributeError):
        return False
    return hmac.compare_digest(calcul, digest)


def _ip_client() -> str:
    """IP client pour le compteur de verrouillage.

    `x-forwarded-for` : on prend la DERNIÈRE valeur — c'est celle ajoutée par le
    reverse proxy de confiance en bout de chaîne. Le PREMIER maillon est contrôlé
    par le client lui-même : un attaquant qui le changeait à chaque tentative
    partait à zéro d'échecs, contournant le verrouillage.
    Hypothèse : un seul proxy de confiance qui AJOUTE l'IP réelle (nginx real_ip,
    Caddy, …). Si l'hébergeur expose plutôt `x-real-ip` (pas de chaîne XFF),
    on le prend en repli. Si aucun hop de confiance n'est garanti, ce
    rate-limit par IP n'est fiable ni sur la première ni sur la dernière valeur
    — c'est alors le proxy/hébergeur qui doit filtrer.
    """
    try:
        import streamlit as st
        h = st.context.headers
        xff = h.get("x-forwarded-for")
        if xff:
            ip = xff.split(",")[-1].strip()
            if ip:
                return ip
        ip = (h.get("x-real-ip") or "").strip()
        return ip or "local"
    except Exception:
        return "local"


def _purger() -> None:
    """Supprime les IP dont les échecs sont tous vieillis (fuite mémoire lente sur longue durée)."""
    now = time.monotonic()
    limite = FENETRE_ECHECS_S + DEBLOCAGE_S
    for ip in [ip for ip, ts in _echecs.items() if not ts or now - max(ts) > limite]:
        del _echecs[ip]


def _enregistrer_echec(ip: str) -> None:
    now = time.monotonic()
    _purger()
    recent = [t for t in _echecs.get(ip, []) if now - t < FENETRE_ECHECS_S]
    recent.append(now)
    _echecs[ip] = recent


def _verrouille(ip: str) -> bool:
    now = time.monotonic()
    recent = [t for t in _echecs.get(ip, []) if now - t < FENETRE_ECHECS_S]
    _echecs[ip] = recent
    if len(recent) >= ECHEC_MAX:
        return now - max(recent) < DEBLOCAGE_S
    return False


def connecter(user: str, mdp: str) -> tuple[bool, str | None]:
    """Tente une connexion. Retourne (ok, message_d_erreur)."""
    ip = _ip_client()
    ident = cfg.secret("APP_IDENTIFIANT")
    stock = cfg.secret("APP_MDP_STOCKAGE")
    if not ident or not stock:
        return False, ("Aucun compte configuré. Définir `APP_IDENTIFIANT` et `APP_MDP_STOCKAGE` "
                       "(voir `.streamlit/secrets.toml.example`).")
    if _verrouille(ip):
        return False, f"Trop de tentatives échouées — réessayez dans {DEBLOCAGE_S // 60} minutes."
    # Les deux vérifications sont TOUJOURS calculées, dans l'ordre, avant de
    # combiner : un `and` court-circuité ne lançait pas le PBKDF2 (~dizaines de ms)
    # quand l'identifiant était mauvais — le temps de réponse distinguait alors
    # « identifiant faux » de « identifiant bon, mot de passe faux ». Les comparer
    # en octets évite aussi la TypeError de compare_digest sur un identifiant non-ASCII.
    user_ok = hmac.compare_digest(user.encode("utf-8"), ident.encode("utf-8"))
    mdp_ok = verifie(mdp, stock)
    if not (user_ok and mdp_ok):
        _enregistrer_echec(ip)
        return False, "Identifiant ou mot de passe incorrect."
    _echecs.pop(ip, None)
    return True, None


def ouvrir_session(user: str) -> None:
    import streamlit as st
    st.session_state.auth = {"user": user, "ts": time.monotonic()}


def veillee() -> bool:
    """True si une session authentifiée est active (et non expirée)."""
    import streamlit as st
    s = st.session_state.get("auth")
    if not s:
        return False
    if time.monotonic() - s["ts"] > SESSION_TIMEOUT_S:
        st.session_state.auth = None
        return False
    return True


def deconnecter() -> None:
    """Déconnecte et efface toutes les données de session (droit à l'effacement)."""
    import streamlit as st
    st.session_state.auth = None
    st.session_state.desiderata = {}
    st.session_state.planning = None
    st.session_state.explication = None
