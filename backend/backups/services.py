"""Sauvegarde et restauration de la base PostgreSQL de l'installation locale (multiplateforme, sans bash).

Équivalent applicatif de `scripts/backup.sh` et `scripts/restore.sh` (voir `docs/dev/sauvegardes-locales.md`).

Point critique : les tables métier sont sous Row-Level Security FORCÉE. `pg_dump` échoue (ou produirait un
fichier vide) avec un rôle ordinaire : il faut un rôle BYPASSRLS ou super-utilisateur, comme `komersia_backup`
créé par `scripts/db-init.sql`. Les identifiants viennent de `DB_BACKUP_USER` / `DB_BACKUP_PASSWORD` ; sans eux,
la configuration de la base par défaut est utilisée (elle ne convient que si ce rôle est super-utilisateur).
La restauration utilise `DB_RESTORE_USER` / `DB_RESTORE_PASSWORD` (propriétaire des tables ou super-utilisateur),
à défaut la configuration de la base par défaut.
"""

# ruff: noqa: E501

import contextlib
import dataclasses
import datetime
import hashlib
import os
import re
import shutil
import subprocess
import uuid
from pathlib import Path

import psycopg
import psycopg.sql
from django.conf import settings
from django.db import connection
from django.utils import timezone

from .models import (
    REASON_MANUAL,
    REASON_PRE_RESTORE,
    REASON_SCHEDULED,
    STATUS_FAILED,
    STATUS_FOLDER_MISSING,
    STATUS_OK,
    BackupRecord,
    BackupSettings,
)

LOCK_KEY = 7_340_122  # verrou consultatif PostgreSQL : une seule sauvegarde ou restauration à la fois
FILE_RE = re.compile(r"^komersia-(\d{8}-\d{6})(?:-(\d+))?\.dump$")
SAFETY_PREFIX = "komersia-avant-restauration-"
PARTIAL_PREFIX = ".komersia-"
SIDECAR_SUFFIX = ".sha256"
MIN_FREE_BYTES = 20 * 1024 * 1024
DUMP_TIMEOUT = 6 * 3600
RETENTION_RANGE = (1, 365)
INTERVAL_RANGE = (1, 168)
LATE_HOURS = 26  # au-delà : « en retard » (orange)
ALERT_HOURS = 48  # au-delà : alerte (rouge) et bandeau global


class BackupError(Exception):
    """Erreur d'exploitation avec un message destiné à l'utilisateur (en français)."""


class BackupBusy(BackupError):
    """Une autre sauvegarde ou restauration est déjà en cours."""


class FolderError(BackupError):
    """Dossier de destination refusé."""


# --------------------------------------------------------------------------- binaires et connexion


def pg_binary(name: str) -> str:
    """Chemin de `pg_dump`, `pg_restore`... : dossier `KOMERSIA_PG_BIN` sinon PATH (gère `.exe`)."""
    directory = os.environ.get("KOMERSIA_PG_BIN", "").strip()
    found = shutil.which(name, path=directory) if directory else shutil.which(name)
    if not found:
        where = f"dans {directory}" if directory else "dans le PATH"
        raise BackupError(f"Le programme PostgreSQL « {name} » est introuvable {where}. "
                          "Vérifiez l'installation de Komersia (variable KOMERSIA_PG_BIN).")
    return found


@dataclasses.dataclass(frozen=True)
class Credentials:
    name: str
    host: str
    port: str
    user: str
    password: str


def _default_credentials() -> Credentials:
    c = connection.settings_dict
    return Credentials(c["NAME"], c.get("HOST") or "", str(c.get("PORT") or ""), c["USER"], c.get("PASSWORD") or "")


def backup_credentials() -> Credentials:
    base = _default_credentials()
    user = os.environ.get("DB_BACKUP_USER", "").strip()
    if not user:
        return base
    return dataclasses.replace(base, user=user, password=os.environ.get("DB_BACKUP_PASSWORD", ""))


def restore_credentials() -> Credentials:
    base = _default_credentials()
    user = os.environ.get("DB_RESTORE_USER", "").strip()
    if not user:
        return base
    return dataclasses.replace(base, user=user, password=os.environ.get("DB_RESTORE_PASSWORD", ""))


def _conn_args(cr: Credentials) -> list[str]:
    args = ["--no-password", "--username", cr.user]
    if cr.host:
        args += ["--host", cr.host]
    if cr.port:
        args += ["--port", cr.port]
    return args


def _run(cmd: list[str], cr: Credentials | None = None, timeout=DUMP_TIMEOUT, extra_env=None):
    env = dict(os.environ)
    if cr is not None and cr.password:
        env["PGPASSWORD"] = cr.password
    elif cr is not None:
        env.pop("PGPASSWORD", None)
    env.update(extra_env or {})
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # pas de fenêtre noire sous Windows
    try:
        return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              env=env, timeout=timeout, creationflags=flags, check=False)
    except subprocess.TimeoutExpired as exc:
        raise BackupError("L'opération PostgreSQL a dépassé le délai autorisé et a été interrompue.") from exc
    except OSError as exc:
        raise BackupError(f"Impossible de lancer {Path(cmd[0]).name} : {exc}") from exc


def _tail(text: str, lines: int = 3) -> str:
    kept = [ln.strip() for ln in (text or "").strip().splitlines() if ln.strip()]
    return " | ".join(kept[-lines:])[:600]


def _connect(cr: Credentials, dbname=None, autocommit=True):
    try:
        return psycopg.connect(dbname=dbname or cr.name, host=cr.host or None, port=cr.port or None,
                               user=cr.user, password=cr.password or None, connect_timeout=10,
                               autocommit=autocommit)
    except psycopg.OperationalError as exc:
        raise BackupError(f"Connexion à la base impossible avec le rôle « {cr.user} » : {_tail(str(exc), 1)}") from exc


def check_backup_role(cr: Credentials):
    """Refuse tôt, avec un message clair, un rôle qui produirait une sauvegarde vide (RLS)."""
    with _connect(cr) as conn:
        row = conn.execute("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user").fetchone()
    if not row or not row[0]:
        raise BackupError(
            f"Le rôle de sauvegarde « {cr.user} » ne peut pas lire toutes les données (Row-Level Security). "
            "Définissez DB_BACKUP_USER et DB_BACKUP_PASSWORD avec le rôle komersia_backup "
            "(attribut BYPASSRLS) ou un super-utilisateur.")


# --------------------------------------------------------------------------- verrou


@contextlib.contextmanager
def backup_lock():
    """Verrou consultatif PostgreSQL : valable entre processus (interface web et planificateur)."""
    with connection.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(%s)", [LOCK_KEY])
        got = cur.fetchone()[0]
    if not got:
        raise BackupBusy("Une sauvegarde ou une restauration est déjà en cours. Réessayez dans quelques minutes.")
    try:
        yield
    finally:
        with connection.cursor() as cur:
            cur.execute("SELECT pg_advisory_unlock(%s)", [LOCK_KEY])


# --------------------------------------------------------------------------- dossier


def _protected_dirs() -> list[str]:
    dirs = [str(settings.BASE_DIR)]
    for var in ("KOMERSIA_DATA_DIR", "KOMERSIA_HOME", "KOMERSIA_INSTALL_DIR"):
        if os.environ.get(var):
            dirs.append(os.environ[var])
    for attr in ("KOMERSIA_DATA_DIR", "KOMERSIA_HOME"):
        if getattr(settings, attr, None):
            dirs.append(str(getattr(settings, attr)))
    try:  # visible seulement pour un super-utilisateur ou pg_read_all_settings
        with connection.cursor() as cur:
            cur.execute("SELECT setting FROM pg_settings WHERE name = 'data_directory'")
            row = cur.fetchone()
            if row and row[0]:
                dirs.append(row[0])
    except Exception:  # noqa: BLE001 - information facultative
        pass
    out = []
    for d in dirs:
        with contextlib.suppress(OSError, ValueError):
            out.append(os.path.normcase(os.path.realpath(d)))
    return out


def _inside(child: str, parent: str) -> bool:
    child, parent = os.path.normcase(child), os.path.normcase(parent)
    return child == parent or child.startswith(parent.rstrip("\\/") + os.sep)


def probe_writable(folder: str):
    probe = Path(folder) / f"{PARTIAL_PREFIX}probe-{uuid.uuid4().hex[:8]}"
    try:
        with open(probe, "wb") as fh:
            fh.write(b"komersia")
    finally:
        with contextlib.suppress(OSError):
            probe.unlink()


def validate_folder(raw) -> str:
    """Valide un dossier de destination et renvoie son chemin réel. Lève FolderError (message français)."""
    if not isinstance(raw, str) or not raw.strip():
        raise FolderError("Indiquez le dossier de sauvegarde.")
    text = raw.strip()
    if "\x00" in text or len(text) > 480:
        raise FolderError("Ce chemin n'est pas valide.")
    if ".." in re.split(r"[\\/]+", text):
        raise FolderError("Le chemin ne doit pas contenir « .. ». Indiquez le chemin complet du dossier.")
    if not os.path.isabs(text):
        raise FolderError("Indiquez un chemin complet, par exemple D:\\Sauvegardes ou la lettre de votre clé USB.")
    if not os.path.isdir(text):
        raise FolderError("Ce dossier n'existe pas ou n'est pas accessible. Créez-le d'abord, "
                          "ou branchez la clé USB.")
    real = os.path.realpath(text)
    for protected in _protected_dirs():
        if _inside(real, protected):
            raise FolderError("Choisissez un dossier en dehors du dossier de Komersia et de ses données : "
                              "une sauvegarde au même endroit serait perdue avec le PC.")
    try:
        probe_writable(real)
    except OSError:
        raise FolderError("Komersia ne peut pas écrire dans ce dossier (accès refusé, disque protégé ou plein)."
                          ) from None
    return real


def folder_state(folder: str) -> tuple[str, str]:
    """État du dossier à l'instant T : ('ok'|'unset'|'missing'|'not_writable', message)."""
    if not folder:
        return "unset", "Aucun dossier de sauvegarde n'est choisi."
    if not os.path.isdir(folder):
        return "missing", (f"Le dossier « {folder} » est introuvable. Si c'est une clé USB, "
                           "branchez-la : la sauvegarde sera refaite automatiquement.")
    try:
        probe_writable(folder)
    except OSError:
        return "not_writable", f"Komersia ne peut pas écrire dans « {folder} » (disque protégé, plein ou accès refusé)."
    return "ok", ""


def free_bytes(folder: str):
    try:
        return shutil.disk_usage(folder).free
    except OSError:
        return None


# --------------------------------------------------------------------------- fichiers


def sha256_file(path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sort_key(name: str):
    m = FILE_RE.match(name)
    return (m.group(1), int(m.group(2) or 0)) if m else ("", 0)


def list_backup_files(folder: str) -> list[Path]:
    """Sauvegardes automatiques du dossier, de la plus récente à la plus ancienne."""
    try:
        names = [n for n in os.listdir(folder) if FILE_RE.match(n)]
    except OSError:
        return []
    return [Path(folder) / n for n in sorted(names, key=_sort_key, reverse=True)]


def _sidecar(path) -> Path:
    return Path(str(path) + SIDECAR_SUFFIX)


def _write_atomic(path: Path, text: str):
    tmp = path.with_name(f"{PARTIAL_PREFIX}{uuid.uuid4().hex[:8]}.partial")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        with contextlib.suppress(OSError):
            tmp.unlink()


def _target_name(folder: str, reason: str, stamp: datetime.datetime) -> Path:
    prefix = SAFETY_PREFIX if reason == REASON_PRE_RESTORE else "komersia-"
    base = f"{prefix}{stamp:%Y%m%d-%H%M%S}"
    candidate, n = Path(folder) / f"{base}.dump", 0
    while candidate.exists():  # jamais d'écrasement
        n += 1
        candidate = Path(folder) / f"{base}-{n}.dump"
    return candidate


def rotate(folder: str, keep: int, protect: Path) -> int:
    """Garde les `keep` dernières sauvegardes (et toujours `protect`, la sauvegarde valide qu'on vient de
    créer). Ne supprime jamais la seule sauvegarde valide. Renvoie le nombre de suppressions."""
    keep = max(1, int(keep))
    files = list_backup_files(folder)
    kept = files[:keep]
    if protect not in kept and protect.exists():
        kept.append(protect)
    removed = 0
    for old in files:
        if old in kept:
            continue
        try:
            old.unlink()
            removed += 1
        except OSError:
            continue
        with contextlib.suppress(OSError):
            _sidecar(old).unlink()
    return removed


# --------------------------------------------------------------------------- lecture d'une archive


def _list_archive(path, cr: Credentials | None = None) -> str:
    result = _run([pg_binary("pg_restore"), "--list", str(path)], cr, timeout=600)
    if result.returncode != 0:
        raise BackupError("Le fichier n'est pas une sauvegarde Komersia lisible ("
                          + (_tail(result.stderr, 2) or "format inconnu") + ").")
    return result.stdout


def _check_listing(listing: str):
    if " TABLE DATA " not in listing or "django_migrations" not in listing:
        raise BackupError("La sauvegarde ne contient aucune donnée (fichier vide ou incomplet).")


def archive_migrations(path) -> set[tuple[str, str]]:
    """Migrations Django enregistrées DANS la sauvegarde (= version de schéma de ses données)."""
    result = _run([pg_binary("pg_restore"), "--data-only", "--table=django_migrations", "-f", "-", str(path)],
                  timeout=600)
    if result.returncode != 0:
        raise BackupError("Impossible de lire la version de la sauvegarde (" + _tail(result.stderr, 2) + ").")
    found, inside = set(), False
    for line in result.stdout.splitlines():
        if line.startswith("COPY ") and "django_migrations" in line:
            inside = True
        elif inside and line.startswith("\\."):
            break
        elif inside:
            parts = line.split("\t")
            if len(parts) >= 3:
                found.add((parts[1], parts[2]))
    if not found:
        raise BackupError("La sauvegarde ne contient pas l'historique des migrations : fichier refusé.")
    return found


def code_migrations() -> set[tuple[str, str]]:
    from django.db.migrations.loader import MigrationLoader

    return set(MigrationLoader(None, ignore_no_migrations=True).disk_migrations.keys())


def expected_sha256(path) -> tuple[str | None, str]:
    """Somme attendue : fichier `.sha256` voisin, sinon journal local. Renvoie (somme|None, origine)."""
    side = _sidecar(path)
    if side.is_file():
        with contextlib.suppress(OSError, IndexError):
            token = side.read_text(encoding="utf-8").split()[0].lower()
            if re.fullmatch(r"[0-9a-f]{64}", token):
                return token, "fichier .sha256"
    rec = BackupRecord.objects.filter(path=str(path), status=STATUS_OK).exclude(sha256="").first()
    if rec:
        return rec.sha256, "journal local"
    return None, ""


# --------------------------------------------------------------------------- journal


def _register(status, reason, message, *, user=None, path="", size=0, sha="", now=None) -> BackupRecord:
    now = now or timezone.now()
    if status != STATUS_OK and reason == REASON_SCHEDULED:
        last = BackupRecord.objects.first()
        if last and last.status == status and last.message == message and last.reason == REASON_SCHEDULED:
            last.attempts += 1
            last.updated_at = now
            last.save(update_fields=["attempts", "updated_at"])
            return last
    return BackupRecord.objects.create(
        path=path, size_bytes=size, sha256=sha, status=status, reason=reason, message=message,
        created_at=now, updated_at=now, created_by=user)


# --------------------------------------------------------------------------- sauvegarde


def _dump(folder: str, reason: str, stamp: datetime.datetime) -> tuple[Path, int, str]:
    cr = backup_credentials()
    check_backup_role(cr)
    free = free_bytes(folder)
    last = BackupRecord.objects.filter(status=STATUS_OK).first()
    needed = max(MIN_FREE_BYTES, int((last.size_bytes if last else 0) * 1.5))
    if free is not None and free < needed:
        raise BackupError(f"Espace insuffisant dans le dossier de sauvegarde ({free // (1024 * 1024)} Mo libres, "
                          f"{needed // (1024 * 1024)} Mo nécessaires). Libérez de la place ou changez de disque.")
    tmp = Path(folder) / f"{PARTIAL_PREFIX}{uuid.uuid4().hex[:8]}.partial"
    final = None
    try:
        result = _run([pg_binary("pg_dump"), *_conn_args(cr), "--dbname", cr.name, "--format=custom",
                       "--compress=6", "--file", str(tmp)], cr)
        if result.returncode != 0:
            hint = ""
            if "row-level security" in result.stderr.lower():
                hint = " Le rôle de sauvegarde doit avoir l'attribut BYPASSRLS (DB_BACKUP_USER)."
            raise BackupError("pg_dump a échoué : " + _tail(result.stderr, 2) + hint)
        if not tmp.is_file() or tmp.stat().st_size == 0:
            raise BackupError("La sauvegarde produite est vide.")
        _check_listing(_list_archive(tmp))  # intégrité immédiate
        digest, size = sha256_file(tmp), tmp.stat().st_size
        final = _target_name(folder, reason, stamp)
        os.replace(tmp, final)
        with contextlib.suppress(OSError):
            _write_atomic(_sidecar(final), f"{digest}  {final.name}\n")
        return final, size, digest
    finally:
        with contextlib.suppress(OSError):
            tmp.unlink()


def create_backup(reason: str = REASON_SCHEDULED, *, user=None, now=None, folder: str | None = None) -> BackupRecord:
    """Crée une sauvegarde et enregistre le résultat (réussite ou échec). Lève BackupBusy si une opération est en
    cours. Un échec ne laisse aucun fichier partiel."""
    if reason not in (REASON_SCHEDULED, REASON_MANUAL, REASON_PRE_RESTORE):
        raise ValueError(f"motif inconnu : {reason}")
    cfg = BackupSettings.get_solo()
    target = folder if folder is not None else cfg.folder
    now = now or timezone.now()
    with backup_lock():
        state, message = folder_state(target)
        if state != "ok":
            return _register(STATUS_FOLDER_MISSING, reason, message, user=user, now=now)
        try:
            path, size, digest = _dump(target, reason, timezone.localtime(now))
        except BackupError as exc:
            return _register(STATUS_FAILED, reason, str(exc), user=user, now=now)
        except Exception as exc:  # noqa: BLE001 - jamais de trace brute pour le commerçant
            return _register(STATUS_FAILED, reason, f"Erreur inattendue pendant la sauvegarde : {exc}", user=user,
                             now=now)
        message = f"Sauvegarde créée ({size / 1024 / 1024:.1f} Mo)."
        if reason != REASON_PRE_RESTORE:
            removed = rotate(target, cfg.retention, path)
            if removed:
                message += f" {removed} ancienne(s) sauvegarde(s) supprimée(s) (rotation)."
        return _register(STATUS_OK, reason, message, user=user, path=str(path), size=size, sha=digest, now=now)


# --------------------------------------------------------------------------- restauration


@dataclasses.dataclass
class RestoreResult:
    restored: str
    safety_backup: str
    pending_migrations: list[str]
    sessions_closed: int
    sessions_left: int


def verify_backup_file(path) -> dict:
    """Vérifie un fichier de sauvegarde sans rien modifier. Lève BackupError s'il est inutilisable."""
    p = Path(path)
    if not p.is_file():
        raise BackupError(f"Fichier de sauvegarde introuvable : {path}")
    expected, origin = expected_sha256(p)
    digest = sha256_file(p)
    if expected and expected != digest:
        raise BackupError("Le fichier est altéré ou incomplet : sa somme de contrôle SHA-256 ne correspond pas "
                          f"à celle enregistrée ({origin}). Restauration refusée.")
    _check_listing(_list_archive(p))
    dumped, known = archive_migrations(p), code_migrations()
    unknown = sorted(f"{a}.{n}" for a, n in dumped - known)
    if unknown:
        raise BackupError("Cette sauvegarde vient d'une version plus récente de Komersia (schéma incompatible : "
                          + ", ".join(unknown[:3]) + "). Mettez Komersia à jour avant de la restaurer.")
    pending = sorted(f"{a}.{n}" for a, n in known - dumped)
    return {"sha256": digest, "checksum_verified": bool(expected), "pending_migrations": pending}


def _close_sessions(cr: Credentials) -> tuple[int, int]:
    connection.ensure_connection()
    own = connection.connection.info.backend_pid
    closed = left = 0
    with _connect(cr) as conn:
        pids = [r[0] for r in conn.execute(
            "SELECT pid FROM pg_stat_activity WHERE datname = current_database() AND pid <> %s "
            "AND pid <> pg_backend_pid()", [own]).fetchall()]
        for pid in pids:
            try:
                conn.execute("SELECT pg_terminate_backend(%s)", [pid])
                closed += 1
            except psycopg.Error:
                left += 1
    return closed, left


def _regrant_app_role(cr: Credentials):
    app_role = _default_credentials().user
    if app_role == cr.user:
        return
    with _connect(cr) as conn:
        if not conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", [app_role]).fetchone():
            return
        ident = psycopg.sql.Identifier(app_role)
        for stmt in ("GRANT USAGE ON SCHEMA public TO {}",
                     "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {}",
                     "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {}"):
            with contextlib.suppress(psycopg.Error):
                conn.execute(psycopg.sql.SQL(stmt).format(ident))


def restore_backup(path, *, safety_folder: str | None = None) -> RestoreResult:
    """Restaure `path` dans la base de l'installation (opération de MAINTENANCE, voir `manage.py restore_backup`).

    Ordre : vérification du fichier (somme, lecture, version de schéma) -> sauvegarde de sécurité de l'état
    actuel -> fermeture des autres connexions -> `pg_restore --clean --if-exists --no-owner` en une seule
    transaction (tout ou rien) -> rétablissement des droits applicatifs et des réglages de sauvegarde.
    """
    p = Path(path).resolve()
    info = verify_backup_file(p)
    cfg = BackupSettings.get_solo()
    saved_settings = {f: getattr(cfg, f) for f in ("enabled", "folder", "retention", "preferred_hour",
                                                   "interval_hours", "created_at")}
    cr = restore_credentials()
    with backup_lock():
        # 1. sauvegarde de sécurité de l'état actuel (dossier configuré, sinon celui du fichier restauré)
        safety = None
        candidates = [safety_folder] if safety_folder else [cfg.folder, str(p.parent)]
        for folder in candidates:
            if folder and folder_state(folder)[0] == "ok":
                safety = create_backup(REASON_PRE_RESTORE, folder=folder)
                break
        if safety is None or safety.status != STATUS_OK:
            why = safety.message if safety else "aucun dossier disponible pour la sauvegarde de sécurité"
            raise BackupError(f"Restauration annulée, rien n'a été modifié : la sauvegarde de sécurité de l'état "
                              f"actuel a échoué ({why}).")
        safety_row = {f.attname: getattr(safety, f.attname) for f in safety._meta.concrete_fields}
        # 2. fermeture des autres connexions (l'application doit être arrêtée : ce n'est qu'un filet de sécurité)
        closed, left = _close_sessions(cr)
        # 3. restauration, tout ou rien (une seule transaction ; délai d'attente des verrous borné)
        cmd = [pg_binary("pg_restore"), *_conn_args(cr), "--dbname", cr.name, "--clean", "--if-exists",
               "--no-owner", "--single-transaction", str(p)]
        result = _run(cmd, cr, extra_env={"PGOPTIONS": "-c lock_timeout=120000"})
        if result.returncode != 0:
            raise BackupError(
                "La restauration a échoué ; la base n'a pas été modifiée. " + _tail(result.stderr, 3)
                + " (Le rôle DB_RESTORE_USER doit être propriétaire des tables ou super-utilisateur ; "
                "l'application doit être arrêtée.)")
        _regrant_app_role(cr)
        # 4. les réglages et le journal reviennent à l'état d'avant la restauration (pas à celui de la copie)
        BackupSettings.objects.update_or_create(id=1, defaults=saved_settings)
        if not BackupRecord.objects.filter(id=safety_row["id"]).exists():
            BackupRecord.objects.create(**safety_row)
    return RestoreResult(str(p), safety.path, info["pending_migrations"], closed, left)


# --------------------------------------------------------------------------- planification et état


def last_success() -> BackupRecord | None:
    return BackupRecord.objects.filter(status=STATUS_OK).exclude(reason=REASON_PRE_RESTORE).first()


def due_at(cfg: BackupSettings, last: BackupRecord | None, now=None):
    """Instant à partir duquel une sauvegarde automatique est due (jamais avant l'heure préférée, sauf si le
    retard dépasse 12 h)."""
    now = now or timezone.now()
    if last is None:
        return now
    candidate = last.created_at + datetime.timedelta(hours=cfg.interval_hours)
    local = timezone.localtime(candidate)
    if local.hour < cfg.preferred_hour:
        target = local.replace(hour=cfg.preferred_hour, minute=0, second=0, microsecond=0)
        candidate = min(target, candidate + datetime.timedelta(hours=12))
    return candidate


def tick(now=None) -> dict:
    """Appel horaire du planificateur. Idempotent ; ne lève jamais pour un dossier absent."""
    now = now or timezone.now()
    cfg = BackupSettings.get_solo()
    if not cfg.enabled:
        return {"action": "disabled"}
    last = last_success()
    if now < due_at(cfg, last, now):
        return {"action": "not_due"}
    state, message = folder_state(cfg.folder)
    cfg.folder_state, cfg.folder_checked_at = state, now
    cfg.save(update_fields=["folder_state", "folder_checked_at"])
    try:
        record = create_backup(REASON_SCHEDULED, now=now)
    except BackupBusy:
        return {"action": "busy"}
    action = {STATUS_OK: "created", STATUS_FAILED: "failed", STATUS_FOLDER_MISSING: "folder_missing"}[record.status]
    return {"action": action, "record": str(record.id), "message": record.message}


def assess(cfg: BackupSettings, last: BackupRecord | None, latest: BackupRecord | None, now=None) -> dict:
    """Santé de la protection des données : ok | late | alert, et affichage (ou non) du bandeau global.

    - alerte (rouge) : dernière sauvegarde réussie de plus de 48 h, jamais sauvegardé 24 h après l'installation,
      ou dernière tentative en échec ;
    - en retard (orange) : plus de 26 h, dossier introuvable, ou pas encore de première sauvegarde.
    """
    now = now or timezone.now()
    failed = latest is not None and latest.status == STATUS_FAILED
    missing = latest is not None and latest.status == STATUS_FOLDER_MISSING
    if last is None:
        banner = (now - cfg.created_at).total_seconds() > 24 * 3600
        return {"health": "alert" if banner or failed else "late", "banner": banner, "age_hours": None}
    age = (now - last.created_at).total_seconds() / 3600
    banner = age > ALERT_HOURS
    if banner or failed:
        health = "alert"
    elif age > LATE_HOURS or missing:
        health = "late"
    else:
        health = "ok"
    return {"health": health, "banner": banner, "age_hours": round(age, 1)}
