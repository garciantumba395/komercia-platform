import io

from catalog.models import Product
from catalog.tests.helpers import client_for_role, make_product
from core.tenancy import tenant_context

P = "/api/v1/catalog/products/"

CSV = """name,sku,barcode,category,kind,unit,price,currency,cost
Savon,SAV-1,111,Hygiène,product,piece,1500,CDF,1000
Coiffure,COIF,,Services,service,other,"12,50",USD,
,NOSKU2,,,product,piece,10,CDF,
Sans prix valide,BAD-1,,,product,piece,abc,CDF,
Doublon dans fichier,SAV-1,,,product,piece,10,CDF,
Mauvais type,T-1,,,bogus,piece,10,CDF,
Décimales CDF,DEC-1,,,product,piece,10.5,CDF,
Huile,HUI-1,222,Hygiène,product,litre,3.75,USD,2.5
"""


def _upload(client, text):
    f = io.BytesIO(text.encode())
    f.name = "produits.csv"
    return client.post(P + "import/", {"file": f}, format="multipart")


def test_import_partial_with_report(tenant_a):
    r = _upload(tenant_a.client(), CSV)
    assert r.status_code == 200, r.content
    rep = r.json()
    assert rep["total"] == 8 and rep["created"] == 3 and rep["failed"] == 5
    by_line = {row["line"]: row for row in rep["rows"]}
    assert by_line[2]["status"] == "created"
    assert by_line[4]["status"] == "error" and "nom" in by_line[4]["errors"][0].lower()
    assert by_line[5]["status"] == "error"
    assert by_line[6]["status"] == "error" and "SKU" in by_line[6]["errors"][0]
    assert by_line[7]["status"] == "error"
    assert by_line[8]["status"] == "error"
    with tenant_context(tenant_a.tenant.id):
        assert Product.objects.count() == 3
        usd = Product.objects.get(sku="COIF")
        assert usd.price_minor == 1250 and usd.currency == "USD" and usd.track_stock is False
        assert Product.objects.get(sku="HUI-1").price_minor == 375
        assert Product.objects.get(sku="HUI-1").cost_minor == 250
        assert Product.objects.get(sku="SAV-1").category.name == "Hygiène"


def test_import_existing_sku_rejected_and_semicolon(tenant_a):
    make_product(tenant_a, sku="EXIST")
    r = _upload(tenant_a.client(), "name;sku;price\nA;EXIST;10\nB;NEW;20\n").json()
    assert r["created"] == 1 and r["failed"] == 1


def test_import_missing_columns_and_file(tenant_a):
    c = tenant_a.client()
    assert _upload(c, "foo,bar\n1,2\n").status_code == 400
    assert c.post(P + "import/", {}, format="multipart").status_code == 400


def test_import_forbidden_for_cashier(tenant_a):
    assert _upload(client_for_role(tenant_a, "cashier"), CSV).status_code == 403


def test_import_isolated(tenant_a, tenant_b):
    _upload(tenant_a.client(), "name,sku\nA,S1\n")
    r = _upload(tenant_b.client(), "name,sku\nA,S1\n").json()
    assert r["created"] == 1  # même SKU autorisé chez un autre tenant


def test_export_csv_roles(tenant_a):
    make_product(tenant_a, name="Savon", sku="S1", price_minor=1500, cost_minor=1000)
    make_product(tenant_a, name="Lait", sku="L1", currency="USD", price_minor=375, cost_minor=250)
    out = tenant_a.client().get(P + "export/")
    assert out.status_code == 200 and out["Content-Type"].startswith("text/csv")
    text = out.content.decode()
    assert text.splitlines()[0] == "name,sku,barcode,category,kind,unit,price,currency,cost"
    assert "Lait,L1,,,product,piece,3.75,USD,2.50" in text
    acc = client_for_role(tenant_a, "accountant").get(P + "export/").content.decode()
    assert "cost" in acc.splitlines()[0]
    assert client_for_role(tenant_a, "cashier").get(P + "export/").status_code == 403
    assert client_for_role(tenant_a, "storekeeper").get(P + "export/").status_code == 403


def test_export_then_import_roundtrip(tenant_a, tenant_b):
    make_product(tenant_a, name="Savon", sku="S1", price_minor=1500, cost_minor=1000)
    text = tenant_a.client().get(P + "export/").content.decode()
    r = _upload(tenant_b.client(), text).json()
    assert r["created"] == 1 and r["failed"] == 0
