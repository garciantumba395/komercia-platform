"""Journal des opérations de synchronisation (cahier des charges 18.5, KOM-NF-016).

Chaque opération reçue d'un appareil laisse une ligne en AJOUT SEUL (jamais modifiée ni supprimée) :
l'opération appliquée (`status="applied"`) ou chaque tentative rejetée (`status="rejected"`).
L'idempotence repose sur l'unicité partielle (tenant, op_id) des opérations appliquées : une opération
rejetée est réévaluée à chaque envoi (elle peut devenir applicable, par exemple après réactivation
d'un produit), et chaque tentative est journalisée avec son contenu, de sorte qu'aucune vente reçue
n'est perdue même si elle est refusée.
"""

from django.db import models
from django.db.models import Q
from django.utils import timezone

from core.models import TenantOwnedModel


class AppendOnlyError(RuntimeError):
    """Tentative de modifier ou de supprimer une ligne du journal de synchronisation."""


class SyncOperationQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise AppendOnlyError("Le journal de synchronisation est en ajout seul.")

    def delete(self):
        raise AppendOnlyError("Le journal de synchronisation est en ajout seul.")


class SyncOperation(TenantOwnedModel):
    APPLIED, REJECTED = "applied", "rejected"
    STATUSES = [(APPLIED, "Appliquée"), (REJECTED, "Rejetée")]

    op_id = models.UUIDField()  # généré par l'appareil : clé d'idempotence
    device_id = models.UUIDField()
    type = models.CharField(max_length=30)
    status = models.CharField(max_length=10, choices=STATUSES)
    entity_id = models.UUIDField(null=True, blank=True)  # entité créée ou modifiée (vente, client...)
    # Date de dernière modification de l'entité après l'opération (sert à détecter une modification
    # faite ailleurs depuis : règle « dernière modification gagne » des fiches client).
    entity_version = models.DateTimeField(null=True, blank=True)
    payload = models.JSONField(default=dict)
    result = models.JSONField(default=dict)
    # Heure de l'appareil : conservée, jamais utilisée comme heure métier.
    device_time = models.DateTimeField()
    received_at = models.DateTimeField(default=timezone.now)  # heure du serveur

    objects = SyncOperationQuerySet.as_manager()

    class Meta:
        ordering = ["received_at", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "op_id"],
                condition=Q(status="applied"),
                name="uniq_syncop_applied_op_id",
            ),
        ]
        indexes = [
            models.Index(fields=["tenant", "op_id"], name="syncop_op_idx"),
            models.Index(fields=["tenant", "device_id", "received_at"], name="syncop_device_idx"),
            models.Index(fields=["tenant", "entity_id"], name="syncop_entity_idx"),
        ]

    def __str__(self):
        return f"{self.type} {self.op_id} ({self.status})"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise AppendOnlyError("Le journal de synchronisation est en ajout seul.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise AppendOnlyError("Le journal de synchronisation est en ajout seul.")
