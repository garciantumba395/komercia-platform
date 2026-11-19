"""Page Abonnement : coquille HTML, menu, bandeau global, logique JavaScript pure (Node, ignoré si absent)."""

import re

import pytest
from django.urls import reverse

from .test_js_logic import NODE, STATIC, run

needs_node = pytest.mark.skipif(NODE is None, reason="Node.js indisponible")


@pytest.mark.django_db
def test_billing_page_is_valid_french_html(client):
    response = client.get("/app/abonnement/")
    assert response.status_code == 200
    assert reverse("web:billing") == "/app/abonnement/"
    html = response.content.decode()
    assert html.lstrip().lower().startswith("<!doctype html>")
    assert '<html lang="fr">' in html
    assert re.search(r"<title>Abonnement – Komersia</title>", html)
    assert 'data-page="billing"' in html and html.count("<main") == 1
    assert "web/billing.js" in html and "web/pages.js" not in html
    # bandeau global d'abonnement dans la mise en page de toutes les pages privées
    assert 'id="sub-banner"' in html


@pytest.mark.django_db
@pytest.mark.parametrize("name", ["pos", "products", "sales", "rates", "dashboard", "users", "billing"])
def test_every_private_page_has_the_subscription_banner(client, name):
    assert 'id="sub-banner"' in client.get(reverse(f"web:{name}")).content.decode()


@pytest.mark.django_db
def test_public_pages_have_no_subscription_banner(client):
    assert 'id="sub-banner"' not in client.get(reverse("web:login")).content.decode()


@pytest.mark.django_db
def test_billing_page_loads_no_external_resource(client):
    html = client.get("/app/abonnement/").content.decode()
    urls = re.findall(r'(?:src|href)="([^"]+)"', html)
    assert urls and all(u.startswith(("/", "#")) for u in urls), urls


def test_menu_entry_is_declared_for_billing_readers_only():
    app = (STATIC / "app.js").read_text(encoding="utf-8")
    entry = re.search(r"\{ id: 'billing'[^}]+\}", app).group(0)
    assert "href: '/app/abonnement/'" in entry and "label: 'Abonnement'" in entry
    assert "roles: ['owner', 'manager', 'accountant']" in entry  # ni caissier ni magasinier


def test_billing_api_is_not_called_for_roles_without_read_access():
    app = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "if (!K.billing.canRead(ctx.role)) return null;" in app
    assert "Abonnement terminé. Demandez au propriétaire de s’abonner." in app  # 402 pour le caissier


def test_pos_disables_cashing_when_read_only():
    pos = (STATIC / "pos.js").read_text(encoding="utf-8")
    assert "K.billing.isReadOnly()" in pos and "Encaissement indisponible" in pos


@needs_node
def test_days_label():
    got = run([["billing.daysLabel", [n]] for n in (14, 2, 1, 0, -1, None)])
    assert got == ["14 jours restants", "2 jours restants", "1 jour restant"] + ["Dernier jour"] * 3


def sub(state, days=None, **extra):
    return {"state": state, "days_left": days, "cancel_at_period_end": False, **extra}


@needs_node
def test_banner_text_and_colour_follow_the_state():
    got = run([
        ["billing.bannerFor", [sub("trial", 10), "owner"]],
        ["billing.bannerFor", [sub("trial", 3), "owner"]],
        ["billing.bannerFor", [sub("trial", 1), "owner"]],
        ["billing.bannerFor", [sub("trial", 0), "owner"]],
        ["billing.bannerFor", [sub("past_due", 5), "owner"]],
        ["billing.bannerFor", [sub("read_only"), "owner"]],
        ["billing.bannerFor", [sub("active", 20), "owner"]],
    ])
    trial10, trial3, trial1, trial0, late, ro, active = got
    assert (trial10["tone"], trial10["text"]) == ("info", "Il vous reste 10 jours d’essai.")
    # orange à 3 jours ou moins
    assert trial3["tone"] == "warn" and trial3["text"] == "Il vous reste 3 jours d’essai."
    assert trial1["tone"] == "warn" and trial1["text"] == "Il vous reste 1 jour d’essai."
    assert trial0["text"] == "Dernier jour d’essai gratuit."
    assert trial10["action"] == {"label": "S’abonner", "href": "/app/abonnement/"}
    assert late["tone"] == "warn" and late["text"].startswith("Paiement attendu : 5 jours de grâce")
    assert late["action"]["label"] == "Payer"
    assert ro["tone"] == "danger" and ro["text"] == "Abonnement terminé : consultation et export seulement."
    assert ro["action"]["label"] == "S’abonner"
    assert active is None  # abonnement actif et à jour : pas de bandeau


@needs_node
def test_banner_for_non_owner_and_cancelled_subscription():
    ro, trial, cancelled = run([
        ["billing.bannerFor", [sub("read_only"), "manager"]],
        ["billing.bannerFor", [sub("trial", 8), "accountant"]],
        ["billing.bannerFor", [
            sub("active", 9, cancel_at_period_end=True, current_period_end="2026-12-31T12:00:00Z"), "owner"]],
    ])
    assert ro["text"].endswith("Demandez au propriétaire de s’abonner.")
    assert ro["action"]["label"] == "Voir l’abonnement"
    assert trial["action"]["label"] == "Voir l’abonnement"
    assert cancelled["tone"] == "info" and "jusqu’au 31/12/2026" in cancelled["text"]
    assert cancelled["action"]["label"] == "Reprendre"


@needs_node
def test_payment_status_labels_are_french():
    statuses = ("pending", "confirmed", "failed", "expired", "autre")
    got = run([["billing.paymentStatusLabel", [s]] for s in statuses])
    assert got == ["En attente", "Confirmé", "Échoué", "Expiré", "autre"]
    tones = run([["billing.paymentStatus", [s]] for s in ("confirmed", "failed")])
    assert [t[1] for t in tones] == ["ok", "danger"]


@needs_node
def test_billing_roles():
    roles = ("owner", "manager", "accountant", "cashier", "storekeeper")
    got = run([["billing.canRead", [r]] for r in roles])
    assert got == [True, True, True, False, False]
