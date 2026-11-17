
from rest_framework import serializers

from accounts.models import Store
from catalog.models import Product
from catalog.permissions import can_see_cost

from .models import StockLevel, StockMovement


class StockLevelSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="product.name", read_only=True)
    sku = serializers.CharField(source="product.sku", read_only=True)
    unit = serializers.CharField(source="product.unit", read_only=True)
    store_name = serializers.CharField(source="store.name", read_only=True)
    min_stock = serializers.DecimalField(
        source="product.min_stock", max_digits=12, decimal_places=3, read_only=True
    )
    is_low = serializers.SerializerMethodField()

    class Meta:
        model = StockLevel
        fields = ["id", "product", "product_name", "sku", "unit", "store", "store_name",
                  "quantity", "min_stock", "is_low", "updated_at"]

    def get_is_low(self, obj) -> bool:
        return obj.quantity <= obj.product.min_stock


class StockMovementSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="product.name", read_only=True)

    class Meta:
        model = StockMovement
        fields = ["id", "product", "product_name", "store", "kind", "quantity", "unit_cost_minor",
                  "reason", "reference", "created_by", "created_at"]
        read_only_fields = fields

    def to_representation(self, instance):
        data = super().to_representation(instance)
        request = self.context.get("request")
        if request is None or not can_see_cost(request):
            data.pop("unit_cost_minor", None)
        return data


class MovementCreateSerializer(serializers.Serializer):
    """Entrée / sortie / ajustement manuels.

    `quantity` est positive pour `in` et `out` (le signe est déduit du type) ; pour `adjustment`
    c'est le delta signé (positif ou négatif, non nul).
    """

    product = serializers.PrimaryKeyRelatedField(queryset=Product.objects.all())
    store = serializers.PrimaryKeyRelatedField(queryset=Store.objects.all())
    kind = serializers.ChoiceField(choices=[("in", "Entrée"), ("out", "Sortie"), ("adjustment", "Ajustement")])
    quantity = serializers.DecimalField(max_digits=12, decimal_places=3)
    unit_cost_minor = serializers.IntegerField(min_value=0, required=False, allow_null=True)
    reason = serializers.CharField(required=False, allow_blank=True, default="")
    reference = serializers.CharField(required=False, allow_blank=True, default="", max_length=100)

    def validate(self, attrs):
        kind, qty = attrs["kind"], attrs["quantity"]
        if not attrs["product"].track_stock:
            raise serializers.ValidationError({"product": "Ce produit n'a pas de suivi de stock."})
        if qty == 0:
            raise serializers.ValidationError({"quantity": "La quantité ne peut pas être nulle."})
        if kind in ("in", "out") and qty < 0:
            raise serializers.ValidationError({"quantity": "La quantité doit être positive."})
        if kind in ("out", "adjustment") and not attrs["reason"].strip():
            raise serializers.ValidationError({"reason": "Le motif est obligatoire."})
        if kind != "in" and attrs.get("unit_cost_minor") is not None:
            raise serializers.ValidationError({"unit_cost_minor": "Réservé aux entrées."})
        attrs["delta"] = -qty if kind == "out" else qty
        attrs["unit_cost_minor"] = attrs.get("unit_cost_minor")
        return attrs

    def to_representation(self, instance):
        return StockMovementSerializer(instance, context=self.context).data


