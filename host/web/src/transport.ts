// The one way the panel talks to a service (d-7c8794-37f9bc). app.js and the Preact screens both
// go through it; nothing else in the panel calls `fetch("/v1/…")`.
//
// Two transports, one interface:
// - **local**: the panel this service serves. Same origin, the `HttpOnly` cookie, `base` = "".
// - **remote**: the PWA on another origin (GitHub Pages, or Vite on http://localhost:5173) talking
//   to `https://<device>:8443`. Every request carries `Authorization: Bearer <client token>`; the
//   stream, which `EventSource` cannot authenticate with a header (WHATWG HTML §9.2), trades the
//   bearer for a one-use ticket first (`POST /v1/stream/ticket`).
//
// What can go wrong is reported once, as an `AccessEvent`, whoever asked: 401 (the token was
// revoked: pair again), 403 (`forbidden`: the scope does not cover it, or a foreign Origin), 429
// (`rate_limited`, with `Retry-After`), and the device not answering at all (`offline`).
import type { OpArgs, OpName, Reply, ResultOf } from "./contract.gen.ts";

export type Credential = { kind: "cookie" } | { kind: "bearer"; token: string };

export type AccessEvent =
  | { kind: "online" }
  | { kind: "offline"; message: string }
  | { kind: "unauthorized"; message: string }
  | { kind: "forbidden"; message: string }
  | { kind: "rate_limited"; message: string; retryAfterS: number };

export interface ApiOptions {
  /** "" for the same origin, or `https://host:port` (no trailing slash). */
  base: string;
  credential: Credential;
  timeoutMs?: number;
  onEvent?: (event: AccessEvent) => void;
  fetchImpl?: typeof fetch;
  eventSource?: (url: string) => EventSource;
}

export interface Api {
  readonly base: string;
  readonly remote: boolean;
  /** One contract message to `POST /v1/command`. Resolves to the reply (an `ok: false` reply is
   * still a reply); null when the request never reached the service. */
  command<K extends OpName>(op: K, args: OpArgs[K]): Promise<Reply<ResultOf<K>> | null>;
  /** Untyped, for app.js, which sends operations the generated types do not list. */
  raw(message: Record<string, unknown>): Promise<Reply<unknown> | null>;
  /** `GET /v1/state`'s reply, or null. */
  state(): Promise<Reply<unknown> | null>;
  /** The live stream (`GET /v1/stream`), or null when it cannot be opened now. */
  stream(since: number): Promise<EventSource | null>;
  /** An address an `<img>` can load: the path itself with the cookie, a blob URL with a bearer.
   * Null when it could not be read. */
  imageUrl(path: string): Promise<string | null>;
  /** An absolute URL on the service (for links: the root certificate). */
  url(path: string): string;
}

export const TIMEOUT_MS = 10_000;

/** `host:port` (a scheme and a path are dropped; the port defaults to 8443). Null if malformed. */
export function normalizeAddress(input: string, defaultPort = 8443): string | null {
  let text = input.trim();
  if (!text) return null;
  text = text.replace(/^https?:\/\//i, "").replace(/\/.*$/, "");
  const ipv6 = /^\[([0-9a-f:]+)\](?::(\d{1,5}))?$/i.exec(text);
  if (ipv6) return `[${ipv6[1]!.toLowerCase()}]:${ipv6[2] ?? defaultPort}`;
  const plain = /^([a-z0-9.-]+)(?::(\d{1,5}))?$/i.exec(text);
  if (!plain) return null;
  const port = Number(plain[2] ?? defaultPort);
  if (!(port > 0 && port <= 65535)) return null;
  const host = plain[1]!.toLowerCase();
  if (host.startsWith(".") || host.endsWith(".") || host.includes("..")) return null;
  return `${host}:${port}`;
}

/** `https://host:port` for an address `host:port`. */
export function baseOf(address: string): string {
  return `https://${address}`;
}

/** A SHA-256 fingerprint as upper-case hex without separators (what the QR carries). */
export function compactFingerprint(fp: string): string {
  return fp.replace(/[^0-9a-f]/gi, "").toUpperCase();
}

/** As browsers show it: `AB:CD:…`. */
export function spacedFingerprint(fp: string): string {
  return (compactFingerprint(fp).match(/.{1,2}/g) ?? []).join(":");
}

const STATUS_CODES: Record<number, string> = { 401: "unauthorized", 403: "forbidden", 404: "not_found", 429: "rate_limited", 503: "busy" };

async function asReply(response: Response): Promise<Reply<unknown>> {
  const text = await response.text();
  try {
    const parsed = JSON.parse(text) as Reply<unknown>;
    if (parsed && typeof parsed === "object" && "ok" in parsed) return parsed;
  } catch {
    // Not JSON: a 403 "foreign Origin" or "unknown Host" is plain text.
  }
  const code = STATUS_CODES[response.status] ?? "internal";
  return { v: 1, ok: false, error: { code, message: text.trim() || `HTTP ${response.status}` } };
}

export function retryAfter(response: Response): number {
  const value = Number(response.headers.get("Retry-After"));
  return Number.isFinite(value) && value > 0 ? value : 1;
}

export function createApi(options: ApiOptions): Api {
  const base = options.base.replace(/\/+$/, "");
  const remote = options.credential.kind === "bearer";
  const timeoutMs = options.timeoutMs ?? TIMEOUT_MS;
  const doFetch = options.fetchImpl ?? ((input: RequestInfo | URL, init?: RequestInit) => fetch(input, init));
  const openSource = options.eventSource ?? ((url: string) => new EventSource(url));
  const emit = (event: AccessEvent): void => options.onEvent?.(event);

  function headers(json: boolean): Record<string, string> {
    const out: Record<string, string> = {};
    if (json) out["Content-Type"] = "application/json";
    if (options.credential.kind === "bearer") out["Authorization"] = `Bearer ${options.credential.token}`;
    return out;
  }

  async function request(method: string, path: string, body?: unknown): Promise<Response | null> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const init: RequestInit = {
        method,
        headers: headers(body !== undefined),
        cache: "no-store",
        // The cookie only travels to this very origin; a remote panel never sends one.
        credentials: remote ? "omit" : "same-origin",
        signal: controller.signal,
      };
      if (body !== undefined) init.body = JSON.stringify(body);
      return await doFetch(`${base}${path}`, init);
    } catch {
      emit({ kind: "offline", message: "the service did not answer" });
      return null;
    } finally {
      clearTimeout(timer);
    }
  }

  /** The reply, after saying what an access failure means to whoever listens. */
  async function reply(response: Response | null): Promise<Reply<unknown> | null> {
    if (response === null) return null;
    const parsed = await asReply(response);
    if (response.status === 401) emit({ kind: "unauthorized", message: parsed.ok ? "" : parsed.error.message });
    else if (response.status === 429) {
      const wait = retryAfter(response);
      const message = `Demasiados intentos fallidos desde esta dirección: esperá ${wait} s.`;
      emit({ kind: "rate_limited", message, retryAfterS: wait });
      if (!parsed.ok) parsed.error.message = message;
    } else if (response.status === 403) emit({ kind: "forbidden", message: parsed.ok ? "" : parsed.error.message });
    else emit({ kind: "online" });
    return parsed;
  }

  const api: Api = {
    base,
    remote,
    async command(op, args) {
      return (await api.raw({ op, ...args })) as Reply<ResultOf<typeof op>> | null;
    },
    async raw(message) {
      return reply(await request("POST", "/v1/command", { v: 1, ...message }));
    },
    async state() {
      return reply(await request("GET", "/v1/state"));
    },
    async stream(since) {
      const query = `since=${encodeURIComponent(String(since))}`;
      if (!remote) return openSource(`${base}/v1/stream?${query}`);
      const answer = await reply(await request("POST", "/v1/stream/ticket"));
      if (!answer || !answer.ok) return null;
      const ticket = (answer.result as { ticket: string }).ticket;
      return openSource(`${base}/v1/stream?ticket=${encodeURIComponent(ticket)}&${query}`);
    },
    async imageUrl(path) {
      if (!remote) {
        const response = await request("GET", path);
        return response && response.ok ? `${path}${path.includes("?") ? "&" : "?"}${Date.now()}` : null;
      }
      const response = await request("GET", path);
      if (!response || !response.ok) return null;
      return URL.createObjectURL(await response.blob());
    },
    url(path) {
      return `${base}${path}`;
    },
  };
  return api;
}

// -- without credentials: hello and pairing -----------------------------------------------

export interface Hello {
  service: string;
  contract: number;
  version: string;
  id: string;
  name: string;
  tls: { enabled: boolean; port?: number; root_sha256?: string; cert_sha256?: string };
  pairing: { accepting: boolean; first_window_s: number };
}

export type Probe =
  | { ok: true; hello: Hello }
  | { ok: false; reason: "unreachable" | "not_aurasync" | "rate_limited"; retryAfterS?: number };

/** `GET /v1/hello`. A fetch that throws is a device that does not answer **or** a certificate
 * this browser does not trust: a page cannot tell the two apart. */
export async function probe(base: string, fetchImpl: typeof fetch = fetch, timeoutMs = 6000): Promise<Probe> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetchImpl(`${base}/v1/hello`, { cache: "no-store", credentials: "omit", signal: controller.signal });
    if (response.status === 429) return { ok: false, reason: "rate_limited", retryAfterS: retryAfter(response) };
    const parsed = (await asReply(response)) as Reply<Hello>;
    if (!parsed.ok || parsed.result.service !== "aurasync") return { ok: false, reason: "not_aurasync" };
    return { ok: true, hello: parsed.result };
  } catch {
    return { ok: false, reason: "unreachable" };
  } finally {
    clearTimeout(timer);
  }
}

export interface PairTicket {
  id: string;
  check: string;
  status: string;
  expires_in_s: number;
}

export type PairPoll =
  | { status: "pending" | "delivered" | "denied" }
  | { status: "approved"; token: string; client: { id: string; name: string; scope: string } };

export type Unauthenticated<T> =
  | { ok: true; result: T }
  | { ok: false; status: number; message: string; retryAfterS?: number };

async function unauthenticated<T>(fetchImpl: typeof fetch, url: string, init: RequestInit): Promise<Unauthenticated<T>> {
  let response: Response;
  try {
    response = await fetchImpl(url, { cache: "no-store", credentials: "omit", ...init });
  } catch {
    return { ok: false, status: 0, message: "sin conexión con el equipo" };
  }
  const parsed = await asReply(response);
  if (parsed.ok) return { ok: true, result: parsed.result as T };
  const out: Unauthenticated<T> = { ok: false, status: response.status, message: parsed.error.message };
  if (response.status === 429) out.retryAfterS = retryAfter(response);
  return out;
}

export function pairRequest(
  base: string,
  body: { name: string; scope?: string; code?: string },
  fetchImpl: typeof fetch = fetch,
): Promise<Unauthenticated<PairTicket>> {
  return unauthenticated(fetchImpl, `${base}/v1/pair/request`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function pairPoll(base: string, id: string, fetchImpl: typeof fetch = fetch): Promise<Unauthenticated<PairPoll>> {
  return unauthenticated(fetchImpl, `${base}/v1/pair/${encodeURIComponent(id)}`, { method: "GET" });
}
