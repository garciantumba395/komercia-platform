"""Secrets, réglages et état d'exécution de l'installation (fichiers JSON du dossier de données)."""

from __future__ import annotations

import json
import os
import secrets
import tempfile
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import winsec
from .paths import Paths

DEFAULT_WEB_PORT = 8000
DEFAULT_PG_PORT = 54329  # loin de 5432 pour ne pas gêner un autre PostgreSQL du poste
DEFAULT_BIND = "0.0.0.0"  # noqa: S104 - nécessaire : les autres appareils du réseau local se connectent
PG_SUPERUSER = "komersia_pg"
DB_NAME = "komersia"


def _random_secret(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)  # caractères sûrs dans une chaîne de connexion et dans du JSON


@dataclass
class Secrets:
    secret_key: str
    pg_superuser_password: str
    owner_password: str
    app_password: str
    backup_password: str

    @classmethod
    def generate(cls) -> Secrets:
        return cls(
            secret_key=secrets.token_urlsafe(64),
            pg_superuser_password=_random_secret(),
            owner_password=_random_secret(),
            app_password=_random_secret(),
            backup_password=_random_secret(),
        )


def _atomic_write(path: Path, text: str, *, private: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:  # mkstemp crée en 0600 (POSIX)
            fh.write(text)
        if private:
            winsec.restrict_to_service_account(tmp)
        else:
            os.chmod(tmp, 0o644)  # lisible par les autres comptes (ports, état) ; sans effet sur les secrets
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def load_secrets(paths: Paths) -> Secrets | None:
    try:
        raw = json.loads(paths.secrets_file.read_text(encoding="utf-8-sig"))
        return Secrets(**{k: raw[k] for k in Secrets.__dataclass_fields__})
    except FileNotFoundError:
        return None
    except (ValueError, KeyError, TypeError) as exc:
        raise RuntimeError(
            f"Le fichier de secrets {paths.secrets_file} est illisible ou incomplet : ne le supprimez pas "
            "(les mots de passe de la base en dépendent). Restaurez-le depuis une sauvegarde."
        ) from exc


def load_or_create_secrets(paths: Paths) -> tuple[Secrets, bool]:
    """Retourne (secrets, créés_maintenant). Les secrets ne sont JAMAIS régénérés s'ils existent."""
    existing = load_secrets(paths)
    if existing is not None:
        return existing, False
    new = Secrets.generate()
    _atomic_write(paths.secrets_file, json.dumps(asdict(new), indent=2), private=True)
    return new, True


@dataclass
class Settings:
    web_port: int = DEFAULT_WEB_PORT
    pg_port: int = DEFAULT_PG_PORT
    bind: str = DEFAULT_BIND
    threads: int = 8
    db_name: str = DB_NAME
    pg_locale: str | None = None  # None : locale du système (Windows) ; tests Linux : C.UTF-8
    extra: dict = field(default_factory=dict)

    def validate(self) -> None:
        for name in ("web_port", "pg_port"):
            if not 1 <= int(getattr(self, name)) <= 65535:
                raise ValueError(f"{name} invalide : {getattr(self, name)}")
        if not self.bind:
            raise ValueError("bind ne peut pas être vide")


def load_settings(paths: Paths, env: Mapping[str, str] | None = None) -> Settings:
    """Fichier settings.json puis variables d'environnement KOMERSIA_PORT, KOMERSIA_BIND, KOMERSIA_PG_PORT."""
    env = os.environ if env is None else env
    s = Settings()
    try:
        raw = json.loads(paths.settings_file.read_text(encoding="utf-8-sig"))
    except (FileNotFoundError, ValueError):
        raw = {}
    for key in ("web_port", "pg_port", "threads"):
        if key in raw:
            setattr(s, key, int(raw[key]))
    for key in ("bind", "db_name", "pg_locale"):
        if raw.get(key):
            setattr(s, key, str(raw[key]))
    if env.get("KOMERSIA_PORT"):
        s.web_port = int(env["KOMERSIA_PORT"])
    if env.get("KOMERSIA_PG_PORT"):
        s.pg_port = int(env["KOMERSIA_PG_PORT"])
    if env.get("KOMERSIA_BIND"):
        s.bind = env["KOMERSIA_BIND"]
    if env.get("KOMERSIA_PG_LOCALE"):
        s.pg_locale = env["KOMERSIA_PG_LOCALE"]
    s.validate()
    return s


def write_default_settings(paths: Paths) -> None:
    """Crée settings.json commenté par ses valeurs par défaut, sans écraser un fichier existant."""
    if paths.settings_file.exists():
        return
    s = Settings()
    data = {"web_port": s.web_port, "pg_port": s.pg_port, "bind": s.bind, "threads": s.threads}
    _atomic_write(paths.settings_file, json.dumps(data, indent=2), private=False)


@dataclass
class Runtime:
    """Ports réellement utilisés par l'instance en cours (peuvent différer des préférés : repli)."""

    pid: int
    web_port: int
    pg_port: int
    bind: str
    version: str
    started_at: str


def write_runtime(paths: Paths, rt: Runtime) -> None:
    _atomic_write(paths.runtime_file, json.dumps(asdict(rt), indent=2), private=False)


def read_runtime(paths: Paths) -> Runtime | None:
    try:
        return Runtime(**json.loads(paths.runtime_file.read_text(encoding="utf-8-sig")))
    except (OSError, ValueError, TypeError):
        return None


def read_state(paths: Paths) -> dict:
    try:
        data = json.loads(paths.state_file.read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def write_state(paths: Paths, state: dict) -> None:
    _atomic_write(paths.state_file, json.dumps(state, indent=2), private=False)
