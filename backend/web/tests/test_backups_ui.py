"""Page Sauvegardes : coquille HTML, menu, bandeau global, logique JavaScript pure (Node, ignoré si absent)."""

# ruff: noqa: E501

import re
import subprocess

import pytest
from django.urls import reverse

from .test_js_logic import NODE, STATIC, run

needs_node = pytest.mark.skipif(NODE is None, reason="Node.js indisponible")


@pytest.mark.django_db
def test_backups_page_is_valid_french_html(client):
    response = client.get("/app/sauvegardes/")
    assert response.status_code == 200
    assert reverse("web:backups") == "/app/sauvegardes/"
    html = response.content.decode()
    assert html.lstrip().lower().startswith("<!doctype html>")
    assert '<html lang="fr">' in html
    assert re.search(r"<title>Sauvegardes – Komersia</title>", html)
    assert 'data-page="backups"' in html and html.count("<main") == 1 and 'id="view"' in html
    assert "web/backups.js" in html and "web/pages.js" not in html
    urls = re.findall(r'(?:src|href)="([^"]+)"', html)
    assert urls and all(u.startswith(("/", "#")) for u in urls), urls  # aucune ressource externe


@pytest.mark.django_db
@pytest.mark.parametrize("name", ["pos", "products", "sales", "dashboard", "users", "billing", "backups"])
def test_every_private_page_has_the_backup_banner_slot(client, name):
    html = client.get(reverse(f"web:{name}")).content.decode()
    assert 'id="backup-banner"' in html and re.search(r'id="backup-banner"[^>]*hidden', html)


@pytest.mark.django_db
def test_public_pages_have_no_backup_banner(client):
    assert 'id="backup-banner"' not in client.get(reverse("web:login")).content.decode()


def test_backups_js_is_valid_and_has_no_external_url():
    subprocess.run([NODE, "--check", str(STATIC / "backups.js")], check=True, capture_output=True, timeout=30) if NODE else None
    text = (STATIC / "backups.js").read_text(encoding="utf-8")
    assert not re.search(r"https?://", text)


def test_menu_entry_for_owner_and_manager_only_and_hidden_until_api_answers():
    app = (STATIC / "app.js").read_text(encoding="utf-8")
    entry = re.search(r"\{ id: 'backups'[^}]+\}", app).group(0)
    assert "href: '/app/sauvegardes/'" in entry and "label: 'Sauvegardes'" in entry
    assert "roles: ['owner', 'manager']" in entry and "optional: true" in entry
    assert "if (!K.backups.canUse(ctx.role)) return null;" in app  # pas d'appel pour les autres rôles
    assert "item.hidden = !ctx.backups;" in app  # visible seulement si l'API répond
    assert "api('backups/status/')" in app


def test_restore_is_never_launched_from_the_browser():
    js = (STATIC / "backups.js").read_text(encoding="utf-8")
    calls = re.findall(r"api\('([^']+)'", js)
    assert calls and not [c for c in calls if "restor" in c.lower()], calls
    assert "Restaurer une sauvegarde" in js and "Démarrer" in js and "Elle ne se lance pas depuis cette page" in js


# --------------------------------------------------------------------------- logique pure


def st(health="ok", last="2026-11-19T01:00:00Z", age=2.0, banner=False, attempt=None):
    return {"health": health, "banner": banner, "age_hours": age,
            "last_success": {"created_at": last} if last else None, "last_attempt": attempt}


NOW = "2026-11-19T14:00:00Z"


@needs_node
def test_when_labels_use_kinshasa_time():
    got = run([["backups.when", [iso, NOW]] for iso in (
        "2026-11-19T01:00:00Z", "2026-11-18T01:00:00Z", "2026-11-20T01:00:00Z", "2026-11-10T13:30:00Z", "")])
    assert got == ["aujourd’hui à 02:00", "hier à 02:00", "demain à 02:00", "le 10/11/2026 à 14:30", ""]


@needs_node
def test_age_and_size_labels():
    ages = run([["backups.age", [h]] for h in (0.2, 1.5, 5, 47.9, 49, 100, None)])
    assert ages == ["moins d’une heure", "1 heure", "5 heures", "47 heures", "2 jours", "4 jours", ""]
    sizes = run([["backups.size", [n]] for n in (500, 2048, 5 * 1024 * 1024, 300 * 1024 * 1024)])
    assert [s.replace("\xa0", " ").replace(" ", " ") for s in sizes] == ["500 o", "2 Ko", "5 Mo", "300 Mo"]


@needs_node
def test_summary_ok_late_alert_and_never():
    ok, late, alert, never, never_alert = run([
        ["backups.summary", [st("ok"), NOW]],
        ["backups.summary", [st("late", age=30), NOW]],
        ["backups.summary", [st("alert", last="2026-11-16T01:00:00Z", age=85, banner=True), NOW]],
        ["backups.summary", [st("late", last=None, age=None), NOW]],
        ["backups.summary", [st("alert", last=None, age=None, banner=True), NOW]],
    ])
    assert ok["tone"] == "ok" and ok["title"] == "Dernière sauvegarde : aujourd’hui à 02:00 — OK"
    assert late["tone"] == "warn" and "un peu ancienne" in late["title"]
    assert alert["tone"] == "danger" and "le 16/11/2026 à 02:00" in alert["title"] and "3 jours" in alert["title"]
    assert never["tone"] == "warn" and never["title"] == "Aucune sauvegarde pour le moment"
    assert never_alert["tone"] == "danger"


@needs_node
def test_summary_shows_a_failed_last_attempt_in_red():
    failed = {"status": "echec", "message": "Espace insuffisant.", "created_at": "2026-11-19T10:00:00Z"}
    old_failure = {"status": "echec", "message": "x", "created_at": "2026-11-18T10:00:00Z"}
    a, b = run([
        ["backups.summary", [st("alert", attempt=failed), NOW]],
        ["backups.summary", [st("ok", attempt=old_failure), NOW]],
    ])
    assert a["tone"] == "danger" and "dernière tentative en échec" in a["title"] and "Espace insuffisant." in a["text"]
    assert b["tone"] == "ok" and b["text"] == ""  # un échec ancien, suivi d'une réussite, n'alarme plus


@needs_node
def test_global_banner_only_when_the_server_says_so():
    none1, none2, old, never = run([
        ["backups.bannerFor", [None]],
        ["backups.bannerFor", [st("late", age=30)]],
        ["backups.bannerFor", [st("alert", age=100, banner=True)]],
        ["backups.bannerFor", [st("alert", last=None, age=None, banner=True)]],
    ])
    assert none1 is None and none2 is None
    assert old["text"] == "Vos données ne sont plus sauvegardées : la dernière sauvegarde date de 4 jours."
    assert old["action"] == {"label": "Voir les sauvegardes", "href": "/app/sauvegardes/"} and old["tone"] == "warn"
    assert never["text"] == "Vos données ne sont pas encore sauvegardées."


@needs_node
def test_status_and_role_helpers():
    labels = run([["backups.statusLabel", [s]] for s in ("ok", "echec", "dossier_introuvable", "autre")])
    assert labels == ["Réussie", "Échec", "Dossier introuvable", "autre"]
    assert run([["backups.statusTone", [s]] for s in ("ok", "echec", "dossier_introuvable")]) == ["ok", "danger", "warn"]
    roles = run([["backups.canUse", [r]] for r in ("owner", "manager", "cashier", "storekeeper", "accountant")])
    assert roles == [True, True, False, False, False]
    assert run([["backups.canConfigure", ["owner"]], ["backups.canConfigure", ["manager"]]]) == [True, False]
