"""API /api/v1/backups/ : rôles, validation des chemins, édition locale, lecture seule, audit, isolation."""

import os
from datetime import timedelta

import pytest
from django.utils import timezone

from audit.models import AuditEntry
from backups.models import BackupRecord, BackupSettings
from billing.models import Subscription
from catalog.tests.helpers import client_for_role
from core.tenancy import tenant_context

from .helpers import make_data

pytestmark = pytest.mark.django_db(transaction=True)

BASE = "/api/v1/backups/"
READERS = ["owner", "manager"]
OTHERS = ["cashier", "storekeeper", "accountant"]


def audit(handle, action):
    with tenant_context(handle.tenant.id):
        return list(AuditEntry.objects.filter(action=action))


@pytest.mark.parametrize("role", READERS)
@pytest.mark.parametrize("path", ["status/", "records/", "settings/"])
def test_owner_and_manager_can_read(tenant_a, cfg, role, path):
    assert client_for_role(tenant_a, role).get(BASE + path).status_code == 200


@pytest.mark.parametrize("role", OTHERS)
@pytest.mark.parametrize("path", ["status/", "records/", "settings/"])
def test_other_roles_cannot_read(tenant_a, cfg, role, path):
    assert client_for_role(tenant_a, role).get(BASE + path).status_code == 403


@pytest.mark.parametrize("role", OTHERS)
def test_other_roles_cannot_run_or_write(tenant_a, cfg, role, folder):
    c = client_for_role(tenant_a, role)
    assert c.post(BASE + "run/").status_code == 403
    assert c.put(BASE + "settings/", {"retention": 5}, format="json").status_code == 403


def test_anonymous_is_refused(client, cfg):
    assert client.get(BASE + "status/").status_code == 401


def test_manager_can_run_but_only_owner_can_write_settings(tenant_a, cfg, folder):
    make_data(tenant_a, sales=1)
    manager = client_for_role(tenant_a, "manager")
    r = manager.post(BASE + "run/")
    assert r.status_code == 201 and r.json()["ok"] is True
    assert manager.put(BASE + "settings/", {"retention": 5}, format="json").status_code == 403
    assert tenant_a.client().put(BASE + "settings/", {"retention": 5}, format="json").status_code == 200


def test_404_outside_local_edition(tenant_a, cfg, settings):
    for value in ("cloud", ""):
        settings.KOMERSIA_EDITION = value
        c = tenant_a.client()
        for path in ("status/", "records/", "settings/"):
            assert c.get(BASE + path).status_code == 404
        assert c.post(BASE + "run/").status_code == 404
        assert c.put(BASE + "settings/", {"retention": 5}, format="json").status_code == 404
    del settings.KOMERSIA_EDITION  # défaut : cloud
    assert tenant_a.client().get(BASE + "status/").status_code == 404


def test_run_creates_a_backup_and_is_audited(tenant_a, cfg, folder):
    make_data(tenant_a, sales=2)
    r = tenant_a.client().post(BASE + "run/")
    body = r.json()
    assert r.status_code == 201 and body["ok"] and body["record"]["status"] == "ok"
    assert (folder / body["record"]["filename"]).is_file()
    assert body["record"]["created_by_name"] == tenant_a.user.full_name
    entry = audit(tenant_a, "backup.run")[0]
    assert entry.after["status"] == "ok" and entry.user_id == tenant_a.user.id


def test_run_reports_a_missing_folder_in_the_result(tenant_a, cfg, folder):
    cfg.folder = str(folder / "absent")
    cfg.save()
    r = tenant_a.client().post(BASE + "run/")
    assert r.status_code == 201 and r.json()["ok"] is False
    assert r.json()["record"]["status"] == "dossier_introuvable"
    assert audit(tenant_a, "backup.run")


def test_run_conflict_when_busy(tenant_a, cfg, monkeypatch):
    from backups import services

    def busy(*a, **k):
        raise services.BackupBusy("Une sauvegarde est déjà en cours.")

    monkeypatch.setattr(services, "create_backup", busy)
    r = tenant_a.client().post(BASE + "run/")
    assert r.status_code == 409 and r.json()["code"] == "backup_busy"


def test_status_content(tenant_a, cfg, folder):
    make_data(tenant_a, sales=1)
    c = tenant_a.client()
    s = c.get(BASE + "status/").json()
    assert s["last_success"] is None and s["folder_state"] == "ok" and s["free_bytes"] > 0
    assert s["health"] == "late" and s["banner"] is False and s["backup_count"] == 0
    c.post(BASE + "run/")
    s = c.get(BASE + "status/").json()
    assert s["last_success"]["status"] == "ok" and s["health"] == "ok" and s["age_hours"] < 1
    assert s["backup_count"] == 1 and s["next_run_at"] and s["folder"] == str(folder)


def test_status_health_levels(tenant_a, cfg, folder):
    c = tenant_a.client()
    make_data(tenant_a, sales=1)
    c.post(BASE + "run/")

    def set_age(hours):
        BackupRecord.objects.filter(status="ok").update(created_at=timezone.now() - timedelta(hours=hours))
        return c.get(BASE + "status/").json()

    assert set_age(10)["health"] == "ok"
    s = set_age(30)
    assert s["health"] == "late" and s["banner"] is False
    s = set_age(49)
    assert s["health"] == "alert" and s["banner"] is True


def test_status_alert_when_never_backed_up_a_day_after_installation(tenant_a, cfg):
    c = tenant_a.client()
    assert c.get(BASE + "status/").json()["banner"] is False
    BackupSettings.objects.update(created_at=timezone.now() - timedelta(hours=25))
    s = c.get(BASE + "status/").json()
    assert s["banner"] is True and s["health"] == "alert"


def test_status_alert_after_a_failed_backup_but_no_banner_if_recent(tenant_a, cfg, folder, monkeypatch):
    c = tenant_a.client()
    make_data(tenant_a, sales=1)
    c.post(BASE + "run/")
    monkeypatch.setenv("DB_BACKUP_PASSWORD", "mauvais")
    r = c.post(BASE + "run/")
    assert r.json()["record"]["status"] == "echec"
    s = c.get(BASE + "status/").json()
    assert s["health"] == "alert" and s["banner"] is False and s["last_attempt"]["status"] == "echec"
    assert s["last_success"]["status"] == "ok"


def test_status_shows_missing_usb_folder(tenant_a, cfg, folder):
    cfg.folder = str(folder / "usb")
    cfg.save()
    s = tenant_a.client().get(BASE + "status/").json()
    assert s["folder_state"] == "missing" and "usb" in s["folder_message"] and s["free_bytes"] is None


def test_records_are_paginated_most_recent_first(tenant_a, cfg, folder):
    now = timezone.now()
    for i in range(60):
        BackupRecord.objects.create(status="ok", reason="scheduled", created_at=now - timedelta(hours=i))
    d = tenant_a.client().get(BASE + "records/").json()
    assert d["count"] == 60 and len(d["results"]) == 50 and d["next"]
    assert d["results"][0]["created_at"] > d["results"][1]["created_at"]
    assert len(tenant_a.client().get(d["next"].split("testserver")[-1]).json()["results"]) == 10


# --------------------------------------------------------------------------- réglages et chemins


def put(handle, body):
    return handle.client().put(BASE + "settings/", body, format="json")


def test_settings_roundtrip_and_audit_before_after(tenant_a, cfg, folder, tmp_path):
    usb = tmp_path / "CleUSB"
    usb.mkdir()
    r = put(tenant_a, {"folder": str(usb), "retention": 10, "preferred_hour": 3})
    assert r.status_code == 200 and r.json()["folder"] == str(usb) and r.json()["retention"] == 10
    assert tenant_a.client().get(BASE + "settings/").json()["preferred_hour"] == 3
    entry = audit(tenant_a, "backup.settings_change")[0]
    assert entry.before["folder"] == str(folder) and entry.after["folder"] == str(usb)
    assert entry.before["retention"] == 30 and entry.after["retention"] == 10


def test_unchanged_settings_are_not_audited(tenant_a, cfg):
    put(tenant_a, {"retention": 30})
    assert audit(tenant_a, "backup.settings_change") == []


@pytest.mark.parametrize("retention", [0, -1, 366, "abc"])
def test_retention_is_validated(tenant_a, cfg, retention):
    assert put(tenant_a, {"retention": retention}).status_code == 400


def test_hour_and_interval_are_validated(tenant_a, cfg):
    assert put(tenant_a, {"preferred_hour": 24}).status_code == 400
    assert put(tenant_a, {"interval_hours": 0}).status_code == 400


def bad_folder(handle, value):
    r = put(handle, {"folder": value})
    assert r.status_code == 400, (value, r.content)
    assert "folder" in r.json()
    return r.json()["folder"][0]


def test_relative_and_traversal_paths_are_refused(tenant_a, cfg, folder, tmp_path):
    assert "chemin complet" in bad_folder(tenant_a, "Sauvegardes")
    assert "chemin complet" in bad_folder(tenant_a, "./Sauvegardes")
    assert "« .. »" in bad_folder(tenant_a, str(folder) + "/../Sauvegardes")
    assert "« .. »" in bad_folder(tenant_a, str(folder) + "\\..\\..")
    assert "« .. »" in bad_folder(tenant_a, "/tmp/../etc")
    assert bad_folder(tenant_a, "")
    assert BackupSettings.get_solo().folder == str(folder)  # inchangé


def test_non_existing_and_file_paths_are_refused(tenant_a, cfg, folder):
    assert "n'existe pas" in bad_folder(tenant_a, str(folder / "absent"))
    f = folder / "f.txt"
    f.write_text("x")
    assert "n'existe pas" in bad_folder(tenant_a, str(f))


def test_installation_folder_is_refused(tenant_a, cfg, settings, monkeypatch, tmp_path):
    install = tmp_path / "Komersia"
    (install / "data").mkdir(parents=True)
    monkeypatch.setenv("KOMERSIA_DATA_DIR", str(install / "data"))
    assert "en dehors du dossier de Komersia" in bad_folder(tenant_a, str(install / "data"))
    sub = install / "data" / "sauvegardes"
    sub.mkdir()
    assert "en dehors" in bad_folder(tenant_a, str(sub))
    assert "en dehors" in bad_folder(tenant_a, str(settings.BASE_DIR))
    assert "en dehors" in bad_folder(tenant_a, str(settings.BASE_DIR / "backups" / "tests"))


def test_symlink_into_installation_is_refused(tenant_a, cfg, settings, tmp_path):
    link = tmp_path / "raccourci"
    os.symlink(settings.BASE_DIR, link)
    assert "en dehors" in bad_folder(tenant_a, str(link))


def test_not_writable_folder_is_refused(tenant_a, cfg, tmp_path, monkeypatch):
    from backups import services

    def refuse(path):
        raise PermissionError

    monkeypatch.setattr(services, "probe_writable", refuse)
    assert "ne peut pas écrire" in bad_folder(tenant_a, str(tmp_path))


def test_api_without_edit_of_folder_keeps_other_fields_valid(tenant_a, cfg, folder):
    r = put(tenant_a, {"enabled": False})
    assert r.status_code == 200 and r.json()["enabled"] is False and r.json()["folder"] == str(folder)


# --------------------------------------------------------------------------- lecture seule d'abonnement


def make_read_only(handle):
    with tenant_context(handle.tenant.id):
        sub = Subscription.objects.get()
        sub.state = "read_only"
        sub.trial_ends_at = timezone.now() - timedelta(days=30)
        sub.save()


def test_backups_remain_usable_when_the_subscription_is_read_only(tenant_a, cfg, folder):
    make_data(tenant_a, sales=1)
    make_read_only(tenant_a)
    c = tenant_a.client()
    # contrôle : une écriture ordinaire est bien bloquée (402)
    assert c.post("/api/v1/customers/", {"name": "X"}, format="json").status_code == 402
    assert c.post(BASE + "run/").status_code == 201
    assert c.put(BASE + "settings/", {"retention": 7}, format="json").status_code == 200
    assert c.get(BASE + "status/").status_code == 200


def test_exemption_prefix_is_declared():
    from billing.permissions import is_exempt_path

    assert is_exempt_path("/api/v1/backups/run/") and not is_exempt_path("/api/v1/backupsx/")


def test_audit_entries_stay_isolated_between_tenants(tenant_a, tenant_b, cfg, folder):
    """Isolation : la sauvegarde est celle de l'installation, mais son journal d'audit reste cloisonné."""
    make_data(tenant_a, sales=1)
    assert tenant_a.client().post(BASE + "run/").status_code == 201
    assert audit(tenant_a, "backup.run") and audit(tenant_b, "backup.run") == []
    assert tenant_b.client().get("/api/v1/audit/entries/?action=backup.run").json()["count"] == 0


def test_isolation_of_data_in_the_ordinary_role_after_backup(tenant_a, tenant_b, cfg):
    make_data(tenant_a, sales=2)
    make_data(tenant_b, sales=1)
    tenant_a.client().post(BASE + "run/")
    assert len(tenant_a.client().get("/api/v1/sales/").json()["results"]) == 2
    assert len(tenant_b.client().get("/api/v1/sales/").json()["results"]) == 1
