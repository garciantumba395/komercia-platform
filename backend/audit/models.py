from django.conf import settings
from django.db import models

from core.models import TenantOwnedModel


class AuditEntry(TenantOwnedModel):
    """Entrée du journal d'audit. AJOUT SEUL : modification et suppression sont refusées
    par un trigger PostgreSQL (voir migration 0001) et par l'ORM.

    L'horodatage serveur est `created_at` ; `device_time` est celui de l'appareil (hors ligne).
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,  # SET_NULL impliquerait un UPDATE, interdit par le trigger
        related_name="+",
    )
    action = models.CharField(max_length=64, db_index=True)
    entity_type = models.CharField(max_length=64)
    entity_id = models.CharField(max_length=64)
    before = models.JSONField(null=True, blank=True)
    after = models.JSONField(null=True, blank=True)
    reason = models.TextField(blank=True)
    device_time = models.DateTimeField(null=True, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["tenant", "entity_type", "entity_id"]),
            models.Index(fields=["tenant", "-created_at"]),
        ]

    def __str__(self):
        return f"{self.action} {self.entity_type}:{self.entity_id}"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise RuntimeError("Le journal d'audit est en ajout seul : modification interdite.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise RuntimeError("Le journal d'audit est en ajout seul : suppression interdite.")
