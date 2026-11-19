"""Premier lancement et mises à jour : secrets, migrations (rôle propriétaire), fichiers statiques.

Les migrations et collectstatic s'exécutent dans un PROCESSUS À PART (`--manage`) avec le rôle
`komersia_owner` : le serveur web, lui, ne se connecte jamais qu'avec `komersia_app` (soumis à la RLS).
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from . import config
from .config import Secrets, Settings
from .paths import Paths
from .postgres import EmbeddedPostgres, pre_upgrade_dump_name
from .version import __version__

log = logging.getLogger(__name__)

SETTINGS_MODULE = "komersia.settings_local"
KEEP_UPGRADE_DUMPS = 3


@dataclass
class Prepared:
    settings: Settings
    secrets: Secrets
    first_run: bool


def prepare(paths: Paths, env=None) -> Prepared:
    """Dossiers, réglages et secrets (générés une seule fois, jamais régénérés)."""
    paths.ensure_dirs()
    config.write_default_settings(paths)
    secrets, created = config.load_or_create_secrets(paths)
    return Prepared(config.load_settings(paths, env), secrets, created)


def backend_dir() -> Path:
    explicit = os.environ.get("KOMERSIA_BACKEND_DIR")
    if explicit:
        return Path(explicit)
    return Path(__file__).resolve().parents[2] / "backend"


def django_env(paths: Paths, *, role: str, pg_port: int, secrets: Secrets | None = None,
               base: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ if base is None else base)
    env.update({
        "DJANGO_SETTINGS_MODULE": SETTINGS_MODULE,
        "KOMERSIA_DATA_DIR": str(paths.data_dir),
        "KOMERSIA_INSTALL_DIR": str(paths.install_dir),
        "KOMERSIA_PG_BIN": str(paths.pg_bin_dir),
        "KOMERSIA_DB_PORT": str(pg_port),
        "KOMERSIA_DB_ROLE": role,
    })
    if secrets is not None:
        # Application `backups` : pg_dump exige un rôle BYPASSRLS (komersia_backup, lecture seule) ; la
        # restauration, avec le propriétaire des tables (komersia_owner), jamais avec le rôle applicatif.
        env.update({
            "DB_BACKUP_USER": "komersia_backup", "DB_BACKUP_PASSWORD": secrets.backup_password,
            "DB_RESTORE_USER": "komersia_owner", "DB_RESTORE_PASSWORD": secrets.owner_password,
        })
    if not getattr(sys, "frozen", False):
        desktop = Path(__file__).resolve().parents[1]
        extra = os.pathsep.join([str(desktop), str(backend_dir())])
        env["PYTHONPATH"] = extra + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    return env


def manage_argv(args: list[str]) -> list[str]:
    """Commande qui exécute `manage.py <args>` dans un processus séparé (même exécutable en mode gelé)."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "--manage", *args]
    return [sys.executable, "-m", "komersia_desktop.main", "--manage", *args]


def run_manage(paths: Paths, args: list[str], *, role: str, pg_port: int, secrets: Secrets | None = None,
               timeout: float = 1800) -> subprocess.CompletedProcess:
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0  # type: ignore[attr-defined]
    log.info("manage.py %s (rôle %s)", " ".join(args), role)
    env = django_env(paths, role=role, pg_port=pg_port, secrets=secrets)
    proc = subprocess.run(manage_argv(args), env=env,
                          capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL,
                          creationflags=flags)
    for line in (proc.stdout or "").splitlines():
        log.info("manage: %s", line)
    if proc.returncode != 0:
        raise RuntimeError(f"manage.py {' '.join(args)} a échoué (code {proc.returncode}) :\n"
                           f"{proc.stdout[-2000:]}\n{proc.stderr[-4000:]}")
    return proc


def needs_update(paths: Paths, version: str = __version__) -> tuple[bool, str | None]:
    """(mise à jour nécessaire ?, version précédemment appliquée). Toujours vrai si les statiques manquent."""
    previous = config.read_state(paths).get("version")
    forced = os.environ.get("KOMERSIA_ALWAYS_MIGRATE") == "1"
    static_missing = not (paths.static_root / "admin").exists()
    return (previous != version or forced or static_missing), previous


def prune_upgrade_dumps(safety_dir: Path, keep: int = KEEP_UPGRADE_DUMPS) -> None:
    dumps = sorted(safety_dir.glob("avant-mise-a-jour-*.dump"), key=lambda p: p.stat().st_mtime,
                   reverse=True)
    for old in dumps[keep:]:
        old.unlink(missing_ok=True)


def apply_updates(paths: Paths, pg: EmbeddedPostgres, *, version: str = __version__,
                  manage: Callable[..., object] = run_manage, now: datetime | None = None) -> bool:
    """Migrations (rôle propriétaire) + fichiers statiques si la version a changé. Retourne True si appliqué.

    Avant de migrer une base EXISTANTE vers une nouvelle version, une copie de sécurité est écrite dans le
    dossier des sauvegardes (conservées : les 3 dernières).
    """
    needed, previous = needs_update(paths, version)
    if not needed:
        return False
    if previous is not None and previous != version:
        try:
            target = paths.safety_dir / pre_upgrade_dump_name(previous, now)
            pg.dump_database(target)
            os.chmod(target, 0o600)
            prune_upgrade_dumps(paths.safety_dir)
            log.info("Copie de sécurité avant mise à jour : %s", target)
        except Exception:
            # On ne migre PAS sans filet : mieux vaut un démarrage refusé qu'une base abîmée.
            log.exception("Copie de sécurité avant mise à jour impossible : migration annulée.")
            raise
    manage(paths, ["migrate", "--noinput"], role="owner", pg_port=pg.port, secrets=pg.secrets)
    manage(paths, ["collectstatic", "--noinput", "--clear"], role="owner", pg_port=pg.port,
           secrets=pg.secrets)
    config.write_state(paths, {"version": version,
                               "applied_at": (now or datetime.now(UTC)).isoformat(timespec="seconds")})
    return True
