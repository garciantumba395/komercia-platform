import time

import pytest

from catalog.models import Category, Product
from catalog.tests.helpers import client_for_role, make_product
from core.tenancy import tenant_context

P = "/api/v1/catalog/products/"
C = "/api/v1/catalog/categories/"
ALL = ["owner", "manager", "cashier", "storekeeper", "accountant"]


def _payload(**kw):
    base = {"name": "Riz 25kg", "sku": "RIZ-25", "price_minor": 45000, "currency": "CDF", "cost_minor": 40000}
    base.update(kw)
    return base


def test_owner_crud_product(tenant_a):
    c = tenant_a.client()
    r = c.post(P, _payload(barcode="123"), format="json")
    assert r.status_code == 201, r.content
    pid = r.json()["id"]
    assert r.json()["cost_minor"] == 40000
    assert c.patch(f"{P}{pid}/", {"price_minor": 46000}, format="json").json()["price_minor"] == 46000
    assert c.delete(f"{P}{pid}/").status_code == 204


def test_service_never_tracks_stock(tenant_a):
    c = tenant_a.client()
    r = c.post(P, _payload(sku="COIF", kind="service", track_stock=True), format="json")
    assert r.status_code == 201
    assert r.json()["track_stock"] is False


def test_sku_and_barcode_unique_per_tenant(tenant_a, tenant_b):
    a, b = tenant_a.client(), tenant_b.client()
    assert a.post(P, _payload(barcode="999"), format="json").status_code == 201
    assert a.post(P, _payload(), format="json").status_code == 400  # SKU
    assert a.post(P, _payload(sku="X", barcode="999"), format="json").status_code == 400
    # deux produits sans code-barres sont autorisés
    assert a.post(P, _payload(sku="Y"), format="json").status_code == 201
    assert a.post(P, _payload(sku="Z"), format="json").status_code == 201
    # le même SKU/code-barres est libre chez un autre tenant
    assert b.post(P, _payload(barcode="999"), format="json").status_code == 201


def test_isolation_between_tenants(tenant_a, tenant_b):
    pa = make_product(tenant_a)
    b = tenant_b.client()
    assert b.get(P).json()["count"] == 0
    assert b.get(f"{P}{pa.id}/").status_code == 404
    assert b.patch(f"{P}{pa.id}/", {"name": "pirate"}, format="json").status_code == 404
    assert b.delete(f"{P}{pa.id}/").status_code == 404
    assert b.get(P + "?q=savon").json()["count"] == 0
    assert b.get(P + "export/").content.decode().strip().count("\n") == 0  # en-tête seul
    with tenant_context(tenant_a.tenant.id):
        cat = Category.objects.create(name="Hygiène")
    assert b.get(C).json() == []
    assert b.get(f"{C}{cat.id}/").status_code == 404
    # catégorie d'un autre tenant refusée à la création d'un produit
    r = b.post(P, _payload(category=str(cat.id)), format="json")
    assert r.status_code == 400


@pytest.mark.parametrize("role", ALL)
def test_read_for_all_roles_and_cost_visibility(tenant_a, role):
    make_product(tenant_a)
    c = client_for_role(tenant_a, role)
    r = c.get(P)
    assert r.status_code == 200
    item = r.json()["results"][0]
    assert ("cost_minor" in item) == (role != "cashier")
    detail = c.get(f"{P}{item['id']}/").json()
    assert ("cost_minor" in detail) == (role != "cashier")


@pytest.mark.parametrize("role", ["cashier", "storekeeper", "accountant"])
def test_write_forbidden_for_other_roles(tenant_a, role):
    p = make_product(tenant_a)
    c = client_for_role(tenant_a, role)
    assert c.post(P, _payload(sku="N"), format="json").status_code == 403
    assert c.patch(f"{P}{p.id}/", {"price_minor": 1}, format="json").status_code == 403
    assert c.delete(f"{P}{p.id}/").status_code == 403
    assert c.post(C, {"name": "X"}, format="json").status_code == 403
    assert c.post(P + "import/", {}, format="multipart").status_code == 403


def test_manager_can_write_and_change_price(tenant_a):
    p = make_product(tenant_a)
    c = client_for_role(tenant_a, "manager")
    assert c.patch(f"{P}{p.id}/", {"price_minor": 2000}, format="json").status_code == 200
    assert c.post(C, {"name": "Épicerie"}, format="json").status_code == 201


def test_unauthenticated_refused(db):
    from rest_framework.test import APIClient

    assert APIClient().get(P).status_code in (401, 403)


def test_category_crud_and_cycle(tenant_a):
    c = tenant_a.client()
    root = c.post(C, {"name": "Boissons"}, format="json").json()
    child = c.post(C, {"name": "Jus", "parent": root["id"]}, format="json").json()
    assert c.patch(f"{C}{root['id']}/", {"parent": child["id"]}, format="json").status_code == 400
    assert c.post(C, {"name": "boissons"}, format="json").status_code == 400  # doublon insensible à la casse
    assert c.delete(f"{C}{root['id']}/").status_code == 204


def test_filters(tenant_a):
    with tenant_context(tenant_a.tenant.id):
        cat = Category.objects.create(name="Beauté")
        Product.objects.create(name="Crème", sku="C1", category=cat)
        Product.objects.create(name="Coiffure", sku="S1", kind="service")
        Product.objects.create(name="Ancien", sku="A1", is_active=False)
    c = tenant_a.client()
    assert c.get(P + f"?category={cat.id}").json()["count"] == 1
    assert c.get(P + "?kind=service").json()["count"] == 1
    assert c.get(P + "?active=false").json()["count"] == 1
    assert c.get(P + "?active=true").json()["count"] == 2
    assert c.get(P + "?category=nope").status_code == 400


def test_search_fields_case_insensitive(tenant_a):
    with tenant_context(tenant_a.tenant.id):
        cat = Category.objects.create(name="Quincaillerie")
        Product.objects.create(name="Marteau", sku="MAR-01", barcode="6001234", category=cat)
        Product.objects.create(name="Tissu", sku="TIS-01")
    c = tenant_a.client()
    count = lambda q: c.get(P, {"q": q}).json()["count"]  # noqa: E731
    assert count("marteau") == 1
    assert count("mar-01") == 1
    assert count("600123") == 1
    assert count("QUINCAILL") == 1
    assert count("inexistant") == 0


def test_search_performance_10k_products(tenant_a):
    with tenant_context(tenant_a.tenant.id):
        Product.objects.bulk_create(
            [
                Product(tenant_id=tenant_a.tenant.id, name=f"Produit {i} article", sku=f"SKU-{i:06d}",
                        barcode=f"600{i:09d}", price_minor=100)
                for i in range(10000)
            ]
        )
    c = tenant_a.client()
    start = time.perf_counter()
    r = c.get(P, {"q": "produit 777"})
    elapsed = time.perf_counter() - start
    assert r.status_code == 200 and r.json()["count"] >= 1
    assert elapsed < 1.0, f"recherche trop lente : {elapsed:.2f}s"
    start = time.perf_counter()
    assert c.get(P, {"q": "SKU-009999"}).json()["count"] == 1
    assert time.perf_counter() - start < 1.0
