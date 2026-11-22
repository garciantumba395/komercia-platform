"""Choix de port avec repli si le port préféré est occupé."""

from __future__ import annotations

import socket
import sys


class NoFreePort(RuntimeError):
    pass


def is_port_free(port: int, host: str = "127.0.0.1") -> bool:
    """Vrai si on peut s'y lier. Sous Windows, SO_EXCLUSIVEADDRUSE évite de « voler » un port déjà pris."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if sys.platform == "win32":
            s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)  # type: ignore[attr-defined]
        else:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
        except OSError:
            return False
        return True


def choose_port(preferred: int, host: str = "127.0.0.1", *, attempts: int = 50,
                is_free=is_port_free) -> int:
    """`preferred` s'il est libre, sinon le premier libre parmi les `attempts` suivants."""
    if not 1 <= preferred <= 65535:
        raise ValueError(f"Port invalide : {preferred}")
    for offset in range(attempts):
        candidate = preferred + offset
        if candidate > 65535:
            break
        # Le serveur web écoute sur toutes les interfaces : tester aussi 127.0.0.1 (occupation locale).
        if is_free(candidate, host) and (host == "127.0.0.1" or is_free(candidate, "127.0.0.1")):
            return candidate
    raise NoFreePort(f"Aucun port libre trouvé à partir de {preferred}.")
