"""Journalisation avec rotation dans le dossier des journaux."""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
_HANDLER_ATTR = "_komersia_handler"


def configure_logging(logs_dir: Path, *, max_bytes: int = 5_000_000, backup_count: int = 5,
                      level: int = logging.INFO, console: bool = False,
                      filename: str = "komersia.log") -> RotatingFileHandler:
    """Installe (une seule fois) un RotatingFileHandler sur le logger racine ; renvoie le gestionnaire."""
    logs_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    for h in list(root.handlers):
        if getattr(h, _HANDLER_ATTR, False):
            root.removeHandler(h)
            h.close()
    handler = RotatingFileHandler(logs_dir / filename, maxBytes=max_bytes, backupCount=backup_count,
                                  encoding="utf-8")
    handler.setFormatter(logging.Formatter(FORMAT))
    setattr(handler, _HANDLER_ATTR, True)
    root.addHandler(handler)
    if console and not any(getattr(h, "_komersia_console", False) for h in root.handlers):
        ch = logging.StreamHandler(sys.stderr)
        ch.setFormatter(logging.Formatter(FORMAT))
        ch._komersia_console = True  # type: ignore[attr-defined]
        root.addHandler(ch)
    root.setLevel(level)
    # Waitress journalise les erreurs d'application sous « waitress » ; Django sous « django ».
    for name in ("waitress", "django"):
        logging.getLogger(name).setLevel(level)
    return handler
