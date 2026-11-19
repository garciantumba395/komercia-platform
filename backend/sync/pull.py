"""Delta des données de référence pour le catalogue local de l'appareil (`GET pull/`).

Curseur : chaque flux (boutiques, catégories, produits, clients, niveaux de stock) avance
indépendamment selon l'ordre total (date de modification, identifiant). Le curseur est un JSON encodé
en base64 url-safe, opaque pour l'appareil : {"v": 1, "store": <uuid|null>, "s": {flux: [iso, id]}}.

Pagination sans oubli ni doublon : la page suivante reprend STRICTEMENT après le couple
(updated_at, id) de la dernière ligne servie (`updated_at > t OR (updated_at = t AND id > id)`), ce qui
reste exact quand plusieurs lignes partagent le même `updated_at`. `limit` s'applique à chaque flux ;
`has_more` est vrai tant qu'un flux a encore des lignes.

Visibilité à la validation : une ligne dont `updated_at` précède un `pull` mais dont la transaction se
valide après lui serait ratée. On ne sert donc que les lignes plus anciennes que `SYNC_PULL_LAG_SECONDS`
(défaut 2 s) ; l'appareil les recevra au passage suivant.

Un client change aussi quand son solde change (écritures de crédit, annulation d'une vente à crédit)
sans que sa fiche soit modifiée : sa date de synchronisation est le plus récent de `updated_at` et de
sa dernière écriture de crédit.
"""

from __future__ import annotations

import base64
import binascii
import json
from datetime import timedelta

from django.conf import settings
from django.db.models import F, OuterRef, Q, Subquery
from django.db.models.functions import Greatest
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from accounts.models import Store
from catalog.models import Category, Product
from currency.services import current_rate
from customers.models import CreditEntry, Customer
from customers.services import balances_by_customer
from stock.models import StockLevel

from .serializers import (
    SyncCategorySerializer,
    SyncCustomerSerializer,
    SyncProductSerializer,
    SyncStockLevelSerializer,
    SyncStoreSerializer,
)

DEFAULT_LIMIT = 500
MAX_LIMIT = 1000
STREAMS = ("stores", "categories", "products", "customers", "stock_levels")


class InvalidCursor(ValueError):
    pass


def pull_lag() -> timedelta:
    return timedelta(seconds=float(getattr(settings, "SYNC_PULL_LAG_SECONDS", 2)))


def encode_cursor(state: dict, store_id) -> str:
    raw = json.dumps({"v": 1, "store": str(store_id) if store_id else None, "s": state},
                     separators=(",", ":"), sort_keys=True)
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_cursor(cursor: str | None, store_id) -> dict:
    """Renvoie l'état {flux: (datetime, id)} ; lève InvalidCursor si le curseur est inutilisable."""
    if not cursor:
        return {}
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        obj = json.loads(raw)
        if obj.get("v") != 1:
            raise ValueError("version")
        if obj.get("store") != (str(store_id) if store_id else None):
            raise InvalidCursor("Ce curseur a été émis pour une autre boutique : repartez sans curseur.")
        state = {}
        for stream, (ts, pk) in obj["s"].items():
            if stream not in STREAMS:
                raise ValueError("flux")
            parsed = parse_datetime(ts)
            if parsed is None or not isinstance(pk, str):
                raise ValueError("valeur")
            state[stream] = (parsed, pk)
        return state
    except InvalidCursor:
        raise
    except (ValueError, TypeError, KeyError, AttributeError, binascii.Error):
        raise InvalidCursor("Curseur invalide.") from None


def _page(qs, ts_field: str, position, upper, limit: int):
    """Une page d'un flux : (lignes, a_encore, nouvelle_position)."""
    qs = qs.filter(**{f"{ts_field}__lte": upper})
    if position is not None:
        ts, pk = position
        qs = qs.filter(Q(**{f"{ts_field}__gt": ts}) | Q(**{ts_field: ts, "pk__gt": pk}))
    rows = list(qs.order_by(ts_field, "pk")[: limit + 1])
    more = len(rows) > limit
    rows = rows[:limit]
    new_position = (getattr(rows[-1], ts_field), str(rows[-1].pk)) if rows else position
    return rows, more, new_position


def _customer_queryset():
    last_entry = (
        CreditEntry.objects.filter(customer_id=OuterRef("pk")).order_by("-created_at").values("created_at")[:1]
    )
    return Customer.objects.annotate(sync_at=Greatest(F("updated_at"), Subquery(last_entry)))


def build_pull(*, since: str | None, store_id, limit: int, with_cost: bool) -> dict:
    state = decode_cursor(since, store_id)
    now = timezone.now()
    upper = now - pull_lag()

    stock_qs = StockLevel.objects.all()
    if store_id is not None:
        stock_qs = stock_qs.filter(store_id=store_id)

    plan = {
        "stores": (Store.objects.all(), "updated_at"),
        "categories": (Category.objects.all(), "updated_at"),
        "products": (Product.objects.all(), "updated_at"),
        "customers": (_customer_queryset(), "sync_at"),
        "stock_levels": (stock_qs, "updated_at"),
    }
    rows_by_stream, new_state, has_more = {}, {}, False
    for stream, (qs, ts_field) in plan.items():
        rows, more, position = _page(qs, ts_field, state.get(stream), upper, limit)
        rows_by_stream[stream] = rows
        has_more = has_more or more
        if position is not None:
            new_state[stream] = [position[0].isoformat(), position[1]]

    customers = rows_by_stream["customers"]
    balances = balances_by_customer([c.pk for c in customers]) if customers else {}
    rate = current_rate(store_id)
    changes = {
        "stores": SyncStoreSerializer(rows_by_stream["stores"], many=True).data,
        "categories": SyncCategorySerializer(rows_by_stream["categories"], many=True).data,
        "products": SyncProductSerializer(
            rows_by_stream["products"], many=True, context={"with_cost": with_cost}).data,
        "customers": SyncCustomerSerializer(customers, many=True, context={"balances": balances}).data,
        "stock_levels": SyncStockLevelSerializer(rows_by_stream["stock_levels"], many=True).data,
        "exchange_rate": None if rate is None else {
            "id": str(rate.id),
            "store": str(rate.store_id) if rate.store_id else None,
            "rate_cdf_per_usd": f"{rate.rate_cdf_per_usd:.4f}",
            "effective_at": rate.effective_at.isoformat(),
        },
    }
    return {
        "cursor": encode_cursor(new_state, store_id),
        "has_more": has_more,
        "server_time": now.isoformat(),
        "changes": changes,
    }
