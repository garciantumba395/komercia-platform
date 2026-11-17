from datetime import date, timedelta

import pytest
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from accounts.models import Membership, User
from core.tenancy import tenant_context
from customers import services
from customers.models import AppendOnlyError, CreditEntry, Customer, PaymentAllocation

pytestmark = pytest.mark.django_db
BASE = "/api/v1/customers"


def client_with_role(handle, role, phone):
    user = User.objects.create_user(phone=phone, password="MotDePasse-123!", full_name=role)
    Membership.objects.create(user=user, tenant=handle.tenant, role=role)
    c = APIClient()
    c.credentials(HTTP_AUTHORIZATION=f"Token {Token.objects.create(user=user).key}")
    return c


def mk(handle, name="Client", limit=1_000_000, ccy="CDF", **kw):
    with tenant_context(handle.tenant.id):
        return Customer.objects.create(name=name, credit_limit_minor=limit, credit_limit_currency=ccy, **kw)


# ---------- services ----------

def test_balance_and_charge(tenant_a):
    c = mk(tenant_a)
    with tenant_context(tenant_a.tenant.id):
        services.charge_credit(c, 10_000, "CDF")
        services.charge_credit(c, 500, "USD", limit_equivalent_minor=1_000)
        assert services.get_balance(c) == {"CDF": 10_000, "USD": 500}


def test_limit_exceeded_refused_and_override(tenant_a):
    c = mk(tenant_a, limit=10_000)
    with tenant_context(tenant_a.tenant.id):
        services.charge_credit(c, 8_000, "CDF")
        with pytest.raises(services.CreditLimitExceeded):
            services.charge_credit(c, 2_001, "CDF")
        services.charge_credit(c, 2_000, "CDF")  # pile à la limite
        e = services.charge_credit(c, 5_000, "CDF", override=True)
        assert e.amount_minor == 5_000
        assert services.get_balance(c) == {"CDF": 15_000}


def test_zero_limit_refuses_credit(tenant_a):
    c = mk(tenant_a, limit=0)
    with tenant_context(tenant_a.tenant.id):
        with pytest.raises(services.CreditLimitExceeded):
            services.charge_credit(c, 1, "CDF")


def test_limit_other_currency_uses_equivalent(tenant_a):
    c = mk(tenant_a, limit=100_000, ccy="CDF")
    with tenant_context(tenant_a.tenant.id):
        with pytest.raises(services.InvalidCreditOperation):
            services.charge_credit(c, 10, "USD")
        with pytest.raises(services.CreditLimitExceeded):
            services.charge_credit(c, 10, "USD", limit_equivalent_minor=100_001)
        services.charge_credit(c, 10, "USD", limit_equivalent_minor=28_000)


def test_partial_then_total_payment(tenant_a):
    c = mk(tenant_a)
    with tenant_context(tenant_a.tenant.id):
        services.charge_credit(c, 10_000, "CDF")
        services.register_payment(c, 4_000, "CDF")
        assert services.get_balance(c) == {"CDF": 6_000}
        services.register_payment(c, 6_000, "CDF", method="mobile_money")
        assert services.get_balance(c) == {"CDF": 0}
        assert not services.open_charges(c).exists()


def test_fifo_allocation_over_several_charges(tenant_a):
    c = mk(tenant_a)
    with tenant_context(tenant_a.tenant.id):
        c1 = services.charge_credit(c, 3_000, "CDF")
        c2 = services.charge_credit(c, 5_000, "CDF")
        c3 = services.charge_credit(c, 4_000, "CDF")
        p = services.register_payment(c, 9_000, "CDF")
        alloc = {a.charge_entry_id: a.amount_minor for a in PaymentAllocation.objects.filter(payment_entry=p)}
        assert alloc == {c1.id: 3_000, c2.id: 5_000, c3.id: 1_000}
        open_ = {x.id: x.open_minor for x in services.open_charges(c)}
        assert open_ == {c3.id: 3_000}


def test_payment_above_debt_refused(tenant_a):
    c = mk(tenant_a)
    with tenant_context(tenant_a.tenant.id):
        services.charge_credit(c, 1_000, "CDF")
        with pytest.raises(services.PaymentExceedsDebt):
            services.register_payment(c, 1_001, "CDF")
        assert CreditEntry.objects.filter(kind="payment").count() == 0
        with pytest.raises(services.PaymentExceedsDebt):  # pas de dette USD
            services.register_payment(c, 1, "USD")


def test_mixed_currencies_separate(tenant_a):
    c = mk(tenant_a)
    with tenant_context(tenant_a.tenant.id):
        services.charge_credit(c, 5_000, "CDF")
        services.charge_credit(c, 200, "USD", limit_equivalent_minor=1)
        services.register_payment(c, 200, "USD")
        assert services.get_balance(c) == {"CDF": 5_000, "USD": 0}
        services.register_payment(c, 2_000, "CDF")
        assert services.get_balance(c) == {"CDF": 3_000, "USD": 0}


def test_ledger_is_append_only(tenant_a):
    c = mk(tenant_a)
    with tenant_context(tenant_a.tenant.id):
        e = services.charge_credit(c, 100, "CDF")
        e.amount_minor = 1
        with pytest.raises(AppendOnlyError):
            e.save()
        with pytest.raises(AppendOnlyError):
            e.delete()
        for bad in (0, -5, 1.5, True):
            with pytest.raises(services.InvalidCreditOperation):
                services.charge_credit(c, bad, "CDF")


# ---------- API ----------

def test_customer_crud_and_search(tenant_a):
    api = tenant_a.client()
    r = api.post(f"{BASE}/customers/", {"name": "Marie Kabila", "phone": "+243811111111"}, format="json")
    assert r.status_code == 201 and r.data["credit_limit_minor"] == 0
    api.post(f"{BASE}/customers/", {"name": "Jean", "phone": "+243822222222"}, format="json")
    assert len(api.get(f"{BASE}/customers/").data["results"]) == 2
    assert [x["name"] for x in api.get(f"{BASE}/customers/?q=kabila").data["results"]] == ["Marie Kabila"]
    assert [x["name"] for x in api.get(f"{BASE}/customers/?q=8222").data["results"]] == ["Jean"]
    r = api.patch(f"{BASE}/customers/{r.data['id']}/", {"notes": "fidèle"}, format="json")
    assert r.status_code == 200 and r.data["notes"] == "fidèle"


def test_with_debt_filter(tenant_a):
    a, b = mk(tenant_a, "Debiteur"), mk(tenant_a, "Sain")
    with tenant_context(tenant_a.tenant.id):
        services.charge_credit(a, 100, "CDF")
        services.charge_credit(b, 100, "CDF")
        services.register_payment(b, 100, "CDF")
    res = tenant_a.client().get(f"{BASE}/customers/?with_debt=1").data["results"]
    assert [x["name"] for x in res] == ["Debiteur"]
    assert res[0]["balance"] == {"CDF": 100}


def test_payment_endpoint_and_statement(tenant_a):
    c = mk(tenant_a)
    with tenant_context(tenant_a.tenant.id):
        services.charge_credit(c, 10_000, "CDF")
        services.charge_credit(c, 100, "USD", limit_equivalent_minor=1)
    api = tenant_a.client()
    r = api.post(f"{BASE}/customers/{c.id}/payments/",
                 {"amount_minor": 4_000, "currency": "CDF", "method": "cash"}, format="json")
    assert r.status_code == 201 and r.data["balance"] == {"CDF": 6_000, "USD": 100}
    r = api.post(f"{BASE}/customers/{c.id}/payments/",
                 {"amount_minor": 6_001, "currency": "CDF"}, format="json")
    assert r.status_code == 400 and "dépasse" in r.data["detail"]
    st = api.get(f"{BASE}/customers/{c.id}/statement/").data
    assert st["balance"] == {"CDF": 6_000, "USD": 100}
    assert [(e["kind"], e["currency"], e["amount_minor"]) for e in st["entries"]] == [
        ("charge", "CDF", 10_000), ("charge", "USD", 100), ("payment", "CDF", 4_000)]
    assert st["entries"][2]["allocations"][0]["amount_minor"] == 4_000


def test_debts_sorted_and_overdue(tenant_a):
    old, recent, late = mk(tenant_a, "Ancien"), mk(tenant_a, "Recent"), mk(tenant_a, "Retard")
    today = date.today()
    with tenant_context(tenant_a.tenant.id):
        services.charge_credit(old, 100, "CDF", due_date=today + timedelta(days=10))
        services.charge_credit(late, 200, "CDF", due_date=today - timedelta(days=3))
        services.charge_credit(recent, 300, "USD", limit_equivalent_minor=1)
    api = tenant_a.client()
    rows = api.get(f"{BASE}/debts/").data
    assert [r["customer"]["name"] for r in rows] == ["Ancien", "Retard", "Recent"]
    assert [r["is_overdue"] for r in rows] == [False, True, False]
    assert rows[1]["days_overdue"] == 3 and rows[1]["overdue_balance"] == {"CDF": 200}
    over = api.get(f"{BASE}/debts/overdue/").data
    assert [r["customer"]["name"] for r in over] == ["Retard"]


def test_destroy_with_history_deactivates(tenant_a):
    c, d = mk(tenant_a, "Avec"), mk(tenant_a, "Sans")
    with tenant_context(tenant_a.tenant.id):
        services.charge_credit(c, 100, "CDF")
    api = tenant_a.client()
    assert api.delete(f"{BASE}/customers/{c.id}/").status_code == 409
    assert api.get(f"{BASE}/customers/{c.id}/").data["is_active"] is False
    assert api.delete(f"{BASE}/customers/{d.id}/").status_code == 204


# ---------- isolation ----------

def test_isolation_between_tenants(tenant_a, tenant_b):
    cb = mk(tenant_b, "Client B")
    with tenant_context(tenant_b.tenant.id):
        services.charge_credit(cb, 500, "CDF")
    api = tenant_a.client()
    mk(tenant_a, "Client A")
    assert [x["name"] for x in api.get(f"{BASE}/customers/").data["results"]] == ["Client A"]
    assert api.get(f"{BASE}/debts/").data == []
    assert api.get(f"{BASE}/debts/overdue/").data == []
    assert api.get(f"{BASE}/customers/{cb.id}/").status_code == 404
    assert api.get(f"{BASE}/customers/{cb.id}/statement/").status_code == 404
    assert api.patch(f"{BASE}/customers/{cb.id}/", {"name": "x"}, format="json").status_code == 404
    assert api.delete(f"{BASE}/customers/{cb.id}/").status_code == 404
    assert api.post(f"{BASE}/customers/{cb.id}/payments/",
                    {"amount_minor": 100, "currency": "CDF"}, format="json").status_code == 404
    with tenant_context(tenant_b.tenant.id):
        assert services.get_balance(Customer.objects.get(id=cb.id)) == {"CDF": 500}
    with tenant_context(tenant_a.tenant.id):
        assert CreditEntry.objects.count() == 0 and PaymentAllocation.objects.count() == 0


# ---------- permissions ----------

@pytest.mark.parametrize("role,read,write", [
    ("owner", 200, 201), ("manager", 200, 201), ("cashier", 200, 201),
    ("accountant", 200, 403), ("storekeeper", 403, 403),
])
def test_role_matrix(tenant_a, role, read, write):
    api = client_with_role(tenant_a, role, f"+2439{abs(hash(role)) % 10**8:08d}")
    assert api.get(f"{BASE}/customers/").status_code == read
    assert api.get(f"{BASE}/debts/").status_code == read
    assert api.post(f"{BASE}/customers/", {"name": "N"}, format="json").status_code == write


def test_credit_limit_only_owner_manager(tenant_a):
    c = mk(tenant_a, limit=0)
    cashier = client_with_role(tenant_a, "cashier", "+243900000011")
    r = cashier.patch(f"{BASE}/customers/{c.id}/", {"credit_limit_minor": 5_000}, format="json")
    assert r.status_code == 403
    r = cashier.post(f"{BASE}/customers/", {"name": "N", "credit_limit_minor": 9}, format="json")
    assert r.status_code == 403
    assert cashier.patch(f"{BASE}/customers/{c.id}/", {"notes": "ok"}, format="json").status_code == 200
    manager = client_with_role(tenant_a, "manager", "+243900000012")
    r = manager.patch(f"{BASE}/customers/{c.id}/", {"credit_limit_minor": 5_000}, format="json")
    assert r.status_code == 200 and r.data["credit_limit_minor"] == 5_000
    assert tenant_a.client().patch(f"{BASE}/customers/{c.id}/", {"credit_limit_minor": 7_000},
                                   format="json").status_code == 200


def test_accountant_cannot_pay_and_can_read_statement(tenant_a):
    c = mk(tenant_a)
    with tenant_context(tenant_a.tenant.id):
        services.charge_credit(c, 100, "CDF")
    acc = client_with_role(tenant_a, "accountant", "+243900000013")
    assert acc.get(f"{BASE}/customers/{c.id}/statement/").status_code == 200
    assert acc.post(f"{BASE}/customers/{c.id}/payments/",
                    {"amount_minor": 100, "currency": "CDF"}, format="json").status_code == 403
    store = client_with_role(tenant_a, "storekeeper", "+243900000014")
    assert store.get(f"{BASE}/customers/{c.id}/statement/").status_code == 403
