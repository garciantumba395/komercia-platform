"""Permissions de fichiers : réservées au compte du service (icacls sous Windows, chmod sous POSIX).

La construction des arguments d'icacls est une fonction pure (testée sous Linux) ; son exécution réelle est
validée par la CI sur un runner Windows.
"""

from __future__ import annotations

import csv
import io
import os
import subprocess
import sys
from pathlib import Path

SID_SYSTEM = "S-1-5-18"


def parse_whoami_user(output: str) -> str:
    """Extrait le SID de la sortie de `whoami /user /fo csv /nh` (« "domaine\\nom","S-1-5-..." »)."""
    rows = list(csv.reader(io.StringIO(output.strip())))
    if not rows or len(rows[0]) < 2 or not rows[0][1].startswith("S-1-"):
        raise ValueError(f"Sortie de whoami inattendue : {output!r}")
    return rows[0][1]


def build_icacls_args(path: Path, owner_sid: str, *, directory: bool) -> list[str]:
    """Arguments d'icacls : supprime l'héritage et ne laisse que le compte du service et SYSTEM."""
    suffix = "(OI)(CI)F" if directory else "F"
    return [
        "icacls", str(path), "/inheritance:r",
        "/grant:r", f"*{owner_sid}:{suffix}",
        "/grant:r", f"*{SID_SYSTEM}:{suffix}",
    ]


def current_user_sid() -> str:
    result = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"], capture_output=True, text=True,
                            check=True)
    return parse_whoami_user(result.stdout)


def restrict_to_service_account(path: Path) -> None:
    """Rend `path` (fichier ou dossier) illisible pour les autres comptes locaux."""
    path = Path(path)
    if sys.platform == "win32":
        args = build_icacls_args(path, current_user_sid(), directory=path.is_dir())
        subprocess.run(args, capture_output=True, text=True, check=True)
    else:
        os.chmod(path, 0o700 if path.is_dir() else 0o600)
