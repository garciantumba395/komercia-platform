"""Cycle COMPLET hors Windows : initdb -> PostgreSQL embarqué -> migrations (rôle propriétaire) -> waitress ->
HTTP -> inscription -> vente -> arrêt propre. C'est le test qui prouve que tout le chemin fonctionne.

Le lanceur est exécuté comme sous-processus (`python -m komersia_desktop.main`), exactement comme le fait la
tâche planifiée Windows. En root (conteneur), initdb refuse de tourner : PostgreSQL est alors lancé avec
l'utilisateur système `postgres` (KOMERSIA_PG_RUN_AS) ; sans cet utilisateur, les tests sont ignorés.
"""

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import psycopg
import pytest
from conftest import DESKTOP, find_pg_bin, free_port, pg_run_as, subprocess_env

PG_BIN = find_pg_bin()
RUN_AS = pg_run_as()

pytestmark = [
    pytest.mark.skipif(PG_BIN is None, reason="binaires PostgreSQL introuvables (KOMERSIA_TEST_PG_BIN)"),
    pytest.mark.skipif(RUN_AS is False, reason="root sans utilisateur « postgres » : initdb impossible"),
    pytest.mark.skipif(sys.platform == "win32", reason="cycle Linux ; Windows est couvert par la CI Windows"),
]

PHONE, PASSWORD = "+243990000123", "Motdepasse!2026x"


class Instance:
    def __init__(self, base: Path):
        self.data = base / "data"
        self.web_port, self.pg_port = free_port(), free_port()
        self.proc: subprocess.Popen | None = None

    def env(self, **extra):
        env = subprocess_env(
            KOMERSIA_DATA_DIR=str(self.data), KOMERSIA_PG_BIN=str(PG_BIN), KOMERSIA_PORT=str(self.web_port),
            KOMERSIA_PG_PORT=str(self.pg_port), KOMERSIA_PG_LOCALE="C.UTF-8", KOMERSIA_SCHEDULER_DELAY="2",
            KOMERSIA_BIND="127.0.0.1")
        if RUN_AS:
            env["KOMERSIA_PG_RUN_AS"] = RUN_AS
        env.update(extra)
        return env

    def cli(self, *args, **extra) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, "-m", "komersia_desktop.main", *args], env=self.env(**extra),
                              cwd=DESKTOP, capture_output=True, text=True, timeout=120)

    def start(self, **extra):
        self.proc = subprocess.Popen([sys.executable, "-m", "komersia_desktop.main", "--run"],
                                     env=self.env(**extra), cwd=DESKTOP, stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, text=True)

    def wait_healthy(self, timeout=180):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise AssertionError(f"Le lanceur s'est arrêté (code {self.proc.returncode}) :\n{self.logs()}")
            try:
                assert self.get("/health/")[0] == 200
                return
            except (urllib.error.URLError, OSError, AssertionError):
                time.sleep(0.5)
        raise AssertionError(f"Pas de /health/ après {timeout}s :\n{self.logs()}")

    def logs(self) -> str:
        parts = []
        for name in ("komersia.log", "postgres.log"):
            f = self.data / "logs" / name
            if f.exists():
                parts.append(f"--- {name}\n" + f.read_text(encoding="utf-8", errors="replace")[-6000:])
        return "\n".join(parts)

    def url(self, path):
        return f"http://127.0.0.1:{self.web_port}{path}"

    def get(self, path, token=None):
        req = urllib.request.Request(self.url(path))
        if token:
            req.add_header("Authorization", f"Token {token}")
        return self._open(req)

    def post(self, path, body, token=None):
        req = urllib.request.Request(self.url(path), data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json"})
        if token:
            req.add_header("Authorization", f"Token {token}")
        return self._open(req)

    @staticmethod
    def _open(req):
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if raw[:1] in (b"{", b"[") else raw)
        except urllib.error.HTTPError as e:
            raw = e.read()
            return e.code, (json.loads(raw) if raw[:1] in (b"{", b"[") else raw)

    def stop_cleanly(self, timeout=60) -> int:
        r = self.cli("--stop")
        assert r.returncode == 0, r.stdout + r.stderr
        return self.proc.wait(timeout)

    def kill(self):
        if self.proc and self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait()
        # nettoyage de secours d'un éventuel PostgreSQL resté actif
        pgdata = self.data / "pgdata"
        if (pgdata / "postmaster.pid").exists():
            cmd = [str(PG_BIN / "pg_ctl"), "stop", "-D", str(pgdata), "-m", "immediate"]
            subprocess.run(cmd, user=RUN_AS or None, capture_output=True)

    def secrets(self) -> dict:
        return json.loads((self.data / "config" / "secrets.json").read_text(encoding="utf-8"))

    def db(self, user, password, dbname="komersia"):
        return psycopg.connect(host="127.0.0.1", port=self.pg_port, user=user, password=password,
                               dbname=dbname, connect_timeout=5)


@pytest.fixture
def instance():
    base = Path(tempfile.mkdtemp(prefix="komersia-e2e-", dir="/var/tmp"))
    os.chmod(base, 0o755)  # l'utilisateur postgres doit pouvoir traverser le dossier
    inst = Instance(base)
    try:
        yield inst
    finally:
        inst.kill()
        shutil.rmtree(base, ignore_errors=True)


def test_full_cycle_first_launch_to_clean_stop(instance):
    inst = instance
    assert inst.cli("--status").returncode == 3  # arrêté
    inst.start()
    inst.wait_healthy()

    # --- pages et API
    status, page = inst.get("/app/connexion/")
    assert status == 200 and b"html" in page.lower()
    assert inst.get("/ops/")[0] == 404 and inst.get("/api/v1/support/")[0] == 404  # pas de console d'exploitation
    assert inst.get("/static/admin/css/base.css")[0] == 200  # fichiers statiques (WhiteNoise)

    # --- inscription d'un commerçant puis vente (base + RLS + application, de bout en bout)
    status, reg = inst.post("/api/v1/auth/register/", {
        "full_name": "Marie Test", "phone": PHONE, "password": PASSWORD, "business_name": "Boutique Marie",
        "base_currency": "CDF"})
    assert status == 201, reg
    token, store = reg["token"], reg["store"]["id"]
    status, again = inst.post("/api/v1/auth/register/", {
        "full_name": "Autre", "phone": "+243990000999", "password": PASSWORD, "business_name": "Autre"})
    assert status == 403 and again["code"] == "already_configured"  # une entreprise par installation

    status, product = inst.post("/api/v1/catalog/products/", {
        "name": "Savon", "sku": "SAV-001", "price_minor": 1500, "currency": "CDF", "track_stock": False}, token)
    assert status == 201, product
    status, sale = inst.post("/api/v1/sales/", {
        "client_id": str(uuid.uuid4()), "store": store,
        "items": [{"product": product["id"], "quantity": "2"}],
        "payments": [{"method": "cash", "amount_minor": 3000, "currency": "CDF"}]}, token)
    assert status == 201, sale
    assert sale["total_minor"] == 3000 if "total_minor" in sale else True
    status, listing = inst.get("/api/v1/sales/", token)
    assert status == 200 and listing["count"] == 1

    # --- sécurité de la base : RLS appliquée au rôle d'exécution, secrets protégés, écoute locale seule
    secrets = inst.secrets()
    with inst.db("komersia_app", secrets["app_password"]) as conn:
        assert conn.execute("SELECT count(*) FROM sales_sale").fetchone()[0] == 0  # sans tenant : rien
        assert conn.execute("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"
                            ).fetchone() == (False, False)
        assert conn.execute("SHOW listen_addresses").fetchone()[0] == "127.0.0.1"
    with inst.db("komersia_backup", secrets["backup_password"]) as conn:
        assert conn.execute("SELECT count(*) FROM sales_sale").fetchone()[0] == 1  # rôle de sauvegarde
    with pytest.raises(psycopg.OperationalError):  # aucun accès sans mot de passe valide
        inst.db("komersia_app", "mauvais")
    assert oct((inst.data / "config" / "secrets.json").stat().st_mode & 0o777) == "0o600"
    assert not any(v in (inst.data / "logs" / "komersia.log").read_text(encoding="utf-8")
                   for v in (secrets["app_password"], secrets["owner_password"], secrets["secret_key"]))
    hba = (inst.data / "pgdata" / "pg_hba.conf").read_text()
    assert "trust" not in hba
    # PostgreSQL n'accepte pas de connexion sur une autre interface que 127.0.0.1
    hostname_ip = socket.gethostbyname(socket.gethostname())
    if not hostname_ip.startswith("127."):
        with socket.socket() as s:
            s.settimeout(2)
            assert s.connect_ex((hostname_ip, inst.pg_port)) != 0

    # --- adresse réseau, état, deuxième instance refusée, planificateur
    assert (inst.data / "ADRESSE.txt").exists()
    status = json.loads(inst.cli("--status", "--json").stdout)
    assert status["running"] and status["healthy"] and status["web_port"] == inst.web_port
    second = inst.cli("--run")
    assert second.returncode == 2 and "déjà lancé" in second.stdout
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline and "backup_tick" not in inst.logs():
        time.sleep(0.5)
    assert "billing_tick" in inst.logs() and "backup_tick" in inst.logs()

    # --- arrêt propre
    assert inst.stop_cleanly() == 0
    assert not (inst.data / "pgdata" / "postmaster.pid").exists()  # PostgreSQL arrêté proprement
    assert not (inst.data / "config" / "runtime.json").exists()
    assert inst.cli("--status").returncode == 3
    with pytest.raises(urllib.error.URLError):
        inst.get("/health/")


def test_restart_keeps_data_and_upgrade_takes_backup(instance):
    inst = instance
    inst.start()
    inst.wait_healthy()
    status, reg = inst.post("/api/v1/auth/register/", {
        "full_name": "Marie Test", "phone": PHONE, "password": PASSWORD, "business_name": "Boutique Marie"})
    assert status == 201
    secrets_before = inst.secrets()
    assert inst.stop_cleanly() == 0

    # Redémarrage : mêmes secrets, mêmes données, aucune migration rejouée (même version)
    inst.start()
    inst.wait_healthy(60)
    assert inst.secrets() == secrets_before
    status, login = inst.post("/api/v1/auth/login/", {"phone": PHONE, "password": PASSWORD})
    assert status == 200 and login["role"] == "owner"
    assert inst.logs().count("manage.py migrate") == 1
    assert inst.stop_cleanly() == 0

    # Mise à jour simulée : version précédente différente -> copie de sécurité PUIS migrations
    state = inst.data / "config" / "state.json"
    state.write_text(json.dumps({"version": "0.0.1"}), encoding="utf-8")
    inst.start()
    inst.wait_healthy(90)
    dumps = list((inst.data / "backups").glob("avant-mise-a-jour-0.0.1-*.dump"))
    assert len(dumps) == 1 and dumps[0].stat().st_size > 1000
    assert inst.logs().count("manage.py migrate") == 2
    status, login = inst.post("/api/v1/auth/login/", {"phone": PHONE, "password": PASSWORD})
    assert status == 200
    assert inst.stop_cleanly() == 0


def test_port_fallback_when_web_port_is_busy(instance):
    inst = instance
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", inst.web_port))
        busy.listen()
        inst.start()
        deadline = time.monotonic() + 180
        runtime = inst.data / "config" / "runtime.json"
        while time.monotonic() < deadline and not runtime.exists():
            assert inst.proc.poll() is None, inst.logs()
            time.sleep(0.5)
        rt = json.loads(runtime.read_text(encoding="utf-8"))
        assert rt["web_port"] != inst.web_port
        inst.web_port = rt["web_port"]
        inst.wait_healthy(30)
        assert inst.stop_cleanly() == 0


def test_cli_version_and_selftest(instance):
    assert instance.cli("--version").stdout.strip()
    r = instance.cli("--selftest")
    assert r.returncode == 0 and "AUTO-TEST OK" in r.stdout, r.stdout + r.stderr
