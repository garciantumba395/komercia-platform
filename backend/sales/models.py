"""Ventes, paiements et sessions de caisse (cahier des charges 4.3, 4.4, 18.1, 18.2).

Tous les montants sont des entiers dans la plus petite unité de la devise (CDF : franc ;
USD : centime), accompagnés d'un code de devise. Une vente est immuable après création : seul le
statut d'annulation (et ses métadonnées) peut changer. Les lignes et les paiements ne changent jamais.
"""

from django.conf import settings
from django.db import models
from django.db.models import Q

from core.models import TenantOwnedModel

CURRENCIES = [("CDF", "Franc congolais"), ("USD", "Dollar américain")]

# Champs seuls modifiables sur une vente existante (annulation).
_CANCEL_FIELDS = {"status", "cancelled_at", "cancelled_by", "cancel_reason", "updated_at"}


class StoreCounter(TenantOwnedModel):
    """Compteur de numérotation d'une boutique (une ligne par boutique, verrouillée à chaque vente).

    `prefix` est dérivé de la boutique : « B01 », « B02 »... selon l'ordre de première vente dans
    l'entreprise (unique par entreprise). Le numéro de vente est `<prefix>-<last_number sur 6 chiffres>`.
    Incrémenté dans la transaction de la vente : un échec annule aussi l'incrément (aucun trou).
    """

    store = models.OneToOneField("accounts.Store", on_delete=models.PROTECT, related_name="sale_counter")
    prefix = models.CharField(max_length=10)
    last_number = models.PositiveBigIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["tenant", "prefix"], name="uniq_storecounter_prefix"),
        ]


class CashSession(TenantOwnedModel):
    """Session de caisse d'un caissier dans une boutique (fonds initial, clôture, écart par devise)."""

    OPEN, CLOSED = "open", "closed"
    STATUSES = [(OPEN, "Ouverte"), (CLOSED, "Clôturée")]

    store = models.ForeignKey("accounts.Store", on_delete=models.PROTECT, related_name="cash_sessions")
    cashier = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    status = models.CharField(max_length=10, choices=STATUSES, default=OPEN)
    opened_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    opening_float = models.JSONField(default=dict)  # {"CDF": int, "USD": int}
    expected = models.JSONField(null=True, blank=True)
    counted = models.JSONField(null=True, blank=True)
    difference = models.JSONField(null=True, blank=True)

    class Meta:
        ordering = ["-opened_at", "-id"]
        constraints = [
            # Une seule session ouverte par caissier et par boutique (protège aussi la concurrence).
            models.UniqueConstraint(
                fields=["tenant", "store", "cashier"],
                condition=Q(status="open"),
                name="uniq_open_cashsession_per_cashier_store",
            ),
        ]
        indexes = [models.Index(fields=["tenant", "store", "-opened_at"], name="cashsess_store_idx")]


class Sale(TenantOwnedModel):
    COMPLETED, CANCELLED = "completed", "cancelled"
    STATUSES = [(COMPLETED, "Terminée"), (CANCELLED, "Annulée")]

    client_id = models.UUIDField()  # généré par l'appareil : clé d'idempotence
    number = models.CharField(max_length=30)
    request_hash = models.CharField(max_length=64)  # empreinte du contenu, pour détecter un rejeu différent
    status = models.CharField(max_length=10, choices=STATUSES, default=COMPLETED)
    store = models.ForeignKey("accounts.Store", on_delete=models.PROTECT, related_name="sales")
    customer = models.ForeignKey(
        "customers.Customer", null=True, blank=True, on_delete=models.PROTECT, related_name="sales"
    )
    cashier = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    cash_session = models.ForeignKey(
        CashSession, null=True, blank=True, on_delete=models.PROTECT, related_name="sales"
    )
    currency = models.CharField(max_length=3, choices=CURRENCIES)
    subtotal_minor = models.BigIntegerField()
    discount_minor = models.BigIntegerField(default=0)  # remise globale
    tax_minor = models.BigIntegerField(default=0)
    total_minor = models.BigIntegerField()
    # Taux figé (null si aucune conversion n'a été nécessaire et qu'aucun taux n'existe).
    rate_cdf_per_usd = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    rate_id = models.UUIDField(null=True, blank=True)
    paid_minor = models.BigIntegerField(default=0)  # total payé, en devise de la vente
    change_minor = models.BigIntegerField(default=0)
    change_currency = models.CharField(max_length=3, choices=CURRENCIES)
    credit_minor = models.BigIntegerField(default=0)  # part mise à crédit, en devise de la vente
    device_time = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    cancel_reason = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["tenant", "client_id"], name="uniq_sale_client_id"),
            models.UniqueConstraint(fields=["tenant", "number"], name="uniq_sale_number"),
            models.CheckConstraint(condition=Q(total_minor__gte=0), name="sale_total_gte_0"),
            models.CheckConstraint(condition=Q(credit_minor__gte=0), name="sale_credit_gte_0"),
        ]
        indexes = [
            models.Index(fields=["tenant", "store", "-created_at"], name="sale_store_date_idx"),
            models.Index(fields=["tenant", "cashier", "-created_at"], name="sale_cashier_date_idx"),
        ]

    def __str__(self):
        return self.number

    def save(self, *args, **kwargs):
        if not self._state.adding:
            fields = kwargs.get("update_fields")
            if fields is None or not set(fields) <= _CANCEL_FIELDS:
                raise RuntimeError("Une vente est immuable : seule l'annulation est permise.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise RuntimeError("Une vente ne peut pas être supprimée : l'annuler.")


class _ImmutableMixin:
    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise RuntimeError("Cet enregistrement de vente est immuable.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise RuntimeError("Cet enregistrement de vente ne peut pas être supprimé.")


class SaleItem(_ImmutableMixin, TenantOwnedModel):
    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey("catalog.Product", on_delete=models.PROTECT, related_name="+")
    name = models.CharField(max_length=200)  # nom du produit au moment de la vente
    quantity = models.DecimalField(max_digits=12, decimal_places=3)
    unit_price_minor = models.BigIntegerField()  # en devise de la vente
    discount_minor = models.BigIntegerField(default=0)
    line_total_minor = models.BigIntegerField()
    position = models.PositiveSmallIntegerField(default=0)  # ordre des lignes dans la vente

    class Meta:
        ordering = ["position"]
        indexes = [models.Index(fields=["tenant", "product"], name="saleitem_product_idx")]


class Payment(_ImmutableMixin, TenantOwnedModel):
    METHOD_CASH = "cash"

    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name="payments")
    method = models.CharField(max_length=20, default=METHOD_CASH)
    amount_minor = models.BigIntegerField()
    currency = models.CharField(max_length=3, choices=CURRENCIES)
    rate_cdf_per_usd = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    rate_id = models.UUIDField(null=True, blank=True)
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["position"]
        constraints = [models.CheckConstraint(condition=Q(amount_minor__gt=0), name="payment_amount_gt_0")]
