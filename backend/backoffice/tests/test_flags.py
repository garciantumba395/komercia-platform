"""Fonctions par entreprise (KOM-084)."""

from audit.models import AuditEntry
from backoffice.flags import is_enabled
from backoffice.models import FeatureFlag
from core.tenancy import tenant_context

from .helpers import DB, agent_client, make_agent

pytestmark = DB


def _set(client, handle, key="loyalty", on=True, reason="Pilote validé"):
    data = {"key": key, "reason": reason}
    if on:
        data["enabled"] = "on"
    return client.post(f"/ops/tenants/{handle.tenant.id}/flags/set/", data)


def test_default_and_override(tenant_a):
    assert is_enabled(tenant_a.tenant.id, "loyalty") is False
    assert is_enabled(tenant_a.tenant.id, "loyalty", default=True) is True
    FeatureFlag.objects.create(tenant=tenant_a.tenant, key="loyalty", enabled=False)
    assert is_enabled(tenant_a.tenant.id, "loyalty", default=True) is False


def test_platform_owner_sets_flag_with_audit(tenant_a, tenant_b):
    owner = make_agent(superuser=True)
    client = agent_client(owner)
    assert _set(client, tenant_a).status_code == 302
    assert is_enabled(tenant_a.tenant.id, "loyalty") and not is_enabled(tenant_b.tenant.id, "loyalty")
    _set(client, tenant_a, on=False, reason="Fin du pilote")
    assert not is_enabled(tenant_a.tenant.id, "loyalty")
    with tenant_context(tenant_a.tenant.id):
        entries = list(AuditEntry.objects.filter(action="platform.flag_set").order_by("created_at"))
    assert [e.reason for e in entries] == ["Pilote validé", "Fin du pilote"]
    assert entries[0].user_id == owner.id and entries[0].after == {"key": "loyalty", "enabled": True}
    assert entries[1].before == {"key": "loyalty", "enabled": True}


def test_regular_agent_cannot_set_flag(tenant_a):
    assert _set(agent_client(), tenant_a).status_code == 403
    assert not is_enabled(tenant_a.tenant.id, "loyalty")


def test_flag_requires_reason_and_known_key(tenant_a):
    client = agent_client(make_agent(superuser=True))
    _set(client, tenant_a, reason="")
    _set(client, tenant_a, key="inconnue")
    assert FeatureFlag.objects.count() == 0


def test_merchant_cannot_set_flag(tenant_a):
    from django.test import Client

    client = Client()
    client.force_login(tenant_a.user)
    assert _set(client, tenant_a).status_code == 403


def test_flag_csrf(tenant_a):
    client = agent_client(make_agent(superuser=True), enforce_csrf_checks=True)
    assert _set(client, tenant_a).status_code == 403
