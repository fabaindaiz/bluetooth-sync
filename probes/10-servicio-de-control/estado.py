"""Lee el estado del servicio de stdin y lo imprime en pocas líneas."""

import json
import sys

r = json.load(sys.stdin)["result"]
s, g = r["session"], r["global"]
motivo = f" ({s['reason']})" if s["reason"] else ""
print(f"  sesión {s['status']}{motivo} · seq {r['sequence']} · preset {r['preset']}")
print(
    f"  volumen {g['volume_db']} dB · traseros {g['rear_delay_ms']} ms · "
    f"ambiente {g['extract_ambience']} · decorrelación {g['decorrelate']}"
)
for p in r["speakers"]:
    suena = "suena" if p.get("playing") else "—"
    print(
        f"    {p['name']:<16} pan {p['pan']:+.2f} amb {p['ambience']:.2f} gan {p['gain_db']:+.1f} dB "
        f"retardo {p['delay_ms']} → ahora {p['delay_now_ms']}  {suena}"
    )
for w in r["warnings"]:
    print(f"  ! {w}")
