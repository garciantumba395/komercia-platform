"""Tests du `pull` : curseur sans oubli ni doublon, désactivés, coût d'achat, taux, gzip, isolation."""

import gzip
import json
from datetime import timedelta

from django.utils import timezone

from catalog.models import Category, Product
from core.tenancy import tenant_context
from customers import services as credit
from customers.models import Customer
from sync.tests.helpers import (
    PULL,
    STATUS,
    body,
    cash,
    client_for_role,
    make_customer,
    make_product,
    make_rate,
    op,
    pull,
    push,
    results,
    sale_op,
    stock,
)

STREAMS = ("stores", "categories", "products", "customers", "stock_levels")


def collect(client, limit, **params):
    """Parcourt toutes les pages ; renvoie ({flux: [ids]}, nombre de pages, dernier curseur)."""
    ids = {s: [] for s in STREAMS}
    cursor, pages = None, 0
    while True:
        query = {"limit": limit, **params}
        if cursor:
            query["since"] = cursor
        data = body(pull(client, **query))
        pages += 1
        for stream in STREAMS:
            ids[stream] += [row["id"] for row in data["changes"][stream]]
        cursor = data["cursor"]
        if not data["has_more"]:
            return ids, pages, cursor
        assert pages < 200


def test_full_cursor_walk_with_identical_updated_at(tenant_a):
    with tenant_context(tenant_a.tenant.id):
        cat = Category.objects.create(name="Boissons")
        Product.objects.bulk_create([
            Product(tenant_id=tenant_a.tenant.id, name=f"P{i:02d}", sku=f"SKU{i:02d}", category=cat,
                    price_minor=100 + i) for i in range(25)
        ])
        same = timezone.now() - timedelta(hours=1)
        Product.objects.update(updated_at=same)  # 25 lignes avec EXACTEMENT le même updated_at
        expected = {str(i) for i in Product.objects.values_list("id", flat=True)}
    c = tenant_a.client()
    ids, pages, cursor = collect(c, limit=7)
    assert pages >= 4
    assert len(ids["products"]) == 25  # aucun doublon
    assert set(ids["products"]) == expected  # aucun oubli
    assert len(ids["categories"]) == 1
    # Un appel avec le curseur final ne renvoie plus rien.
    data = body(pull(c, since=cursor))
    assert data["has_more"] is False
    assert all(data["changes"][s] == [] for s in STREAMS)
    # Une modification ultérieure : seule la ligne modifiée revient.
    with tenant_context(tenant_a.tenant.id):
        target = Product.objects.get(sku="SKU03")
        target.price_minor = 999
        target.save()
    data = body(pull(c, since=cursor))
    assert [r["sku"] for r in data["changes"]["products"]] == ["SKU03"]
    assert data["changes"]["products"][0]["price_minor"] == 999


def test_pagination_with_ties_spanning_page_boundary(tenant_a):
    """Cas piège : plusieurs pages tombent au milieu d'un groupe de même updated_at."""
    with tenant_context(tenant_a.tenant.id):
        Product.objects.bulk_create([
            Product(tenant_id=tenant_a.tenant.id, name=f"Q{i}", sku=f"Q{i}") for i in range(10)])
        base = timezone.now() - timedelta(hours=2)
        for i, product in enumerate(Product.objects.order_by("sku")):
            Product.objects.filter(pk=product.pk).update(updated_at=base + timedelta(seconds=i // 4))
    for limit in (1, 2, 3, 4, 5, 9, 10, 11):
        ids, _, _ = collect(tenant_a.client(), limit=limit)
        assert len(ids["products"]) == 10 and len(set(ids["products"])) == 10, limit


def test_first_pull_returns_everything_and_deactivated_products(tenant_a):
    live = make_product(tenant_a, sku="A", name="Actif")
    dead = make_product(tenant_a, sku="B", name="Désactivé")
    c = tenant_a.client()
    first = body(pull(c))
    assert {p["sku"] for p in first["changes"]["products"]} == {"A", "B"}
    assert [s["id"] for s in first["changes"]["stores"]] == [str(tenant_a.store.id)]
    with tenant_context(tenant_a.tenant.id):
        dead.is_active = False
        dead.save()
    second = body(pull(c, since=first["cursor"]))
    rows = second["changes"]["products"]
    assert [(r["sku"], r["is_active"]) for r in rows] == [("B", False)]
    assert live.id  # le produit actif n'est pas renvoyé sans modification


def test_cost_hidden_for_cashier_visible_for_owner_and_manager(tenant_a):
    make_product(tenant_a, sku="A", cost_minor=700)
    owner = body(pull(tenant_a.client()))["changes"]["products"][0]
    manager = body(pull(client_for_role(tenant_a, "manager")))["changes"]["products"][0]
    cashier_resp = pull(client_for_role(tenant_a, "cashier"))
    cashier = body(cashier_resp)["changes"]["products"][0]
    assert owner["cost_minor"] == 700 and manager["cost_minor"] == 700
    assert "cost_minor" not in cashier
    assert b"700" not in cashier_resp.content.replace(b"price", b"")  # nulle part dans la réponse


def test_current_exchange_rate_and_null_when_missing(tenant_a):
    c = tenant_a.client()
    assert body(pull(c))["changes"]["exchange_rate"] is None
    make_rate(tenant_a, "2800")
    newer = make_rate(tenant_a, "2850.5")
    rate = body(pull(c))["changes"]["exchange_rate"]
    assert rate["rate_cdf_per_usd"] == "2850.5000"
    assert rate["id"] == str(newer.id)
    assert rate["store"] is None
    # Le taux courant est renvoyé à chaque appel, même sans changement.
    again = body(pull(c, since=body(pull(c))["cursor"]))
    assert again["changes"]["exchange_rate"]["rate_cdf_per_usd"] == "2850.5000"


def test_stock_levels_scoped_to_store_and_follow_sales(tenant_a):
    other = client_for_role(tenant_a, "manager")  # noqa: F841
    from accounts.models import Store

    with tenant_context(tenant_a.tenant.id):
        store2 = Store.objects.create(name="Succursale")
    p = make_product(tenant_a, sku="A", price_minor=1000)
    stock(tenant_a, p, 10)
    from stock.services import apply_movement

    with tenant_context(tenant_a.tenant.id):
        apply_movement(p, store2, 4, "in")
    c = tenant_a.client()
    scoped = body(pull(c, store=str(tenant_a.store.id)))
    levels = scoped["changes"]["stock_levels"]
    assert [(lv["store"], lv["quantity"]) for lv in levels] == [(str(tenant_a.store.id), "10.000")]
    everything = body(pull(c))["changes"]["stock_levels"]
    assert len(everything) == 2
    # Une vente rejouée modifie le niveau : il revient dans le delta suivant.
    results(push(c, [sale_op(tenant_a, p, 3)]))
    delta = body(pull(c, store=str(tenant_a.store.id), since=scoped["cursor"]))
    assert [lv["quantity"] for lv in delta["changes"]["stock_levels"]] == ["7.000"]


def test_customers_with_balances_and_balance_change_without_edit(tenant_a):
    cust = make_customer(tenant_a, "Marie", limit=100_000)
    c = tenant_a.client()
    first = body(pull(c))
    row = first["changes"]["customers"][0]
    assert row["balance"] == {} and row["credit_limit_minor"] == 100_000
    # Une vente à crédit change le solde sans modifier la fiche : le client doit revenir.
    p = make_product(tenant_a, sku="A", price_minor=1000)
    stock(tenant_a, p, 5)
    results(push(c, [sale_op(tenant_a, p, 2, customer=str(cust.id), payments=[cash(500)],
                             credit_remaining=True)]))
    second = body(pull(c, since=first["cursor"]))
    assert [(r["name"], r["balance"]) for r in second["changes"]["customers"]] == [("Marie", {"CDF": 1500})]
    # Un paiement de la dette aussi.
    with tenant_context(tenant_a.tenant.id):
        credit.register_payment(Customer.objects.get(pk=cust.pk), 500, "CDF")
    third = body(pull(c, since=second["cursor"]))
    assert third["changes"]["customers"][0]["balance"] == {"CDF": 1000}
    # Et plus rien ensuite.
    assert body(pull(c, since=third["cursor"]))["changes"]["customers"] == []


def test_inactive_customer_is_propagated(tenant_a):
    cust = make_customer(tenant_a, "Partie")
    c = tenant_a.client()
    cursor = body(pull(c))["cursor"]
    with tenant_context(tenant_a.tenant.id):
        cust.is_active = False
        cust.save()
    rows = body(pull(c, since=cursor))["changes"]["customers"]
    assert [(r["id"], r["is_active"]) for r in rows] == [(str(cust.id), False)]


def test_customer_pagination_with_balance_ties(tenant_a):
    with tenant_context(tenant_a.tenant.id):
        Customer.objects.bulk_create(
            [Customer(tenant_id=tenant_a.tenant.id, name=f"C{i}") for i in range(12)])
        Customer.objects.update(updated_at=timezone.now() - timedelta(days=1))
    ids, _, _ = collect(tenant_a.client(), limit=5)
    assert len(ids["customers"]) == 12 == len(set(ids["customers"]))


def test_gzip_when_accepted_and_plain_otherwise(tenant_a):
    for i in range(30):
        make_product(tenant_a, sku=f"G{i}", name=f"Produit {i}")
    c = tenant_a.client()
    plain = c.get(PULL)
    assert plain.headers.get("Content-Encoding") is None
    zipped = c.get(PULL, HTTP_ACCEPT_ENCODING="gzip, deflate")
    assert zipped.headers["Content-Encoding"] == "gzip"
    assert "Accept-Encoding" in zipped.headers["Vary"]
    assert len(zipped.content) < len(plain.content)
    assert len(json.loads(gzip.decompress(zipped.content))["changes"]["products"]) == 30
    refused = c.get(PULL, HTTP_ACCEPT_ENCODING="gzip;q=0")
    assert refused.headers.get("Content-Encoding") is None


def test_pull_parameter_validation(tenant_a):
    c = tenant_a.client()
    assert pull(c, since="n'importe quoi").status_code == 400
    assert pull(c, since="n'importe quoi").json()["code"] == "invalid_cursor"
    assert pull(c, limit="0").status_code == 400
    assert pull(c, limit="1001").status_code == 400
    assert pull(c, limit="abc").status_code == 400
    assert pull(c, store="pas-un-uuid").status_code == 400
    assert pull(c, store="00000000-0000-0000-0000-000000000000").status_code == 404
    assert pull(c, limit="1000").status_code == 200


def test_cursor_is_bound_to_store(tenant_a):
    c = tenant_a.client()
    cursor = body(pull(c, store=str(tenant_a.store.id)))["cursor"]
    assert pull(c, since=cursor).status_code == 400  # curseur émis pour une boutique, réutilisé sans
    assert pull(c, since=cursor, store=str(tenant_a.store.id)).status_code == 200


def test_pull_lag_hides_very_recent_rows(tenant_a, settings):
    settings.SYNC_PULL_LAG_SECONDS = 3600
    make_product(tenant_a, sku="A")
    data = body(pull(tenant_a.client()))
    assert data["changes"]["products"] == []  # trop récent : sera servi plus tard, jamais perdu
    settings.SYNC_PULL_LAG_SECONDS = 0
    assert len(body(pull(tenant_a.client(), since=data["cursor"]))["changes"]["products"]) == 1


def test_pull_isolation_between_tenants(tenant_a, tenant_b):
    make_product(tenant_a, sku="A", name="Secret A")
    make_customer(tenant_a, "Client A")
    make_rate(tenant_a, "2800")
    data = pull(tenant_b.client())
    assert data.status_code == 200
    changes = body(data)["changes"]
    assert changes["products"] == [] and changes["customers"] == []
    assert changes["exchange_rate"] is None
    assert [s["id"] for s in changes["stores"]] == [str(tenant_b.store.id)]
    assert b"Secret A" not in data.content
    # Le magasin de A est introuvable pour B.
    assert pull(tenant_b.client(), store=str(tenant_a.store.id)).status_code == 404


def test_pull_and_status_permissions_by_role(tenant_a):
    expected = {"owner": 200, "manager": 200, "cashier": 200, "storekeeper": 403, "accountant": 403}
    for role, code in expected.items():
        client = client_for_role(tenant_a, role)
        assert client.get(PULL).status_code == code, role
        assert client.get(STATUS).status_code == code, role


def test_status_endpoint(tenant_a):
    data = tenant_a.client().get(STATUS).json()
    assert data["min_app_version"] == "0.1.0"
    assert data["max_offline_days"] == 7
    assert "server_time" in data


def test_pull_requires_authentication(db):
    from rest_framework.test import APIClient

    assert APIClient().get(PULL).status_code == 401
    assert APIClient().get(STATUS).status_code == 401


def test_device_can_build_catalog_from_pull_then_push(tenant_a):
    """Parcours de bout en bout : l'appareil tire le catalogue, vend hors ligne, pousse, retire le delta."""
    p = make_product(tenant_a, sku="A", price_minor=2000)
    stock(tenant_a, p, 5)
    c = tenant_a.client()
    first = body(pull(c, store=str(tenant_a.store.id)))
    product = first["changes"]["products"][0]
    create_customer = op("customer.create", {"id": "7b0f4a1e-0000-4000-8000-000000000001", "name": "Nouveau"})
    sale = sale_op(tenant_a, p, 1)
    sale["payload"]["items"][0]["product"] = product["id"]
    res = results(push(c, [create_customer, sale]))
    assert [r["status"] for r in res] == ["applied", "applied"]
    second = body(pull(c, store=str(tenant_a.store.id), since=first["cursor"]))
    assert [r["name"] for r in second["changes"]["customers"]] == ["Nouveau"]
    assert [lv["quantity"] for lv in second["changes"]["stock_levels"]] == ["4.000"]
