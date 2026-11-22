"""settings_local et urls_local (backend/komersia) : lus dans un processus séparé (réglages Django isolés)."""

import json

from conftest import run_python

from komersia_desktop import config
from komersia_desktop.paths import Paths

LOCAL = "komersia.settings_local"


def probe(code: str, tmp_path, **env):
    return run_python(code, DJANGO_SETTINGS_MODULE=LOCAL, KOMERSIA_DATA_DIR=str(tmp_path), **env)


def make_secrets(tmp_path):
    p = Paths(tmp_path / "i", tmp_path, tmp_path / "pg")
    p.ensure_dirs()
    secrets, _ = config.load_or_create_secrets(p)
    return p, secrets


def test_settings_local_values(tmp_path):
    p, secrets = make_secrets(tmp_path)
    (p.config_dir / "runtime.json").write_text(json.dumps({"pg_port": 54555}), encoding="utf-8")
    r = probe(
        "import json; from django.conf import settings as s;"
        "print(json.dumps(dict(edition=s.KOMERSIA_EDITION, single=s.KOMERSIA_SINGLE_TENANT, debug=s.DEBUG,"
        " hosts=s.ALLOWED_HOSTS, urlconf=s.ROOT_URLCONF, billing=s.BILLING_PROVIDER, key=s.SECRET_KEY,"
        " apps=s.INSTALLED_APPS, dbs=s.DATABASES, static=str(s.STATIC_ROOT), data=str(s.KOMERSIA_DATA_DIR))))",
        tmp_path)
    assert r.returncode == 0, r.stderr
    d = json.loads(r.stdout)
    assert (d["edition"], d["single"], d["debug"], d["hosts"]) == ("local", True, False, ["*"])
    assert d["urlconf"] == "komersia.urls_local" and d["billing"] == ""
    assert d["key"] == secrets.secret_key
    assert "backoffice" not in d["apps"] and "billing" in d["apps"]
    assert list(d["dbs"]) == ["default"]  # alias « ops » supprimé
    db = d["dbs"]["default"]
    assert db["USER"] == "komersia_app" and db["PASSWORD"] == secrets.app_password
    assert db["HOST"] == "127.0.0.1" and db["PORT"] == "54555" and db["ATOMIC_REQUESTS"] is True
    assert d["static"] == str(tmp_path / "static")


def test_owner_role_for_migrations_only(tmp_path):
    _, secrets = make_secrets(tmp_path)
    r = probe("from django.conf import settings as s; d=s.DATABASES['default'];"
              "print(d['USER'], d['PASSWORD'])", tmp_path, KOMERSIA_DB_ROLE="owner")
    assert r.stdout.split() == ["komersia_owner", secrets.owner_password]
    bad = probe("import django.conf; django.conf.settings.DEBUG", tmp_path, KOMERSIA_DB_ROLE="postgres")
    assert bad.returncode != 0 and "KOMERSIA_DB_ROLE" in bad.stderr


def test_no_default_secret_when_file_missing(tmp_path):
    r = probe("from django.conf import settings; settings.SECRET_KEY", tmp_path)
    assert r.returncode != 0 and "secrets" in r.stderr and "--init" in r.stderr


def test_env_overrides_db_port(tmp_path):
    make_secrets(tmp_path)
    r = probe("from django.conf import settings as s; print(s.DATABASES['default']['PORT'])", tmp_path,
              KOMERSIA_DB_PORT="60001")
    assert r.stdout.strip() == "60001"


URL_DUMP = """
import json
import django; django.setup()
from django.urls import get_resolver
out = []
for p in get_resolver().url_patterns:
    mod = getattr(p, "urlconf_name", None)
    out.append([str(p.pattern), getattr(mod, "__name__", None) if mod is not None and not isinstance(mod, list) else None])
print(json.dumps(out))
"""


def test_urls_local_matches_urls_except_ops_and_support(tmp_path):
    full = run_python(URL_DUMP, DJANGO_SETTINGS_MODULE="komersia.settings", DJANGO_DEBUG="0")
    assert full.returncode == 0, full.stderr
    # urls_local est chargé avec les réglages complets : seule sa liste de routes nous intéresse
    local = run_python(URL_DUMP.replace("get_resolver()", "get_resolver('komersia.urls_local')"),
                       DJANGO_SETTINGS_MODULE="komersia.settings", DJANGO_DEBUG="0")
    assert local.returncode == 0, local.stderr
    full_routes, local_routes = json.loads(full.stdout), json.loads(local.stdout)
    removed = {"ops/", "api/v1/support/"}
    expected = [r for r in full_routes if r[0] not in removed]
    assert {r[0] for r in full_routes} >= removed, "urls.py ne contient plus ops/ et support/ ?"
    assert local_routes == expected, "Mettre à jour backend/komersia/urls_local.py comme urls.py"
