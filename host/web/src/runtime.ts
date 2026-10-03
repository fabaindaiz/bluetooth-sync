// What app.js and the Preact screens share about the connection: which mode the page runs in,
// the one `Api` (transport.ts), and whether something must not be interrupted. It lives on
// `window.aurasync`, which this module sets when cadena.js loads — before DOMContentLoaded, when
// app.js starts — so app.js (a classic script, no imports) finds it there.
import type { AccessEvent, Api } from "./transport.ts";
import type { Undo } from "./undo.ts";

export type Mode = "local" | "remote";

export interface Runtime {
  mode: Mode;
  /** Null until a device is chosen (remote mode) or at once (local mode). */
  api: Api | null;
  /** Resolves with the api once there is one. */
  ready: Promise<Api>;
  /** The device the remote panel talks to; null in local mode. */
  device: { name: string; address: string } | null;
  /** A calibration or a blind A/B is running: no update reloads the page meanwhile. */
  busy(): boolean;
  /** The PWA's status line (`sw … upd …`), for Diagnóstico and the tests. */
  pwaStatus(): string;
  /** The "Deshacer" notice (undo.ts), instead of confirm(). Null until the page is parsed. */
  undo: Undo | null;
}

declare global {
  interface Window {
    aurasync?: Runtime;
  }
}

export function pageMode(doc: Document = document): Mode {
  return doc.querySelector<HTMLMetaElement>('meta[name="aurasync-mode"]')?.content === "remote" ? "remote" : "local";
}

let resolveReady: (api: Api) => void = () => undefined;

export const runtime: Runtime = {
  mode: "local",
  api: null,
  ready: new Promise<Api>((resolve) => {
    resolveReady = resolve;
  }),
  device: null,
  busy: () => false,
  pwaStatus: () => "",
  undo: null,
};

export function install(mode: Mode): Runtime {
  runtime.mode = mode;
  window.aurasync = runtime;
  return runtime;
}

export function provide(api: Api, device: Runtime["device"] = null): void {
  runtime.api = api;
  runtime.device = device;
  resolveReady(api);
}

/** Every access failure, as a DOM event app.js and the screens listen to. */
export function announce(event: AccessEvent): void {
  document.dispatchEvent(new CustomEvent("aurasync:access", { detail: event }));
}
