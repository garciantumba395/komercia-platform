from decimal import Decimal

from rest_framework import serializers

from accounts.models import Store

from .models import ExchangeRate


class ExchangeRateSerializer(serializers.ModelSerializer):
    store = serializers.PrimaryKeyRelatedField(
        queryset=Store.objects.all(), allow_null=True, required=False, default=None
    )
    rate_cdf_per_usd = serializers.DecimalField(
        max_digits=14,
        decimal_places=4,
        min_value=Decimal("0.0001"),
        help_text="CDF pour 1 USD (ex. 2800 ou 2850.5).",
    )
    set_by = serializers.UUIDField(source="set_by_id", read_only=True)

    class Meta:
        model = ExchangeRate
        fields = ["id", "store", "rate_cdf_per_usd", "effective_at", "set_by", "note", "created_at"]
        read_only_fields = ["id", "set_by", "created_at"]
