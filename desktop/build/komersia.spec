# -*- mode: python ; coding: utf-8 -*-
"""Spécification PyInstaller de KomersiaServer (mode DOSSIER).

    pyinstaller desktop/build/komersia.spec --noconfirm --distpath <dist> --workpath <work>

Résultat : <dist>/KomersiaServer/KomersiaServer.exe (+ _internal/). Le serveur est une application
« console » : lancée par la tâche planifiée Windows dans la session 0, elle n'affiche aucune fenêtre ; les
raccourcis (--open, --stop) cachent leur console eux-mêmes (platform_win.hide_console).

Inclut : Django et toutes les applications du backend (sauf `backoffice`, absent de l'édition locale),
leurs gabarits et fichiers statiques, waitress, psycopg binaire, cryptography, tzdata (fuseaux sous Windows).
"""
from pathlib import Path

from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_dynamic_libs,
    collect_submodules,
)

SPEC_DIR = Path(SPECPATH).resolve()  # noqa: F821 - fourni par PyInstaller
DESKTOP = SPEC_DIR.parent
BACKEND = DESKTOP.parent / "backend"

EXCLUDED_PACKAGES = {"backoffice"}          # console d'exploitation : absente de l'édition locale
EXCLUDED_PARTS = {"tests", "__pycache__", "node_modules"}


def backend_modules():
    """Noms pointés de tous les modules du backend (hors tests), pour que PyInstaller les analyse."""
    names = []
    for pkg in sorted(BACKEND.iterdir()):
        if not (pkg.is_dir() and (pkg / "__init__.py").exists()) or pkg.name in EXCLUDED_PACKAGES:
            continue
        for py in sorted(pkg.rglob("*.py")):
            rel = py.relative_to(BACKEND)
            if EXCLUDED_PARTS & set(rel.parts) or py.name.startswith(("test_", "conftest")) or py.name == "tests.py":
                continue
            parts = list(rel.with_suffix("").parts)
            if parts[-1] == "__init__":
                parts.pop()
            names.append(".".join(parts))
    if "komersia.urls" in names:
        names.remove("komersia.urls")  # importe backoffice : inutile en local
    return names


def backend_data():
    """Gabarits et fichiers statiques des applications (dossiers `templates`, `static`, `locale`)."""
    datas = []
    for pkg in sorted(BACKEND.iterdir()):
        if not pkg.is_dir() or pkg.name in EXCLUDED_PACKAGES or pkg.name.startswith("."):
            continue
        for sub in ("templates", "static", "locale"):
            if (pkg / sub).is_dir():
                datas.append((str(pkg / sub), f"{pkg.name}/{sub}"))
    return datas


hiddenimports = backend_modules()
for lib in ("django", "rest_framework", "drf_spectacular", "whitenoise", "waitress", "psycopg",
            "psycopg_binary", "cryptography", "komersia_desktop"):
    hiddenimports += collect_submodules(lib)

datas = backend_data()
for lib in ("django", "rest_framework", "drf_spectacular", "tzdata"):
    datas += collect_data_files(lib)
datas += [(str(DESKTOP / "VERSION"), ".")]

binaries = collect_dynamic_libs("psycopg_binary")

a = Analysis(  # noqa: F821
    [str(SPEC_DIR / "entry.py")],
    pathex=[str(BACKEND), str(DESKTOP)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "pytest", "_pytest", "IPython", "matplotlib", "numpy"],
    noarchive=False,
)
pyz = PYZ(a.pure)  # noqa: F821
exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="KomersiaServer",
    debug=False,
    strip=False,
    upx=False,
    console=True,   # voir l'en-tête : aucune fenêtre n'apparaît sous la tâche planifiée
)
coll = COLLECT(  # noqa: F821
    exe, a.binaries, a.datas,
    strip=False, upx=False,
    name="KomersiaServer",
)
