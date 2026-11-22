from core.tenancy import tenant_context
from sales.tests.helpers import cash, make_product, make_rate, sale_body
from stock.services import apply_movement

TABLES = ["accounts_tenant", "accounts_user", "catalog_product", "sales_sale", "sales_saleitem",
          "sales_payment", "stock_stockmovement", "django_migrations"]


def make_data(handle, sales=3):
    """Quelques produits et ventes réels (tables sous RLS) ; renvoie la liste des ventes."""
    make_rate(handle)
    product = make_product(handle, price_minor=1500)
    with tenant_context(handle.tenant.id):
        apply_movement(product, handle.store, 100, "in")
    client = handle.client()
    for _ in range(sales):
        r = client.post("/api/v1/sales/", sale_body(handle, [(product, 2)], [cash(3000)]), format="json")
        assert r.status_code == 201, r.content
    return product


def add_sale(handle, product):
    r = handle.client().post("/api/v1/sales/", sale_body(handle, [(product, 1)], [cash(1500)]), format="json")
    assert r.status_code == 201, r.content
