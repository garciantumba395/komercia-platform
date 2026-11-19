"""Abonnement de la plateforme (cahier des charges, section 16, KOM-070 à KOM-079).

- `Plan` et `PaymentReference` sont des tables globales, SANS tenant et SANS RLS.
- `Subscription`, `SubscriptionPayment` et `BillingNotice` sont isolées par tenant (RLS).
"""

import uuid

from django.db import models

from core.models import TenantOwnedModel


def new_reference() -> str:
    return "KOM-" + uuid.uuid4().hex[:20].upper()


class Plan(models.Model):
    """Plan d'abonnement. Modifiable uniquement via l'admin Django. Montants en centimes USD."""

    code = models.SlugField(max_length=30, unique=True)
    name = models.CharField(max_length=80)
    price_usd_minor = models.PositiveIntegerField()
    extra_store_price_usd_minor = models.PositiveIntegerField(default=0)
    max_stores = models.PositiveIntegerField()
    max_users = models.PositiveIntegerField()
    features = models.JSONField(default=dict, blank=True)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "price_usd_minor"]

    def __str__(self):
        return self.name


class Subscription(TenantOwnedModel):
    """Abonnement d'un tenant (un seul par tenant)."""

    TRIAL, ACTIVE, PAST_DUE, READ_ONLY = "trial", "active", "past_due", "read_only"
    STATES = [
        (TRIAL, "Essai"),
        (ACTIVE, "Actif"),
        (PAST_DUE, "En retard"),
        (READ_ONLY, "Lecture seule"),
    ]

    plan = models.ForeignKey(Plan, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    pending_plan = models.ForeignKey(
        Plan, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    state = models.CharField(max_length=12, choices=STATES, default=TRIAL)
    trial_ends_at = models.DateTimeField(null=True, blank=True)
    current_period_start = models.DateTimeField(null=True, blank=True)
    current_period_end = models.DateTimeField(null=True, blank=True)
    grace_ends_at = models.DateTimeField(null=True, blank=True)
    canceled_at = models.DateTimeField(null=True, blank=True)
    cancel_at_period_end = models.BooleanField(default=False)
    read_only_since = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["tenant"], name="uniq_subscription_tenant")]

    def __str__(self):
        return f"{self.tenant_id} [{self.state}]"


class AppendOnlyError(RuntimeError):
    pass


class _PaymentQuerySet(models.QuerySet):
    def delete(self):
        raise AppendOnlyError("Un paiement d'abonnement ne peut pas être supprimé.")


class SubscriptionPayment(TenantOwnedModel):
    """Paiement d'abonnement. Ajout seul : seules les transitions de statut sont permises."""

    PENDING, CONFIRMED, FAILED, EXPIRED = "pending", "confirmed", "failed", "expired"
    STATUSES = [(PENDING, "En attente"), (CONFIRMED, "Confirmé"), (FAILED, "Échoué"),
                (EXPIRED, "Expiré")]
    KIND_SUBSCRIBE, KIND_UPGRADE = "subscribe", "upgrade"
    KINDS = [(KIND_SUBSCRIBE, "Souscription ou renouvellement"), (KIND_UPGRADE, "Complément de plan")]
    CURRENCIES = [("USD", "USD"), ("CDF", "CDF")]

    # Seuls ces champs peuvent changer après la création (transition de statut).
    MUTABLE_FIELDS = frozenset({"status", "confirmed_at", "period_start", "period_end", "updated_at"})

    subscription = models.ForeignKey(Subscription, on_delete=models.PROTECT, related_name="payments")
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name="+")
    kind = models.CharField(max_length=10, choices=KINDS, default=KIND_SUBSCRIBE)
    amount_minor = models.BigIntegerField()
    currency = models.CharField(max_length=3, choices=CURRENCIES)
    amount_usd_minor = models.BigIntegerField()
    rate_cdf_per_usd = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    provider = models.CharField(max_length=30)
    reference = models.CharField(max_length=64, unique=True, default=new_reference)
    status = models.CharField(max_length=10, choices=STATUSES, default=PENDING)
    period_start = models.DateTimeField(null=True, blank=True)
    period_end = models.DateTimeField(null=True, blank=True)
    phone = models.CharField(max_length=20, blank=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)

    objects = _PaymentQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at", "-id"]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            fields = kwargs.get("update_fields")
            if fields is None or not set(fields) <= self.MUTABLE_FIELDS:
                raise AppendOnlyError("Un paiement d'abonnement ne peut qu'avoir une transition de statut.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise AppendOnlyError("Un paiement d'abonnement ne peut pas être supprimé.")

    def __str__(self):
        return f"{self.reference} {self.amount_minor} {self.currency} ({self.status})"


class PaymentReference(models.Model):
    """Index global SANS RLS : retrouve le tenant d'un paiement depuis un webhook (sans contexte).

    Ne contient que la référence (opaque) et l'identifiant du tenant ; jamais exposé par l'API.
    """

    reference = models.CharField(max_length=64, unique=True)
    tenant_id = models.UUIDField()

    def __str__(self):
        return self.reference


class BillingNotice(TenantOwnedModel):
    """Rappel d'abonnement. Un seul par (abonnement, type, échéance)."""

    KINDS = [(k, k) for k in ("trial_7", "trial_3", "trial_1", "trial_0", "due_7", "due_3", "due_0",
                              "past_due", "read_only")]

    subscription = models.ForeignKey(Subscription, on_delete=models.CASCADE, related_name="notices")
    kind = models.CharField(max_length=10, choices=KINDS)
    due_at = models.DateTimeField()
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["subscription", "kind", "due_at"], name="uniq_notice")
        ]
