// The page's side of the PWA's service worker (host/web/pwa/sw.js; d-7c8794-37f9bc).
//
// Registers it, keeps the status line Diagnóstico shows (`sw <state> <cached>/<total>  upd
// <state>`, the only way to see from across the room what the worker did), and answers its
// `busy?`: a new version is applied by reloading the page, and never while a calibration or a
// blind A/B runs. Busy, the page is told `updated` and reloads itself as soon as it is idle.
// The rules are thom-music-player's (docs/platform-web.md, "Offline"), adapted.

import { subscribe } from "./bridge.ts";

export interface PwaStatus {
  /** `none` (no service workers), `insecure`, `registering`, `active`, `error <why>`. */
  sw: string;
  cached: number | null;
  total: number | null;
  /** `idle`, `checking`, `downloading`, `ready` (waiting for an idle moment), `failed`. */
  upd: string;
  version: string;
  build: { version?: string; commit?: string } | null;
}

export const status: PwaStatus = { sw: "registering", cached: null, total: null, upd: "idle", version: "", build: null };

export function statusLine(s: PwaStatus = status): string {
  const count = s.total === null ? "" : ` ${s.cached ?? 0}/${s.total}`;
  const build = s.build ? `  ${s.build.version ?? "?"} ${s.build.commit ?? "?"}` : "";
  return `sw ${s.sw}${count}  upd ${s.upd}${build}`;
}

function show(): void {
  const node = document.getElementById("pwa-status");
  if (node) {
    node.hidden = false;
    node.textContent = statusLine();
  }
}

function set(changes: Partial<PwaStatus>): void {
  Object.assign(status, changes);
  show();
}

/** `url` with `?v=<version>`, its fragment kept: the reload has to change the address (WebKit
 * treats navigating to the same URL with a fragment as a jump within the page). */
export function fresh(url: string, version: string): string {
  const address = new URL(url);
  address.searchParams.set("v", version);
  return address.href;
}

/** The address without the `?v=` the last update added. */
export function withoutVersion(url: string): string {
  const address = new URL(url);
  address.searchParams.delete("v");
  return address.href;
}

let registration: ServiceWorkerRegistration | null = null;

async function askStatus(): Promise<void> {
  const worker = navigator.serviceWorker.controller ?? registration?.active;
  if (!worker) return;
  const channel = new MessageChannel();
  const answer = new Promise<{ version: string; cached: number; total: number } | null>((resolve) => {
    const timer = setTimeout(() => resolve(null), 2000);
    channel.port1.onmessage = (event) => {
      clearTimeout(timer);
      resolve(event.data as { version: string; cached: number; total: number });
    };
  });
  worker.postMessage({ aurasync: "status" }, [channel.port2]);
  const got = await answer;
  if (got) set({ sw: "active", cached: got.cached, total: got.total, version: got.version });
}

function watchInstalling(worker: ServiceWorker | null): void {
  if (!worker) return;
  // The very first install is not an update.
  if (navigator.serviceWorker.controller) set({ upd: "downloading" });
  worker.addEventListener("statechange", () => {
    if (worker.state === "redundant" && status.upd === "downloading") set({ upd: "failed" });
    if (worker.state === "activated") void askStatus();
  });
}

/** Reload into `version` once nothing must be left alone. */
function applyWhenIdle(version: string, busy: () => boolean): void {
  set({ upd: "ready" });
  const tryNow = (): boolean => {
    if (busy()) return false;
    location.replace(fresh(location.href, version));
    return true;
  };
  if (tryNow()) return;
  const stop = subscribe(() => {
    if (tryNow()) stop();
  });
}

export async function startPwa(busy: () => boolean): Promise<void> {
  if (new URL(location.href).searchParams.has("v")) history.replaceState(history.state, "", withoutVersion(location.href));
  try {
    const response = await fetch("./build.json", { cache: "no-store" });
    if (response.ok) status.build = (await response.json()) as PwaStatus["build"];
  } catch {
    // Offline before the worker serves it: the line says so without the build.
  }
  if (!("serviceWorker" in navigator)) {
    set({ sw: window.isSecureContext ? "none" : "insecure" });
    return;
  }
  navigator.serviceWorker.addEventListener("message", (event: MessageEvent) => {
    const data = event.data as { aurasync?: string; version?: string } | null;
    if (data?.aurasync === "busy?") event.ports[0]?.postMessage(busy() ? "busy" : "idle");
    else if (data?.aurasync === "updated" && data.version) applyWhenIdle(data.version, busy);
  });
  try {
    registration = await navigator.serviceWorker.register("./sw.js", { scope: "./", updateViaCache: "none" });
  } catch (error) {
    set({ sw: `error ${error instanceof Error ? error.name : String(error)}` });
    return;
  }
  watchInstalling(registration.installing);
  registration.addEventListener("updatefound", () => watchInstalling(registration?.installing ?? null));
  navigator.serviceWorker.addEventListener("controllerchange", () => void askStatus());
  await navigator.serviceWorker.ready;
  await askStatus();
  setInterval(() => void askStatus(), 10_000);
  // Look for a new version now and then; GitHub Pages serves sw.js with a 10-minute cache, which
  // `updateViaCache: "none"` skips.
  setInterval(() => void checkForUpdate(), 30 * 60_000);
}

export async function checkForUpdate(): Promise<void> {
  if (!registration) return;
  if (status.upd === "idle" || status.upd === "failed") set({ upd: "checking" });
  try {
    await registration.update();
  } catch {
    set({ upd: "failed" });
    return;
  }
  if (status.upd === "checking") set({ upd: registration.installing ? "downloading" : "idle" });
}
