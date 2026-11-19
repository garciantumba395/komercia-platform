"""Point d'entrée de l'édition locale : `KomersiaServer`.

    KomersiaServer --run       (défaut) base PostgreSQL + serveur web + planificateur, jusqu'à l'arrêt
    KomersiaServer --stop      demande l'arrêt propre de l'instance en cours
    KomersiaServer --status    état, ports, adresses réseau
    KomersiaServer --init      premier lancement seulement (secrets, base, migrations), puis arrêt
    KomersiaServer --open      ouvre le navigateur sur http://localhost:<port>/app/
    KomersiaServer --version
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from datetime import UTC, datetime

from . import __version__, config, network, platform_win, setup
from .lock import AlreadyRunning, InstanceLock, is_locked
from .logs import configure_logging
from .paths import Paths
from .ports import choose_port
from .postgres import EmbeddedPostgres
from .scheduler import Scheduler
from .server import WebServer

log = logging.getLogger("komersia")

EXIT_OK, EXIT_ERROR, EXIT_ALREADY_RUNNING, EXIT_NOT_RUNNING, EXIT_STARTING = 0, 1, 2, 3, 4


def http_health(port: int, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health/", timeout=timeout) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


class KomersiaServer:
    def __init__(self, paths: Paths | None = None, *, pg_run_as: str | None = None):
        self.paths = paths or Paths.from_env()
        self.pg_run_as = pg_run_as or os.environ.get("KOMERSIA_PG_RUN_AS") or None  # tests Linux en root
        self.stop_event = threading.Event()

    # ------------------------------------------------------------------------------------------------ run
    def _install_signal_handlers(self) -> None:
        def handler(signum, _frame):
            log.info("Signal %s reçu : arrêt demandé", signum)
            self.stop_event.set()

        names = ["SIGINT", "SIGTERM"] + (["SIGBREAK"] if sys.platform == "win32" else [])
        for name in names:
            if hasattr(signal, name):
                signal.signal(getattr(signal, name), handler)

    def _bring_up_database(self, prepared: setup.Prepared, *,
                           stop_after: bool) -> tuple[EmbeddedPostgres, int]:
        paths, st = self.paths, prepared.settings
        pg = EmbeddedPostgres(paths, prepared.secrets, st, st.pg_port, run_as=self.pg_run_as)
        if pg.is_initialized() and pg.is_running():
            pg.stop()  # reste d'un lancement interrompu : on repart d'un état propre
        pg.port = choose_port(st.pg_port, "127.0.0.1")
        if pg.port != st.pg_port:
            log.warning("Port PostgreSQL %s occupé : utilisation de %s", st.pg_port, pg.port)
        pg.initdb()
        pg.start()
        pg.ensure_roles_and_database()
        setup.apply_updates(paths, pg)
        if stop_after:
            pg.stop()
        return pg, pg.port

    def init_only(self) -> int:
        self.paths.ensure_dirs()
        try:
            lock = InstanceLock(self.paths.lock_file)
            lock.acquire()
        except AlreadyRunning as exc:
            print(str(exc))
            return EXIT_ALREADY_RUNNING
        configure_logging(self.paths.logs_dir, console=True)
        try:
            prepared = setup.prepare(self.paths)
            self._bring_up_database(prepared, stop_after=True)
            print("Initialisation terminée.")
            return EXIT_OK
        except Exception:
            log.exception("Échec de l'initialisation")
            return EXIT_ERROR
        finally:
            lock.release()

    def run(self) -> int:
        paths = self.paths
        paths.ensure_dirs()
        lock = InstanceLock(paths.lock_file)
        try:
            lock.acquire()
        except AlreadyRunning as exc:
            print(str(exc))
            return EXIT_ALREADY_RUNNING
        configure_logging(paths.logs_dir, console=sys.stderr is not None and sys.stderr.isatty())
        log.info("Komersia %s : démarrage (données : %s)", __version__, paths.data_dir)
        pg = server = scheduler = None
        code = EXIT_OK
        try:
            self._install_signal_handlers()
            paths.stop_request.unlink(missing_ok=True)  # demande d'arrêt périmée
            prepared = setup.prepare(paths)
            st = prepared.settings
            if prepared.first_run:
                log.info("Premier lancement : secrets générés, création de la base (environ une minute)")
            pg, pg_port = self._bring_up_database(prepared, stop_after=False)
            web_port = choose_port(st.web_port, st.bind)
            if web_port != st.web_port:
                log.warning("Port web %s occupé : utilisation de %s", st.web_port, web_port)
            os.environ.update(setup.django_env(paths, role="app", pg_port=pg_port))
            add_backend_to_path()
            import django

            django.setup()
            server = WebServer(st.bind, web_port, threads=st.threads)
            server.start()
            config.write_runtime(paths, config.Runtime(
                pid=os.getpid(), web_port=web_port, pg_port=pg_port, bind=st.bind, version=__version__,
                started_at=datetime.now(UTC).isoformat(timespec="seconds")))
            network.write_address_file(paths.address_file, web_port, st.bind)
            if os.environ.get("KOMERSIA_SCHEDULER", "1") != "0":
                scheduler = Scheduler(initial_delay=float(os.environ.get("KOMERSIA_SCHEDULER_DELAY", "60")))
                scheduler.start()
            log.info("Komersia prêt : http://localhost:%s/app/", web_port)
            code = self._supervise(pg, server)
        except Exception:
            log.exception("Erreur fatale au démarrage ou à l'exécution")
            code = EXIT_ERROR
        finally:
            log.info("Arrêt en cours")
            if scheduler:
                scheduler.stop()
            if server:
                server.stop()
            if pg:
                try:
                    pg.stop()
                except Exception:
                    log.exception("Arrêt de PostgreSQL en échec : arrêt de secours")
                    pg.emergency_stop()
            paths.runtime_file.unlink(missing_ok=True)
            paths.stop_request.unlink(missing_ok=True)
            log.info("Komersia arrêté")
            lock.release()
        return code

    def _supervise(self, pg: EmbeddedPostgres, server: WebServer, poll: float = 1.0) -> int:
        """Boucle principale : attend l'arrêt ; surveille serveur web et PostgreSQL (relance 1 fois)."""
        last_pg_check = time.monotonic()
        pg_restarts = 0
        while not self.stop_event.wait(poll):
            if self.paths.stop_request.exists():
                log.info("Demande d'arrêt reçue (%s)", self.paths.stop_request)
                return EXIT_OK
            if not server.is_alive():
                log.error("Le serveur web s'est arrêté : sortie (le planificateur Windows relancera).")
                return EXIT_ERROR
            if time.monotonic() - last_pg_check >= 30:
                last_pg_check = time.monotonic()
                if not pg.is_running():
                    if pg_restarts >= 1:
                        log.error("PostgreSQL est arrêté de nouveau : sortie.")
                        return EXIT_ERROR
                    pg_restarts += 1
                    log.error("PostgreSQL s'est arrêté : tentative de redémarrage")
                    pg.start()
        return EXIT_OK

    # ------------------------------------------------------------------------------------- stop / status
    def stop_running(self, timeout: float = 90) -> int:
        if not is_locked(self.paths.lock_file):
            self.paths.stop_request.unlink(missing_ok=True)
            print("Komersia n'est pas lancé.")
            return EXIT_OK
        self.paths.control_dir.mkdir(parents=True, exist_ok=True)
        self.paths.stop_request.write_text(datetime.now(UTC).isoformat(), encoding="utf-8")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not is_locked(self.paths.lock_file):
                print("Komersia est arrêté.")
                return EXIT_OK
            time.sleep(0.5)
        print("Komersia ne s'est pas arrêté dans le délai imparti.")
        return EXIT_ERROR

    def status(self) -> dict:
        running = is_locked(self.paths.lock_file)
        rt = config.read_runtime(self.paths) if running else None
        info: dict = {"version": __version__, "running": running, "data_dir": str(self.paths.data_dir)}
        if rt:
            info.update(web_port=rt.web_port, pg_port=rt.pg_port, bind=rt.bind, pid=rt.pid,
                        started_at=rt.started_at, healthy=http_health(rt.web_port),
                        urls=network.access_urls(rt.web_port, rt.bind))
        else:
            info["healthy"] = False
        return info

    def print_status(self, as_json: bool = False) -> int:
        info = self.status()
        if as_json:
            print(json.dumps(info, ensure_ascii=False, indent=2))
        else:
            print(f"Komersia {info['version']}")
            print("État : " + ("en marche" if info["running"] and info["healthy"] else
                               "démarrage en cours" if info["running"] else "arrêté"))
            if info.get("urls"):
                print("Adresse sur ce PC et sur le réseau local :")
                for url in info["urls"]:
                    print(f"  {url}")
                print(f"Ports : web {info['web_port']}, base de données (127.0.0.1) {info['pg_port']}")
            print(f"Données : {info['data_dir']}")
        if not info["running"]:
            return EXIT_NOT_RUNNING
        return EXIT_OK if info["healthy"] else EXIT_STARTING

    def open_browser(self, wait: float = 120) -> int:
        if not is_locked(self.paths.lock_file):
            if platform_win.start_task():
                time.sleep(2)
            else:
                platform_win.message_box(
                    "Komersia", "Komersia n'est pas démarré. Utilisez « Démarrer Komersia » dans le menu "
                    "Démarrer (ou redémarrez le PC).")
                return EXIT_NOT_RUNNING
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            rt = config.read_runtime(self.paths)
            if rt and http_health(rt.web_port):
                webbrowser.open(f"http://localhost:{rt.web_port}/app/")
                return EXIT_OK
            time.sleep(1)
        platform_win.message_box("Komersia", "Komersia met trop de temps à démarrer. Consultez le dossier "
                                 f"des journaux : {self.paths.logs_dir}")
        return EXIT_STARTING


# --------------------------------------------------------------------------------------------- utilitaires
def add_backend_to_path() -> None:
    """En développement (non gelé), rend `komersia`, `core`... importables."""
    if getattr(sys, "frozen", False):
        return
    backend = str(setup.backend_dir())
    if backend not in sys.path:
        sys.path.insert(0, backend)


def run_manage(args: list[str]) -> int:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", setup.SETTINGS_MODULE)
    add_backend_to_path()
    from django.core.management import execute_from_command_line

    execute_from_command_line(["komersia", *args])
    return EXIT_OK


def selftest() -> int:
    """Vérifie, sans base de données, que l'application est complète (utile sur l'exécutable gelé)."""
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", setup.SETTINGS_MODULE)
    os.environ.setdefault("KOMERSIA_EPHEMERAL_SECRETS", "1")
    add_backend_to_path()
    import django

    django.setup()
    from django.apps import apps
    from django.contrib.staticfiles import finders
    from django.db.migrations.loader import MigrationLoader
    from django.template.loader import get_template
    from django.urls import Resolver404, resolve

    problems: list[str] = []
    names = {a.label for a in apps.get_app_configs()}
    for must in ("accounts", "sales", "billing", "web", "core"):
        if must not in names:
            problems.append(f"application absente : {must}")
    if "backoffice" in names:
        problems.append("l'application backoffice ne doit pas être présente en édition locale")
    for url in ("/health/", "/app/connexion/", "/api/v1/auth/register/"):
        try:
            resolve(url)
        except Resolver404:
            problems.append(f"URL absente : {url}")
    for url in ("/ops/", "/api/v1/support/"):
        try:
            resolve(url)
            problems.append(f"URL interdite en local : {url}")
        except Resolver404:
            pass
    try:
        get_template("web/base.html")
    except Exception as exc:  # noqa: BLE001
        problems.append(f"gabarit web/base.html introuvable : {exc}")
    if not finders.find("admin/css/base.css"):
        problems.append("fichiers statiques de l'administration introuvables")
    graph = MigrationLoader(None, ignore_no_migrations=True).graph
    migrated = {key[0] for key in graph.nodes}
    for must in ("accounts", "sales", "catalog"):
        if must not in migrated:
            problems.append(f"migrations introuvables : {must}")
    import cryptography  # noqa: F401
    import psycopg  # noqa: F401
    import waitress  # noqa: F401

    pg_bin = Paths.from_env().pg_bin_dir
    print(f"Komersia {__version__} ; binaires PostgreSQL : {'présents' if pg_bin.exists() else 'absents'}")
    if problems:
        print("AUTO-TEST ÉCHEC :\n  " + "\n  ".join(problems))
        return EXIT_ERROR
    print("AUTO-TEST OK")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="KomersiaServer", description="Komersia, édition locale.")
    group = p.add_mutually_exclusive_group()
    group.add_argument("--run", action="store_true",
                       help="démarre base, serveur web et planificateur (défaut)")
    group.add_argument("--stop", action="store_true", help="arrête proprement l'instance en cours")
    group.add_argument("--status", action="store_true", help="affiche l'état et les adresses")
    group.add_argument("--init", action="store_true", help="premier lancement seulement, puis arrêt")
    group.add_argument("--open", action="store_true", help="ouvre le navigateur sur l'application")
    group.add_argument("--version", action="store_true", help="affiche la version")
    group.add_argument("--selftest", action="store_true", help="vérifie l'installation (sans base)")
    p.add_argument("--json", action="store_true", help="avec --status : sortie JSON")
    p.add_argument("--data-dir", help="dossier de données (défaut : %%PROGRAMDATA%%\\Komersia)")
    p.add_argument("--port", type=int, help="port du serveur web (défaut : 8000, repli si occupé)")
    p.add_argument("--bind", help="adresse d'écoute (défaut 0.0.0.0 ; 127.0.0.1 pour ce PC seulement)")
    return p


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "--manage":  # outil interne : migrate, collectstatic, support
        return run_manage(argv[1:])
    args = build_parser().parse_args(argv)
    if args.data_dir:
        os.environ["KOMERSIA_DATA_DIR"] = args.data_dir
    if args.port:
        os.environ["KOMERSIA_PORT"] = str(args.port)
    if args.bind:
        os.environ["KOMERSIA_BIND"] = args.bind
    if args.version:
        print(__version__)
        return EXIT_OK
    if args.selftest:
        return selftest()
    app = KomersiaServer()
    if args.stop or args.open:
        platform_win.hide_console()
    if args.stop:
        return app.stop_running()
    if args.status:
        return app.print_status(args.json)
    if args.open:
        return app.open_browser()
    if args.init:
        return app.init_only()
    return app.run()


if __name__ == "__main__":
    sys.exit(main())
