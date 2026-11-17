"""Traitement d'un lot d'opérations rejouées par un appareil (`POST push/`).

Principes (cahier des charges 18.5) :
- chaque opération s'exécute dans SA transaction : un rejet n'annule pas les autres, et ce qui est
  appliqué reste appliqué même si la réponse HTTP est perdue (le rejeu renvoie « duplicate ») ;
- idempotence par (tenant, op_id) : verrou consultatif PostgreSQL sur (tenant, op_id) puis relecture
  du journal ; la contrainte d'unicité partielle reste le filet de sécurité ;
- les services métier existants (`sales`, `stock`) sont réutilisés tels quels, sans dupliquer leur logique ;
- l'heure de l'appareil n'est JAMAIS l'heure de la vente (`created_at` est celui du serveur) ;
- une vente déjà faite n'est jamais refusée pour stock insuffisant (stock négatif toléré, signalé).
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from django.db import IntegrityError, connection, transaction
from django.utils import timezone

from accounts.models import Store
from audit import services as audit
from catalog.models import Product
from core.permissions import CASHIER, MANAGER, OWNER
from core.tenancy import activate_tenant
from customers.models import Customer
from sales import services as sales
from sales.models import CashSession, Sale
from sales.serializers import (
    CancelInputSerializer,
    CloseSessionInputSerializer,
    OpenSessionInputSerializer,
    SaleInputSerializer,
)
from stock.models import StockLevel
from stock.serializers import MovementCreateSerializer
from stock.services import apply_movement

from .models import SyncOperation
from .serializers import CustomerSyncSerializer

logger = logging.getLogger(__name__)

CLOCK_SKEW_THRESHOLD = timedelta(minutes=10)
SENSITIVE_CUSTOMER_FIELDS = ("credit_limit_minor", "credit_limit_currency")
SENSITIVE_DEFAULTS = {"credit_limit_minor": 0, "credit_limit_currency": "CDF"}

ROLES_BY_TYPE = {
    "sale.create": (OWNER, MANAGER, CASHIER),
    "sale.cancel": (OWNER, MANAGER),
    "customer.create": (OWNER, MANAGER, CASHIER),
    "customer.update": (OWNER, MANAGER, CASHIER),
    "stock.movement": (OWNER, MANAGER),
    "cash_session.open": (OWNER, MANAGER, CASHIER),
    "cash_session.close": (OWNER, MANAGER, CASHIER),
}


class OpRejected(Exception):
    """Rejet métier d'une opération : `code` stable (lisible par la machine), `message` affichable."""

    def __init__(self, code: str, message: str, details: Any = None):
        super().__init__(message)
        self.code, self.message, self.details = code, message, details

    def as_error(self) -> dict:
        error = {"code": self.code, "message": self.message}
        if self.details is not None:
            error["details"] = self.details
        return error


@dataclass
class Operation:
    op_id: Any
    type: str
    device_time: datetime
    payload: dict
    received_at: datetime


@dataclass
class Outcome:
    entity_id: Any = None
    entity_version: datetime | None = None
    data: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)
    duplicate: bool = False  # l'entité existait déjà (rejeu sous un autre op_id)


def _warning(code: str, message: str, details: Any = None) -> dict:
    warning = {"code": code, "message": message}
    if details is not None:
        warning["details"] = details
    return warning


def _validation_error(serializer) -> OpRejected:
    return OpRejected("validation_error", "Données de l'opération invalides.", _plain(serializer.errors))


def _plain(errors):
    if isinstance(errors, dict):
        return {k: _plain(v) for k, v in errors.items()}
    if isinstance(errors, list):
        return [_plain(v) for v in errors]
    return str(errors)


def _error_codes(errors) -> set:
    if isinstance(errors, dict):
        return {c for v in errors.values() for c in _error_codes(v)}
    if isinstance(errors, list):
        return {c for v in errors for c in _error_codes(v)}
    return {getattr(errors, "code", None)}


def _opt_uuid(payload: dict, key: str):
    value = payload.get(key)
    if not value:
        return None
    try:
        return uuid.UUID(str(value))
    except ValueError:
        raise OpRejected("validation_error", f"« {key} » n'est pas un UUID valide.") from None


def _sale_error(exc: sales.SaleError) -> OpRejected:
    code = {403: "forbidden", 404: "not_found", 409: "conflict", 503: "temporary_error"}.get(
        exc.status, "business_rule")
    return OpRejected(code, exc.message)


# --------------------------------------------------------------------------- ventes


def _sale_create(op: Operation, user, role: str) -> Outcome:
    serializer = SaleInputSerializer(data=op.payload)
    if not serializer.is_valid():
        raise _validation_error(serializer)
    data = dict(serializer.validated_data)
    if data.get("device_time") is None:
        data["device_time"] = op.device_time

    if not Sale.objects.filter(client_id=data["client_id"]).exists():
        _check_sale_dependencies(data, user, role)
    try:
        sale, created = sales.create_sale(data, user=user, role=role)
    except sales.SaleError as exc:
        raise _sale_error(exc) from exc

    outcome = Outcome(
        entity_id=sale.id, duplicate=not created,
        data={"client_id": str(sale.client_id), "number": sale.number, "total_minor": sale.total_minor,
              "currency": sale.currency},
    )
    if created:
        outcome.warnings.extend(_negative_stock_warnings(sale.items.select_related("product"), sale.store_id))
    return outcome


def _check_sale_dependencies(data: dict, user, role: str) -> None:
    """Codes précis pour les cas que l'appareil doit pouvoir distinguer (références manquantes)."""
    if not Store.objects.filter(pk=data["store"], is_active=True).exists():
        raise OpRejected("store_not_found", "Boutique introuvable ou inactive.")
    if data.get("customer") and not Customer.objects.filter(pk=data["customer"]).exists():
        raise OpRejected("dependency_missing",
                         "Client introuvable : la création de ce client n'a pas été appliquée.")
    wanted = {i["product"] for i in data["items"]}
    products = {p.id: p for p in Product.objects.filter(id__in=wanted)}
    for product_id in wanted:
        product = products.get(product_id)
        if product is None:
            raise OpRejected("product_not_found", "Produit introuvable.", {"product": str(product_id)})
        if not product.is_active:
            raise OpRejected("product_inactive", f"Le produit « {product.name} » est inactif.",
                             {"product": str(product_id)})
    if role == CASHIER and not CashSession.objects.filter(
            store_id=data["store"], cashier=user, status=CashSession.OPEN).exists():
        raise OpRejected("dependency_missing",
                         "Aucune session de caisse ouverte : l'ouverture de caisse n'a pas été appliquée.")


def _negative_stock_warnings(items, store_id) -> list:
    """Un avertissement par produit dont le stock de la boutique est devenu négatif."""
    products = {i.product_id: i.product for i in items if i.product.track_stock}
    warnings = []
    levels = StockLevel.objects.filter(store_id=store_id, product_id__in=products, quantity__lt=0)
    for level in levels:
        product = products[level.product_id]
        warnings.append(_warning(
            "negative_stock", f"Stock négatif pour « {product.name} » ({level.quantity}).",
            {"product": str(product.id), "store": str(store_id), "quantity": str(level.quantity)}))
    return warnings


def _sale_cancel(op: Operation, user, role: str) -> Outcome:
    payload = op.payload
    serializer = CancelInputSerializer(data=payload)
    if not serializer.is_valid():
        raise _validation_error(serializer)
    lookup = {}
    for key, column in (("sale_id", "pk"), ("client_id", "client_id")):
        if payload.get(key):
            try:
                lookup[column] = uuid.UUID(str(payload[key]))
            except ValueError:
                raise OpRejected("validation_error", f"« {key} » n'est pas un UUID valide.") from None
            break
    if not lookup:
        raise OpRejected("validation_error", "« sale_id » ou « client_id » est obligatoire.")
    sale = Sale.objects.filter(**lookup).first()
    if sale is None:
        raise OpRejected("dependency_missing",
                         "Vente introuvable : sa création n'a pas été appliquée.")
    try:
        sale = sales.cancel_sale(sale.pk, user=user, reason=serializer.validated_data["reason"])
    except sales.SaleError as exc:
        raise _sale_error(exc) from exc
    return Outcome(entity_id=sale.id, data={"number": sale.number, "status": sale.status})


# --------------------------------------------------------------------------- sessions de caisse


def _cash_session_open(op: Operation, user, role: str) -> Outcome:
    serializer = OpenSessionInputSerializer(data=op.payload)
    if not serializer.is_valid():
        raise _validation_error(serializer)
    store_id = serializer.validated_data["store"]
    existing = CashSession.objects.filter(store_id=store_id, cashier=user, status=CashSession.OPEN).first()
    if existing is not None:
        raise OpRejected(
            "conflict", "Une session de caisse est déjà ouverte pour ce caissier dans cette boutique.",
            {"session_id": str(existing.id)})
    try:
        session = sales.open_session(
            store_id=store_id, user=user, opening_float=serializer.validated_data["opening_float"])
    except sales.SaleError as exc:
        raise _sale_error(exc) from exc
    return Outcome(entity_id=session.id, data={"store": str(session.store_id)})


def _cash_session_close(op: Operation, user, role: str) -> Outcome:
    payload = op.payload
    serializer = CloseSessionInputSerializer(data=payload)
    if not serializer.is_valid():
        raise _validation_error(serializer)
    session_id = _opt_uuid(payload, "session_id")
    open_op_id = _opt_uuid(payload, "open_op_id")
    store_id = _opt_uuid(payload, "store")
    if not session_id and open_op_id:
        opened = SyncOperation.objects.filter(
            op_id=open_op_id, type="cash_session.open", status=SyncOperation.APPLIED).first()
        if opened is None:
            raise OpRejected("dependency_missing", "L'ouverture de caisse référencée n'a pas été appliquée.")
        session_id = opened.entity_id
    if not session_id and store_id:
        current = CashSession.objects.filter(store_id=store_id, cashier=user, status=CashSession.OPEN).first()
        if current is None:
            raise OpRejected("dependency_missing", "Aucune session de caisse ouverte dans cette boutique.")
        session_id = current.id
    if not session_id:
        raise OpRejected("validation_error", "« session_id », « open_op_id » ou « store » est obligatoire.")
    try:
        session = sales.close_session(
            session_id, counted=serializer.validated_data["counted"], user=user,
            only_cashier=user if role == CASHIER else None)
    except sales.SaleError as exc:
        raise _sale_error(exc) from exc
    return Outcome(entity_id=session.id,
                   data={"expected": session.expected, "difference": session.difference})


# --------------------------------------------------------------------------- clients


def _sensitive_warning(payload: dict, current: dict) -> list:
    ignored = [f for f in SENSITIVE_CUSTOMER_FIELDS if f in payload and payload[f] != current[f]]
    if not ignored:
        return []
    return [_warning(
        "sensitive_field_ignored",
        "Champs sensibles ignorés : la limite de crédit ne se modifie pas par synchronisation.",
        {"fields": ignored})]


def _customer_create(op: Operation, user, role: str) -> Outcome:
    serializer = CustomerSyncSerializer(data=op.payload)
    if not serializer.is_valid():
        raise _validation_error(serializer)
    data = serializer.validated_data
    existing = Customer.objects.filter(pk=data["id"]).first()
    if existing is not None:
        return Outcome(entity_id=existing.id, entity_version=existing.updated_at, duplicate=True)
    fields = {k: data[k] for k in ("name", "phone", "address", "notes", "is_active") if k in data}
    try:
        with transaction.atomic():
            customer = Customer.objects.create(id=data["id"], **fields)
    except IntegrityError:  # identifiant déjà pris (par un autre tenant : on ne le révèle pas)
        raise OpRejected("conflict", "Cet identifiant de client est déjà utilisé.") from None
    audit.log("customer.create", "customer", customer.id, user=user, device_time=op.device_time,
              after=audit.snapshot(customer))
    return Outcome(entity_id=customer.id, entity_version=customer.updated_at,
                   warnings=_sensitive_warning(op.payload, SENSITIVE_DEFAULTS))


def _customer_is_stale(customer: Customer, op: Operation) -> bool:
    """Dernière modification gagne : l'opération perd si la fiche a été modifiée après elle.

    La date de modification de la fiche est celle du serveur ; or nos propres écritures de
    synchronisation la mettent à jour. On compare donc, si la dernière modification est une
    écriture de synchronisation, à l'heure appareil de cette écriture (ordre entre appareils) ;
    sinon (modification ailleurs, p. ex. interface web) à `updated_at`. Une heure appareil dans le
    futur est ramenée à l'heure de réception."""
    effective = min(op.device_time, op.received_at)
    last = (SyncOperation.objects
            .filter(entity_id=customer.id, status=SyncOperation.APPLIED,
                    type__in=("customer.create", "customer.update"))
            .order_by("-received_at", "-id").first())
    if last is not None and last.entity_version == customer.updated_at:
        reference = min(last.device_time, last.received_at)
    else:
        reference = customer.updated_at
    return effective < reference


def _customer_update(op: Operation, user, role: str) -> Outcome:
    serializer = CustomerSyncSerializer(data=op.payload, partial=True)
    if not serializer.is_valid():
        raise _validation_error(serializer)
    data = serializer.validated_data
    if "id" not in data:
        raise OpRejected("validation_error", "« id » est obligatoire.")
    customer = Customer.objects.select_for_update().filter(pk=data["id"]).first()
    if customer is None:
        raise OpRejected("dependency_missing", "Client introuvable : sa création n'a pas été appliquée.")
    warnings = _sensitive_warning(op.payload, {f: getattr(customer, f) for f in SENSITIVE_CUSTOMER_FIELDS})
    if _customer_is_stale(customer, op):
        raise OpRejected(
            "stale_update", "La fiche client a été modifiée plus récemment sur le serveur : "
            "la modification est ignorée (dernière modification gagne). Rechargez-la avec `pull`.",
            {"server_updated_at": customer.updated_at.isoformat()})
    changed = {k: data[k] for k in ("name", "phone", "address", "notes", "is_active")
               if k in data and getattr(customer, k) != data[k]}
    if changed:
        before = audit.snapshot(customer)
        for key, value in changed.items():
            setattr(customer, key, value)
        customer.save(update_fields=[*changed, "updated_at"])
        audit.log("customer.update", "customer", customer.id, user=user, device_time=op.device_time,
                  before=before, after=audit.snapshot(customer))
    return Outcome(entity_id=customer.id, entity_version=customer.updated_at, warnings=warnings)


# --------------------------------------------------------------------------- stock


def _stock_movement(op: Operation, user, role: str) -> Outcome:
    serializer = MovementCreateSerializer(data=op.payload)
    if not serializer.is_valid():
        codes = _error_codes(serializer.errors)
        if "does_not_exist" in codes:
            field_name = "product" if "product" in serializer.errors else "store"
            raise OpRejected(f"{field_name}_not_found",
                             "Produit introuvable." if field_name == "product" else "Boutique introuvable.",
                             _plain(serializer.errors))
        raise _validation_error(serializer)
    d = serializer.validated_data
    # Un mouvement déjà constaté sur l'appareil n'est jamais refusé pour stock insuffisant.
    movement = apply_movement(
        d["product"], d["store"], d["delta"], d["kind"], reason=d["reason"].strip(),
        reference=d["reference"], unit_cost_minor=d["unit_cost_minor"], user=user, allow_negative=True)
    audit.log("stock.movement", "stock_movement", movement.id, user=user, device_time=op.device_time,
              reason=d["reason"].strip(), after=audit.snapshot(movement))
    outcome = Outcome(entity_id=movement.id, data={"quantity": str(movement.quantity)})
    level = StockLevel.objects.filter(product=d["product"], store=d["store"]).first()
    if level is not None and level.quantity < 0:
        outcome.warnings.append(_warning(
            "negative_stock", f"Stock négatif pour « {d['product'].name} » ({level.quantity}).",
            {"product": str(d["product"].id), "store": str(d["store"].id), "quantity": str(level.quantity)}))
    return outcome


HANDLERS = {
    "sale.create": _sale_create,
    "sale.cancel": _sale_cancel,
    "customer.create": _customer_create,
    "customer.update": _customer_update,
    "stock.movement": _stock_movement,
    "cash_session.open": _cash_session_open,
    "cash_session.close": _cash_session_close,
}


# --------------------------------------------------------------------------- orchestration


def _clock_skew_warnings(op: Operation) -> list:
    skew = op.device_time - op.received_at
    if abs(skew) <= CLOCK_SKEW_THRESHOLD:
        return []
    minutes = int(skew.total_seconds() // 60)
    return [_warning(
        "clock_skew",
        "L'heure de l'appareil diffère de plus de 10 minutes de celle du serveur "
        "(l'heure du serveur fait foi pour la vente).",
        {"device_time": op.device_time.isoformat(), "server_time": op.received_at.isoformat(),
         "skew_minutes": minutes})]


def _lock(tenant_id, op_id) -> None:
    """Sérialise les traitements concurrents d'un même (tenant, op_id) jusqu'à la fin de la transaction."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", [f"sync:{tenant_id}:{op_id}"])


def _duplicate_result(row: SyncOperation) -> dict:
    stored = row.result or {}
    result = {"op_id": str(row.op_id), "status": "duplicate"}
    if row.entity_id:
        result["entity_id"] = str(row.entity_id)
    for key in ("data", "warnings"):
        if stored.get(key):
            result[key] = stored[key]
    return result


def process_operation(raw: dict, *, device_id, tenant_id, user, role: str) -> dict:
    """Traite UNE opération dans sa propre transaction et renvoie son résultat."""
    op = Operation(op_id=raw["op_id"], type=raw["type"], device_time=raw["device_time"],
                   payload=raw["payload"], received_at=timezone.now())
    skew = _clock_skew_warnings(op)
    try:
        with transaction.atomic():
            activate_tenant(tenant_id)
            _lock(tenant_id, op.op_id)
            applied = SyncOperation.objects.filter(op_id=op.op_id, status=SyncOperation.APPLIED).first()
            if applied is not None:
                return _duplicate_result(applied)
            handler = HANDLERS.get(op.type)
            if handler is None:
                raise OpRejected("unknown_type", f"Type d'opération inconnu : « {op.type} ».")
            if role not in ROLES_BY_TYPE[op.type]:
                raise OpRejected("forbidden", "Votre rôle n'autorise pas cette opération.")
            outcome = handler(op, user, role)
            warnings = [*outcome.warnings, *skew]
            stored = {}
            if outcome.data:
                stored["data"] = outcome.data
            if warnings:
                stored["warnings"] = warnings
            SyncOperation.objects.create(
                op_id=op.op_id, device_id=device_id, type=op.type, status=SyncOperation.APPLIED,
                entity_id=outcome.entity_id, entity_version=outcome.entity_version,
                payload=op.payload, result=stored, device_time=op.device_time, received_at=op.received_at)
            result = {"op_id": str(op.op_id), "status": "duplicate" if outcome.duplicate else "applied"}
            if outcome.entity_id:
                result["entity_id"] = str(outcome.entity_id)
            result.update(stored)
            return result
    except OpRejected as exc:
        error = exc
    except IntegrityError:
        # Course résiduelle sur la contrainte d'unicité : l'opération a été appliquée entre-temps.
        with transaction.atomic():
            activate_tenant(tenant_id)
            applied = SyncOperation.objects.filter(op_id=op.op_id, status=SyncOperation.APPLIED).first()
        if applied is None:
            error = OpRejected("temporary_error", "Conflit d'écriture temporaire : renvoyez l'opération.")
        else:
            return _duplicate_result(applied)
    except Exception:  # bug ou panne inattendue : le lot continue, l'opération est journalisée
        logger.exception("Erreur inattendue pendant la synchronisation de l'opération %s", op.op_id)
        error = OpRejected("internal_error", "Erreur interne : l'opération n'a pas été appliquée.")
    return _record_rejection(op, error, skew, device_id=device_id, tenant_id=tenant_id)


def _record_rejection(op: Operation, error: OpRejected, skew: list, *, device_id, tenant_id) -> dict:
    stored = {"error": error.as_error()}
    if skew:
        stored["warnings"] = skew
    with transaction.atomic():
        activate_tenant(tenant_id)
        SyncOperation.objects.create(
            op_id=op.op_id, device_id=device_id, type=op.type[:30], status=SyncOperation.REJECTED,
            payload=op.payload, result=stored, device_time=op.device_time, received_at=op.received_at)
    result = {"op_id": str(op.op_id), "status": "rejected", "error": error.as_error()}
    if skew:
        result["warnings"] = skew
    return result


def process_batch(*, device_id, operations: list, tenant_id, user, role: str) -> list:
    return [
        process_operation(raw, device_id=device_id, tenant_id=tenant_id, user=user, role=role)
        for raw in operations
    ]
