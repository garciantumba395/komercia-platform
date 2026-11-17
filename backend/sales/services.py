"""Services du module `sales` : création idempotente d'une vente, annulation, sessions de caisse.

Principes (cahier des charges 18.1, 18.2, 18.5) :
- tout se passe dans UNE transaction atomique ; une erreur annule tout (stock, crédit, numéro) ;
- idempotence par (tenant, client_id) : le compteur de la boutique est verrouillé AVANT de relire
  l'existence de la vente, ce qui sérialise deux requêtes simultanées de la même boutique ; la
  contrainte d'unicité reste le filet de sécurité ;
- le taux de change est figé une seule fois par vente ;
- numéro de vente : `<préfixe boutique>-<séquence sur 6 chiffres>` (voir `StoreCounter`), sans trou.
"""

from __future__ import annotations

import hashlib
import json
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone

from accounts.models import Store, Tenant
from audit import services as audit
from catalog.models import Product
from core.permissions import CASHIER, MANAGER, OWNER
from core.tenancy import get_current_tenant_id
from currency.money import compute_payment, convert_minor, format_minor
from currency.services import NoExchangeRate, snapshot
from customers import services as credit
from customers.models import KIND_CHARGE, KIND_PAYMENT, CreditEntry, Customer, PaymentAllocation
from stock.services import apply_movement

from .models import CashSession, Payment, Sale, SaleItem, StoreCounter

CURRENCY_CODES = ("CDF", "USD")
NO_RATE_MESSAGE = "Aucun taux de change défini : définissez le taux CDF/USD avant cette vente."
DEFAULT_CASHIER_MAX_DISCOUNT_PERCENT = 10


class SaleError(Exception):
    """Erreur métier : `message` est affichable, `status` est le code HTTP à renvoyer."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


# --------------------------------------------------------------------------- numérotation


def _lock_counter(store) -> StoreCounter:
    """Compteur de la boutique, verrouillé (FOR UPDATE) jusqu'à la fin de la transaction."""
    qs = StoreCounter.objects.select_for_update().filter(store=store)
    counter = qs.first()
    for _ in range(8):
        if counter is not None:
            return counter
        n = StoreCounter.objects.count() + 1
        taken = set(StoreCounter.objects.values_list("prefix", flat=True))
        while f"B{n:02d}" in taken:
            n += 1
        try:
            with transaction.atomic():
                StoreCounter.objects.create(store=store, prefix=f"B{n:02d}")
        except IntegrityError:  # créé en parallèle (même boutique ou même préfixe) : on relit
            pass
        counter = qs.first()
    raise SaleError("Impossible d'attribuer un numéro de vente, réessayez.", 503)


# --------------------------------------------------------------------------- création


def _fingerprint(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _plain(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _normalized_request(data: dict, currency: str, change_currency: str) -> dict:
    """Contenu métier (hors client_id, horloge appareil et override) pour comparer deux rejeux."""
    return {
        "store": str(data["store"]),
        "currency": currency,
        "customer": str(data["customer"]) if data.get("customer") else None,
        "items": [
            {
                "product": str(i["product"]),
                "quantity": _plain(i["quantity"]),
                "unit_price_minor": i.get("unit_price_minor"),
                "discount_minor": i.get("discount_minor", 0),
            }
            for i in data["items"]
        ],
        "discount_minor": data.get("discount_minor", 0),
        "payments": [
            {"method": p["method"], "amount_minor": p["amount_minor"], "currency": p["currency"]}
            for p in data.get("payments", [])
        ],
        "credit_remaining": bool(data.get("credit_remaining")),
        "change_currency": change_currency,
    }


def _replay(existing: Sale, fingerprint: str) -> tuple[Sale, bool]:
    if existing.request_hash != fingerprint:
        raise SaleError(
            "Ce client_id a déjà été utilisé pour une vente au contenu différent.", status=409
        )
    return existing, False


def _round_line(quantity: Decimal, unit_price_minor: int) -> int:
    return int((quantity * unit_price_minor).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _cashier_max_percent() -> Decimal:
    return Decimal(str(getattr(settings, "SALES_CASHIER_MAX_DISCOUNT_PERCENT",
                               DEFAULT_CASHIER_MAX_DISCOUNT_PERCENT)))


def create_sale(data: dict, *, user, role: str) -> tuple[Sale, bool]:
    """Crée une vente ; renvoie (vente, créée). `créée` est False si le client_id existait déjà
    (rejeu idempotent : rien n'est refait). Lève `SaleError` pour toute erreur métier."""
    with transaction.atomic():
        return _create_sale(data, user=user, role=role)


def _create_sale(data: dict, *, user, role: str) -> tuple[Sale, bool]:
    tenant_id = get_current_tenant_id()
    tenant = Tenant.objects.get(pk=tenant_id)
    currency = data.get("currency") or tenant.base_currency
    change_currency = data.get("change_currency") or currency
    fingerprint = _fingerprint(_normalized_request(data, currency, change_currency))

    # Chemin rapide : rejeu d'une vente déjà enregistrée.
    existing = Sale.objects.filter(client_id=data["client_id"]).first()
    if existing is not None:
        return _replay(existing, fingerprint)

    store = Store.objects.filter(pk=data["store"], is_active=True).first()
    if store is None:
        raise SaleError("Boutique introuvable.")

    counter = _lock_counter(store)
    # Relecture après verrou : une requête concurrente identique a pu valider entre-temps.
    existing = Sale.objects.filter(client_id=data["client_id"]).first()
    if existing is not None:
        return _replay(existing, fingerprint)

    customer = None
    if data.get("customer"):
        customer = Customer.objects.filter(pk=data["customer"]).first()
        if customer is None:
            raise SaleError("Client introuvable.")
        if not customer.is_active:
            raise SaleError("Ce client est inactif.")

    # Taux figé une seule fois ; une absence de taux n'est une erreur que si une conversion est requise.
    try:
        snap = snapshot(store.id)
    except NoExchangeRate:
        snap = None
    rate = snap["rate"] if snap else None
    rate_id = snap["rate_id"] if snap else None

    def convert(amount: int, src: str, dst: str) -> int:
        if src == dst:
            return amount
        if rate is None:
            raise SaleError(NO_RATE_MESSAGE)
        return convert_minor(amount, src, dst, rate).amount_minor

    # --- lignes
    products = {p.id: p for p in Product.objects.filter(id__in={i["product"] for i in data["items"]})}
    lines = []
    gross = 0
    line_discounts = 0
    for position, item in enumerate(data["items"]):
        product = products.get(item["product"])
        if product is None:
            raise SaleError("Produit introuvable.")
        if not product.is_active:
            raise SaleError(f"Le produit « {product.name} » est inactif et ne peut pas être vendu.")
        catalog_price = convert(product.price_minor, product.currency, currency)
        unit_price = item.get("unit_price_minor")
        if unit_price is None:
            unit_price = catalog_price
        elif unit_price != catalog_price and role not in (OWNER, MANAGER):
            raise SaleError("Modification de prix non autorisée pour votre rôle.", status=403)
        discount = item.get("discount_minor", 0)
        line_gross = _round_line(item["quantity"], unit_price)
        if discount > line_gross:
            raise SaleError(f"La remise de la ligne « {product.name} » dépasse son montant.")
        gross += line_gross
        line_discounts += discount
        lines.append({"product": product, "quantity": item["quantity"], "unit_price": unit_price,
                      "discount": discount, "total": line_gross - discount, "position": position})

    subtotal = sum(line["total"] for line in lines)
    global_discount = data.get("discount_minor", 0)
    if global_discount > subtotal:
        raise SaleError("La remise globale dépasse le sous-total.")
    total = subtotal - global_discount
    total_discount = line_discounts + global_discount

    if total_discount > 0 and role == CASHIER:
        if total_discount * 100 > _cashier_max_percent() * gross:
            raise SaleError("Remise supérieure à votre autorisation.", status=403)

    # --- session de caisse (verrouillée : une clôture concurrente attend la fin de cette vente)
    cash_session = (
        CashSession.objects.select_for_update()
        .filter(store=store, cashier=user, status=CashSession.OPEN)
        .first()
    )
    if cash_session is None and role == CASHIER:
        raise SaleError("Ouvrez la caisse avant de vendre.")

    # --- paiements
    payments = data.get("payments", [])
    for p in payments:
        if p["method"] != "cash":
            raise SaleError("Ce mode de paiement sera disponible au jalon M2 : seul « cash » est accepté.")
        if p["currency"] != currency and rate is None:
            raise SaleError(NO_RATE_MESSAGE)
    pairs = [(p["amount_minor"], p["currency"]) for p in payments]
    result = compute_payment(total, currency, pairs, rate, currency)
    if result.overpaid_minor and change_currency != currency:
        if rate is None:
            raise SaleError(NO_RATE_MESSAGE)
        result = compute_payment(total, currency, pairs, rate, change_currency)

    credit_minor = 0
    if result.remaining_minor > 0:
        if not data.get("credit_remaining"):
            raise SaleError(
                f"Paiement insuffisant : il manque {format_minor(result.remaining_minor, currency)}."
            )
        if customer is None:
            raise SaleError("Un client est obligatoire pour mettre le reste à crédit.")
        credit_minor = result.remaining_minor

    # --- écriture de la vente
    counter.last_number += 1
    counter.save(update_fields=["last_number", "updated_at"])
    number = f"{counter.prefix}-{counter.last_number:06d}"
    try:
        with transaction.atomic():
            sale = Sale.objects.create(
                client_id=data["client_id"], number=number, request_hash=fingerprint,
                store=store, customer=customer, cashier=user, cash_session=cash_session,
                currency=currency, subtotal_minor=subtotal, discount_minor=global_discount,
                tax_minor=0, total_minor=total, rate_cdf_per_usd=rate, rate_id=rate_id,
                paid_minor=result.paid_minor, change_minor=result.change_minor,
                change_currency=change_currency, credit_minor=credit_minor,
                device_time=data.get("device_time"),
            )
    except IntegrityError:
        existing = Sale.objects.filter(client_id=data["client_id"]).first()
        if existing is None:
            raise
        return _replay(existing, fingerprint)

    SaleItem.objects.bulk_create([
        SaleItem(tenant_id=tenant_id, sale=sale, product=line["product"], name=line["product"].name,
                 quantity=line["quantity"], unit_price_minor=line["unit_price"],
                 discount_minor=line["discount"], line_total_minor=line["total"],
                 position=line["position"])
        for line in lines
    ])
    Payment.objects.bulk_create([
        Payment(tenant_id=tenant_id, sale=sale, method="cash", amount_minor=p["amount_minor"],
                currency=p["currency"], rate_cdf_per_usd=rate, rate_id=rate_id, position=i)
        for i, p in enumerate(payments)
    ])

    # --- stock : une vente déjà faite n'est jamais refusée pour stock insuffisant (18.4)
    for line in sorted(lines, key=lambda x: str(x["product"].id)):
        apply_movement(line["product"], store, -line["quantity"], "sale", reference=number,
                       user=user, allow_negative=True)

    # --- crédit
    if credit_minor:
        limit_equivalent = None
        if customer.credit_limit_currency != currency:
            limit_equivalent = convert(credit_minor, currency, customer.credit_limit_currency)
        override = bool(data.get("override_credit_limit")) and role in (OWNER, MANAGER)
        try:
            credit.charge_credit(customer, credit_minor, currency, sale_id=sale.id, user=user,
                                 override=override, limit_equivalent_minor=limit_equivalent)
        except credit.CreditError as exc:
            raise SaleError(exc.message) from exc

    if total_discount > 0:
        audit.log("sale.discount", "sale", sale.id, user=user, device_time=data.get("device_time"),
                  after={"number": number, "currency": currency, "line_discounts_minor": line_discounts,
                         "global_discount_minor": global_discount, "total_discount_minor": total_discount,
                         "gross_minor": gross, "total_minor": total})
    return sale, True


# --------------------------------------------------------------------------- annulation


def cancel_sale(sale_id, *, user, reason: str) -> Sale:
    """Annule une vente par contre-passation (la vente n'est jamais supprimée).

    - stock : mouvement de kind « return » par ligne suivie en stock ;
    - crédit : le module `customers` n'expose pas d'opération d'annulation, et `register_payment`
      refuse le mode « cancellation » et imputerait FIFO (peut-être sur une AUTRE dette). On crée donc,
      sous verrou du client, une écriture de grand livre « payment » (method « other », note
      « Annulation de la vente <n°> ») imputée EXACTEMENT sur la charge de cette vente. Si le client
      a déjà payé une partie de cette dette, l'annulation est refusée (409) : rembourser d'abord ;
    - audit : action « sale.cancel ».
    """
    reason = (reason or "").strip()
    if not reason:
        raise SaleError("Le motif d'annulation est obligatoire.")
    with transaction.atomic():
        sale = Sale.objects.select_for_update().filter(pk=sale_id).first()
        if sale is None:
            raise SaleError("Vente introuvable.", 404)
        if sale.status == Sale.CANCELLED:
            raise SaleError("Cette vente est déjà annulée.", 409)

        if sale.credit_minor > 0:
            _cancel_credit(sale, user)

        items = list(sale.items.select_related("product"))
        for item in sorted(items, key=lambda i: str(i.product_id)):
            apply_movement(item.product, sale.store, item.quantity, "return", reference=sale.number,
                           reason=f"Annulation de la vente {sale.number}", user=user, allow_negative=True)

        before = {"status": sale.status}
        sale.status = Sale.CANCELLED
        sale.cancelled_at = timezone.now()
        sale.cancelled_by = user
        sale.cancel_reason = reason
        sale.save(update_fields=["status", "cancelled_at", "cancelled_by", "cancel_reason", "updated_at"])
        audit.log("sale.cancel", "sale", sale.id, user=user, reason=reason, before=before,
                  after={"status": sale.status, "number": sale.number, "total_minor": sale.total_minor,
                         "currency": sale.currency, "credit_minor": sale.credit_minor})
        return sale


def _cancel_credit(sale: Sale, user) -> None:
    customer = Customer.objects.select_for_update().get(pk=sale.customer_id)
    charge = CreditEntry.objects.filter(sale_id=sale.id, kind=KIND_CHARGE).first()
    if charge is None:
        return
    paid = (PaymentAllocation.objects.filter(charge_entry=charge)
            .aggregate(t=Sum("amount_minor"))["t"] or 0)
    if paid:
        raise SaleError(
            f"Le client a déjà payé {format_minor(paid, charge.currency)} sur cette vente à crédit : "
            "remboursez-le d'abord avant d'annuler la vente.", status=409)
    entry = CreditEntry.objects.create(
        customer=customer, kind=KIND_PAYMENT, amount_minor=charge.amount_minor,
        currency=charge.currency, sale_id=sale.id, method="other",
        note=f"Annulation de la vente {sale.number}", created_by=user,
    )
    PaymentAllocation.objects.create(payment_entry=entry, charge_entry=charge,
                                     amount_minor=charge.amount_minor)


# --------------------------------------------------------------------------- sessions de caisse


def _check_amounts(raw, label: str, *, required: bool) -> dict:
    if not isinstance(raw, dict):
        raise SaleError(f"« {label} » doit contenir un montant entier par devise (CDF, USD).")
    out = {}
    for code in CURRENCY_CODES:
        if code not in raw:
            if required:
                raise SaleError(f"« {label} » : montant {code} manquant.")
            out[code] = 0
            continue
        value = raw[code]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise SaleError(f"« {label} » : le montant {code} doit être un entier positif ou nul.")
        out[code] = value
    unknown = set(raw) - set(CURRENCY_CODES)
    if unknown:
        raise SaleError(f"« {label} » : devise inconnue ({', '.join(sorted(unknown))}).")
    return out


def open_session(*, store_id, user, opening_float) -> CashSession:
    float_ = _check_amounts(opening_float or {}, "Fonds initial", required=False)
    with transaction.atomic():
        store = Store.objects.filter(pk=store_id, is_active=True).first()
        if store is None:
            raise SaleError("Boutique introuvable.")
        if CashSession.objects.filter(store=store, cashier=user, status=CashSession.OPEN).exists():
            raise SaleError("Une session de caisse est déjà ouverte pour ce caissier dans cette boutique.",
                            status=409)
        try:
            with transaction.atomic():
                return CashSession.objects.create(store=store, cashier=user, opening_float=float_)
        except IntegrityError:  # course avec une autre ouverture simultanée
            raise SaleError("Une session de caisse est déjà ouverte pour ce caissier dans cette boutique.",
                            status=409) from None


def session_totals(session: CashSession) -> dict:
    """Ventes non annulées de la session : nombre et espèces nettes (encaissées − rendu) par devise."""
    sales = Sale.objects.filter(cash_session=session, status=Sale.COMPLETED)
    cash = dict.fromkeys(CURRENCY_CODES, 0)
    rows = (Payment.objects.filter(sale__in=sales, method="cash")
            .values("currency").annotate(total=Sum("amount_minor")))
    for row in rows:
        cash[row["currency"]] += row["total"]
    for row in sales.filter(change_minor__gt=0).values("change_currency").annotate(t=Sum("change_minor")):
        cash[row["change_currency"]] -= row["t"]
    float_ = {c: session.opening_float.get(c, 0) for c in CURRENCY_CODES}
    return {
        "sales_count": sales.count(),
        "cash_sales": cash,
        "expected": {c: float_[c] + cash[c] for c in CURRENCY_CODES},
    }


def close_session(session_id, *, counted, user, only_cashier=None) -> CashSession:
    """Clôture : attendu = fonds initial + espèces encaissées − monnaie rendue, par devise (ventes
    annulées exclues) ; écart = compté − attendu. Le verrou de la session sérialise avec les ventes."""
    counted_ = _check_amounts(counted, "Montant compté", required=True)
    with transaction.atomic():
        qs = CashSession.objects.select_for_update()
        if only_cashier is not None:
            qs = qs.filter(cashier=only_cashier)
        session = qs.filter(pk=session_id).first()
        if session is None:
            raise SaleError("Session de caisse introuvable.", 404)
        if session.status == CashSession.CLOSED:
            raise SaleError("Cette session de caisse est déjà clôturée.", 409)
        totals = session_totals(session)
        session.status = CashSession.CLOSED
        session.closed_at = timezone.now()
        session.closed_by = user
        session.counted = counted_
        session.expected = totals["expected"]
        session.difference = {c: counted_[c] - totals["expected"][c] for c in CURRENCY_CODES}
        session.save()
        return session

