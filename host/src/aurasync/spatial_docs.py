"""The spatial mode's knobs explained, one by one, with a SIMULATED figure (d-7c8794-0a4586).

Each figure shows two numbers measured on test signals with the real renderer (`dsp/spatial.py`)
for the listener's speakers: **separation** (how many dB the speaker a hard-panned source comes out
of is over the farthest principal: localisation) and **ambience** (the share of the energy that
goes as ambience: envelopment), for the recommended value, the listener's, and the extremes. The
classic mode is reproduced with the real ambience extractor on the same signals. The room is drawn
from above: the listener in the middle, each principal at its angle, the ambient speakers around.

Slow (a few hundred ms of rendering per figure): never on the engine thread.
"""

from __future__ import annotations

import dataclasses
from typing import Any

import numpy as np

from aurasync import chain, control
from aurasync.dsp import ambience as amb_module
from aurasync.dsp.spatial import SpatialParams, SpatialUpmix, from_character
from aurasync.knob_docs import Doc, Figure

SR = 48000
SECONDS = 1.5
BLOCK = 4096
_STAGE = chain.STAGES["spatial"]


def _default(name: str) -> Any:
    return _STAGE.find_param(name, "spatial").default


_APPLY = "Se oye al instante, salvo cambiar de modo, que pasa por el corte suave."

DOCS: dict[str, Doc] = {
    "render": Doc(
        "render",
        "Modo",
        "Clásico (como hasta ahora), espacial (cada instrumento en su ángulo, el ambiente aparte), frente "
        "intacto (el estéreo adelante, el ambiente alrededor) o directo (estéreo puro alineado, sin efectos).",
        _STAGE.help + " " + _APPLY,
        "classic",
        "Es el sonido de siempre: el espacial se vuelve el de fábrica solo si gana el A/B ciego 8 de 10.",
        sounds_choices={
            "classic": "Un instrumento suena por todos los parlantes, un poco corrido según el pan: "
            "envuelve, pero ubica poco, y a veces gana el estéreo solo.",
            "spatial": "Cada instrumento sale de los parlantes de su lado; el ambiente llena la pieza "
            "desde los ambientales. Se nota mucho más el espacio.",
            "front": "Adelante, el estéreo tal cual: nunca suena peor que el estéreo solo. Atrás y a los "
            "costados, solo el ambiente de la grabación, más fuerte y un poco tarde: la sala te rodea sin "
            "mover a los instrumentos.",
            "direct": "Estéreo puro alineado: cada parlante toca su lado de L/R, sin ningún efecto, al mismo "
            "volumen que el clásico. Es el «antes» para comparar qué agrega el proceso.",
        },
    ),
    "character": Doc(
        "character",
        "Carácter",
        "De ubicación (0) a envolvimiento (1).",
        "Mueve juntas el arco, el ambiente, su nivel y el retardo de Haas. " + _APPLY,
        _default("character"),
        "A medio camino: los instrumentos tienen lugar y el ambiente se siente sin tapar el directo.",
        sounds_low="Cada instrumento muy en su lugar, el estéreo abierto hasta atrás; casi sin ambiente.",
        sounds_high="El sonido te rodea y llena la pieza; los instrumentos se ubican menos.",
    ),
    "manual": Doc(
        "manual",
        "Manual",
        "Si mandan las cuatro perillas avanzadas en vez del carácter.",
        _APPLY,
        _default("manual"),
        "El carácter ya da combinaciones probadas en simulación; manual es para afinar.",
        sounds_choices={
            "False": "Las fija el carácter.",
            "True": "Mandan arco, ambiente, nivel y Haas como los pongas.",
        },
    ),
    "arc_deg": Doc(
        "arc_deg",
        "Arco",
        "Cuánto se abre el estéreo en el anillo de principales.",
        "Un instrumento a la izquierda del todo va a -arco. " + _APPLY,
        _default("arc_deg"),
        "Abre el estéreo hacia los costados sin mandar casi nada atrás.",
        sounds_low="Todo adelante, como un estéreo ancho: los de atrás reciben poco directo.",
        sounds_high="Lo paneado a los lados se va atrás: te rodean los instrumentos, el centro queda adelante.",
        unit="°",
    ),
    "ambience": Doc(
        "ambience",
        "Ambiente",
        "Cuánto de lo que parece ambiente se saca del directo.",
        _APPLY,
        _default("ambience"),
        "Saca la sala sin vaciar los instrumentos.",
        sounds_low="Casi todo es directo: muy ubicado, poco envolvente.",
        sounds_high="Mucho ambiente afuera: envuelve, pero los instrumentos suenan más secos.",
    ),
    "ambient_level_db": Doc(
        "ambient_level_db",
        "Nivel del ambiente",
        "El ambiente frente al directo (la energía total no cambia).",
        _APPLY,
        _default("ambient_level_db"),
        "Un poco más de ambiente que directo en los ambientales: se nota el espacio sin eco.",
        sounds_low="El ambiente queda de fondo.",
        sounds_high="El ambiente adelante: la pieza suena grande, los instrumentos más lejos.",
        unit="dB",
    ),
    "haas_ms": Doc(
        "haas_ms",
        "Retardo de Haas",
        "Cuánto llega tarde el ambiente.",
        "Entre 10 y 25 ms un parlante puede ir hasta 10 dB más fuerte sin robar la ubicación "
        "(research/09 §3). " + _APPLY,
        _default("haas_ms"),
        "Dentro de la zona de Haas: suma espacio sin que el ambiente tire la imagen hacia atrás.",
        sounds_low="El ambiente llega junto al directo: puede correr la imagen hacia los ambientales.",
        sounds_high="Más de 25 ms empieza a separarse: se puede oír como un eco corto.",
        unit="ms",
    ),
}


def params_of(values: chain.ChainValues) -> SpatialParams:
    """What the motor would use for these chain values (`motor._parametros_espaciales`)."""
    if values.param("spatial", "manual"):
        params = SpatialParams(
            arc_deg=values.param("spatial", "arc_deg"),
            ambience=values.param("spatial", "ambience"),
            ambient_level_db=values.param("spatial", "ambient_level_db"),
            haas_ms=values.param("spatial", "haas_ms"),
        )
    else:
        params = from_character(values.param("spatial", "character"))
    return dataclasses.replace(params, front_intact=values.algorithm("spatial") == "front")


def _signals(seed: int = 0) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    rng = np.random.default_rng(seed)
    n = int(SECONDS * SR)
    s = rng.standard_normal(n) * 0.1
    common = rng.standard_normal(n)
    music = (0.1 * (common + 0.6 * rng.standard_normal(n)), 0.1 * (common + 0.6 * rng.standard_normal(n)))
    return {"left": (s, np.zeros(n)), "right": (np.zeros(n), s), "music": music}


def _split(ring: dict[str, tuple[float | None, str]]) -> tuple[dict[str, float], set[str]]:
    angles = {n: a for n, (a, kind) in ring.items() if kind == "principal" and a is not None}
    return angles, {n for n, (_, kind) in ring.items() if kind == "ambient"}


def _render(up: SpatialUpmix, left: np.ndarray, right: np.ndarray) -> dict[str, tuple[float, float]]:
    direct = dict.fromkeys(up.names, 0.0)
    amb = dict.fromkeys(up.names, 0.0)
    skip = up.latency + SR // 4
    done = 0
    for i in range(0, len(left), BLOCK):
        out = up.process(left[i : i + BLOCK], right[i : i + BLOCK])
        for name, (d, a) in out.items():
            start = max(skip - done, 0)
            direct[name] += float(d[start:] @ d[start:])
            amb[name] += float(a[start:] @ a[start:])
        done += min(BLOCK, len(left) - i)
    return {n: (direct[n], amb[n]) for n in up.names}


def _separation(energies: list[dict[str, float]], principals: list[str]) -> float:
    if len(principals) < 2:  # noqa: PLR2004
        return 0.0
    values = []
    for e in energies:
        level = [e[p] for p in principals]
        values.append(10 * np.log10(max(level) / max(min(level), 1e-30)))
    return float(min(np.mean(values), 60.0))


_CACHE: dict[tuple, dict[str, float]] = {}
_CACHE_MAX = 64


class Stopped(Exception):  # noqa: N818 - a signal, not an error
    """`explain` was asked to stop (its service closed)."""


def metrics(params: SpatialParams, ring: dict[str, tuple[float | None, str]], seed: int = 0) -> dict[str, float]:
    """Separation (dB) and ambience share (%) of the spatial renderer for this ring. Cached: the
    explanation asks for the same settings several times (the recommended value in every figure)."""
    key = (params, tuple(sorted(ring.items())), seed)
    if key not in _CACHE:
        if len(_CACHE) >= _CACHE_MAX:
            _CACHE.pop(next(iter(_CACHE)))
        _CACHE[key] = _metrics(params, ring, seed)
    return _CACHE[key]


def _metrics(params: SpatialParams, ring: dict[str, tuple[float | None, str]], seed: int) -> dict[str, float]:
    angles, ambient = _split(ring)
    names = list(ring)
    sig = _signals(seed)
    energies = []
    for key in ("left", "right"):
        out = _render(SpatialUpmix(names, angles, ambient, SR, params), *sig[key])
        energies.append({n: d + a for n, (d, a) in out.items()})
    music = _render(SpatialUpmix(names, angles, ambient, SR, params), *sig["music"])
    total = sum(d + a for d, a in music.values())
    ambient_energy = sum(a for _, a in music.values())
    return {
        "separation_db": round(_separation(energies, sorted(angles)), 2),
        "ambient_pct": round(100 * ambient_energy / max(total, 1e-30), 2),
    }


def classic_metrics(ring: dict[str, tuple[float | None, str]], seed: int = 0) -> dict[str, float]:
    """The same two numbers for today's classic mix (a broadband pan per speaker plus ambience)."""
    sig = _signals(seed)
    places = {n: control.role_from_angle(a) if a is not None else (0.0, 0.55) for n, (a, _) in ring.items()}

    def mix(left, right):
        extractor = amb_module.Extractor()
        lat = extractor.latencia
        out = dict.fromkeys(places, 0.0)
        amb_out = dict.fromkeys(places, 0.0)
        a_sig = np.concatenate(
            [extractor.procesar(left[i : i + BLOCK], right[i : i + BLOCK]) for i in range(0, len(left), BLOCK)]
        )
        ld = np.concatenate([np.zeros(lat), left])[: len(left)]
        rd = np.concatenate([np.zeros(lat), right])[: len(right)]
        skip = lat + SR // 4
        for n, (pan, a) in places.items():
            direct = (1 - pan) / 2 * ld + (1 + pan) / 2 * rd
            x = (1 - a) * direct + a * a_sig
            out[n] = float(x[skip:] @ x[skip:])
            amb_out[n] = float((a * a_sig[skip:]) @ (a * a_sig[skip:]))
        return out, amb_out

    energies = [mix(*sig["left"])[0], mix(*sig["right"])[0]]
    totals, ambs = mix(*sig["music"])
    principals = sorted(n for n, (a, k) in ring.items() if k == "principal" and a is not None)
    return {
        "separation_db": round(_separation(energies, principals), 2),
        "ambient_pct": round(100 * sum(ambs.values()) / max(sum(totals.values()), 1e-30), 2),
    }


def direct_metrics(ring: dict[str, tuple[float | None, str]], seed: int = 0) -> dict[str, float]:
    """The same two numbers for `direct`: each speaker its side of L/R by its pan, at constant
    power, and no ambience at all (motor.py)."""
    sig = _signals(seed)
    places = {n: control.role_from_angle(a) if a is not None else (0.0, 0.55) for n, (a, _) in ring.items()}
    skip = SR // 4

    def mix(left, right):
        out = {}
        for n, (pan, _amb) in places.items():
            theta = (pan + 1) * np.pi / 4
            x = np.cos(theta) * left + np.sin(theta) * right
            out[n] = float(x[skip:] @ x[skip:])
        return out

    energies = [mix(*sig["left"]), mix(*sig["right"])]
    principals = sorted(n for n, (a, k) in ring.items() if k == "principal" and a is not None)
    return {"separation_db": round(_separation(energies, principals), 2), "ambient_pct": 0.0}


_CATEGORIES = [{"x": 0, "label": "ubicación (dB)"}, {"x": 1, "label": "ambiente (%)"}]


def _bars(series: list[tuple[str, str, dict[str, float]]], caption: str) -> Figure:
    rows = [
        {"label": label, "style": style, "points": [[0, m["separation_db"]], [1, m["ambient_pct"]]]}
        for label, style, m in series
    ]
    return Figure("bars", "medida", "valor", rows, caption, "SIMULADO", _CATEGORIES)


def _manual(**changes: Any) -> SpatialParams:
    base = SpatialParams(
        arc_deg=_default("arc_deg"),
        ambience=_default("ambience"),
        ambient_level_db=_default("ambient_level_db"),
        haas_ms=_default("haas_ms"),
    )
    return dataclasses.replace(base, **changes)


EXTREMES = {"character": (0.0, 1.0), "arc_deg": (30.0, 180.0), "ambience": (0.0, 1.0), "ambient_level_db": (-6.0, 10.0)}
CAPTION = (
    "Medido en simulación con tus parlantes: «ubicación» es cuántos dB más suena el parlante de una "
    "fuente paneada a un lado que el principal más lejano; «ambiente», el % de la energía que va como "
    "ambiente. Recomendado contra el tuyo y los extremos."
)


def figure(name: str, values: chain.ChainValues, ring: dict[str, tuple[float | None, str]]) -> Figure:
    current = params_of(values)
    if name == "render":
        render = values.algorithm("spatial")
        return _bars(
            [
                ("clásico", "recommended", classic_metrics(ring)),
                (
                    "espacial",
                    "current" if render == "spatial" else "other",
                    metrics(dataclasses.replace(current, front_intact=False), ring),
                ),
                (
                    "frente intacto",
                    "current" if render == "front" else "other",
                    metrics(dataclasses.replace(current, front_intact=True), ring),
                ),
                ("directo", "current" if render == "direct" else "other", direct_metrics(ring)),
            ],
            CAPTION,
        )
    if name == "haas_ms":
        rows = [
            {
                "label": f"recomendado {_default('haas_ms'):g} ms",
                "style": "recommended",
                "points": [[0, _default("haas_ms")]],
            },
            {"label": f"el tuyo {current.haas_ms:g} ms", "style": "current", "points": [[0, current.haas_ms]]},
        ]
        caption = "Retardo del ambiente. Entre 10 y 25 ms suma espacio sin robar la ubicación (research/09 §3)."
        return Figure("bars", "retardo", "ms", rows, caption, "SIMULADO", [{"x": 0, "label": "retardo (ms)"}])
    if name == "character":
        rec = _default("character")
        series = [(f"carácter {rec:g}", "recommended", metrics(from_character(rec), ring))]
        mine = values.param("spatial", "character")
        series.append((f"el tuyo {mine:g}", "current", metrics(current, ring)))
        series += [(f"carácter {v:g}", "other", metrics(from_character(v), ring)) for v in EXTREMES["character"]]
        return _bars(series, CAPTION)
    if name == "manual":
        return _bars(
            [
                ("con carácter", "recommended", metrics(from_character(values.param("spatial", "character")), ring)),
                (
                    "manual (tus valores)",
                    "current",
                    metrics(
                        SpatialParams(
                            arc_deg=values.param("spatial", "arc_deg"),
                            ambience=values.param("spatial", "ambience"),
                            ambient_level_db=values.param("spatial", "ambient_level_db"),
                            haas_ms=values.param("spatial", "haas_ms"),
                        ),
                        ring,
                    ),
                ),
            ],
            CAPTION,
        )
    rec = _default(name)
    mine = getattr(current, name)
    unit = DOCS[name].unit
    series = [(f"recomendado {rec:g}{unit}", "recommended", metrics(_manual(**{name: rec}), ring))]
    series.append((f"el tuyo {mine:g}{unit}", "current", metrics(_manual(**{name: mine}), ring)))
    series += [(f"{v:g}{unit}", "other", metrics(_manual(**{name: v}), ring)) for v in EXTREMES[name]]
    return _bars(series, CAPTION)


def room(ring: dict[str, tuple[float | None, str]]) -> Figure:
    """The room from above: each principal at its angle (0 in front), ambient speakers around."""
    series = [
        {"label": n, "style": "current" if kind == "principal" else "other", "points": [[a, 1.0]]}
        for n, (a, kind) in ring.items()
    ]
    caption = "La pieza vista desde arriba: tú en el centro, cada principal en su ángulo; los ambientales, alrededor."
    return Figure("room", "ángulo", "", series, caption, "SIMULADO")


def explain(
    values: chain.ChainValues,
    ring: dict[str, tuple[float | None, str]],
    should_stop: Any = lambda: False,
) -> dict[str, Any]:
    """`should_stop()` is checked between figures: a closing service does not leave this running
    for seconds (raises `Stopped`)."""
    docs = {}
    for name, doc in DOCS.items():
        if should_stop():
            raise Stopped
        docs[name] = {**doc.to_dict(), "figure": figure(name, values, ring).to_dict()}
    params = {name: values.param("spatial", name) for name in DOCS if name != "render"}
    return {"docs": docs, "room": room(ring).to_dict(), "render": values.algorithm("spatial"), "params": params}
