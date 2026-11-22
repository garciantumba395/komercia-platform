import urllib.request

from conftest import free_port

from komersia_desktop.server import WebServer


def app(environ, start_response):
    start_response("200 OK", [("Content-Type", "text/plain")])
    return [b"bonjour"]


def test_waitress_starts_serves_and_stops_cleanly():
    port = free_port()
    server = WebServer("127.0.0.1", port, threads=2, app=app)
    server.start()
    try:
        assert server.is_alive()
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as r:
            assert r.read() == b"bonjour"
    finally:
        server.stop()
    assert not server.is_alive()
    # le port est bien libéré
    from komersia_desktop.ports import is_port_free
    assert is_port_free(port)
