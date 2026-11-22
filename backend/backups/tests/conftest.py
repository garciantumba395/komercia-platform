"""Fixtures des tests de sauvegarde : vrai PostgreSQL, vrais pg_dump/pg_restore, bases jetables."""

import os
import shutil
import uuid
from pathlib import Path

import psycopg
import pytest
from django.db import connection

from backups import services
from backups.models import BackupSettings

PG_BIN = "/usr/lib/postgresql/16/bin"


@pytest.fixture(autouse=True)
def backup_env(settings, monkeypatch):
    """Édition locale, binaires PostgreSQL, rôle de sauvegarde komersia_backup (BYPASSRLS, db-init.sql)."""
    settings.KOMERSIA_EDITION = "local"
    if not shutil.which("pg_dump") and Path(PG_BIN).is_dir():
        monkeypatch.setenv("KOMERSIA_PG_BIN", PG_BIN)
    monkeypatch.setenv("DB_BACKUP_USER", os.environ.get("TEST_BACKUP_USER", "komersia_backup"))
    monkeypatch.setenv("DB_BACKUP_PASSWORD", os.environ.get("TEST_BACKUP_PASSWORD", "backup"))
    monkeypatch.delenv("DB_RESTORE_USER", raising=False)
    monkeypatch.delenv("KOMERSIA_BACKUP_DIR", raising=False)


@pytest.fixture
def folder(tmp_path):
    path = tmp_path / "Sauvegardes"
    path.mkdir()
    return path


@pytest.fixture
def cfg(folder, db):
    cfg = BackupSettings.get_solo()
    cfg.folder = str(folder)
    cfg.save()
    return cfg


def admin_conn(dbname="postgres", backup=False):
    cr = services.backup_credentials() if backup else services.restore_credentials()
    return psycopg.connect(dbname=dbname, host=cr.host or None, port=cr.port or None, user=cr.user,
                           password=cr.password or None, autocommit=True)


def count_rows(tables, dbname=None):
    """Nombre de lignes par table, lues avec le rôle de sauvegarde (BYPASSRLS : voit tous les tenants)."""
    with admin_conn(dbname or connection.settings_dict["NAME"], backup=True) as conn:
        return {t: conn.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0] for t in tables}


@pytest.fixture
def scratch_db():
    """Base jetable pour prouver le contenu d'une sauvegarde par restauration."""
    name = f"komersia_bkp_scratch_{uuid.uuid4().hex[:8]}"
    with admin_conn() as conn:
        conn.execute(f'CREATE DATABASE "{name}"')
    yield name
    with admin_conn() as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def restore_into(dump, dbname):
    cr = services.restore_credentials()
    cmd = [services.pg_binary("pg_restore"), *services._conn_args(cr), "--dbname", dbname, "--no-owner",
           "--no-acl", "--exit-on-error", str(dump)]
    result = services._run(cmd, cr)
    assert result.returncode == 0, result.stderr
