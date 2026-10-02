"""`aurasync clients …` and `aurasync tls …`: through the running service, or on the files."""

import json
import threading
from dataclasses import asdict

import pytest

from aurasync import cli
from aurasync.access import Access
from aurasync.clients import ClientStore
from aurasync.config import Instalacion, Parlante
from aurasync.lan import LocalNames
from aurasync.rest import make_server
from aurasync.service import Service, ServiceConfig
from aurasync.tls import Certificates
from tests.test_service import FakeSession

TOKEN = "k" * 43


@pytest.fixture
def xdg(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    (tmp_path / "aurasync").mkdir()
    return tmp_path / "aurasync"


def write_config(xdg, port):
    path = xdg / "service.json"
    path.write_text(json.dumps(asdict(ServiceConfig(bind="127.0.0.1", port=port, token=TOKEN))))
    path.chmod(0o600)


@pytest.fixture
def running(xdg, tmp_path):
    Instalacion(parlantes=[Parlante("Red", "s0")]).guardar(tmp_path / "inst.json")
    svc = Service(tmp_path / "inst.json", tmp_path / "presets.json", session_factory=FakeSession, log=lambda _: None)
    engine = threading.Thread(target=svc.run, daemon=True)
    engine.start()
    access = Access(TOKEN, ClientStore(xdg / "clients.json"), window_s=0)
    httpd = make_server(svc, "127.0.0.1", 0, TOKEN, access=access)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    write_config(xdg, httpd.server_address[1])
    yield access
    svc.handle({"v": 1, "op": "shutdown"})
    engine.join(timeout=5)
    httpd.shutdown()
    httpd.server_close()
    svc.close()


def test_list_approve_and_revoke_through_the_service(running, capsys):
    view, _ = running.desk.request("Kitchen tablet", "192.168.1.30")
    assert cli.main(["clients", "list"]) == 0
    out = capsys.readouterr().out
    assert view["id"] in out
    assert "Kitchen tablet" in out
    assert cli.main(["clients", "approve", view["id"], "--alcance", "read"]) == 0
    token = running.desk.poll(view["id"])["token"]
    client_id = token.split("_")[1]
    assert running.store.verify(token).scope == "read"
    assert cli.main(["clients", "revoke", client_id]) == 0
    assert running.store.verify(token) is None


def test_code_prints_a_six_digit_code(running, capsys):
    assert cli.main(["clients", "code"]) == 0
    out = capsys.readouterr().out
    code = out.split("código de emparejamiento: ")[1][:6]
    assert code.isdigit()
    assert running.desk.status()["code"]["code"] == code


def test_without_the_service_revoke_works_on_the_file(xdg, capsys):
    write_config(xdg, 1)  # nothing listens on port 1
    store = ClientStore(xdg / "clients.json")
    client, token = store.add("old phone", "control")
    assert cli.main(["clients", "list"]) == 0
    assert "old phone" in capsys.readouterr().out
    assert cli.main(["clients", "revoke", client.id]) == 0
    assert ClientStore(xdg / "clients.json").verify(token) is None
    assert cli.main(["clients", "approve", "x" * 24]) == 1, "requests live in the service"


def test_tls_info_and_root(xdg, capsys):
    assert cli.main(["tls", "root"]) == 1
    certs = Certificates(xdg / "tls")
    names = LocalNames("0.0.0.0", addresses=lambda: ["192.168.1.50"], hostname=lambda: "pc")
    certs.ensure_server(names.dns_names(), names.ip_addresses(), hostname="pc")
    capsys.readouterr()
    assert cli.main(["tls", "info"]) == 0
    out = capsys.readouterr().out
    assert certs.info().root_sha256 in out
    assert "aurasync.local" in out
    assert cli.main(["tls", "root"]) == 0
    assert str(certs.root_pem) in capsys.readouterr().out
