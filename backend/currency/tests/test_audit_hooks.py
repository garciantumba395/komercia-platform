"""Journal d'audit : création de taux de change (KOM-027)."""

from unittest import mock

import pytest

from audit.models import AuditEntry
from catalog.tests.helpers import client_for_role
from core.tenancy import tenant_context
from currency.models import ExchangeRate

pytestmark = pytest.mark.django_db
URL = "/api/v1/currency/rates/"


def entries(handle, action=None):
    with tenant_context(handle.tenant.id):
        qs = AuditEntry.objects.order_by("created_at")
        return list(qs.filter(action=action) if action else qs)


def test_rate_creation_is_audited(tenant_a):
    r = tenant_a.client().post(URL, {"rate_cdf_per_usd": "2850.5", "note": "marché"}, format="json")
    assert r.status_code == 201
    (e,) = entries(tenant_a, "exchange_rate.set")
    assert (e.entity_type, e.entity_id, e.user_id) == ("exchangerate", r.json()["id"], tenant_a.user.id)
    assert e.before is None and e.after["rate_cdf_per_usd"] == "2850.5000"
    assert e.after["set_by_id"] == str(tenant_a.user.id)


def test_each_rate_creates_one_entry(tenant_a):
    api = tenant_a.client()
    api.post(URL, {"rate_cdf_per_usd": "2800"}, format="json")
    api.post(URL, {"rate_cdf_per_usd": "2900"}, format="json")
    assert len(entries(tenant_a, "exchange_rate.set")) == 2


def test_failed_rate_creates_no_entry(tenant_a):
    assert tenant_a.client().post(URL, {"rate_cdf_per_usd": "0"}, format="json").status_code == 400
    assert client_for_role(tenant_a, "cashier").post(
        URL, {"rate_cdf_per_usd": "2800"}, format="json").status_code == 403
    assert entries(tenant_a) == []


def test_rate_entries_are_isolated(tenant_a, tenant_b):
    tenant_a.client().post(URL, {"rate_cdf_per_usd": "2800"}, format="json")
    assert entries(tenant_b) == []
    assert tenant_b.client().get("/api/v1/audit/entries/").data["results"] == []


def test_audit_failure_rolls_back_rate(tenant_a):
    with mock.patch("audit.services.log", side_effect=RuntimeError("journal indisponible")):
        with pytest.raises(RuntimeError):
            tenant_a.client().post(URL, {"rate_cdf_per_usd": "2800"}, format="json")
    with tenant_context(tenant_a.tenant.id):
        assert ExchangeRate.objects.count() == 0
