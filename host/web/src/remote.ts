// The PWA's start (d-7c8794-37f9bc): the service worker, the remembered devices, the link a QR
// opened, and the panel on the chosen device. In local mode (the panel the service serves) none
// of this runs: main.tsx gives app.js the cookie transport at once.
import { h, render } from "preact";
import { current } from "./bridge.ts";
import { ConnectScreen } from "./connect/ConnectScreen.tsx";
import { parseLink } from "./connect/link.ts";
import { type Device, activeDevice, load, save, upsert } from "./devices.ts";
import { startPwa, statusLine } from "./pwa.ts";
import { announce, provide, runtime } from "./runtime.ts";
import { createDemoApi } from "./demo/api.ts";
import { type AccessEvent, baseOf, createApi } from "./transport.ts";

const SEEN_EVERY_MS = 60_000;

/** A calibration or a blind A/B in progress: an update must wait (thom's "never mid-song"). */
export function busy(state: unknown): boolean {
  const s = state as { calibration?: { state?: string } | null; ab?: { active?: boolean } | null } | null;
  if (!s) return false;
  return Boolean(s.ab?.active) || ["running", "measuring"].includes(s.calibration?.state ?? "");
}

function chip(device: Device | null): void {
  const node = document.getElementById("device-open");
  if (!node) return;
  node.hidden = false;
  node.textContent = device ? device.name : "Elegir equipo";
  node.title = device ? `${device.address}: tocá para cambiar de equipo o administrarlo` : "";
}

function start(device: Device): void {
  let seenAt = 0;
  const onEvent = (event: AccessEvent): void => {
    announce(event);
    if (event.kind !== "online" || Date.now() - seenAt < SEEN_EVERY_MS) return;
    seenAt = Date.now();
    void load().then((saved) => {
      const found = saved.devices.find((d) => d.id === device.id);
      if (found) return save(upsert(saved, { ...found, lastSeen: seenAt }));
    });
  };
  const api = createApi({ base: baseOf(device.address), credential: { kind: "bearer", token: device.token ?? "" }, onEvent });
  chip(device);
  provide(api, { name: device.name, address: device.address });
}

/** Whether this page was opened as the demo (`?demo`): the panel with no device (demo/api.ts). */
export function isDemo(search: string = location.search): boolean {
  return new URLSearchParams(search).has("demo");
}

/** The warning while the demo runs: always at the top, with the way out. */
function demoBanner(): void {
  const banner = document.createElement("div");
  banner.className = "demo-banner";
  banner.setAttribute("role", "alert");
  banner.dataset["demo"] = "1";
  const text = document.createElement("span");
  text.innerHTML =
    "<b>Modo demo:</b> no hay ningún equipo conectado; nada suena y lo que cambies se pierde al cerrar.";
  const leave = document.createElement("button");
  leave.type = "button";
  leave.className = "btn small-btn";
  leave.textContent = "Salir de la demo";
  leave.addEventListener("click", () => location.assign(location.pathname));
  banner.append(text, leave);
  document.body.prepend(banner);
}

function startDemo(): void {
  demoBanner();
  const node = document.getElementById("device-open");
  if (node) {
    node.hidden = false;
    node.textContent = "Demo";
    node.title = "Modo demo: tocá para salir y conectarte a un equipo";
    node.addEventListener("click", () => location.assign(location.pathname));
  }
  provide(createDemoApi(), { name: "Demo", address: "demo" });
}

export async function bootRemote(): Promise<void> {
  runtime.busy = () => busy(current().state);
  runtime.pwaStatus = () => statusLine();
  if (isDemo()) {
    void startPwa(runtime.busy);
    startDemo();
    return;
  }
  // The link of a QR, read before app.js puts its `#v=` in the address.
  const link = parseLink(location.hash);
  if (link) history.replaceState(history.state, "", `${location.pathname}${location.search}`);
  void startPwa(runtime.busy);
  document.getElementById("device-open")?.addEventListener("click", () => {
    document.dispatchEvent(new CustomEvent("aurasync:connect-open"));
  });
  const saved = await load();
  const active = activeDevice(saved);
  const root = document.getElementById("connect-root");
  if (root) render(h(ConnectScreen, { initial: saved, link, start }), root);
  if (active?.token) start(active);
  else chip(null);
}
