"""Création de vente : totaux, devises, paiements, crédit, stock, idempotence, numérotation, remises."""

import uuid
from decimal import Decimal

import pytest

from audit.models import AuditEntry
from core.tenancy import tenant_context
from customers import services as credit
from sales.models import Payment, Sale, SaleItem
from sales.tests.helpers import (
    BASE,
    SESSIONS,
    cash,
    client_for_role,
    make_customer,
    make_product,
    make_rate,
    make_store,
    sale_body,
)
from stock.models import StockMovement
from stock.services import apply_movement, get_quantity

pytestmark = pytest.mark.django_db


def give_stock(handle, product, qty, store=None):
    with tenant_context(handle.tenant.id):
        apply_movement(product, store or handle.store, qty, "in")


def stock_of(handle, product, store=None):
    with tenant_context(handle.tenant.id):
        return get_quantity(product, store or handle.store)


def post(client, body):
    r = client.post(BASE, body, format="json")
    if r["Content-Type"].startswith("application/json"):
        r.data = r.json()  # valeurs telles que le client les reçoit (UUID en chaînes)
    return r


def count_sales(handle):
    with tenant_context(handle.tenant.id):
        return Sale.objects.count()


# ----------------------------------------------------------------------------- ventes simples


def test_simple_sale(tenant_a):
    make_rate(tenant_a)
    p = make_product(tenant_a, price_minor=1500)
    give_stock(tenant_a, p, 10)
    r = post(tenant_a.client(), sale_body(tenant_a, [(p, 2)], [cash(3000)]))
    assert r.status_code == 201, r.data
    d = r.data
    assert d["number"] == "B01-000001"
    assert d["status"] == "completed" and d["currency"] == "CDF"
    assert d["subtotal_minor"] == d["total_minor"] == 3000
    assert d["discount_minor"] == 0 and d["tax_minor"] == 0
    assert d["paid_minor"] == 3000 and d["change_minor"] == 0 and d["credit_minor"] == 0
    assert d["rate_cdf_per_usd"] == "2800.0000" and d["rate_id"]
    assert d["items"] == [{"product": str(p.id), "name": "Savon", "quantity": "2",
                           "unit_price_minor": 1500, "discount_minor": 0, "line_total_minor": 3000}]
    assert d["payments"] == [{"method": "cash", "amount_minor": 3000, "currency": "CDF"}]
    assert d["cashier"] == str(tenant_a.user.id) and d["store"] == str(tenant_a.store.id)
    assert d["customer"] is None and d["cash_session"] is None
    assert stock_of(tenant_a, p) == Decimal("8")


def test_all_amounts_are_integers(tenant_a):
    make_rate(tenant_a)
    p = make_product(tenant_a, price_minor=1500)
    d = post(tenant_a.client(), sale_body(tenant_a, [(p, "1.5", {"discount_minor": 50})],
                                          [cash(5000)], discount_minor=20)).data
    for key in ("subtotal_minor", "discount_minor", "tax_minor", "total_minor", "paid_minor",
                "change_minor", "credit_minor"):
        assert type(d[key]) is int, key
    for item in d["items"]:
        assert type(item["unit_price_minor"]) is int and type(item["line_total_minor"]) is int
        assert type(item["discount_minor"]) is int
    assert type(d["payments"][0]["amount_minor"]) is int


def test_decimal_quantity_rounds_half_up(tenant_a):
    p = make_product(tenant_a, price_minor=1001, track_stock=False)
    # 1,5 x 1001 = 1501,5 -> 1502
    d = post(tenant_a.client(), sale_body(tenant_a, [(p, "1.5")], [cash(1502)])).data
    assert d["items"][0]["line_total_minor"] == 1502 and d["items"][0]["quantity"] == "1.5"
    assert d["total_minor"] == 1502


def test_line_and_global_discounts(tenant_a):
    p = make_product(tenant_a, price_minor=1000, track_stock=False)
    q = make_product(tenant_a, sku="B2", name="Eau", price_minor=500, track_stock=False)
    body = sale_body(tenant_a, [(p, 3, {"discount_minor": 500}), (q, 2)], [cash(3250)],
                     discount_minor=250)
    d = post(tenant_a.client(), body).data
    # lignes : 3000-500 = 2500 ; 1000 -> sous-total 3500 ; remise globale 250 -> 3250
    assert [i["line_total_minor"] for i in d["items"]] == [2500, 1000]
    assert d["subtotal_minor"] == 3500 and d["discount_minor"] == 250 and d["total_minor"] == 3250
    assert d["paid_minor"] == 3250 and d["change_minor"] == 0
    # payé 3000 < 3250 sans crédit : refus
    short = sale_body(tenant_a, [(p, 3, {"discount_minor": 500}), (q, 2)], [cash(3000)], discount_minor=250)
    assert post(tenant_a.client(), short).status_code == 400


def test_discount_larger_than_amount_refused(tenant_a):
    p = make_product(tenant_a, price_minor=1000, track_stock=False)
    c = tenant_a.client()
    r = post(c, sale_body(tenant_a, [(p, 1, {"discount_minor": 1001})], [cash(1000)]))
    assert r.status_code == 400
    r = post(c, sale_body(tenant_a, [(p, 1)], [cash(1000)], discount_minor=1001))
    assert r.status_code == 400
    r = post(c, sale_body(tenant_a, [(p, 1)], [], discount_minor=1000))  # 100 % : total 0
    assert r.status_code == 201 and r.data["total_minor"] == 0


def test_validation_errors(tenant_a):
    p = make_product(tenant_a, price_minor=1000, track_stock=False)
    c = tenant_a.client()
    assert post(c, {**sale_body(tenant_a, [(p, 1)]), "items": []}).status_code == 400
    assert post(c, sale_body(tenant_a, [(p, 0)])).status_code == 400
    assert post(c, sale_body(tenant_a, [(p, 1)], currency="EUR")).status_code == 400
    assert post(c, sale_body(tenant_a, [(p, 1)], [cash(-5)])).status_code == 400
    body = sale_body(tenant_a, [(p, 1)])
    del body["client_id"]
    assert post(c, body).status_code == 400


def test_non_cash_payment_refused(tenant_a):
    p = make_product(tenant_a, price_minor=1000, track_stock=False)
    pay = {"method": "mobile_money", "amount_minor": 1000, "currency": "CDF"}
    r = post(tenant_a.client(), sale_body(tenant_a, [(p, 1)], [pay]))
    assert r.status_code == 400 and "M2" in str(r.data)


def test_underpayment_refused_without_credit(tenant_a):
    p = make_product(tenant_a, price_minor=1000, track_stock=False)
    r = post(tenant_a.client(), sale_body(tenant_a, [(p, 1)], [cash(900)]))
    assert r.status_code == 400 and "insuffisant" in r.data["detail"]
    assert count_sales(tenant_a) == 0


# ----------------------------------------------------------------------------- devises


def test_catalog_price_converted_cdf_to_usd(tenant_a):
    make_rate(tenant_a, "2800")
    p = make_product(tenant_a, price_minor=2800, currency="CDF", track_stock=False)
    d = post(tenant_a.client(), sale_body(tenant_a, [(p, 2)], [cash(200, "USD")], currency="USD")).data
    assert d["currency"] == "USD"
    assert d["items"][0]["unit_price_minor"] == 100  # 2800 CDF = 1,00 USD
    assert d["total_minor"] == 200 and d["paid_minor"] == 200


def test_catalog_price_converted_usd_to_cdf(tenant_a):
    make_rate(tenant_a, "2800")
    p = make_product(tenant_a, price_minor=1050, currency="USD", track_stock=False)  # 10,50 USD
    d = post(tenant_a.client(), sale_body(tenant_a, [(p, 1)], [cash(29400)], currency="CDF")).data
    assert d["items"][0]["unit_price_minor"] == 29400 and d["total_minor"] == 29400


def test_default_currency_is_tenant_base_currency(tenant_a):
    p = make_product(tenant_a, price_minor=1000, track_stock=False)
    d = post(tenant_a.client(), sale_body(tenant_a, [(p, 1)], [cash(1000)])).data
    assert d["currency"] == "CDF"


def test_mixed_payment_with_change_in_sale_currency(tenant_a):
    make_rate(tenant_a, "2800")
    p = make_product(tenant_a, price_minor=10_000, track_stock=False)
    # 2 USD (= 5600 CDF) + 5000 CDF = 10 600 -> monnaie 600 CDF
    d = post(tenant_a.client(), sale_body(tenant_a, [(p, 1)], [cash(200, "USD"), cash(5000)])).data
    assert d["paid_minor"] == 10_600 and d["change_minor"] == 600 and d["change_currency"] == "CDF"
    assert d["credit_minor"] == 0


def test_change_in_other_currency(tenant_a):
    make_rate(tenant_a, "2800")
    p = make_product(tenant_a, price_minor=10_000, track_stock=False)
    d = post(tenant_a.client(), sale_body(tenant_a, [(p, 1)], [cash(200, "USD"), cash(5000)],
                                          change_currency="USD")).data
    assert d["change_minor"] == 21 and d["change_currency"] == "USD"  # 600 CDF = 0,214 USD -> 21 c
    assert d["paid_minor"] == 10_600


def test_usd_payment_on_cdf_sale_requires_rate(tenant_a):
    p = make_product(tenant_a, price_minor=2800, track_stock=False)
    r = post(tenant_a.client(), sale_body(tenant_a, [(p, 1)], [cash(100, "USD")]))
    assert r.status_code == 400 and "taux" in r.data["detail"]
    assert count_sales(tenant_a) == 0


def test_cross_currency_product_requires_rate(tenant_a):
    p = make_product(tenant_a, price_minor=100, currency="USD", track_stock=False)
    r = post(tenant_a.client(), sale_body(tenant_a, [(p, 1)], [cash(1000)], currency="CDF"))
    assert r.status_code == 400 and "taux" in r.data["detail"]


def test_sale_without_rate_when_no_conversion_needed(tenant_a):
    p = make_product(tenant_a, price_minor=1000, track_stock=False)
    d = post(tenant_a.client(), sale_body(tenant_a, [(p, 1)], [cash(1000)])).data
    assert d["rate_cdf_per_usd"] is None and d["rate_id"] is None


def test_rate_is_frozen_on_sale(tenant_a):
    rate = make_rate(tenant_a, "2800")
    p = make_product(tenant_a, price_minor=1000, track_stock=False)
    sale_id = post(tenant_a.client(), sale_body(tenant_a, [(p, 1)], [cash(1000)])).data["id"]
    make_rate(tenant_a, "3000")
    d = tenant_a.client().get(f"{BASE}{sale_id}/").data
    assert d["rate_cdf_per_usd"] == "2800.0000" and d["rate_id"] == str(rate.id)


# ----------------------------------------------------------------------------- crédit


def test_credit_sale_within_limit(tenant_a):
    cust = make_customer(tenant_a, limit=10_000)
    p = make_product(tenant_a, price_minor=6000, track_stock=False)
    body = sale_body(tenant_a, [(p, 1)], [cash(2000)], customer=str(cust.id), credit_remaining=True)
    r = post(tenant_a.client(), body)
    assert r.status_code == 201, r.data
    assert r.data["paid_minor"] == 2000 and r.data["credit_minor"] == 4000
    with tenant_context(tenant_a.tenant.id):
        assert credit.get_balance(cust) == {"CDF": 4000}
        entry = cust.credit_entries.get()
        assert str(entry.sale_id) == r.data["id"]


def test_full_credit_without_payment(tenant_a):
    cust = make_customer(tenant_a)
    p = make_product(tenant_a, price_minor=6000, track_stock=False)
    r = post(tenant_a.client(), sale_body(tenant_a, [(p, 1)], customer=str(cust.id),
                                          credit_remaining=True))
    assert r.status_code == 201 and r.data["credit_minor"] == 6000 and r.data["paid_minor"] == 0


def test_credit_limit_exceeded_refused_and_rolled_back(tenant_a):
    cust = make_customer(tenant_a, limit=3000)
    p = make_product(tenant_a, price_minor=6000)
    give_stock(tenant_a, p, 5)
    body = sale_body(tenant_a, [(p, 1)], customer=str(cust.id), credit_remaining=True)
    r = post(tenant_a.client(), body)
    assert r.status_code == 400 and "limite" in r.data["detail"].lower()
    assert count_sales(tenant_a) == 0
    assert stock_of(tenant_a, p) == 5  # le stock n'a pas bougé
    with tenant_context(tenant_a.tenant.id):
        assert credit.get_balance(cust) == {}
    # le numéro n'a pas été consommé
    ok = post(tenant_a.client(), sale_body(tenant_a, [(p, 1)], [cash(6000)]))
    assert ok.data["number"] == "B01-000001"


def test_credit_limit_override_owner_and_manager_only(tenant_a):
    cust = make_customer(tenant_a, limit=3000)
    p = make_product(tenant_a, price_minor=6000, track_stock=False)

    def body():
        return sale_body(tenant_a, [(p, 1)], customer=str(cust.id), credit_remaining=True,
                         override_credit_limit=True)

    # propriétaire : override accepté
    assert post(tenant_a.client(), body()).status_code == 201
    # gérant : accepté aussi
    manager = client_for_role(tenant_a, "manager")
    assert post(manager, body()).status_code == 201
    with tenant_context(tenant_a.tenant.id):
        assert credit.get_balance(cust) == {"CDF": 12_000}


def test_cashier_cannot_override_credit_limit(tenant_a):
    cust = make_customer(tenant_a, limit=3000)
    p = make_product(tenant_a, price_minor=6000, track_stock=False)
    cashier = client_for_role(tenant_a, "cashier")
    assert cashier.post(SESSIONS + "open/", {"store": str(tenant_a.store.id)}, format="json").status_code == 201  # noqa: E501
    r = post(cashier, sale_body(tenant_a, [(p, 1)], customer=str(cust.id), credit_remaining=True,
                                override_credit_limit=True))
    assert r.status_code == 400


def test_credit_without_customer_refused(tenant_a):
    p = make_product(tenant_a, price_minor=6000, track_stock=False)
    r = post(tenant_a.client(), sale_body(tenant_a, [(p, 1)], [cash(1000)], credit_remaining=True))
    assert r.status_code == 400 and "client" in r.data["detail"].lower()


def test_credit_remaining_flag_ignored_when_fully_paid(tenant_a):
    cust = make_customer(tenant_a)
    p = make_product(tenant_a, price_minor=1000, track_stock=False)
    r = post(tenant_a.client(), sale_body(tenant_a, [(p, 1)], [cash(1000)], customer=str(cust.id),
                                          credit_remaining=True))
    assert r.status_code == 201 and r.data["credit_minor"] == 0
    with tenant_context(tenant_a.tenant.id):
        assert credit.get_balance(cust) == {}


def test_credit_in_usd_sale_with_cdf_limit_uses_equivalent(tenant_a):
    make_rate(tenant_a, "2800")
    cust = make_customer(tenant_a, limit=100_000, ccy="CDF")
    p = make_product(tenant_a, price_minor=5000, currency="USD", track_stock=False)  # 50 USD
    # 50 USD = 140 000 CDF > 100 000 -> refusé
    r = post(tenant_a.client(), sale_body(tenant_a, [(p, 1)], currency="USD", customer=str(cust.id),
                                          credit_remaining=True))
    assert r.status_code == 400
    # 30 USD = 84 000 CDF <= 100 000 -> accepté ; dette en USD
    r = post(tenant_a.client(), sale_body(tenant_a, [(p, 1)], [cash(2000, "USD")], currency="USD",
                                          customer=str(cust.id), credit_remaining=True))
    assert r.status_code == 201 and r.data["credit_minor"] == 3000
    with tenant_context(tenant_a.tenant.id):
        assert credit.get_balance(cust) == {"USD": 3000}


# ----------------------------------------------------------------------------- stock


def test_sale_accepted_with_insufficient_stock(tenant_a):
    p = make_product(tenant_a, price_minor=1000)
    give_stock(tenant_a, p, 1)
    r = post(tenant_a.client(), sale_body(tenant_a, [(p, 3)], [cash(3000)]))
    assert r.status_code == 201
    assert stock_of(tenant_a, p) == Decimal("-2")
    with tenant_context(tenant_a.tenant.id):
        mv = StockMovement.objects.get(kind="sale")
        assert mv.quantity == Decimal("-3") and mv.reference == r.data["number"]


def test_service_has_no_stock_movement(tenant_a):
    svc = make_product(tenant_a, sku="SRV", name="Coiffure", kind="service", price_minor=5000)
    r = post(tenant_a.client(), sale_body(tenant_a, [(svc, 1)], [cash(5000)]))
    assert r.status_code == 201
    with tenant_context(tenant_a.tenant.id):
        assert StockMovement.objects.count() == 0


def test_inactive_product_refused(tenant_a):
    p = make_product(tenant_a, price_minor=1000, is_active=False, track_stock=False)
    r = post(tenant_a.client(), sale_body(tenant_a, [(p, 1)], [cash(1000)]))
    assert r.status_code == 400 and "inactif" in r.data["detail"]


def test_same_product_on_two_lines(tenant_a):
    p = make_product(tenant_a, price_minor=1000)
    give_stock(tenant_a, p, 10)
    r = post(tenant_a.client(), sale_body(tenant_a, [(p, 1), (p, 2)], [cash(3000)]))
    assert r.status_code == 201 and r.data["total_minor"] == 3000
    assert stock_of(tenant_a, p) == Decimal("7")


# ----------------------------------------------------------------------------- idempotence


def test_same_client_id_twice_creates_one_sale(tenant_a):
    p = make_product(tenant_a, price_minor=1000)
    give_stock(tenant_a, p, 10)
    body = sale_body(tenant_a, [(p, 2)], [cash(2000)])
    c = tenant_a.client()
    first = post(c, body)
    second = post(c, body)
    assert first.status_code == 201 and second.status_code == 200
    assert first.data == second.data
    assert count_sales(tenant_a) == 1
    assert stock_of(tenant_a, p) == Decimal("8")
    with tenant_context(tenant_a.tenant.id):
        assert StockMovement.objects.filter(kind="sale").count() == 1


def test_replay_returns_existing_even_if_context_changed(tenant_a):
    """Le rejeu d'une vente déjà enregistrée n'est pas re-validé (stock, produit désactivé...)."""
    p = make_product(tenant_a, price_minor=1000, track_stock=False)
    body = sale_body(tenant_a, [(p, 1)], [cash(1000)])
    c = tenant_a.client()
    assert post(c, body).status_code == 201
    p.is_active = False
    with tenant_context(tenant_a.tenant.id):
        p.save()
    assert post(c, body).status_code == 200


def test_same_client_id_different_content_is_409(tenant_a):
    p = make_product(tenant_a, price_minor=1000, track_stock=False)
    body = sale_body(tenant_a, [(p, 1)], [cash(1000)])
    c = tenant_a.client()
    assert post(c, body).status_code == 201
    other = {**body, "items": [{"product": str(p.id), "quantity": "2"}], "payments": [cash(2000)]}
    r = post(c, other)
    assert r.status_code == 409
    assert count_sales(tenant_a) == 1


def test_replay_ignores_device_time_and_flags(tenant_a):
    p = make_product(tenant_a, price_minor=1000, track_stock=False)
    body = sale_body(tenant_a, [(p, 1)], [cash(1000)])
    c = tenant_a.client()
    post(c, body)
    r = post(c, {**body, "device_time": "2026-11-17T10:00:00Z", "override_credit_limit": True})
    assert r.status_code == 200


def test_same_client_id_in_other_tenant_is_independent(tenant_a, tenant_b):
    pa = make_product(tenant_a, price_minor=1000, track_stock=False)
    pb = make_product(tenant_b, price_minor=1000, track_stock=False)
    cid = str(uuid.uuid4())
    ra = post(tenant_a.client(), {**sale_body(tenant_a, [(pa, 1)], [cash(1000)]), "client_id": cid})
    rb = post(tenant_b.client(), {**sale_body(tenant_b, [(pb, 1)], [cash(1000)]), "client_id": cid})
    assert ra.status_code == 201 and rb.status_code == 201 and ra.data["id"] != rb.data["id"]


# ----------------------------------------------------------------------------- numérotation


def test_numbering_sequential_without_gap(tenant_a):
    p = make_product(tenant_a, price_minor=1000, track_stock=False)
    c = tenant_a.client()
    numbers = [post(c, sale_body(tenant_a, [(p, 1)], [cash(1000)])).data["number"] for _ in range(3)]
    assert post(c, sale_body(tenant_a, [(p, 1)], [cash(1)])).status_code == 400  # échec : pas de trou
    numbers.append(post(c, sale_body(tenant_a, [(p, 1)], [cash(1000)])).data["number"])
    assert numbers == ["B01-000001", "B01-000002", "B01-000003", "B01-000004"]


def test_numbering_is_per_store(tenant_a):
    p = make_product(tenant_a, price_minor=1000, track_stock=False)
    store2 = make_store(tenant_a)
    c = tenant_a.client()
    n1 = post(c, sale_body(tenant_a, [(p, 1)], [cash(1000)])).data["number"]
    body2 = {**sale_body(tenant_a, [(p, 1)], [cash(1000)]), "store": str(store2.id)}
    n2 = post(c, body2).data["number"]
    n3 = post(c, sale_body(tenant_a, [(p, 1)], [cash(1000)])).data["number"]
    assert (n1, n2, n3) == ("B01-000001", "B02-000001", "B01-000002")


def test_numbering_independent_between_tenants(tenant_a, tenant_b):
    pa = make_product(tenant_a, price_minor=1000, track_stock=False)
    pb = make_product(tenant_b, price_minor=1000, track_stock=False)
    na = post(tenant_a.client(), sale_body(tenant_a, [(pa, 1)], [cash(1000)])).data["number"]
    nb = post(tenant_b.client(), sale_body(tenant_b, [(pb, 1)], [cash(1000)])).data["number"]
    assert na == nb == "B01-000001"


# ----------------------------------------------------------------------------- remises et prix


def test_cashier_discount_cap(tenant_a):
    p = make_product(tenant_a, price_minor=1000, track_stock=False)
    cashier = client_for_role(tenant_a, "cashier")
    cashier.post(SESSIONS + "open/", {"store": str(tenant_a.store.id)}, format="json")
    # 10 % exactement de 10 000 : accepté
    ok = post(cashier, sale_body(tenant_a, [(p, 10)], [cash(9000)], discount_minor=1000))
    assert ok.status_code == 201, ok.data
    # 10,01 % : refusé
    r = post(cashier, sale_body(tenant_a, [(p, 10)], [cash(9000)], discount_minor=1001))
    assert r.status_code == 403 and r.data["detail"] == "Remise supérieure à votre autorisation."
    # remises de ligne + globale cumulées
    r = post(cashier, sale_body(tenant_a, [(p, 10, {"discount_minor": 600})], [cash(9000)],
                                discount_minor=500))
    assert r.status_code == 403
    assert count_sales(tenant_a) == 1


def test_cashier_discount_cap_configurable(tenant_a, settings):
    settings.SALES_CASHIER_MAX_DISCOUNT_PERCENT = 50
    p = make_product(tenant_a, price_minor=1000, track_stock=False)
    cashier = client_for_role(tenant_a, "cashier")
    cashier.post(SESSIONS + "open/", {"store": str(tenant_a.store.id)}, format="json")
    assert post(cashier, sale_body(tenant_a, [(p, 10)], [cash(5000)], discount_minor=5000)).status_code == 201


def test_owner_and_manager_have_no_discount_cap(tenant_a):
    p = make_product(tenant_a, price_minor=1000, track_stock=False)
    manager = client_for_role(tenant_a, "manager")
    assert post(tenant_a.client(), sale_body(tenant_a, [(p, 10)], [cash(2000)], discount_minor=8000)).status_code == 201  # noqa: E501
    assert post(manager, sale_body(tenant_a, [(p, 10)], [cash(2000)], discount_minor=8000)).status_code == 201


def test_discount_is_audited(tenant_a):
    p = make_product(tenant_a, price_minor=1000, track_stock=False)
    plain = post(tenant_a.client(), sale_body(tenant_a, [(p, 1)], [cash(1000)])).data
    d = post(tenant_a.client(), sale_body(tenant_a, [(p, 2, {"discount_minor": 100})], [cash(2000)],
                                          discount_minor=200)).data
    with tenant_context(tenant_a.tenant.id):
        entries = AuditEntry.objects.filter(action="sale.discount")
        assert entries.count() == 1
        e = entries.get()
        assert e.entity_id == d["id"] and e.entity_id != plain["id"]
        assert e.after["total_discount_minor"] == 300 and e.user_id == tenant_a.user.id


def test_cashier_cannot_change_price_but_manager_can(tenant_a):
    p = make_product(tenant_a, price_minor=1000, track_stock=False)
    cashier = client_for_role(tenant_a, "cashier")
    cashier.post(SESSIONS + "open/", {"store": str(tenant_a.store.id)}, format="json")
    manager = client_for_role(tenant_a, "manager")
    custom = sale_body(tenant_a, [(p, 1, {"unit_price_minor": 800})], [cash(800)])
    assert post(cashier, custom).status_code == 403
    r = post(manager, {**custom, "client_id": str(uuid.uuid4())})
    assert r.status_code == 201 and r.data["items"][0]["unit_price_minor"] == 800
    # prix explicite identique au prix catalogue : accepté pour un caissier
    same = sale_body(tenant_a, [(p, 1, {"unit_price_minor": 1000})], [cash(1000)])
    assert post(cashier, same).status_code == 201


# ----------------------------------------------------------------------------- annulation


def test_cancel_restores_stock_and_marks_cancelled(tenant_a):
    p = make_product(tenant_a, price_minor=1000)
    give_stock(tenant_a, p, 10)
    sale = post(tenant_a.client(), sale_body(tenant_a, [(p, 4)], [cash(4000)])).data
    assert stock_of(tenant_a, p) == 6
    r = tenant_a.client().post(f"{BASE}{sale['id']}/cancel/", {"reason": "Erreur de caisse"}, format="json")
    assert r.status_code == 200, r.data
    assert r.data["status"] == "cancelled" and r.data["cancel_reason"] == "Erreur de caisse"
    assert r.data["cancelled_at"] and str(r.data["cancelled_by"]) == str(tenant_a.user.id)
    assert stock_of(tenant_a, p) == 10
    with tenant_context(tenant_a.tenant.id):
        ret = StockMovement.objects.get(kind="return")
        assert ret.quantity == 4 and ret.reference == sale["number"]
        assert Sale.objects.count() == 1  # jamais supprimée
        e = AuditEntry.objects.get(action="sale.cancel")
        assert e.entity_id == sale["id"] and e.reason == "Erreur de caisse"
    listed = tenant_a.client().get(BASE).data["results"]
    assert [s["status"] for s in listed] == ["cancelled"]


def test_cancel_requires_reason_and_rejects_double_cancel(tenant_a):
    p = make_product(tenant_a, price_minor=1000, track_stock=False)
    sale = post(tenant_a.client(), sale_body(tenant_a, [(p, 1)], [cash(1000)])).data
    url = f"{BASE}{sale['id']}/cancel/"
    c = tenant_a.client()
    assert c.post(url, {}, format="json").status_code == 400
    assert c.post(url, {"reason": "   "}, format="json").status_code == 400
    assert c.post(url, {"reason": "Doublon"}, format="json").status_code == 200
    assert c.post(url, {"reason": "Encore"}, format="json").status_code == 409


def test_cancel_credit_sale_cancels_debt(tenant_a):
    cust = make_customer(tenant_a)
    p = make_product(tenant_a, price_minor=6000, track_stock=False)
    sale = post(tenant_a.client(), sale_body(tenant_a, [(p, 1)], [cash(2000)], customer=str(cust.id),
                                             credit_remaining=True)).data
    r = tenant_a.client().post(f"{BASE}{sale['id']}/cancel/", {"reason": "Retour"}, format="json")
    assert r.status_code == 200
    with tenant_context(tenant_a.tenant.id):
        assert credit.get_balance(cust) == {"CDF": 0}
        assert not credit.open_charges(cust).exists()


def test_cancel_targets_the_sales_own_charge_not_fifo(tenant_a):
    cust = make_customer(tenant_a)
    p = make_product(tenant_a, price_minor=6000, track_stock=False)
    c = tenant_a.client()
    s1 = post(c, sale_body(tenant_a, [(p, 1)], customer=str(cust.id), credit_remaining=True)).data
    s2 = post(c, sale_body(tenant_a, [(p, 2)], customer=str(cust.id), credit_remaining=True)).data
    assert c.post(f"{BASE}{s2['id']}/cancel/", {"reason": "x"}, format="json").status_code == 200
    with tenant_context(tenant_a.tenant.id):
        assert credit.get_balance(cust) == {"CDF": 6000}
        open_ = list(credit.open_charges(cust))
        assert len(open_) == 1 and str(open_[0].sale_id) == s1["id"] and open_[0].open_minor == 6000


def test_cancel_refused_if_debt_partially_paid(tenant_a):
    cust = make_customer(tenant_a)
    p = make_product(tenant_a, price_minor=6000)
    give_stock(tenant_a, p, 5)
    sale = post(tenant_a.client(), sale_body(tenant_a, [(p, 1)], customer=str(cust.id),
                                             credit_remaining=True)).data
    with tenant_context(tenant_a.tenant.id):
        credit.register_payment(cust, 1000, "CDF")
    r = tenant_a.client().post(f"{BASE}{sale['id']}/cancel/", {"reason": "Retour"}, format="json")
    assert r.status_code == 409 and "rembours" in r.data["detail"]
    # rien n'a changé : vente active, stock inchangé, dette intacte
    assert tenant_a.client().get(f"{BASE}{sale['id']}/").data["status"] == "completed"
    assert stock_of(tenant_a, p) == 4
    with tenant_context(tenant_a.tenant.id):
        assert credit.get_balance(cust) == {"CDF": 5000}


def test_cancel_permissions(tenant_a):
    p = make_product(tenant_a, price_minor=1000, track_stock=False)
    sale = post(tenant_a.client(), sale_body(tenant_a, [(p, 1)], [cash(1000)])).data
    url = f"{BASE}{sale['id']}/cancel/"
    for role in ("cashier", "storekeeper", "accountant"):
        r = client_for_role(tenant_a, role).post(url, {"reason": "x"}, format="json")
        assert r.status_code == 403, role
    assert client_for_role(tenant_a, "manager").post(url, {"reason": "x"}, format="json").status_code == 200


# ----------------------------------------------------------------------------- permissions


def test_role_matrix(tenant_a):
    p = make_product(tenant_a, price_minor=1000, track_stock=False)
    cashier = client_for_role(tenant_a, "cashier")
    cashier.post(SESSIONS + "open/", {"store": str(tenant_a.store.id)}, format="json")
    expected = {"owner": 201, "manager": 201, "cashier": 201, "accountant": 403, "storekeeper": 403}
    for role, code in expected.items():
        client = tenant_a.client() if role == "owner" else (cashier if role == "cashier"
                                                            else client_for_role(tenant_a, role))
        assert post(client, sale_body(tenant_a, [(p, 1)], [cash(1000)])).status_code == code, role
    reads = {"owner": 200, "manager": 200, "cashier": 200, "accountant": 200, "storekeeper": 403}
    sale_id = tenant_a.client().get(BASE).data["results"][0]["id"]
    for role, code in reads.items():
        client = tenant_a.client() if role == "owner" else client_for_role(tenant_a, role)
        assert client.get(BASE).status_code == code, role
        # le caissier ne voit que ses ventes ; la dernière vente de la liste est celle du caissier
        assert client.get(f"{BASE}{sale_id}/").status_code == code
        assert client.get(f"{BASE}{sale_id}/receipt/").status_code == code


def test_unauthenticated_refused(tenant_a):
    from rest_framework.test import APIClient

    assert APIClient().get(BASE).status_code in (401, 403)
    assert APIClient().post(BASE, {}, format="json").status_code in (401, 403)


def test_cashier_sees_only_own_sales(tenant_a):
    p = make_product(tenant_a, price_minor=1000, track_stock=False)
    cashier = client_for_role(tenant_a, "cashier")
    cashier.post(SESSIONS + "open/", {"store": str(tenant_a.store.id)}, format="json")
    mine = post(cashier, sale_body(tenant_a, [(p, 1)], [cash(1000)])).data
    others = post(tenant_a.client(), sale_body(tenant_a, [(p, 1)], [cash(1000)])).data
    listed = cashier.get(BASE).data
    assert listed["count"] == 1 and listed["results"][0]["id"] == mine["id"]
    assert cashier.get(f"{BASE}{others['id']}/").status_code == 404
    assert cashier.get(f"{BASE}{others['id']}/receipt/").status_code == 404
    assert tenant_a.client().get(BASE).data["count"] == 2
    assert client_for_role(tenant_a, "accountant").get(BASE).data["count"] == 2


# ----------------------------------------------------------------------------- liste et filtres


def test_list_filters(tenant_a):
    cust = make_customer(tenant_a)
    store2 = make_store(tenant_a)
    p = make_product(tenant_a, price_minor=1000, track_stock=False)
    c = tenant_a.client()
    s1 = post(c, sale_body(tenant_a, [(p, 1)], [cash(1000)])).data
    s2 = post(c, {**sale_body(tenant_a, [(p, 1)], [cash(1000)], customer=str(cust.id)),
                  "store": str(store2.id)}).data
    c.post(f"{BASE}{s1['id']}/cancel/", {"reason": "x"}, format="json")

    def ids(**params):
        return {s["id"] for s in c.get(BASE, params).data["results"]}

    assert ids() == {s1["id"], s2["id"]}
    assert ids(store=str(store2.id)) == {s2["id"]}
    assert ids(customer=str(cust.id)) == {s2["id"]}
    assert ids(status="cancelled") == {s1["id"]}
    assert ids(status="completed") == {s2["id"]}
    assert ids(cashier=str(tenant_a.user.id)) == {s1["id"], s2["id"]}
    assert ids(q="B02-0000") == {s2["id"]}
    assert ids(date_from="2999-01-01") == set()
    assert ids(date_to="2000-01-01") == set()
    assert ids(date_from="2000-01-01", date_to="2999-01-01") == {s1["id"], s2["id"]}
    assert c.get(BASE, {"date_from": "pas-une-date"}).status_code == 400
    assert c.get(BASE, {"store": "nope"}).status_code == 400


# ----------------------------------------------------------------------------- isolation


def test_tenant_isolation(tenant_a, tenant_b):
    pa = make_product(tenant_a, price_minor=1000, track_stock=False)
    pb = make_product(tenant_b, price_minor=1000, track_stock=False)
    cust_b = make_customer(tenant_b)
    sale_b = post(tenant_b.client(), sale_body(tenant_b, [(pb, 1)], [cash(1000)])).data
    a = tenant_a.client()
    assert a.get(BASE).data["count"] == 0
    assert a.get(f"{BASE}{sale_b['id']}/").status_code == 404
    assert a.get(f"{BASE}{sale_b['id']}/receipt/").status_code == 404
    assert a.post(f"{BASE}{sale_b['id']}/cancel/", {"reason": "x"}, format="json").status_code == 404
    # produits, boutique et client de B inaccessibles à A
    assert post(a, sale_body(tenant_a, [(pb, 1)], [cash(1000)])).status_code == 400
    assert post(a, {**sale_body(tenant_a, [(pa, 1)], [cash(1000)]), "store": str(tenant_b.store.id)}
                ).status_code == 400
    assert post(a, sale_body(tenant_a, [(pa, 1)], [cash(1000)], customer=str(cust_b.id))).status_code == 400
    # la vente de B est intacte
    assert tenant_b.client().get(f"{BASE}{sale_b['id']}/").data["status"] == "completed"
    with tenant_context(tenant_a.tenant.id):
        assert Sale.objects.count() == 0 and SaleItem.objects.count() == 0 and Payment.objects.count() == 0
    with tenant_context(tenant_b.tenant.id):
        assert Sale.objects.count() == 1


def test_sale_is_immutable(tenant_a):
    p = make_product(tenant_a, price_minor=1000, track_stock=False)
    sale_id = post(tenant_a.client(), sale_body(tenant_a, [(p, 1)], [cash(1000)])).data["id"]
    with tenant_context(tenant_a.tenant.id):
        sale = Sale.objects.get(pk=sale_id)
        sale.total_minor = 1
        with pytest.raises(RuntimeError):
            sale.save()
        with pytest.raises(RuntimeError):
            sale.delete()
        item = sale.items.get()
        with pytest.raises(RuntimeError):
            item.save()
        with pytest.raises(RuntimeError):
            sale.payments.get().delete()
