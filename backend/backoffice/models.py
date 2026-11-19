"""Tables propres au back-office.

Ces deux tables sont SANS Row-Level Security, volontairement :

- `SupportAccessGrant` est lue par la console (rôle `ops`, sans contexte de tenant) pour savoir si un
  consentement est valide, et écrite par l'API du commerçant. La RLS (`tenant_id = app.tenant_id`) empêcherait
  la lecture inter-entreprises par un chemin autre que BYPASSRLS ; ici l'isolation est assurée par le code :
  l'API filtre TOUJOURS sur `request.tenant` (voir `api.py`) et la console n'y accède qu'en lecture.
- `FeatureFlag` doit pouvoir être lue par n'importe quel module (`flags.is_enabled`) avec ou sans contexte de
  tenant, et est écrite uniquement par la console.

Le rôle applicatif y accède par les privilèges par défaut de `scripts/db-init.sql` (SELECT/INSERT/UPDATE/DELETE) ;
le rôle `ops` y a des droits limités (voir `ops_role.py`). Aucune de ces tables ne contient de secret.
"""

import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


class SupportAccessGrant(models.Model):
    """Consentement du propriétaire à un accès de SUPPORT en lecture seule, limité dans le temps."""

    SCOPE_READ = "read"
    SCOPES = [(SCOPE_READ, "Lecture seule")]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey("accounts.Tenant", on_delete=models.CASCADE, related_name="+")
    granted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    scope = models.CharField(max_length=10, choices=SCOPES, default=SCOPE_READ)
    revoked_at = models.DateTimeField(null=True, blank=True)
    reason = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["tenant", "-expires_at"], name="supportgrant_tenant_idx")]

    def is_valid(self, now=None) -> bool:
        return self.revoked_at is None and self.expires_at > (now or timezone.now())


class FeatureFlag(models.Model):
    """Fonction ou module activé (ou non) pour une entreprise (KOM-084)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey("accounts.Tenant", on_delete=models.CASCADE, related_name="+")
    key = models.SlugField(max_length=64)
    enabled = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["tenant", "key"], name="uniq_featureflag_tenant_key")]
