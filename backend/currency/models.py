from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

from core.models import TenantOwnedModel


class AppendOnlyError(RuntimeError):
    """Tentative de modifier ou supprimer un taux de change (historique immuable, KOM-038)."""


class ExchangeRateQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise AppendOnlyError("Un taux de change ne peut pas être modifié.")

    def delete(self):
        raise AppendOnlyError("Un taux de change ne peut pas être supprimé.")


class ExchangeRate(TenantOwnedModel):
    """Taux CDF pour 1 USD. Table en ajout seul (trigger PostgreSQL + garde applicative).

    `store` nul : taux par défaut du tenant ; sinon taux propre à la boutique.
    """

    store = models.ForeignKey(
        "accounts.Store", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    rate_cdf_per_usd = models.DecimalField(max_digits=14, decimal_places=4)
    effective_at = models.DateTimeField(default=timezone.now)
    set_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT, related_name="+"
    )
    note = models.CharField(max_length=255, blank=True)

    objects = ExchangeRateQuerySet.as_manager()

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(rate_cdf_per_usd__gt=Decimal(0)), name="exchangerate_rate_positive"
            )
        ]
        indexes = [models.Index(fields=["tenant", "store", "-effective_at"], name="currency_rate_lookup")]
        ordering = ["-effective_at", "-created_at"]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise AppendOnlyError("Un taux de change ne peut pas être modifié.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise AppendOnlyError("Un taux de change ne peut pas être supprimé.")

    def __str__(self):
        return f"1 USD = {self.rate_cdf_per_usd} CDF ({self.effective_at:%Y-%m-%d %H:%M})"
