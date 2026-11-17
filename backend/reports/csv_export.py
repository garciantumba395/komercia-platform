"""Export CSV (KOM-064) : séparateur « ; », UTF-8 avec BOM (Excel), montants lisibles par devise."""

from __future__ import annotations

import csv
import io

from currency.money import from_minor

BOM = "﻿"
_DANGEROUS = ("=", "+", "-", "@", "\t", "\r")


def _text(value) -> str:
    """Neutralise l'injection de formule (Excel) dans les libellés saisis par les utilisateurs."""
    value = "" if value is None else str(value)
    return "'" + value if value.startswith(_DANGEROUS) else value


def _amount(minor: int, currency: str) -> str:
    """Montant lisible dans sa devise : CDF « 12500 », USD « 12,50 » (virgule décimale, Excel FR)."""
    return str(from_minor(minor, currency)).replace(".", ",")


def _money_rows(writer, label: str, totals: dict) -> None:
    for ccy in ("CDF", "USD"):
        writer.writerow([label, ccy, _amount(totals[ccy], ccy)])


def _indicators(writer, data: dict) -> None:
    writer.writerow(["Indicateur", "Devise", "Montant"])
    writer.writerow(["Nombre de ventes", "", data["sales_count"]])
    _money_rows(writer, "Chiffre d'affaires", data["revenue"])
    _money_rows(writer, "Encaissements espèces", data["cash_collected"])
    _money_rows(writer, "Ventes à crédit", data["credit_given"])
    _money_rows(writer, "Marge estimée", data["estimated_margin"])


def _top_products(writer, rows: list) -> None:
    writer.writerow([])
    writer.writerow(["Produits les plus vendus"])
    writer.writerow(["Produit", "Quantité", "Devise", "Chiffre d'affaires"])
    for r in rows:
        amount = _amount(r["revenue_minor"], r["currency"])
        writer.writerow([_text(r["name"]), r["quantity"], r["currency"], amount])


def render(kind: str, data: dict, store_name: str | None) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    store_label = _text(store_name) if store_name else "Toutes les boutiques"
    if kind == "daily":
        writer.writerow(["Rapport journalier"])
        writer.writerow(["Date", data["date"]])
        writer.writerow(["Boutique", store_label])
        writer.writerow([])
        _indicators(writer, data)
        writer.writerow(["Ventes annulées", "", data["cancelled_count"]])
        _money_rows(writer, "Dettes clients ouvertes", data["debts"])
        _top_products(writer, data["top_products"])
        writer.writerow([])
        writer.writerow(["Stock bas"])
        writer.writerow(["Produit", "Boutique", "Quantité", "Seuil minimum"])
        for r in data["low_stock"]:
            writer.writerow([_text(r["name"]), r["store"], r["quantity"], r["min_stock"]])
    else:
        writer.writerow(["Rapport de synthèse"])
        writer.writerow(["Du", data["from"]])
        writer.writerow(["Au", data["to"]])
        writer.writerow(["Boutique", store_label])
        writer.writerow([])
        _indicators(writer, data)
        writer.writerow([])
        writer.writerow(["Ventes par jour"])
        writer.writerow(["Date", "Nombre de ventes", "CA CDF", "CA USD"])
        for d in data["by_day"]:
            writer.writerow([d["date"], d["sales_count"], _amount(d["revenue"]["CDF"], "CDF"),
                             _amount(d["revenue"]["USD"], "USD")])
        _top_products(writer, data["top_products"])
        writer.writerow([])
        writer.writerow(["Comparaison des boutiques"])
        writer.writerow(["Boutique", "Nombre de ventes", "CA CDF", "CA USD"])
        for s in data["by_store"]:
            writer.writerow([_text(s["name"]), s["sales_count"], _amount(s["revenue"]["CDF"], "CDF"),
                             _amount(s["revenue"]["USD"], "USD")])
    return BOM + buf.getvalue()
