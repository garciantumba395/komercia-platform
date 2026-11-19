"""Planificateur : appelle périodiquement (toutes les heures) `billing_tick` et `backup_tick`.

L'horloge et l'attente sont injectables : les tests ne dorment pas. Une commande absente
(`backup_tick` est écrite par un autre composant) est ignorée sans erreur ; une commande qui échoue
est journalisée et réessayée au tour suivant, sans arrêter le planificateur.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

DEFAULT_COMMANDS = ("billing_tick", "backup_tick")
HOUR = 3600.0


def django_runner(name: str) -> bool:
    """Exécute la commande Django `name`. Retourne False si elle n'existe pas (ignorée)."""
    from django.core.management import call_command, get_commands
    from django.db import connections

    if name not in get_commands():
        return False
    try:
        call_command(name)
    finally:
        connections.close_all()  # une connexion par fil : ne pas la garder ouverte entre deux passages
    return True


@dataclass
class Job:
    name: str
    next_run: float = 0.0
    runs: int = 0
    failures: int = 0
    skipped: bool = False


@dataclass
class Scheduler:
    commands: tuple[str, ...] = DEFAULT_COMMANDS
    interval: float = HOUR
    initial_delay: float = 60.0
    runner: Callable[[str], bool] = django_runner
    clock: Callable[[], float] = time.time
    stop_event: threading.Event = field(default_factory=threading.Event)
    jobs: dict[str, Job] = field(init=False)
    _thread: threading.Thread | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        start = self.clock() + self.initial_delay
        self.jobs = {c: Job(c, next_run=start) for c in self.commands}

    def run_due(self) -> list[str]:
        """Exécute les tâches échues ; retourne les noms exécutés. Aucune exception ne sort d'ici."""
        now = self.clock()
        ran: list[str] = []
        for job in self.jobs.values():
            # Garde-fou si l'horloge du PC recule : ne jamais attendre plus d'un intervalle.
            if job.next_run - now > self.interval:
                job.next_run = now
            if job.next_run > now:
                continue
            try:
                executed = self.runner(job.name)
            except Exception:  # noqa: BLE001
                job.failures += 1
                log.exception("Tâche planifiée « %s » en échec (nouvel essai au prochain passage)", job.name)
                executed = None
            else:
                if executed:
                    job.runs += 1
                    ran.append(job.name)
                    log.info("Tâche planifiée « %s » exécutée", job.name)
                elif not job.skipped:
                    job.skipped = True
                    log.info("Tâche planifiée « %s » absente : ignorée", job.name)
            # Pas de rattrapage en rafale après une veille prolongée : prochain passage dans 1 intervalle.
            job.next_run = self.clock() + self.interval
        return ran

    def seconds_until_next(self) -> float:
        return max(0.0, min(j.next_run for j in self.jobs.values()) - self.clock()) if self.jobs else HOUR

    def run_forever(self, max_sleep: float = 30.0) -> None:
        while not self.stop_event.is_set():
            self.run_due()
            self.stop_event.wait(max(0.2, min(self.seconds_until_next(), max_sleep)))

    def start(self) -> None:
        self._thread = threading.Thread(target=self.run_forever, name="komersia-scheduler", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 10) -> None:
        self.stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout)
