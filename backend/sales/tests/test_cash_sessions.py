"""Sessions de caisse : ouverture, doublon, clôture avec attendu/compté/écart par devise, permissions."""

import pytest

from core.tenancy import tenant_context
from sales.models import CashSession
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

pytestmark = pytest.mark.django_db


def open_session(client, handle, float_=None, store=None):
    body = {"store": str(store or handle.store.id)}
    if float_ is not None:
        body["opening_float"] = float_
    return client.post(SESSIONS + "open/", body, format="json")


def test_open_and_current(tenant_a):
    cashier = client_for_role(tenant_a, "cashier")
    assert cashier.get(SESSIONS + "current/", {"store": str(tenant_a.store.id)}).status_code == 404
    r = open_session(cashier, tenant_a, {"CDF": 10_000, "USD": 500})
    assert r.status_code == 201, r.data
    assert r.data["status"] == "open" and r.data["opening_float"] == {"CDF": 10_000, "USD": 500}
    assert r.data["expected"] == {"CDF": 10_000, "USD": 500}
    cur = cashier.get(SESSIONS + "current/", {"store": str(tenant_a.store.id)})
    assert cur.status_code == 200 and cur.data["id"] == r.data["id"]


def test_opening_float_defaults_to_zero(tenant_a):
    r = open_session(client_for_role(tenant_a, "cashier"), tenant_a)
    assert r.status_code == 201 and r.data["opening_float"] == {"CDF": 0, "USD": 0}


def test_duplicate_open_refused_per_cashier_and_store(tenant_a):
    cashier = client_for_role(tenant_a, "cashier")
    assert open_session(cashier, tenant_a).status_code == 201
    assert open_session(cashier, tenant_a).status_code == 409
    # un autre caissier ou une autre boutique : autorisé
    assert open_session(client_for_role(tenant_a, "manager"), tenant_a).status_code == 201
    assert open_session(cashier, tenant_a, store=make_store(tenant_a).id).status_code == 201


def test_open_validation(tenant_a):
    c = client_for_role(tenant_a, "cashier")
    assert open_session(c, tenant_a, {"CDF": -1}).status_code == 400
    assert open_session(c, tenant_a, {"CDF": 1.5}).status_code == 400
    assert open_session(c, tenant_a, {"EUR": 1}).status_code == 400
    assert c.post(SESSIONS + "open/", {"store": "11111111-1111-1111-1111-111111111111"},
                  format="json").status_code == 400


def test_reopen_after_close(tenant_a):
    cashier = client_for_role(tenant_a, "cashier")
    sid = open_session(cashier, tenant_a).data["id"]
    assert cashier.post(f"{SESSIONS}{sid}/close/", {"counted": {"CDF": 0, "USD": 0}}, format="json").status_code == 200
    assert open_session(cashier, tenant_a).status_code == 201


def test_cashier_cannot_sell_without_open_session(tenant_a):
    p = make_product(tenant_a, price_minor=1000, track_stock=False)
    cashier = client_for_role(tenant_a, "cashier")
    r = cashier.post(BASE, sale_body(tenant_a, [(p, 1)], [cash(1000)]), format="json")
    assert r.status_code == 400 and r.data["detail"] == "Ouvrez la caisse avant de vendre."
    # propriétaire et gérant vendent sans session
    assert tenant_a.client().post(BASE, sale_body(tenant_a, [(p, 1)], [cash(1000)]), format="json").status_code == 201
    manager = client_for_role(tenant_a, "manager")
    assert manager.post(BASE, sale_body(tenant_a, [(p, 1)], [cash(1000)]), format="json").status_code == 201


def test_sale_attached_to_open_session(tenant_a):
    p = make_product(tenant_a, price_minor=1000, track_stock=False)
    cashier = client_for_role(tenant_a, "cashier")
    sid = open_session(cashier, tenant_a).data["id"]
    r = cashier.post(BASE, sale_body(tenant_a, [(p, 1)], [cash(1000)]), format="json")
    assert r.status_code == 201 and str(r.data["cash_session"]) == str(sid)
    # une vente d'un autre utilisateur n'est pas rattachée à cette session
    other = tenant_a.client().post(BASE, sale_body(tenant_a, [(p, 1)], [cash(1000)]), format="json")
    assert other.data["cash_session"] is None


def test_sale_refused_after_session_closed(tenant_a):
    p = make_product(tenant_a, price_minor=1000, track_stock=False)
    cashier = client_for_role(tenant_a, "cashier")
    sid = open_session(cashier, tenant_a).data["id"]
    cashier.post(f"{SESSIONS}{sid}/close/", {"counted": {"CDF": 0, "USD": 0}}, format="json")
    assert cashier.post(BASE, sale_body(tenant_a, [(p, 1)], [cash(1000)]), format="json").status_code == 400


def test_close_expected_counted_difference_per_currency(tenant_a):
    make_rate(tenant_a, "2800")
    cust = make_customer(tenant_a)
    cdf_prod = make_product(tenant_a, sku="C1", price_minor=3000, track_stock=False)
    usd_prod = make_product(tenant_a, sku="U1", name="Cahier", price_minor=2800, track_stock=False)
    cashier = client_for_role(tenant_a, "cashier")
    sid = open_session(cashier, tenant_a, {"CDF": 10_000, "USD": 5000}).data["id"]

    def sell(body, client=cashier):
        r = client.post(BASE, body, format="json")
        assert r.status_code == 201, r.data
        return r.data

    # S1 : 3000 CDF payés 5000 -> monnaie 2000 CDF (net +3000 CDF)
    sell(sale_body(tenant_a, [(cdf_prod, 1)], [cash(5000)]))
    # S2 : vente en USD (1,00 $), payée 2,00 $ -> monnaie 1,00 $ (net +100 USD)
    sell(sale_body(tenant_a, [(usd_prod, 1)], [cash(200, "USD")], currency="USD"))
    # S3 : annulée, exclue
    s3 = sell(sale_body(tenant_a, [(cdf_prod, 1)], [cash(3000)]))
    assert tenant_a.client().post(f"{BASE}{s3['id']}/cancel/", {"reason": "Erreur"}, format="json").status_code == 200
    # S4 : crédit, 500 CDF encaissés seulement
    sell(sale_body(tenant_a, [(cdf_prod, 1)], [cash(500)], customer=str(cust.id), credit_remaining=True))

    live = cashier.get(SESSIONS + "current/").data
    assert live["sales_count"] == 3 and live["cash_sales"] == {"CDF": 3500, "USD": 100}

    r = cashier.post(f"{SESSIONS}{sid}/close/", {"counted": {"CDF": 13_000, "USD": 5100}}, format="json")
    assert r.status_code == 200, r.data
    d = r.data
    assert d["id"] == sid and d["status"] == "closed"
    assert d["opening_float"] == {"CDF": 10_000, "USD": 5000}
    assert d["expected"] == {"CDF": 13_500, "USD": 5100}
    assert d["counted"] == {"CDF": 13_000, "USD": 5100}
    assert d["difference"] == {"CDF": -500, "USD": 0}
    assert d["sales_count"] == 3 and d["cash_sales"] == {"CDF": 3500, "USD": 100}
    assert cashier.get(SESSIONS + "current/").status_code == 404
    for key in ("expected", "counted", "difference", "cash_sales"):
        assert all(type(v) is int for v in d[key].values())


def test_change_in_other_currency_counts_in_that_currency(tenant_a):
    make_rate(tenant_a, "2800")
    p = make_product(tenant_a, price_minor=10_000, track_stock=False)
    cashier = client_for_role(tenant_a, "cashier")
    sid = open_session(cashier, tenant_a, {"CDF": 0, "USD": 1000}).data["id"]
    # 10 000 CDF dus ; payé 5 USD (= 14 000 CDF) ; monnaie 4 000 CDF rendue en USD = 1,43 $ (143 c)
    r = cashier.post(BASE, sale_body(tenant_a, [(p, 1)], [cash(500, "USD")], change_currency="USD"),
                     format="json")
    assert r.status_code == 201 and r.data["change_minor"] == 143
    d = cashier.post(f"{SESSIONS}{sid}/close/", {"counted": {"CDF": 0, "USD": 1357}}, format="json").data
    assert d["expected"] == {"CDF": 0, "USD": 1000 + 500 - 143} and d["difference"] == {"CDF": 0, "USD": 0}


def test_close_requires_both_counted_currencies_and_open_session(tenant_a):
    cashier = client_for_role(tenant_a, "cashier")
    sid = open_session(cashier, tenant_a).data["id"]
    url = f"{SESSIONS}{sid}/close/"
    assert cashier.post(url, {"counted": {"CDF": 0}}, format="json").status_code == 400
    assert cashier.post(url, {"counted": {"CDF": -1, "USD": 0}}, format="json").status_code == 400
    assert cashier.post(url, {}, format="json").status_code == 400
    assert cashier.post(url, {"counted": {"CDF": 0, "USD": 0}}, format="json").status_code == 200
    assert cashier.post(url, {"counted": {"CDF": 0, "USD": 0}}, format="json").status_code == 409


def test_close_permissions(tenant_a):
    cashier = client_for_role(tenant_a, "cashier")
    sid = open_session(cashier, tenant_a).data["id"]
    body = {"counted": {"CDF": 0, "USD": 0}}
    url = f"{SESSIONS}{sid}/close/"
    for role in ("storekeeper", "accountant"):
        assert client_for_role(tenant_a, role).post(url, body, format="json").status_code == 403
    # un caissier ne clôture pas la session d'un autre (invisible : 404)
    other = client_for_role(tenant_a, "cashier", "autre")
    other_user_session = open_session(tenant_a.client(), tenant_a).data["id"]
    assert other.post(f"{SESSIONS}{other_user_session}/close/", body, format="json").status_code == 404
    # propriétaire/gérant clôturent n'importe quelle session
    assert client_for_role(tenant_a, "manager").post(url, body, format="json").status_code == 200
    assert tenant_a.client().post(f"{SESSIONS}{other_user_session}/close/", body, format="json").status_code == 200
    with tenant_context(tenant_a.tenant.id):
        assert CashSession.objects.get(pk=sid).closed_by.full_name == "Manager"


def test_list_permissions(tenant_a):
    cashier = client_for_role(tenant_a, "cashier")
    open_session(cashier, tenant_a)
    open_session(tenant_a.client(), tenant_a)
    assert tenant_a.client().get(SESSIONS).data["count"] == 2
    assert client_for_role(tenant_a, "manager").get(SESSIONS).data["count"] == 2
    assert cashier.get(SESSIONS).data["count"] == 1  # uniquement les siennes
    assert client_for_role(tenant_a, "storekeeper").get(SESSIONS).status_code == 403
    assert client_for_role(tenant_a, "accountant").get(SESSIONS).status_code == 403
    assert open_session(client_for_role(tenant_a, "storekeeper"), tenant_a).status_code == 403
    assert open_session(client_for_role(tenant_a, "accountant"), tenant_a).status_code == 403


def test_session_isolation(tenant_a, tenant_b):
    sid = open_session(tenant_b.client(), tenant_b).data["id"]
    a = tenant_a.client()
    assert a.get(SESSIONS).data["count"] == 0
    assert a.get(SESSIONS + "current/").status_code == 404
    assert a.post(f"{SESSIONS}{sid}/close/", {"counted": {"CDF": 0, "USD": 0}}, format="json").status_code == 404
    assert open_session(a, tenant_a, store=tenant_b.store.id).status_code == 400
