from datetime import UTC, datetime

import pytest

from komersia_desktop import config, setup
from komersia_desktop.paths import Paths


class FakePg:
    port = 5555

    def __init__(self, fail=False):
        self.dumps, self.fail = [], fail

    def dump_database(self, target):
        if self.fail:
            raise RuntimeError("pg_dump en panne")
        target.write_bytes(b"dump")
        self.dumps.append(target)
        return target


def paths_for(tmp_path):
    p = Paths(tmp_path / "i", tmp_path / "d", tmp_path / "pg")
    p.ensure_dirs()
    (p.static_root / "admin").mkdir(parents=True)
    return p


def test_first_run_migrates_without_dump_then_skips(tmp_path):
    p, calls = paths_for(tmp_path), []
    manage = lambda paths, args, **kw: calls.append((args[0], kw["role"]))  # noqa: E731
    pg = FakePg()
    assert setup.apply_updates(p, pg, version="1.0.0", manage=manage) is True
    assert calls == [("migrate", "owner"), ("collectstatic", "owner")]  # migrations : rôle propriétaire
    assert pg.dumps == []
    assert config.read_state(p)["version"] == "1.0.0"
    calls.clear()
    assert setup.apply_updates(p, pg, version="1.0.0", manage=manage) is False and calls == []


def test_upgrade_takes_safety_dump_before_migrating(tmp_path):
    p, order = paths_for(tmp_path), []
    config.write_state(p, {"version": "1.0.0"})
    pg = FakePg()
    manage = lambda paths, args, **kw: order.append(("manage", len(pg.dumps)))  # noqa: E731
    now = datetime(2026, 5, 1, tzinfo=UTC)
    assert setup.apply_updates(p, pg, version="1.1.0", manage=manage, now=now)
    assert pg.dumps and pg.dumps[0].name.startswith("avant-mise-a-jour-1.0.0-")
    assert order[0] == ("manage", 1)  # la copie existait déjà au moment de migrer
    assert config.read_state(p)["version"] == "1.1.0"


def test_upgrade_aborts_without_migrating_if_dump_fails(tmp_path):
    p, calls = paths_for(tmp_path), []
    config.write_state(p, {"version": "1.0.0"})
    with pytest.raises(RuntimeError):
        setup.apply_updates(p, FakePg(fail=True), version="1.1.0", manage=lambda *a, **k: calls.append(1))
    assert calls == [] and config.read_state(p)["version"] == "1.0.0"


def test_failed_migration_does_not_record_version(tmp_path):
    p = paths_for(tmp_path)

    def manage(*a, **k):
        raise RuntimeError("migration cassée")

    with pytest.raises(RuntimeError):
        setup.apply_updates(p, FakePg(), version="2.0.0", manage=manage)
    assert "version" not in config.read_state(p)


def test_prune_keeps_last_three(tmp_path):
    import os
    d = tmp_path / "b"
    d.mkdir()
    for i in range(6):
        f = d / f"avant-mise-a-jour-1.0.{i}-x.dump"
        f.write_bytes(b"x")
        os.utime(f, (1000 + i, 1000 + i))
    (d / "autre.dump").write_bytes(b"x")
    setup.prune_upgrade_dumps(d)
    assert sorted(x.name for x in d.glob("avant-*")) == [f"avant-mise-a-jour-1.0.{i}-x.dump" for i in (3, 4, 5)]
    assert (d / "autre.dump").exists()


def test_django_env_selects_role_and_port(tmp_path):
    p = paths_for(tmp_path)
    env = setup.django_env(p, role="owner", pg_port=54321, base={})
    assert env["KOMERSIA_DB_ROLE"] == "owner" and env["KOMERSIA_DB_PORT"] == "54321"
    assert env["DJANGO_SETTINGS_MODULE"] == "komersia.settings_local"
    assert env["KOMERSIA_DATA_DIR"] == str(p.data_dir)
