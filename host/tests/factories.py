"""Snapshots armados a mano para probar reglas que dependen del estado."""

from aurasync.state import (
    MODES,
    BigState,
    CalibrationResult,
    CalibrationState,
    ClockState,
    ControllerState,
    EngineConfig,
    EngineInfo,
    Latency,
    Meter,
    Service,
    Snapshot,
    Source,
    Speaker,
)

ADDRESSES = ("02:00:00:00:00:01", "02:00:00:00:00:02", "02:00:00:00:00:03", "02:00:00:00:00:04")

# nombre: (managed, depende de)
SERVICES = {
    "controller": (True, []),
    "capture": (True, []),
    "dsp": (True, ["capture"]),
    "emitter": (True, ["controller", "dsp"]),
    "bass": (False, ["controller"]),
    "panel": (False, []),
    "coreaudiod": (False, []),
}


def snapshot_with(
    *,
    mode: str = "quad",
    assignments: dict[str, str] | None = None,
    iso_broadcaster: bool | None = True,
    running: bool = False,
    kind: str = "simulated",
    service_states: dict[str, str] | None = None,
    calibration: str = "idle",
) -> Snapshot:
    assignments = assignments or {}
    states = {"bass": "unavailable", "panel": "running", "coreaudiod": "running"}
    states.update(service_states or {})
    speakers = [
        Speaker(
            address=address,
            name=f"Parlante {i + 1}",
            model="JBL Go 4",
            firmware=None,
            channel=assignments.get(address),
            bis_index=None,
            state="seen",
            rssi_dbm=None,
            volume_db=0.0,
            muted=False,
            delay_ms=0.0,
            gain_db=0.0,
        )
        for i, address in enumerate(ADDRESSES)
    ]
    services = [
        Service(
            name=name,
            label=name,
            kind="tarea",
            managed=managed,
            state=states.get(name, "stopped"),
            depends_on=list(deps),
        )
        for name, (managed, deps) in SERVICES.items()
    ]
    results = [CalibrationResult(ch, 1.0, 0.5, 0.9) for ch in MODES[mode]] if calibration == "done" else []
    return Snapshot(
        seq=1,
        engine=EngineInfo(kind=kind, running=running, mode=mode, source=Source("system", None), master_db=-20.0),
        controller=ControllerState(
            present=True,
            port="prueba",
            unit=None,
            firmware=None,
            iso_broadcaster=iso_broadcaster,
            big=BigState(),
        ),
        clock=ClockState(),
        speakers=speakers,
        meters={channel: Meter() for channel in MODES[mode]},
        calibration=CalibrationState(state=calibration, results=results),
        services=services,
        config=EngineConfig(),
        latency=Latency(),
    )
