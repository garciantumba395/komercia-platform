import json
import os
import stat
import sys

import pytest
from conftest import run_python

from komersia_desktop import config
from komersia_desktop.paths import Paths, default_data_dir


def make_paths(tmp_path):
    return Paths(install_dir=tmp_path / "inst", data_dir=tmp_path / "data", pg_bin_dir=tmp_path / "pg")


def test_default_data_dir_variants(tmp_path):
    assert default_data_dir({"KOMERSIA_DATA_DIR": str(tmp_path)}, "linux") == tmp_path
    assert str(default_data_dir({"PROGRAMDATA": r"D:\PD"}, "win32")).replace("\\", "/").endswith("PD/Komersia")
    assert default_data_dir({}, "win32").name == "Komersia"


def test_settings_local_resolves_same_data_dir(tmp_path):
    """backend/komersia/settings_local.py duplique la logique : les deux doivent rester identiques."""
    for env in ({"KOMERSIA_DATA_DIR": str(tmp_path / "x")}, {}):
        r = run_python(
            "import os; os.environ['KOMERSIA_EPHEMERAL_SECRETS']='1';"
            "os.environ['DJANGO_SETTINGS_MODULE']='komersia.settings_local';"
            "import komersia.settings_local as s; print(s.KOMERSIA_DATA_DIR)", **env)
        assert r.returncode == 0, r.stderr
        assert r.stdout.strip() == str(default_data_dir(env or None, sys.platform)) or not env


def test_paths_layout(tmp_path):
    p = make_paths(tmp_path)
    p.ensure_dirs()
    for d in (p.config_dir, p.logs_dir, p.backups_dir, p.control_dir):
        assert d.is_dir() and d.parent == p.data_dir
    assert p.pgdata_dir.parent == p.data_dir and p.secrets_file.parent == p.config_dir


def test_secrets_generated_once_and_private(tmp_path):
    p = make_paths(tmp_path)
    p.ensure_dirs()
    s1, created = config.load_or_create_secrets(p)
    assert created
    mode = stat.S_IMODE(os.stat(p.secrets_file).st_mode)
    assert mode == 0o600, oct(mode)
    s2, created2 = config.load_or_create_secrets(p)
    assert not created2 and s1 == s2  # jamais régénérés


def test_secrets_are_random_distinct_and_not_default(tmp_path):
    a = config.Secrets.generate()
    b = config.Secrets.generate()
    values = list(vars(a).values())
    assert len(set(values)) == len(values)  # tous différents entre eux
    assert a != b
    for v in values:
        assert len(v) >= 40 - 8
        assert v not in {"owner", "app", "ops", "backup", "dev-only-insecure-key-change-me"}
    assert len(a.secret_key) >= 64


def test_corrupted_secrets_not_overwritten(tmp_path):
    p = make_paths(tmp_path)
    p.ensure_dirs()
    p.secrets_file.write_text("{pas du json", encoding="utf-8")
    with pytest.raises(RuntimeError):
        config.load_or_create_secrets(p)
    assert p.secrets_file.read_text(encoding="utf-8") == "{pas du json"


def test_no_leftover_tmp_files(tmp_path):
    p = make_paths(tmp_path)
    p.ensure_dirs()
    config.load_or_create_secrets(p)
    assert [f.name for f in p.config_dir.iterdir()] == ["secrets.json"]


def test_settings_defaults_file_and_env(tmp_path):
    p = make_paths(tmp_path)
    p.ensure_dirs()
    s = config.load_settings(p, {})
    assert (s.web_port, s.pg_port, s.bind) == (8000, 54329, "0.0.0.0")
    p.settings_file.write_text(json.dumps({"web_port": 8100, "bind": "10.0.0.5"}), encoding="utf-8")
    s = config.load_settings(p, {})
    assert (s.web_port, s.bind) == (8100, "10.0.0.5")
    s = config.load_settings(p, {"KOMERSIA_PORT": "9000", "KOMERSIA_BIND": "127.0.0.1"})
    assert (s.web_port, s.bind) == (9000, "127.0.0.1")
    with pytest.raises(ValueError):
        config.load_settings(p, {"KOMERSIA_PORT": "70000"})


def test_write_default_settings_does_not_overwrite(tmp_path):
    p = make_paths(tmp_path)
    p.ensure_dirs()
    p.settings_file.write_text(json.dumps({"web_port": 8123}), encoding="utf-8")
    config.write_default_settings(p)
    assert json.loads(p.settings_file.read_text(encoding="utf-8")) == {"web_port": 8123}


def test_runtime_roundtrip(tmp_path):
    p = make_paths(tmp_path)
    p.ensure_dirs()
    assert config.read_runtime(p) is None
    rt = config.Runtime(pid=1, web_port=8001, pg_port=54330, bind="0.0.0.0", version="1", started_at="x")
    config.write_runtime(p, rt)
    assert config.read_runtime(p) == rt
