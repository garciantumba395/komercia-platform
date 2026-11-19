"""Horloge d'abonnement qui ne recule jamais (protection contre le retour en arrière de l'horloge).

`effective_now() = max(horloge système, LicenseState.last_seen_at)`. La valeur `last_seen_at` est mise en
cache dans le processus (une seule lecture SQL au premier usage) et écrite en base au plus une fois par minute
par `touch()`, appelé à chaque requête authentifiée d'un commerçant (`SubscriptionWriteAllowed`) et par
`billing_tick`. Un saut en avant aberrant (plus de 400 jours d'un coup) n'est PAS enregistré : on journalise
et on laisse la base inchangée, pour qu'une horloge folle, une fois corrigée, ne bloque pas le commerçant.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from django.utils import timezone

logger = logging.getLogger("billing.clock")

WRITE_INTERVAL = timedelta(minutes=1)
MAX_FORWARD_JUMP = timedelta(days=400)
LOG_INTERVAL = timedelta(minutes=1)

_cache: dict = {"seen": None, "written": None, "logged": None}


def reset_cache() -> None:
    """Vide le cache du processus (tests, ou après une restauration de la base)."""
    _cache.update(seen=None, written=None, logged=None)


def _load_seen() -> datetime:
    if _cache["seen"] is None:
        from .licensing import get_license_state

        _cache["seen"] = get_license_state().last_seen_at
    return _cache["seen"]


def effective_now() -> datetime:
    """Maintenant, sans jamais reculer par rapport à la dernière date vue."""
    return max(timezone.now(), _load_seen())


def _warn(system: datetime, message: str, *args) -> None:
    last = _cache["logged"]
    if last is None or abs(system - last) >= LOG_INTERVAL:
        _cache["logged"] = system
        logger.warning(message, *args)


def touch() -> None:
    """Mémorise la date vue (au plus une écriture SQL par minute, aucune requête sinon)."""
    from django.db import transaction

    from .licensing import get_license_state
    from .models import LicenseState

    system = timezone.now()
    seen = _load_seen()
    if system <= seen:
        if seen - system > timedelta(minutes=5):
            _warn(system, "Horloge système en retard de %s sur la dernière date vue : le temps "
                          "d'abonnement n'est pas reculé.", seen - system)
        return
    if system - seen > MAX_FORWARD_JUMP:
        _warn(system, "Horloge système aberrante (+%s d'un coup) : date non enregistrée.", system - seen)
        return
    written = _cache["written"]
    if written is not None and timedelta(0) <= system - written < WRITE_INTERVAL:
        _cache["seen"] = max(seen, system)  # en mémoire seulement
        return
    with transaction.atomic():
        state = get_license_state()
        if system > state.last_seen_at:
            LicenseState.objects.filter(pk=state.pk).update(last_seen_at=system)
            state.last_seen_at = system
    _cache["seen"], _cache["written"] = max(state.last_seen_at, system), system
