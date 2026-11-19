"""Accès support avec consentement (KOM-082) : API du propriétaire + consultation en console."""

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from accounts.models import Membership
from audit.models import AuditEntry
from backoffice.models import SupportAccessGrant
from core.permissions import CASHIER, MANAGER
from core.tenancy import tenant_context

from .helpers import DB, agent_client, make_agent, make_sale

pytestmark = DB


def _grant(handle, hours=2, reason="Aide pour la caisse"):
    return handle.client().post("/api/v1/support/grant/", {"hours": hours, "reason": reason}, format="json")


def _audit(handle, action):
    with tenant_context(handle.tenant.id):
        return list(AuditEntry.objects.filter(action=action).order_by("created_at"))


# --- API du propriétaire -------------------------------------------------------------------------------------


def test_owner_grants_reads_and_revokes(tenant_a):
    api = tenant_a.client()
    assert api.get("/api/v1/support/").json()["active"] is False
    response = _grant(tenant_a, hours=24)
    assert response.status_code == 201
    body = response.json()
    assert body["active"] and body["current"]["scope"] == "read" and body["current"]["valid"]
    assert api.get("/api/v1/support/").json()["active"] is True
    revoked = api.post("/api/v1/support/revoke/")
    assert revoked.status_code == 200 and revoked.json()["active"] is False
    assert [e.action for e in _audit(tenant_a, "support.grant")] == ["support.grant"]
    assert len(_audit(tenant_a, "support.revoke")) == 1


@pytest.mark.parametrize("hours", [0, 73, -5, "abc", None])
def test_grant_duration_bounds(tenant_a, hours):
    response = tenant_a.client().post("/api/v1/support/grant/", {"hours": hours}, format="json")
    assert response.status_code == 400
    assert SupportAccessGrant.objects.count() == 0


@pytest.mark.parametrize("hours", [1, 72])
def test_grant_duration_limits_accepted(tenant_a, hours):
    assert _grant(tenant_a, hours=hours).status_code == 201
    grant = SupportAccessGrant.objects.get()
    assert abs((grant.expires_at - grant.created_at) - timedelta(hours=hours)) < timedelta(seconds=5)


def test_only_owner_can_grant_or_revoke(tenant_a):
    from rest_framework.authtoken.models import Token
    from rest_framework.test import APIClient

    User = get_user_model()
    for role in (MANAGER, CASHIER):
        user = User.objects.create_user(phone=f"+24399111{role[:3].encode().hex()[:4]}", password="x-Secret-1234",
                                        full_name=role)
        Membership.objects.create(user=user, tenant=tenant_a.tenant, role=role)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Token {Token.objects.create(user=user).key}")
        assert client.post("/api/v1/support/grant/", {"hours": 2}, format="json").status_code == 403
        assert client.post("/api/v1/support/revoke/").status_code == 403
        assert client.get("/api/v1/support/").status_code == 403
    assert SupportAccessGrant.objects.count() == 0


def test_anonymous_cannot_grant(client):
    assert client.post("/api/v1/support/grant/", {"hours": 2}, content_type="application/json").status_code == 401


def test_tenants_are_isolated_in_support_api(tenant_a, tenant_b):
    _grant(tenant_a)
    assert tenant_b.client().get("/api/v1/support/").json()["active"] is False
    assert tenant_b.client().post("/api/v1/support/revoke/").json()["active"] is False
    assert tenant_a.client().get("/api/v1/support/").json()["active"] is True


def test_new_grant_replaces_previous(tenant_a):
    _grant(tenant_a, hours=2)
    _grant(tenant_a, hours=5)
    valid = [g for g in SupportAccessGrant.objects.all() if g.is_valid()]
    assert len(valid) == 1


def test_owner_can_grant_even_when_subscription_read_only(tenant_a):
    from billing.models import Subscription

    with tenant_context(tenant_a.tenant.id):
        Subscription.objects.update(state="read_only", read_only_since=timezone.now())
    assert _grant(tenant_a).status_code == 201


# --- Console ------------------------------------------------------------------------------------------------


def _data_urls(handle):
    base = f"/ops/tenants/{handle.tenant.id}/support"
    return [f"{base}/sales/", f"{base}/products/", f"{base}/customers/"]


def test_no_consent_no_detailed_data(tenant_a):
    make_sale(tenant_a, "A-SECRET-1")
    client = agent_client()
    for url in _data_urls(tenant_a):
        response = client.get(url)
        assert response.status_code == 403
        assert "A-SECRET-1" not in response.content.decode()
    assert _audit(tenant_a, "platform.support_view") == []


def test_with_consent_agent_reads_and_every_view_is_audited(tenant_a):
    from catalog.models import Product
    from customers.models import Customer

    make_sale(tenant_a, "A-SECRET-1", total=5000)
    with tenant_context(tenant_a.tenant.id):
        Product.objects.create(name="Savon Marseille", sku="SAV1", price_minor=2500)
        Customer.objects.create(name="Mama Client")
    _grant(tenant_a)
    agent = make_agent()
    client = agent_client(agent)
    pages = dict(zip(["sales", "products", "customers"], _data_urls(tenant_a), strict=True))
    expectations = {"sales": "A-SECRET-1", "products": "Savon Marseille", "customers": "Mama Client"}
    for kind, url in pages.items():
        response = client.get(url)
        assert response.status_code == 200 and expectations[kind] in response.content.decode()
    entries = _audit(tenant_a, "platform.support_view")
    assert [e.after["page"] for e in entries] == ["sales", "products", "customers"]
    assert all(e.user_id == agent.id and e.entity_id == str(tenant_a.tenant.id) for e in entries)
    # Le propriétaire voit ces consultations dans son propre journal d'audit (API).
    audit_api = tenant_a.client().get("/api/v1/audit/entries/?action=platform.support_view").json()
    results = audit_api["results"] if isinstance(audit_api, dict) else audit_api
    assert any(r["action"] == "platform.support_view" for r in results)


def test_consent_is_per_tenant(tenant_a, tenant_b):
    make_sale(tenant_b, "B-SECRET-1")
    _grant(tenant_a)
    assert agent_client().get(_data_urls(tenant_b)[0]).status_code == 403


def test_expired_grant_denies(tenant_a):
    _grant(tenant_a)
    SupportAccessGrant.objects.update(expires_at=timezone.now() - timedelta(minutes=1))
    client = agent_client()
    assert client.get(_data_urls(tenant_a)[0]).status_code == 403
    assert tenant_a.client().get("/api/v1/support/").json()["active"] is False
    assert _audit(tenant_a, "platform.support_view") == []


def test_revoked_grant_denies(tenant_a):
    _grant(tenant_a)
    client = agent_client()
    assert client.get(_data_urls(tenant_a)[0]).status_code == 200
    tenant_a.client().post("/api/v1/support/revoke/")
    assert client.get(_data_urls(tenant_a)[0]).status_code == 403


def test_detail_page_shows_link_only_with_consent(tenant_a):
    client = agent_client()
    url = f"/ops/tenants/{tenant_a.tenant.id}/"
    assert "/support/sales/" not in client.get(url).content.decode()
    _grant(tenant_a)
    assert "/support/sales/" in client.get(url).content.decode()


def test_console_is_read_only_no_write_route(tenant_a):
    _grant(tenant_a)
    client = agent_client()
    for url in _data_urls(tenant_a):
        assert client.post(url, {"name": "x"}).status_code == 405 or client.post(url).status_code in (403, 405)
        assert client.delete(url).status_code in (403, 405)


def test_unknown_support_page_404(tenant_a):
    _grant(tenant_a)
    assert agent_client().get(f"/ops/tenants/{tenant_a.tenant.id}/support/secrets/").status_code == 404


def test_merchant_cannot_use_support_pages(tenant_a, tenant_b):
    from django.test import Client

    _grant(tenant_b)
    client = Client()
    client.force_login(tenant_a.user)
    assert client.get(_data_urls(tenant_b)[0]).status_code == 403
