from rest_framework import serializers

from .models import CURRENCIES, CashSession, Payment, Sale, SaleItem

CURRENCY_CODES = [c for c, _ in CURRENCIES]


class SaleItemInputSerializer(serializers.Serializer):
    product = serializers.UUIDField()
    quantity = serializers.DecimalField(max_digits=12, decimal_places=3, min_value=0.001)
    unit_price_minor = serializers.IntegerField(min_value=0, required=False)
    discount_minor = serializers.IntegerField(min_value=0, required=False, default=0)


class PaymentInputSerializer(serializers.Serializer):
    method = serializers.CharField(max_length=20)
    amount_minor = serializers.IntegerField(min_value=1)
    currency = serializers.ChoiceField(choices=CURRENCY_CODES)

    def validate_method(self, value):
        if value != "cash":
            raise serializers.ValidationError(
                "Ce mode de paiement sera disponible au jalon M2 : seul « cash » est accepté."
            )
        return value


class SaleInputSerializer(serializers.Serializer):
    client_id = serializers.UUIDField()
    store = serializers.UUIDField()
    currency = serializers.ChoiceField(choices=CURRENCY_CODES, required=False)
    customer = serializers.UUIDField(required=False, allow_null=True, default=None)
    items = SaleItemInputSerializer(many=True, allow_empty=False)
    discount_minor = serializers.IntegerField(min_value=0, required=False, default=0)
    payments = PaymentInputSerializer(many=True, required=False, default=list)
    credit_remaining = serializers.BooleanField(required=False, default=False)
    change_currency = serializers.ChoiceField(choices=CURRENCY_CODES, required=False)
    device_time = serializers.DateTimeField(required=False, allow_null=True, default=None)
    override_credit_limit = serializers.BooleanField(required=False, default=False)


class CancelInputSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=500)


class SaleItemSerializer(serializers.ModelSerializer):
    quantity = serializers.SerializerMethodField()

    class Meta:
        model = SaleItem
        fields = ["product", "name", "quantity", "unit_price_minor", "discount_minor", "line_total_minor"]

    def get_quantity(self, obj) -> str:
        return format(obj.quantity.normalize(), "f")


class PaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Payment
        fields = ["method", "amount_minor", "currency"]


class SaleSerializer(serializers.ModelSerializer):
    items = SaleItemSerializer(many=True, read_only=True)
    payments = PaymentSerializer(many=True, read_only=True)
    rate_cdf_per_usd = serializers.SerializerMethodField()

    class Meta:
        model = Sale
        fields = [
            "id", "number", "client_id", "status", "store", "currency", "customer", "cashier",
            "items", "subtotal_minor", "discount_minor", "tax_minor", "total_minor",
            "rate_cdf_per_usd", "rate_id", "payments", "paid_minor", "change_minor", "change_currency",
            "credit_minor", "cash_session", "device_time", "created_at",
            "cancelled_at", "cancelled_by", "cancel_reason",
        ]
        read_only_fields = fields

    def get_rate_cdf_per_usd(self, obj) -> str | None:
        return None if obj.rate_cdf_per_usd is None else f"{obj.rate_cdf_per_usd:.4f}"


class OpenSessionInputSerializer(serializers.Serializer):
    store = serializers.UUIDField()
    opening_float = serializers.DictField(child=serializers.JSONField(), required=False, default=dict)


class CloseSessionInputSerializer(serializers.Serializer):
    counted = serializers.DictField(child=serializers.JSONField())


class CashSessionSerializer(serializers.ModelSerializer):
    """Session de caisse. Pour une session ouverte, `expected`, `sales_count` et `cash_sales` sont
    calculés à la volée ; une fois clôturée, `expected` et `difference` sont ceux figés à la clôture."""

    expected = serializers.SerializerMethodField()
    sales_count = serializers.SerializerMethodField()
    cash_sales = serializers.SerializerMethodField()

    class Meta:
        model = CashSession
        fields = ["id", "status", "store", "cashier", "opened_at", "closed_at", "closed_by",
                  "opening_float", "expected", "counted", "difference", "sales_count", "cash_sales"]
        read_only_fields = fields

    def _totals(self, obj):
        from .services import session_totals

        cache = self.context.setdefault("_totals", {})
        if obj.pk not in cache:
            cache[obj.pk] = session_totals(obj)
        return cache[obj.pk]

    def get_expected(self, obj):
        return obj.expected if obj.expected is not None else self._totals(obj)["expected"]

    def get_sales_count(self, obj) -> int:
        return self._totals(obj)["sales_count"]

    def get_cash_sales(self, obj) -> dict:
        return self._totals(obj)["cash_sales"]
