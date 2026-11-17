"""Services de stock, appelés notamment par le module `sales`.

Les signatures de `apply_movement` et `get_quantity` sont un contrat : ne pas les modifier.
"""

from decimal import ROUND_HALF_UP, Decimal

from django.db import IntegrityError, transaction
from django.db.models import Sum

from catalog.models import Product

from .models import StockLevel, StockMovement


class InsufficientStock(Exception):
    def __init__(self, product, store, available, requested):
        self.product, self.store = product, store
        self.available, self.requested = available, requested
        super().__init__(
            f"Stock insuffisant pour « {product.name} » : {available} disponible, {requested} demandé."
        )


def get_quantity(product, store) -> Decimal:
    """Quantité en stock du produit dans la boutique (0 si aucune ligne)."""
    level = StockLevel.objects.filter(product=product, store=store).first()
    return level.quantity if level else Decimal("0")


def _locked_level(product, store) -> StockLevel:
    qs = StockLevel.objects.select_for_update().filter(product=product, store=store)
    level = qs.first()
    if level is None:
        try:
            with transaction.atomic():
                StockLevel.objects.create(
                    product=product, store=store, quantity=0, tenant_id=product.tenant_id
                )
        except IntegrityError:  # créée entre-temps par une transaction concurrente
            pass
        level = qs.get()
    return level


def _weighted_average_cost(product, old_total: Decimal, delta: Decimal, unit_cost_minor: int) -> int:
    base = max(old_total, Decimal("0"))
    new_total = base + delta
    value = (base * product.cost_minor + delta * unit_cost_minor) / new_total
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def apply_movement(
    product,
    store,
    quantity_delta,
    kind,
    *,
    reason="",
    reference="",
    unit_cost_minor=None,
    user=None,
    allow_negative=True,
):
    """Applique un mouvement de stock (delta signé) et renvoie le `StockMovement` créé.

    - Ne fait rien (renvoie None) pour un produit sans suivi de stock (service, track_stock False).
    - Verrouille la ligne de stock (select_for_update) : à appeler dans une transaction.
    - Lève `InsufficientStock` si `allow_negative` est False et que le stock deviendrait négatif.
    - Pour une entrée avec `unit_cost_minor`, recalcule le coût moyen pondéré du produit (KOM-046).
    """
    if not product.track_stock:
        return None
    delta = Decimal(str(quantity_delta))
    if delta == 0:
        raise ValueError("La quantité d'un mouvement ne peut pas être nulle.")

    with transaction.atomic():
        # Verrou produit : sérialise aussi le recalcul du coût moyen.
        locked_product = Product.objects.select_for_update().get(pk=product.pk)
        level = _locked_level(locked_product, store)
        new_quantity = level.quantity + delta
        if not allow_negative and new_quantity < 0:
            raise InsufficientStock(product, store, level.quantity, -delta)

        if kind == StockMovement.IN and unit_cost_minor is not None and delta > 0:
            old_total = (
                StockLevel.objects.filter(product=locked_product).aggregate(t=Sum("quantity"))["t"]
                or Decimal("0")
            )
            new_cost = _weighted_average_cost(locked_product, old_total, delta, unit_cost_minor)
            Product.objects.filter(pk=product.pk).update(cost_minor=new_cost)
            product.cost_minor = new_cost

        level.quantity = new_quantity
        level.save(update_fields=["quantity", "updated_at"])
        return StockMovement.objects.create(
            product=product,
            store=store,
            kind=kind,
            quantity=delta,
            unit_cost_minor=unit_cost_minor,
            reason=reason,
            reference=reference,
            created_by=user,
            tenant_id=locked_product.tenant_id,
        )
