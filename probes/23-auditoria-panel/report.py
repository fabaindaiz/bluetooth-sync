"""Summaries of data/results.json as Markdown fragments (stdout), to build medicion.md."""

import json
import sys
from collections import defaultdict

from harness import AUDIT, TAB_IDS

R = json.loads((AUDIT / "data" / ("snap.json" if __import__("os").environ.get("SNAP") else "results.json")).read_text())
KEYS = [k for k in ("pc1440-light", "pc1440-dark", "pc1920-light", "pc1920-dark", "phone390-light", "phone390-dark") if k in R]
part = sys.argv[1] if len(sys.argv) > 1 else "all"


def h(t):
    print(f"\n### {t}\n")


if part in ("axe", "all"):
    h("axe: violaciones por pantalla (nodos)")
    print("| contexto | " + " | ".join(TAB_IDS) + " |")
    print("|---|" + "---|" * len(TAB_IDS))
    for k in KEYS:
        cells = []
        for v in TAB_IDS:
            ax = R[k].get(v, {}).get("axe")
            if not ax:
                cells.append("—")
                continue
            cells.append(" · ".join(f"{x['id']} {x['n']}" for x in ax["violations"]) or "0")
        print(f"| {k} | " + " | ".join(cells) + " |")
    h("axe: detalle por regla")
    rules = defaultdict(lambda: {"screens": set(), "n": 0, "nodes": {}, "impact": "", "tags": [], "help": ""})
    for k in KEYS:
        for v in TAB_IDS:
            for x in R[k].get(v, {}).get("axe", {}).get("violations", []):
                r = rules[x["id"]]
                r["screens"].add(f"{k}/{v}")
                r["n"] += x["n"]
                r["impact"], r["tags"], r["help"] = x["impact"], x["tags"], x["help"]
                for n in x["nodes"]:
                    r["nodes"].setdefault(n["target"], n)
    for rid, r in sorted(rules.items(), key=lambda kv: -kv[1]["n"]):
        print(f"- **{rid}** ({r['impact']}, {', '.join(r['tags'])}): {r['help']}. Nodos sumados: {r['n']} en {len(r['screens'])} pantallas.")
        for t, n in list(r["nodes"].items())[:10]:
            print(f"  - `{t}` — `{n['html'][:110]}` — {n['summary'][:220].replace(chr(10), ' ')}")
    h("axe: incompletos (revisión manual) por regla, PC 1440 claro")
    inc = defaultdict(int)
    for v in TAB_IDS:
        for x in R.get("pc1440-light", {}).get(v, {}).get("axe", {}).get("incomplete", []):
            inc[x["id"]] += x["n"]
    print(dict(inc))
    if "ab_live" in R:
        h("axe: A/B en curso")
        print([(x["id"], x["n"], [n["target"] for n in x["nodes"]][:6]) for x in R["ab_live"]["axe"]["violations"]])

if part in ("graphics", "all"):
    h("Contraste no textual < 3:1")
    for k in KEYS:
        seen = {}
        rows = list(R[k].get("header_graphics", []))
        for v in TAB_IDS:
            for g in R[k].get(v, {}).get("graphics", []):
                rows.append({**g, "v": v})
        for g in rows:
            if g["ratio"] >= 3:
                continue
            key = (g["kind"], g["sel"], g["fg"], g["bg"])
            if key not in seen:
                seen[key] = {**g, "views": set(), "N": 0}
            seen[key]["views"].add(g.get("v", "cabecera"))
            seen[key]["N"] += g["n"]
        print(f"\n**{k}** ({len(seen)} combinaciones bajo 3:1)\n")
        print("| tipo | elemento | dónde | color | fondo | razón | alfa | nota | pantallas | n |")
        print("|---|---|---|---|---|---|---|---|---|---|")
        for g in sorted(seen.values(), key=lambda x: x["ratio"]):
            print(f"| {g['kind']} | `{g['sel']}` | {g['where']} | {g['fg']} | {g['bg']} | {g['ratio']} | {g['alpha']} | {g['note'][:40]} | {', '.join(sorted(g['views']))} | {g['N']} |")
    h("Controles nativos (muestreo de píxeles)")
    print("| contexto | pantalla | control | tamaño | fondo | colores (razón contra el fondo) |")
    print("|---|---|---|---|---|---|")
    for k in KEYS:
        done = set()
        for v in TAB_IDS:
            for n in R[k].get(v, {}).get("native", []):
                sig = (n["control"].split(" (")[0], n["top"][0]["color"] if n["top"] else "")
                if sig in done:
                    continue
                done.add(sig)
                cols = ", ".join(f"{t['color']} {t['share']*100:.0f}% ({t['ratio']})" for t in n["top"][:3])
                print(f"| {k} | {v} | {n['control']} | {n['w']}×{n['h']} | {n['bg']} | {cols} |")

if part in ("targets", "all"):
    h("Tamaño de objetivo")
    print("| contexto | pantalla | interactivos | < 24 px | < 24 sin excepción de espaciado | < 44 px (teléfono) |")
    print("|---|---|---|---|---|---|")
    for k in KEYS:
        for v in TAB_IDS:
            t = R[k].get(v, {})
            if "targets24" not in t:
                continue
            b = t["targets24"]["below"]
            fail = [x for x in b if not x["spacingOk"]]
            t44 = len(t["targets44"]["below"]) if "targets44" in t else "—"
            print(f"| {k} | {v} | {t['targets24']['total']} | {len(b)} | {len(fail)} | {t44} |")
    for k in ("pc1440-light", "phone390-light"):
        if k not in R:
            continue
        print(f"\n**{k}: los que fallan 2.5.8 (bajo 24 px y sin espaciado)**\n")
        seen = set()
        for v in TAB_IDS:
            for x in R[k].get(v, {}).get("targets24", {}).get("below", []):
                if x["spacingOk"]:
                    continue
                sig = (x["sel"], x["text"][:20])
                if sig in seen:
                    continue
                seen.add(sig)
                print(f"- {v}: `{x['sel']}` «{x['text'][:30]}» {x['tw']}×{x['th']} px ({x['where']}); choca con `{x['clash']}`")
        print(f"\n**{k}: bajo 24 px pero con espaciado suficiente**\n")
        seen = set()
        for v in TAB_IDS:
            for x in R[k].get(v, {}).get("targets24", {}).get("below", []):
                if not x["spacingOk"]:
                    continue
                sig = (x["sel"], x["text"][:20])
                if sig in seen:
                    continue
                seen.add(sig)
                print(f"- {v}: `{x['sel']}` «{x['text'][:30]}» {x['tw']}×{x['th']} px ({x['where']})")
    if "phone390-light" in R:
        print("\n**phone390-light: bajo 44 px, agrupado por selector**\n")
        g = defaultdict(lambda: [0, set(), None])
        for v in TAB_IDS:
            for x in R["phone390-light"].get(v, {}).get("targets44", {}).get("below", []):
                s = x["sel"]
                g[s][0] += 1
                g[s][1].add(v)
                g[s][2] = f"{x['tw']}×{x['th']}"
        for s, (n, vs, size) in sorted(g.items(), key=lambda kv: -kv[1][0])[:40]:
            print(f"- `{s}` ×{n} ({', '.join(sorted(vs))}), p. ej. {size}")

if part in ("keyboard", "all"):
    for k in ("pc1440-light", "pc1440-dark"):
        if k not in R:
            continue
        h(f"Teclado {k}")
        print("| pantalla | paradas de Tab | interactivos no alcanzados | sin foco visible (sin :focus-visible o 0 px cambiados) | tapados al recibir foco | contorno < 3:1 | clic sin foco |")
        print("|---|---|---|---|---|---|---|")
        for v in TAB_IDS:
            kb = R[k].get(v, {}).get("keyboard")
            if not kb:
                continue
            o = [x for x in kb["order"] if "id" in x]
            novis = [x for x in o if x.get("inView") and (("diff" in x and x["diff"].get("changed", 1) == 0) or not x["fv"])]
            cov = [x for x in o if x.get("covered")]
            low = [x for x in o if x.get("outlineRatio") is not None and x["outlineRatio"] < 3]
            print(f"| {v} | {kb['tabStops']} | {len(kb['unreached'])} | {len(novis)} | {len(cov)} | {len(low)} | {len(kb['clickableNotFocusable'])} |")
        for v in TAB_IDS:
            kb = R[k].get(v, {}).get("keyboard")
            if not kb:
                continue
            o = [x for x in kb["order"] if "id" in x]
            novis = [x for x in o if x.get("inView") and (("diff" in x and x["diff"].get("changed", 1) == 0) or not x["fv"])]
            cov = [x for x in o if x.get("covered")]
            low = {(x["sel"], x["outline"], x["outlineRatio"]) for x in o if x.get("outlineRatio") is not None and x["outlineRatio"] < 3}
            if kb["unreached"]:
                print(f"- {v} no alcanzados: " + "; ".join(f"`{u['sel']}` «{u['text'][:25]}»" for u in kb["unreached"][:12]))
            if novis:
                print(f"- {v} sin foco visible: " + "; ".join(f"`{x['sel']}` «{x['text'][:20]}» ({x['outline']}, fv={x['fv']}, diff={x.get('diff')})" for x in novis[:12]))
            if cov:
                print(f"- {v} tapados: " + "; ".join(f"`{x['sel']}` por {x['coverer']}" for x in cov[:10]) + f" (total {len(cov)})")
            if low:
                print(f"- {v} contorno bajo: " + "; ".join(f"`{a}` {b} → {c}" for a, b, c in list(low)[:8]))
            if kb["clickableNotFocusable"]:
                print(f"- {v} con cursor de clic y sin foco: " + "; ".join(f"`{x['sel']}` «{x['text'][:25]}»" for x in kb["clickableNotFocusable"][:10]))
            outs = sorted({x["outline"] for x in o})
            print(f"- {v} contornos vistos: {outs[:6]}; razón mínima/mediana: " + (lambda rs: f"{min(rs)} / {sorted(rs)[len(rs)//2]}" if rs else "—")([x["outlineRatio"] for x in o if x.get("outlineRatio")]))
    if "escape" in R.get("pc1440-light", {}):
        h("Escape y divulgaciones")
        for e in R["pc1440-light"]["escape"]:
            print(f"- {e}")
        print(R["pc1440-light"].get("escape_error", ""))
        h("Deslizadores con flechas")
        for s in R["pc1440-light"].get("sliders", []):
            print(f"- {s}")
        print(R["pc1440-light"].get("sliders_error", ""))

if part in ("aria", "all"):
    h("Árbol de accesibilidad (PC 1440 claro)")
    for v in TAB_IDS:
        ax = R.get("pc1440-light", {}).get(v, {}).get("ax")
        if not ax:
            continue
        um = defaultdict(int)
        for u in ax["unnamed"]:
            um[(u["role"], u.get("el"))] += 1
        meters = ax["meters"]
        nov = [m for m in meters if m["value"] is None and not m["valuetext"]]
        print(f"- {v}: nodos {ax['nodes']}; sin nombre: {dict(um) or 0}; medidores {len(meters)}, sin valor {len(nov)}; ej. {meters[:3]}")

if part in ("motion", "all"):
    h("Regiones vivas y ritmo de actualización (PC 1440 claro, 5 s)")
    for v in TAB_IDS:
        w = R.get("pc1440-light", {}).get(v, {}).get("watch")
        if not w:
            continue
        lv = [f"{x['sel']}({x['politeness']}{'' if x['visible'] else ', oculto'}) {x['perMin']}/min, {x['texts']} textos" for x in w["live"] if x["changes"]]
        fast = [f"{u['k']} {u['perSec']}/s [{u['kinds']}]" for u in w["updaters"] if u["perSec"] > 3]
        print(f"- **{v}**: fps {w['fps']}; transiciones {w['transitions']}; animaciones {w['animations']}")
        print(f"  - regiones vivas que cambiaron: {lv or 'ninguna'}")
        print(f"  - > 3 actualizaciones/s: {fast or 'ninguno'}")
    if "reduced_motion" in R:
        h("Con prefers-reduced-motion: reduce")
        for v, d in R["reduced_motion"].items():
            w = d["watch"]
            fast = [f"{u['k']} {u['perSec']}/s" for u in w["updaters"] if u["perSec"] > 3]
            print(f"- **{v}**: fps {w['fps']}; transiciones {w['transitions']}; > 3/s: {fast or 'ninguno'}; CPU {d['perf']['mainThreadBusyPct']} %")

if part in ("perf", "all"):
    h("Rendimiento (PC 1440 claro, 10 s por pestaña)")
    print("| pantalla | hilo principal ocupado | script | layout | estilo | layouts/s | recálculos/s | tareas largas (máx ms) | heap MB | nodos DOM | pedidos en 10 s |")
    print("|---|---|---|---|---|---|---|---|---|---|---|")
    rows = [(v, R.get("pc1440-light", {}).get(v, {}).get("perf")) for v in TAB_IDS]
    rows += [(f"reducido/{v}", d["perf"]) for v, d in R.get("reduced_motion", {}).items()]
    for v, p in rows:
        if not p:
            continue
        print(f"| {v} | {p['mainThreadBusyPct']} % | {p['scriptPct']} % | {p['layoutPct']} % | {p['stylePct']} % | {p['layoutsPerSec']} | {p['styleRecalcsPerSec']} | {p['longTasks']} ({p['longTaskMaxMs']}) | {p['heapMB']} | {p['nodes']} | {p['requests']} |")

if part in ("layout", "all"):
    h("Distribución")
    print("| contexto | pantalla | alto total | pantallas de alto | ancho útil del contenido | cabecera | aviso | barra abajo | scroll horizontal | tarjetas con > 120 px en blanco |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    for k in KEYS:
        if "dark" in k:
            continue
        for v in TAB_IDS:
            L = R[k].get(v, {}).get("layout")
            if not L:
                continue
            blank = [f"{c['card']} {c['blankBelow']}" for c in L["cards"] if c["blankBelow"] > 120]
            print(f"| {k} | {v} | {L['scrollH']} | {L['scrollH']/L['vh']:.1f} | {L['viewWidth']} de {L['vw']} (izq. {L['viewLeft']}) | {L['header']} ({L['headerPos']}) | {L['warnings']} | {L['bottomNav']} | {L['hscroll']} | {', '.join(blank) or '—'} |")
    for k in ("pc1440-light", "pc1920-light", "phone390-light"):
        if "tasks" in R.get(k, {}):
            h(f"Tareas, {k}")
            for t in R[k]["tasks"]:
                print(f"- {t}")
    for k in KEYS:
        if R[k].get("pageErrors"):
            print(f"errores de página {k}: {R[k]['pageErrors'][:5]}")
        for v in TAB_IDS:
            if "error" in R[k].get(v, {}):
                print(f"ERROR {k}/{v}: {R[k][v]['error'][-400:]}")
