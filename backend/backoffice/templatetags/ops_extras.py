from django import template

register = template.Library()


@register.filter
def money(minor, currency):
    """Montant entier en plus petite unité -> texte (USD : centimes ; CDF : francs)."""
    if minor is None:
        return "—"
    minor = int(minor)
    if currency == "USD":
        sign = "-" if minor < 0 else ""
        return f"{sign}{abs(minor) // 100}.{abs(minor) % 100:02d} USD"
    return f"{minor:,}".replace(",", " ") + f" {currency}"


@register.filter
def pct(value):
    return "—" if value is None else f"{value:.1f} %".replace(".", ",")


@register.filter
def usd(minor):
    return "—" if minor is None else money(minor, "USD")


@register.filter
def state_label(code):
    return {"trial": "Essai", "active": "Actif", "past_due": "En retard", "read_only": "Lecture seule"}.get(
        code, "Aucun abonnement"
    )
