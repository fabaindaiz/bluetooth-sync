import subprocess
import sys
from importlib.metadata import version

import pytest

from aurasync import __version__
from aurasync.cli import main


def test_version_matches_installed_metadata():
    assert version("aurasync") == __version__


def test_version_flag_prints_name_and_version(capsys):
    with pytest.raises(SystemExit) as exit_info:
        main(["--version"])
    assert exit_info.value.code == 0
    assert capsys.readouterr().out.strip() == f"aurasync {__version__}"


def test_unknown_subcommand_fails_instead_of_doing_nothing():
    with pytest.raises(SystemExit) as exit_info:
        main(["noexiste"])
    assert exit_info.value.code == 2


def test_play_sin_archivo_falla_en_vez_de_no_hacer_nada():
    with pytest.raises(SystemExit) as exit_info:
        main(["play"])
    assert exit_info.value.code == 2


def test_module_entry_point_runs():
    result = subprocess.run(
        [sys.executable, "-m", "aurasync", "--version"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == f"aurasync {__version__}"


# -- los subcomandos que tocan el sistema ------------------------------------------
# Se prueban con la lista de parlantes sustituida: lo que importa acá es la lógica de
# decisión (qué rol le toca a cada uno, qué cuenta como problema), no PipeWire.


def _salidas(n: int, codec: str = "sbc"):
    from aurasync.sonido import SalidaBluetooth

    return [SalidaBluetooth(f"bluez_output.AA_BB_CC_DD_EE_0{i}.1", f"Parlante {i}", codec) for i in range(n)]


def test_init_reparte_roles_sin_pedir_numeros(tmp_path, monkeypatch):
    from aurasync import sonido
    from aurasync.config import Instalacion

    monkeypatch.setattr(sonido, "salidas_bluetooth", lambda: _salidas(3))
    destino = tmp_path / "instalacion.json"
    assert main(["--config", str(destino), "init"]) == 0

    inst = Instalacion.cargar(destino)
    assert [p.nombre for p in inst.parlantes] == ["Parlante 0", "Parlante 1", "Parlante 2"]
    # Los dos primeros al frente, abiertos; el tercero, mayormente ambiente.
    assert inst.parlantes[0].pan < 0
    assert inst.parlantes[1].pan > 0
    assert inst.parlantes[2].ambiente > inst.parlantes[0].ambiente
    # **Ninguno en ambiente puro.** Escuchado: un parlante que solo reproduce el ambiente
    # extraído suena difuso y no se ubica en la pieza
    # (`docs/research/experimentos/09-primera-escucha-con-3-go-4.md`).
    assert all(0.0 < p.ambiente < 1.0 for p in inst.parlantes)
    # Y nadie tiene todavía corrección: eso lo escribe `calibrate`.
    assert all(p.retardo_ms == 0.0 and p.ganancia_db == 0.0 for p in inst.parlantes)


def test_init_no_pisa_una_instalacion_existente(tmp_path, monkeypatch):
    from aurasync import sonido

    monkeypatch.setattr(sonido, "salidas_bluetooth", lambda: _salidas(2))
    destino = tmp_path / "instalacion.json"
    assert main(["--config", str(destino), "init"]) == 0
    assert main(["--config", str(destino), "init"]) == 1
    assert main(["--config", str(destino), "init", "--forzar"]) == 0


def test_init_avisa_si_no_hay_parlantes(tmp_path, monkeypatch):
    from aurasync import sonido

    monkeypatch.setattr(sonido, "salidas_bluetooth", list)
    assert main(["--config", str(tmp_path / "i.json"), "init"]) == 1


def test_doctor_marca_los_codecs_mezclados(tmp_path, monkeypatch, capsys):
    """Es el problema que más caro sale: 45 a 150 ms de desfase entre parlantes."""
    from aurasync import sonido
    from aurasync.sonido import SalidaBluetooth

    mezclados = [
        SalidaBluetooth("bluez_output.A.1", "uno", "sbc"),
        SalidaBluetooth("bluez_output.B.1", "dos", "aac"),
    ]
    monkeypatch.setattr(sonido, "salidas_bluetooth", lambda: mezclados)
    assert main(["--config", str(tmp_path / "i.json"), "doctor"]) == 1
    assert "códec" in capsys.readouterr().out.lower()


def test_doctor_marca_mas_de_tres_parlantes(tmp_path, monkeypatch, capsys):
    """Medido: con 4 streams A2DP el enlace se desestabiliza."""
    from aurasync import sonido

    monkeypatch.setattr(sonido, "salidas_bluetooth", lambda: _salidas(4))
    assert main(["--config", str(tmp_path / "i.json"), "doctor"]) == 1
    assert "3 streams" in capsys.readouterr().out


def test_doctor_aprueba_una_instalacion_sana(tmp_path, monkeypatch, capsys):
    from aurasync import sonido

    monkeypatch.setattr(sonido, "salidas_bluetooth", lambda: _salidas(3))
    destino = tmp_path / "instalacion.json"
    main(["--config", str(destino), "init"])
    capsys.readouterr()
    assert main(["--config", str(destino), "doctor"]) == 0
    assert "todo en su lugar" in capsys.readouterr().out


def test_run_sin_instalacion_avisa(tmp_path, capsys):
    """El primer comando de la sesión no puede ser `run`: antes hay que crear la instalación."""
    assert main(["--config", str(tmp_path / "no-existe.json"), "run"]) == 1
    assert "aurasync init" in capsys.readouterr().err


def test_run_avisa_si_un_parlante_no_esta_conectado(tmp_path, monkeypatch, capsys):
    from aurasync import sonido

    monkeypatch.setattr(sonido, "salidas_bluetooth", lambda: _salidas(2))
    destino = tmp_path / "instalacion.json"
    main(["--config", str(destino), "init"])
    # Ahora se desconecta uno: `run` no debe arrancar a medias.
    monkeypatch.setattr(sonido, "salidas_bluetooth", lambda: _salidas(1))
    capsys.readouterr()
    assert main(["--config", str(destino), "run"]) == 1
    assert "no están conectados" in capsys.readouterr().err


# -- las opciones del lazo de recalibración -------------------------------------------


def test_run_trae_el_lazo_apagado_por_defecto():
    """Hasta validarlo con parlantes, `run` tiene que comportarse como siempre."""
    from aurasync.cli import build_parser

    args = build_parser().parse_args(["run"])
    assert args.recalibrar is False
    assert args.guardar is False
    assert args.registro is None
    assert args.volumen_db == 0.0


def test_el_valor_por_defecto_de_medir_es_el_que_valido_la_simulacion():
    """Por debajo de 10 s el estimador falla en silencio (`experimentos/08` §3)."""
    from aurasync.cli import build_parser
    from aurasync.sincronia import VentanaDeEmision

    args = build_parser().parse_args(["run"])
    assert args.medir == VentanaDeEmision.SEGUNDOS_DE_MEDICION == 10.0


def test_run_acepta_las_opciones_de_la_prueba_de_audio():
    from aurasync.cli import build_parser

    args = build_parser().parse_args(
        ["run", "--recalibrar", "--cada", "30", "--medir", "12", "--registro", "/tmp/x.jsonl", "--guardar"]
    )
    assert args.recalibrar
    assert args.guardar
    assert (args.cada, args.medir) == (30.0, 12.0)
    assert args.registro == "/tmp/x.jsonl"


def test_el_microfono_se_resuelve_en_orden(tmp_path, monkeypatch):
    """`--microfono`, después `service.json`, después la fuente por defecto (spec §4.3)."""
    import json

    from aurasync import cli, sonido

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr(sonido, "microfono_por_defecto", lambda: "el.del.sistema")
    parser = cli.build_parser()

    assert cli.resolver_microfono(parser.parse_args(["calibrate"])) == "el.del.sistema"
    (tmp_path / "aurasync").mkdir()
    (tmp_path / "aurasync" / "service.json").write_text(json.dumps({"microphone": "el.del.servicio"}))
    assert cli.resolver_microfono(parser.parse_args(["run"])) == "el.del.servicio"
    assert cli.resolver_microfono(parser.parse_args(["calibrate", "--microfono", "el.pedido"])) == "el.pedido"


def test_la_fuente_por_defecto_no_vale_si_es_un_monitor(monkeypatch):
    import subprocess

    from aurasync import sonido

    def falso(salida):
        return lambda *a, **_: subprocess.CompletedProcess(a, 0, stdout=salida, stderr="")

    monkeypatch.setattr(subprocess, "run", falso("alsa_output.pci.analog-stereo.monitor\n"))
    assert sonido.microfono_por_defecto() is None
    monkeypatch.setattr(subprocess, "run", falso("alsa_input.usb-fifine.analog-stereo\n"))
    assert sonido.microfono_por_defecto() == "alsa_input.usb-fifine.analog-stereo"
