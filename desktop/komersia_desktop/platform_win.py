"""Fonctions propres à Windows, isolées pour rester testables (les arguments sont construits par des
fonctions pures) : validées par la CI sur un runner Windows, sans effet ailleurs."""

from __future__ import annotations

import subprocess
import sys

TASK_NAME = "Komersia"


def schtasks_run_args(task: str = TASK_NAME) -> list[str]:
    return ["schtasks", "/Run", "/TN", task]


def schtasks_end_args(task: str = TASK_NAME) -> list[str]:
    return ["schtasks", "/End", "/TN", task]


def is_windows() -> bool:
    return sys.platform == "win32"


def _run(args: list[str]) -> bool:
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=30,
                              creationflags=subprocess.CREATE_NO_WINDOW)  # type: ignore[attr-defined]
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


def start_task() -> bool:
    """Démarre la tâche planifiée « Komersia » (réussit seulement avec les droits suffisants)."""
    return is_windows() and _run(schtasks_run_args())


def end_task() -> bool:
    return is_windows() and _run(schtasks_end_args())


def hide_console() -> None:
    """Cache la fenêtre console (raccourcis « Komersia » / « Arrêter Komersia »)."""
    if not is_windows():
        return
    import ctypes

    hwnd = ctypes.windll.kernel32.GetConsoleWindow()  # type: ignore[attr-defined]
    if hwnd:
        ctypes.windll.user32.ShowWindow(hwnd, 0)  # type: ignore[attr-defined]


def message_box(title: str, text: str) -> None:
    """Affiche un message à l'utilisateur (la console est cachée) ; sinon l'écrit sur la sortie."""
    if is_windows():
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, text, title, 0x40)  # type: ignore[attr-defined]
    else:
        print(f"{title}\n{text}")
