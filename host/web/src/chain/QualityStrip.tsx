// The quality strip on top of the Cadena screen (spec 2026-10-02 §7.1, research/11 §4.1):
// loudness in and out, the chain's gain, PSR in and out, the highest true peak and how much the
// limiter works. The warnings say it in words and with a shape, not only with a colour (WCAG 1.4.1).
import type { QualityEvent } from "../contract.gen.ts";
import { nf, qualityReadout } from "../format.ts";

const short = (name: string | null): string => (name ?? "").replace(/^JBL /, "");

function Tile({ label, value, sub, id }: { label: string; value: string; sub?: string; id: string }) {
  return (
    <div class="q-tile" data-q={id}>
      <div class="tile-label">{label}</div>
      <div class="q-value num">{value}</div>
      {sub && <div class="tile-sub">{sub}</div>}
    </div>
  );
}

export function QualityStrip({ quality, playing }: { quality: QualityEvent | null; playing: boolean }) {
  if (!playing || !quality) {
    return (
      <div id="chain-quality" class="quality-strip" role="group" aria-label="Calidad">
        <p class="muted small m-0">Calidad: se mide mientras suena una sesión (sonoridad, ganancia, PSR y pico real).</p>
      </div>
    );
  }
  const r = qualityReadout(quality);
  const lu = (v: number | null): string => (v == null ? "—" : `${v > 0 ? "+" : ""}${nf(v, 1)} LU`);
  return (
    <div id="chain-quality" class="quality-strip" role="group" aria-label="Calidad">
      <div class="q-tiles">
        <Tile id="in" label="Entrada" value={r.inLufs == null ? "—" : `${nf(r.inLufs, 1)} LUFS`} sub="sonoridad, últimos 3 s" />
        <Tile id="out" label="Salida (suma)" value={r.outLufs == null ? "—" : `${nf(r.outLufs, 1)} LUFS`} sub="todos los parlantes" />
        <Tile id="gain" label="Ganancia de la cadena" value={lu(r.gainLu)} sub={`con el volumen: ${lu(r.netGainLu)}`} />
        <Tile
          id="psr"
          label="PSR entrada / salida"
          value={`${nf(r.psrIn, 1)} / ${nf(r.psrOut, 1)} dB`}
          sub={r.psrOutWho ? `salida: la menor, ${short(r.psrOutWho)}` : "pico menos sonoridad"}
        />
        <Tile id="tp" label="Pico real máximo" value={r.tpMax == null ? "—" : `${nf(r.tpMax, 1)} dBTP`} sub="de las salidas, últimos 3 s" />
        <Tile id="limiter" label="Limitador" value={r.limiterPct == null ? "—" : `${nf(r.limiterPct, 0)} %`} sub="del tiempo, el que más trabaja" />
      </div>
      {r.warnings.length > 0 && (
        <ul class="q-warnings" aria-live="polite">
          {r.warnings.map((w) => (
            <li class="q-warn" data-warning={w.kind} key={w.kind}>
              <span class="q-shape" data-shape={w.kind === "flattening" ? "rombo" : "triángulo"} aria-hidden="true" />
              {w.text}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
