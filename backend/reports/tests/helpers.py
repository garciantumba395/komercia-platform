import datetime
from zoneinfo import ZoneInfo

from core.tenancy import tenant_context
from sales.models import Sale
from sales.tests.helpers import BASE, make_product, sale_body

KIN = ZoneInfo("Africa/Kinshasa")
DAILY = "/api/v1/reports/daily/"
SUMMARY = "/api/v1/reports/summary/"
EXPORT = "/api/v1/reports/export/"


def at(day: str, hh=10, mm=0, ss=0) -> datetime.datetime:
    """Instant local (Africa/Kinshasa) du jour « AAAA-MM-JJ »."""
    y, m, d = map(int, day.split("-"))
    return datetime.datetime(y, m, d, hh, mm, ss, tzinfo=KIN)


def sell(handle, items, payments, when, *, cancel=False, store=None, **extra):
    """Crée une vente par l'API réelle (propriétaire), puis fixe sa date d'enregistrement."""
    body = sale_body(handle, items, payments, **extra)
    if store is not None:
        body["store"] = str(store.id)
    client = handle.client()
    r = client.post(BASE, body, format="json")
    assert r.status_code == 201, r.content
    sale_id = r.json()["id"]
    if cancel:
        c = client.post(f"{BASE}{sale_id}/cancel/", {"reason": "Erreur"}, format="json")
        assert c.status_code == 200, c.content
    with tenant_context(handle.tenant.id):
        Sale.objects.filter(id=sale_id).update(created_at=when)
    return sale_id


__all__ = ["make_product", "sell", "at", "DAILY", "SUMMARY", "EXPORT"]
