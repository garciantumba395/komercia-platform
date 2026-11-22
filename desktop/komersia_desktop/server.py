"""Serveur HTTP waitress (WSGI de `komersia.wsgi`) dans un fil d'exécution, arrêtable proprement."""

from __future__ import annotations

import logging
import threading

log = logging.getLogger(__name__)


class WebServer:
    def __init__(self, host: str, port: int, *, threads: int = 8, app=None):
        self.host = host
        self.port = int(port)
        self.threads = threads
        self._app = app
        self._server = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        import waitress

        app = self._app
        if app is None:
            from komersia.wsgi import application as app  # noqa: PLC0415 - après réglage de l'environnement
        # Le lien (bind) a lieu ici : une erreur « port occupé » remonte immédiatement à l'appelant.
        self._server = waitress.create_server(
            app, host=self.host, port=self.port, threads=self.threads, ident="Komersia",
            channel_timeout=300, connection_limit=200, max_request_body_size=64 * 1024 * 1024,
            expose_tracebacks=False,
        )
        self._thread = threading.Thread(target=self._server.run, name="komersia-web", daemon=True)
        self._thread.start()
        log.info("Serveur web à l'écoute sur %s:%s (%s fils)", self.host, self.port, self.threads)

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def stop(self, timeout: float = 15) -> None:
        if self._server is not None:
            try:
                self._server.close()
            except Exception:  # noqa: BLE001
                log.exception("Erreur à la fermeture du serveur web")
        if self._thread is not None:
            self._thread.join(timeout)
        self._server = self._thread = None
        log.info("Serveur web arrêté")
