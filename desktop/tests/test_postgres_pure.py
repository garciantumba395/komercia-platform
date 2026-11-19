import os
from datetime import UTC, datetime

from komersia_desktop import postgres as pgmod
from komersia_desktop.postgres import (
    CONF_BEGIN,
    CONF_END,
    apply_conf_block,
    pre_upgrade_dump_name,
    render_conf_block,
    render_pg_hba,
)


def test_conf_block_listens_on_loopback_only():
    block = render_conf_block(54329, windows=True)
    assert "listen_addresses = '127.0.0.1'" in block and "port = 54329" in block
    assert "scram-sha-256" in block and "unix_socket_directories" not in block
    assert "unix_socket_directories = ''" in render_conf_block(1234, windows=False)
    assert "*" not in block and "0.0.0.0" not in block


def test_apply_conf_block_is_idempotent_and_replaces():
    base = "max_connections = 100\n#listen_addresses = 'localhost'\n"
    once = apply_conf_block(base, render_conf_block(1111, windows=False))
    twice = apply_conf_block(once, render_conf_block(2222, windows=False))
    assert twice.count(CONF_BEGIN) == 1 and twice.count(CONF_END) == 1
    assert "port = 2222" in twice and "port = 1111" not in twice
    assert twice.startswith("max_connections = 100")
    assert apply_conf_block(twice, render_conf_block(2222, windows=False)) == twice


def test_pg_hba_never_trust_and_loopback_only():
    for windows in (True, False):
        hba = render_pg_hba(windows=windows)
        lines = [ln for ln in hba.splitlines() if ln and not ln.startswith("#")]
        assert all("scram-sha-256" in ln for ln in lines)
        assert "trust" not in " ".join(lines) and "0.0.0.0/0" not in hba
        assert any("127.0.0.1/32" in ln for ln in lines)
    assert "local" not in render_pg_hba(windows=True).replace("# ", "").split("host")[0].split()[-1:]


def test_dump_name():
    assert pre_upgrade_dump_name("1.0.0", datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)) == \
        "avant-mise-a-jour-1.0.0-20260102T030405Z.dump"


def test_missing_binary_error(tmp_path):
    import pytest

    from komersia_desktop.config import Secrets, Settings
    from komersia_desktop.paths import Paths

    p = Paths(tmp_path, tmp_path / "d", tmp_path / "nope")
    pg = pgmod.EmbeddedPostgres(p, Secrets.generate(), Settings(), 5555)
    with pytest.raises(pgmod.PostgresError):
        pg.exe("initdb")
    assert os.path.basename(str(pg.bin_dir)) == "nope"
