"""backup_tick (planification) et restore_backup (aller-retour réel)."""

from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from backups import services
from backups.models import BackupRecord, BackupSettings
from core.tenancy import tenant_context

from .conftest import count_rows
from .helpers import TABLES, add_sale, make_data

pytestmark = pytest.mark.django_db(transaction=True)


def tick(**kw):
    out = StringIO()
    call_command("backup_tick", stdout=out)
    return out.getvalue()


def test_tick_creates_the_first_backup(tenant_a, cfg, folder):
    make_data(tenant_a, sales=1)
    assert "action=created" in tick()
    assert BackupRecord.objects.filter(status="ok", reason="scheduled").count() == 1
    assert len(list(folder.glob("komersia-*.dump"))) == 1


def test_tick_is_idempotent_when_the_last_backup_is_recent(tenant_a, cfg, folder):
    make_data(tenant_a, sales=1)
    tick()
    assert "action=not_due" in tick()
    assert "action=not_due" in tick()
    assert len(list(folder.glob("komersia-*.dump"))) == 1


def test_tick_runs_again_after_24_hours(tenant_a, cfg, folder):
    make_data(tenant_a, sales=1)
    tick()
    BackupRecord.objects.update(created_at=timezone.now() - timedelta(hours=25))
    assert "action=created" in tick()
    assert BackupRecord.objects.filter(status="ok").count() == 2


def test_interval_is_configurable(tenant_a, cfg):
    make_data(tenant_a, sales=1)
    cfg.interval_hours = 6
    cfg.save()
    tick()
    BackupRecord.objects.update(created_at=timezone.now() - timedelta(hours=7))
    assert "action=created" in tick()


def test_preferred_hour_delays_a_backup_that_would_fall_in_the_night(tenant_a, cfg):
    last = BackupRecord(created_at=timezone.localtime().replace(hour=0, minute=30) - timedelta(days=1))
    cfg.preferred_hour = 2
    due = timezone.localtime(services.due_at(cfg, last))
    assert (due.hour, due.minute) == (2, 0)
    # retard important : jamais plus de 12 h d'attente supplémentaire
    cfg.preferred_hour = 23
    assert services.due_at(cfg, last) <= last.created_at + timedelta(hours=36)


def test_tick_with_missing_folder_records_state_and_retries_next_time(tenant_a, cfg, folder):
    make_data(tenant_a, sales=1)
    usb = folder / "usb"
    cfg.folder = str(usb)
    cfg.save()
    assert "action=folder_missing" in tick()  # ne plante pas
    assert "action=folder_missing" in tick()
    records = BackupRecord.objects.all()
    assert records.count() == 1 and records[0].status == "dossier_introuvable" and records[0].attempts == 2
    assert BackupSettings.get_solo().folder_state == "missing"
    usb.mkdir()  # la clé est rebranchée
    assert "action=created" in tick()
    assert list(usb.glob("komersia-*.dump"))
    assert BackupSettings.get_solo().folder_state == "ok"


def test_tick_does_nothing_when_disabled(tenant_a, cfg, folder):
    cfg.enabled = False
    cfg.save()
    assert "action=disabled" in tick()
    assert not list(folder.iterdir())


def test_tick_skips_when_another_backup_is_running(tenant_a, cfg, monkeypatch):
    def busy(*a, **k):
        raise services.BackupBusy("occupé")

    monkeypatch.setattr(services, "create_backup", busy)
    assert "action=busy" in tick()


# --------------------------------------------------------------------------- restauration


def test_restore_round_trip_returns_to_the_original_state(tenant_a, cfg, folder):
    product = make_data(tenant_a, sales=3)
    original = count_rows(TABLES)
    record = services.create_backup("manual")
    assert record.status == "ok"

    # on modifie les données : une vente de plus, un client de plus, un produit supprimé
    add_sale(tenant_a, product)
    from customers.models import Customer
    with tenant_context(tenant_a.tenant.id):
        Customer.objects.create(name="Intrus")
    assert count_rows(TABLES)["sales_sale"] == original["sales_sale"] + 1

    out = StringIO()
    call_command("restore_backup", str(folder / record.filename), "--yes", stdout=out)

    assert "Restauration terminée" in out.getvalue()
    assert count_rows(TABLES) == original
    with tenant_context(tenant_a.tenant.id):
        assert not Customer.objects.filter(name="Intrus").exists()
        from sales.models import Sale
        assert Sale.objects.count() == 3
    # sauvegarde de sécurité de l'état AVANT restauration : contient la vente de plus
    safety = list(folder.glob("komersia-avant-restauration-*.dump"))
    assert len(safety) == 1
    assert BackupRecord.objects.filter(reason="pre_restore", status="ok").count() == 1
    # l'application fonctionne toujours (droits du rôle applicatif, RLS)
    r = tenant_a.client().get("/api/v1/sales/")
    assert r.status_code == 200 and len(r.json()["results"]) == 3
    # la sauvegarde de sécurité n'est pas supprimée par la rotation
    cfg.refresh_from_db()
    assert cfg.folder == str(folder)


def test_safety_backup_can_undo_the_restore(tenant_a, cfg, folder):
    product = make_data(tenant_a, sales=1)
    record = services.create_backup("manual")
    add_sale(tenant_a, product)
    call_command("restore_backup", str(folder / record.filename), "--yes", stdout=StringIO())
    assert count_rows(["sales_sale"])["sales_sale"] == 1
    safety = next(folder.glob("komersia-avant-restauration-*.dump"))
    call_command("restore_backup", str(safety), "--yes", stdout=StringIO())
    assert count_rows(["sales_sale"])["sales_sale"] == 2


def test_restore_requires_confirmation(tenant_a, cfg, folder):
    make_data(tenant_a, sales=1)
    record = services.create_backup("manual")
    with pytest.raises(CommandError, match="--yes"):
        call_command("restore_backup", str(folder / record.filename), stdout=StringIO())


def test_corrupted_file_is_refused_and_data_untouched(tenant_a, cfg, folder):
    make_data(tenant_a, sales=2)
    record = services.create_backup("manual")
    path = folder / record.filename
    data = bytearray(path.read_bytes())
    data[len(data) // 2] ^= 0xFF  # un seul octet altéré : la somme SHA-256 le détecte
    path.write_bytes(bytes(data))
    before = count_rows(TABLES)
    with pytest.raises(CommandError, match="somme de contrôle"):
        call_command("restore_backup", str(path), "--yes", stdout=StringIO())
    assert count_rows(TABLES) == before
    assert not list(folder.glob("komersia-avant-restauration-*"))  # refusé avant toute action


def test_truncated_or_foreign_file_is_refused(tenant_a, cfg, folder, tmp_path):
    make_data(tenant_a, sales=1)
    record = services.create_backup("manual")
    truncated = tmp_path / "tronque.dump"
    truncated.write_bytes((folder / record.filename).read_bytes()[:500])
    foreign = tmp_path / "autre.dump"
    foreign.write_text("ceci n'est pas une sauvegarde")
    for bad in (truncated, foreign, tmp_path / "absent.dump"):
        with pytest.raises(CommandError):
            call_command("restore_backup", str(bad), "--yes", stdout=StringIO())


def test_backup_from_a_newer_schema_is_refused(tenant_a, cfg, folder, monkeypatch):
    make_data(tenant_a, sales=1)
    record = services.create_backup("manual")
    real = services.code_migrations
    monkeypatch.setattr(services, "code_migrations",
                        lambda: {m for m in real() if m[0] != "sales"})  # l'application « connaît moins »
    with pytest.raises(CommandError, match="plus récente"):
        call_command("restore_backup", str(folder / record.filename), "--yes", stdout=StringIO())


def test_backup_from_an_older_schema_is_accepted_with_a_notice(tenant_a, cfg, folder, monkeypatch):
    make_data(tenant_a, sales=1)
    record = services.create_backup("manual")
    real = services.code_migrations
    monkeypatch.setattr(services, "code_migrations", lambda: real() | {("sales", "9999_futur")})
    out = StringIO()
    call_command("restore_backup", str(folder / record.filename), "--yes", stdout=out)
    assert "migrate" in out.getvalue()


def test_check_only_verifies_without_restoring(tenant_a, cfg, folder):
    make_data(tenant_a, sales=1)
    record = services.create_backup("manual")
    out = StringIO()
    call_command("restore_backup", str(folder / record.filename), "--check-only", stdout=out)
    assert "Fichier valide" in out.getvalue()
    assert not list(folder.glob("komersia-avant-restauration-*"))


def test_restore_aborts_if_the_safety_backup_fails(tenant_a, cfg, folder, monkeypatch):
    product = make_data(tenant_a, sales=1)
    record = services.create_backup("manual")
    add_sale(tenant_a, product)
    monkeypatch.setenv("DB_BACKUP_PASSWORD", "mauvais")
    with pytest.raises(CommandError, match="sauvegarde de sécurité"):
        call_command("restore_backup", str(folder / record.filename), "--yes", stdout=StringIO())
    monkeypatch.setenv("DB_BACKUP_PASSWORD", "backup")
    assert count_rows(["sales_sale"])["sales_sale"] == 2  # rien n'a changé
