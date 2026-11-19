"""Reçu imprimable : HTML et texte 32 colonnes."""

import pytest

from sales.tests.helpers import BASE, cash, make_customer, make_product, make_rate, sale_body

pytestmark = pytest.mark.django_db


def make_receipt_sale(handle):
    make_rate(handle, "2800")
    cust = make_customer(handle, name="Mama Julie")
    p = make_product(handle, name="Savon de Marseille parfumé lavande", price_minor=12_500, track_stock=False)
    q = make_product(handle, sku="E1", name="Eau", price_minor=1000, track_stock=False)
    body = sale_body(handle, [(p, 2, {"discount_minor": 500}), (q, 1)], [cash(10_000), cash(5, "USD")],
                     discount_minor=1000, customer=str(cust.id), credit_remaining=True,
                     change_currency="CDF")
    r = handle.client().post(BASE, body, format="json")
    assert r.status_code == 201, r.data
    return r.data


def test_html_receipt(tenant_a):
    sale = make_receipt_sale(tenant_a)
    r = tenant_a.client().get(f"{BASE}{sale['id']}/receipt/", {"format": "html"})
    assert r.status_code == 200 and r["Content-Type"].startswith("text/html")
    html = r.content.decode()
    for needle in ("Commerce A", sale["number"], "Savon de Marseille", "TOTAL", "Remise globale",
                   "Remises lignes", "Espèces (USD)", "Reste à crédit", "Solde du client",
                   "Mama Julie", "Taux : 1 USD = 2800.00 CDF", "Équiv. USD", "Merci"):
        assert needle in html, needle
    assert "VENTE ANNULÉE" not in html


def test_default_format_is_html(tenant_a):
    sale = make_receipt_sale(tenant_a)
    r = tenant_a.client().get(f"{BASE}{sale['id']}/receipt/")
    assert r.status_code == 200 and r["Content-Type"].startswith("text/html")


def test_text_receipt_is_32_columns(tenant_a):
    sale = make_receipt_sale(tenant_a)
    r = tenant_a.client().get(f"{BASE}{sale['id']}/receipt/", {"format": "text"})
    assert r.status_code == 200 and r["Content-Type"].startswith("text/plain")
    text = r.content.decode()
    assert all(len(line) <= 32 for line in text.splitlines()), text
    # total = 2*12500 - 500 + 1000 - 1000 = 24 500 CDF ; équivalent 8,75 $
    assert "TOTAL" in text and "24 500 FC" in text
    assert "$8.75" in text and "Équiv. USD" in text
    assert "Espèces (CDF)" in text and "Espèces (USD)" in text
    assert "Reçu N° " + sale["number"] in text
    assert "Solde du client" in text


def test_receipt_of_cancelled_sale_is_marked(tenant_a):
    sale = make_receipt_sale(tenant_a)
    # annulation impossible tant que le crédit n'est pas payé partiellement : ici aucune allocation
    r = tenant_a.client().post(f"{BASE}{sale['id']}/cancel/", {"reason": "Erreur"}, format="json")
    assert r.status_code == 200
    text = tenant_a.client().get(f"{BASE}{sale['id']}/receipt/", {"format": "text"}).content.decode()
    assert "VENTE ANNULÉE" in text
    assert "VENTE ANNULÉE" in tenant_a.client().get(f"{BASE}{sale['id']}/receipt/").content.decode()


def test_receipt_escapes_html(tenant_a):
    p = make_product(tenant_a, name="<script>alert(1)</script>", price_minor=1000, track_stock=False)
    sale = tenant_a.client().post(BASE, sale_body(tenant_a, [(p, 1)], [cash(1000)]), format="json").data
    html = tenant_a.client().get(f"{BASE}{sale['id']}/receipt/").content.decode()
    assert "<script>alert" not in html and "&lt;script&gt;" in html


def test_receipt_404_for_unknown_sale(tenant_a):
    r = tenant_a.client().get(f"{BASE}11111111-1111-1111-1111-111111111111/receipt/")
    assert r.status_code == 404
