"""PostgreSQL EMBARQUÉ : binaires portables fournis par l'installateur dans `<install>\\pgsql\\bin`.

Responsabilités : initdb au premier lancement (authentification scram, écoute sur 127.0.0.1 UNIQUEMENT),
démarrage et arrêt propres avec pg_ctl, attente de disponibilité, création des rôles et de la base avec les
privilèges par défaut de `scripts/db-init.sql`, arrêt de secours.

Tout ce qui touche au système passe par `self._run` (sous-processus) : les parties pures (contenu de
postgresql.conf et de pg_hba.conf, chaînes de connexion) sont testées sans PostgreSQL.
"""

from __future__ import annotations

import logging
import os
import shutil
import signal
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import psycopg
from psycopg import sql

from . import winsec
from .config import PG_SUPERUSER, Secrets, Settings
from .paths import Paths

log = logging.getLogger(__name__)

CONF_BEGIN = "# >>> KOMERSIA (géré automatiquement : ne pas modifier entre ces lignes) >>>"
CONF_END = "# <<< KOMERSIA <<<"
OWNER_ROLE = "komersia_owner"
APP_ROLE = "komersia_app"
BACKUP_ROLE = "komersia_backup"


class PostgresError(RuntimeError):
    pass


def render_conf_block(port: int, *, windows: bool) -> str:
    """Bloc ajouté à postgresql.conf. Écoute sur 127.0.0.1 UNIQUEMENT (jamais le réseau)."""
    lines = [
        CONF_BEGIN,
        "listen_addresses = '127.0.0.1'",
        f"port = {int(port)}",
        "max_connections = 60",
        "password_encryption = 'scram-sha-256'",
        "log_min_messages = warning",
    ]
    if not windows:
        lines.append("unix_socket_directories = ''")  # TCP local seulement : pas de socket Unix
    lines.append(CONF_END)
    return "\n".join(lines) + "\n"


def apply_conf_block(existing: str, block: str) -> str:
    """Remplace le bloc géré (ou l'ajoute à la fin) : idempotent."""
    start = existing.find(CONF_BEGIN)
    end = existing.find(CONF_END)
    if start != -1 and end != -1 and end > start:
        existing = existing[:start].rstrip("\n") + "\n" + existing[end + len(CONF_END):].lstrip("\n")
    return existing.rstrip("\n") + "\n\n" + block


def render_pg_hba(*, windows: bool) -> str:
    """Toutes les connexions exigent un mot de passe scram ; jamais « trust »."""
    lines = ["# Géré par Komersia : connexions locales uniquement, mot de passe scram obligatoire."]
    if not windows:
        lines.append("local   all   all   scram-sha-256")
    lines.append("host    all   all   127.0.0.1/32   scram-sha-256")
    return "\n".join(lines) + "\n"


class EmbeddedPostgres:
    def __init__(self, paths: Paths, secrets: Secrets, settings: Settings, port: int, *,
                 bin_dir: Path | None = None, run_as: str | None = None):
        self.paths = paths
        self.secrets = secrets
        self.settings = settings
        self.port = int(port)
        self.bin_dir = Path(bin_dir or paths.pg_bin_dir)
        self.run_as = run_as  # POSIX seulement (tests en root : initdb refuse de tourner en root)
        self.windows = sys.platform == "win32"

    # --- Sous-processus -------------------------------------------------------------------------------
    def exe(self, name: str) -> Path:
        path = self.bin_dir / (name + (".exe" if self.windows else ""))
        if not path.exists():
            raise PostgresError(f"Binaire PostgreSQL introuvable : {path}")
        return path

    def _env(self, **extra: str) -> dict[str, str]:
        env = {k: v for k, v in os.environ.items() if not k.startswith("PG")}
        if self.windows:
            env["PATH"] = str(self.bin_dir) + os.pathsep + env.get("PATH", "")
        env.update(extra)
        return env

    def _kwargs(self) -> dict:
        kw: dict = {}
        if self.windows:
            kw["creationflags"] = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
        elif self.run_as:
            kw["user"] = self.run_as
            kw["group"] = self.run_as
        return kw

    def _run(self, name: str, args: list[str], *, timeout: float = 120, env: dict | None = None,
             quiet_pipes: bool = False, check: bool = True) -> subprocess.CompletedProcess:
        cmd = [str(self.exe(name)), *args]
        io = ({"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
              if quiet_pipes else {"capture_output": True, "text": True, "stdin": subprocess.DEVNULL})
        try:
            proc = subprocess.run(cmd, timeout=timeout, env=env or self._env(), **io, **self._kwargs())
        except subprocess.TimeoutExpired as exc:
            raise PostgresError(f"{name} : délai dépassé ({timeout:.0f} s).") from exc
        if check and proc.returncode != 0:
            detail = (getattr(proc, "stderr", "") or "").strip()
            raise PostgresError(f"{name} a échoué (code {proc.returncode}). {detail}\n{self.log_tail()}")
        return proc

    def log_tail(self, lines: int = 15) -> str:
        try:
            text = (self.paths.logs_dir / "postgres.log").read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        return "Journal PostgreSQL :\n" + "\n".join(text.splitlines()[-lines:])

    def _chown(self, path: Path) -> None:
        if self.run_as and not self.windows:
            shutil.chown(path, user=self.run_as, group=self.run_as)

    # --- Cycle de vie -----------------------------------------------------------------------------------
    @property
    def datadir(self) -> Path:
        return self.paths.pgdata_dir

    def is_initialized(self) -> bool:
        return (self.datadir / "PG_VERSION").exists()

    def initdb(self) -> None:
        """Crée le cluster. Refuse d'écraser un cluster existant."""
        if self.is_initialized():
            return
        if self.datadir.exists() and any(self.datadir.iterdir()):
            raise PostgresError(
                f"Le dossier {self.datadir} n'est pas vide et n'est pas un cluster PostgreSQL.")
        self.datadir.mkdir(parents=True, exist_ok=True)
        winsec.restrict_to_service_account(self.datadir)  # les données ne doivent pas être lisibles par tous
        self._chown(self.datadir)
        pwfile = self.paths.config_dir / "pgpass.init"
        fd = os.open(pwfile, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(self.secrets.pg_superuser_password + "\n")
        self._chown(pwfile)
        try:
            args = ["-D", str(self.datadir), "-U", PG_SUPERUSER, "--auth=scram-sha-256",
                    f"--pwfile={pwfile}", "-E", "UTF8", "--data-checksums"]
            if self.settings.pg_locale:
                args.append(f"--locale={self.settings.pg_locale}")
            log.info("initdb : création du cluster dans %s", self.datadir)
            self._run("initdb", args, timeout=300)
        except BaseException:
            shutil.rmtree(self.datadir, ignore_errors=True)  # pas de cluster à moitié créé
            raise
        finally:
            pwfile.unlink(missing_ok=True)
        self.write_config()

    def write_config(self) -> None:
        """(Ré)écrit postgresql.conf (bloc géré) et pg_hba.conf à chaque démarrage : auto-réparation."""
        conf = self.datadir / "postgresql.conf"
        text = conf.read_text(encoding="utf-8") if conf.exists() else ""
        conf.write_text(apply_conf_block(text, render_conf_block(self.port, windows=self.windows)),
                        encoding="utf-8")
        (self.datadir / "pg_hba.conf").write_text(render_pg_hba(windows=self.windows), encoding="utf-8")

    def is_running(self) -> bool:
        if not self.is_initialized():
            return False
        return self._run("pg_ctl", ["status", "-D", str(self.datadir)], check=False, timeout=30,
                         quiet_pipes=True).returncode == 0

    def read_postmaster_pid(self) -> int | None:
        try:
            first = (self.datadir / "postmaster.pid").read_text(encoding="ascii").splitlines()[0]
            return int(first)
        except (OSError, ValueError, IndexError):
            return None

    def start(self, timeout: float = 90) -> None:
        if not self.is_initialized():
            raise PostgresError("Le cluster PostgreSQL n'est pas initialisé.")
        self.paths.logs_dir.mkdir(parents=True, exist_ok=True)
        logfile = self.paths.logs_dir / "postgres.log"
        logfile.touch(exist_ok=True)
        self._chown(logfile)
        self.write_config()
        if self.is_running():
            log.warning("PostgreSQL tournait déjà (reste d'un lancement précédent) : reprise.")
            self._restart_if_port_differs()
        else:
            log.info("Démarrage de PostgreSQL sur 127.0.0.1:%s", self.port)
            self._run("pg_ctl", ["start", "-D", str(self.datadir), "-l", str(logfile), "-w",
                                 "-t", str(int(timeout))], timeout=timeout + 30, quiet_pipes=True)
        self.wait_ready(timeout)

    def _restart_if_port_differs(self) -> None:
        # Un PostgreSQL resté actif écoute sur l'ancien port : on le relance avec la configuration à jour.
        try:
            running_conf = (self.datadir / "postmaster.pid").read_text(encoding="ascii").splitlines()
            running_port = int(running_conf[3]) if len(running_conf) > 3 else self.port
        except (OSError, ValueError):
            running_port = self.port
        if running_port != self.port:
            log.info("Port PostgreSQL modifié (%s -> %s) : redémarrage.", running_port, self.port)
            self.stop()
            self._run("pg_ctl", ["start", "-D", str(self.datadir), "-l",
                                 str(self.paths.logs_dir / "postgres.log"), "-w"], timeout=120,
                      quiet_pipes=True)

    def connect(self, *, user: str, password: str, dbname: str, timeout: int = 5, autocommit: bool = False):
        return psycopg.connect(host="127.0.0.1", port=self.port, user=user, password=password, dbname=dbname,
                               connect_timeout=timeout, autocommit=autocommit)

    def connect_admin(self, dbname: str = "postgres", **kw):
        return self.connect(user=PG_SUPERUSER, password=self.secrets.pg_superuser_password, dbname=dbname,
                            **kw)

    def wait_ready(self, timeout: float = 60) -> None:
        deadline = time.monotonic() + timeout
        last: Exception | None = None
        while time.monotonic() < deadline:
            try:
                with self.connect_admin(autocommit=True) as conn:
                    conn.execute("SELECT 1")
                return
            except psycopg.Error as exc:
                last = exc
                time.sleep(0.5)
        raise PostgresError(
            f"PostgreSQL n'est pas disponible après {timeout:.0f} s ({last}).\n{self.log_tail()}")

    def stop(self, timeout: float = 60) -> None:
        """Arrêt propre (mode fast) ; en cas d'échec, arrêt immédiat puis arrêt de secours."""
        if not self.is_initialized() or not self.is_running():
            return
        log.info("Arrêt de PostgreSQL")
        proc = self._run("pg_ctl", ["stop", "-D", str(self.datadir), "-m", "fast", "-w", "-t",
                                    str(int(timeout))], timeout=timeout + 30, check=False, quiet_pipes=True)
        if proc.returncode != 0 or self.is_running():
            log.error("Arrêt normal de PostgreSQL impossible : arrêt de secours.")
            self.emergency_stop()

    def emergency_stop(self) -> None:
        """Dernier recours : arrêt immédiat (récupération automatique au prochain démarrage), puis kill."""
        try:
            self._run("pg_ctl", ["stop", "-D", str(self.datadir), "-m", "immediate", "-w", "-t", "20"],
                      timeout=60, check=False, quiet_pipes=True)
        except PostgresError:
            pass
        pid = self.read_postmaster_pid()
        if pid is None:
            return
        try:
            if self.windows:
                subprocess.run(["taskkill", "/PID", str(pid), "/F", "/T"], capture_output=True,
                               creationflags=subprocess.CREATE_NO_WINDOW)  # type: ignore[attr-defined]
            else:
                os.kill(pid, signal.SIGKILL)
        except (OSError, subprocess.SubprocessError):
            pass

    # --- Rôles, base, privilèges (équivalent de scripts/db-init.sql, sans rôle `ops`) ---
    def ensure_roles_and_database(self) -> None:
        name = self.settings.db_name
        with self.connect_admin(autocommit=True) as conn:
            self._ensure_role(conn, OWNER_ROLE, self.secrets.owner_password, "CREATEDB NOSUPERUSER")
            self._ensure_role(conn, APP_ROLE, self.secrets.app_password,
                              "NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE")
            # Sauvegarde : lecture seule MAIS BYPASSRLS (les tables ont FORCE ROW LEVEL SECURITY).
            self._ensure_role(conn, BACKUP_ROLE, self.secrets.backup_password,
                              "BYPASSRLS NOSUPERUSER NOCREATEDB NOCREATEROLE")
            conn.execute(sql.SQL("GRANT pg_read_all_data TO {}").format(sql.Identifier(BACKUP_ROLE)))
            exists = conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone()
            if not exists:
                conn.execute(sql.SQL("CREATE DATABASE {} OWNER {} ENCODING 'UTF8'").format(
                    sql.Identifier(name), sql.Identifier(OWNER_ROLE)))
            conn.execute(sql.SQL("ALTER DATABASE {} OWNER TO {}").format(
                sql.Identifier(name), sql.Identifier(OWNER_ROLE)))
            conn.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(name)))
            conn.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}, {}").format(
                sql.Identifier(name), sql.Identifier(APP_ROLE), sql.Identifier(BACKUP_ROLE)))
        with self.connect_admin(name, autocommit=True) as conn:
            for stmt in (
                "ALTER DEFAULT PRIVILEGES FOR ROLE komersia_owner IN SCHEMA public "
                "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO komersia_app",
                "ALTER DEFAULT PRIVILEGES FOR ROLE komersia_owner IN SCHEMA public "
                "GRANT USAGE, SELECT ON SEQUENCES TO komersia_app",
                "GRANT USAGE ON SCHEMA public TO komersia_app",
                # Réparation idempotente si des tables existent déjà (mise à jour, restauration).
                "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO komersia_app",
                "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO komersia_app",
            ):
                conn.execute(stmt)

    @staticmethod
    def _ensure_role(conn, role: str, password: str, attributes: str) -> None:
        exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone()
        verb = "ALTER" if exists else "CREATE"
        # Le mot de passe est rejoué à chaque démarrage : le fichier de secrets fait foi.
        stmt = sql.SQL(verb + " ROLE {} WITH LOGIN PASSWORD {} " + attributes).format(
            sql.Identifier(role), sql.Literal(password))
        conn.execute(stmt)

    # --- Sauvegarde de sécurité avant mise à jour -----------------------------------------------------------
    def dump_database(self, target: Path) -> Path:
        target.parent.mkdir(parents=True, exist_ok=True)
        self._run("pg_dump", ["-h", "127.0.0.1", "-p", str(self.port), "-U", PG_SUPERUSER, "-Fc",
                              "-f", str(target), self.settings.db_name], timeout=1800,
                  env=self._env(PGPASSWORD=self.secrets.pg_superuser_password))
        return target

    def database_exists(self) -> bool:
        with self.connect_admin(autocommit=True) as conn:
            return bool(conn.execute("SELECT 1 FROM pg_database WHERE datname = %s",
                                     (self.settings.db_name,)).fetchone())


def pre_upgrade_dump_name(old_version: str, now: datetime | None = None) -> str:
    now = now or datetime.now(UTC)
    return f"avant-mise-a-jour-{old_version}-{now:%Y%m%dT%H%M%SZ}.dump"
