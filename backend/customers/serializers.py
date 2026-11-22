from rest_framework import serializers

from .models import CURRENCIES, PAYMENT_METHODS, CreditEntry, Customer


class CustomerSerializer(serializers.ModelSerializer):
    balance = serializers.SerializerMethodField()

    class Meta:
        model = Customer
        fields = ["id", "name", "phone", "address", "notes", "credit_limit_minor",
                  "credit_limit_currency", "is_active", "balance", "created_at", "updated_at"]
        read_only_fields = ["id", "balance", "created_at", "updated_at"]

    def get_balance(self, obj):
        cache = self.context.get("balances")
        if cache is not None:
            return cache.get(obj.id, {})
        from .services import get_balance
        return get_balance(obj)


class CreditEntrySerializer(serializers.ModelSerializer):
    allocations = serializers.SerializerMethodField()

    class Meta:
        model = CreditEntry
        fields = ["id", "kind", "amount_minor", "currency", "sale_id", "due_date", "method",
                  "note", "created_by", "created_at", "allocations"]

    def get_allocations(self, obj):
        rel = obj.allocations_made if obj.kind == "payment" else obj.allocations_received
        key = "charge_entry_id" if obj.kind == "payment" else "payment_entry_id"
        return [{"entry_id": str(getattr(a, key)), "amount_minor": a.amount_minor} for a in rel.all()]


class PaymentInputSerializer(serializers.Serializer):
    amount_minor = serializers.IntegerField(min_value=1)
    currency = serializers.ChoiceField(choices=[c for c, _ in CURRENCIES])
    method = serializers.ChoiceField(choices=[m for m, _ in PAYMENT_METHODS], default="cash")
    note = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
