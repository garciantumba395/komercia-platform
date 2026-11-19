import logging
import socket
import subprocess
import sys
import textwrap

import pytest

from komersia_desktop import network
from komersia_desktop.lock import AlreadyRunning, InstanceLock, is_locked, read_pid
from komersia_desktop.logs import configure_logging
from komersia_desktop.ports import NoFreePort, choose_port, is_port_free


def test_choose_port_prefers_free_then_falls_back():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        busy = s.getsockname()[1]
        assert not is_port_free(busy)
        chosen = choose_port(busy, "127.0.0.1")
        assert chosen != busy and chosen > busy
    assert choose_port(busy, "127.0.0.1") == busy  # libéré


def test_choose_port_wildcard_checks_loopback_too():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        busy = s.getsockname()[1]
        assert choose_port(busy, "0.0.0.0") != busy  # noqa: S104


def test_choose_port_exhausted_and_invalid():
    with pytest.raises(NoFreePort):
        choose_port(8000, attempts=3, is_free=lambda p, h: False)
    with pytest.raises(ValueError):
        choose_port(0)


def test_instance_lock(tmp_path):
    path = tmp_path / "k.lock"
    a = InstanceLock(path)
    a.acquire()
    assert read_pid(path) is not None
    with pytest.raises(AlreadyRunning):
        InstanceLock(path).acquire()
    assert is_locked(path)
    a.release()
    assert not is_locked(path)
    with InstanceLock(path):
        assert is_locked(path)


def test_lock_released_when_process_dies(tmp_path):
    path = tmp_path / "k.lock"
    code = textwrap.dedent(f"""
        import sys, time
        from komersia_desktop.lock import InstanceLock
        l = InstanceLock({str(path)!r}); l.acquire(); print("ok", flush=True); time.sleep(60)
    """)
    import os

    from conftest import subprocess_env
    p = subprocess.Popen([sys.executable, "-c", code], env=subprocess_env(), stdout=subprocess.PIPE, text=True)
    try:
        assert p.stdout.readline().strip() == "ok"
        assert is_locked(path)
        assert read_pid(path) == p.pid
    finally:
        p.kill()
        p.wait()
    assert not is_locked(path)
    del os


def test_log_rotation(tmp_path):
    handler = configure_logging(tmp_path / "logs", max_bytes=2000, backup_count=2, level=logging.INFO)
    try:
        log = logging.getLogger("rotation-test")
        for i in range(200):
            log.info("ligne numéro %d avec un peu de texte pour grossir le fichier", i)
        handler.flush()
        names = sorted(p.name for p in (tmp_path / "logs").iterdir())
        assert names == ["komersia.log", "komersia.log.1", "komersia.log.2"]  # rotation bornée
        # reconfigurer ne duplique pas le gestionnaire
        configure_logging(tmp_path / "logs", max_bytes=2000, backup_count=2)
        assert sum(getattr(h, "_komersia_handler", False) for h in logging.getLogger().handlers) == 1
    finally:
        for h in list(logging.getLogger().handlers):
            if getattr(h, "_komersia_handler", False):
                logging.getLogger().removeHandler(h)
                h.close()


def test_address_text_lan_and_local_only(tmp_path):
    text = network.render_address_text(8000, "0.0.0.0", hostname="PC-BOUTIQUE", ips=["192.168.1.12"])  # noqa: S104
    assert "http://PC-BOUTIQUE:8000/app/" in text and "http://192.168.1.12:8000/app/" in text
    local = network.render_address_text(8000, "127.0.0.1", hostname="PC", ips=["192.168.1.12"])
    assert "192.168.1.12" not in local and "http://localhost:8000/app/" in local
    f = tmp_path / "ADRESSE.txt"
    network.write_address_file(f, 8001, "0.0.0.0", hostname="X", ips=["10.0.0.2"])  # noqa: S104
    assert "10.0.0.2:8001" in f.read_text(encoding="utf-8-sig")


def test_local_ips_exclude_loopback():
    assert all(not ip.startswith("127.") for ip in network.local_ip_addresses())
