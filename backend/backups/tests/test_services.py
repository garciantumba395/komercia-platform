"""Service de sauvegarde : contenu prouvé par restauration, rotation, atomicité, échecs propres."""

import os
import re
import subprocess

import pytest

from backups import services
from backups.models import BackupRecord
from core.tenancy import tenant_context

from .conftest import count_rows, restore_into
from .helpers import TABLES, make_data

pytestmark = pytest.mark.django_db(transaction=True)


def files(folder):
    return sorted(p.name for p in folder.iterdir())


def test_backup_contains_the_data_proved_by_restoring_it(tenant_a, tenant_b, cfg, folder, scratch_db):
    make_data(tenant_a, sales=3)
    make_data(tenant_b, sales=2)
    source = count_rows(TABLES)
    assert source["sales_sale"] == 5 and source["catalog_product"] == 2 and source["accounts_tenant"] == 2

    record = services.create_backup("manual")

    assert record.status == "ok", record.message
    dump = folder / record.filename
    assert re.fullmatch(r"komersia-\d{8}-\d{6}\.dump", record.filename)
    assert record.size_bytes == dump.stat().st_size > 1000
    restore_into(dump, scratch_db)
    copy = count_rows(TABLES, scratch_db)
    assert copy == source  # les ventes et les autres tables sont bien dans la sauvegarde


def test_checksum_is_recorded_and_written_next_to_the_file(tenant_a, cfg, folder):
    make_data(tenant_a, sales=1)
    record = services.create_backup("manual")
    assert record.sha256 == services.sha256_file(folder / record.filename)
    assert (folder / (record.filename + ".sha256")).read_text().startswith(record.sha256)
    assert record.reason == "manual" and record.path == str(folder / record.filename)


def test_ordinary_role_is_refused_with_a_clear_message_and_no_file(tenant_a, cfg, folder, monkeypatch):
    make_data(tenant_a, sales=1)
    monkeypatch.delenv("DB_BACKUP_USER")  # retombe sur le rôle de la base par défaut (non BYPASSRLS ici)
    monkeypatch.setenv("DB_BACKUP_USER", "komersia_app")
    # Mot de passe du rôle applicatif : « app » en développement, autre valeur en CI (variable dédiée).
    monkeypatch.setenv("DB_BACKUP_PASSWORD", os.environ.get("KOMERSIA_TEST_APP_PASSWORD", "app"))
    record = services.create_backup("manual")
    assert record.status == "echec" and "BYPASSRLS" in record.message
    assert files(folder) == []


def test_no_overwrite_when_two_backups_share_the_same_second(tenant_a, cfg, folder, monkeypatch):
    make_data(tenant_a, sales=1)
    from django.utils import timezone
    fixed = timezone.now().replace(microsecond=0)
    first = services.create_backup("manual", now=fixed)
    content = (folder / first.filename).read_bytes()
    second = services.create_backup("manual", now=fixed)
    assert first.filename != second.filename and second.filename.endswith("-1.dump")
    assert (folder / first.filename).read_bytes() == content
    assert len([n for n in files(folder) if n.endswith(".dump")]) == 2


def test_rotation_keeps_the_last_n_and_removes_their_checksum_files(tenant_a, cfg, folder):
    make_data(tenant_a, sales=1)
    cfg.retention = 3
    cfg.save()
    from datetime import timedelta

    from django.utils import timezone
    base = timezone.now() - timedelta(days=10)
    names = []
    for i in range(5):
        names.append(services.create_backup("scheduled", now=base + timedelta(days=i)).filename)
    kept = [n for n in files(folder) if n.endswith(".dump")]
    assert kept == sorted(names)[-3:] and len(files(folder)) == 6  # 3 sauvegardes + 3 sommes


def test_rotation_never_deletes_the_only_valid_backup(tenant_a, cfg, folder):
    make_data(tenant_a, sales=1)
    cfg.retention = 1
    cfg.save()
    first = services.create_backup("manual")
    second = services.create_backup("manual")
    assert files(folder) == sorted([second.filename, second.filename + ".sha256"])
    assert first.filename not in files(folder)
    # la rotation protège toujours le fichier valide qu'on vient de créer, même si « keep » est absurde
    (folder / "komersia-29990101-000000.dump").write_bytes(b"x")  # plus « récent » mais pas protégé
    new = folder / second.filename
    services.rotate(str(folder), 1, new)
    assert new.exists()


def test_failure_leaves_no_partial_file_and_is_recorded(tenant_a, cfg, folder, monkeypatch):
    make_data(tenant_a, sales=1)
    monkeypatch.setenv("DB_BACKUP_PASSWORD", "mauvais-mot-de-passe")
    record = services.create_backup("manual")
    assert record.status == "echec" and "komersia_backup" in record.message
    assert files(folder) == []
    assert BackupRecord.objects.filter(status="echec").count() == 1


def test_failure_in_pg_dump_cleans_the_temporary_file(tenant_a, cfg, folder, monkeypatch):
    make_data(tenant_a, sales=1)
    real_run = services._run

    def fake_run(cmd, cr=None, **kw):
        if cmd[0].endswith("pg_dump"):
            open(cmd[cmd.index("--file") + 1], "wb").write(b"debut de fichier")  # écriture partielle
            return subprocess.CompletedProcess(cmd, 1, "", "pg_dump: erreur simulée\n")
        return real_run(cmd, cr, **kw)

    monkeypatch.setattr(services, "_run", fake_run)
    record = services.create_backup("manual")
    assert record.status == "echec" and "erreur simulée" in record.message
    assert files(folder) == []


def test_truncated_dump_is_detected_by_the_integrity_check(tenant_a, cfg, folder, monkeypatch):
    make_data(tenant_a, sales=1)
    real_run = services._run

    def fake_run(cmd, cr=None, **kw):
        result = real_run(cmd, cr, **kw)
        if cmd[0].endswith("pg_dump"):
            target = cmd[cmd.index("--file") + 1]
            data = open(target, "rb").read()
            open(target, "wb").write(data[: len(data) // 2])
        return result

    monkeypatch.setattr(services, "_run", fake_run)
    record = services.create_backup("manual")
    assert record.status == "echec" and files(folder) == []


def test_missing_folder_is_recorded_without_crashing(tenant_a, cfg, folder):
    cfg.folder = str(folder / "cle-usb-debranchee")
    cfg.save()
    record = services.create_backup("scheduled")
    assert record.status == "dossier_introuvable" and "introuvable" in record.message


def test_unset_folder_is_reported(tenant_a, db):
    record = services.create_backup("manual")
    assert record.status == "dossier_introuvable" and "Aucun dossier" in record.message


def test_read_only_folder_is_reported(tenant_a, cfg, folder, monkeypatch):
    def refuse(path):
        raise PermissionError("lecture seule")

    monkeypatch.setattr(services, "probe_writable", refuse)
    record = services.create_backup("manual")
    assert record.status == "dossier_introuvable" and "écrire" in record.message


def test_insufficient_free_space_fails_cleanly(tenant_a, cfg, folder, monkeypatch):
    make_data(tenant_a, sales=1)
    monkeypatch.setattr(services, "free_bytes", lambda f: 1024)
    record = services.create_backup("manual")
    assert record.status == "echec" and "Espace insuffisant" in record.message and files(folder) == []


def test_missing_pg_binary_gives_a_clear_message(tenant_a, cfg, monkeypatch):
    monkeypatch.setenv("KOMERSIA_PG_BIN", "/nulle/part")
    record = services.create_backup("manual")
    assert record.status == "echec" and "pg_dump" in record.message and "KOMERSIA_PG_BIN" in record.message


def test_lock_prevents_concurrent_backups(tenant_a, cfg, folder):
    import psycopg
    cr = services.backup_credentials()
    with psycopg.connect(dbname=cr.name, host=cr.host or None, port=cr.port or None, user=cr.user,
                         password=cr.password, autocommit=True) as other:
        assert other.execute("SELECT pg_try_advisory_lock(%s)", [services.LOCK_KEY]).fetchone()[0]
        with pytest.raises(services.BackupBusy):
            services.create_backup("manual")
        other.execute("SELECT pg_advisory_unlock(%s)", [services.LOCK_KEY])
    assert services.create_backup("manual").status == "ok"


def test_validate_folder_rules(tmp_path, folder):
    assert services.validate_folder(str(folder)) == os.path.realpath(folder)
    for bad in ["", "   ", "relatif/dossier", "sauvegardes", str(folder) + "/../x", "..",
                str(folder / "absent"), str(folder / "a\x00b")]:
        with pytest.raises(services.FolderError):
            services.validate_folder(bad)
    f = folder / "fichier.txt"
    f.write_text("x")
    with pytest.raises(services.FolderError):
        services.validate_folder(str(f))
    from django.conf import settings
    with pytest.raises(services.FolderError):
        services.validate_folder(str(settings.BASE_DIR))
    with pytest.raises(services.FolderError):
        services.validate_folder(str(settings.BASE_DIR / "backups"))


def test_tenant_rows_stay_isolated_in_the_ordinary_connection(tenant_a, tenant_b, cfg):
    """La sauvegarde (rôle BYPASSRLS) voit tout, mais l'application reste isolée."""
    make_data(tenant_a, sales=2)
    make_data(tenant_b, sales=1)
    from sales.models import Sale
    with tenant_context(tenant_a.tenant.id):
        assert Sale.objects.count() == 2
    assert count_rows(["sales_sale"])["sales_sale"] == 3

