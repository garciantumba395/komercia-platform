"""Contrôle d'accès de la console /ops/.

Seul un utilisateur `is_staff` ET `is_active`, authentifié par SESSION Django (formulaire /ops/login/),
entre. Un jeton d'API de commerçant (en-tête Authorization) est ignoré : ces vues n'utilisent pas DRF.
Un commerçant, même propriétaire, a `is_staff=False` : refus (403) ; un anonyme est redirigé vers la connexion.
"""

# ruff: noqa: E501

import hashlib
from functools import wraps

from django.core.cache import cache
from django.http import HttpResponseForbidden
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache

LOGIN_URL = "/ops/login/"

# Limitation des tentatives de connexion : 5 échecs en 15 minutes, par numéro ET par adresse IP.
# Le compteur est dans le cache Django (par processus avec LocMemCache : configurer un cache partagé en production).
MAX_FAILURES = 5
LOCK_SECONDS = 15 * 60


def is_agent(user) -> bool:
    return bool(user and user.is_authenticated and user.is_active and user.is_staff)


def ops_required(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f"{LOGIN_URL}?next={request.path}")
        if not is_agent(request.user):
            return HttpResponseForbidden(
                render(request, "backoffice/forbidden.html", {"message": "Accès réservé au personnel Komersia."}).content
            )
        return view(request, *args, **kwargs)

    return never_cache(wrapper)


def platform_owner_required(view):
    """Réservé aux propriétaires de la plateforme (super-utilisateurs)."""

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_superuser:
            return HttpResponseForbidden(
                render(request, "backoffice/forbidden.html",
                       {"message": "Action réservée aux propriétaires de la plateforme."}).content
            )
        return view(request, *args, **kwargs)

    return ops_required(wrapper)


def safe_next(request, candidate):
    if candidate and candidate.startswith("/ops/") and url_has_allowed_host_and_scheme(
        candidate, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return candidate
    return "/ops/"


def _keys(request, phone):
    ip = request.META.get("REMOTE_ADDR", "")
    digest = hashlib.sha256((phone or "").strip().lower().encode()).hexdigest()[:32]
    return [f"ops-login:ip:{ip}", f"ops-login:phone:{digest}"]


def is_locked(request, phone) -> bool:
    return any((cache.get(key) or 0) >= MAX_FAILURES for key in _keys(request, phone))


def register_failure(request, phone) -> None:
    for key in _keys(request, phone):
        cache.add(key, 0, LOCK_SECONDS)
        try:
            cache.incr(key)
        except ValueError:
            cache.set(key, 1, LOCK_SECONDS)


def clear_failures(request, phone) -> None:
    cache.delete(_keys(request, phone)[1])
