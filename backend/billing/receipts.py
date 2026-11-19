from django.utils import timezone
from django.utils.html import escape

from currency.money import format_minor

MONTHS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre",
          "octobre", "novembre", "décembre"]


def _date(value) -> str:
    if value is None:
        return "-"
    local = timezone.localtime(value)
    return f"{local.day} {MONTHS[local.month - 1]} {local.year}"


def receipt_lines(payment, business_name: str) -> list[tuple[str, str]]:
    if payment.provider == "license":
        return [
            ("Reçu d'abonnement n°", payment.reference),
            ("Entreprise", business_name),
            ("Plan", payment.plan.name),
            ("Date d'activation", _date(payment.confirmed_at)),
            ("Durée", f"{payment.months} mois"),
            ("Période couverte", f"du {_date(payment.period_start)} au {_date(payment.period_end)}"),
            ("Mode de paiement", "Clé de licence (payée hors application)"),
            ("Statut", "Payé"),
        ]
    rows = [
        ("Reçu d'abonnement n°", payment.reference),
        ("Entreprise", business_name),
        ("Plan", payment.plan.name),
        ("Date du paiement", _date(payment.confirmed_at)),
        ("Période couverte", f"du {_date(payment.period_start)} au {_date(payment.period_end)}"),
        ("Montant payé", format_minor(payment.amount_minor, payment.currency)),
    ]
    if payment.currency == "CDF":
        rows.append(("Équivalent", format_minor(payment.amount_usd_minor, "USD")))
        rows.append(("Taux appliqué", f"1 USD = {payment.rate_cdf_per_usd:f} CDF"))
    rows.append(("Mode de paiement", f"Mobile Money ({payment.provider})"
                 + (f" - {payment.phone}" if payment.phone else "")))
    rows.append(("Statut", "Payé"))
    return rows


def render_text(payment, business_name: str) -> str:
    rows = receipt_lines(payment, business_name)
    width = max(len(label) for label, _ in rows)
    body = "\n".join(f"{label.ljust(width)} : {value}" for label, value in rows)
    return f"KOMERSIA - REÇU D'ABONNEMENT\n{'=' * 30}\n{body}\n\nMerci de votre confiance.\n"


def render_html(payment, business_name: str) -> str:
    rows = "".join(f"<tr><th>{escape(label)}</th><td>{escape(value)}</td></tr>"
                   for label, value in receipt_lines(payment, business_name))
    return (
        '<!doctype html><html lang="fr"><head><meta charset="utf-8">'
        f"<title>Reçu {escape(payment.reference)}</title>"
        "<style>body{font-family:sans-serif;max-width:560px;margin:2rem auto}"
        "th{text-align:left;padding:4px 12px 4px 0}</style></head><body>"
        f"<h1>Komersia - Reçu d'abonnement</h1><table>{rows}</table>"
        "<p>Merci de votre confiance.</p></body></html>"
    )
