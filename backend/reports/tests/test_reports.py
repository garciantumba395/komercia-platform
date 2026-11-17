"""Rapports : totaux exacts (valeurs calculées à la main), devises, jours, boutiques, export, droits."""

import csv
import io
import uuid
from decimal import Decimal
from unittest.mock import patch

import pytest

from core.tenancy import tenant_context
from customers import services as credit
from reports.tests.helpers import DAILY, EXPORT, SUMMARY, at, make_product, sell
from sales.models import Payment, Sale, SaleItem
from sales.tests.helpers import cash, client_for_role, make_customer, make_rate, make_store
from stock.models import StockLevel
from stock.services import apply_movement

pytestmark = pytest.mark.django_db

D = "2026-03-10"  # jour de référence (mardi)


def build_reference_data(h):
    """Jeu de données de référence, taux 1 USD = 2800 CDF. Voir test_daily_exact_totals pour le détail."""
    make_rate(h, "2800")
    savon = make_product(h, name="Savon", sku="SAV", price_minor=1500, cost_minor=1000, currency="CDF")
    riz = make_product(h, name="Riz", sku="RIZ", price_minor=500, cost_minor=300, currency="USD")
    coupe = make_product(h, name="Coupe", sku="CPE", price_minor=2000, cost_minor=0, currency="CDF",
                         kind="service", track_stock=False)
    import_ = make_product(h, name="Import", sku="IMP", price_minor=1000, cost_minor=400, currency="USD")
    for p in (savon, riz, import_):
        p.track_stock = False
        with tenant_context(h.tenant.id):
            p.save()
    c1 = make_customer(h, "Client Un")
    c2 = make_customer(h, "Client Deux")
    # S1 : CDF, 3 savons = 4500 ; payé 5000, rendu 500 CDF
    sell(h, [(savon, 3)], [cash(5000)], at(D, 8))
    # S2 : CDF, 2 savons (3000) - remise ligne 500 - remise globale 200 = 2300 ;
    #      payé 1000 CDF + 0,50 USD (1400 CDF) = 2400 ; rendu 100 CDF
    sell(h, [(savon, 2, {"discount_minor": 500})], [cash(1000), cash(50, "USD")], at(D, 9),
         discount_minor=200)
    # S3 : USD, 2 riz = 1000 cents ; payé 1000 USD
    sell(h, [(riz, 2)], [cash(1000, "USD")], at(D, 10), currency="USD")
    # S4 : USD, 1 riz (500) + 1 coupe (2000 CDF = 71 cents) = 571 ; payé 300 USD, 271 à crédit
    sell(h, [(riz, 1), (coupe, 1)], [cash(300, "USD")], at(D, 11), currency="USD",
         customer=str(c1.id), credit_remaining=True)
    # S5 : CDF, 1 import (10 $ = 28000 CDF) + 2 coupes (4000) = 32000 ; payé 30000, 2000 à crédit
    sell(h, [(import_, 1), (coupe, 2)], [cash(30000)], at(D, 12), customer=str(c2.id), credit_remaining=True)
    # S6 : CDF, 10 savons, ANNULÉE
    sell(h, [(savon, 10)], [cash(15000)], at(D, 13), cancel=True)
    # S7 : USD, 1 savon (1500 CDF = 54 cents) ; payé 54 USD ; coût 1000 CDF = 36 cents
    sell(h, [(savon, 1)], [cash(54, "USD")], at(D, 23, 30), currency="USD")
    return dict(savon=savon, riz=riz, coupe=coupe, import_=import_, c1=c1, c2=c2)


def get(client, url, **params):
    return client.get(url, params)


# ------------------------------------------------------------------------------------- journalier


def test_daily_exact_totals(tenant_a):
    ref = build_reference_data(tenant_a)
    r = get(tenant_a.client(), DAILY, date=D)
    assert r.status_code == 200, r.content
    d = r.json()
    assert d["date"] == D and d["store"] is None
    assert d["sales_count"] == 6  # S1..S5 + S7 ; S6 annulée exclue
    assert d["cancelled_count"] == 1
    # CA : CDF 4500 + 2300 + 32000 ; USD 1000 + 571 + 54
    assert d["revenue"] == {"CDF": 38800, "USD": 1625}
    # Encaissé : CDF (5000-500) + (1000-100) + 30000 ; USD 50 + 1000 + 300 + 54
    assert d["cash_collected"] == {"CDF": 35400, "USD": 1404}
    assert d["credit_given"] == {"CDF": 2000, "USD": 271}
    # Marge CDF : S1 4500-3000 ; S2 2300-2000 ; S5 32000 - 11200 (4 $ = 11200 CDF) ; services : coût 0
    #   = 1500 + 300 + 20800 ; USD : S3 1000-600 ; S4 571-300 ; S7 54-36 (1000 CDF = 35,71 -> 36) = 400+271+18
    assert d["estimated_margin"] == {"CDF": 22600, "USD": 689}
    assert d["debts"] == {"CDF": 2000, "USD": 271}
    assert d["low_stock"] == []
    # Produits : par (produit, devise de vente), CA des lignes décroissant, annulée exclue
    tops = [(t["name"], t["currency"], t["quantity"], t["revenue_minor"]) for t in d["top_products"]]
    assert tops == [
        ("Import", "CDF", "1", 28000),
        ("Savon", "CDF", "5", 7000),  # 4500 + (3000-500)
        ("Coupe", "CDF", "2", 4000),
        ("Riz", "USD", "3", 1500),
        ("Coupe", "USD", "1", 71),
        ("Savon", "USD", "1", 54),
    ]
    assert d["top_products"][0]["product"] == str(ref["import_"].id)


def test_currencies_never_mixed(tenant_a):
    make_rate(tenant_a, "2800")
    riz = make_product(tenant_a, name="Riz", sku="R", price_minor=500, cost_minor=0, currency="USD",
                       track_stock=False)
    sell(tenant_a, [(riz, 2)], [cash(1000, "USD")], at(D), currency="USD")
    d = get(tenant_a.client(), DAILY, date=D).json()
    assert d["revenue"] == {"CDF": 0, "USD": 1000}
    assert d["cash_collected"] == {"CDF": 0, "USD": 1000}
    assert d["estimated_margin"] == {"CDF": 0, "USD": 1000}  # coût 0 : marge = CA


def test_margin_currency_conversion_rounding(tenant_a):
    """Coût 1 USD-cent x 3 = 3 cents -> 84 CDF ; coût CDF 1000 en vente USD -> 35,71 -> 36 cents."""
    make_rate(tenant_a, "2800")
    usd_cost = make_product(tenant_a, name="Usd", sku="U", price_minor=100, cost_minor=1, currency="USD",
                            track_stock=False)
    cdf_cost = make_product(tenant_a, name="Cdf", sku="C", price_minor=2800, cost_minor=1000, currency="CDF",
                            track_stock=False)
    sell(tenant_a, [(usd_cost, 3)], [cash(300000)], at(D, 9))  # prix 1 $ = 2800 CDF x 3 = 8400 CDF
    sell(tenant_a, [(cdf_cost, 1)], [cash(100, "USD")], at(D, 10),
         currency="USD")  # prix 2800 CDF = 100 cents
    d = get(tenant_a.client(), DAILY, date=D).json()
    # vente CDF : 3 x 2800 = 8400 CDF ; coût 3 cents = 84 CDF -> marge 8316
    assert d["estimated_margin"]["CDF"] == 8400 - 84
    assert d["estimated_margin"]["USD"] == 100 - 36


def test_empty_day(tenant_a):
    d = get(tenant_a.client(), DAILY, date="2020-01-01").json()
    zeros = {"CDF": 0, "USD": 0}
    assert d["sales_count"] == 0 and d["cancelled_count"] == 0
    for key in ("revenue", "cash_collected", "credit_given", "estimated_margin", "debts"):
        assert d[key] == zeros
    assert d["top_products"] == [] and d["low_stock"] == []


def test_default_date_is_today_in_kinshasa(tenant_a):
    import datetime

    make_rate(tenant_a)
    p = make_product(tenant_a, price_minor=1000, track_stock=False)
    sell(tenant_a, [(p, 1)], [cash(1000)], at("2026-05-02", 7))
    with patch("reports.services.today", return_value=datetime.date(2026, 5, 2)):
        d = tenant_a.client().get(DAILY).json()
    assert d["date"] == "2026-05-02" and d["sales_count"] == 1


def test_day_boundary_at_midnight_kinshasa(tenant_a):
    """Kinshasa = UTC+1 : 23h30 locale (22h30 UTC) reste le même jour ; 00h30 locale (23h30 UTC la
    veille) appartient au lendemain ; minuit pile ouvre le jour, 23h59m59s le clôt."""
    make_rate(tenant_a)
    p = make_product(tenant_a, price_minor=1000, track_stock=False)
    for when in (at("2026-03-09", 23, 59, 59), at(D, 0, 0, 0), at(D, 23, 30), at(D, 23, 59, 59),
                 at("2026-03-11", 0, 0, 0), at("2026-03-11", 0, 30)):
        sell(tenant_a, [(p, 1)], [cash(1000)], when)
    c = tenant_a.client()
    assert get(c, DAILY, date="2026-03-09").json()["sales_count"] == 1
    assert get(c, DAILY, date=D).json()["sales_count"] == 3
    assert get(c, DAILY, date="2026-03-11").json()["sales_count"] == 2
    s = get(c, SUMMARY, period="custom", date_from="2026-03-09", date_to="2026-03-11").json()
    assert [x["sales_count"] for x in s["by_day"]] == [1, 3, 2]


def test_store_filter(tenant_a):
    make_rate(tenant_a)
    store2 = make_store(tenant_a, "Boutique 2")
    p = make_product(tenant_a, price_minor=1000, track_stock=False)
    sell(tenant_a, [(p, 1)], [cash(1000)], at(D, 9))
    sell(tenant_a, [(p, 2)], [cash(2000)], at(D, 10), store=store2)
    sell(tenant_a, [(p, 3)], [cash(3000)], at(D, 11), store=store2)
    c = tenant_a.client()
    s1 = get(c, DAILY, date=D, store=str(tenant_a.store.id)).json()
    s2 = get(c, DAILY, date=D, store=str(store2.id)).json()
    allr = get(c, DAILY, date=D).json()
    assert (s1["sales_count"], s1["revenue"]["CDF"], s1["store"]) == (1, 1000, str(tenant_a.store.id))
    assert (s2["sales_count"], s2["revenue"]["CDF"]) == (2, 5000)
    assert (allr["sales_count"], allr["revenue"]["CDF"]) == (3, 6000)
    assert s2["top_products"][0]["quantity"] == "5"


def test_low_stock_and_store_filter(tenant_a):
    store2 = make_store(tenant_a, "Boutique 2")
    low = make_product(tenant_a, name="Bas", sku="B", min_stock=Decimal("5"), track_stock=True)
    ok = make_product(tenant_a, name="Ok", sku="O", min_stock=Decimal("5"), track_stock=True)
    untracked = make_product(tenant_a, name="Libre", sku="L", min_stock=Decimal("5"), track_stock=False)
    with tenant_context(tenant_a.tenant.id):
        apply_movement(low, tenant_a.store, Decimal("5"), "in")  # == seuil : en alerte
        apply_movement(ok, tenant_a.store, Decimal("10"), "in")
        apply_movement(low, store2, Decimal("2.5"), "in")
        StockLevel.objects.get_or_create(product=untracked, store=tenant_a.store)
    c = tenant_a.client()
    d = get(c, DAILY, date=D).json()
    assert [(x["name"], x["store"], x["quantity"], x["min_stock"]) for x in d["low_stock"]] == [
        ("Bas", str(store2.id), "2.5", "5"), ("Bas", str(tenant_a.store.id), "5", "5")]
    d = get(c, DAILY, date=D, store=str(store2.id)).json()
    assert [x["quantity"] for x in d["low_stock"]] == ["2.5"]


def test_debts_reflect_payments(tenant_a):
    make_rate(tenant_a)
    cust = make_customer(tenant_a)
    p = make_product(tenant_a, price_minor=10000, track_stock=False)
    sell(tenant_a, [(p, 1)], [], at(D), customer=str(cust.id), credit_remaining=True)
    with tenant_context(tenant_a.tenant.id):
        credit.register_payment(cust, 4000, "CDF")
    assert get(tenant_a.client(), DAILY, date="2020-01-01").json()["debts"] == {"CDF": 6000, "USD": 0}


def test_top_products_limited_to_10(tenant_a):
    make_rate(tenant_a)
    for i in range(12):
        p = make_product(tenant_a, name=f"P{i:02d}", sku=f"S{i}", price_minor=1000 * (i + 1),
                         track_stock=False)
        sell(tenant_a, [(p, 1)], [cash(1000 * (i + 1))], at(D))
    tops = get(tenant_a.client(), DAILY, date=D).json()["top_products"]
    assert len(tops) == 10 and tops[0]["name"] == "P11" and tops[-1]["name"] == "P02"


# ------------------------------------------------------------------------------------- synthèse


def test_summary_exact_and_by_day(tenant_a):
    build_reference_data(tenant_a)
    p = make_product(tenant_a, name="Autre", sku="AU", price_minor=700, cost_minor=0, track_stock=False)
    sell(tenant_a, [(p, 1)], [cash(700)], at("2026-03-12", 15))
    sell(tenant_a, [(p, 1)], [cash(700)], at("2026-03-06", 15))  # hors période
    r = get(tenant_a.client(), SUMMARY, period="custom", date_from="2026-03-09", date_to="2026-03-13")
    assert r.status_code == 200, r.content
    s = r.json()
    assert (s["from"], s["to"]) == ("2026-03-09", "2026-03-13")
    assert s["sales_count"] == 7
    assert s["revenue"] == {"CDF": 38800 + 700, "USD": 1625}
    assert s["cash_collected"] == {"CDF": 35400 + 700, "USD": 1404}
    assert s["estimated_margin"] == {"CDF": 22600 + 700, "USD": 689}
    assert s["credit_given"] == {"CDF": 2000, "USD": 271}
    assert s["by_day"] == [
        {"date": "2026-03-09", "sales_count": 0, "revenue": {"CDF": 0, "USD": 0}},
        {"date": "2026-03-10", "sales_count": 6, "revenue": {"CDF": 38800, "USD": 1625}},
        {"date": "2026-03-11", "sales_count": 0, "revenue": {"CDF": 0, "USD": 0}},
        {"date": "2026-03-12", "sales_count": 1, "revenue": {"CDF": 700, "USD": 0}},
        {"date": "2026-03-13", "sales_count": 0, "revenue": {"CDF": 0, "USD": 0}},
    ]
    assert len(s["top_products"]) == 7  # 6 de la veille + Autre
    assert sum(x["sales_count"] for x in s["by_day"]) == s["sales_count"]


def test_summary_by_store(tenant_a):
    make_rate(tenant_a)
    store2 = make_store(tenant_a, "Zeta")
    make_store(tenant_a, "Vide")
    p = make_product(tenant_a, price_minor=1000, track_stock=False)
    sell(tenant_a, [(p, 1)], [cash(1000)], at(D))
    sell(tenant_a, [(p, 2)], [cash(2000)], at(D, 11), store=store2)
    sell(tenant_a, [(p, 1)], [cash(36, "USD")], at(D, 12), store=store2,
         currency="USD")  # 1000 CDF = 35,71 -> 36 cents
    s = get(tenant_a.client(), SUMMARY, period="custom", date_from=D, date_to=D).json()
    by = {x["name"]: x for x in s["by_store"]}
    assert set(by) == {tenant_a.store.name, "Zeta", "Vide"}
    assert by["Zeta"]["store"] == str(store2.id) and by["Zeta"]["sales_count"] == 2
    assert by["Zeta"]["revenue"] == {"CDF": 2000, "USD": 36}
    assert by[tenant_a.store.name]["revenue"] == {"CDF": 1000, "USD": 0}
    assert by["Vide"] == {"store": by["Vide"]["store"], "name": "Vide", "sales_count": 0,
                          "revenue": {"CDF": 0, "USD": 0}}
    only = get(tenant_a.client(), SUMMARY, period="custom", date_from=D, date_to=D,
               store=str(store2.id)).json()
    assert [x["name"] for x in only["by_store"]] == ["Zeta"] and only["sales_count"] == 2


def test_summary_week_and_month_periods(tenant_a):
    import datetime

    make_rate(tenant_a)
    p = make_product(tenant_a, price_minor=1000, track_stock=False)
    for day in ("2026-03-03", "2026-03-04", "2026-03-10", "2026-02-28"):
        sell(tenant_a, [(p, 1)], [cash(1000)], at(day))
    c = tenant_a.client()
    with patch("reports.services.today", return_value=datetime.date(2026, 3, 10)):
        week = c.get(SUMMARY, {"period": "week"}).json()
        month = c.get(SUMMARY, {"period": "month"}).json()
        default = c.get(SUMMARY).json()
    assert (week["from"], week["to"]) == ("2026-03-04", "2026-03-10") and len(week["by_day"]) == 7
    assert week["sales_count"] == 2  # 4 et 10 mars ; le 3 est hors des 7 jours glissants
    assert (month["from"], month["to"]) == ("2026-03-01", "2026-03-10") and month["sales_count"] == 3
    assert default["from"] == week["from"]


# ------------------------------------------------------------------------------------- paramètres


@pytest.mark.parametrize("params", [
    {"date": "2026-13-40"}, {"date": "hier"}, {"store": "pas-un-uuid"}, {"store": str(uuid.uuid4())},
])
def test_daily_invalid_params(tenant_a, params):
    r = get(tenant_a.client(), DAILY, **params)
    assert r.status_code == 400
    assert list(r.json())[0] in ("date", "store")


@pytest.mark.parametrize("params", [
    {"period": "year"},
    {"period": "custom"},
    {"period": "custom", "date_from": "2026-03-01"},
    {"period": "custom", "date_from": "2026-03-10", "date_to": "2026-03-01"},
    {"period": "custom", "date_from": "x", "date_to": "2026-03-01"},
    {"period": "custom", "date_from": "2025-01-01", "date_to": "2026-01-02"},  # 367 jours
    {"store": "zzz"},
])
def test_summary_invalid_params(tenant_a, params):
    assert get(tenant_a.client(), SUMMARY, **params).status_code == 400


def test_summary_366_days_ok(tenant_a):
    r = get(tenant_a.client(), SUMMARY, period="custom", date_from="2025-01-01", date_to="2026-01-01")
    assert r.status_code == 200 and len(r.json()["by_day"]) == 366


# ------------------------------------------------------------------------------------- export CSV


def parse_csv(response):
    text = response.content.decode("utf-8")
    assert text.startswith("﻿")
    return list(csv.reader(io.StringIO(text[1:]), delimiter=";"))


def test_export_daily_csv(tenant_a):
    build_reference_data(tenant_a)
    r = get(tenant_a.client(), EXPORT, kind="daily", date=D, format="csv")
    assert r.status_code == 200
    assert r["Content-Type"].startswith("text/csv")
    assert "rapport-journalier-2026-03-10.csv" in r["Content-Disposition"]
    assert r.content.startswith("﻿".encode())  # BOM UTF-8
    rows = parse_csv(r)
    assert ["Rapport journalier"] in rows and ["Date", D] in rows
    assert ["Boutique", "Toutes les boutiques"] in rows
    assert ["Indicateur", "Devise", "Montant"] in rows
    assert ["Nombre de ventes", "", "6"] in rows
    assert ["Chiffre d'affaires", "CDF", "38800"] in rows
    assert ["Chiffre d'affaires", "USD", "16,25"] in rows
    assert ["Marge estimée", "USD", "6,89"] in rows
    assert ["Ventes annulées", "", "1"] in rows
    assert ["Dettes clients ouvertes", "CDF", "2000"] in rows
    assert ["Import", "1", "CDF", "28000"] in rows


def test_export_summary_csv_and_store_name(tenant_a):
    build_reference_data(tenant_a)
    r = get(tenant_a.client(), EXPORT, kind="summary", period="custom", date_from="2026-03-09",
            date_to="2026-03-11", store=str(tenant_a.store.id))
    assert r.status_code == 200
    rows = parse_csv(r)
    assert ["Boutique", tenant_a.store.name] in rows
    assert ["2026-03-09", "0", "0", "0,00"] in rows
    assert ["2026-03-10", "6", "38800", "16,25"] in rows
    assert [tenant_a.store.name, "6", "38800", "16,25"] in rows


def test_export_neutralizes_formula_injection(tenant_a):
    make_rate(tenant_a)
    p = make_product(tenant_a, name="=CMD()", price_minor=1000, track_stock=False)
    sell(tenant_a, [(p, 1)], [cash(1000)], at(D))
    rows = parse_csv(get(tenant_a.client(), EXPORT, kind="daily", date=D))
    assert ["'=CMD()", "1", "CDF", "1000"] in rows


@pytest.mark.parametrize("params", [
    {}, {"kind": "pdf"}, {"kind": "daily", "format": "xlsx"}, {"kind": "daily", "date": "bad"},
    {"kind": "summary", "period": "custom"},
])
def test_export_invalid_params(tenant_a, params):
    r = get(tenant_a.client(), EXPORT, **params)
    assert r.status_code == 400 and r["Content-Type"].startswith("application/json")


# ------------------------------------------------------------------------------------- permissions


@pytest.mark.parametrize("role,allowed", [
    ("owner", True), ("manager", True), ("accountant", True), ("cashier", False), ("storekeeper", False),
])
def test_permissions_matrix(tenant_a, role, allowed):
    client = client_for_role(tenant_a, role)
    expected = 200 if allowed else 403
    assert get(client, DAILY, date=D).status_code == expected
    assert get(client, SUMMARY, period="week").status_code == expected
    assert get(client, EXPORT, kind="daily", date=D, format="csv").status_code == expected
    assert get(client, EXPORT, kind="summary", period="week").status_code == expected


def test_anonymous_is_refused(tenant_a):
    from rest_framework.test import APIClient

    for url in (DAILY, SUMMARY, EXPORT):
        assert APIClient().get(url).status_code in (401, 403)


# ------------------------------------------------------------------------------------- isolation


def test_tenant_isolation_all_reports(tenant_a, tenant_b):
    build_reference_data(tenant_a)
    # B : données bien distinctes (une vente USD, une dette, un stock bas, une boutique)
    make_rate(tenant_b, "2800")
    pb = make_product(tenant_b, name="SecretB", sku="SB", price_minor=999, cost_minor=1, currency="USD",
                      min_stock=Decimal("9"), track_stock=True)
    with tenant_context(tenant_b.tenant.id):
        apply_movement(pb, tenant_b.store, Decimal("100"), "in")
    cb = make_customer(tenant_b, "DebiteurB")
    sell(tenant_b, [(pb, 1)], [], at(D), currency="USD", customer=str(cb.id), credit_remaining=True)
    store_b2 = make_store(tenant_b, "Boutique B2")
    with tenant_context(tenant_b.tenant.id):
        low = make_product(tenant_b, name="BasB", sku="BB", min_stock=Decimal("9"), track_stock=True)
        apply_movement(low, tenant_b.store, Decimal("1"), "in")

    ca, cbc = tenant_a.client(), tenant_b.client()
    a = get(ca, DAILY, date=D).json()
    assert a["revenue"] == {"CDF": 38800, "USD": 1625} and a["debts"] == {"CDF": 2000, "USD": 271}
    assert a["low_stock"] == []
    assert "SecretB" not in str(a)

    b = get(cbc, DAILY, date=D).json()
    assert b["sales_count"] == 1 and b["revenue"] == {"CDF": 0, "USD": 999}
    assert b["debts"] == {"CDF": 0, "USD": 999}
    assert [x["name"] for x in b["low_stock"]] == ["BasB"]
    assert "Savon" not in str(b)

    sa = get(ca, SUMMARY, period="custom", date_from=D, date_to=D).json()
    assert sa["sales_count"] == 6 and "Boutique B2" not in str(sa) and "SecretB" not in str(sa)
    sb = get(cbc, SUMMARY, period="custom", date_from=D, date_to=D).json()
    assert sb["sales_count"] == 1 and "Savon" not in str(sb)
    assert {x["name"] for x in sb["by_store"]} == {tenant_b.store.name, "Boutique B2"}

    csv_a = get(ca, EXPORT, kind="summary", period="custom", date_from=D, date_to=D).content.decode()
    assert "SecretB" not in csv_a and "Boutique B2" not in csv_a
    # boutique de l'autre entreprise : introuvable
    assert get(ca, DAILY, date=D, store=str(store_b2.id)).status_code == 400
    assert get(ca, SUMMARY, store=str(store_b2.id)).status_code == 400
    assert get(ca, EXPORT, kind="daily", store=str(store_b2.id)).status_code == 400


# ------------------------------------------------------------------------------------- performance


def _bulk_sales(handle, n, store=None):
    product = make_product(handle, name="Bulk", sku="BULK", price_minor=1000, cost_minor=600,
                           track_stock=False)
    cust = make_customer(handle, "Bulk")
    with tenant_context(handle.tenant.id):
        tid, user, st = handle.tenant.id, handle.user, store or handle.store
        sales = [
            Sale(tenant_id=tid, client_id=uuid.uuid4(), number=f"X-{i:06d}", request_hash="h", store=st,
                 customer=cust if i % 5 == 0 else None, cashier=user,
                 currency="CDF" if i % 2 else "USD", subtotal_minor=1000, total_minor=1000,
                 paid_minor=1000, change_currency="CDF", credit_minor=0,
                 status=Sale.CANCELLED if i % 50 == 0 else Sale.COMPLETED)
            for i in range(n)
        ]
        Sale.objects.bulk_create(sales)
        SaleItem.objects.bulk_create([
            SaleItem(tenant_id=tid, sale=s, product=product, name="Bulk", quantity=Decimal("1"),
                     unit_price_minor=1000, line_total_minor=1000) for s in sales])
        Payment.objects.bulk_create([
            Payment(tenant_id=tid, sale=s, amount_minor=1000, currency=s.currency) for s in sales])
        Sale.objects.filter(tenant_id=tid, number__startswith="X-").update(created_at=at(D, 12))
    return product


def test_bounded_query_count_with_300_sales(tenant_a, django_assert_max_num_queries):
    make_rate(tenant_a)
    _bulk_sales(tenant_a, 300)
    client = tenant_a.client()
    with django_assert_max_num_queries(20):
        r = get(client, DAILY, date=D)
    d = r.json()
    assert d["sales_count"] == 294 and d["cancelled_count"] == 6
    with django_assert_max_num_queries(20):
        s = get(client, SUMMARY, period="custom", date_from="2026-03-01", date_to="2026-03-31")
    assert s.json()["sales_count"] == 294 and len(s.json()["by_day"]) == 31
    with django_assert_max_num_queries(20):
        assert get(client, EXPORT, kind="summary", period="custom", date_from="2026-03-01",
                   date_to="2026-03-31").status_code == 200


def test_query_count_does_not_grow_with_volume(tenant_a):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    make_rate(tenant_a)
    client = tenant_a.client()
    with CaptureQueriesContext(connection) as small:
        get(client, DAILY, date=D)
    _bulk_sales(tenant_a, 300)
    with CaptureQueriesContext(connection) as big:
        get(client, DAILY, date=D)
    assert len(big) <= len(small) + 1  # + éventuellement la requête de marge (absente sans vente)
