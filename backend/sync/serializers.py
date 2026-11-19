"""Sérialiseurs de la synchronisation : enveloppe du `push`, format du `pull` et du `status`."""

from rest_framework import serializers

from accounts.models import Store
from catalog.models import Category, Product
from customers.models import CURRENCIES, Customer
from stock.models import StockLevel

OPERATION_TYPES = [
    "sale.create",
    "sale.cancel",
    "customer.create",
    "customer.update",
    "stock.movement",
    "cash_session.open",
    "cash_session.close",
]
MAX_OPERATIONS_PER_BATCH = 200


# --------------------------------------------------------------------------- push


class OperationSerializer(serializers.Serializer):
    op_id = serializers.UUIDField(
        help_text="Identifiant de l'opération, généré sur l'appareil (clé d'idempotence)."
    )
    type = serializers.CharField(
        max_length=30, help_text="Un des types : " + ", ".join(OPERATION_TYPES) + "."
    )
    device_time = serializers.DateTimeField(
        help_text="Heure de l'appareil à la création de l'opération (ISO 8601). Conservée, jamais utilisée "
        "comme heure de la vente."
    )
    payload = serializers.DictField(
        help_text="Contenu de l'opération (voir la documentation de chaque type)."
    )


class PushRequestSerializer(serializers.Serializer):
    device_id = serializers.UUIDField(help_text="Identifiant de l'appareil (UUID généré à l'installation).")
    operations = OperationSerializer(many=True, allow_empty=False)


class OperationErrorSerializer(serializers.Serializer):
    code = serializers.CharField()
    message = serializers.CharField()
    details = serializers.JSONField(required=False)


class OperationWarningSerializer(serializers.Serializer):
    code = serializers.CharField(help_text="negative_stock, clock_skew, sensitive_field_ignored...")
    message = serializers.CharField()
    details = serializers.JSONField(required=False)


class OperationResultSerializer(serializers.Serializer):
    op_id = serializers.UUIDField()
    status = serializers.ChoiceField(choices=["applied", "duplicate", "rejected"])
    entity_id = serializers.UUIDField(
        required=False, help_text="Identifiant serveur de l'entité (vente, client...)."
    )
    data = serializers.JSONField(required=False, help_text="Données utiles (numéro de vente...).")
    error = OperationErrorSerializer(required=False)
    warnings = OperationWarningSerializer(many=True, required=False)


class PushResponseSerializer(serializers.Serializer):
    results = OperationResultSerializer(many=True)
    server_time = serializers.DateTimeField()


class CustomerSyncSerializer(serializers.Serializer):
    """Corps de `customer.create` (id obligatoire) et `customer.update` (id + champs à changer).

    `credit_limit_minor` et `credit_limit_currency` sont acceptés pour compatibilité mais JAMAIS
    appliqués par la synchronisation (champs sensibles) : ils produisent un avertissement."""

    id = serializers.UUIDField()
    name = serializers.CharField(max_length=150)
    phone = serializers.CharField(max_length=20, required=False, allow_blank=True)
    address = serializers.CharField(max_length=255, required=False, allow_blank=True)
    notes = serializers.CharField(required=False, allow_blank=True)
    is_active = serializers.BooleanField(required=False)
    credit_limit_minor = serializers.IntegerField(min_value=0, required=False)
    credit_limit_currency = serializers.ChoiceField(choices=[c for c, _ in CURRENCIES], required=False)


# --------------------------------------------------------------------------- pull


class SyncStoreSerializer(serializers.ModelSerializer):
    class Meta:
        model = Store
        fields = ["id", "name", "address", "is_active", "updated_at"]


class SyncCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ["id", "name", "parent", "updated_at"]


class SyncProductSerializer(serializers.ModelSerializer):
    """`cost_minor` n'est présent que si `context["with_cost"]` est vrai (jamais pour un caissier)."""

    class Meta:
        model = Product
        fields = [
            "id", "name", "sku", "barcode", "category", "kind", "unit", "price_minor", "currency",
            "cost_minor", "track_stock", "min_stock", "is_active", "updated_at",
        ]

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if not self.context.get("with_cost"):
            data.pop("cost_minor", None)
        return data


class SyncCustomerSerializer(serializers.ModelSerializer):
    balance = serializers.SerializerMethodField(help_text="Dette ouverte par devise : {\"CDF\": 15000}.")

    class Meta:
        model = Customer
        fields = ["id", "name", "phone", "address", "notes", "credit_limit_minor",
                  "credit_limit_currency", "is_active", "balance", "updated_at"]

    def get_balance(self, obj) -> dict:
        return self.context.get("balances", {}).get(obj.id, {})


class SyncStockLevelSerializer(serializers.ModelSerializer):
    class Meta:
        model = StockLevel
        fields = ["id", "product", "store", "quantity", "updated_at"]


class SyncExchangeRateSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    store = serializers.UUIDField(allow_null=True)
    rate_cdf_per_usd = serializers.CharField(help_text="CDF pour 1 USD, 4 décimales.")
    effective_at = serializers.DateTimeField()


class PullChangesSerializer(serializers.Serializer):
    stores = SyncStoreSerializer(many=True)
    categories = SyncCategorySerializer(many=True)
    products = SyncProductSerializer(many=True)
    customers = SyncCustomerSerializer(many=True)
    stock_levels = SyncStockLevelSerializer(many=True)
    exchange_rate = SyncExchangeRateSerializer(allow_null=True)


class PullResponseSerializer(serializers.Serializer):
    cursor = serializers.CharField(help_text="Curseur opaque à renvoyer dans `since` au prochain appel.")
    has_more = serializers.BooleanField(help_text="Vrai : rappeler `pull` immédiatement avec ce curseur.")
    server_time = serializers.DateTimeField()
    changes = PullChangesSerializer()


class StatusResponseSerializer(serializers.Serializer):
    server_time = serializers.DateTimeField()
    min_app_version = serializers.CharField()
    max_offline_days = serializers.IntegerField()
