"""Import CSV du catalogue : validation ligne par ligne, chaque ligne valide est enregistrée
dans son propre point de sauvegarde (une ligne erronée n'empêche pas les autres)."""

import csv
import io

from django.db import IntegrityError, transaction

from .models import Category, Product
from .money import to_minor

COLUMNS = ["name", "sku", "barcode", "category", "kind", "unit", "price", "currency", "cost"]
REQUIRED = ["name", "sku"]
MAX_ROWS = 10000
KINDS = {k for k, _ in Product.KINDS}
UNITS = {k for k, _ in Product.UNITS}
CURRENCIES = {k for k, _ in Product.CURRENCIES}


class ImportFileError(Exception):
    pass


def _read_rows(raw: bytes):
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    first_line = text.split("\n", 1)[0]
    delimiter = ";" if first_line.count(";") > first_line.count(",") else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    if not reader.fieldnames:
        raise ImportFileError("Fichier vide.")
    reader.fieldnames = [(f or "").strip().lower() for f in reader.fieldnames]
    missing = [c for c in REQUIRED if c not in reader.fieldnames]
    if missing:
        raise ImportFileError("Colonnes obligatoires manquantes : " + ", ".join(missing) + ".")
    return reader


def import_products(raw: bytes) -> dict:
    reader = _read_rows(raw)
    rows, created = [], 0
    seen_skus = set(Product.objects.values_list("sku", flat=True))
    seen_barcodes = set(Product.objects.exclude(barcode="").values_list("barcode", flat=True))
    categories = {c.name.lower(): c for c in Category.objects.all()}

    for index, record in enumerate(reader, start=2):  # ligne 1 = en-tête
        if index - 1 > MAX_ROWS:
            raise ImportFileError(f"Trop de lignes (maximum {MAX_ROWS}).")
        get = lambda key: (record.get(key) or "").strip()  # noqa: E731
        errors = []
        name, sku, barcode = get("name"), get("sku"), get("barcode")
        kind = get("kind").lower() or "product"
        unit = get("unit").lower() or "piece"
        currency = get("currency").upper() or "CDF"
        if not name:
            errors.append("Le nom est obligatoire.")
        if not sku:
            errors.append("Le SKU est obligatoire.")
        elif sku in seen_skus:
            errors.append("SKU déjà existant (dans le catalogue ou plus haut dans le fichier).")
        if barcode and barcode in seen_barcodes:
            errors.append("Code-barres déjà existant.")
        if kind not in KINDS:
            errors.append(f"Type invalide : « {kind} » (product ou service).")
        if unit not in UNITS:
            errors.append(f"Unité invalide : « {unit} ».")
        if currency not in CURRENCIES:
            errors.append(f"Devise invalide : « {currency} » (CDF ou USD).")
        price = cost = 0
        if currency in CURRENCIES:
            for label, key in (("prix", "price"), ("coût", "cost")):
                if get(key):
                    try:
                        value = to_minor(get(key), currency)
                    except ValueError as exc:
                        errors.append(f"{label.capitalize()} : {exc}")
                    else:
                        price, cost = (value, cost) if key == "price" else (price, value)
        if not errors:
            try:
                with transaction.atomic():
                    category = None
                    if get("category"):
                        key = get("category").lower()
                        category = categories.get(key)
                        if category is None:
                            category = Category.objects.create(name=get("category"))
                            categories[key] = category
                    Product.objects.create(
                        name=name, sku=sku, barcode=barcode, category=category, kind=kind, unit=unit,
                        price_minor=price, currency=currency, cost_minor=cost,
                        track_stock=(kind == "product"),
                    )
            except IntegrityError:
                errors.append("Conflit d'unicité (SKU ou code-barres).")
        if errors:
            rows.append({"line": index, "sku": sku, "status": "error", "errors": errors})
        else:
            seen_skus.add(sku)
            if barcode:
                seen_barcodes.add(barcode)
            created += 1
            rows.append({"line": index, "sku": sku, "status": "created", "errors": []})

    failed = len(rows) - created
    return {"total": len(rows), "created": created, "failed": failed, "rows": rows}
