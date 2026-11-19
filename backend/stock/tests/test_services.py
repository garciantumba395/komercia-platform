from decimal import Decimal

import pytest

from catalog.models import Product
from catalog.tests.helpers import make_product
from core.tenancy import tenant_context
from stock.models import StockLevel, StockMovement
from stock.services import InsufficientStock, apply_movement, get_quantity


@pytest.fixture
def ctx(tenant_a):
    product = make_product(tenant_a)
    with tenant_context(tenant_a.tenant.id):
        yield tenant_a, product, tenant_a.store


def test_in_out_adjustment(ctx):
    h, p, s = ctx
    assert get_quantity(p, s) == 0
    apply_movement(p, s, Decimal("10"), "in", user=h.user)
    apply_movement(p, s, Decimal("-3.5"), "out", reason="casse")
    apply_movement(p, s, Decimal("-0.5"), "adjustment", reason="inventaire")
    assert get_quantity(p, s) == Decimal("6.000")
    kinds = list(StockMovement.objects.order_by("created_at").values_list("kind", "quantity"))
    assert kinds == [("in", Decimal("10")), ("out", Decimal("-3.5")), ("adjustment", Decimal("-0.5"))]
    assert StockMovement.objects.first().created_by_id in (None, h.user.id)
    assert StockLevel.objects.count() == 1


def test_untracked_product_ignored(ctx):
    h, _, s = ctx
    service = make_product(h, sku="SRV", kind="service")
    assert apply_movement(service, s, 5, "sale") is None
    assert StockMovement.objects.count() == 0 and StockLevel.objects.count() == 0


def test_negative_allowed_by_default_refused_on_demand(ctx):
    h, p, s = ctx
    apply_movement(p, s, 2, "in")
    apply_movement(p, s, -5, "sale", reference="V-1")
    assert get_quantity(p, s) == Decimal("-3")
    with pytest.raises(InsufficientStock):
        apply_movement(p, s, -1, "sale", allow_negative=False)
    assert get_quantity(p, s) == Decimal("-3")
    assert StockMovement.objects.count() == 2  # aucun mouvement créé lors du refus


def test_insufficient_stock_exact_boundary(ctx):
    h, p, s = ctx
    apply_movement(p, s, 5, "in")
    apply_movement(p, s, -5, "sale", allow_negative=False)  # va exactement à 0 : OK
    assert get_quantity(p, s) == 0
    with pytest.raises(InsufficientStock):
        apply_movement(p, s, Decimal("-0.001"), "sale", allow_negative=False)


def test_weighted_average_cost(ctx):
    h, p, s = ctx  # coût initial 1000, stock 0
    apply_movement(p, s, 10, "in", unit_cost_minor=1000)
    apply_movement(p, s, 10, "in", unit_cost_minor=1200)
    p.refresh_from_db()
    assert p.cost_minor == 1100
    apply_movement(p, s, -5, "sale")  # une sortie ne change pas le coût
    apply_movement(p, s, 15, "in", unit_cost_minor=1500)  # (15*1100 + 15*1500)/30 = 1300
    p.refresh_from_db()
    assert p.cost_minor == 1300
    apply_movement(p, s, 1, "in")  # sans coût : inchangé
    p.refresh_from_db()
    assert p.cost_minor == 1300


def test_weighted_average_from_empty_stock_takes_unit_cost(ctx):
    h, p, s = ctx
    apply_movement(p, s, 4, "in", unit_cost_minor=777)
    p.refresh_from_db()
    assert p.cost_minor == 777


def test_weighted_average_ignores_negative_stock(ctx):
    h, p, s = ctx
    apply_movement(p, s, -3, "sale")
    apply_movement(p, s, 10, "in", unit_cost_minor=500)
    p.refresh_from_db()
    assert p.cost_minor == 500


def test_weighted_average_spans_stores(ctx):
    from accounts.models import Store

    h, p, s = ctx
    s2 = Store.objects.create(name="Dépôt")
    apply_movement(p, s, 10, "in", unit_cost_minor=1000)
    apply_movement(p, s2, 10, "in", unit_cost_minor=2000)
    p.refresh_from_db()
    assert p.cost_minor == 1500
    assert get_quantity(p, s) == 10 and get_quantity(p, s2) == 10


def test_movement_is_append_only(ctx):
    h, p, s = ctx
    m = apply_movement(p, s, 1, "in")
    m.reason = "x"
    with pytest.raises(RuntimeError):
        m.save()
    with pytest.raises(RuntimeError):
        m.delete()


def test_zero_delta_rejected(ctx):
    h, p, s = ctx
    with pytest.raises(ValueError):
        apply_movement(p, s, 0, "in")


def test_product_service_flag(ctx):
    h, _, _ = ctx
    svc = Product.objects.create(name="Coupe", sku="CP", kind="service", track_stock=True)
    assert svc.track_stock is False
