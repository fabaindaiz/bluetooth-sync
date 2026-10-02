"""`aurasync radio-log on|off|status` (spec 2026-10-02 §3.2): through the running service when
there is one, by itself (written down, reverted at the service's next start) when not."""

import json
import threading
from dataclasses import asdict

import pytest

from aurasync import cli, radio
from aurasync.config import Instalacion, Parlante
from aurasync.rest import make_server
from aurasync.service import Service, ServiceConfig
from aurasync.simulated import simulated_log_level
from tests.test_service import FakeSession

TOKEN = "c" * 43


@pytest.fixture
def xdg(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    (tmp_path / "aurasync").mkdir()
    return tmp_path / "aurasync"


def test_without_the_service_it_acts_alone_and_writes_the_change(xdg, monkeypatch, capsys):
    made = []

    def level(path):
        made.append(simulated_log_level(path))
        return made[-1]

    monkeypatch.setattr(radio, "LogLevel", level)
    assert cli.main(["radio-log", "on"]) == 0
    assert "sin el servicio" in capsys.readouterr().out
    text = (xdg / "cambios-de-sistema.txt").read_text()
    assert "cambio temporal: wpctl set-log-level" in text
    assert "(revertir: wpctl set-log-level -)" in text
    assert cli.main(["radio-log", "off"]) == 0
    assert "devuelto" in capsys.readouterr().out
    assert "revertido (al arrancar)" in (xdg / "cambios-de-sistema.txt").read_text()


def test_with_the_service_it_asks_the_service(xdg, tmp_path, capsys):
    Instalacion(parlantes=[Parlante("Red", "s0")]).guardar(tmp_path / "inst.json")
    level = simulated_log_level(xdg / "cambios-de-sistema.txt")
    svc = Service(
        tmp_path / "inst.json",
        tmp_path / "presets.json",
        session_factory=FakeSession,
        log=lambda _: None,
        log_level=level,
    )
    engine = threading.Thread(target=svc.run, daemon=True)
    engine.start()
    httpd = make_server(svc, "127.0.0.1", 0, TOKEN)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    config = ServiceConfig(bind="127.0.0.1", port=httpd.server_address[1], token=TOKEN)
    path = xdg / "service.json"
    path.write_text(json.dumps(asdict(config)))
    path.chmod(0o600)
    try:
        assert cli.main(["radio-log", "on", "--modo", "heavy"]) == 0
        out = capsys.readouterr().out
        assert "servicio: registro de radio encendido (heavy)" in out
        assert level.mode == "heavy"
        assert cli.main(["radio-log", "status"]) == 0
        assert "encendido" in capsys.readouterr().out
        assert cli.main(["radio-log", "off"]) == 0
        assert "apagado" in capsys.readouterr().out
        assert level.mode is None
    finally:
        svc.handle({"v": 1, "op": "shutdown"})
        engine.join(timeout=5)
        httpd.shutdown()
        svc.close()
