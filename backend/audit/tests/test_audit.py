import datetime
import decimal
import uuid
import zlib

import pytest
from django.db import IntegrityError, ProgrammingError, connection, transaction
from django.test import RequestFactory, override_settings
from django.utils import timezone

from accounts.models import Store
from audit import services
from audit.models import AuditEntry
from core.permissions import ACCOUNTANT, CASHIER, MANAGER, OWNER, STOREKEEPER
from core.tenancy import tenant_context

pytestmark = pytest.mark.django_db
URL = "/api/v1/audit/entries/"


def make_entry(handle, **kw):
    kw.setdefault("action", "sale.cancel")
    kw.setdefault("entity_type", "sale")
    kw.setdefault("entity_id", "1")
    with tenant_context(handle.tenant.id):
        return services.log(**kw)


# ---------- sérialisation ----------

def test_log_serializes_decimal_uuid_dates(tenant_a):
    uid = uuid.uuid4()
    now = timezone.now()
    entry = make_entry(
        tenant_a, action="price.change", entity_type="product", entity_id=uid,
        before={"price": decimal.Decimal("10.50"), "id": uid, "d": datetime.date(2026, 1, 2)},
        after={"price": decimal.Decimal("12"), "at": now, "tags": {1, }},
        reason="Hausse fournisseur", device_time=now,
    )
    with tenant_context(tenant_a.tenant.id):
        e = AuditEntry.objects.get(pk=entry.pk)
    assert e.entity_id == str(uid)
    assert e.before == {"price": "10.50", "id": str(uid), "d": "2026-01-02"}
    assert e.after["price"] == "12" and e.after["tags"] == [1]
    assert isinstance(e.after["at"], str)
    assert e.tenant_id == tenant_a.tenant.id and e.created_at and e.device_time == now


def test_snapshot_of_model(tenant_a):
    snap = services.snapshot(tenant_a.store)
    assert snap["name"] == "Commerce A" and snap["id"] == str(tenant_a.store.id)
    assert snap["tenant_id"] == str(tenant_a.tenant.id)
    assert services.snapshot(tenant_a.store, fields=["name"]) == {"name": "Commerce A"}
    import json
    json.dumps(snap)
    snap_user = services.snapshot(tenant_a.user)
    assert "password" not in snap_user


def test_log_with_request_uses_user_and_ip(tenant_a):
    request = RequestFactory().get("/", HTTP_X_FORWARDED_FOR="203.0.113.7, 10.0.0.1")
    request.user = tenant_a.user
    with tenant_context(tenant_a.tenant.id):
        e = services.log("user.create", "user", "x", request=request)
    assert e.user_id == tenant_a.user.id and e.ip_address == "203.0.113.7"
    request2 = RequestFactory().get("/", REMOTE_ADDR="127.0.0.1")
    request2.user = tenant_a.user
    with tenant_context(tenant_a.tenant.id):
        assert services.log("a", "b", "c", request=request2).ip_address == "127.0.0.1"


def test_log_without_tenant_fails(db):
    with pytest.raises(RuntimeError):
        services.log("a", "b", "c")


# ---------- append-only ----------

def test_orm_update_and_delete_refused(tenant_a):
    entry = make_entry(tenant_a)
    with tenant_context(tenant_a.tenant.id):
        entry = AuditEntry.objects.get(pk=entry.pk)
        entry.reason = "x"
        with pytest.raises(RuntimeError):
            entry.save()
        with pytest.raises(RuntimeError):
            entry.delete()
        # queryset: le trigger prend le relais
        with pytest.raises(IntegrityError), transaction.atomic():
            AuditEntry.objects.filter(pk=entry.pk).update(reason="x")
        with pytest.raises(IntegrityError), transaction.atomic():
            AuditEntry.objects.filter(pk=entry.pk).delete()
        assert AuditEntry.objects.filter(pk=entry.pk, reason="").exists()


def test_direct_sql_update_and_delete_refused(tenant_a):
    entry = make_entry(tenant_a)
    with tenant_context(tenant_a.tenant.id):
        for sql in ("UPDATE audit_auditentry SET reason = 'pirate'",
                    "DELETE FROM audit_auditentry WHERE id = %s"):
            with pytest.raises(Exception, match="ajout seul"), transaction.atomic():
                with connection.cursor() as c:
                    c.execute(sql, [entry.pk] if "%s" in sql else [])
        with connection.cursor() as c:
            c.execute("SELECT count(*), max(reason) FROM audit_auditentry")
            assert c.fetchone() == (1, "")


def test_no_write_endpoint(tenant_a, client_for_role):
    entry = make_entry(tenant_a)
    api = tenant_a.client()
    assert api.post(URL, {"action": "x"}, format="json").status_code == 405
    detail = f"{URL}{entry.pk}/"
    assert api.put(detail, {}, format="json").status_code == 405
    assert api.patch(detail, {"reason": "x"}, format="json").status_code == 405
    assert api.delete(detail).status_code == 405
    assert api.get(detail).status_code == 200


# ---------- isolation ----------

def test_tenant_isolation(tenant_a, tenant_b):
    ea = make_entry(tenant_a, entity_id="A")
    eb = make_entry(tenant_b, entity_id="B")
    ids_a = {r["id"] for r in tenant_a.client().get(URL).data["results"]}
    ids_b = {r["id"] for r in tenant_b.client().get(URL).data["results"]}
    assert ids_a == {str(ea.pk)} and ids_b == {str(eb.pk)}
    assert tenant_a.client().get(f"{URL}{eb.pk}/").status_code == 404
    assert tenant_b.client().get(f"{URL}{ea.pk}/").status_code == 404
    with tenant_context(tenant_a.tenant.id):
        assert AuditEntry.objects.count() == 1


def test_cannot_write_for_other_tenant_at_db_level(tenant_a, tenant_b):
    with tenant_context(tenant_a.tenant.id):
        with pytest.raises(ProgrammingError), transaction.atomic():
            AuditEntry.objects.create(tenant_id=tenant_b.tenant.id, action="a",
                                      entity_type="b", entity_id="c")


# ---------- permissions ----------

@pytest.mark.parametrize("role,allowed", [
    (OWNER, True), (MANAGER, True), (ACCOUNTANT, True), (CASHIER, False), (STOREKEEPER, False),
])
def test_permissions_by_role(tenant_a, client_for_role, role, allowed):
    entry = make_entry(tenant_a)
    api = client_for_role(tenant_a, role, f"+24399{zlib.crc32(role.encode()) % 10**7:07d}")
    expected = 200 if allowed else 403
    assert api.get(URL).status_code == expected
    assert api.get(f"{URL}{entry.pk}/").status_code == expected


def test_anonymous_refused(db):
    from rest_framework.test import APIClient
    assert APIClient().get(URL).status_code in (401, 403)


# ---------- filtres ----------

def test_filters(tenant_a):
    other = uuid.uuid4()
    e1 = make_entry(tenant_a, action="sale.cancel", entity_type="sale", entity_id="s1",
                    user=tenant_a.user)
    e2 = make_entry(tenant_a, action="stock.adjust", entity_type="stock", entity_id="p1")
    api = tenant_a.client()

    def ids(**params):
        r = api.get(URL, params)
        assert r.status_code == 200
        return {x["id"] for x in r.data["results"]}

    assert ids() == {str(e1.pk), str(e2.pk)}
    assert ids(action="sale.cancel") == {str(e1.pk)}
    assert ids(entity_type="stock") == {str(e2.pk)}
    assert ids(entity_id="p1") == {str(e2.pk)}
    assert ids(user=str(tenant_a.user.id)) == {str(e1.pk)}
    assert ids(user=str(other)) == set()
    today = timezone.localdate()  # le filtre raisonne en heure locale (Africa/Kinshasa), pas en UTC
    assert ids(date_from=today.isoformat(), date_to=today.isoformat()) == {str(e1.pk), str(e2.pk)}
    tomorrow = today + datetime.timedelta(days=1)
    assert ids(date_from=tomorrow.isoformat()) == set()
    assert ids(date_to=(today - datetime.timedelta(days=1)).isoformat()) == set()
    assert api.get(URL, {"date_from": "pas-une-date"}).status_code == 400
    assert api.get(URL, {"user": "pas-un-uuid"}).status_code == 400


def test_pagination_and_ordering(tenant_a):
    for i in range(3):
        make_entry(tenant_a, entity_id=str(i))
    data = tenant_a.client().get(URL).data
    assert data["count"] == 3
    created = [r["created_at"] for r in data["results"]]
    assert created == sorted(created, reverse=True)


# ---------- mixin ----------

@override_settings(ROOT_URLCONF="audit.tests.urls_test")
def test_audited_mixin_create_update_destroy(tenant_a):
    api = tenant_a.client()
    r = api.post("/test/audited-stores/", {"name": "Succursale", "address": "Gombe",
                                          "reason": "ouverture"}, format="json")
    assert r.status_code == 201, r.data
    store_id = r.data["id"]
    r = api.patch(f"/test/audited-stores/{store_id}/", {"name": "Succursale 2"}, format="json")
    assert r.status_code == 200
    assert api.delete(f"/test/audited-stores/{store_id}/").status_code == 204

    with tenant_context(tenant_a.tenant.id):
        entries = list(AuditEntry.objects.filter(entity_id=store_id).order_by("created_at"))
        assert not Store.objects.filter(id=store_id).exists()
    assert [e.action for e in entries] == ["store.create", "store.update", "store.delete"]
    create, update, delete = entries
    assert create.before is None and create.after["name"] == "Succursale"
    assert create.reason == "ouverture"
    assert set(create.after) == {"name", "address", "is_active"}
    assert update.before["name"] == "Succursale" and update.after["name"] == "Succursale 2"
    assert delete.before["name"] == "Succursale 2" and delete.after is None
    assert all(e.user_id == tenant_a.user.id and e.ip_address for e in entries)
    # visible via l'API
    assert api.get(URL, {"entity_type": "store"}).data["count"] == 3


@override_settings(ROOT_URLCONF="audit.tests.urls_test")
def test_audited_mixin_no_entry_on_failed_validation(tenant_a):
    r = tenant_a.client().post("/test/audited-stores/", {}, format="json")
    assert r.status_code == 400
    with tenant_context(tenant_a.tenant.id):
        assert AuditEntry.objects.count() == 0
