import os
import pwd
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import pytest

DESKTOP = Path(__file__).resolve().parents[1]
BACKEND = DESKTOP.parent / "backend"
PG_BIN_CANDIDATES = [Path(p) for p in ("/usr/lib/postgresql/16/bin", "/usr/lib/postgresql/17/bin",
                                        "/usr/lib/postgresql/15/bin")]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def find_pg_bin() -> Path | None:
    explicit = os.environ.get("KOMERSIA_TEST_PG_BIN")
    for candidate in ([Path(explicit)] if explicit else []) + PG_BIN_CANDIDATES:
        if (candidate / "initdb").exists():
            return candidate
    found = shutil.which("initdb")
    return Path(found).parent if found else None


def pg_run_as() -> str | None | bool:
    """None : lancer avec l'utilisateur courant ; « postgres » : en root ; False : impossible (test ignoré)."""
    if os.name == "nt":
        return None
    if os.geteuid() != 0:
        return None
    try:
        pwd.getpwnam("postgres")
    except KeyError:
        return False
    return "postgres"


def subprocess_env(**extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("KOMERSIA_", "DJANGO_", "DB_"))}
    env["PYTHONPATH"] = os.pathsep.join([str(DESKTOP), str(BACKEND)])
    env.update(extra)
    return env


def run_python(code: str, **env) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-c", code], env=subprocess_env(**env), capture_output=True,
                          text=True, timeout=120)


@pytest.fixture
def data_dir(tmp_path) -> Path:
    d = tmp_path / "data"
    d.mkdir()
    return d
