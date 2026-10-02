import { describe, expect, it } from "vitest";
import { ago, parseLink } from "../src/connect/link.ts";
import { type AccessEvent, createApi, normalizeAddress, pairPoll, probe, spacedFingerprint } from "../src/transport.ts";

type Call = { url: string; init: RequestInit };

function fakeFetch(answer: (call: Call) => Response | Error): { fetch: typeof fetch; calls: Call[] } {
  const calls: Call[] = [];
  const impl = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const call = { url: String(input), init: init ?? {} };
    calls.push(call);
    const out = answer(call);
    if (out instanceof Error) throw out;
    return out;
  }) as typeof fetch;
  return { fetch: impl, calls };
}

const json = (body: unknown, status = 200, headers: Record<string, string> = {}): Response =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json", ...headers } });

describe("addresses and links", () => {
  it("normalizes what a person types", () => {
    expect(normalizeAddress("aurasync.local")).toBe("aurasync.local:8443");
    expect(normalizeAddress("https://Aurasync.Local:9443/v1/hello")).toBe("aurasync.local:9443");
    expect(normalizeAddress("198.51.100.4:8443")).toBe("198.51.100.4:8443");
    expect(normalizeAddress("[FD00::1]")).toBe("[fd00::1]:8443");
    expect(normalizeAddress("")).toBeNull();
    expect(normalizeAddress("a b")).toBeNull();
    expect(normalizeAddress("host:99999")).toBeNull();
  });

  it("reads the QR's fragment, with or without a fingerprint", () => {
    const fp = "AB".repeat(32);
    expect(parseLink(`#d=aurasync.local:8443&fp=${fp}`)).toEqual({ address: "aurasync.local:8443", fp });
    expect(parseLink("#d=aurasync.local")).toEqual({ address: "aurasync.local:8443", fp: "" });
    expect(parseLink(`#d=x:1&fp=${spacedFingerprint(fp).toLowerCase()}`)?.fp).toBe(fp);
    expect(parseLink("#d=x:1&fp=abc123")?.fp).toBe("");
    expect(parseLink("#v=escuchar")).toBeNull();
  });

  it("says how long ago", () => {
    expect(ago(0)).toBe("nunca");
    expect(ago(1000, 1000 + 5 * 60_000)).toBe("hace 5 min");
    expect(ago(1000, 1000 + 2 * 86_400_000)).toBe("hace 2 días");
  });
});

describe("the remote transport", () => {
  it("sends the bearer, never the cookie, and trades it for a stream ticket", async () => {
    const { fetch, calls } = fakeFetch((c) =>
      c.url.endsWith("/v1/stream/ticket") ? json({ v: 1, ok: true, result: { ticket: "T1" } }) : json({ v: 1, ok: true, result: {} }),
    );
    const opened: string[] = [];
    const api = createApi({
      base: "https://dev:8443",
      credential: { kind: "bearer", token: "asc_x" },
      fetchImpl: fetch,
      eventSource: (url) => {
        opened.push(url);
        return {} as EventSource;
      },
    });
    await api.command("state", {});
    await api.stream(42);
    expect(calls[0]!.url).toBe("https://dev:8443/v1/command");
    expect((calls[0]!.init.headers as Record<string, string>)["Authorization"]).toBe("Bearer asc_x");
    expect(calls[0]!.init.credentials).toBe("omit");
    expect(opened).toEqual(["https://dev:8443/v1/stream?ticket=T1&since=42"]);
    expect(opened[0]).not.toContain("asc_");
  });

  it("the local transport uses the cookie and a plain EventSource", async () => {
    const { fetch, calls } = fakeFetch(() => json({ v: 1, ok: true, result: {} }));
    const opened: string[] = [];
    const api = createApi({ base: "", credential: { kind: "cookie" }, fetchImpl: fetch, eventSource: (u) => (opened.push(u), {} as EventSource) });
    await api.state();
    await api.stream(3);
    expect(calls[0]!.init.credentials).toBe("same-origin");
    expect(calls[0]!.init.headers).toEqual({});
    expect(opened).toEqual(["/v1/stream?since=3"]);
  });

  it("says each access failure once: 401, 403, 429 with Retry-After, and offline", async () => {
    const replies: (Response | Error)[] = [
      json({ v: 1, ok: false, error: { code: "unauthorized", message: "missing or wrong token" } }, 401),
      new Response("foreign Origin", { status: 403 }),
      json({ v: 1, ok: false, error: { code: "rate_limited", message: "too many" } }, 429, { "Retry-After": "7" }),
      new TypeError("Failed to fetch"),
    ];
    const { fetch } = fakeFetch(() => replies.shift()!);
    const events: AccessEvent[] = [];
    const api = createApi({ base: "https://d:1", credential: { kind: "bearer", token: "t" }, fetchImpl: fetch, onEvent: (e) => events.push(e) });
    const r401 = await api.state();
    const r403 = await api.state();
    const r429 = await api.state();
    const offline = await api.state();
    expect(events.map((e) => e.kind)).toEqual(["unauthorized", "forbidden", "rate_limited", "offline"]);
    expect(r401?.ok).toBe(false);
    expect(r403?.ok === false && r403.error.code).toBe("forbidden");
    expect(r429?.ok === false && r429.error.message).toContain("7 s");
    expect((events[2] as { retryAfterS: number }).retryAfterS).toBe(7);
    expect(offline).toBeNull();
  });

  it("no ticket, no stream", async () => {
    const { fetch } = fakeFetch(() => json({ v: 1, ok: false, error: { code: "unauthorized", message: "x" } }, 401));
    const api = createApi({ base: "https://d:1", credential: { kind: "bearer", token: "t" }, fetchImpl: fetch, eventSource: () => { throw new Error("opened"); } });
    expect(await api.stream(0)).toBeNull();
  });
});

describe("without credentials", () => {
  it("hello: a thrown fetch is unreachable (or an untrusted certificate), another service is not aurasync", async () => {
    expect(await probe("https://d:1", fakeFetch(() => new TypeError("cert")).fetch)).toEqual({ ok: false, reason: "unreachable" });
    const other = fakeFetch(() => json({ v: 1, ok: true, result: { service: "other" } })).fetch;
    expect(await probe("https://d:1", other)).toEqual({ ok: false, reason: "not_aurasync" });
    const busy = fakeFetch(() => json({}, 429, { "Retry-After": "3" })).fetch;
    expect(await probe("https://d:1", busy)).toEqual({ ok: false, reason: "rate_limited", retryAfterS: 3 });
  });

  it("polling a request that expired is a 404", async () => {
    const gone = fakeFetch(() => json({ v: 1, ok: false, error: { code: "not_found", message: "no such pairing request" } }, 404));
    expect(await pairPoll("https://d:1", "abc", gone.fetch)).toEqual({ ok: false, status: 404, message: "no such pairing request" });
    expect(gone.calls[0]!.url).toBe("https://d:1/v1/pair/abc");
  });
});
