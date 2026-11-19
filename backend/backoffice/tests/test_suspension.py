"""Suspension et réactivation (KOM-081)."""

from audit.models import AuditEntry
from core.tenancy import tenant_context

from .helpers import DB, agent_client, make_agent

pytestmark = DB


def _audit(handle, action):
    with tenant_context(handle.tenant.id):
        return list(AuditEntry.objects.filter(action=action))


def _toggle_url(handle, action):
    return f"/ops/tenants/{handle.tenant.id}/{action}/"


def test_confirmation_page_has_form_and_csrf(tenant_a):
    html = agent_client().get(_toggle_url(tenant_a, "suspend")).content.decode()
    assert 'name="reason"' in html and "csrfmiddlewaretoken" in html


def test_suspend_requires_reason(tenant_a):
    client = agent_client()
    for reason in ("", "   ", "ab"):
        response = client.post(_toggle_url(tenant_a, "suspend"), {"reason": reason, "confirm": "yes"})
        assert response.status_code == 400
        assert "motif est obligatoire" in response.content.decode()
    from accounts.models import Tenant

    assert Tenant.objects.get(pk=tenant_a.tenant.id).is_active
    assert _audit(tenant_a, "platform.tenant_suspend") == []


def test_suspend_requires_confirmation_checkbox(tenant_a):
    response = agent_client().post(_toggle_url(tenant_a, "suspend"), {"reason": "Impayé constaté"})
    assert response.status_code == 400
    from accounts.models import Tenant

    assert Tenant.objects.get(pk=tenant_a.tenant.id).is_active


def test_suspend_is_audited_with_agent_and_reason(tenant_a):
    agent = make_agent()
    response = agent_client(agent).post(_toggle_url(tenant_a, "suspend"),
                                        {"reason": "Fraude suspectée", "confirm": "yes"})
    assert response.status_code == 302
    from accounts.models import Tenant

    assert not Tenant.objects.get(pk=tenant_a.tenant.id).is_active
    (entry,) = _audit(tenant_a, "platform.tenant_suspend")
    assert entry.user_id == agent.id and entry.reason == "Fraude suspectée"
    assert entry.before == {"is_active": True} and entry.after == {"is_active": False}
    assert entry.entity_type == "tenant" and entry.entity_id == str(tenant_a.tenant.id)


def test_suspended_tenant_cannot_use_api_and_reactivation_restores(tenant_a):
    api = tenant_a.client()
    assert api.get("/api/v1/auth/me/").status_code == 200
    agent = agent_client()
    agent.post(_toggle_url(tenant_a, "suspend"), {"reason": "Impayé constaté", "confirm": "yes"})
    refused = api.get("/api/v1/auth/me/")
    assert refused.status_code == 401
    assert "Aucune entreprise active" in refused.json()["detail"]
    agent.post(_toggle_url(tenant_a, "reactivate"), {"reason": "Régularisé", "confirm": "yes"})
    assert api.get("/api/v1/auth/me/").status_code == 200
    (entry,) = _audit(tenant_a, "platform.tenant_reactivate")
    assert entry.reason == "Régularisé"


def test_double_suspend_does_not_duplicate_audit(tenant_a):
    client = agent_client()
    data = {"reason": "Impayé constaté", "confirm": "yes"}
    client.post(_toggle_url(tenant_a, "suspend"), data)
    response = client.post(_toggle_url(tenant_a, "suspend"), data)
    assert response.status_code == 400 and "déjà suspendue" in response.content.decode()
    assert len(_audit(tenant_a, "platform.tenant_suspend")) == 1


def test_unknown_action_404(tenant_a):
    client = agent_client()
    assert client.get(_toggle_url(tenant_a, "delete")).status_code == 404
    assert client.post(_toggle_url(tenant_a, "delete"), {"reason": "xxxxxx"}).status_code == 404


def test_suspend_csrf_enforced(tenant_a):
    client = agent_client(enforce_csrf_checks=True)
    response = client.post(_toggle_url(tenant_a, "suspend"), {"reason": "Impayé constaté", "confirm": "yes"})
    assert response.status_code == 403
    from accounts.models import Tenant

    assert Tenant.objects.get(pk=tenant_a.tenant.id).is_active


def test_merchant_cannot_suspend_anyone(tenant_a, tenant_b):
    from django.test import Client

    client = Client()
    client.force_login(tenant_a.user)
    response = client.post(_toggle_url(tenant_b, "suspend"), {"reason": "Concurrent", "confirm": "yes"})
    assert response.status_code == 403
    from accounts.models import Tenant

    assert Tenant.objects.get(pk=tenant_b.tenant.id).is_active


def test_get_does_not_modify(tenant_a):
    agent_client().get(_toggle_url(tenant_a, "suspend"))
    from accounts.models import Tenant

    assert Tenant.objects.get(pk=tenant_a.tenant.id).is_active
