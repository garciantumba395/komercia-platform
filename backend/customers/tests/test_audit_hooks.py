"""Journal d'audit branché sur les clients, limites de crédit et paiements de dettes (KOM-027)."""

from unittest import mock

import pytest

from audit.models import AuditEntry
from catalog.tests.helpers import client_for_role
from core.tenancy import tenant_context
from customers import services
from customers.models import CreditEntry, Customer
from customers.tests.test_customers import mk

pytestmark = pytest.mark.django_db
BASE = "/api/v1/customers"
CUST = f"{BASE}/customers/"


def entries(handle, action=None):
    with tenant_context(handle.tenant.id):
        qs = AuditEntry.objects.order_by("created_at")
        return list(qs.filter(action=action) if action else qs)


def test_customer_create_update_delete_are_audited(tenant_a):
    api = tenant_a.client()
    cid = api.post(CUST, {"name": "Marie", "phone": "+243811111111"}, format="json").json()["id"]
    (e,) = entries(tenant_a, "customer.create")
    assert (e.entity_type, e.entity_id, e.user_id) == ("customer", cid, tenant_a.user.id)
    assert e.before is None and e.after["name"] == "Marie"

    assert api.patch(f"{CUST}{cid}/", {"notes": "fidèle"}, format="json").status_code == 200
    (e,) = entries(tenant_a, "customer.update")
    assert e.before["notes"] == "" and e.after["notes"] == "fidèle"
    assert entries(tenant_a, "credit_limit.change") == []

    assert api.delete(f"{CUST}{cid}/").status_code == 204
    (e,) = entries(tenant_a, "customer.delete")
    assert e.entity_id == cid and e.before["name"] == "Marie" and e.after is None


def test_delete_with_history_is_audited_as_deactivation(tenant_a):
    c = mk(tenant_a)
    with tenant_context(tenant_a.tenant.id):
        services.charge_credit(c, 1_000, "CDF")
    assert tenant_a.client().delete(f"{CUST}{c.id}/").status_code == 409
    (e,) = entries(tenant_a, "customer.deactivate")
    assert e.entity_id == str(c.id) and e.before["is_active"] is True and e.after["is_active"] is False
    assert entries(tenant_a, "customer.delete") == []


def test_credit_limit_change_is_audited(tenant_a):
    c = mk(tenant_a, limit=1_000)
    r = tenant_a.client().patch(f"{CUST}{c.id}/", {"credit_limit_minor": 5_000,
                                                   "credit_limit_currency": "USD"}, format="json")
    assert r.status_code == 200
    (e,) = entries(tenant_a, "credit_limit.change")
    assert (e.entity_type, e.entity_id, e.user_id) == ("customer", str(c.id), tenant_a.user.id)
    assert e.before == {"credit_limit_minor": 1_000, "credit_limit_currency": "CDF"}
    assert e.after == {"credit_limit_minor": 5_000, "credit_limit_currency": "USD"}
    assert len(entries(tenant_a, "customer.update")) == 1


def test_debt_payment_is_audited(tenant_a):
    c = mk(tenant_a)
    with tenant_context(tenant_a.tenant.id):
        services.charge_credit(c, 10_000, "CDF")
    r = tenant_a.client().post(f"{CUST}{c.id}/payments/", {"amount_minor": 4_000, "currency": "CDF",
                                                           "method": "mobile_money"}, format="json")
    assert r.status_code == 201, r.content
    (e,) = entries(tenant_a, "debt.payment")
    assert (e.entity_type, e.entity_id, e.user_id) == ("creditentry", r.json()["entry"]["id"],
                                                       tenant_a.user.id)
    assert e.after == {"customer_id": str(c.id), "amount_minor": 4_000, "currency": "CDF",
                       "method": "mobile_money", "note": ""}


def test_failed_operations_create_no_entry(tenant_a):
    c = mk(tenant_a)
    with tenant_context(tenant_a.tenant.id):
        services.charge_credit(c, 1_000, "CDF")
    api = tenant_a.client()
    assert api.post(CUST, {"name": ""}, format="json").status_code == 400
    assert api.post(f"{CUST}{c.id}/payments/", {"amount_minor": 9_999, "currency": "CDF"},
                    format="json").status_code == 400  # dépasse la dette
    assert api.post(f"{CUST}{c.id}/payments/", {"amount_minor": 0, "currency": "CDF"},
                    format="json").status_code == 400
    cashier = client_for_role(tenant_a, "cashier")
    assert cashier.patch(f"{CUST}{c.id}/", {"credit_limit_minor": 9}, format="json").status_code == 403
    accountant = client_for_role(tenant_a, "accountant")
    assert accountant.post(f"{CUST}{c.id}/payments/", {"amount_minor": 10, "currency": "CDF"},
                           format="json").status_code == 403
    assert entries(tenant_a) == []


def test_entries_are_isolated_between_tenants(tenant_a, tenant_b):
    tenant_a.client().post(CUST, {"name": "Client A"}, format="json")
    assert entries(tenant_b) == []
    assert tenant_b.client().get("/api/v1/audit/entries/").data["results"] == []
    assert len(tenant_a.client().get("/api/v1/audit/entries/").data["results"]) == 1


def test_audit_failure_rolls_back_operations(tenant_a):
    c = mk(tenant_a, limit=1_000)
    with tenant_context(tenant_a.tenant.id):
        services.charge_credit(c, 5_000, "CDF", override=True)
    api = tenant_a.client()
    with mock.patch("audit.services.log", side_effect=RuntimeError("journal indisponible")):
        with pytest.raises(RuntimeError):
            api.post(CUST, {"name": "Fantôme"}, format="json")
        with pytest.raises(RuntimeError):
            api.patch(f"{CUST}{c.id}/", {"credit_limit_minor": 77}, format="json")
        with pytest.raises(RuntimeError):
            api.post(f"{CUST}{c.id}/payments/", {"amount_minor": 2_000, "currency": "CDF"},
                     format="json")
    with tenant_context(tenant_a.tenant.id):
        assert not Customer.objects.filter(name="Fantôme").exists()
        assert Customer.objects.get(pk=c.pk).credit_limit_minor == 1_000
        assert CreditEntry.objects.filter(customer=c, kind="payment").count() == 0
