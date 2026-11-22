"""Branchements de l'abonnement dans les autres modules : lecture seule, limites de plan, synchronisation."""

from datetime import timedelta

import pytest
from django.utils import timezone

from billing.models import Plan
from billing.tests.helpers import get_sub, role_client, update_sub
from core.tenancy import tenant_context

pytestmark = pytest.mark.django_db

PRODUCT = {"name": "Savon", "sku": "S-1", "kind": "product", "unit": "piece",
           "price_minor": 1500, "currency": "CDF"}


def expire_trial(handle):
    update_sub(handle, trial_ends_at=timezone.now() - timedelta(days=1))


def test_expired_trial_blocks_writes_but_not_reads(tenant_a):
    api = tenant_a.client()
    assert api.post("/api/v1/catalog/products/", PRODUCT, format="json").status_code == 201
    expire_trial(tenant_a)
    blocked = api.post("/api/v1/catalog/products/", {**PRODUCT, "sku": "S-2"}, format="json")
    assert blocked.status_code == 402
    assert blocked.data["code"] == "subscription_inactive"
    assert api.get("/api/v1/catalog/products/").status_code == 200
    assert api.get("/api/v1/reports/daily/").status_code == 200
    assert api.get("/api/v1/catalog/products/export/").status_code == 200


def test_read_only_applies_to_hasrole_views_too(tenant_a):
    api = tenant_a.client()
    expire_trial(tenant_a)
    assert api.post("/api/v1/stores/", {"name": "Autre"}, format="json").status_code == 402
    assert api.post("/api/v1/users/", {"full_name": "X", "phone": "+243995550001",
                                       "password": "Employe-Pass-1!", "role": "cashier"},
                    format="json").status_code == 402
    assert api.get("/api/v1/stores/").status_code == 200


def test_billing_and_auth_stay_available_when_read_only(tenant_a):
    api = tenant_a.client()
    expire_trial(tenant_a)
    assert api.get("/api/v1/billing/subscription/").status_code == 200
    assert api.get("/api/v1/auth/me/").status_code == 200
    # /api/v1/sync/ n'est pas bloqué par la permission (402) : la requête est traitée.
    assert api.get("/api/v1/sync/status/").status_code == 200


def test_sync_accepts_sales_made_before_read_only_and_rejects_later_ones(tenant_a):
    from catalog.tests.helpers import make_product
    from sync.tests.helpers import make_rate, push, results, sale_op, stock

    make_rate(tenant_a)
    product = make_product(tenant_a, price_minor=1000)
    stock(tenant_a, product, 50)
    update_sub(tenant_a, trial_ends_at=timezone.now() - timedelta(days=2))
    sub = get_sub(tenant_a)
    since = sub.trial_ends_at
    api = tenant_a.client()
    before = sale_op(tenant_a, product, device_time=since - timedelta(hours=3))
    after = sale_op(tenant_a, product, device_time=since + timedelta(hours=3))
    res = results(push(api, [before, after]))
    assert res[0]["status"] == "applied", res[0]
    assert res[1]["status"] == "rejected"
    assert res[1]["error"]["code"] == "subscription_inactive"


def test_subscribing_reopens_writes(tenant_a):
    api = tenant_a.client()
    expire_trial(tenant_a)
    res = api.post("/api/v1/billing/subscribe/",
                   {"plan": "essentiel", "currency": "USD", "phone": "+243990000001"}, format="json")
    assert res.status_code == 201, res.data
    from billing import services

    with tenant_context(tenant_a.tenant.id):
        services.confirm_payment(res.data["payment"]["reference"])
    assert api.post("/api/v1/catalog/products/", PRODUCT, format="json").status_code == 201


def test_store_limit_of_plan(tenant_a):
    api = tenant_a.client()
    sub = get_sub(tenant_a)
    plan = Plan.objects.get(code="essentiel")
    update_sub(tenant_a, plan=plan, state="active", trial_ends_at=None,
               current_period_start=timezone.now(), current_period_end=timezone.now() + timedelta(days=30))
    assert sub is not None
    res = api.post("/api/v1/stores/", {"name": "Deuxième"}, format="json")
    assert res.status_code == 403
    assert res.data["code"] == "plan_limit_reached"


def test_user_limit_of_plan(tenant_a):
    api = tenant_a.client()
    plan = Plan.objects.get(code="essentiel")  # 3 utilisateurs, le propriétaire compte pour 1
    update_sub(tenant_a, plan=plan, state="active", trial_ends_at=None,
               current_period_start=timezone.now(), current_period_end=timezone.now() + timedelta(days=30))
    for i in range(2):
        r = api.post("/api/v1/users/", {"full_name": f"E{i}", "phone": f"+24399555000{i}",
                                        "password": "Employe-Pass-1!", "role": "cashier"}, format="json")
        assert r.status_code == 201, r.data
    over = api.post("/api/v1/users/", {"full_name": "E3", "phone": "+243995550009",
                                       "password": "Employe-Pass-1!", "role": "cashier"}, format="json")
    assert over.status_code == 403 and over.data["code"] == "plan_limit_reached"


def test_trial_gives_pro_limits(tenant_a):
    api = tenant_a.client()
    for i in range(2):  # pro : 3 boutiques, la première existe déjà
        assert api.post("/api/v1/stores/", {"name": f"B{i}"}, format="json").status_code == 201
    assert api.post("/api/v1/stores/", {"name": "B3"}, format="json").status_code == 403


def test_other_tenant_unaffected_by_read_only(tenant_a, tenant_b):
    expire_trial(tenant_a)
    assert tenant_b.client().post("/api/v1/catalog/products/", PRODUCT, format="json").status_code == 201
    with tenant_context(tenant_b.tenant.id):
        assert get_sub(tenant_b).state == "trial"


def test_cashier_role_client_is_also_blocked(tenant_a):
    cashier = role_client(tenant_a, "cashier", "+243995551234")
    expire_trial(tenant_a)
    assert cashier.post("/api/v1/customers/customers/", {"name": "Client"}, format="json").status_code == 402
