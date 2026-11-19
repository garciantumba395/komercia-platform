"""Version : générée à la construction (`_build_info.py`), sinon fichier `desktop/VERSION`."""

from pathlib import Path


def _read_version() -> str:
    try:
        from ._build_info import VERSION  # type: ignore[import-not-found]

        return str(VERSION)
    except ImportError:
        pass
    try:
        return (Path(__file__).resolve().parent.parent / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        return "0.0.0+dev"


__version__ = _read_version()
