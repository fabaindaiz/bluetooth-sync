// The panel's Preact side (d-7c8794-6da524) and its connection (d-7c8794-37f9bc).
//
// The same bundle runs in two places. Served by the device (`aurasync service`), it gives app.js
// the cookie transport at once. As the PWA (index.html carries `aurasync-mode=remote`), it starts
// the service worker and the connection screen, and gives app.js a bearer transport once a
// device is chosen. Then it draws the Preact screens: Cadena (#chain-root) and who may use the
// device (#pair-admin-root, in the "Conectar teléfono" dialog).
import { render } from "preact";
import { connect } from "./bridge.ts";
import { ChainScreen } from "./chain/ChainScreen.tsx";
import { AccessAdmin } from "./connect/AccessAdmin.tsx";
import { activeDevice, load } from "./devices.ts";
import { bootRemote } from "./remote.ts";
import { announce, install, pageMode, provide } from "./runtime.ts";
import { createApi } from "./transport.ts";
import { createUndo } from "./undo.ts";

connect();
const runtime = install(pageMode());
if (runtime.mode === "local") provide(createApi({ base: "", credential: { kind: "cookie" }, onEvent: announce }));
else void bootRemote();
// The "Deshacer" notice (#undo in index.html): a module runs after the page is parsed.
runtime.undo = createUndo(document.getElementById("undo"), async (message) => {
  const { op, ...args } = message;
  const api = runtime.api ?? (await runtime.ready);
  return api.raw({ op, ...args });
});

async function mountAdmin(): Promise<void> {
  const root = document.getElementById("pair-admin-root");
  const dialog = document.getElementById("pair-dialog") as HTMLDialogElement | null;
  if (!root || !dialog) return;
  let selfId: string | null = null;
  if (runtime.mode === "remote") {
    const device = activeDevice(await load());
    if (device?.scope !== "admin") return;
    selfId = device.clientId;
  }
  await runtime.ready;
  root.hidden = false;
  render(<AccessAdmin visible={() => dialog.open} selfId={selfId} />, root);
}

function mount(): void {
  const root = document.getElementById("chain-root");
  if (root) render(<ChainScreen root={root} />, root);
  void mountAdmin();
}

if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", mount);
else mount();
