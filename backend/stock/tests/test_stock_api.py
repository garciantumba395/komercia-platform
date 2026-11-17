from decimal import Decimal

import pytest

from catalog.tests.helpers import client_for_role, make_product
from core.tenancy import tenant_context
from stock.models import StockMovement
from stock.services import apply_movement

L = "/api/v1/stock/levels/"
M = "/api/v1/stock/movements/"
A = "/api/v1/stock/alerts/low-stock/"
ALL = ["owner", "manager", "cashier", "storekeeper", "accountant"]


def _seed(h, qty="10", **kw):
    p = make_product(h, **kw)
    with tenant_context(h.tenant.id):
        apply_movement(p, h.store, Decimal(qty), "in", unit_cost_minor=1000)
    return p


def _move(c, h, p, **kw):
    body = {"product": str(p.id), "store": str(h.store.id), "kind": "in", "quantity": "5"}
    body.update(kw)
    return c.post(M, body, format="json")


@pytest.mark.parametrize("role", ["owner", "manager", "storekeeper"])
def test_adjusters_can_create_movements(tenant_a, role):
    p = _seed(tenant_a)
    c = client_for_role(tenant_a, role)
    assert _move(c, tenant_a, p, kind="in", quantity="5", unit_cost_minor=1600).status_code == 201
    r = _move(c, tenant_a, p, kind="out", quantity="3", reason="casse")
    assert r.status_code == 201 and Decimal(r.json()["quantity"]) == Decimal("-3")
    r = _move(c, tenant_a, p, kind="adjustment", quantity="-2", reason="inventaire")
    assert r.status_code == 201
    lvl = c.get(L).json()["results"][0]
    assert Decimal(lvl["quantity"]) == Decimal("10")  # 10 + 5 - 3 - 2


@pytest.mark.parametrize("role", ["cashier", "accountant"])
def test_others_cannot_write_stock(tenant_a, role):
    p = _seed(tenant_a)
    assert _move(client_for_role(tenant_a, role), tenant_a, p).status_code == 403


@pytest.mark.parametrize("role", ALL)
def test_everyone_reads_levels_and_movements(tenant_a, role):
    _seed(tenant_a)
    c = client_for_role(tenant_a, role)
    assert c.get(L).json()["count"] == 1
    r = c.get(M)
    assert r.status_code == 200
    assert ("unit_cost_minor" in r.json()["results"][0]) == (role != "cashier")
    assert c.get(A).status_code == 200


def test_reason_required_for_out_and_adjustment(tenant_a):
    p = _seed(tenant_a)
    c = tenant_a.client()
    assert _move(c, tenant_a, p, kind="out", quantity="1").status_code == 400
    assert _move(c, tenant_a, p, kind="adjustment", quantity="1", reason="  ").status_code == 400
    assert _move(c, tenant_a, p, kind="in", quantity="1").status_code == 201


def test_validation_rules(tenant_a):
    p = _seed(tenant_a)
    svc = make_product(tenant_a, sku="SRV", kind="service")
    c = tenant_a.client()
    assert _move(c, tenant_a, p, quantity="0").status_code == 400
    assert _move(c, tenant_a, p, kind="in", quantity="-1").status_code == 400
    assert _move(c, tenant_a, svc).status_code == 400  # service sans suivi
    assert _move(c, tenant_a, p, kind="sale", quantity="1").status_code == 400  # type interdit en API
    assert _move(c, tenant_a, p, kind="out", quantity="1", reason="x", unit_cost_minor=5).status_code == 400


def test_manual_out_cannot_go_negative(tenant_a):
    p = _seed(tenant_a, qty="2")
    r = _move(tenant_a.client(), tenant_a, p, kind="out", quantity="3", reason="vol")
    assert r.status_code == 400
    with tenant_context(tenant_a.tenant.id):
        assert StockMovement.objects.count() == 1


def test_movements_append_only_via_api(tenant_a):
    p = _seed(tenant_a)
    c = tenant_a.client()
    mid = c.get(M).json()["results"][0]["id"]
    assert c.patch(f"{M}{mid}/", {"reason": "x"}, format="json").status_code in (403, 405)
    assert c.put(f"{M}{mid}/", {}, format="json").status_code in (403, 405)
    assert c.delete(f"{M}{mid}/").status_code in (403, 405)
    assert p.id


def test_movement_filters(tenant_a):
    p1 = _seed(tenant_a, sku="A")
    p2 = _seed(tenant_a, sku="B", name="Autre")
    c = tenant_a.client()
    assert c.get(M, {"product": str(p1.id)}).json()["count"] == 1
    assert c.get(M, {"kind": "in"}).json()["count"] == 2
    assert c.get(M, {"kind": "out"}).json()["count"] == 0
    assert c.get(M, {"store": str(tenant_a.store.id)}).json()["count"] == 2
    assert c.get(M, {"date_from": "2999-01-01"}).json()["count"] == 0
    assert c.get(M, {"date_from": "nope"}).status_code == 400
    assert p2.id


def test_levels_filters_and_low_stock(tenant_a):
    ok = _seed(tenant_a, qty="10", sku="OK", name="Ok", min_stock="5")
    low = _seed(tenant_a, qty="5", sku="LOW", name="Bas", min_stock="5")
    under = _seed(tenant_a, qty="1", sku="UND", name="Sous", min_stock="5")
    _seed(tenant_a, qty="1", sku="NOMIN", name="Zéro", min_stock="0")
    c = tenant_a.client()
    assert c.get(L).json()["count"] == 4
    assert c.get(L, {"product": str(ok.id)}).json()["count"] == 1
    low_ids = {r["product"] for r in c.get(L, {"low": "1"}).json()["results"]}
    assert low_ids == {str(low.id), str(under.id)}
    alerts = c.get(A).json()
    assert alerts["count"] == 2 and alerts["results"][0]["product"] == str(under.id)
    assert c.get(L, {"store": str(tenant_a.store.id)}).json()["count"] == 4
    assert c.get(L, {"store": "00000000-0000-0000-0000-000000000000"}).json()["count"] == 0


def test_isolation(tenant_a, tenant_b):
    p = _seed(tenant_a, min_stock="50")
    b = tenant_b.client()
    assert b.get(L).json()["count"] == 0
    assert b.get(M).json()["count"] == 0
    assert b.get(A).json()["count"] == 0
    level_id = tenant_a.client().get(L).json()["results"][0]["id"]
    mov_id = tenant_a.client().get(M).json()["results"][0]["id"]
    assert b.get(f"{L}{level_id}/").status_code == 404
    assert b.get(f"{M}{mov_id}/").status_code == 404
    # B ne peut pas mouvementer un produit ou une boutique de A
    assert _move(b, tenant_a, p).status_code == 400
    r = b.post(M, {"product": str(p.id), "store": str(tenant_b.store.id), "kind": "in", "quantity": "1"},
               format="json")
    assert r.status_code == 400
    assert tenant_a.client().get(L).json()["count"] == 1
