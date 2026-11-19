import re

from rest_framework import serializers

from .models import BillingNotice, Plan, SubscriptionPayment

PHONE_RE = re.compile(r"^\+?\d{9,15}$")


class PaymentSerializer(serializers.ModelSerializer):
    plan = serializers.CharField(source="plan.code", read_only=True)

    class Meta:
        model = SubscriptionPayment
        fields = ["id", "reference", "plan", "kind", "amount_minor", "currency", "amount_usd_minor",
                  "rate_cdf_per_usd", "provider", "status", "period_start", "period_end", "phone",
                  "created_at", "confirmed_at"]
        read_only_fields = fields


class PaymentBriefSerializer(serializers.ModelSerializer):
    class Meta:
        model = SubscriptionPayment
        fields = ["id", "reference", "amount_minor", "currency", "status"]
        read_only_fields = fields


class NoticeSerializer(serializers.ModelSerializer):
    class Meta:
        model = BillingNotice
        fields = ["id", "kind", "due_at", "created_at", "read_at"]
        read_only_fields = fields


class PlanSerializer(serializers.ModelSerializer):
    price_cdf_estimate = serializers.SerializerMethodField()

    class Meta:
        model = Plan
        fields = ["code", "name", "price_usd_minor", "extra_store_price_usd_minor", "max_stores",
                  "max_users", "features", "price_cdf_estimate"]

    def get_price_cdf_estimate(self, plan):
        rate = self.context.get("rate")
        if rate is None:
            return None
        from currency.money import convert_minor

        return convert_minor(plan.price_usd_minor, "USD", "CDF", rate.rate_cdf_per_usd).amount_minor


def _clean_phone(value: str) -> str:
    value = value.replace(" ", "")
    if value and not PHONE_RE.match(value):
        raise serializers.ValidationError("Numéro de téléphone invalide (ex. +243990000000).")
    return value


class SubscribeSerializer(serializers.Serializer):
    plan = serializers.CharField()
    currency = serializers.ChoiceField(choices=["CDF", "USD"])
    phone = serializers.CharField()

    def validate_phone(self, value):
        value = _clean_phone(value)
        if not value:
            raise serializers.ValidationError("Le numéro de téléphone est obligatoire.")
        return value


class ChangePlanSerializer(serializers.Serializer):
    plan = serializers.CharField()
    currency = serializers.ChoiceField(choices=["CDF", "USD"], default="USD")
    phone = serializers.CharField(required=False, allow_blank=True, default="")

    def validate_phone(self, value):
        return _clean_phone(value)


class ProviderResultSerializer(serializers.Serializer):
    reference = serializers.CharField()
    status = serializers.ChoiceField(choices=["confirmed", "failed"])
