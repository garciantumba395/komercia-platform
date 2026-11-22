"""Contexte de tenant et authentification.

Principe (cahier des charges, section 7) : le `tenant_id` vient du token d'authentification,
jamais d'un paramètre client. Il est transmis à PostgreSQL par `set_config('app.tenant_id', ...)`
local à la transaction ; les politiques Row-Level Security filtrent ensuite chaque requête.
"""

import contextvars
from contextlib import contextmanager

from django.db import connection, transaction
from rest_framework.authentication import TokenAuthentication
from rest_framework.exceptions import AuthenticationFailed

_current_tenant_id: contextvars.ContextVar = contextvars.ContextVar("tenant_id", default=None)


def get_current_tenant_id():
    return _current_tenant_id.get()


def activate_tenant(tenant_id) -> None:
    """Active le tenant pour la transaction en cours (doit être appelé dans une transaction)."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT set_config('app.tenant_id', %s, true)", [str(tenant_id or "")])
    _current_tenant_id.set(tenant_id)


def reset_tenant() -> None:
    _current_tenant_id.set(None)


@contextmanager
def tenant_context(tenant_id):
    """Exécute un bloc dans le contexte d'un tenant (scripts, tests, tâches asynchrones)."""
    previous = _current_tenant_id.get()
    with transaction.atomic():
        activate_tenant(tenant_id)
        try:
            yield
        finally:
            activate_tenant(previous)


class TenantTokenAuthentication(TokenAuthentication):
    """Authentification par token ; active le tenant de l'utilisateur pour la requête."""

    def authenticate(self, request):
        result = super().authenticate(request)
        if result is None:
            return None
        user, token = result
        membership = (
            user.memberships.filter(is_active=True, tenant__is_active=True)
            .select_related("tenant")
            .order_by("created_at")
            .first()
        )
        if membership is None:
            raise AuthenticationFailed("Aucune entreprise active pour cet utilisateur.")
        request.membership = membership
        request.tenant = membership.tenant
        activate_tenant(membership.tenant_id)
        return user, token
