// Who may use this device (an `admin` only): the pending pairing requests (approve with a scope,
// or deny), a 6-digit code for the next phone, and the paired clients (rename, revoke). Drawn in
// the PWA's connection screen and in the local panel's "Conectar teléfono" dialog. It polls
// `pair_status` and `clients` every 2 s while `visible()` says it is on screen.
import { useEffect, useState } from "preact/hooks";
import { send } from "../bridge.ts";

interface PairRequestView {
  id: string;
  name: string;
  ip: string;
  scope: string;
  check: string;
  status: string;
  age_s: number;
}

interface PairStatus {
  requests: PairRequestView[];
  window: { open: boolean; remaining_s: number };
  code: { active: boolean; code: string | null; expires_in_s: number };
}

interface ClientView {
  id: string;
  name: string;
  scope: string;
  created: string;
  last_used: string;
  last_ip: string;
  expires: string;
}

export const SCOPE_LABELS: Record<string, string> = {
  read: "solo mirar",
  control: "escuchar y ajustar",
  admin: "administrar",
};

const POLL_MS = 2000;

function when(iso: string): string {
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return iso;
  return new Date(t).toLocaleString("es", { dateStyle: "short", timeStyle: "short" });
}

function ScopeSelect({ value, onChange, label }: { value: string; onChange: (v: string) => void; label: string }) {
  return (
    <select class="input" aria-label={label} value={value} onChange={(e) => onChange((e.target as HTMLSelectElement).value)}>
      {Object.entries(SCOPE_LABELS).map(([id, text]) => (
        <option key={id} value={id}>
          {text}
        </option>
      ))}
    </select>
  );
}

function RequestRow({ req, onDone }: { req: PairRequestView; onDone: () => void }) {
  const [scope, setScope] = useState(req.scope);
  return (
    <li class="access-row" data-request={req.id}>
      <div class="min-w-0 flex-1">
        <div class="font-medium">{req.name}</div>
        <div class="muted small">
          desde {req.ip} · pide {SCOPE_LABELS[req.scope] ?? req.scope} · hace {Math.round(req.age_s)} s
        </div>
        <div class="small">
          Número de comprobación: <strong class="num text-base tracking-widest">{req.check}</strong>{" "}
          <span class="muted">(tiene que ser el mismo que muestra el otro dispositivo)</span>
        </div>
      </div>
      <div class="row">
        <ScopeSelect value={scope} onChange={setScope} label={`Permiso para ${req.name}`} />
        <button
          type="button"
          class="btn btn-primary"
          onClick={async () => {
            await send("pair_approve", { request: req.id, scope: scope as "read" | "control" | "admin" });
            onDone();
          }}
        >
          Aprobar
        </button>
        <button
          type="button"
          class="btn btn-ghost"
          onClick={async () => {
            await send("pair_deny", { request: req.id });
            onDone();
          }}
        >
          Rechazar
        </button>
      </div>
    </li>
  );
}

function ClientRow({ client, self, onDone }: { client: ClientView; self: boolean; onDone: () => void }) {
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(client.name);
  const [confirming, setConfirming] = useState(false);
  return (
    <li class="access-row" data-client={client.id}>
      <div class="min-w-0 flex-1">
        {editing ? (
          <form
            class="row"
            onSubmit={async (e) => {
              e.preventDefault();
              const reply = await send("client_rename", { client: client.id, name: name.trim() });
              if (reply?.ok) setEditing(false);
              onDone();
            }}
          >
            <input
              class="input flex-1"
              value={name}
              maxLength={64}
              aria-label={`Nombre nuevo para ${client.name}`}
              onInput={(e) => setName((e.target as HTMLInputElement).value)}
            />
            <button type="submit" class="btn">
              Guardar
            </button>
            <button type="button" class="btn btn-ghost" onClick={() => setEditing(false)}>
              Cancelar
            </button>
          </form>
        ) : (
          <div class="font-medium">
            {client.name} {self && <span class="chip">este navegador</span>}
          </div>
        )}
        <div class="muted small">
          {SCOPE_LABELS[client.scope] ?? client.scope} · último uso {when(client.last_used)}
          {client.last_ip ? ` desde ${client.last_ip}` : ""}
        </div>
      </div>
      {!editing && (
        <div class="row">
          <button type="button" class="btn btn-ghost small-btn" onClick={() => setEditing(true)}>
            Renombrar
          </button>
          {confirming ? (
            <>
              <button
                type="button"
                class="btn btn-stop small-btn"
                onClick={async () => {
                  await send("client_revoke", { client: client.id });
                  setConfirming(false);
                  onDone();
                }}
              >
                Sí, revocar
              </button>
              <button type="button" class="btn btn-ghost small-btn" onClick={() => setConfirming(false)}>
                No
              </button>
            </>
          ) : (
            <button
              type="button"
              class="btn btn-ghost small-btn danger"
              title={self ? "Este navegador dejará de tener acceso" : "Deja de tener acceso al momento, con su stream"}
              onClick={() => setConfirming(true)}
            >
              Revocar
            </button>
          )}
        </div>
      )}
    </li>
  );
}

/** `visible` is read on every poll, so it must read live state (a ref), not a rendered value. */
export function AccessAdmin({ visible, selfId }: { visible: () => boolean; selfId: string | null }) {
  const [status, setStatus] = useState<PairStatus | null>(null);
  const [clients, setClients] = useState<ClientView[] | null>(null);
  const [tick, setTick] = useState(0);
  const refresh = (): void => setTick((t) => t + 1);

  useEffect(() => {
    let alive = true;
    let timer = 0;
    const poll = async (): Promise<void> => {
      if (visible() && !document.hidden) {
        const [p, c] = await Promise.all([send("pair_status", {}), send("clients", {})]);
        if (!alive) return;
        if (p?.ok) setStatus(p.result as PairStatus);
        if (c?.ok) setClients((c.result as { clients: ClientView[] }).clients);
      }
      if (alive) timer = window.setTimeout(() => void poll(), POLL_MS);
    };
    void poll();
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [tick]);

  return (
    <div class="access-admin flex flex-col gap-3" data-access-admin="1">
      <section aria-labelledby="access-requests-h">
        <h3 id="access-requests-h" class="tile-label">
          Solicitudes pendientes
        </h3>
        {status === null ? (
          <p class="muted small">Cargando…</p>
        ) : status.requests.length === 0 ? (
          <p class="muted small">Ninguna. Un dispositivo nuevo la manda desde «Agregar equipo» en la app.</p>
        ) : (
          <ul class="access-list" aria-live="polite">
            {status.requests.map((r) => (
              <RequestRow key={r.id} req={r} onDone={refresh} />
            ))}
          </ul>
        )}
      </section>
      <section aria-labelledby="access-code-h">
        <h3 id="access-code-h" class="tile-label">
          Código de emparejamiento
        </h3>
        {status?.code.active ? (
          <p class="text-sm">
            <strong class="num text-2xl tracking-widest" data-pair-code="1">
              {status.code.code}
            </strong>{" "}
            <span class="muted small">vence en {Math.round(status.code.expires_in_s)} s · sirve una vez</span>
          </p>
        ) : (
          <p class="muted small">Con un código, el otro dispositivo queda aprobado al escribirlo, sin pasar por aquí.</p>
        )}
        <button
          type="button"
          class="btn mt-1"
          onClick={async () => {
            await send("pair_start", { seconds: 120 });
            refresh();
          }}
        >
          Generar código
        </button>
      </section>
      <section aria-labelledby="access-clients-h">
        <h3 id="access-clients-h" class="tile-label">
          Dispositivos con acceso
        </h3>
        {clients === null ? null : clients.length === 0 ? (
          <p class="muted small">Ninguno todavía.</p>
        ) : (
          <ul class="access-list">
            {clients.map((c) => (
              <ClientRow key={`${c.id}-${c.name}`} client={c} self={c.id === selfId} onDone={refresh} />
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
