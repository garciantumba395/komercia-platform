"""Opérations de la console. TOUTES les requêtes passent par l'alias `ops` (jamais par `default`)."""

from django.db import transaction
from django.utils import timezone

from accounts.models import Tenant

from . import platform_audit as pa
from .flags import KNOWN_FLAGS
from .models import FeatureFlag, SupportAccessGrant

OPS = "ops"


class ConsoleError(Exception):
    """Erreur affichable à l'agent (message en français)."""


def clean_reason(raw: str) -> str:
    reason = (raw or "").strip()
    if len(reason) < 5:
        raise ConsoleError("Le motif est obligatoire (5 caractères minimum).")
    if len(reason) > 500:
        raise ConsoleError("Le motif est trop long (500 caractères maximum).")
    return reason


def active_grant(tenant_id, now=None):
    """Consentement de support valide (non révoqué, non expiré) le plus récent, ou None."""
    now = now or timezone.now()
    return (
        SupportAccessGrant.objects.using(OPS)
        .filter(tenant_id=tenant_id, revoked_at__isnull=True, expires_at__gt=now)
        .order_by("-created_at")
        .first()
    )


def set_tenant_active(request, tenant_id, *, active: bool, reason: str) -> None:
    """Suspend (`active=False`) ou réactive une entreprise, avec motif obligatoire et audit, atomiquement."""
    reason = clean_reason(reason)
    with transaction.atomic(using=OPS):
        # Mise à jour conditionnelle : évite les doubles écritures concurrentes et les audits parasites.
        changed = Tenant.objects.using(OPS).filter(pk=tenant_id, is_active=not active).update(is_active=active)
        if not changed:
            if not Tenant.objects.using(OPS).filter(pk=tenant_id).exists():
                raise ConsoleError("Entreprise introuvable.")
            raise ConsoleError("Cette entreprise est déjà active." if active else "Cette entreprise est déjà suspendue.")
        pa.log_platform_action(
            request,
            tenant_id,
            pa.ACTION_REACTIVATE if active else pa.ACTION_SUSPEND,
            before={"is_active": not active},
            after={"is_active": active},
            reason=reason,
        )


def set_flag(request, tenant_id, key: str, enabled: bool, reason: str) -> None:
    reason = clean_reason(reason)
    if key not in KNOWN_FLAGS:
        raise ConsoleError("Fonction inconnue.")
    with transaction.atomic(using=OPS):
        existing = FeatureFlag.objects.using(OPS).filter(tenant_id=tenant_id, key=key).first()
        before = {"key": key, "enabled": existing.enabled if existing else None}
        FeatureFlag.objects.using(OPS).update_or_create(
            tenant_id=tenant_id, key=key, defaults={"enabled": enabled}
        )
        pa.log_platform_action(
            request, tenant_id, pa.ACTION_FLAG, before=before, after={"key": key, "enabled": enabled},
            reason=reason,
        )
