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


# `doctor` también lista los micrófonos (`pw-dump`) y resuelve el que se usa (`pactl` y
# `service.json`). Se sustituyen en el borde del subproceso, con la forma que da `pw-dump`,
# para que el test no dependa de que el equipo tenga PipeWire (en el Mac no lo tiene) ni de
# la configuración real del usuario.
FIFINE = "alsa_input.usb-fifine_Microphone-00.analog-stereo"


def _sin_pipewire(monkeypatch, tmp_path):
    from aurasync import sonido

    dump = [
        {
            "type": "PipeWire:Interface:Node",
            "info": {
                "props": {"media.class": "Audio/Source", "node.name": FIFINE, "node.description": "fifine Microphone"}
            },
        }
    ]
    monkeypatch.setattr(sonido, "_pw_dump", lambda: dump)
    monkeypatch.setattr(sonido, "microfono_por_defecto", lambda: FIFINE)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))


def test_doctor_marca_los_codecs_mezclados(tmp_path, monkeypatch, capsys):
    """Es el problema que más caro sale: 45 a 150 ms de desfase entre parlantes."""
    from aurasync import sonido
    from aurasync.sonido import SalidaBluetooth

    mezclados = [
        SalidaBluetooth("bluez_output.A.1", "uno", "sbc"),
        SalidaBluetooth("bluez_output.B.1", "dos", "aac"),
    ]
    _sin_pipewire(monkeypatch, tmp_path)
    monkeypatch.setattr(sonido, "salidas_bluetooth", lambda: mezclados)
    assert main(["--config", str(tmp_path / "i.json"), "doctor"]) == 1
    assert "códec" in capsys.readouterr().out.lower()


def test_doctor_marca_mas_de_tres_parlantes(tmp_path, monkeypatch, capsys):
    """Medido: con 4 streams A2DP el enlace se desestabiliza."""
    from aurasync import sonido

    _sin_pipewire(monkeypatch, tmp_path)
    monkeypatch.setattr(sonido, "salidas_bluetooth", lambda: _salidas(4))
    assert main(["--config", str(tmp_path / "i.json"), "doctor"]) == 1
    assert "3 streams" in capsys.readouterr().out


def test_doctor_aprueba_una_instalacion_sana(tmp_path, monkeypatch, capsys):
    from aurasync import sonido

    _sin_pipewire(monkeypatch, tmp_path)
    monkeypatch.setattr(sonido, "salidas_bluetooth", lambda: _salidas(3))
    destino = tmp_path / "instalacion.json"
    main(["--config", str(destino), "init"])
    capsys.readouterr()
    assert main(["--config", str(destino), "doctor"]) == 0
    out = capsys.readouterr().out
    assert "todo en su lugar" in out
    assert f"{FIFINE}  ← el que se usa" in out


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


# -- parlantes virtuales (sink null) ----------------------------------------------------


def _con_virtual(tmp_path):
    from aurasync.config import Instalacion, Parlante

    destino = tmp_path / "i.json"
    Instalacion(parlantes=[Parlante("A", "bluez_output.A.1"), Parlante("V", None, pan=0.7)]).guardar(destino)
    return destino


def test_doctor_etiqueta_el_virtual(tmp_path, monkeypatch, capsys):
    from aurasync import sonido

    _sin_pipewire(monkeypatch, tmp_path)
    monkeypatch.setattr(sonido, "salidas_bluetooth", list)
    main(["--config", str(_con_virtual(tmp_path)), "doctor"])
    filas = {
        fila.split()[0]: fila for fila in capsys.readouterr().out.splitlines() if fila.startswith(("  A ", "  V "))
    }
    assert filas["A"].rstrip().endswith("NO conectado")
    assert filas["V"].rstrip().endswith("virtual")
    assert "NO conectado" not in filas["V"]


def test_play_no_manda_el_virtual_al_reproductor(tmp_path, monkeypatch):
    import numpy as np

    from aurasync import sonido

    abiertos = []

    class Rep:
        def __init__(self, nodos):
            abiertos.append(list(nodos))
            self.escritos = []

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def escribir(self, bloques):
            assert None not in bloques
            self.escritos.append(set(bloques))

    monkeypatch.setattr(sonido, "leer_wav_estereo", lambda _p: (np.zeros(8192), np.zeros(8192), 48000))
    monkeypatch.setattr(sonido, "Reproductor", Rep)
    destino = _con_virtual(tmp_path)
    assert main(["--config", str(destino), "play", "x.wav"]) == 0
    assert abiertos == [["bluez_output.A.1"]]


def test_run_no_pide_conectar_un_parlante_virtual(tmp_path, monkeypatch, capsys):
    from aurasync import session as session_module
    from aurasync import sonido

    monkeypatch.setattr(sonido, "salidas_bluetooth", lambda: _salidas(1))
    from aurasync.config import Instalacion, Parlante

    destino = tmp_path / "i.json"
    Instalacion(parlantes=[Parlante("V1", None), Parlante("V2", None, pan=0.7)]).guardar(destino)
    pasos = []

    class Sesion:
        loop = None

        def __init__(self, inst, *_a):
            self.inst = inst

        def open(self):
            pass

        def step(self):
            pasos.append(1)
            raise KeyboardInterrupt

        def close(self):
            pass

    monkeypatch.setattr(session_module, "AudioSession", Sesion)
    assert main(["--config", str(destino), "run"]) == 0
    assert pasos == [1]
    assert "no están conectados" not in capsys.readouterr().err
