"""Journal d'audit branché sur les mouvements de stock de l'API (KOM-027)."""

from decimal import Decimal
from unittest import mock

import pytest

from audit.models import AuditEntry
from catalog.tests.helpers import client_for_role
from core.tenancy import tenant_context
from stock.models import StockMovement
from stock.services import apply_movement
from stock.tests.test_stock_api import _move, _seed

pytestmark = pytest.mark.django_db


def entries(handle, action=None):
    with tenant_context(handle.tenant.id):
        qs = AuditEntry.objects.order_by("created_at")
        return list(qs.filter(action=action) if action else qs)


def test_in_and_out_create_one_stock_movement_entry(tenant_a):
    p = _seed(tenant_a, "10")
    api = tenant_a.client()
    r = _move(api, tenant_a, p, kind="in", quantity="5", reason="réappro")
    mid = r.json()["id"]
    (e,) = entries(tenant_a, "stock.movement")
    assert (e.entity_type, e.entity_id, e.user_id) == ("stockmovement", mid, tenant_a.user.id)
    assert e.reason == "réappro"
    assert Decimal(e.before["quantity"]) == 10 and Decimal(e.after["quantity"]) == 15
    assert e.after["kind"] == "in" and e.after["product_id"] == str(p.id)
    assert entries(tenant_a, "stock.adjust") == []

    _move(api, tenant_a, p, kind="out", quantity="3", reason="casse")
    assert len(entries(tenant_a, "stock.movement")) == 2
    assert entries(tenant_a, "stock.adjust") == []


def test_adjustment_creates_movement_and_adjust_entries(tenant_a):
    p = _seed(tenant_a, "10")
    r = _move(tenant_a.client(), tenant_a, p, kind="adjustment", quantity="-2", reason="inventaire")
    assert r.status_code == 201
    (m,) = entries(tenant_a, "stock.movement")
    (a,) = entries(tenant_a, "stock.adjust")
    for e in (m, a):
        assert e.entity_id == r.json()["id"] and e.reason == "inventaire"
        assert e.user_id == tenant_a.user.id
        assert Decimal(e.before["quantity"]) == 10 and Decimal(e.after["quantity"]) == 8
        assert Decimal(e.after["delta"]) == -2


def test_service_level_sale_and_return_movements_are_not_audited(tenant_a):
    p = _seed(tenant_a, "10")
    with tenant_context(tenant_a.tenant.id):
        apply_movement(p, tenant_a.store, Decimal("-1"), "sale", reference="V-1")
        apply_movement(p, tenant_a.store, Decimal("1"), "return", reference="V-1")
    assert entries(tenant_a) == []


def test_failed_movements_create_no_entry(tenant_a):
    p = _seed(tenant_a, "1")
    api = tenant_a.client()
    assert _move(api, tenant_a, p, kind="adjustment", quantity="-1").status_code == 400  # sans motif
    assert _move(api, tenant_a, p, kind="out", quantity="5", reason="x").status_code == 400  # stock
    assert _move(api, tenant_a, p, kind="in", quantity="-5").status_code == 400
    assert _move(client_for_role(tenant_a, "cashier"), tenant_a, p).status_code == 403
    assert entries(tenant_a) == []


def test_entries_are_isolated_between_tenants(tenant_a, tenant_b):
    p = _seed(tenant_a)
    _move(tenant_a.client(), tenant_a, p)
    assert len(entries(tenant_a, "stock.movement")) == 1
    assert entries(tenant_b) == []
    assert tenant_b.client().get("/api/v1/audit/entries/").data["results"] == []


def test_audit_failure_rolls_back_movement_and_level(tenant_a):
    p = _seed(tenant_a, "10")
    with mock.patch("audit.services.log", side_effect=RuntimeError("journal indisponible")):
        with pytest.raises(RuntimeError):
            _move(tenant_a.client(), tenant_a, p, kind="in", quantity="5")
    with tenant_context(tenant_a.tenant.id):
        from stock.services import get_quantity

        assert get_quantity(p, tenant_a.store) == Decimal("10")
        assert StockMovement.objects.filter(product=p).count() == 1  # le seul mouvement du seed
