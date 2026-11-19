"""Dossiers de l'édition locale : installation, données, journaux, sauvegardes.

- Installation (lecture seule, remplacée à chaque mise à jour) : `C:\\Program Files\\Komersia`.
- Données (conservées à la désinstallation) : `%PROGRAMDATA%\\Komersia`, ou `KOMERSIA_DATA_DIR`.
La logique de dossier de données doit rester identique à `backend/komersia/settings_local.py`.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


def is_windows() -> bool:
    return sys.platform == "win32"


def default_data_dir(env: Mapping[str, str] | None = None, platform: str | None = None) -> Path:
    env = os.environ if env is None else env
    platform = sys.platform if platform is None else platform
    explicit = env.get("KOMERSIA_DATA_DIR")
    if explicit:
        return Path(explicit)
    if platform == "win32":
        return Path(env.get("PROGRAMDATA") or r"C:\ProgramData") / "Komersia"
    return Path.home() / ".local" / "share" / "komersia"


def default_install_dir(env: Mapping[str, str] | None = None) -> Path:
    env = os.environ if env is None else env
    explicit = env.get("KOMERSIA_INSTALL_DIR")
    if explicit:
        return Path(explicit)
    if getattr(sys, "frozen", False):  # PyInstaller : l'exécutable est à la racine de l'installation
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]  # dépôt (développement)


@dataclass(frozen=True)
class Paths:
    install_dir: Path
    data_dir: Path
    pg_bin_dir: Path

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Paths:
        env = os.environ if env is None else env
        install = default_install_dir(env)
        pg_bin = Path(env["KOMERSIA_PG_BIN"]) if env.get("KOMERSIA_PG_BIN") else install / "pgsql" / "bin"
        return cls(install_dir=install, data_dir=default_data_dir(env), pg_bin_dir=pg_bin)

    # Données
    @property
    def config_dir(self) -> Path:
        return self.data_dir / "config"

    @property
    def secrets_file(self) -> Path:
        return self.config_dir / "secrets.json"  # secrets : lisible seulement par le compte du service

    @property
    def settings_file(self) -> Path:
        return self.config_dir / "settings.json"  # réglages non secrets (ports, adresse d'écoute)

    @property
    def runtime_file(self) -> Path:
        return self.config_dir / "runtime.json"  # ports réellement utilisés, pid, version

    @property
    def state_file(self) -> Path:
        return self.config_dir / "state.json"  # version appliquée (migrations)

    @property
    def pgdata_dir(self) -> Path:
        return self.data_dir / "pgdata"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def backups_dir(self) -> Path:
        return self.data_dir / "backups"

    @property
    def static_root(self) -> Path:
        return self.data_dir / "static"

    @property
    def lock_file(self) -> Path:
        return self.data_dir / "komersia.lock"

    @property
    def control_dir(self) -> Path:
        # Seul dossier modifiable par les utilisateurs standard (demande d'arrêt sans droits administrateur).
        return self.data_dir / "control"

    @property
    def stop_request(self) -> Path:
        return self.control_dir / "stop.request"

    @property
    def address_file(self) -> Path:
        return self.data_dir / "ADRESSE.txt"

    @property
    def pycache_dir(self) -> Path:
        return self.data_dir / "pycache"

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.config_dir, self.logs_dir, self.backups_dir, self.control_dir):
            d.mkdir(parents=True, exist_ok=True)
