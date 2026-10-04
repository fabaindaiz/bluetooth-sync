"""The sync estimator's settings explained, one by one and together (d-7c8794-0a4586).

Each setting has its recommendation and why, how the sound changes for low and high values
(or per choice), and — for the ones that act today — a figure computed with `sync_sim`, the
same simulator the tests use: the alignment error at the anchor over simulated time, for the
recommended value, the listener's, and two extremes. Settings of steps not built yet say which
step and have no figure: a figure of something that does not run would be invented.

**What the figures do not show** (said in their captions): the simulated drift is linear, so
the cost of a long window when the drift changes pace is not in them; and the estimator only
suggests, so the sound changes when the suggestion is applied.

Copy in Spanish, as the panel.
"""

from __future__ import annotations

import dataclasses
from typing import Any

from aurasync import sync_sim
from aurasync.knob_docs import Doc, Figure
from aurasync.sync_methods import SyncSettings, fit, suggested_delays

_APPLY = "Estas perillas cambian qué se sugiere: el sonido cambia cuando aplicas la sugerencia."

DOCS: dict[str, Doc] = {
    "method": Doc(
        "method",
        "Método de cálculo",
        "Cómo se combinan las mediciones de todos los micrófonos en un retardo por parlante.",
        "Mínimos cuadrados robustos resuelve el modelo completo (deriva, saltos, la base de cada "
        "micrófono) con las mediciones de la ventana. Pistas y Kalman llegan en la etapa 5: "
        "hasta entonces no sugieren nada. " + _APPLY,
        "robust_ls",
        "Une grabaciones parciales a través de los parlantes que comparten y no se deja mover "
        "por una medición equivocada.",
        sounds_choices={
            "robust_ls": "Con todo en orden suena igual que los otros; con un micrófono que mide mal "
            "sigue sugiriendo bien, y no corre la imagen hacia un lado.",
            "tracks": "La lógica del lazo de hoy por micrófono. Etapa 5: todavía no sugiere.",
            "kalman": "Sigue rápido, pero un pico equivocado puede sugerir un corrimiento que se oye "
            "como la imagen que se va a un lado. Etapa 5: todavía no sugiere.",
        },
    ),
    "window_min": Doc(
        "window_min",
        "Ventana",
        "Cuántos minutos de mediciones se usan.",
        "Las mediciones más viejas que esto se dejan de usar, salvo la calibración puntual que "
        "fija el objetivo si no hay nada más nuevo del mismo lugar. " + _APPLY,
        10.0,
        "Diez minutos bastan para ver una deriva con firmeza sin arrastrar mucho pasado.",
        sounds_low="Sigue una deriva al tiro, pero sugiere cambios chicos que no hacían falta: si "
        "los aplicas seguido, la imagen tiembla un poco.",
        sounds_high="Sugerencias firmes, pero tarda en ver que la deriva cambió de ritmo: eso se oye "
        "como un eco leve o un ensanchamiento que crece hasta que aplicas.",
        unit="min",
    ),
    "huber_ms": Doc(
        "huber_ms",
        "Tolerancia a mediciones raras",
        "Desde cuántos ms un residuo se trata como sospechoso.",
        "Pérdida de Huber y una última pasada de Tukey: una medición que se aleja más que esto "
        "pesa menos, y muy lejos no pesa nada. " + _APPLY,
        0.3,
        "Un poco más que el error típico de la sonda (0,01 ms) y que la zona muerta del lazo a medias.",
        sounds_low="Ignora casi cualquier diferencia: tarda en creer un cambio real del parlante, "
        "que mientras tanto se oye corrido.",
        sounds_high="Le cree a mediciones raras: puede sugerir un corrimiento falso que, aplicado, corre la imagen.",
        unit="ms",
    ),
    "jump_repeats": Doc(
        "jump_repeats",
        "Mediciones para creer un salto",
        "Cuántas mediciones seguidas tienen que ver el mismo salto de un parlante.",
        "Un stream que se resincroniza salta de golpe (6,5 ms el 2026-10-02). Se cree cuando lo "
        "repiten estas mediciones, como pide CLAUDE.md. " + _APPLY,
        2,
        "Dos mediciones independientes que coinciden: la regla del repositorio.",
        sounds_low="Un error de una sola medición puede sugerir un salto falso: aplicado, el sonido "
        "de ese parlante salta de lugar.",
        sounds_high="Un salto real queda sin corregir más tiempo: ese parlante se oye corrido, como "
        "un eco, hasta que se confirma.",
    ),
    "point_role_default": Doc(
        "point_role_default",
        "Papel de una calibración puntual",
        "Si una medición puntual fija el objetivo o vota.",
        "Al tomarla se puede elegir otro; esto es lo que viene marcado. " + _APPLY,
        "target",
        "Lo más común es calibrar donde se escucha: ahí tiene que quedar alineado.",
        sounds_choices={
            "target": "Queda mejor alineado justo donde mediste; a un metro o dos se nota un poco menos.",
            "vote": "Un poco peor en ese punto y más parejo en la pieza: el objetivo queda entre los "
            "lugares que votaron.",
        },
    ),
    "point_vote_weight": Doc(
        "point_vote_weight",
        "Peso de un voto",
        "Cuánto pesa una medición puntual que vota frente al ancla.",
        "Con un voto de peso 3 y el ancla de peso 1, el objetivo queda a tres cuartos del camino "
        "hacia el lugar del voto. " + _APPLY,
        3.0,
        "Pesa más que el ancla sin borrarla: la persona que votó está donde se escucha.",
        sounds_low="El voto casi no cambia nada: sigue alineado donde está el ancla.",
        sounds_high="El voto manda: queda alineado donde se votó y se aleja del ancla.",
    ),
    "moved_threshold_ms": Doc(
        "moved_threshold_ms",
        "Micrófono movido",
        "Cuánto tienen que correrse a la vez los parlantes de un micrófono para creer que se movió.",
        "Se activa en la etapa 4, con los celulares en medición continua.",
        1.0,
        "Un metro mueve los parlantes ~3 ms: 1 ms distingue moverlo de un cambio real.",
        sounds_low="Rehace la base al menor movimiento y pierde continuidad.",
        sounds_high="Toma un movimiento por un cambio de los parlantes y sugiere un corrimiento que no hacía falta.",
        unit="ms",
    ),
    "min_speakers": Doc(
        "min_speakers",
        "Parlantes por medición",
        "Cuántos parlantes tiene que oír un micrófono para que su medición cuente.",
        "Una sola llegada no tiene con qué compararse: el mínimo es 2. " + _APPLY,
        2,
        "Con 2 se aprovechan micrófonos que oyen solo los parlantes cercanos.",
        sounds_low="Se usan más mediciones, también las de micrófonos que oyen poco.",
        sounds_high="Cada medición es más sólida, pero hay menos: un micrófono que oye pocos no ayuda.",
    ),
    "accept_processed_audio": Doc(
        "accept_processed_audio",
        "Aceptar audio procesado",
        "Si una medición de un navegador que dejó prendido su procesado de voz se usa igual.",
        "La supresión de ruido se come una sonda que es ruido. Se activa en la etapa 3, con la medición en el celular.",
        False,
        "Más vale perder la medición que creerle a un micrófono que filtró la sonda.",
        sounds_choices={
            "False": "Esas mediciones se descartan.",
            "True": "Se usan, marcadas como dudosas: pueden sugerir corrimientos falsos.",
        },
    ),
    "continuous_every_s": Doc(
        "continuous_every_s",
        "Cada cuánto mide un celular",
        "En modo micrófono continuo, cada cuántos segundos mide.",
        "Se activa en la etapa 4.",
        20.0,
        "Como el lazo de hoy: suficiente para una deriva de 22 ppm.",
        sounds_low="Sigue más rápido; la sonda suena más seguido (bajo la música).",
        sounds_high="Menos mediciones: tarda más en ver un salto.",
        unit="s",
    ),
    "kalman_gate_sigma": Doc(
        "kalman_gate_sigma",
        "Kalman: compuerta",
        "Cuántas desviaciones aguanta una medición antes de descartarla.",
        "Solo para el método Kalman, en la etapa 5.",
        3.0,
        "Tres desviaciones es la compuerta usual.",
        sounds_low="Descarta de más y tarda en seguir un cambio real.",
        sounds_high="Le cree a picos equivocados.",
    ),
    "kalman_drift_ppm": Doc(
        "kalman_drift_ppm",
        "Kalman: deriva esperada",
        "Cuánta deriva espera el filtro.",
        "Solo para el método Kalman, en la etapa 5.",
        50.0,
        "Por sobre los 22 ppm medidos, con margen.",
        sounds_low="Sigue lento una deriva más rápida de lo esperado.",
        sounds_high="Persigue el ruido de cada medición.",
        unit="ppm",
    ),
}

WITH_FIGURE = frozenset(
    {"method", "window_min", "huber_ms", "jump_repeats", "point_role_default", "point_vote_weight", "min_speakers"}
)
EXTREMES: dict[str, tuple[Any, Any]] = {
    "window_min": (2.0, 30.0),
    "huber_ms": (0.05, 2.0),
    "jump_repeats": (1, 4),
    "point_vote_weight": (1.0, 10.0),
    "min_speakers": (3, 4),
}
DURATION_S = 1200.0
EVAL_EVERY_S = 60.0
TOGETHER_EVERY_S = 180.0


def _timeline(
    sc: sync_sim.Scenario, settings: SyncSettings, seed: int, every_s: float = EVAL_EVERY_S
) -> list[list[float | None]]:
    ms = sync_sim.measurements(sc, seed)
    points: list[list[float | None]] = []
    known: tuple = ()
    anchor = sc.anchor_position or "server"
    # As the estimator does: one fit per new measurement, carrying the confirmed jumps from one
    # fit to the next. Fitting only at the plotted points skipped measurements and confirmed a
    # jump later than the estimator would (review 2026-10-03). A point is drawn every `every_s`.
    next_point = every_s
    for t in sorted({m.t for m in ms}):
        if t == 0.0:
            continue
        f = fit([m for m in ms if m.t <= t], settings, t, anchor, known)
        known = tuple(f.jumps)
        if t + 1e-9 < next_point:
            continue
        next_point += every_s
        delays = suggested_delays(f)
        err = sync_sim.alignment_error(sc, delays, t, anchor) if len(delays) > 1 else None
        points.append([round(t / 60, 2), None if err is None else round(err, 4)])
    return points


def _scenario(name: str) -> tuple[sync_sim.Scenario, list[dict]]:
    marks: list[dict] = []
    if name == "window_min":
        sc = sync_sim.standard("drift")
        sc.noise_ms = 0.05
    elif name == "huber_ms":
        sc = sync_sim.standard("drift")
        sc.outlier_rate = 0.05
    elif name == "jump_repeats":
        sc = sync_sim.standard("jump")
        sc.jumps = [(600.0, "s1", 6.52)]
        sc.outlier_rate = 0.03
        marks.append({"x": 10.0, "label": "salto real de 6,5 ms"})
    elif name == "min_speakers":
        sc = sync_sim.standard("partial", 4)
    else:  # method
        sc = sync_sim.standard("biased")
    sc.duration_s = DURATION_S
    return sc, marks


def _series(label: str, style: str, points) -> dict[str, Any]:
    return {"label": label, "style": style, "points": points}


def _value_label(name: str, value: Any) -> str:
    unit = DOCS[name].unit
    return f"{value:g} {unit}".strip() if isinstance(value, int | float) and not isinstance(value, bool) else str(value)


def figure(name: str, current: SyncSettings, seed: int = 0) -> Figure | None:
    if name not in WITH_FIGURE:
        return None
    if name in {"point_role_default", "point_vote_weight"}:
        return _room_figure(name, current, seed)
    sc, marks = _scenario(name)
    recommended = DOCS[name].recommended
    values: list[tuple[str, Any]] = [("recommended", recommended)]
    if getattr(current, name) != recommended:
        values.append(("current", getattr(current, name)))
    else:
        values.append(("current", recommended))
    for extreme in EXTREMES.get(name, ()):
        if extreme not in (v for _, v in values):
            values.append(("other", extreme))
    if name == "method" and getattr(current, name) == recommended:
        values += [("other", m) for m in ("tracks", "kalman")]
    series = []
    for style, value in values:
        settings = dataclasses.replace(SyncSettings(), **{name: value})
        series.append(_series(_value_label(name, value), style, _timeline(sc, settings, seed)))
    caption = (
        f"{DOCS[name].title}: desalineación en el lugar del ancla durante {DURATION_S / 60:.0f} min "
        "simulados, con cada valor. Un hueco es que no hubo sugerencia. La deriva simulada es lineal. "
        "Cambia lo que se sugiere; el sonido cambia al aplicarlo."
    )
    return Figure("timeline", "minutos", "desalineación (ms)", series, caption, "SIMULADO", marks)


def _room_measurements(role: str, weight: float, seed: int):
    sc = sync_sim.standard("drift")
    sc.anchor_every_s = None
    sc.positions["sofa"] = dict(sc.positions["server"])
    sc.positions["sofa"]["s0"] += 1.0
    sc.hears["sofa"] = set(sc.speakers)
    sc.duration_s = 600.0
    if role == "vote":
        sc.votes = {"sofa": weight}
    ms = sync_sim.measurements(sc, seed)
    if role == "target":
        i = next(k for k, m in enumerate(ms) if m.position_id == "sofa" and m.t > 0)
        ms[i] = dataclasses.replace(ms[i], kind="point", role="target")
    return sc, ms


def _room_figure(name: str, current: SyncSettings, seed: int) -> Figure:
    t = 600.0
    if name == "point_role_default":
        configs = [("recommended", "target", current.point_vote_weight)]
        configs.append(
            ("current" if current.point_role_default == "vote" else "other", "vote", current.point_vote_weight)
        )
    else:
        rec = DOCS[name].recommended
        configs = [("recommended", "vote", rec), ("current", "vote", current.point_vote_weight)]
        configs += [("other", "vote", w) for w in EXTREMES[name] if w not in (rec, current.point_vote_weight)]
    series = []
    for style, role, weight in configs:
        sc, ms = _room_measurements(role, weight, seed)
        f = fit([m for m in ms if m.t <= t], dataclasses.replace(SyncSettings(), point_vote_weight=weight), t, "server")
        d = suggested_delays(f)
        points = [
            [0, round(sync_sim.alignment_error(sc, d, t, "server"), 4)],
            [1, round(sync_sim.alignment_error(sc, d, t, "sofa"), 4)],
        ]
        label = "fija el objetivo" if role == "target" else f"vota con peso {weight:g}"
        series.append(_series(label, style, points))
    caption = (
        "Desalineación en el lugar del ancla (0) y en el lugar de la calibración puntual (1), a 1 m "
        "de distancia acústica en un parlante. SIMULADO. Cambia lo que se sugiere; el sonido cambia al aplicarlo."
    )
    places = [{"x": 0, "label": "donde está el ancla"}, {"x": 1, "label": "lugar de la calibración puntual"}]
    return Figure("bars", "lugar", "desalineación (ms)", series, caption, "SIMULADO", places)


def together(current: SyncSettings, seed: int = 0) -> dict[str, Any]:
    text = _together_text(current)
    sc = sync_sim.standard("partial")
    sc.jumps = [(1200.0, "s1", 6.52)]
    sc.outlier_rate = 0.02
    sc.noise_ms = 0.03
    sc.duration_s = 3600.0
    series = [
        _series("recomendada", "recommended", _timeline(sc, SyncSettings(), seed, TOGETHER_EVERY_S)),
        _series("la tuya", "current", _timeline(sc, current, seed, TOGETHER_EVERY_S)),
    ]
    caption = (
        "Una hora simulada con todo junto: deriva de 22 ppm, un salto de 6,5 ms a los 20 min, dos "
        "celulares que oyen dos de tres parlantes cada uno y mediciones raras. Tu configuración contra la recomendada."
    )
    fig = Figure(
        "timeline", "minutos", "desalineación (ms)", series, caption, "SIMULADO", [{"x": 20.0, "label": "salto"}]
    )
    return {"text": text, "figure": fig}


def _together_text(s: SyncSettings) -> str:
    parts = []
    if s.method != "robust_ls":
        parts.append(
            f"El método {DOCS['method'].sounds_choices.get(s.method, s.method).split('.')[0].lower()}: hasta la etapa 5 no sugiere nada."
        )
    else:
        parts.append(
            f"Calcula con mínimos cuadrados robustos sobre los últimos {s.window_min:g} min: sigue una deriva "
            f"y no le cree a una medición que se aleja más de {s.huber_ms:g} ms del resto."
        )
    plural = "" if s.jump_repeats == 1 else "es"
    parts.append(
        f"Cree un salto de un parlante después de {s.jump_repeats} medición{plural} que lo repite{'n' if plural else ''}."
    )
    if s.point_role_default == "target":
        parts.append("Una calibración puntual fija dónde queda alineado: alinea para el lugar donde mediste.")
    else:
        parts.append(
            f"Una calibración puntual vota con peso {s.point_vote_weight:g} frente al ancla: el objetivo queda entre los dos lugares."
        )
    changed = [
        DOCS[f.name].title.lower() for f in dataclasses.fields(s) if getattr(s, f.name) != DOCS[f.name].recommended
    ]
    if changed:
        parts.append("Distinto de lo recomendado: " + ", ".join(changed) + ".")
    parts.append(_APPLY)
    return " ".join(parts)


def explain(current: SyncSettings, seed: int = 0) -> dict[str, Any]:
    """Everything the panel shows about the settings. Slow (simulations): call it off the engine thread."""
    docs = {}
    for name, doc in DOCS.items():
        fig = figure(name, current, seed)
        docs[name] = {**doc.to_dict(), "figure": fig.to_dict() if fig else None}
    tog = together(current, seed)
    return {
        "settings": current.to_dict(),
        "docs": docs,
        "together": {"text": tog["text"], "figure": tog["figure"].to_dict()},
    }
