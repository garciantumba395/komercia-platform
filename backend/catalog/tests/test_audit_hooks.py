"""Journal d'audit branché sur le catalogue (KOM-027)."""

from unittest import mock

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from audit.models import AuditEntry
from catalog.models import Category, Product
from catalog.tests.helpers import client_for_role, make_product
from core.tenancy import tenant_context

pytestmark = pytest.mark.django_db
P = "/api/v1/catalog/products/"
C = "/api/v1/catalog/categories/"


def entries(handle, action=None):
    with tenant_context(handle.tenant.id):
        qs = AuditEntry.objects.order_by("created_at")
        return list(qs.filter(action=action) if action else qs)


def test_category_crud_is_audited(tenant_a):
    api = tenant_a.client()
    cid = api.post(C, {"name": "Boissons"}, format="json").json()["id"]
    (e,) = entries(tenant_a, "category.create")
    assert (e.entity_type, e.entity_id, e.user_id) == ("category", cid, tenant_a.user.id)
    assert e.after["name"] == "Boissons"
    assert api.patch(f"{C}{cid}/", {"name": "Jus"}, format="json").status_code == 200
    (e,) = entries(tenant_a, "category.update")
    assert e.before["name"] == "Boissons" and e.after["name"] == "Jus"
    assert api.delete(f"{C}{cid}/").status_code == 204
    (e,) = entries(tenant_a, "category.delete")
    assert e.entity_id == cid and e.before["name"] == "Jus" and e.after is None


def test_product_create_update_delete_are_audited(tenant_a):
    api = tenant_a.client()
    r = api.post(P, {"name": "Savon", "sku": "SAV-1", "price_minor": 1500, "currency": "CDF"},
                 format="json")
    assert r.status_code == 201, r.content
    pid = r.json()["id"]
    (e,) = entries(tenant_a, "product.create")
    assert (e.entity_type, e.entity_id, e.user_id) == ("product", pid, tenant_a.user.id)
    assert e.before is None and e.after["sku"] == "SAV-1" and e.after["price_minor"] == 1500

    # modification sans prix : product.update seulement
    assert api.patch(f"{P}{pid}/", {"name": "Savon bleu"}, format="json").status_code == 200
    (e,) = entries(tenant_a, "product.update")
    assert e.before["name"] == "Savon" and e.after["name"] == "Savon bleu"
    assert entries(tenant_a, "price.change") == []

    assert api.delete(f"{P}{pid}/").status_code == 204
    (e,) = entries(tenant_a, "product.delete")
    assert e.entity_id == pid and e.before["name"] == "Savon bleu" and e.after is None


@pytest.mark.parametrize("change", [{"price_minor": 2000}, {"cost_minor": 1200},
                                    {"currency": "USD"}])
def test_price_change_is_audited_in_addition(tenant_a, change):
    p = make_product(tenant_a)
    r = tenant_a.client().patch(f"{P}{p.id}/", change, format="json")
    assert r.status_code == 200, r.content
    (pc,) = entries(tenant_a, "price.change")
    assert pc.entity_type == "product" and pc.entity_id == str(p.id)
    assert pc.user_id == tenant_a.user.id
    assert set(pc.before) == {"price_minor", "cost_minor", "currency"} == set(pc.after)
    key = next(iter(change))
    assert pc.before[key] != pc.after[key] and pc.after[key] == change[key]
    assert len(entries(tenant_a, "product.update")) == 1


def test_price_change_carries_reason(tenant_a):
    p = make_product(tenant_a)
    tenant_a.client().patch(f"{P}{p.id}/", {"price_minor": 1800, "reason": "Hausse fournisseur"},
                            format="json")
    (pc,) = entries(tenant_a, "price.change")
    assert pc.reason == "Hausse fournisseur"


def test_import_is_one_event_with_totals(tenant_a):
    csv_data = b"name,sku,price,currency\nA,S1,10,USD\nB,S2,5,USD\n,S3,1,USD\nC,S1,2,USD\n"
    r = tenant_a.client().post(f"{P}import/", {"file": SimpleUploadedFile("p.csv", csv_data)},
                               format="multipart")
    assert r.status_code == 200, r.content
    assert (r.json()["total"], r.json()["created"], r.json()["failed"]) == (4, 2, 2)
    (e,) = entries(tenant_a)
    assert e.action == "product.import" and e.user_id == tenant_a.user.id
    assert e.after == {"total": 4, "created": 2, "failed": 2}
    with tenant_context(tenant_a.tenant.id):
        assert Product.objects.count() == 2


def test_failed_operations_create_no_entry(tenant_a):
    p = make_product(tenant_a)
    api = tenant_a.client()
    assert api.post(P, {"name": "", "sku": ""}, format="json").status_code == 400
    assert api.patch(f"{P}{p.id}/", {"price_minor": -5}, format="json").status_code == 400
    assert api.post(f"{P}import/", {}, format="multipart").status_code == 400
    bad = SimpleUploadedFile("p.csv", b"foo,bar\n1,2\n")
    assert api.post(f"{P}import/", {"file": bad}, format="multipart").status_code == 400
    assert api.post(C, {"name": ""}, format="json").status_code == 400
    cashier = client_for_role(tenant_a, "cashier")
    assert cashier.patch(f"{P}{p.id}/", {"price_minor": 1}, format="json").status_code == 403
    assert cashier.delete(f"{P}{p.id}/").status_code == 403
    assert cashier.post(C, {"name": "X"}, format="json").status_code == 403
    assert entries(tenant_a) == []


def test_delete_blocked_by_history_creates_no_entry(tenant_a):
    from decimal import Decimal

    from stock.services import apply_movement

    p = make_product(tenant_a)
    with tenant_context(tenant_a.tenant.id):
        apply_movement(p, tenant_a.store, Decimal("3"), "in")
    assert tenant_a.client().delete(f"{P}{p.id}/").status_code == 409
    assert entries(tenant_a, "product.delete") == []


def test_entries_are_isolated_between_tenants(tenant_a, tenant_b):
    tenant_a.client().post(C, {"name": "Cat A"}, format="json")
    tenant_b.client().post(C, {"name": "Cat B"}, format="json")
    assert [e.after["name"] for e in entries(tenant_a)] == ["Cat A"]
    assert [e.after["name"] for e in entries(tenant_b)] == ["Cat B"]
    api = tenant_a.client()
    assert {r["after"]["name"] for r in api.get("/api/v1/audit/entries/").data["results"]} == {"Cat A"}


def test_audit_failure_rolls_back_operation(tenant_a):
    p = make_product(tenant_a)
    api = tenant_a.client()
    with mock.patch("audit.services.log", side_effect=RuntimeError("journal indisponible")):
        with pytest.raises(RuntimeError):
            api.post(P, {"name": "Fantôme", "sku": "F-1"}, format="json")
        with pytest.raises(RuntimeError):
            api.patch(f"{P}{p.id}/", {"price_minor": 9999}, format="json")
        with pytest.raises(RuntimeError):
            api.delete(f"{P}{p.id}/")
        with pytest.raises(RuntimeError):
            api.post(C, {"name": "Fantôme"}, format="json")
        csv_data = b"name,sku\nA,IMP-1\n"
        with pytest.raises(RuntimeError):
            api.post(f"{P}import/", {"file": SimpleUploadedFile("p.csv", csv_data)},
                     format="multipart")
    with tenant_context(tenant_a.tenant.id):
        assert not Product.objects.filter(sku__in=["F-1", "IMP-1"]).exists()
        assert Product.objects.get(pk=p.pk).price_minor == 1500
        assert not Category.objects.filter(name="Fantôme").exists()
