"""The state the panel sees, built from the service on the engine thread.

One function, one shape: every client (the panel on the PC, the phone, `curl`) gets the
same snapshot. Fields that cannot be observed are `null`, never a made-up value: an RSSI
that Bluetooth did not report, a latency nobody measured.

It reads the session with `getattr` defaults on purpose: the service's tests use a fake
session with only the core interface (spec §8).
"""

from __future__ import annotations

import os
import time
from typing import TYPE_CHECKING, Any

from aurasync import __version__, chain, control
from aurasync.dsp import profiles
from aurasync.session import pipe_size_ms

if TYPE_CHECKING:
    from aurasync.service import Service

SERVICE_LABELS = {
    "session": "Sesión de audio",
    "sink": "Entrada virtual (pw-record)",
    "recalibration": "Lazo de recalibración",
    "source": "Fuente",
    "panel": "Panel y API",
    "bluetoothd": "bluetoothd",
    "pipewire": "PipeWire",
    "wireplumber": "WirePlumber",
}


def build_snapshot(svc: Service) -> dict[str, Any]:
    session = svc.session
    installation = svc.installation
    observer = svc.observer.view
    motor = svc.motor
    rate = svc.options.rate
    current = motor.retardos_actuales_ms() if motor is not None else {}
    lost = list(getattr(session, "lost", []) or [])
    pids = session.pids() if session is not None and hasattr(session, "pids") else {}
    outputs = {s["sink"]: s for s in observer.get("outputs", [])}
    devices = {d["address"]: d for d in observer.get("devices", [])}
    layout = svc.settings.layout

    speakers = []
    if installation is not None:
        for p in installation.parlantes:
            address = p.sink.removeprefix("bluez_output.").split(".")[0].replace("_", ":")
            device = devices.get(address, {})
            output = outputs.get(p.sink)
            speakers.append(
                {
                    "name": p.nombre,
                    "sink": p.sink,
                    "address": address,
                    "pan": p.pan,
                    "ambience": p.ambiente,
                    "gain_db": p.ganancia_db,
                    "delay_ms": round(p.retardo_ms, 3),
                    "delay_now_ms": round(current[p.nombre], 3) if p.nombre in current else None,
                    "role": control.role_of(p.pan, p.ambiente, layout),
                    "muted": p.nombre in svc.settings.muted,
                    "playing": session is not None and p.nombre not in lost,
                    "connected": (output is not None) if observer.get("at") else None,
                    "codec": output.get("codec") if output else None,
                    "rssi_dbm": device.get("rssi_dbm"),
                    "battery_pct": device.get("battery_pct"),
                    "modalias": device.get("modalias"),
                    "pid": (pids.get("players") or {}).get(p.nombre),
                    "eq_db": p.ecualizacion_db,
                    "kind": p.tipo,
                    "kind_guess": profiles.guess(p.nombre),
                }
            )

    calibration = None
    if session is not None and getattr(session, "calibration", None) is not None:
        calibration = session.calibration.describe()
    elif svc.last_calibration is not None:
        calibration = {**svc.last_calibration, "stale": True}

    block = (
        (getattr(svc, "session_options", None) or svc.options).block if session is not None else svc.settings.block_size
    )
    block_ms = block / rate * 1000
    latency = {
        "input_ms": round(block_ms, 1),
        "pipe_ms": round(pipe_size_ms(svc.settings.block_size, 48000, svc.settings.player_latency_ms), 1),
        "extractor_ms": round((motor.latencia if motor is not None else 2048) / rate * 1000, 1),
        "player_ms": float(svc.settings.player_latency_ms),
        "a2dp_ms": None,
    }
    latency["known_ms"] = round(
        latency["input_ms"] + latency["pipe_ms"] + latency["extractor_ms"] + latency["player_ms"], 1
    )
    # Measured by the last calibration, from written to heard (microphone included).
    latency["measured_ms"] = (calibration or {}).get("latency_ms")

    source = session.source.describe() if session is not None and getattr(session, "source", None) else None
    if source is not None:
        source["busy"] = bool(getattr(session, "source_busy", False))
        source["error"] = svc.errors.get("source") or source.get("error")

    ab = None
    if svc.ab is not None:
        ab = {
            "active": True,
            "a": svc.ab.a,
            "b": svc.ab.b,
            "playing": svc.ab.playing,
            "trials": len(svc.ab.trials),
            "correct": sum(t["correct"] for t in svc.ab.trials),
            "last": svc.ab.trials[-1] if svc.ab.trials else None,
            "match_loudness": svc.ab.match,
            # Measured only while A or B plays, never X (it would say which one X is).
            "loudness_lu": svc.ab.loudness(),
            "compensation_db": dict(svc.ab.compensation),
        }
    elif svc.ab_last is not None:
        ab = {"active": False, **svc.ab_last}

    return {
        "service": {
            "version": __version__,
            "uptime_s": round(time.monotonic() - svc._started, 1),  # noqa: SLF001
            "simulated": svc.simulated,
            "pid": os.getpid(),
        },
        "session": {
            "status": svc.status.status,
            "since": svc.status.since,
            "reason": svc.status.reason,
        },
        "sequence": svc.sequence,
        "dirty": svc.dirty,
        "global": {
            "rear_delay_ms": installation.retardo_traseros_ms if installation is not None else None,
            "volume_db": svc.settings.volume_db,
            "extract_ambience": svc.settings.extract_ambience,
            "decorrelate": svc.settings.decorrelate,
            "layout": layout,
            "eq_active": svc.settings.eq_active,
            "recalibrate": svc.settings.recalibrate,
            "probe": svc.settings.probe,
            "probe_margin_db": svc.settings.probe_margin_db,
        },
        "config": _config(svc),
        "speakers": speakers,
        "roles": {name: list(roles) for name, roles in control.ROLES.items()},
        # Where each role sits, for the panel's room plan and its front/rear groups (5 to 8
        # speakers, experimentos/16 §7): its mix and, when the layout gives one, its angle.
        "role_places": _role_places(),
        "speaker_kinds": [{"key": k, "label": v.label} for k, v in profiles.PROFILES.items()],
        "preset": svc.preset,
        "presets": sorted(svc.preset_store.presets),
        # The chain (spec 2026-10-02 §4.3): the algorithm of each stage and the latency they
        # add; the whole description is the `chain` operation.
        "chain_summary": chain.summary(svc.settings.chain),
        "chain_latency_ms": chain.latency_ms(svc.settings.chain),
        "chain_pending": [stage for stage, waiting in chain.pending(svc.settings.chain).items() if waiting],
        "source": source,
        "apps": [a["name"] for a in observer.get("apps", [])],
        "devices": [
            {**d, "in_installation": any(s["address"] == d["address"] for s in speakers)}
            for d in observer.get("devices", [])
        ],
        "scanning": bool(observer.get("scanning")),
        "services": _services(svc, pids, lost),
        "health": _health(svc, block_ms, lost),
        "latency": latency,
        "meters": dict(getattr(session, "meters", None).values)
        if session is not None and hasattr(session, "meters")
        else {},
        "recalibration": {
            "active": session is not None and getattr(session, "loop", None) is not None,
            "last": getattr(session, "last_recalibration", None) if session is not None else None,
            "history": list(getattr(session, "recalibration_history", []) or []) if session is not None else [],
            "drift_ms_h": getattr(session, "drift_ms_h", None) if session is not None else None,
            "drift_ppm": getattr(session, "drift_ppm", None) if session is not None else None,
            "probe": session.probe_state() if session is not None and hasattr(session, "probe_state") else None,
            "microphone": svc.options.microphone,
        },
        "microphones": observer.get("microphones", []),
        "input": {
            "analysis": session.input_analysis.summary()
            if session is not None and hasattr(session, "input_analysis")
            else None,
            # What the applications send to the `aurasync` sink, as PipeWire reports it.
            "apps": [{"name": a["name"], "sample_spec": a.get("sample_spec")} for a in observer.get("apps", [])],
        },
        "calibration": calibration,
        "sync": _sync(session),
        # Spec 2026-10-02 §6.3: the polling fallback of the stream's `quality` and `radio`.
        "quality": svc.quality,
        "radio": svc.radio_view(),
        "radio_log": _radio_log(svc),
        "volume_avrcp": svc.bt_volume.status(),
        "ab": ab,
        "pairing": svc.pairing,
        "logs_last": svc.logs.last_seq,
        "warnings": _warnings(svc, lost),
    }


def _role_places() -> dict[str, dict[str, dict[str, Any]]]:
    """Each layout's roles with their `pan` and `ambience` (`control.ROLES`), plus `angle_deg` (0 in
    front, positive to the right) and `lift` (the outer ring's extra ambience) where the layout is
    defined by angles (`control.LAYOUT_ANGLES`, when it exists)."""
    angles = getattr(control, "LAYOUT_ANGLES", {})
    places: dict[str, dict[str, dict[str, Any]]] = {}
    for layout, roles in control.ROLES.items():
        places[layout] = {}
        for role, (pan, ambience) in roles.items():
            place: dict[str, Any] = {"pan": pan, "ambience": ambience}
            angle = angles.get(layout, {}).get(role)
            if angle is not None:
                place["angle_deg"], place["lift"] = angle
            places[layout][role] = place
    return places


def _sync(session: Any) -> dict[str, Any]:
    """The residual misalignment the recalibration loop measured last (spec 2026-10-02 §7.3.4).

    Each loop round measures it through the corrections in place; it used to reach only the log.
    `age_s` says how old it is: the panel does not show a stale number as the present one."""
    last = getattr(session, "last_residual", None) if session is not None else None
    if not last:
        return {"residual_ms": None, "measured_at": None, "age_s": None, "speakers": []}
    return {
        "residual_ms": last["residual_ms"],
        "measured_at": last["measured_at"],
        "age_s": round(time.monotonic() - last["t"], 1),
        "speakers": list(last["speakers"]),
    }


def _radio_log(svc: Service) -> dict[str, Any]:
    level = svc.log_level
    if level is None:
        return {"available": False, "active": False, "mode": None, "pending": False, "error": None}
    path = level.changes_file
    return {"available": True, **level.status(), "changes_file": str(path) if path is not None else None}


def _config(svc: Service) -> dict[str, Any]:
    s = svc.settings
    running = getattr(svc, "session_options", None) if svc.session is not None else None
    values = {
        "block_size": s.block_size,
        "player_latency_ms": s.player_latency_ms,
        "sink_description": s.sink_description,
        "recalibrate_every_s": s.recalibrate_every_s,
        "recalibrate_measure_s": s.recalibrate_measure_s,
        "output_mode": s.output_mode,
    }
    pending = []
    if running is not None:
        now = {
            "block_size": running.block,
            "player_latency_ms": running.player_latency_ms,
            "sink_description": running.sink_description,
            "recalibrate_every_s": running.every_s,
            "recalibrate_measure_s": running.measure_s,
            "output_mode": running.output,
        }
        pending = [k for k, v in values.items() if now[k] != v]
    return {**values, "pending_restart": pending}


def _services(svc: Service, pids: dict, lost: list[str]) -> list[dict]:
    session = svc.session
    status = svc.status
    playing = session is not None
    uptime = time.monotonic() - status.started_at if status.started_at else None
    out = [
        {
            "name": "session",
            "label": SERVICE_LABELS["session"],
            "kind": "tarea",
            "managed": True,
            "state": {"playing": "running", "starting": "starting", "error": "failed"}.get(status.status, "stopped"),
            "pid": None,
            "uptime_s": uptime,
            "restarts": svc.restarts.get("session", 0),
            "depends_on": ["pipewire", "bluetoothd"],
            "detail": f"{len(svc.installation.parlantes) if svc.installation else 0} parlantes · bloque {svc.settings.block_size}",
            "last_error": svc.errors.get("session"),
        },
        {
            "name": "sink",
            "label": SERVICE_LABELS["sink"],
            "kind": "proceso",
            "managed": False,
            "state": "running" if playing and pids.get("sink") else "stopped",
            "pid": pids.get("sink"),
            "uptime_s": uptime,
            "restarts": 0,
            "depends_on": ["session"],
            "detail": f"{svc.options.sink_name} · {'recibe audio' if getattr(session, 'input_active', False) else 'sin audio entrando'}"
            if playing
            else svc.options.sink_name,
            "last_error": None,
        },
    ]
    for name, pid in (pids.get("players") or {}).items():
        out.append(
            {
                "name": f"player:{name}",
                "label": f"{name} (pw-play)",
                "kind": "proceso",
                "managed": False,
                "state": "failed" if name in lost else ("running" if pid else "stopped"),
                "pid": pid,
                "uptime_s": uptime if pid else None,
                "restarts": 0,
                "depends_on": ["session"],
                "detail": "un stream mono por parlante",
                "last_error": "su stream se cerró: el parlante se perdió" if name in lost else None,
            }
        )
    loop_on = playing and getattr(session, "loop", None) is not None
    last = getattr(session, "last_recalibration", None) if playing else None
    out.append(
        {
            "name": "recalibration",
            "label": SERVICE_LABELS["recalibration"],
            "kind": "tarea",
            "managed": True,
            "state": "running" if loop_on else "stopped",
            "pid": pids.get("microphone"),
            "uptime_s": None,
            "restarts": svc.restarts.get("recalibration", 0),
            "depends_on": ["session"],
            "detail": (f"{last['kind']}: {last['reason']}" if last else "midiendo contra el contenido")
            if loop_on
            else f"micrófono: {svc.options.microphone or 'ninguno'}",
            "last_error": None,
        }
    )
    source = getattr(session, "source", None) if playing else None
    out.append(
        {
            "name": "source",
            "label": SERVICE_LABELS["source"],
            "kind": "proceso",
            "managed": True,
            "state": ("starting" if getattr(session, "source_busy", False) else "running")
            if source is not None
            else "stopped",
            "pid": pids.get("source"),
            "uptime_s": time.monotonic() - source.started_at if source is not None and source.started_at else None,
            "restarts": svc.restarts.get("source", 0),
            "depends_on": ["session"],
            "detail": f"{source.kind}{f' · {source.name}' if source.name else ''}" if source is not None else "—",
            "last_error": svc.errors.get("source"),
        }
    )
    out.append(
        {
            "name": "panel",
            "label": SERVICE_LABELS["panel"],
            "kind": "tarea",
            "managed": False,
            "state": "running",
            "pid": os.getpid(),
            "uptime_s": time.monotonic() - svc._started,  # noqa: SLF001
            "restarts": 0,
            "depends_on": [],
            "detail": "este proceso",
            "last_error": None,
        }
    )
    for name, unit in svc.observer.view.get("units", {}).items():
        out.append(
            {
                "name": name,
                "label": SERVICE_LABELS.get(name, name),
                "kind": "sistema",
                "managed": False,
                "state": "running"
                if unit["state"] == "active"
                else ("failed" if unit["state"] == "failed" else "stopped"),
                "pid": unit["pid"],
                "uptime_s": unit["uptime_s"],
                "restarts": 0,
                "depends_on": [],
                "detail": f"systemd: {unit['state']}",
                "last_error": None,
            }
        )
    return out


def _health(svc: Service, block_ms: float, lost: list[str]) -> dict[str, Any]:
    session = svc.session
    motor_ms = getattr(session, "block_ms", None) if session is not None else None
    return {
        "input_active": bool(getattr(session, "input_active", False)) if session is not None else None,
        "motor_ms": round(motor_ms, 2) if motor_ms is not None else None,
        "budget_ms": round(block_ms, 1),
        "realtime_x": round(block_ms / motor_ms, 0) if motor_ms else None,
        "routing_repairs": getattr(session, "routing_repairs", 0) if session is not None else 0,
        "lost": lost,
        "blocks": getattr(session, "blocks", 0) if session is not None else 0,
        "observed_at": svc.observer.view.get("at"),
        "xruns": svc.observer.view.get("xruns", {}) if session is not None else {},
        "pipe_level_ms": round(session.pipe_ms, 1) if getattr(session, "pipe_ms", None) is not None else None,
        "bt_discovering": bool(svc.observer.view.get("discovering")),
        "cuts": session.cuts.summary() if session is not None and hasattr(session, "cuts") else None,
        "streams_open": svc.streams.get("open", 0),
    }


def _warnings(svc: Service, lost: list[str]) -> list[str]:
    session = svc.session
    warnings = []
    if svc.installation is None:
        warnings.append(f"no installation at {svc.installation_path}")
    if not svc.settings.decorrelate:
        warnings.append("the calibration was measured with decorrelation on")
    if session is not None and getattr(session, "loop", None) is None:
        warnings.append("alignment not measured in this session")
    if lost:
        warnings.append(f"speakers without a stream (the rest keep playing): {', '.join(lost)}")
    repairs = getattr(session, "routing_repairs", 0) if session is not None else 0
    if repairs:
        warnings.append(f"streams moved back to their speaker {repairs} time(s) during this session")
    if svc.dirty:
        warnings.append("the installation has unsaved changes")
    warnings.extend(svc.bt_volume.warnings())
    level = svc.log_level
    if level is not None and level.mode == "heavy":
        warnings.append("the radio log is at debug for everything: journald may drop lines")
    if level is not None and level.mode is not None and level.verified is False:
        warnings.append("the radio log level was asked but reads back different")
    if level is not None and level.error:
        warnings.append(f"radio log: {level.error}")
    return warnings
