"""Reçu de vente : texte monospace 32 colonnes ou HTML étroit (58/80 mm). Libellés en français."""

from __future__ import annotations

import textwrap

from django.template.loader import render_to_string
from django.utils import timezone

from currency.money import convert_minor, format_minor
from customers.services import get_balance

WIDTH = 32


def _other(currency: str) -> str:
    return "USD" if currency == "CDF" else "CDF"


def _money(amount: int, currency: str) -> str:
    return format_minor(amount, currency)


def receipt_context(sale, tenant_name: str) -> dict:
    """Données prêtes à afficher (toutes les valeurs sont déjà formatées en français)."""
    cur = sale.currency
    other = _other(cur)
    lines = []
    for item in sale.items.all():
        qty = format(item.quantity.normalize(), "f")
        lines.append({
            "name": item.name,
            "detail": f"{qty} x {_money(item.unit_price_minor, cur)}",
            "total": _money(item.line_total_minor + item.discount_minor, cur),
            "discount": _money(item.discount_minor, cur) if item.discount_minor else None,
        })
    line_discounts = sum(i.discount_minor for i in sale.items.all())
    equivalent = rate_label = None
    if sale.rate_cdf_per_usd is not None:
        equivalent = _money(convert_minor(sale.total_minor, cur, other, sale.rate_cdf_per_usd).amount_minor,
                            other)
        rate_label = f"1 USD = {sale.rate_cdf_per_usd:.2f} CDF"
    payments = [{"label": f"Espèces ({p.currency})", "amount": _money(p.amount_minor, p.currency)}
                for p in sale.payments.all()]
    balance = None
    if sale.customer_id:
        balance = [_money(v, c) for c, v in sorted(get_balance(sale.customer).items()) if v]
    return {
        "company": tenant_name,
        "store": sale.store.name,
        "number": sale.number,
        "date": timezone.localtime(sale.created_at).strftime("%d/%m/%Y %H:%M"),
        "cashier": sale.cashier.full_name,
        "customer": sale.customer.name if sale.customer_id else None,
        "lines": lines,
        "subtotal": _money(sale.subtotal_minor + line_discounts, cur),  # avant toute remise
        "line_discounts": _money(line_discounts, cur) if line_discounts else None,
        "discount": _money(sale.discount_minor, cur) if sale.discount_minor else None,
        "total": _money(sale.total_minor, cur),
        "equivalent": equivalent,
        "equivalent_currency": other,
        "rate_label": rate_label,
        "payments": payments,
        "change": _money(sale.change_minor, sale.change_currency) if sale.change_minor else None,
        "credit": _money(sale.credit_minor, cur) if sale.credit_minor else None,
        "customer_balance": balance,
        "cancelled": sale.status == "cancelled",
        "cancel_reason": sale.cancel_reason,
    }


def _center(text: str) -> list[str]:
    return [line.center(WIDTH).rstrip() for line in textwrap.wrap(text, WIDTH)] or [""]


def _cols(left: str, right: str) -> str:
    gap = WIDTH - len(left) - len(right)
    if gap < 1:
        return left[: max(WIDTH - len(right) - 1, 0)] + " " + right
    return left + " " * gap + right


def render_text(ctx: dict) -> str:
    rule = "-" * WIDTH
    out: list[str] = [*_center(ctx["company"]), *_center(ctx["store"]), rule]
    if ctx["cancelled"]:
        out += [*_center("*** VENTE ANNULÉE ***"), rule]
    out += [f"Reçu N° {ctx['number']}", f"Date : {ctx['date']}", f"Caissier : {ctx['cashier']}"]
    if ctx["customer"]:
        out.append(f"Client : {ctx['customer']}")
    out.append(rule)
    for line in ctx["lines"]:
        out.extend(textwrap.wrap(line["name"], WIDTH))
        out.append(_cols("  " + line["detail"], line["total"]))
        if line["discount"]:
            out.append(_cols("  Remise", "-" + line["discount"]))
    out.append(rule)
    if ctx["line_discounts"] or ctx["discount"]:
        out.append(_cols("Sous-total", ctx["subtotal"]))
    if ctx["line_discounts"]:
        out.append(_cols("Remises lignes", "-" + ctx["line_discounts"]))
    if ctx["discount"]:
        out.append(_cols("Remise globale", "-" + ctx["discount"]))
    out.append(_cols("TOTAL", ctx["total"]))
    if ctx["equivalent"]:
        out.append(_cols(f"Équiv. {ctx['equivalent_currency']}", ctx["equivalent"]))
        out.append(f"Taux : {ctx['rate_label']}")
    if ctx["payments"]:
        out.append(rule)
        out.extend(_cols(p["label"], p["amount"]) for p in ctx["payments"])
    if ctx["change"]:
        out.append(_cols("Monnaie rendue", ctx["change"]))
    if ctx["credit"]:
        out.append(_cols("Reste à crédit", ctx["credit"]))
    if ctx["customer_balance"]:
        out.append(_cols("Solde du client", " / ".join(ctx["customer_balance"])))
    out += [rule, *_center("Merci de votre visite !")]
    return "\n".join(out) + "\n"


def render_html(ctx: dict) -> str:
    return render_to_string("sales/receipt.html", {"r": ctx})
