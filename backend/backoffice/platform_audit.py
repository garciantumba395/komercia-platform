"""Journalisation des actions de la console dans l'audit DU TENANT CONCERNÉ.

L'écriture passe par la connexion `ops` (BYPASSRLS, INSERT seul sur `audit_auditentry`) : l'agent n'a pas de
tenant actif. Le trigger d'ajout seul de la table interdit toute modification ultérieure.
"""

from audit.models import AuditEntry
from audit.services import _client_ip, _jsonable

OPS = "ops"

ACTION_SUSPEND = "platform.tenant_suspend"
ACTION_REACTIVATE = "platform.tenant_reactivate"
ACTION_SUPPORT_VIEW = "platform.support_view"
ACTION_FLAG = "platform.flag_set"


def log_platform_action(request, tenant_id, action, *, before=None, after=None, reason=""):
    ip = _client_ip(request)
    try:
        from django.core.validators import validate_ipv46_address

        validate_ipv46_address(ip)
    except Exception:
        ip = None
    entry = AuditEntry(
        tenant_id=tenant_id,
        user=request.user,
        action=action,
        entity_type="tenant",
        entity_id=str(tenant_id),
        before=None if before is None else _jsonable(before),
        after=None if after is None else _jsonable(after),
        reason=reason or "",
        ip_address=ip,
    )
    entry.save(using=OPS)
    return entry
