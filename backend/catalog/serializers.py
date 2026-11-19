from rest_framework import serializers

from .models import Category, Product
from .permissions import can_see_cost


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ["id", "name", "parent", "created_at", "updated_at"]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate_name(self, value):
        value = value.strip()
        qs = Category.objects.filter(name__iexact=value)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError("Une catégorie porte déjà ce nom.")
        return value

    def validate_parent(self, parent):
        if parent is not None and self.instance is not None:
            node = parent
            while node is not None:  # empêche les cycles
                if node.pk == self.instance.pk:
                    raise serializers.ValidationError("Une catégorie ne peut pas être son propre ancêtre.")
                node = node.parent
        return parent


class ProductSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source="category.name", read_only=True, default=None)

    class Meta:
        model = Product
        fields = [
            "id", "name", "sku", "barcode", "category", "category_name", "kind", "unit",
            "price_minor", "currency", "cost_minor", "track_stock", "min_stock", "is_active",
            "created_at", "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def to_representation(self, instance):
        data = super().to_representation(instance)
        request = self.context.get("request")
        if request is None or not can_see_cost(request):
            data.pop("cost_minor", None)
        return data

    def _unique(self, field, value, message):
        qs = Product.objects.filter(**{field: value})
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError({field: message})

    def validate(self, attrs):
        if "sku" in attrs:
            attrs["sku"] = attrs["sku"].strip()
            self._unique("sku", attrs["sku"], "Ce SKU existe déjà.")
        if attrs.get("barcode"):
            attrs["barcode"] = attrs["barcode"].strip()
            self._unique("barcode", attrs["barcode"], "Ce code-barres existe déjà.")
        kind = attrs.get("kind", self.instance.kind if self.instance else Product.KIND_PRODUCT)
        if kind == Product.KIND_SERVICE:
            attrs["track_stock"] = False
        return attrs
