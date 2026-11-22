"""Calculs monétaires purs (sans base de données ni float).

Conventions (cahier des charges, section 18.1) :
- un montant est un entier dans la plus petite unité de la devise (CDF : franc ; USD : centime) ;
- le taux est exprimé en CDF pour 1 USD (``Decimal``) ;
- arrondi ROUND_HALF_UP : CDF à l'unité, USD au centime ; l'écart d'arrondi est toujours retourné.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation, localcontext

# Précision de calcul très large : aucun montant réaliste ne peut être tronqué.
_PRECISION = 80
# Les valeurs exactes et écarts exposés sont ramenés à 12 décimales (lisibles, stockables) ; les
# décisions d'arrondi, elles, sont prises sur la valeur en pleine précision.
_EXPOSED = Decimal(1).scaleb(-12)


def _expose(value: Decimal) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = _PRECISION
        return value.quantize(_EXPOSED, rounding=ROUND_HALF_UP)


class MoneyError(ValueError):
    """Erreur de calcul monétaire (entrée invalide)."""


class UnsupportedCurrency(MoneyError):
    pass


@dataclass(frozen=True)
class Currency:
    code: str
    decimals: int
    symbol: str
    name: str
    symbol_before: bool = False  # "$12.50" (True) ou "12 500 FC" (False)
    thousands_sep: str = " "
    decimal_sep: str = "."

    @property
    def factor(self) -> int:
        """Nombre d'unités mineures dans une unité majeure (CDF : 1 ; USD : 100)."""
        return 10**self.decimals


CDF = Currency("CDF", 0, "FC", "Franc congolais", symbol_before=False, thousands_sep=" ")
USD = Currency("USD", 2, "$", "Dollar américain", symbol_before=True, thousands_sep=",")

CURRENCIES: dict[str, Currency] = {c.code: c for c in (CDF, USD)}

# Paire de conversion supportée : le taux vaut « RATE_QUOTE par 1 RATE_BASE ».
RATE_BASE = "USD"
RATE_QUOTE = "CDF"


def get_currency(currency: Currency | str) -> Currency:
    if isinstance(currency, Currency):
        return currency
    try:
        return CURRENCIES[currency]
    except (KeyError, TypeError):
        raise UnsupportedCurrency(f"Devise non supportée : {currency!r}") from None


def _to_decimal(value) -> Decimal:
    if isinstance(value, float) or isinstance(value, bool):
        raise TypeError("Les float sont interdits pour les montants : utiliser str, int ou Decimal.")
    if isinstance(value, Decimal):
        dec = value
    elif isinstance(value, int | str):
        try:
            dec = Decimal(value.strip() if isinstance(value, str) else value)
        except InvalidOperation:
            raise MoneyError(f"Valeur numérique invalide : {value!r}") from None
    else:
        raise TypeError(f"Type non supporté pour un montant : {type(value).__name__}")
    if not dec.is_finite():
        raise MoneyError(f"Valeur non finie : {value!r}")
    return dec


def _round_half_up(value: Decimal) -> int:
    with localcontext() as ctx:
        ctx.prec = _PRECISION
        return int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _check_minor(value, label: str = "montant") -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"Le {label} doit être un entier (unité mineure), reçu {type(value).__name__}.")
    return value


def check_rate(rate) -> Decimal:
    """Valide un taux (CDF par USD) : Decimal/str/int fini et strictement positif."""
    dec = _to_decimal(rate)
    if dec <= 0:
        raise MoneyError("Le taux de change doit être strictement positif.")
    return dec


def to_minor(value: str | Decimal, currency: Currency | str) -> int:
    """Montant en unité majeure (« 12.50 ») -> entier en unité mineure (1250), ROUND_HALF_UP."""
    cur = get_currency(currency)
    dec = _to_decimal(value)
    with localcontext() as ctx:
        ctx.prec = _PRECISION
        return _round_half_up(dec * cur.factor)


def from_minor(minor: int, currency: Currency | str) -> Decimal:
    """Entier en unité mineure -> Decimal en unité majeure (1250 USD -> Decimal('12.50'))."""
    cur = get_currency(currency)
    _check_minor(minor)
    with localcontext() as ctx:
        ctx.prec = _PRECISION
        return (Decimal(minor) / cur.factor).quantize(Decimal(1).scaleb(-cur.decimals))


def format_minor(minor: int, currency: Currency | str) -> str:
    """Affichage : 12500 CDF -> « 12 500 FC » ; 1250 USD -> « $12.50 »."""
    cur = get_currency(currency)
    _check_minor(minor)
    sign = "-" if minor < 0 else ""
    major = from_minor(abs(minor), cur)
    integer_part, _, frac = f"{major:f}".partition(".")
    grouped = f"{int(integer_part):,}".replace(",", cur.thousands_sep)
    number = grouped + (cur.decimal_sep + frac if cur.decimals else "")
    if cur.symbol_before:
        return f"{sign}{cur.symbol}{number}"
    return f"{sign}{number} {cur.symbol}"


@dataclass(frozen=True)
class ConversionResult:
    amount_minor: int  # montant arrondi, dans l'unité mineure de la devise cible
    exact: Decimal  # valeur exacte avant arrondi, même unité
    rounding_diff_minor: Decimal  # amount_minor - exact (positif : arrondi à la hausse)


def _convert_full(amount_minor: int, src: Currency, dst: Currency, rate_cdf_per_usd) -> Decimal:
    """Valeur exacte (pleine précision) du montant converti, en unité mineure de ``dst``."""
    if src.code == dst.code:
        return Decimal(amount_minor)
    if {src.code, dst.code} != {RATE_BASE, RATE_QUOTE}:
        raise UnsupportedCurrency(f"Conversion non supportée : {src.code} -> {dst.code}")
    rate = check_rate(rate_cdf_per_usd)
    base, quote = CURRENCIES[RATE_BASE], CURRENCIES[RATE_QUOTE]
    with localcontext() as ctx:
        ctx.prec = _PRECISION
        if src.code == RATE_QUOTE:  # CDF -> USD : multiplier avant de diviser
            return Decimal(amount_minor) * base.factor / (rate * quote.factor)
        return Decimal(amount_minor) * rate * quote.factor / base.factor  # USD -> CDF


def convert_minor(
    amount_minor: int,
    from_currency: Currency | str,
    to_currency: Currency | str,
    rate_cdf_per_usd: str | Decimal,
) -> ConversionResult:
    """Convertit un montant entre CDF et USD ; arrondi ROUND_HALF_UP dans la devise cible.

    ``exact`` et ``rounding_diff_minor`` sont exposés à 12 décimales ; ``amount_minor`` est arrondi
    sur la valeur en pleine précision (80 chiffres).
    """
    src, dst = get_currency(from_currency), get_currency(to_currency)
    _check_minor(amount_minor)
    full = _convert_full(amount_minor, src, dst, rate_cdf_per_usd)
    rounded = _round_half_up(full)
    exact = _expose(full)
    return ConversionResult(rounded, exact, Decimal(rounded) - exact)


@dataclass(frozen=True)
class PaymentResult:
    total_minor: int
    total_currency: str
    paid_minor: int  # total payé converti dans la devise de la vente
    paid_rounding_diff_minor: Decimal  # écart d'arrondi de cette conversion (unité de la vente)
    remaining_minor: int  # reste à payer (devise de la vente), 0 si soldé
    overpaid_minor: int  # excédent dans la devise de la vente, 0 sinon
    change_currency: str
    change_minor: int  # monnaie à rendre, dans change_currency
    change_rounding_diff_minor: Decimal  # écart d'arrondi du rendu (unité de change_currency)

    @property
    def is_fully_paid(self) -> bool:
        return self.remaining_minor == 0


def compute_payment(
    total_minor: int,
    total_currency: Currency | str,
    payments: Iterable[tuple[int, Currency | str]],
    rate_cdf_per_usd: str | Decimal,
    change_currency: Currency | str,
) -> PaymentResult:
    """Paiement mixte (KOM-039) : total payé, reste à payer et rendu de monnaie.

    Les paiements sont convertis dans la devise de la vente en sommant les valeurs exactes puis en
    arrondissant UNE seule fois (ROUND_HALF_UP) : pas d'accumulation d'erreurs. Le rendu éventuel
    est ensuite converti dans ``change_currency`` (arrondi au plus petit billet comptable : franc
    ou centime) ; les deux écarts d'arrondi sont retournés.
    """
    sale_cur, chg_cur = get_currency(total_currency), get_currency(change_currency)
    _check_minor(total_minor, "total")
    if total_minor < 0:
        raise MoneyError("Le total ne peut pas être négatif.")
    exact_paid = Decimal(0)
    for amount, currency in payments:
        _check_minor(amount, "paiement")
        if amount < 0:
            raise MoneyError("Un paiement ne peut pas être négatif.")
        exact_paid += _convert_full(amount, get_currency(currency), sale_cur, rate_cdf_per_usd)
    paid = _round_half_up(exact_paid)
    paid_diff = Decimal(paid) - _expose(exact_paid)

    remaining = max(total_minor - paid, 0)
    overpaid = max(paid - total_minor, 0)
    if overpaid:
        change = convert_minor(overpaid, sale_cur, chg_cur, rate_cdf_per_usd)
        change_minor, change_diff = change.amount_minor, change.rounding_diff_minor
    else:
        change_minor, change_diff = 0, Decimal(0)
    return PaymentResult(
        total_minor=total_minor,
        total_currency=sale_cur.code,
        paid_minor=paid,
        paid_rounding_diff_minor=paid_diff,
        remaining_minor=remaining,
        overpaid_minor=overpaid,
        change_currency=chg_cur.code,
        change_minor=change_minor,
        change_rounding_diff_minor=change_diff,
    )
