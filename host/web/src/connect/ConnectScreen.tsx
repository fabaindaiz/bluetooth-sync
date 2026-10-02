// The PWA's connection screen (d-7c8794-37f9bc): the devices this browser remembers, adding one by
// its address or from a QR's link, the trust in its certificate, pairing, and — for an admin —
// who else may use it. It covers the panel while no device is chosen, and opens from the device
// chip in the top bar, or by itself when the device stops recognising this browser (401).
import { useEffect, useRef, useState } from "preact/hooks";
import { type Device, type Saved, activeDevice, browserName, forget, save, upsert } from "../devices.ts";
import { runtime } from "../runtime.ts";
import {
  type AccessEvent,
  type Hello,
  baseOf,
  compactFingerprint,
  normalizeAddress,
  pairPoll,
  pairRequest,
  probe,
  spacedFingerprint,
} from "../transport.ts";
import { AccessAdmin, SCOPE_LABELS } from "./AccessAdmin.tsx";
import { type Link, ago, parseLink } from "./link.ts";

export const POLL_MS = 1500;

type Add =
  | { phase: "form"; address: string; fp: string; error?: string }
  | { phase: "checking"; address: string; fp: string }
  | { phase: "found"; address: string; fp: string; hello: Hello; error?: string }
  | { phase: "unreachable"; address: string; fp: string }
  | { phase: "waiting"; address: string; fp: string; hello: Hello; id: string; check: string; scope: string; name: string }
  | { phase: "denied" | "expired"; address: string; fp: string; hello: Hello };

interface Props {
  initial: Saved;
  link: Link | null;
  /** Start the panel on `device` (it has a token). */
  start: (device: Device) => void;
}

function short(fp: string): string {
  const spaced = spacedFingerprint(fp);
  return spaced.length > 23 ? `${spaced.slice(0, 11)}…${spaced.slice(-11)}` : spaced;
}

function Fingerprint({ fp, label }: { fp: string; label: string }) {
  return (
    <span class="fingerprint" title={spacedFingerprint(fp)}>
      <span class="muted">{label} </span>
      <code class="num break-all">{spacedFingerprint(fp)}</code>
    </span>
  );
}

function Untrusted({ address, fp, onRetry }: { address: string; fp: string; onRetry: () => void }) {
  const base = baseOf(address);
  return (
    <div class="callout connect-untrusted" role="alert">
      <p class="font-semibold">No se pudo conectar con {base}.</p>
      <p class="mt-1">
        Si el equipo está encendido, con <code>aurasync service</code> corriendo y en esta misma red, lo más
        probable es que este navegador todavía no confíe en su certificado. Dos maneras de arreglarlo:
      </p>
      <ol class="mt-2 flex list-decimal flex-col gap-2 pl-5">
        <li>
          <b>Instalar la raíz del equipo</b> (una vez por teléfono; es lo recomendado):{" "}
          <a class="link-btn" href={`${base}/v1/tls/root.mobileconfig`} target="_blank" rel="noopener">
            perfil para iPhone
          </a>{" "}
          ·{" "}
          <a class="link-btn" href={`${base}/v1/tls/root.crt`} target="_blank" rel="noopener">
            certificado para Android
          </a>
          . En el iPhone, después de instalar el perfil hay que activarlo en Ajustes → General → Información →
          Ajustes de confianza de certificados.{" "}
          {fp ? (
            <>
              La huella que muestre el teléfono tiene que ser <Fingerprint fp={fp} label="" />.
            </>
          ) : (
            <>La huella que muestre el teléfono tiene que ser la que imprime <code>aurasync tls info</code> en el equipo.</>
          )}
        </li>
        <li>
          <b>O, de respaldo, aceptar el aviso:</b> abrí{" "}
          <a class="link-btn" href={`${base}/v1/hello`} target="_blank" rel="noopener">
            {base}
          </a>
          , aceptá el aviso de seguridad y volvé aquí. La excepción caduca y en el iPhone puede no pasar a la app
          instalada.
        </li>
      </ol>
      <button type="button" class="btn mt-2" onClick={onRetry}>
        Reintentar
      </button>
    </div>
  );
}

export function ConnectScreen({ initial, link, start }: Props) {
  const [saved, setSavedState] = useState<Saved>(initial);
  const active = activeDevice(saved);
  const [open, setOpen] = useState<boolean>(!active?.token || link !== null);
  const [revoked, setRevoked] = useState<string | null>(null);
  const [add, setAdd] = useState<Add | null>(link ? { phase: "checking", address: link.address, fp: link.fp } : null);
  const [confirmForget, setConfirmForget] = useState<string | null>(null);
  const [pairName, setPairName] = useState(browserName());
  const [pairScope, setPairScope] = useState("control");
  const [pairCode, setPairCode] = useState("");
  const heading = useRef<HTMLHeadingElement>(null);
  const savedRef = useRef(saved);
  savedRef.current = saved;
  const openRef = useRef(open);
  openRef.current = open;

  const persist = async (next: Saved): Promise<void> => {
    setSavedState(next);
    savedRef.current = next;
    await save(next);
  };

  const use = async (device: Device): Promise<void> => {
    await persist({ ...savedRef.current, active: device.id });
    if (runtime.api) {
      location.reload();
      return;
    }
    setRevoked(null);
    setOpen(false);
    start(device);
  };

  // Events from the rest of the page: the chip, a 401, a pasted link.
  useEffect(() => {
    const onOpen = (): void => setOpen(true);
    const onAccess = (e: Event): void => {
      const event = (e as CustomEvent<AccessEvent>).detail;
      if (event.kind !== "unauthorized") return;
      const current = activeDevice(savedRef.current);
      if (!current) return;
      setRevoked(current.id);
      setOpen(true);
      void persist(upsert(savedRef.current, { ...current, token: null, scope: null, clientId: null }));
    };
    const onHash = (): void => {
      const found = parseLink(location.hash);
      if (!found) return;
      history.replaceState(history.state, "", `${location.pathname}${location.search}`);
      setAdd({ phase: "checking", address: found.address, fp: found.fp });
      setOpen(true);
    };
    document.addEventListener("aurasync:connect-open", onOpen);
    document.addEventListener("aurasync:access", onAccess);
    window.addEventListener("hashchange", onHash);
    return () => {
      document.removeEventListener("aurasync:connect-open", onOpen);
      document.removeEventListener("aurasync:access", onAccess);
      window.removeEventListener("hashchange", onHash);
    };
  }, []);

  useEffect(() => {
    if (open) heading.current?.focus({ preventScroll: true });
  }, [open]);

  // Asking the device who it is.
  useEffect(() => {
    if (add?.phase !== "checking") return;
    let alive = true;
    void probe(baseOf(add.address)).then((answer) => {
      if (!alive) return;
      if (answer.ok) {
        setAdd({ phase: "found", address: add.address, fp: add.fp, hello: answer.hello });
        const known = savedRef.current.devices.find((d) => d.id === answer.hello.id);
        if (known) void persist(upsert(savedRef.current, { ...known, address: add.address, lastSeen: Date.now() }));
      } else if (answer.reason === "rate_limited") {
        setAdd({ phase: "form", address: add.address, fp: add.fp, error: `Demasiados intentos fallidos desde esta dirección: esperá ${answer.retryAfterS ?? 1} s.` });
      } else if (answer.reason === "not_aurasync") {
        setAdd({ phase: "form", address: add.address, fp: add.fp, error: "Esa dirección contesta, pero no es un servicio aurasync." });
      } else {
        setAdd({ phase: "unreachable", address: add.address, fp: add.fp });
      }
    });
    return () => {
      alive = false;
    };
  }, [add?.phase, add?.address]);

  // Waiting for the approval.
  useEffect(() => {
    if (add?.phase !== "waiting") return;
    let alive = true;
    const timer = window.setInterval(async () => {
      const answer = await pairPoll(baseOf(add.address), add.id);
      if (!alive) return;
      if (!answer.ok) {
        if (answer.status === 404) setAdd({ phase: "expired", address: add.address, fp: add.fp, hello: add.hello });
        return; // offline for a moment, or rate limited: keep asking
      }
      const result = answer.result;
      if (result.status === "approved") {
        const device: Device = {
          id: add.hello.id,
          address: add.address,
          name: add.hello.name,
          version: add.hello.version,
          rootSha256: add.hello.tls.root_sha256 ?? "",
          lastSeen: Date.now(),
          token: result.token,
          scope: result.client.scope,
          clientId: result.client.id,
        };
        alive = false;
        clearInterval(timer);
        setAdd(null);
        await persist(upsert(savedRef.current, device));
        await use(device);
      } else if (result.status === "denied") {
        setAdd({ phase: "denied", address: add.address, fp: add.fp, hello: add.hello });
      } else if (result.status === "delivered") {
        setAdd({ phase: "found", address: add.address, fp: add.fp, hello: add.hello, error: "El permiso se entregó a otra pestaña o ventana. Pedí acceso de nuevo." });
      }
    }, POLL_MS);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, [add?.phase, add?.phase === "waiting" ? add.id : ""]);

  const ask = async (found: Extract<Add, { phase: "found" }>): Promise<void> => {
    const body: { name: string; scope: string; code?: string } = { name: pairName.trim() || browserName(), scope: pairScope };
    if (pairCode.trim()) body.code = pairCode.trim();
    const answer = await pairRequest(baseOf(found.address), body);
    if (!answer.ok) {
      const error = answer.status === 429
        ? `Demasiados intentos fallidos desde esta dirección: esperá ${answer.retryAfterS ?? 1} s.`
        : answer.status === 0 ? "Sin conexión con el equipo." : answer.message;
      setAdd({ ...found, error });
      return;
    }
    setAdd({ phase: "waiting", address: found.address, fp: found.fp, hello: found.hello, id: answer.result.id, check: answer.result.check, scope: body.scope, name: body.name });
  };

  const closable = Boolean(active?.token) && runtime.api !== null;
  const revokedDevice = revoked ? saved.devices.find((d) => d.id === revoked) : null;

  return (
    <div
      class="connect-screen"
      hidden={!open}
      role="dialog"
      aria-modal="true"
      aria-labelledby="connect-title"
      onKeyDown={(e) => {
        if (e.key === "Escape" && closable) setOpen(false);
      }}
    >
      <div class="mx-auto flex max-w-2xl flex-col gap-4 px-4 py-4">
        <div class="flex items-center justify-between gap-2">
          <h1 id="connect-title" ref={heading} tabIndex={-1} class="text-lg font-bold tracking-wide">
            aurasync · equipos
          </h1>
          {closable && (
            <button type="button" class="btn btn-ghost" onClick={() => setOpen(false)}>
              Volver al panel
            </button>
          )}
        </div>

        {revokedDevice && (
          <div class="callout" role="alert" data-revoked="1">
            <p>
              <b>{revokedDevice.name}</b> ya no reconoce a este navegador: se revocó su acceso. Para seguir usándolo,
              volvé a emparejar.
            </p>
            <button
              type="button"
              class="btn btn-primary mt-2"
              onClick={() => setAdd({ phase: "checking", address: revokedDevice.address, fp: compactFingerprint(revokedDevice.rootSha256) })}
            >
              Emparejar de nuevo
            </button>
          </div>
        )}

        <section class="card" aria-labelledby="devices-h">
          <div class="card-head">
            <h2 id="devices-h" class="card-title">
              Equipos recordados
            </h2>
          </div>
          {saved.devices.length === 0 ? (
            <p class="muted small">
              Ninguno todavía. Agregá el equipo donde corre <code>aurasync service</code>, en la misma red.
            </p>
          ) : (
            <ul class="flex flex-col gap-2">
              {saved.devices.map((d) => (
                <li key={d.id} class="device-card" data-device={d.id} data-active={d.id === saved.active ? "1" : undefined}>
                  <div class="min-w-0 flex-1">
                    <div class="font-medium">
                      {d.name}{" "}
                      {d.id === saved.active && <span class="chip chip-ok">en uso</span>}{" "}
                      {d.scope && <span class="chip">{SCOPE_LABELS[d.scope] ?? d.scope}</span>}
                      {!d.token && <span class="chip chip-warn">sin emparejar</span>}
                    </div>
                    <div class="muted small">
                      {d.address} · visto {ago(d.lastSeen)} · versión {d.version}
                    </div>
                    <div class="small">
                      <span class="muted">huella de la raíz </span>
                      <code class="num" title={spacedFingerprint(d.rootSha256)}>
                        {short(d.rootSha256)}
                      </code>
                    </div>
                  </div>
                  <div class="row">
                    {d.token ? (
                      <button type="button" class="btn btn-primary" onClick={() => void use(d)} disabled={d.id === saved.active && runtime.api !== null}>
                        Usar
                      </button>
                    ) : (
                      <button type="button" class="btn" onClick={() => setAdd({ phase: "checking", address: d.address, fp: compactFingerprint(d.rootSha256) })}>
                        Emparejar
                      </button>
                    )}
                    {confirmForget === d.id ? (
                      <>
                        <button
                          type="button"
                          class="btn btn-stop"
                          onClick={async () => {
                            setConfirmForget(null);
                            const wasActive = d.id === saved.active;
                            await persist(forget(savedRef.current, d.id));
                            if (wasActive && runtime.api) location.reload();
                          }}
                        >
                          Sí, olvidar
                        </button>
                        <button type="button" class="btn btn-ghost" onClick={() => setConfirmForget(null)}>
                          No
                        </button>
                      </>
                    ) : (
                      <button
                        type="button"
                        class="btn btn-ghost"
                        title="Borra su dirección y su permiso de este navegador. En el equipo sigue en la lista hasta que un administrador lo revoque."
                        onClick={() => setConfirmForget(d.id)}
                      >
                        Olvidar
                      </button>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section class="card" aria-labelledby="add-h">
          <div class="card-head">
            <h2 id="add-h" class="card-title">
              Agregar equipo
            </h2>
          </div>
          <form
            class="row"
            onSubmit={(e) => {
              e.preventDefault();
              const input = (e.currentTarget as HTMLFormElement).elements.namedItem("address") as HTMLInputElement;
              const address = normalizeAddress(input.value);
              if (!address) {
                setAdd({ phase: "form", address: input.value, fp: "", error: "Escribí una dirección como aurasync.local:8443 o IP:puerto." });
                return;
              }
              setAdd({ phase: "checking", address, fp: add?.address === address ? add.fp : "" });
            }}
          >
            <label class="field min-w-48 flex-1">
              <span>Dirección del equipo</span>
              <input
                name="address"
                type="text"
                inputMode="url"
                autoComplete="off"
                autoCapitalize="off"
                spellcheck={false}
                placeholder="aurasync.local:8443"
                class="input"
                defaultValue={add?.address ?? ""}
                key={add?.address ?? ""}
                aria-describedby="add-hint"
              />
            </label>
            <button type="submit" class="btn btn-primary">
              Buscar
            </button>
          </form>
          <p id="add-hint" class="muted small mt-1">
            O IP:puerto; sin puerto se usa el 8443 (HTTPS). También sirve escanear con la cámara el código QR de
            «Conectar teléfono» en el panel del equipo.
          </p>

          <div class="mt-3" role="status" aria-live="polite">
            {add?.phase === "form" && add.error && <p class="danger text-sm">{add.error}</p>}
            {add?.phase === "checking" && <p class="text-sm">Buscando {add.address}…</p>}
            {add?.phase === "unreachable" && (
              <Untrusted address={add.address} fp={add.fp} onRetry={() => setAdd({ ...add, phase: "checking" })} />
            )}
            {add?.phase === "found" && (
              <FoundCard
                add={add}
                saved={saved}
                pairName={pairName}
                setPairName={setPairName}
                pairScope={pairScope}
                setPairScope={setPairScope}
                pairCode={pairCode}
                setPairCode={setPairCode}
                onAsk={() => void ask(add)}
                onUse={(d) => void use(d)}
              />
            )}
            {add?.phase === "waiting" && (
              <div class="callout" data-waiting="1">
                <p>
                  Esperando la aprobación de <b>{add.hello.name}</b>…
                </p>
                <p class="mt-1">
                  Número de comprobación: <strong class="num text-2xl tracking-widest" data-check="1">{add.check}</strong>
                </p>
                <p class="muted small mt-1">
                  Quien lo apruebe (otro dispositivo administrador en «Conectar teléfono», o{" "}
                  <code>aurasync clients approve</code> en el equipo) tiene que ver el mismo número junto a «{add.name}».
                  La solicitud vence en 5 minutos.
                </p>
                <button type="button" class="btn btn-ghost mt-2" onClick={() => setAdd({ phase: "found", address: add.address, fp: add.fp, hello: add.hello })}>
                  Cancelar
                </button>
              </div>
            )}
            {add?.phase === "denied" && <p class="danger text-sm">{add.hello.name} rechazó la solicitud.</p>}
            {add?.phase === "expired" && (
              <p class="danger text-sm">La solicitud venció sin respuesta. Pedí acceso de nuevo.</p>
            )}
          </div>
        </section>

        {active?.token && active.scope === "admin" && runtime.api && (
          <section class="card" aria-labelledby="admin-h">
            <div class="card-head">
              <h2 id="admin-h" class="card-title">
                Quién usa {active.name}
              </h2>
            </div>
            <AccessAdmin visible={() => openRef.current} selfId={active.clientId} />
          </section>
        )}
      </div>
    </div>
  );
}

interface FoundProps {
  add: Extract<Add, { phase: "found" }>;
  saved: Saved;
  pairName: string;
  setPairName: (v: string) => void;
  pairScope: string;
  setPairScope: (v: string) => void;
  pairCode: string;
  setPairCode: (v: string) => void;
  onAsk: () => void;
  onUse: (d: Device) => void;
}

function FoundCard({ add, saved, pairName, setPairName, pairScope, setPairScope, pairCode, setPairCode, onAsk, onUse }: FoundProps) {
  const hello = add.hello;
  const root = hello.tls.root_sha256 ?? "";
  const matches = add.fp ? compactFingerprint(root) === add.fp : null;
  const known = saved.devices.find((d) => d.id === hello.id && d.token);
  return (
    <div class="device-card flex-col items-stretch" data-found={hello.id}>
      <div>
        <div class="font-medium" data-found-name="1">
          {hello.name}
        </div>
        <div class="muted small">
          {add.address} · aurasync {hello.version} · contrato {hello.contract}
        </div>
        <div class="small mt-1">
          <Fingerprint fp={root} label="Huella de la raíz:" />
        </div>
        {matches === true && <p class="small mt-1 text-emerald-700 dark:text-emerald-300" data-fp-match="1">Coincide con la del código QR.</p>}
        {matches === false && (
          <p class="callout" role="alert" data-fp-match="0">
            <b>No coincide con la huella del código QR:</b> en esa dirección contesta otro equipo. No lo emparejes; revisá
            la dirección.
          </p>
        )}
      </div>
      {known ? (
        <div class="row mt-2">
          <span class="text-sm">Este navegador ya está emparejado con {hello.name}.</span>
          <button type="button" class="btn btn-primary" onClick={() => onUse(known)}>
            Usar
          </button>
        </div>
      ) : (
        <form
          class="mt-2 flex flex-col gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            onAsk();
          }}
        >
          {hello.pairing.first_window_s > 0 && (
            <p class="small">
              {hello.name} no tiene ningún dispositivo emparejado: el primero que pida acceso en los próximos{" "}
              {Math.ceil(hello.pairing.first_window_s / 60)} min queda como <b>administrador</b>.
            </p>
          )}
          <div class="row">
            <label class="field min-w-40 flex-1">
              <span>Nombre de este dispositivo</span>
              <input class="input" maxLength={64} value={pairName} onInput={(e) => setPairName((e.target as HTMLInputElement).value)} />
            </label>
            <label class="field">
              <span>Permiso que pide</span>
              <select class="input" value={pairScope} onChange={(e) => setPairScope((e.target as HTMLSelectElement).value)}>
                {Object.entries(SCOPE_LABELS).map(([id, text]) => (
                  <option key={id} value={id}>
                    {text}
                  </option>
                ))}
              </select>
            </label>
            <label class="field w-32">
              <span>Código (opcional)</span>
              <input
                class="input num"
                inputMode="numeric"
                pattern="[0-9]{6}"
                maxLength={6}
                autoComplete="one-time-code"
                value={pairCode}
                onInput={(e) => setPairCode((e.target as HTMLInputElement).value)}
              />
            </label>
          </div>
          {add.error && <p class="danger text-sm">{add.error}</p>}
          <div>
            <button type="submit" class="btn btn-primary" disabled={matches === false}>
              Pedir acceso
            </button>
          </div>
        </form>
      )}
    </div>
  );
}
