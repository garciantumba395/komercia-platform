from django.conf import settings
from django.db import models

from core.models import TenantOwnedModel


class StockLevel(TenantOwnedModel):
    product = models.ForeignKey("catalog.Product", on_delete=models.PROTECT, related_name="stock_levels")
    store = models.ForeignKey("accounts.Store", on_delete=models.PROTECT, related_name="stock_levels")
    quantity = models.DecimalField(max_digits=12, decimal_places=3, default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["product", "store"], name="uniq_stocklevel_product_store"),
        ]

    def __str__(self):
        return f"{self.product_id} @ {self.store_id} = {self.quantity}"


class StockMovement(TenantOwnedModel):
    """Mouvement de stock. Append-only : jamais modifié ni supprimé (corriger par un ajustement)."""

    IN, OUT, ADJUSTMENT, SALE, RETURN = "in", "out", "adjustment", "sale", "return"
    KINDS = [
        (IN, "Entrée"),
        (OUT, "Sortie"),
        (ADJUSTMENT, "Ajustement"),
        (SALE, "Vente"),
        (RETURN, "Retour"),
    ]

    product = models.ForeignKey("catalog.Product", on_delete=models.PROTECT, related_name="stock_movements")
    store = models.ForeignKey("accounts.Store", on_delete=models.PROTECT, related_name="stock_movements")
    kind = models.CharField(max_length=12, choices=KINDS)
    quantity = models.DecimalField(max_digits=12, decimal_places=3)  # signée
    unit_cost_minor = models.PositiveBigIntegerField(null=True, blank=True)
    reason = models.TextField(blank=True, default="")
    reference = models.CharField(max_length=100, blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["product", "store", "-created_at"], name="stockmv_prod_store_idx"),
            models.Index(fields=["reference"], name="stockmv_reference_idx"),
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise RuntimeError("Un mouvement de stock est immuable.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise RuntimeError("Un mouvement de stock ne peut pas être supprimé.")
