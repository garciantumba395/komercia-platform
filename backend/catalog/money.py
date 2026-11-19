"""Conversion montant lisible <-> entier en plus petite unité (CDF : franc ; USD : centime)."""

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

DECIMALS = {"CDF": 0, "USD": 2}


def to_minor(text, currency: str) -> int:
    """Convertit « 12,50 » ou « 12.5 » en entier. Lève ValueError si invalide ou trop précis."""
    raw = str(text).strip().replace(" ", "").replace(",", ".")
    try:
        value = Decimal(raw)
    except InvalidOperation:
        raise ValueError(f"Montant invalide : « {text} ».") from None
    if not value.is_finite() or value < 0:
        raise ValueError(f"Montant invalide : « {text} ».")
    digits = DECIMALS[currency]
    scaled = value * (10**digits)
    if scaled != scaled.to_integral_value(rounding=ROUND_HALF_UP):
        raise ValueError(f"Trop de décimales pour {currency} : « {text} ».")
    return int(scaled)


def from_minor(amount: int, currency: str) -> str:
    digits = DECIMALS[currency]
    if digits == 0:
        return str(amount)
    return f"{Decimal(amount) / (10**digits):.{digits}f}"
