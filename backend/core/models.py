import uuid

from django.db import models

from .tenancy import get_current_tenant_id


class TenantOwnedModel(models.Model):
    """Base de toutes les tables métier : `tenant_id` obligatoire + Row-Level Security.

    Chaque migration qui crée une sous-classe doit ajouter `core.rls.EnableTenantRLS("<table>")`.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        "accounts.Tenant", on_delete=models.CASCADE, editable=False, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if self.tenant_id is None:
            tenant_id = get_current_tenant_id()
            if tenant_id is None:
                raise RuntimeError("Aucun tenant actif : impossible d'enregistrer cet objet.")
            self.tenant_id = tenant_id
        super().save(*args, **kwargs)
