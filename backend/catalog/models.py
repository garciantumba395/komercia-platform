from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower

from core.models import TenantOwnedModel


class Category(TenantOwnedModel):
    name = models.CharField(max_length=120)
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="children"
    )

    class Meta:
        verbose_name_plural = "categories"
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(Lower("name"), "tenant", name="uniq_category_name_per_tenant"),
        ]

    def __str__(self):
        return self.name


class Product(TenantOwnedModel):
    KIND_PRODUCT = "product"
    KIND_SERVICE = "service"
    KINDS = [(KIND_PRODUCT, "Produit"), (KIND_SERVICE, "Service")]
    UNITS = [
        ("piece", "Pièce"),
        ("kg", "Kilogramme"),
        ("litre", "Litre"),
        ("box", "Boîte"),
        ("meter", "Mètre"),
        ("other", "Autre"),
    ]
    CURRENCIES = [("CDF", "Franc congolais"), ("USD", "Dollar américain")]

    name = models.CharField(max_length=200)
    sku = models.CharField(max_length=64)
    barcode = models.CharField(max_length=64, blank=True, default="")
    category = models.ForeignKey(
        Category, null=True, blank=True, on_delete=models.SET_NULL, related_name="products"
    )
    kind = models.CharField(max_length=10, choices=KINDS, default=KIND_PRODUCT)
    unit = models.CharField(max_length=10, choices=UNITS, default="piece")
    price_minor = models.PositiveBigIntegerField(default=0)
    currency = models.CharField(max_length=3, choices=CURRENCIES, default="CDF")
    cost_minor = models.PositiveBigIntegerField(default=0)
    track_stock = models.BooleanField(default=False)
    min_stock = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["tenant", "sku"], name="uniq_product_sku_per_tenant"),
            models.UniqueConstraint(
                fields=["tenant", "barcode"],
                condition=~Q(barcode=""),
                name="uniq_product_barcode_per_tenant",
            ),
        ]
        indexes = [
            models.Index(fields=["tenant", "kind", "is_active"], name="prod_kind_active_idx"),
        ]

    def __str__(self):
        return f"{self.name} ({self.sku})"

    def save(self, *args, **kwargs):
        if self.kind == self.KIND_SERVICE:
            self.track_stock = False
        super().save(*args, **kwargs)
