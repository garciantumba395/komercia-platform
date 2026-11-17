"""Garde-fous sur le contenu des fichiers JS : comportements validés lors de la recette navigateur."""

from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "static" / "web"


def read(name):
    return (STATIC / name).read_text(encoding="utf-8")


def test_sales_page_offers_sale_detail_and_owner_only_cancel():
    pages = read("pages.js")
    assert "function detail(s, who)" in pages and "Réimprimer le reçu" in pages
    assert "canCancel && !cancelled" in pages  # le bouton Annuler n'existe pas pour un caissier
    assert "cancel: (role) => ['owner', 'manager'].includes(role)" in read("app.js")


def test_cash_session_lookup_avoids_a_404_in_console():
    pos = read("pos.js")
    assert "sales/cash-sessions/?status=open" in pos


def test_stock_motif_is_checked_before_calling_the_api():
    assert "Le motif est obligatoire pour une sortie ou un ajustement" in read("pages.js")


def test_units_are_abbreviated_and_agree_in_number():
    pages = read("pages.js")
    assert "function unitLabel(u, qty)" in pages and "'pièces'" in pages


def test_deactivated_account_gets_an_explicit_message_on_login():
    app = read("app.js")
    assert "?expired=1" in app and "Contactez le propriétaire du commerce" in app
