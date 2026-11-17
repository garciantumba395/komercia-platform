"""Rapports de base (KOM-060 à KOM-065) : agrégations SQL, aucun calcul sur float.

Décisions de calcul (documentées dans le README du module et le rapport de livraison) :

- Une « journée » est délimitée à minuit Africa/Kinshasa ; une vente appartient au jour de son
  ``created_at`` (heure d'enregistrement serveur), pas de ``device_time`` (heure appareil, non fiable).
- Seules les ventes ``completed`` comptent ; les annulées sont uniquement comptées (``cancelled_count``).
- Les devises ne sont jamais mélangées : chaque total est un dictionnaire ``{"CDF": int, "USD": int}``.
- ``cash_collected`` = paiements en espèces des ventes terminées, par devise de paiement, moins la
  monnaie rendue (``change_minor``) dans sa devise de rendu.
- ``estimated_margin`` (par devise de vente) = total des ventes (hors taxe, remises ligne et globale
  déjà déduites) moins le coût d'achat des lignes. Coût d'une ligne = quantité x ``cost_minor`` du produit
  (arrondi ROUND_HALF_UP à l'unité mineure de la devise du produit), converti dans la devise de la vente au
  taux figé de la vente via ``currency.money.convert_minor``. Les services et les produits sans coût
  (``cost_minor = 0``) comptent pour un coût de 0 : leur marge estimée est donc leur prix de vente.
- ``top_products`` : groupé par (produit, devise de vente), trié par chiffre d'affaires décroissant dans
  chaque devise (les montants de devises différentes ne sont pas convertis pour le classement).
- ``debts`` : dettes clients ouvertes de l'entreprise à l'instant de la requête (toutes boutiques :
  une dette n'est pas rattachée à une boutique).
"""

from __future__ import annotations

import datetime
from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

from django.db.models import Case, Count, DecimalField, F, Q, Sum, Value, When
from django.db.models.functions import TruncDate
from django.utils import timezone

from accounts.models import Store
from currency.money import convert_minor
from customers.models import KIND_CHARGE, CreditEntry
from sales.models import Payment, Sale, SaleItem
from stock.models import StockLevel

TZ = ZoneInfo("Africa/Kinshasa")
CURRENCY_CODES = ("CDF", "USD")
TOP_PRODUCTS_LIMIT = 10
MAX_RANGE_DAYS = 366


def zero() -> dict[str, int]:
    return dict.fromkeys(CURRENCY_CODES, 0)


def plain(value: Decimal) -> str:
    """Décimal en chaîne sans exposant ni zéros inutiles ("2", "2.5")."""
    return format(Decimal(value).normalize(), "f")


def today() -> datetime.date:
    return timezone.now().astimezone(TZ).date()


def day_bounds(date_from: datetime.date, date_to: datetime.date):
    """[début, fin[ aware : minuit local du premier jour -> minuit local du lendemain du dernier jour."""
    start = datetime.datetime.combine(date_from, datetime.time.min, tzinfo=TZ)
    end = datetime.datetime.combine(date_to + datetime.timedelta(days=1), datetime.time.min, tzinfo=TZ)
    return start, end


def _sales(start, end, store_id=None):
    qs = Sale.objects.filter(created_at__gte=start, created_at__lt=end)
    return qs.filter(store_id=store_id) if store_id else qs


def _by_currency(rows, key: str, value: str) -> dict[str, int]:
    out = zero()
    for r in rows:
        out[r[key]] = out.get(r[key], 0) + (r[value] or 0)
    return out


def _margin(completed, start, end, store_id) -> dict[str, int]:
    """Marge estimée par devise de vente (voir la docstring du module)."""
    revenue = completed.values("currency").annotate(total=Sum("total_minor"), tax=Sum("tax_minor"))
    margin = zero()
    for r in revenue:
        margin[r["currency"]] += r["total"] - r["tax"]

    items = SaleItem.objects.filter(
        sale__created_at__gte=start, sale__created_at__lt=end, sale__status=Sale.COMPLETED
    )
    if store_id:
        items = items.filter(sale__store_id=store_id)
    rate = Case(
        When(sale__currency=F("product__currency"), then=Value(None)),
        default=F("sale__rate_cdf_per_usd"),
        output_field=DecimalField(max_digits=14, decimal_places=4, null=True),
    )
    groups = (
        items.filter(product__cost_minor__gt=0)
        .annotate(conv_rate=rate)
        .values("sale__currency", "product__currency", "conv_rate", "product__cost_minor", "quantity")
        .annotate(n=Count("id"))
        .order_by()
    )
    for g in groups:
        # coût d'une ligne dans la devise du produit (arrondi à l'unité mineure), puis conversion
        raw_cost = g["quantity"] * g["product__cost_minor"]
        line_cost = int(raw_cost.quantize(Decimal(1), rounding=ROUND_HALF_UP))
        sale_ccy, prod_ccy = g["sale__currency"], g["product__currency"]
        if sale_ccy != prod_ccy and g["conv_rate"] is None:
            continue  # vente sans taux figé : conversion impossible, coût ignoré (ne doit pas arriver)
        converted = convert_minor(line_cost, prod_ccy, sale_ccy, g["conv_rate"]).amount_minor
        margin[sale_ccy] -= converted * g["n"]
    return margin


def _top_products(start, end, store_id) -> list[dict]:
    items = SaleItem.objects.filter(
        sale__created_at__gte=start, sale__created_at__lt=end, sale__status=Sale.COMPLETED
    )
    if store_id:
        items = items.filter(sale__store_id=store_id)
    rows = (
        items.values("product_id", "product__name", "sale__currency")
        .annotate(quantity=Sum("quantity"), revenue=Sum("line_total_minor"))
        .order_by("-revenue", "product__name", "product_id")[:TOP_PRODUCTS_LIMIT]
    )
    return [
        {
            "product": r["product_id"],
            "name": r["product__name"],
            "quantity": plain(r["quantity"]),
            "revenue_minor": r["revenue"],
            "currency": r["sale__currency"],
        }
        for r in rows
    ]


def _totals(start, end, store_id) -> dict:
    sales = _sales(start, end, store_id)
    completed = sales.filter(status=Sale.COMPLETED)
    rows = list(
        completed.values("currency").annotate(
            n=Count("id"), revenue=Sum("total_minor"), credit=Sum("credit_minor"))
    )
    cash = zero()
    pay_rows = (
        Payment.objects.filter(
            sale__created_at__gte=start,
            sale__created_at__lt=end,
            sale__status=Sale.COMPLETED,
            method=Payment.METHOD_CASH,
            **({"sale__store_id": store_id} if store_id else {}),
        )
        .values("currency")
        .annotate(total=Sum("amount_minor"))
        .order_by()
    )
    for r in pay_rows:
        cash[r["currency"]] += r["total"]
    change_rows = (
        completed.filter(change_minor__gt=0).values("change_currency").annotate(t=Sum("change_minor")).order_by()
    )
    for r in change_rows:
        cash[r["change_currency"]] -= r["t"]
    return {
        "sales_count": sum(r["n"] for r in rows),
        "revenue": _by_currency(rows, "currency", "revenue"),
        "cash_collected": cash,
        "credit_given": _by_currency(rows, "currency", "credit"),
        "estimated_margin": _margin(completed, start, end, store_id),
        "cancelled_count": sales.filter(status=Sale.CANCELLED).count(),
        "top_products": _top_products(start, end, store_id),
    }


def open_debts() -> dict[str, int]:
    """Somme des soldes clients ouverts par devise (charges - paiements), une seule requête."""
    out = zero()
    rows = CreditEntry.objects.values("currency").annotate(
        charges=Sum("amount_minor", filter=Q(kind=KIND_CHARGE)),
        payments=Sum("amount_minor", filter=~Q(kind=KIND_CHARGE)),
    )
    for r in rows:
        out[r["currency"]] = (r["charges"] or 0) - (r["payments"] or 0)
    return out


def low_stock(store_id=None) -> list[dict]:
    """Même critère que stock.LowStockAlertViewSet : produit suivi, quantité <= seuil minimum."""
    qs = StockLevel.objects.select_related("product").filter(
        product__track_stock=True, quantity__lte=F("product__min_stock")
    )
    if store_id:
        qs = qs.filter(store_id=store_id)
    return [
        {
            "product": lv.product_id,
            "name": lv.product.name,
            "store": lv.store_id,
            "quantity": plain(lv.quantity),
            "min_stock": plain(lv.product.min_stock),
        }
        for lv in qs.order_by("quantity", "id")
    ]


def daily_report(date: datetime.date, store_id=None) -> dict:
    start, end = day_bounds(date, date)
    data = _totals(start, end, store_id)
    return {
        "date": date.isoformat(),
        "store": store_id,
        "sales_count": data["sales_count"],
        "revenue": data["revenue"],
        "cash_collected": data["cash_collected"],
        "credit_given": data["credit_given"],
        "estimated_margin": data["estimated_margin"],
        "top_products": data["top_products"],
        "low_stock": low_stock(store_id),
        "debts": open_debts(),
        "cancelled_count": data["cancelled_count"],
    }


def summary_report(date_from: datetime.date, date_to: datetime.date, store_id=None) -> dict:
    start, end = day_bounds(date_from, date_to)
    data = _totals(start, end, store_id)
    completed = _sales(start, end, store_id).filter(status=Sale.COMPLETED)

    per_day: dict[datetime.date, dict] = {}
    day_rows = (
        completed.annotate(day=TruncDate("created_at", tzinfo=TZ))
        .values("day", "currency")
        .annotate(n=Count("id"), revenue=Sum("total_minor"))
        .order_by()
    )
    for r in day_rows:
        d = per_day.setdefault(r["day"], {"sales_count": 0, "revenue": zero()})
        d["sales_count"] += r["n"]
        d["revenue"][r["currency"]] += r["revenue"]
    by_day = []
    for i in range((date_to - date_from).days + 1):
        day = date_from + datetime.timedelta(days=i)
        d = per_day.get(day, {"sales_count": 0, "revenue": zero()})
        by_day.append({"date": day.isoformat(), "sales_count": d["sales_count"], "revenue": d["revenue"]})

    stores = Store.objects.filter(is_active=True)
    if store_id:
        stores = stores.filter(id=store_id)
    per_store: dict = defaultdict(lambda: {"sales_count": 0, "revenue": zero()})
    store_rows = (
        completed.values("store_id", "currency")
        .annotate(n=Count("id"), revenue=Sum("total_minor"))
        .order_by()
    )
    for r in store_rows:
        s = per_store[r["store_id"]]
        s["sales_count"] += r["n"]
        s["revenue"][r["currency"]] += r["revenue"]
    by_store = []
    known = set()
    for st in stores.order_by("name", "id"):
        known.add(st.id)
        s = per_store.get(st.id, {"sales_count": 0, "revenue": zero()})
        by_store.append({"store": st.id, "name": st.name, **s})
    # boutique désactivée mais ayant des ventes sur la période : on la garde dans la comparaison
    missing = [sid for sid in per_store if sid not in known]
    if missing:
        for st in Store.objects.filter(id__in=missing).order_by("name", "id"):
            by_store.append({"store": st.id, "name": st.name, **per_store[st.id]})

    return {
        "from": date_from.isoformat(),
        "to": date_to.isoformat(),
        "sales_count": data["sales_count"],
        "revenue": data["revenue"],
        "cash_collected": data["cash_collected"],
        "credit_given": data["credit_given"],
        "estimated_margin": data["estimated_margin"],
        "by_day": by_day,
        "top_products": data["top_products"],
        "by_store": by_store,
    }
