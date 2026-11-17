"""Clients et grand livre de crédit (cahier des charges 4.6 et 18.3)."""

from django.conf import settings
from django.db import models

from core.models import TenantOwnedModel

CURRENCIES = [("CDF", "Franc congolais"), ("USD", "Dollar américain")]
KIND_CHARGE = "charge"
KIND_PAYMENT = "payment"
KINDS = [(KIND_CHARGE, "Vente à crédit"), (KIND_PAYMENT, "Paiement")]
PAYMENT_METHODS = [("cash", "Espèces"), ("mobile_money", "Mobile Money"), ("other", "Autre")]


class Customer(TenantOwnedModel):
    name = models.CharField(max_length=150)
    phone = models.CharField(max_length=20, blank=True)
    address = models.CharField(max_length=255, blank=True)
    notes = models.TextField(blank=True)
    credit_limit_minor = models.BigIntegerField(default=0)
    credit_limit_currency = models.CharField(max_length=3, choices=CURRENCIES, default="CDF")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(credit_limit_minor__gte=0), name="customer_limit_gte_0"
            )
        ]

    def __str__(self):
        return self.name


class AppendOnlyError(RuntimeError):
    pass


class CreditEntry(TenantOwnedModel):
    """Grand livre append-only : une ligne ne se modifie ni ne se supprime jamais."""

    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="credit_entries")
    kind = models.CharField(max_length=10, choices=KINDS)
    amount_minor = models.BigIntegerField()
    currency = models.CharField(max_length=3, choices=CURRENCIES)
    sale_id = models.UUIDField(null=True, blank=True, db_index=True)  # pas de FK : module sales
    due_date = models.DateField(null=True, blank=True)
    method = models.CharField(max_length=20, choices=PAYMENT_METHODS, blank=True)
    note = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        ordering = ["created_at", "id"]
        indexes = [models.Index(fields=["customer", "currency", "kind"])]
        constraints = [
            models.CheckConstraint(condition=models.Q(amount_minor__gt=0), name="creditentry_amount_gt_0"),
            models.CheckConstraint(
                condition=models.Q(kind__in=["charge", "payment"]), name="creditentry_kind_valid"
            ),
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise AppendOnlyError("Le grand livre de crédit est en ajout seul.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise AppendOnlyError("Le grand livre de crédit est en ajout seul.")


class PaymentAllocation(TenantOwnedModel):
    """Imputation (FIFO) d'un paiement sur une charge."""

    payment_entry = models.ForeignKey(
        CreditEntry, on_delete=models.PROTECT, related_name="allocations_made"
    )
    charge_entry = models.ForeignKey(
        CreditEntry, on_delete=models.PROTECT, related_name="allocations_received"
    )
    amount_minor = models.BigIntegerField()

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(amount_minor__gt=0), name="allocation_amount_gt_0")
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise AppendOnlyError("Le grand livre de crédit est en ajout seul.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise AppendOnlyError("Le grand livre de crédit est en ajout seul.")
