"""Verrou d'instance : empêche deux lanceurs simultanés sur les mêmes données.

Verrou noyau (flock / LockFileEx) sur un octet éloigné du début : libéré automatiquement si le processus
meurt, donc jamais de verrou « périmé ». Le PID est écrit au début du fichier (lisible par les autres).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_LOCK_OFFSET = 4096


class AlreadyRunning(RuntimeError):
    def __init__(self, pid: int | None):
        self.pid = pid
        super().__init__(f"Komersia est déjà lancé (PID {pid})." if pid else "Komersia est déjà lancé.")


def read_pid(path: Path) -> int | None:
    try:
        text = Path(path).read_bytes()[:32].decode("ascii", "ignore").strip().strip("\x00")
        return int(text) if text.isdigit() else None
    except OSError:
        return None


class InstanceLock:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._fd: int | None = None

    def _try_lock(self, fd: int) -> bool:
        os.lseek(fd, _LOCK_OFFSET, os.SEEK_SET)
        if sys.platform == "win32":
            import msvcrt

            try:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)  # type: ignore[attr-defined]
            except OSError:
                return False
            return True
        import fcntl

        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        return True

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
        if not self._try_lock(fd):
            os.close(fd)
            raise AlreadyRunning(read_pid(self.path))
        self._fd = fd
        os.lseek(fd, 0, os.SEEK_SET)
        os.write(fd, str(os.getpid()).encode("ascii").ljust(32))

    def release(self) -> None:
        if self._fd is None:
            return
        fd, self._fd = self._fd, None
        try:
            if sys.platform == "win32":
                import msvcrt

                os.lseek(fd, _LOCK_OFFSET, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)  # type: ignore[attr-defined]
        except OSError:
            pass
        finally:
            os.close(fd)

    def __enter__(self) -> InstanceLock:
        self.acquire()
        return self

    def __exit__(self, *exc) -> None:
        self.release()


def is_locked(path: Path) -> bool:
    """Vrai si une autre instance détient le verrou (essaie de le prendre puis le relâche)."""
    if not Path(path).exists():
        return False
    probe = InstanceLock(path)
    try:
        probe.acquire()
    except AlreadyRunning:
        return True
    probe.release()
    return False
