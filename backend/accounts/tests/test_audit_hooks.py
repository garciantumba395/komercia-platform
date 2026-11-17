"""Journal d'audit branché sur les boutiques et la gestion des utilisateurs (KOM-027)."""

from unittest import mock

import pytest

from accounts.models import Membership, Store, User
from audit.models import AuditEntry
from catalog.tests.helpers import client_for_role
from core.tenancy import tenant_context

pytestmark = pytest.mark.django_db
STORES = "/api/v1/stores/"
MEMBERS = "/api/v1/members/"


def entries(handle, action=None):
    with tenant_context(handle.tenant.id):
        qs = AuditEntry.objects.order_by("created_at")
        if action:
            qs = qs.filter(action=action)
        return list(qs)


def new_member(api, phone="+243990000077", role="cashier"):
    r = api.post(MEMBERS, {"full_name": "Caissier", "phone": phone,
                           "password": "MotDePasse-123!", "role": role}, format="json")
    assert r.status_code == 201, r.content
    return r.json()["id"]


def test_store_create_update_delete_are_audited(tenant_a):
    api = tenant_a.client()
    r = api.post(STORES, {"name": "Annexe", "address": "Gombe"}, format="json")
    assert r.status_code == 201
    store_id = r.json()["id"]
    (e,) = entries(tenant_a, "store.create")
    assert (e.entity_type, e.entity_id, e.user_id) == ("store", store_id, tenant_a.user.id)
    assert e.before is None and e.after["name"] == "Annexe"

    assert api.patch(f"{STORES}{store_id}/", {"name": "Annexe 2"}, format="json").status_code == 200
    (e,) = entries(tenant_a, "store.update")
    assert e.entity_id == store_id and e.before["name"] == "Annexe" and e.after["name"] == "Annexe 2"

    assert api.delete(f"{STORES}{store_id}/").status_code == 204
    (e,) = entries(tenant_a, "store.delete")
    assert e.entity_id == store_id and e.before["name"] == "Annexe 2" and e.after is None


def test_member_create_deactivate_reactivate_are_audited(tenant_a):
    api = tenant_a.client()
    uid = new_member(api)
    (e,) = entries(tenant_a, "user.create")
    assert (e.entity_type, e.entity_id, e.user_id) == ("user", uid, tenant_a.user.id)
    assert e.after["role"] == "cashier" and e.after["phone"] == "+243990000077"
    assert "password" not in str(e.after)

    assert api.patch(f"{MEMBERS}{uid}/", {"is_active": False}, format="json").status_code == 200
    (e,) = entries(tenant_a, "user.deactivate")
    assert e.entity_id == uid and e.before == {"is_active": True} and e.after == {"is_active": False}
    assert api.patch(f"{MEMBERS}{uid}/", {"is_active": True}, format="json").status_code == 200
    (e,) = entries(tenant_a, "user.reactivate")
    assert e.entity_id == uid and e.before == {"is_active": False} and e.after == {"is_active": True}


def test_member_patch_without_change_is_not_audited(tenant_a):
    api = tenant_a.client()
    uid = new_member(api)
    assert api.patch(f"{MEMBERS}{uid}/", {"is_active": True}, format="json").status_code == 200
    assert entries(tenant_a, "user.deactivate") == [] and entries(tenant_a, "user.reactivate") == []


def test_failed_operations_create_no_entry(tenant_a):
    api = tenant_a.client()
    assert api.post(STORES, {"name": ""}, format="json").status_code == 400
    assert api.post(MEMBERS, {"full_name": "X", "phone": "+243990000078", "password": "x",
                              "role": "cashier"}, format="json").status_code == 400
    # propre rôle : refusé
    assert api.patch(f"{MEMBERS}{tenant_a.user.id}/", {"is_active": False},
                     format="json").status_code == 400
    cashier = client_for_role(tenant_a, "cashier")
    assert cashier.post(STORES, {"name": "Interdit"}, format="json").status_code == 403
    assert cashier.post(MEMBERS, {}, format="json").status_code == 403
    assert entries(tenant_a) == []


def test_entries_are_isolated_between_tenants(tenant_a, tenant_b):
    tenant_a.client().post(STORES, {"name": "Annexe A"}, format="json")
    tenant_b.client().post(STORES, {"name": "Annexe B"}, format="json")
    assert [e.after["name"] for e in entries(tenant_a)] == ["Annexe A"]
    assert [e.after["name"] for e in entries(tenant_b)] == ["Annexe B"]
    names = {r["after"]["name"] for r in tenant_a.client().get("/api/v1/audit/entries/").data["results"]}
    assert names == {"Annexe A"}


def test_audit_failure_rolls_back_store_and_member(tenant_a):
    api = tenant_a.client()
    with mock.patch("audit.services.log", side_effect=RuntimeError("journal indisponible")):
        with pytest.raises(RuntimeError):
            api.post(STORES, {"name": "Fantôme"}, format="json")
        with pytest.raises(RuntimeError):
            new_member(api, phone="+243990000079")
    with tenant_context(tenant_a.tenant.id):
        assert not Store.objects.filter(name="Fantôme").exists()
        assert not Membership.objects.filter(user__phone="+243990000079").exists()
    assert not User.objects.filter(phone="+243990000079").exists()
