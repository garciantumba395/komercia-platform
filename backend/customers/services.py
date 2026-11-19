"""Services de crédit client, appelés par le module `sales` (signatures stables)."""

from collections import defaultdict
from datetime import date

from django.db import transaction
from django.db.models import F, Sum
from django.db.models.functions import Coalesce

from .models import KIND_CHARGE, KIND_PAYMENT, CreditEntry, Customer, PaymentAllocation


class CreditError(Exception):
    """Erreur métier de crédit ; `message` est affichable à l'utilisateur."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class CreditLimitExceeded(CreditError):
    pass


class PaymentExceedsDebt(CreditError):
    pass


class InvalidCreditOperation(CreditError):
    pass


def _check(amount_minor, currency):
    if isinstance(amount_minor, bool) or not isinstance(amount_minor, int) or amount_minor <= 0:
        raise InvalidCreditOperation("Le montant doit être un entier strictement positif.")
    if currency not in ("CDF", "USD"):
        raise InvalidCreditOperation("Devise inconnue.")


def get_balance(customer) -> dict[str, int]:
    """Dette ouverte par devise (charges - paiements). Ne contient que les devises avec écritures."""
    rows = (
        CreditEntry.objects.filter(customer=customer)
        .values("currency", "kind")
        .annotate(total=Sum("amount_minor"))
    )
    balance: dict[str, int] = defaultdict(int)
    for r in rows:
        balance[r["currency"]] += r["total"] if r["kind"] == KIND_CHARGE else -r["total"]
    return dict(balance)


def balances_by_customer(customer_ids=None) -> dict:
    """{customer_id: {devise: solde}} en une requête."""
    qs = CreditEntry.objects.all()
    if customer_ids is not None:
        qs = qs.filter(customer_id__in=customer_ids)
    out: dict = defaultdict(lambda: defaultdict(int))
    for r in qs.values("customer_id", "currency", "kind").annotate(total=Sum("amount_minor")):
        out[r["customer_id"]][r["currency"]] += r["total"] if r["kind"] == KIND_CHARGE else -r["total"]
    return {k: dict(v) for k, v in out.items()}


def open_charges(customer=None, currency=None):
    """Charges non soldées (plus ancienne d'abord), avec `open_minor` = reste dû."""
    qs = CreditEntry.objects.filter(kind=KIND_CHARGE)
    if customer is not None:
        qs = qs.filter(customer=customer)
    if currency is not None:
        qs = qs.filter(currency=currency)
    return (
        qs.annotate(allocated=Coalesce(Sum("allocations_received__amount_minor"), 0))
        .annotate(open_minor=F("amount_minor") - F("allocated"))
        .filter(open_minor__gt=0)
        .order_by("created_at", "id")
    )


@transaction.atomic
def charge_credit(customer, amount_minor, currency, *, sale_id=None, due_date=None, user=None,
                  override=False, limit_equivalent_minor=None) -> CreditEntry:
    _check(amount_minor, currency)
    customer = Customer.objects.select_for_update().get(pk=customer.pk)
    if not customer.is_active:
        raise InvalidCreditOperation("Ce client est inactif.")
    if not override:
        limit_ccy = customer.credit_limit_currency
        if currency == limit_ccy:
            charge_in_limit_ccy = amount_minor
        elif limit_equivalent_minor is not None:
            charge_in_limit_ccy = limit_equivalent_minor
        else:
            raise InvalidCreditOperation(
                "Devise différente de celle de la limite : fournir limit_equivalent_minor."
            )
        if customer.credit_limit_minor == 0:
            raise CreditLimitExceeded("Crédit refusé : aucune limite de crédit n'est accordée à ce client.")
        current = get_balance(customer).get(limit_ccy, 0)
        if current + charge_in_limit_ccy > customer.credit_limit_minor:
            raise CreditLimitExceeded(
                f"Limite de crédit dépassée : limite {customer.credit_limit_minor} {limit_ccy}, "
                f"dette actuelle {current}, vente {charge_in_limit_ccy}."
            )
    return CreditEntry.objects.create(
        customer=customer, kind=KIND_CHARGE, amount_minor=amount_minor, currency=currency,
        sale_id=sale_id, due_date=due_date, created_by=user,
    )


@transaction.atomic
def register_payment(customer, amount_minor, currency, *, method="cash", user=None,
                     note="") -> CreditEntry:
    """Encaisse une dette, imputée FIFO aux charges ouvertes de la même devise."""
    _check(amount_minor, currency)
    if method not in ("cash", "mobile_money", "other"):
        raise InvalidCreditOperation("Mode de paiement inconnu.")
    customer = Customer.objects.select_for_update().get(pk=customer.pk)
    charges = list(open_charges(customer, currency))
    owed = sum(c.open_minor for c in charges)
    if amount_minor > owed:
        raise PaymentExceedsDebt(
            f"Le paiement ({amount_minor} {currency}) dépasse la dette ouverte en {currency} "
            f"({owed} {currency}). Aucune conversion automatique."
        )
    payment = CreditEntry.objects.create(
        customer=customer, kind=KIND_PAYMENT, amount_minor=amount_minor, currency=currency,
        method=method, note=note, created_by=user,
    )
    remaining = amount_minor
    for charge in charges:
        if remaining == 0:
            break
        part = min(remaining, charge.open_minor)
        PaymentAllocation.objects.create(payment_entry=payment, charge_entry=charge, amount_minor=part)
        remaining -= part
    return payment


def is_overdue(due_date, today=None) -> bool:
    return due_date is not None and due_date < (today or date.today())
