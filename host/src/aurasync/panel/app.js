// Panel de aurasync sobre el servicio de control (spec del servicio §15). Sin build ni
// dependencias. Habla solo el contrato de control.py: lee GET /v1/state y manda cada orden
// como el mensaje crudo por POST /v1/command, igual que viajaría por serie.
// Los textos que vienen del servicio o del log se insertan con textContent, nunca como HTML.
"use strict";

const $ = (id) => document.getElementById(id);

const ROLE_POSITIONS = {
  FL: [22, 26], FR: [78, 26], RL: [22, 80], RR: [78, 80], FC: [50, 20], RC: [50, 82],
};
const ROLE_NAMES = {
  FL: "frontal izquierdo", FR: "frontal derecho", FC: "centro",
  RL: "trasero izquierdo", RR: "trasero derecho", RC: "trasero (surround)",
};
const SERVICE_STATUS = {
  running: ["", "corriendo"], starting: ["", "iniciando"], stopping: ["", "deteniendo"],
  stopped: ["", "detenido"], failed: ["", "falló"], unavailable: ["", "no disponible"],
};
const SPEAKER_STATUS = {
  playing: ["", "sonando"], connected: ["", "conectado"], lost: ["", "perdido"],
  disconnected: ["", "desconectado"], unknown: ["", "sin observar"],
};
const KIND_NAMES = { proceso: "proceso", tarea: "tarea", sistema: "sistema (solo se observa)" };
const LEVELS = { debug: 10, info: 20, warning: 30, error: 40, critical: 50 };
const SILENCE = -120;
const LOG_CAPACITY = 2000;
const STATE_EVERY_MS = 500;
const LOGS_EVERY_MS = 1000;
const SERIES = ["--s1", "--s2", "--s3", "--s4", "--s5", "--s6"];

let latest = null;
let online = false;
let renderQueued = false;

// -- organizaciones -------------------------------------------------------------
// Las mismas tarjetas, repartidas de cuatro maneras. Se elige con ?layout= (y queda
// recordada); docs/research/10-panel-de-control.md mide las cuatro con las tareas típicas.

const CARDS_ALL = ["now", "presets", "quick", "ab", "room", "speakers", "devices", "calibration", "response",
  "health", "cuts", "levels", "input", "services", "logs", "config"];
const WIDE = new Set(["speakers", "devices", "services", "logs", "config", "calibration"]);
const LAYOUTS = {
  pagina: { nav: "none", views: [{ id: "todo", label: "Todo", icon: "list", cards: CARDS_ALL }] },
  pestanas: { nav: "tabs", views: [
    // La pantalla principal: lo de todos los días arriba (estado, efectos, presets, parlantes,
    // niveles); comparar y analizar la entrada, más abajo (research/10 §3, escenarios).
    { id: "escuchar", label: "Escuchar", icon: "play", cards: ["now", "presets", "quick", "levels", "ab", "input"] },
    // Primero conectar, después ajustar cada parlante, al final la sala.
    { id: "parlantes", label: "Parlantes", icon: "speaker", cards: ["devices", "speakers", "room"] },
    { id: "calibrar", label: "Calibrar", icon: "target", cards: ["calibration", "response"] },
    { id: "diagnostico", label: "Diagnóstico", icon: "activity", cards: ["health", "cuts", "services", "logs"],
      columns: { left: ["health", "cuts"], right: ["services"], bottom: ["logs"] } },
    { id: "ajustes", label: "Ajustes", icon: "sliders", cards: ["config"] },
  ] },
  inicio: { nav: "hub", views: [
    { id: "inicio", label: "Inicio", icon: "home", cards: ["now", "presets", "quick", "hub"] },
    { id: "parlantes", label: "Sala y parlantes", icon: "speaker", cards: ["devices", "speakers", "room"] },
    { id: "calibrar", label: "Calibrar", icon: "target", cards: ["calibration", "response"] },
    { id: "comparar", label: "A/B ciego", icon: "compare", cards: ["ab", "levels"] },
    { id: "diagnostico", label: "Diagnóstico", icon: "activity", cards: ["health", "cuts", "input", "services", "logs"],
      columns: { left: ["health", "cuts", "input"], right: ["services"], bottom: ["logs"] } },
    { id: "ajustes", label: "Ajustes", icon: "sliders", cards: ["config"] },
  ] },
  lateral: { nav: "sidebar", views: [
    { id: "sonido", label: "Sonido", icon: "play", cards: ["now", "presets", "quick", "levels", "ab", "input"] },
    { id: "sala", label: "Sala", icon: "speaker", cards: ["devices", "speakers", "room"] },
    { id: "medir", label: "Medir", icon: "target", cards: ["calibration", "response", "health", "cuts"] },
    { id: "sistema", label: "Sistema", icon: "server", cards: ["services", "logs", "config"] },
  ] },
};
const DEFAULT_LAYOUT = "pestanas";

// Íconos de trazo, dibujados acá: se ven igual en todos los sistemas (un emoji no) y un lector
// de pantalla no los anuncia (aria-hidden); la etiqueta de texto va siempre al lado.
const ICONS = {
  list: "M4 6h16M4 12h16M4 18h16",
  play: "M8 5v14l11-7z",
  speaker: "M11 5 6 9H3v6h3l5 4V5zM15.5 8.5a5 5 0 0 1 0 7M18.5 5.5a9 9 0 0 1 0 13",
  target: "M12 3v3M12 18v3M3 12h3M18 12h3M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8z",
  activity: "M3 12h4l3-8 4 16 3-8h4",
  sliders: "M4 7h10M18 7h2M4 17h4M12 17h8M14 4v6M8 14v6",
  home: "M3 11 12 4l9 7M5 10v10h14V10",
  server: "M4 4h16v6H4zM4 14h16v6H4zM8 7h.01M8 17h.01",
  compare: "M7 4v16M17 4v16M3 8h8M13 16h8",
};

function icon(name) {
  const node = svg("svg", { viewBox: "0 0 24 24", class: "nav-icon h-5 w-5", fill: "none", stroke: "currentColor",
    "stroke-width": "1.8", "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true" });
  node.append(svg("path", { d: ICONS[name] || ICONS.list }));
  return node;
}
let layoutName = DEFAULT_LAYOUT;
let currentView = null;

function chooseLayout() {
  const asked = new URLSearchParams(location.search).get("layout");
  let saved = null;
  try { saved = localStorage.getItem("aurasync.layout"); } catch { /* sin almacenamiento */ }
  const name = [asked, saved, DEFAULT_LAYOUT].find((n) => n && n in LAYOUTS);
  try { localStorage.setItem("aurasync.layout", name); } catch { /* sin almacenamiento */ }
  return name;
}

function hubCard(layout) {
  const grid = el("div", { class: "grid grid-cols-2 gap-2 sm:grid-cols-3" });
  for (const view of layout.views.slice(1)) {
    const b = el("button", { type: "button", class: "btn min-h-16 flex-col", "data-goto": view.id },
      icon(view.icon), el("span", { text: view.label }));
    b.addEventListener("click", () => showView(view.id));
    grid.append(b);
  }
  return el("section", { class: "card", "data-card": "hub" }, el("div", { class: "card-head" }, el("h2", { class: "card-title", text: "Más" })), grid);
}

function buildLayout() {
  layoutName = chooseLayout();
  const layout = LAYOUTS[layoutName];
  document.body.dataset.layout = layoutName;
  const cards = Object.fromEntries([...document.querySelectorAll("#cards > [data-card]")].map((c) => [c.dataset.card, c]));
  const views = $("views");
  const sidebar = layout.nav === "sidebar";
  views.className = `mx-auto max-w-7xl px-4 pt-4 pb-24 sm:pb-8 ${sidebar ? "lg:grid lg:grid-cols-[12rem_1fr] lg:gap-6" : ""}`;
  views.replaceChildren();
  let side = null;
  if (sidebar) {
    side = el("nav", { id: "side-nav", class: "sticky top-40 hidden h-fit flex-col gap-1 self-start lg:flex", "aria-label": "Secciones" });
    views.append(side);
  }
  const holder = sidebar ? el("div", { class: "min-w-0" }) : views;
  if (sidebar) views.append(holder);
  for (const view of layout.views) {
    const section = el("div", { class: "view grid grid-cols-1 gap-4 lg:grid-cols-2", "data-view": view.id, hidden: "" });
    if (layout.nav === "hub" && view !== layout.views[0]) {
      const back = el("button", { type: "button", class: "btn btn-ghost w-fit lg:col-span-2", "data-back": "" }, "← Inicio");
      back.addEventListener("click", () => showView(layout.views[0].id));
      section.append(back, el("h1", { class: "text-lg font-semibold lg:col-span-2", text: view.label }));
    }
    if (view.columns) {
      // Zonas fijas: en Diagnóstico, salud y cortes a la izquierda, servicios a la derecha y
      // los logs abajo, a todo el ancho (lo pidió el usuario el 2026-10-01).
      const zone = (ids, extra) => el("div", { class: `flex min-w-0 flex-col gap-4 ${extra}` },
        ...ids.map((id) => cards[id]).filter(Boolean).map((card) => { card.classList.remove("lg:col-span-2"); return card; }));
      section.append(zone(view.columns.left, ""), zone(view.columns.right, ""), zone(view.columns.bottom, "lg:col-span-2"));
    } else {
      for (const id of view.cards) {
        const card = id === "hub" ? hubCard(layout) : cards[id];
        if (!card) continue;
        card.classList.toggle("lg:col-span-2", WIDE.has(id));
        section.append(card);
      }
    }
    holder.append(section);
  }
  const tabs = layout.nav === "tabs" || sidebar;
  const top = $("nav");
  const bottom = $("bottom-nav");
  top.replaceChildren();
  bottom.replaceChildren();
  top.classList.toggle("hidden", true);
  top.classList.toggle("sm:flex", layout.nav === "tabs");
  bottom.classList.toggle("hidden", !tabs);
  bottom.classList.toggle("sm:hidden", layout.nav === "tabs");
  bottom.classList.toggle("lg:hidden", sidebar);
  if (tabs) {
    for (const view of layout.views) {
      const make = (extra) => {
        const b = el("button", { type: "button", class: `nav-tab ${extra}`, "data-goto": view.id },
          icon(view.icon), el("span", { text: view.label }));
        b.addEventListener("click", () => showView(view.id));
        return b;
      };
      if (layout.nav === "tabs") top.append(make(""));
      if (side) side.append(make("sm:flex-row sm:justify-start"));
      bottom.append(make(""));
    }
    bottom.classList.add("flex");
  }
  const wanted = new URLSearchParams(location.hash.slice(1)).get("v");
  showView(layout.views.some((v) => v.id === wanted) ? wanted : layout.views[0].id, false);
}

function showView(id, remember = true) {
  currentView = id;
  for (const v of document.querySelectorAll("[data-view]")) v.hidden = v.dataset.view !== id;
  for (const b of document.querySelectorAll("[data-goto]")) {
    if (b.closest("[data-card=hub]")) continue;
    if (b.dataset.goto === id) b.setAttribute("aria-current", "page");
    else b.removeAttribute("aria-current");
  }
  if (remember) {
    history.replaceState(null, "", `${location.pathname}${location.search}#v=${id}`);
    window.scrollTo({ top: 0 });
  }
  // Los gráficos se dibujan con el ancho real: al mostrarse una vista, se redibujan.
  if (latest) render(latest);
}

// Para los tests y la medición: muestra la vista que contiene un elemento.
window.aurasyncShow = (selector) => {
  const node = document.querySelector(selector);
  const view = node && node.closest("[data-view]");
  if (view && view.hidden) showView(view.dataset.view);
  return Boolean(view);
};

// -- utilidades -----------------------------------------------------------------

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (key === "text") node.textContent = value;
    else if (key === "class") node.className = value;
    else node.setAttribute(key, value);
  }
  for (const child of children) node.append(child);
  return node;
}

function svg(tag, attrs = {}) {
  const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
  return node;
}

let toastTimer = null;
function toast(message) {
  const box = $("toast");
  box.textContent = message;
  box.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { box.hidden = true; }, 5000);
}

function duration(seconds) {
  if (seconds == null) return "—";
  if (seconds < 60) return `${Math.floor(seconds)} s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ${Math.floor(seconds % 60)} s`;
  return `${Math.floor(seconds / 3600)} h ${Math.floor((seconds % 3600) / 60)} min`;
}

const fmt = (value, digits = 1) => (value == null ? "—" : Number(value).toFixed(digits));

// -- controles que no se pisan --------------------------------------------------
// Un control se escribe solo cuando cambia su valor en el servicio, y nunca mientras se
// edita. "Editar" empieza con pointerdown o focus, porque en Firefox para macOS un clic en
// un <select> no le da el foco (bug de la rama panel-demo, su doc 09 §7.4).

const editables = new Set();

function editable(node) {
  editables.add(node);
  const start = () => { node.dataset.editing = "1"; node.dataset.editingSince = String(Date.now()); };
  const stop = () => { node.dataset.editing = "0"; };
  node.addEventListener("pointerdown", start);
  node.addEventListener("focus", start);
  node.addEventListener("change", stop);
  node.addEventListener("blur", stop);
  return node;
}

document.addEventListener("pointerdown", (event) => {
  for (const node of editables) {
    if (node !== event.target && !node.contains(event.target) && document.activeElement !== node) {
      node.dataset.editing = "0";
    }
  }
}, true);

function isEditing(node) {
  if (document.activeElement === node && node.type !== "checkbox" && node.type !== "range") return true;
  if (node.dataset.editing !== "1") return false;
  return Date.now() - Number(node.dataset.editingSince || 0) < 8000;
}

function syncValue(node, value) {
  const text = value == null ? "" : String(value);
  if (node.dataset.synced === text || isEditing(node)) return;
  if (node.type === "checkbox") node.checked = Boolean(value);
  else node.value = text;
  node.dataset.synced = text;
}

function resync(node) {
  node.dataset.synced = "\u0000";
  node.dataset.editing = "0";
}

// -- el contrato ----------------------------------------------------------------

async function send(op, args = {}) {
  let reply;
  try {
    const response = await fetch("/v1/command", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ v: 1, op, ...args }),
    });
    reply = await response.json();
  } catch {
    toast("Sin conexión: la orden no se envió.");
    return null;
  }
  if (!reply.ok) toast(reply.error.message);
  return reply;
}

async function sendFrom(node, op, args) {
  const reply = await send(op, args);
  if (!reply || !reply.ok) resync(node);
  return reply;
}

function setSpeaker(node, speaker, changes) { return sendFrom(node, "set", { speaker, changes }); }
function setGlobal(node, changes) { return sendFrom(node, "set", { changes }); }

// Un deslizador manda mientras se arrastra, como mucho cada 80 ms, y al soltarlo. Con el stream
// la respuesta vuelve en el cuadro siguiente.
const SLIDER_EVERY_MS = 80;
function liveRange(node, output, format, sendValue) {
  editable(node);
  let last = 0;
  let timer = null;
  node.addEventListener("input", () => {
    output.textContent = format(Number(node.value));
    const now = Date.now();
    clearTimeout(timer);
    if (now - last > SLIDER_EVERY_MS) { last = now; sendValue(Number(node.value)); }
    else timer = setTimeout(() => { last = Date.now(); sendValue(Number(node.value)); }, SLIDER_EVERY_MS);
  });
  node.addEventListener("change", () => { clearTimeout(timer); sendValue(Number(node.value)); });
}

async function pollState() {
  if (live.alive) { setTimeout(pollState, STATE_EVERY_MS); return; }
  try {
    const response = await fetch("/v1/state", { credentials: "same-origin", cache: "no-store" });
    if (response.status === 401) {
      setOnline(false, "Sin autorización: abrí el link con el token que imprime aurasync service.");
      return;
    }
    const reply = await response.json();
    latest = reply.result;
    setOnline(true);
    if (!renderQueued) {
      renderQueued = true;
      requestAnimationFrame(() => { renderQueued = false; render(latest); });
    }
  } catch {
    setOnline(false);
  } finally {
    setTimeout(pollState, STATE_EVERY_MS);
  }
}

// -- stream en vivo (spec §17) ---------------------------------------------------
// El estado, los niveles a 20 Hz, la entrada a 10 Hz y los logs llegan por Server-Sent Events.
// Mientras el stream vive, las consultas periódicas se saltan; si falla 3 veces seguidas, el
// panel vuelve a consultar y lo dice ("Consultando").
const live = { source: null, alive: false, failures: 0, meters: null, metersAt: 0, input: null, shownMode: "" };
const STREAM_MAX_FAILURES = 3;

function openStream() {
  if (typeof EventSource === "undefined" || live.failures >= STREAM_MAX_FAILURES) return;
  const source = new EventSource(`/v1/stream?since=${logSince}`);
  live.source = source;
  source.addEventListener("open", () => { live.failures = 0; live.alive = true; setOnline(true); });
  source.addEventListener("state", (e) => {
    latest = JSON.parse(e.data);
    live.alive = true;
    setOnline(true);
    if (!renderQueued) {
      renderQueued = true;
      requestAnimationFrame(() => { renderQueued = false; render(latest); });
    }
  });
  source.addEventListener("meters", (e) => { live.meters = JSON.parse(e.data); live.metersAt = performance.now(); });
  source.addEventListener("input", (e) => { live.input = JSON.parse(e.data); });
  source.addEventListener("log", (e) => ingestLogs(JSON.parse(e.data)));
  source.addEventListener("error", () => {
    source.close();
    live.alive = false;
    live.failures += 1;
    setOnline(online);
    if (live.failures < STREAM_MAX_FAILURES) setTimeout(openStream, 1000 * live.failures);
  });
}

function setOnline(value, message) {
  const mode = `${value}|${live.alive}`;
  if (online === value && live.shownMode === mode && !message) return;
  live.shownMode = mode;
  online = value;
  $("connection").textContent = value ? (live.alive ? "En vivo" : "Consultando") : "Desconectado";
  $("connection").title = value
    ? (live.alive ? "Recibe el estado y los niveles por stream" : "Sin stream: consulta el estado cada 500 ms")
    : "";
  $("connection").classList.toggle("ok", value);
  $("disconnected").hidden = value;
  if (message) $("disconnected").textContent = message;
  document.body.classList.toggle("stale", !value);
}

// -- dibujo ---------------------------------------------------------------------

function render(s) {
  document.body.dataset.ready = "1";
  renderTop(s);
  renderQuick(s);
  renderControls(s);
  renderRoom(s);
  renderSpeakers(s);
  renderDevices(s);
  renderServices(s);
  renderHealth(s);
  renderCuts(s);
  renderNow(s);
  renderChart(s);
  renderMeters(s);
  renderInput(s);
  renderMicrophones(s);
  renderConfig(s);
  renderCalibration(s);
  renderPresets(s);
}

function renderTop(s) {
  $("engine-badge").hidden = !s.service.simulated;
  $("dirty").hidden = !s.dirty;
  $("save").hidden = !s.dirty;
  $("pair-open").hidden = !(s.pairing && s.pairing.urls && s.pairing.urls.some((u) => !u.includes("127.0.0.1")));
  const box = $("warnings");
  const warnings = s.warnings.filter((w) => !w.includes("unsaved"));
  box.hidden = warnings.length === 0;
  box.replaceChildren(...warnings.map((w) => el("div", {}, el("strong", { text: "Aviso: " }), inSpanish(w))));
}

// Los avisos del servicio vienen en inglés, como todo el contrato (d-7c8794-7b3093).
const WARNINGS = [
  [/^no installation at (.*)$/, (m) => `No hay instalación en ${m[1]}.`],
  [/^the calibration was measured with decorrelation on$/, () => "La calibración se midió con la decorrelación encendida: apagarla corre los parlantes 1 a 2 ms."],
  [/^alignment not measured in this session$/, () => "La alineación no se está midiendo en esta sesión (el lazo de recalibración está apagado)."],
  [/^speakers without a stream \(the rest keep playing\): (.*)$/, (m) => `Parlantes sin stream (los demás siguen sonando): ${m[1]}.`],
  [/^streams moved back to their speaker (\d+) time\(s\) during this session$/, (m) => `Streams devueltos a su parlante ${m[1]} vez/veces en esta sesión.`],
];

function inSpanish(text) {
  for (const [pattern, say] of WARNINGS) {
    const m = text.match(pattern);
    if (m) return say(m);
  }
  return text;
}

function renderControls(s) {
  const run = $("run");
  const status = s.session.status;
  run.textContent = { playing: "Detener", starting: "Iniciando…" }[status] || "Iniciar";
  const busy = status === "playing" || status === "starting";
  run.classList.toggle("btn-stop", busy);
  run.classList.toggle("btn-primary", !busy);
  run.disabled = status === "starting";
  run.title = status === "error" ? `La última sesión falló: ${s.session.reason}` : "";
  for (const button of document.querySelectorAll("[data-room-layout]")) {
    button.setAttribute("aria-pressed", String(button.dataset.roomLayout === s.global.layout));
  }
  const source = s.source || { kind: "system", name: null, busy: false };
  syncValue($("source-kind"), source.kind);
  $("source-kind").disabled = !s.source || source.busy;
  const apps = $("source-app");
  const key = s.apps.join("\u0001");
  if (apps.dataset.apps !== key) {
    apps.replaceChildren(el("option", { value: "", text: s.apps.length ? "— elegí una" : "nada está sonando" }),
      ...s.apps.map((a) => el("option", { value: a, text: a })));
    apps.dataset.apps = key;
    apps.dataset.synced = "\u0000";
  }
  if (source.kind === "app") syncValue(apps, source.name || "");
  if (source.kind === "file") syncValue($("source-file"), source.name || "");
  syncSourceFields();
  syncValue($("volume"), s.global.volume_db);
  $("volume-out").textContent = `${Number($("volume").value).toFixed(0)} dB`;
  const l = s.latency;
  $("latency-total").textContent = l.measured_ms != null ? `${Math.round(l.measured_ms)} ms` : `≥ ${Math.round(l.known_ms)} ms`;
  $("chip-preset").hidden = !s.preset;
  $("chip-preset").textContent = s.preset ? `Preset: ${s.preset}` : "";
}

function syncSourceFields() {
  const kind = $("source-kind").value;
  $("source-app").hidden = kind !== "app";
  $("source-file").hidden = kind !== "file";
}

// -- sala y parlantes -----------------------------------------------------------

let roomLayout = null;
const slots = new Map();

function renderRoom(s) {
  const room = $("room");
  if (roomLayout !== s.global.layout) {
    for (const slot of slots.values()) slot.box.remove();
    slots.clear();
    for (const role of s.roles[s.global.layout]) {
      const [x, y] = ROLE_POSITIONS[role];
      const name = el("div", { class: "slot-name" });
      const box = el("div", { class: "slot", title: ROLE_NAMES[role] }, el("div", { class: "slot-channel", text: role }), name);
      box.style.left = `${x}%`;
      box.style.top = `${y}%`;
      room.append(box);
      slots.set(role, { box, name });
    }
    roomLayout = s.global.layout;
  }
  for (const [role, slot] of slots) {
    const speaker = s.speakers.find((sp) => sp.role === role);
    slot.name.textContent = speaker ? speaker.name : "libre";
    slot.box.classList.toggle("filled", Boolean(speaker));
    slot.box.classList.toggle("lost", Boolean(speaker && s.session.status === "playing" && !speaker.playing));
  }
  const custom = s.speakers.filter((sp) => sp.role == null).map((sp) => sp.name);
  let note = room.querySelector(".room-custom");
  if (!note) { note = el("div", { class: "room-custom" }); room.append(note); }
  note.textContent = custom.length ? `personalizados: ${custom.join(", ")}` : "";
  const scan = $("scan");
  scan.disabled = s.scanning;
  scan.textContent = s.scanning ? "Buscando…" : "Buscar parlantes";
}

const speakerRows = new Map();

function speakerState(s, sp) {
  if (s.session.status === "playing") return sp.playing ? "playing" : "lost";
  if (sp.connected === null) return "unknown";
  return sp.connected ? "connected" : "disconnected";
}

function rangeCell(min, max, step, label, format, onValue, neutral = null) {
  const input = el("input", { type: "range", min, max, step, "aria-label": label });
  const out = el("output", { class: "num" });
  liveRange(input, out, format, onValue);
  if (neutral != null) {
    // Convención de consola: doble clic (o doble toque) vuelve al valor neutro, y una marca lo muestra.
    input.setAttribute("list", "tick-zero");
    input.title = `${label}: doble clic vuelve a ${format(neutral)}`;
    input.addEventListener("dblclick", () => { input.value = String(neutral); out.textContent = format(neutral); onValue(neutral); });
  }
  return { input, out, cell: el("div", { class: "inline" }, input, el("span", { class: "w-14 shrink-0 text-right text-xs tabular-nums" }, out)) };
}

function speakerRow(speaker) {
  const name = speaker.name;
  const current = () => latest && latest.speakers.find((sp) => sp.name === name);
  const role = editable(el("select", { "aria-label": `Rol de ${name}` }));
  // Qué parlante es: la ecualización confía en la banda del fabricante antes que en el micrófono.
  const kind = editable(el("select", { "aria-label": `Tipo de ${name}` }));
  kind.addEventListener("change", () => setSpeaker(kind, name, { kind: kind.value }));
  role.addEventListener("change", () => { if (role.value) sendFrom(role, "assign", { speaker: name, role: role.value }); });
  const pan = rangeCell("-1", "1", "0.05", `Pan de ${name}`, (v) => (v === 0 ? "C" : v < 0 ? `I ${(-v).toFixed(2)}` : `D ${v.toFixed(2)}`),
    (v) => setSpeaker(pan.input, name, { pan: v }), 0);
  const ambience = rangeCell("0", "1", "0.05", `Ambiente de ${name}`, (v) => v.toFixed(2),
    (v) => setSpeaker(ambience.input, name, { ambience: v }));
  const gain = rangeCell("-40", "6", "0.5", `Volumen de ${name}`, (v) => `${v.toFixed(1)} dB`,
    (v) => setSpeaker(gain.input, name, { gain_db: v }), 0);
  const delay = editable(el("input", { type: "number", min: "0", max: "100", step: "0.1", class: "num-input",
    "aria-label": `Retardo de ${name} en ms` }));
  delay.addEventListener("change", () => setSpeaker(delay, name, { delay_ms: Number(delay.value) }));
  const delayNow = el("div", { class: "speaker-sub" });
  const mute = el("button", { type: "button", class: "ghost small-btn" });
  mute.addEventListener("click", () => { const sp = current(); if (sp) send("set", { speaker: name, changes: { muted: !sp.muted } }); });
  const tone = el("button", { type: "button", class: "ghost small-btn", text: "Tono" });
  tone.addEventListener("click", () => send("tone", { speaker: name, seconds: 2 }));
  const remove = el("button", { type: "button", class: "ghost small-btn danger", text: "Quitar" });
  remove.addEventListener("click", () => {
    if (confirm(`¿Quitar ${name} de la instalación? (se guarda con «Guardar instalación»)`)) send("speaker_remove", { speaker: name });
  });
  const row = {
    tr: el("tr"), name: el("div", { class: "speaker-name" }), sub: el("div", { class: "speaker-sub" }),
    status: el("span", { class: "status" }), role, kind, pan, ambience, gain, delay, delayNow, mute, tone, remove,
  };
  row.tr.append(
    el("td", { class: "cell-name" }, row.name, row.sub),
    el("td", { class: "cell-status" }, row.status),
    el("td", { class: "cell-kind" }, kind),
    el("td", { class: "cell-role" }, role),
    el("td", { class: "cell-pan" }, pan.cell),
    el("td", { class: "cell-ambience" }, ambience.cell),
    el("td", { class: "cell-volume" }, gain.cell),
    el("td", { class: "num cell-delay" }, delay, delayNow),
    el("td", { class: "cell-actions" }, el("div", { class: "inline" }, tone, mute, remove)),
  );
  return row;
}

function setStatus(node, table, state, extra = "") {
  const [, label] = table[state] || ["", state];
  node.className = `status status-${state}`;
  // Un punto de color y la palabra: el color nunca va solo (WCAG 1.4.1).
  node.replaceChildren(el("span", { class: "status-dot", "aria-hidden": "true" }), label + extra);
}

function renderSpeakers(s) {
  const body = $("speakers");
  const seen = new Set();
  const playing = s.session.status === "playing";
  const loop = s.recalibration.active;
  for (const sp of s.speakers) {
    seen.add(sp.name);
    let row = speakerRows.get(sp.name);
    if (!row) {
      row = speakerRow(sp);
      speakerRows.set(sp.name, row);
      body.append(row.tr);
    }
    row.name.textContent = sp.name;
    row.sub.textContent = [sp.address, sp.codec, sp.modalias, sp.rssi_dbm != null ? `${sp.rssi_dbm} dBm` : null]
      .filter(Boolean).join(" · ");
    setStatus(row.status, SPEAKER_STATUS, speakerState(s, sp), sp.muted ? " · mudo" : "");
    const roles = s.roles[s.global.layout];
    if (row.role.dataset.layout !== s.global.layout) {
      row.role.replaceChildren(el("option", { value: "", text: "personalizado", disabled: "" }),
        ...roles.map((r) => el("option", { value: r, text: `${r} · ${ROLE_NAMES[r]}` })));
      row.role.dataset.layout = s.global.layout;
      row.role.dataset.synced = "\u0000";
    }
    syncValue(row.role, sp.role || "");
    if (!row.kind.options.length) {
      row.kind.replaceChildren(...s.speaker_kinds.map((k) => el("option", { value: k.key, text: k.label })));
    }
    syncValue(row.kind, sp.kind || sp.kind_guess || "generic");
    for (const [key, cell, format] of [
      ["pan", row.pan, (v) => (v === 0 ? "C" : v < 0 ? `I ${(-v).toFixed(2)}` : `D ${v.toFixed(2)}`)],
      ["ambience", row.ambience, (v) => v.toFixed(2)],
      ["gain_db", row.gain, (v) => `${v.toFixed(1)} dB`],
    ]) {
      syncValue(cell.input, sp[key]);
      cell.out.textContent = format(Number(cell.input.value));
    }
    syncValue(row.delay, sp.delay_ms);
    row.delay.disabled = loop;
    row.delay.title = loop ? "Lo maneja el lazo de recalibración mientras corre" : "";
    // El retardo que suena: el de calibración más el de Haas (ambiente × retardo trasero).
    row.delayNow.textContent = sp.delay_now_ms != null ? `efectivo ${sp.delay_now_ms.toFixed(2)}` : "";
    row.delayNow.title = "Lo que se aplica ahora: este retardo más ambiente × retardo trasero";
    row.mute.textContent = sp.muted ? "Activar" : "Silenciar";
    row.mute.setAttribute("aria-pressed", String(sp.muted));
    row.tone.disabled = !playing;
    row.tone.title = playing ? `Tono de 2 s en ${sp.name}` : "Iniciá la sesión para mandar un tono";
    row.remove.hidden = playing;
  }
  for (const [name, row] of speakerRows) {
    if (!seen.has(name)) { row.tr.remove(); speakerRows.delete(name); }
  }
}

// -- la tarjeta compacta de parlantes: lo que se toca mientras suena ------------------------

const quickRows = new Map();

function quickRow(speaker) {
  const name = speaker.name;
  const current = () => latest && latest.speakers.find((sp) => sp.name === name);
  const title = el("div", { class: "speaker-name truncate" });
  const status = el("span", { class: "status text-xs" });
  const ambience = rangeCell("0", "1", "0.05", `Ambiente de ${name}`, (v) => v.toFixed(2),
    (v) => setSpeaker(ambience.input, name, { ambience: v }));
  ambience.input.classList.add("q-ambience");
  const gain = rangeCell("-40", "6", "0.5", `Volumen de ${name}`, (v) => `${v.toFixed(1)} dB`,
    (v) => setSpeaker(gain.input, name, { gain_db: v }), 0);
  gain.input.classList.add("q-gain");
  const mute = el("button", { type: "button", class: "btn small-btn q-mute" });
  mute.addEventListener("click", () => { const sp = current(); if (sp) send("set", { speaker: name, changes: { muted: !sp.muted } }); });
  const tone = el("button", { type: "button", class: "btn btn-ghost small-btn q-tone", text: "Tono" });
  tone.addEventListener("click", () => send("tone", { speaker: name, seconds: 2 }));
  const box = el("div", { class: "rounded-xl border border-zinc-200 px-3 py-2 dark:border-zinc-800", "data-quick": name },
    el("div", { class: "flex items-center justify-between gap-2" },
      el("div", { class: "flex min-w-0 items-baseline gap-2" }, title, status), el("div", { class: "inline" }, tone, mute)),
    el("div", { class: "grid grid-cols-2 gap-3" },
      el("label", { class: "field" }, el("span", { text: "Ambiente" }), ambience.cell),
      el("label", { class: "field" }, el("span", { text: "Volumen" }), gain.cell)));
  return { box, title, status, ambience, gain, mute, tone };
}

function renderQuick(s) {
  const list = $("quick");
  const seen = new Set();
  const playing = s.session.status === "playing";
  for (const sp of s.speakers) {
    seen.add(sp.name);
    let row = quickRows.get(sp.name);
    if (!row) { row = quickRow(sp); quickRows.set(sp.name, row); list.append(row.box); }
    row.title.textContent = `${sp.name}${sp.role ? ` · ${sp.role}` : ""}`;
    setStatus(row.status, SPEAKER_STATUS, speakerState(s, sp), sp.muted ? " · mudo" : "");
    syncValue(row.ambience.input, sp.ambience);
    row.ambience.out.textContent = Number(row.ambience.input.value).toFixed(2);
    syncValue(row.gain.input, sp.gain_db);
    row.gain.out.textContent = `${Number(row.gain.input.value).toFixed(1)} dB`;
    // Convención de consola: el botón queda "encendido" mientras el parlante está silenciado.
    row.mute.textContent = sp.muted ? "Silenciado" : "Silenciar";
    row.mute.setAttribute("aria-pressed", String(sp.muted));
    row.tone.disabled = !playing;
  }
  for (const [name, row] of quickRows) if (!seen.has(name)) { row.box.remove(); quickRows.delete(name); }
  if (!s.speakers.length && !list.querySelector(".chart-empty")) list.append(el("p", { class: "chart-empty", text: "La instalación no tiene parlantes: agregalos en Sala." }));
}

const DEVICE_GROUPS = [
  ["connected", "Conectados"],
  ["paired", "Emparejados, sin conectar (apagados o lejos)"],
  ["seen", "Encontrados cerca, sin emparejar"],
];

function deviceRow(d, playing) {
  const actions = el("div", { class: "inline" });
  const button = (text, cls, onClick, title = "") => {
    const b = el("button", { type: "button", class: cls, text, title });
    b.addEventListener("click", onClick);
    actions.append(b);
    return b;
  };
  if (d.busy) actions.append(el("span", { class: "muted small", text: "trabajando…" }));
  else if (d.connected) {
    if (!d.in_installation) {
      const add = button("Agregar a la instalación", "small-btn", () => send("speaker_add", { address: d.address }));
      add.disabled = playing;
      add.title = playing ? "Detené la sesión para agregar un parlante" : "";
    }
    button("Desconectar", "ghost small-btn", () => send("disconnect", { address: d.address }));
  } else {
    button(d.paired ? "Conectar" : "Emparejar y conectar", "small-btn", () => send("connect", { address: d.address }));
  }
  if (d.paired && !d.busy && !d.in_installation) {
    button("Olvidar", "ghost small-btn danger", () => {
      if (confirm(`¿Olvidar ${d.name || d.address}? Para volver a usarlo habrá que emparejarlo de nuevo.`)) send("forget", { address: d.address });
    }, "Borra el emparejamiento");
  }
  const state = d.connected ? (d.in_installation ? "conectado · en la instalación" : "conectado") : d.paired ? "sin conectar" : "visto";
  const battery = d.battery_pct == null ? "—" : `${d.battery_pct} %`;
  return el("tr", {},
    el("td", {}, el("div", { class: "speaker-name", text: d.name || d.address }),
      el("div", { class: "speaker-sub", text: d.address })),
    el("td", { text: state }),
    el("td", { class: `num ${d.battery_pct != null && d.battery_pct <= 20 ? "text-rose-600" : ""}`, text: battery }),
    el("td", { class: "num", text: d.rssi_dbm != null ? `${d.rssi_dbm} dBm` : "—" }),
    el("td", {}, actions));
}

function renderDevices(s) {
  $("devices-count").textContent = `(${s.devices.length})`;
  $("scan").disabled = Boolean(s.scanning);
  $("scan").textContent = s.scanning ? "Buscando…" : "Buscar cerca";
  const playing = s.session.status === "playing";
  const group = (d) => (d.connected ? "connected" : d.paired ? "paired" : "seen");
  const rows = [];
  for (const [key, title] of DEVICE_GROUPS) {
    const members = s.devices.filter((d) => group(d) === key);
    if (!members.length) continue;
    rows.push(el("tr", { class: "group-row" }, el("th", { colspan: "5", scope: "colgroup", text: `${title} (${members.length})` })));
    rows.push(...members.map((d) => deviceRow(d, playing)));
  }
  $("devices").replaceChildren(...(rows.length ? rows
    : [el("tr", {}, el("td", { colspan: "5", class: "muted", text: "No hay dispositivos de audio conocidos. Tocá «Buscar cerca»." }))]));
}

// -- servicios -------------------------------------------------------------------

const serviceRows = new Map();

function serviceButton(text, op, name, cls = "ghost small-btn") {
  const button = el("button", { type: "button", class: cls, text });
  button.addEventListener("click", () => send(op, { name }));
  return button;
}

function serviceRow() {
  const name = el("button", { type: "button", class: "link-btn", title: "Ver solo los logs de este servicio" });
  const row = {
    tr: el("tr"), name, kind: el("div", { class: "speaker-sub" }), status: el("span", { class: "status" }),
    pid: el("td", { class: "num cell-pid" }), uptime: el("td", { class: "num cell-uptime" }),
    restarts: el("td", { class: "num cell-restarts" }), detail: el("div", { class: "service-detail" }),
    error: el("div", { class: "service-error" }), actions: el("div", { class: "inline" }), actionsKey: null,
  };
  row.tr.append(
    el("td", { class: "cell-name" }, name, row.kind),
    el("td", { class: "cell-status" }, row.status),
    row.pid, row.uptime, row.restarts,
    el("td", { class: "cell-detail" }, row.detail, row.error),
    el("td", { class: "cell-actions" }, row.actions),
  );
  return row;
}

function logPart(service) { return service.startsWith("player:") ? "session" : service; }

function renderServices(s) {
  const body = $("services");
  const seen = new Set();
  for (const service of s.services) {
    seen.add(service.name);
    let row = serviceRows.get(service.name);
    if (!row) {
      row = serviceRow();
      row.name.addEventListener("click", () => {
        ensureLogService(logPart(service.name));
        $("log-service").value = logPart(service.name);
        renderLogList(true);
        $("logs").scrollIntoView({ behavior: "smooth", block: "nearest" });
      });
      serviceRows.set(service.name, row);
      body.append(row.tr);
    }
    row.name.textContent = service.label;
    row.kind.textContent = `${service.name} · ${KIND_NAMES[service.kind] || service.kind}` +
      (service.depends_on.length ? ` · depende de ${service.depends_on.join(", ")}` : "");
    setStatus(row.status, SERVICE_STATUS, service.state);
    row.pid.textContent = service.pid == null ? "—" : String(service.pid);
    row.uptime.textContent = duration(service.uptime_s);
    row.restarts.textContent = String(service.restarts);
    row.detail.textContent = service.detail;
    row.error.textContent = service.last_error || "";
    row.error.hidden = !service.last_error;
    const key = `${service.managed}|${service.state}`;
    if (row.actionsKey !== key) {
      row.actionsKey = key;
      const buttons = [];
      if (service.managed) {
        if (["stopped", "failed"].includes(service.state)) buttons.push(serviceButton("Iniciar", "service_start", service.name));
        if (["running", "starting"].includes(service.state)) {
          buttons.push(serviceButton("Detener", "service_stop", service.name));
          buttons.push(serviceButton("Reiniciar", "service_restart", service.name));
        }
      }
      row.actions.replaceChildren(...buttons);
    }
  }
  for (const [name, row] of serviceRows) {
    if (!seen.has(name)) { row.tr.remove(); serviceRows.delete(name); }
  }
  // En el orden del servicio: los pw-play junto a la sesión, no al final por haber aparecido después.
  const order = s.services.map((sv) => serviceRows.get(sv.name).tr);
  if (order.some((tr, i) => body.children[i] !== tr)) body.append(...order);
}

// -- salud y latencia -------------------------------------------------------------

function renderHealth(s) {
  const h = s.health;
  const playing = s.session.status === "playing";
  $("t-input").textContent = !playing ? "—" : h.input_active ? "recibe audio" : "en silencio";
  $("t-input-sub").textContent = playing ? "lo que suena en la salida «aurasync»" : "la sesión está detenida";
  $("t-motor").textContent = h.motor_ms == null || !playing ? "—" : `${h.motor_ms.toFixed(2)} ms`;
  $("t-motor-sub").textContent = h.realtime_x ? `por bloque de ${h.budget_ms} ms · ${h.realtime_x}× tiempo real` : `bloque de ${h.budget_ms} ms`;
  $("t-routing").textContent = !playing ? "—" : h.lost.length ? `${h.lost.length} perdido(s)` : "en orden";
  $("t-routing-sub").textContent = `${h.routing_repairs} stream(s) devuelto(s) a su parlante`;
  // Cuánto audio quedaba esperando a los parlantes cuando el motor volvió a escribir.
  const pipe = h.pipe_level_ms;
  $("t-pipe").textContent = pipe == null || !playing ? "—" : `${Math.round(pipe)} ms`;
  $("t-pipe").className = `tile-value ${pipe == null || !playing ? "" : pipe < 20 ? "bad" : pipe < 60 ? "warn" : "good"}`;
  $("t-pipe-sub").textContent = h.bt_discovering
    ? "Bluetooth está buscando dispositivos: puede cortar el audio"
    : "audio esperando en la tubería; bajo 20 ms el parlante se queda sin nada";
  // Calidad: cada xrun es un hueco o un salto en lo que suena. 0/min es lo esperado.
  const xr = Object.entries(h.xruns || {});
  const worst = xr.reduce((m, [, v]) => (v.per_min != null && v.per_min > m ? v.per_min : m), xr.length ? 0 : null);
  const tile = $("t-xruns");
  tile.textContent = !playing ? "—" : worst == null ? "midiendo…" : worst === 0 ? "sin cortes" : `${worst.toFixed(0)} por minuto`;
  tile.classList.toggle("bad", worst != null && worst >= 6);
  tile.classList.toggle("warn", worst != null && worst > 0 && worst < 6);
  // El chip de arriba cuenta los cortes de verdad (cuts.py), no solo los xruns, y lleva a ellos.
  const chip = $("chip-quality");
  const cuts = h.cuts;
  const lastMin = cuts ? cuts.faults_1min : null;
  chip.textContent = !playing || !cuts ? "cortes —" : lastMin === 0 ? (cuts.faults_10min ? `${cuts.faults_10min} cortes en 10 min` : "Sin cortes") : `${lastMin} cortes en 1 min`;
  chip.className = `chip ${!playing || !cuts ? "" : lastMin === 0 && !cuts.faults_10min ? "chip-ok" : lastMin < 3 ? "chip-warn" : "chip-bad"}`;
  $("t-xruns-sub").textContent = xr.length
    ? xr.map(([target, v]) => `${target.replace("bluez_output.", "").replace("aurasync_salida", "salida combinada")}: ${v.total} en total`).join(" · ")
    : "se lee de pw-top cada pocos segundos";
  const r = s.recalibration;
  $("t-recal").textContent = r.active ? "encendida" : "apagada";
  $("t-recal-sub").textContent = r.last ? `${r.last.kind}: ${r.last.reason}` : `micrófono: ${r.microphone || "ninguno"}`;
  const drift = r.drift_ms_h ? Object.entries(r.drift_ms_h).map(([n, v]) => `${n} ${v >= 0 ? "+" : ""}${v.toFixed(1)}`).join(" · ") : "";
  $("t-drift").textContent = drift ? `deriva ms/h (INFERIDA): ${drift}` : "";
  const l = s.latency;
  const rows = [
    ["Entrada (un bloque)", l.input_ms], ["Tubería hacia pw-play (dos bloques)", l.pipe_ms],
    ["Extractor de ambiente", l.extractor_ms], ["Buffer de pw-play", l.player_ms], ["A2DP y el parlante", l.a2dp_ms],
  ];
  $("latency").replaceChildren(
    ...rows.map(([label, ms]) => el("tr", {}, el("td", { text: label }),
      el("td", { class: "num", text: ms == null ? "sin medir" : `${ms.toFixed(1)} ms` }))),
    el("tr", { class: "total" }, el("td", { text: "Conocido" }), el("td", { class: "num", text: `≥ ${l.known_ms.toFixed(1)} ms` })),
    el("tr", { class: "total" }, el("td", { text: "Medida (última calibración, de escrito a oído)" }),
      el("td", { class: "num", text: l.measured_ms == null ? "calibrá para medirla" : `${l.measured_ms.toFixed(0)} ms` })),
  );
}

function renderChart(s) {
  const box = $("drift-chart");
  const history = s.recalibration.history;
  box.replaceChildren();
  const names = s.speakers.map((sp) => sp.name);
  $("drift-legend").replaceChildren(...names.map((n, i) => el("span", { class: "legend-item" },
    el("span", { class: "legend-swatch", style: `background: var(${SERIES[i % SERIES.length]})` }), n)));
  if (history.length < 2) {
    box.append(el("div", { class: "chart-empty", text: "Sin datos: el lazo de recalibración no aplicó nada todavía." }));
    return;
  }
  const width = box.clientWidth || 300;
  const height = box.clientHeight || 96;
  const pad = { left: 34, right: 6, top: 6, bottom: 6 };
  const values = history.flatMap((h) => Object.values(h.delays_ms));
  let lo = Math.min(...values);
  let hi = Math.max(...values);
  if (hi - lo < 1) { lo -= 0.5; hi += 0.5; }
  const t0 = history[0].t;
  const t1 = history.at(-1).t;
  const x = (t) => pad.left + ((t - t0) / Math.max(1e-9, t1 - t0)) * (width - pad.left - pad.right);
  const y = (v) => pad.top + (1 - (v - lo) / (hi - lo)) * (height - pad.top - pad.bottom);
  const chart = svg("svg", { viewBox: `0 0 ${width} ${height}`, role: "img",
    "aria-label": `Retardo por parlante, entre ${lo.toFixed(1)} y ${hi.toFixed(1)} ms` });
  for (const v of [lo, hi]) {
    chart.append(svg("line", { class: "grid-line", x1: pad.left, x2: width - pad.right, y1: y(v), y2: y(v) }));
    const label = svg("text", { class: "axis-label", x: pad.left - 4, y: y(v) + 3, "text-anchor": "end" });
    label.textContent = v.toFixed(1);
    chart.append(label);
  }
  names.forEach((name, i) => {
    const points = history.filter((h) => name in h.delays_ms).map((h) => `${x(h.t)},${y(h.delays_ms[name])}`).join(" ");
    if (points) chart.append(svg("polyline", { class: "line", points, style: `stroke: var(${SERIES[i % SERIES.length]})` }));
  });
  box.append(chart);
}

// -- niveles ----------------------------------------------------------------------

const meterRows = new Map();
const METER_MARKS = [-60, -40, -18, -6, 0];
const PEAK_HOLD_MS = 1500;
const PEAK_FALL_DB_PER_MS = 20 / 1700;
const RMS_TAU_MS = 300;
const meterPct = (v) => Math.max(0, Math.min(100, ((v + 60) / 60) * 100));
const METER_NAMES = { "in L": "Entrada L", "in R": "Entrada R", mic: "Micrófono" };

function buildMeterRows(names) {
  const box = $("meters");
  box.replaceChildren();
  meterRows.clear();
  $("meter-scale").replaceChildren(...METER_MARKS.map((m) => {
    const t = el("span", { text: String(m) });
    t.style.left = `${meterPct(m)}%`;
    return t;
  }));
  for (const name of names) {
    const fill = el("div", { class: "meter-fill" });
    const peak = el("div", { class: "meter-peak" });
    const value = el("div", { class: "meter-value num" });
    const clip = el("button", { type: "button", class: "meter-clip", title: "Saturación: tocalo para apagarlo", "aria-label": `Saturación en ${name}` });
    clip.addEventListener("click", () => clip.classList.remove("on"));
    const limit = el("div", { class: "meter-limit num", title: "Reducción del limitador" });
    const track = el("div", { class: "meter-track", role: "meter", "aria-label": `Nivel ${name}`,
      "aria-valuemin": "-60", "aria-valuemax": "0" }, fill, peak);
    const label = METER_NAMES[name] || name.replace("JBL ", "");
    box.append(el("div", { class: "meter" }, el("div", { class: "meter-label" }, label, limit), track, value, clip));
    meterRows.set(name, { fill, peak, value, track, clip, limit, rms: SILENCE, held: SILENCE, heldAt: 0, at: 0 });
  }
}

// Con stream: valores crudos cada 21 ms; la integración del RMS (300 ms) y la retención del pico
// (1,5 s, después cae 20 dB en 1,7 s) se hacen acá, contra el reloj del navegador.
function drawMeter(row, rmsDb, peakDb, now) {
  const dt = row.at ? now - row.at : 0;
  row.at = now;
  const a = dt > 0 ? Math.exp(-dt / RMS_TAU_MS) : 0;
  const power = (db) => (db <= SILENCE ? 0 : 10 ** (db / 10));
  const mixed = a * power(row.rms) + (1 - a) * power(rmsDb);
  row.rms = mixed > 0 ? 10 * Math.log10(mixed) : SILENCE;
  if (peakDb >= row.held) { row.held = peakDb; row.heldAt = now; }
  else if (now - row.heldAt > PEAK_HOLD_MS) row.held = Math.max(peakDb, row.held - PEAK_FALL_DB_PER_MS * dt);
  paintMeter(row, row.rms, row.held);
}

function paintMeter(row, rmsDb, peakDb) {
  row.fill.style.setProperty("--track-w", `${row.track.clientWidth}px`);
  row.fill.style.width = rmsDb <= SILENCE ? "0%" : `${meterPct(rmsDb)}%`;
  row.peak.hidden = peakDb <= SILENCE;
  row.peak.style.left = `${meterPct(peakDb)}%`;
  if (peakDb >= -0.5) row.clip.classList.add("on");
  row.value.textContent = rmsDb <= SILENCE ? "—" : rmsDb.toFixed(1);
  row.track.setAttribute("aria-valuenow", String(Math.max(-60, Math.round(rmsDb))));
}

function renderMeters(s) {
  const box = $("meters");
  const fresh = live.meters && performance.now() - live.metersAt < 500;
  const values = fresh ? live.meters.meters : s.meters;
  const names = Object.keys(values || {});
  if (names.length === 0) {
    if (!box.querySelector(".chart-empty")) box.replaceChildren(el("div", { class: "chart-empty", text: "Sin sesión: no hay niveles." }));
    $("meter-scale").replaceChildren();
    meterRows.clear();
    return;
  }
  if ([...meterRows.keys()].join("|") !== names.join("|")) buildMeterRows(names);
  if (fresh) return; // el cuadro de animación los dibuja
  for (const [name, row] of meterRows) {
    const m = values[name];
    if (m) paintMeter(row, m.rms_db, m.peak_db);
  }
}

function animateMeters(now) {
  const frame = live.meters;
  if (frame && now - live.metersAt < 500 && meterRows.size) {
    for (const [name, row] of meterRows) {
      const m = frame.meters[name];
      if (m) drawMeter(row, m.rms_db, m.peak_db, now);
      const cut = (frame.limiter_db || {})[name];
      row.limit.textContent = cut > 0.05 ? ` −${cut.toFixed(1)} dB lim` : "";
    }
    $("meters-sync").textContent = frame.synced ? "sincronizado con lo que suena" : "sin latencia medida: adelantado respecto de lo que suena";
  }
  drawInputLive();
  requestAnimationFrame(animateMeters);
}

// -- ahora: la pantalla principal ---

function renderNow(s) {
  const playing = s.session.status === "playing";
  $("now-preset").textContent = s.preset ? `preset: ${s.preset}` : "sin preset";
  const cuts = s.health && s.health.cuts;
  const analysis = s.input && s.input.analysis;
  const rec = s.recalibration;
  const facts = [
    ["Estado", playing ? "sonando" : s.session.status === "error" ? `detenido: ${s.session.reason || "error"}` : "detenido"],
    ["Fuente", s.source ? [SOURCE_NAMES[s.source.kind] || s.source.kind, s.source.name].filter(Boolean).join(" · ") : "—"],
    ["Entrada", analysis ? `${analysis.kind}${analysis.bandwidth_hz ? ` · hasta ${(analysis.bandwidth_hz / 1000).toFixed(1)} kHz` : ""}` : "—"],
    ["Cortes", !cuts ? "—" : cuts.faults_1min ? `${cuts.faults_1min} en el último minuto` : cuts.faults_10min ? `${cuts.faults_10min} en 10 min` : "ninguno"],
    ["Sincronía", !playing ? "—" : rec.active ? (rec.last ? `${rec.last.kind}: ${rec.last.reason || ""}`.slice(0, 80) : "midiendo") : "sin seguir la deriva"],
  ];
  $("now-facts").replaceChildren(...facts.flatMap(([k, v]) => {
    const dd = el("dd", { text: v });
    if (k === "Cortes" && cuts && cuts.faults_10min) {
      dd.replaceChildren(el("button", { type: "button", class: "link", text: v }));
      dd.firstChild.addEventListener("click", () => window.aurasyncShow("[data-card=cuts]"));
    }
    return [el("dt", { text: k }), dd];
  }));
  $("now-calibrate").disabled = !playing || nowBusy;
  $("now-identify").disabled = !playing || nowBusy;
}

let nowBusy = false;
const SOURCE_NAMES = { system: "todo el sistema", app: "una aplicación", file: "un archivo", tone: "señal de prueba" };

async function waitFor(predicate, timeoutMs) {
  const end = Date.now() + timeoutMs;
  while (Date.now() < end) {
    if (latest && predicate(latest)) return latest;
    await new Promise((r) => setTimeout(r, 250));
  }
  return null;
}

// Calibrar y aplicar en un paso: lo que antes eran tres botones en otra pestaña.
async function calibrateAndApply() {
  nowBusy = true;
  const status = $("now-action");
  try {
    status.textContent = "Calibrando 10 s a volumen bajo…";
    const reply = await send("calibrate", { seconds: 10, amplitude: 0.1 });
    if (!reply || !reply.ok) { status.textContent = "No se pudo calibrar."; return; }
    const s = await waitFor((st) => st.calibration && ["done", "error", "cancelled"].includes(st.calibration.state), 40000);
    if (!s || s.calibration.state !== "done") { status.textContent = "La calibración no terminó bien: mirá la pestaña Calibrar."; return; }
    const good = s.calibration.results.filter((r) => !r.silent && !r.doubtful);
    if (!good.length) { status.textContent = "Ningún parlante se midió con confianza: no se aplicó nada."; return; }
    const applied = await send("calibration_apply");
    const skipped = applied && applied.ok ? applied.result.skipped || [] : [];
    status.textContent = applied && applied.ok
      ? `Alineación aplicada${skipped.length ? `; quedaron como estaban: ${skipped.join(", ")}` : ""}.`
      : "No se pudo aplicar.";
  } finally {
    nowBusy = false;
  }
}

async function identifySpeakers() {
  nowBusy = true;
  try {
    for (const sp of latest.speakers) {
      $("now-action").textContent = `Suena: ${sp.name}`;
      await send("tone", { speaker: sp.name, seconds: 1.2 });
      await new Promise((r) => setTimeout(r, 1500));
    }
    $("now-action").textContent = "";
  } finally {
    nowBusy = false;
  }
}

// -- cortes ---

const CUT_CLASS = { underrun: "cut-fault", low: "cut-fault", late: "cut-fault", lost: "cut-fault", routing: "cut-fault",
  xrun: "cut-xrun", input_gap: "cut-input", fade: "cut-fade" };
const CUT_CONTEXT = {
  loop_measuring: () => "el lazo medía",
  bt_discovering: () => "Bluetooth buscaba dispositivos",
  slow_order: (v) => `orden lenta: ${v}`,
  last_order: (v) => `después de «${v}»`,
};
const CUT_WINDOW_S = 600;

function renderCuts(s) {
  const c = s.health && s.health.cuts;
  const list = $("cuts-list");
  const line = $("cuts-timeline");
  if (!c) {
    $("cuts-count").textContent = "sin sesión";
    $("cuts-likely").hidden = true;
    line.replaceChildren();
    list.replaceChildren(el("tr", {}, el("td", { colspan: "5", class: "muted", text: "Se registran mientras suena una sesión." })));
    return;
  }
  $("cuts-count").textContent = c.faults_10min
    ? `${c.faults_10min} en 10 min · ${c.faults_1min} en el último minuto` : "ninguno en 10 min";
  $("cuts-count").classList.toggle("ok", c.faults_10min === 0);
  $("cuts-likely").hidden = !c.likely;
  $("cuts-likely").textContent = c.likely || "";
  line.replaceChildren(...c.events.map((e) => {
    const age = c.now - e.t;
    const dot = el("span", { class: `cut-dot ${CUT_CLASS[e.kind] || "cut-fault"}`, title: `${e.at.slice(11, 19)} · ${e.what}${e.where ? ` · ${e.where}` : ""}` });
    dot.style.left = `${Math.max(0, Math.min(100, (1 - age / CUT_WINDOW_S) * 100))}%`;
    return dot;
  }));
  const rows = [...c.events].reverse().slice(0, 15);
  list.replaceChildren(...(rows.length ? rows.map((e) => el("tr", { class: e.fault ? "" : "muted" },
    el("td", { class: "num", text: e.at.slice(11, 19) }),
    el("td", { text: e.where || "—" }),
    el("td", { text: e.what }),
    el("td", { text: e.detail || "" }),
    el("td", { text: Object.entries(e.context || {}).map(([k, v]) => (CUT_CONTEXT[k] ? CUT_CONTEXT[k](v) : `${k}: ${v}`)).join(" · ") }),
  )) : [el("tr", {}, el("td", { colspan: "5", class: "muted", text: "Sin cortes en los últimos 10 minutos." }))]));
}

// -- entrada ---

const INPUT_NOTES = {
  mono: "La entrada es mono: los parlantes reciben lo mismo y el envolvente no tiene de dónde salir.",
  "fuera de fase": "Gran parte de la entrada está en contrafase entre canales: suele ser un cable o un ajuste invertido.",
  "un solo canal": "Casi todo llega por un solo canal: revisá el balance de la aplicación o del sistema.",
  "estéreo": "",
};

function renderInput(s) {
  const a = s.input && s.input.analysis;
  const facts = $("input-facts");
  const apps = (s.input && s.input.apps) || [];
  if (!a) {
    facts.replaceChildren(el("div", { class: "chart-empty", text: "Sin datos: inicia la sesión y pon música." }));
    $("input-note").textContent = "";
    $("input-stale").hidden = true;
    return;
  }
  const band = a.bandwidth_hz == null ? "—" : `${(a.bandwidth_hz / 1000).toFixed(1)} kHz`;
  const rows = [
    ["Tipo", a.kind],
    ["Correlación L/R", `${a.correlation.toFixed(2)} (1 = mono)`],
    ["Lateral / central", `${a.side_db.toFixed(1)} dB`],
    ["Balance L − R", `${a.balance_db.toFixed(1)} dB`],
    ["Ancho de banda", band],
    ["Nivel", `RMS ${a.rms_db.toFixed(1)} · pico ${a.peak_db.toFixed(1)} dBFS`],
    ["Saturación", a.clipped ? `${a.clipped} muestras en los últimos segundos` : "ninguna"],
    ...apps.map((app) => [`Aplicación: ${app.name}`, app.sample_spec || "—"]),
  ];
  facts.replaceChildren(...rows.flatMap(([k, v]) => [el("dt", { text: k }), el("dd", { class: "num", text: v })]));
  const notes = [INPUT_NOTES[a.kind] || ""];
  if (a.bandwidth_hz != null && a.bandwidth_hz < 17000) {
    notes.push(`La entrada llega hasta ${band}: la fuente ya viene recortada en agudos (un archivo comprimido o calidad de streaming baja).`);
  }
  if (a.clipped) notes.push("La entrada satura: baja el volumen de la aplicación, no el de aurasync.");
  $("input-note").textContent = notes.filter(Boolean).join(" ");
  $("input-stale").hidden = !a.stale;
}

function drawInputLive() {
  const frame = live.input;
  const bars = $("input-bars");
  if (!frame || !bars) return;
  if (bars.childElementCount !== frame.bands_db.length) {
    bars.replaceChildren(...frame.bands_db.map(() => el("div", { class: "input-bar" })));
  }
  frame.bands_db.forEach((db, i) => {
    bars.children[i].style.height = `${Math.max(0, Math.min(100, ((db + 80) / 80) * 100))}%`;
  });
  const c = frame.correlation;
  const marker = $("corr-marker");
  marker.hidden = c == null;
  if (c != null) marker.style.left = `${((c + 1) / 2) * 100}%`;
  $("corr-value").textContent = c == null ? "—" : c.toFixed(2);
}

function renderMicrophones(s) {
  const select = $("cal-mic");
  const current = s.recalibration.microphone;
  const mics = s.microphones || [];
  const key = JSON.stringify([mics, current]);
  if (select.dataset.key === key || isEditing(select)) return;
  select.dataset.key = key;
  const known = mics.some((m) => m.node === current);
  select.replaceChildren(
    el("option", { value: "", text: "ninguno" }),
    ...mics.map((m) => el("option", { value: m.node, text: m.description })),
    ...(current && !known ? [el("option", { value: current, text: `${current} (no está conectado)` })] : []),
  );
  select.value = current || "";
}

// -- configuración ------------------------------------------------------------------

function renderConfig(s) {
  const values = { ...s.global, ...s.config };
  for (const node of document.querySelectorAll("[data-global]")) syncValue(node, values[node.dataset.global]);
  const out = document.querySelector('[data-out="rear_delay_ms"]');
  out.textContent = `${Number(document.querySelector('[data-global="rear_delay_ms"]').value).toFixed(1)} ms`;
  for (const node of document.querySelectorAll("[data-pending]")) {
    const pending = s.config.pending_restart.includes(node.dataset.pending);
    node.textContent = pending ? "pendiente: reiniciá la sesión" : "al reiniciar";
    node.classList.toggle("pending", pending);
  }
}

function setupConfig() {
  for (const node of document.querySelectorAll("[data-global]")) {
    editable(node);
    const key = node.dataset.global;
    node.addEventListener("change", () => {
      let value = node.type === "checkbox" ? node.checked : node.value;
      if (node.type === "range" || node.dataset.number) value = Number(value);
      if (typeof value === "string") value = value.trim();
      setGlobal(node, { [key]: value });
    });
  }
  const rear = document.querySelector('[data-global="rear_delay_ms"]');
  rear.addEventListener("input", () => {
    document.querySelector('[data-out="rear_delay_ms"]').textContent = `${Number(rear.value).toFixed(1)} ms`;
  });
}

// -- calibración --------------------------------------------------------------------

function renderCalibration(s) {
  const cal = s.calibration;
  renderResponse(cal, s.speakers);
  const playing = s.session.status === "playing";
  const busy = cal && ["running", "measuring"].includes(cal.state);
  $("cal-run").textContent = busy ? "Cancelar" : "Calibrar";
  // El lazo es parte del protocolo: calibrar lo pausa y aplicar lo reinicia, en el servicio.
  $("cal-run").disabled = !busy && !playing;
  $("cal-run").title = playing ? "" : "Iniciá la sesión para calibrar";
  $("cal-apply").disabled = !(cal && cal.state === "done" && !cal.stale && playing);
  $("eq-apply").disabled = !(cal && cal.state === "done" && !cal.stale && playing);
  $("eq-reset").disabled = !s.speakers.some((sp) => sp.eq_db);
  $("cal-progress").hidden = !(cal && cal.state === "running");
  $("cal-bar").style.width = `${Math.round((cal ? cal.progress : 0) * 100)}%`;
  $("cal-save").disabled = !(cal && cal.state === "done") || s.service.simulated;
  let note = "Emite ruido por los parlantes dentro de la sesión abierta y lo mide con el micrófono. Amplitud 0,1 por defecto.";
  if (cal) {
    if (cal.state === "running") note = `Emitiendo… ${Math.round(cal.progress * 100)} %`;
    else if (cal.state === "measuring") note = "Midiendo la grabación…";
    else if (cal.state === "error") note = `Falló: ${cal.error}`;
    else if (cal.state === "cancelled") note = "Cancelada.";
    else if (cal.state === "done") {
      note = `${s.service.simulated ? "SIMULADO · " : ""}Medido ${cal.measured_at}. ${cal.reliable ? "Confiable." : "Dudosa: conviene repetirla con menos ruido."}`;
      if (cal.stale) note += " (de una sesión anterior: no se puede aplicar)";
    }
  }
  $("cal-note").textContent = note;
  const results = cal ? cal.results : [];
  $("cal-table").hidden = results.length === 0;
  $("cal-results").replaceChildren(...results.map((r) => el("tr", {},
    el("td", { text: r.speaker }),
    el("td", { class: "num", text: fmt(r.delay_ms, 2) }),
    el("td", { class: "num", text: fmt(r.gain_db, 1) }),
    el("td", { class: "num", text: fmt(r.stability_ms, 2) }),
    el("td", { class: "num", text: r.band_hz && r.band_hz[0] != null ? `${hz(r.band_hz[0])}–${hz(r.band_hz[1])}` : "—" }),
    el("td", { class: "small", text: [r.silent ? "no suena" : "", r.doubtful ? "dudoso" : ""].filter(Boolean).join(" · ") }),
  )));
}

const hz = (f) => (f >= 1000 ? `${(f / 1000).toFixed(f >= 10000 ? 0 : 1)} kHz` : `${Math.round(f)} Hz`);

// La respuesta de cada parlante: escala logarítmica en frecuencia, de −40 a +15 dB.
function renderResponse(cal, speakers) {
  const box = $("resp-chart");
  const results = (cal && cal.results) || [];
  $("resp-box").hidden = !(results.length && cal.response_hz);
  $("resp-empty").hidden = !$("resp-box").hidden;
  if ($("resp-box").hidden) return;
  const key = JSON.stringify([results.map((r) => r.response_db), speakers.map((sp) => sp.eq_db)]);
  if (box.dataset.key === key && box.clientWidth === Number(box.dataset.w)) return;
  box.dataset.key = key;
  box.dataset.w = String(box.clientWidth);
  box.replaceChildren();
  const width = box.clientWidth || 300;
  const height = box.clientHeight || 140;
  const pad = { left: 34, right: 6, top: 6, bottom: 18 };
  const fs = cal.response_hz;
  // Eje de frecuencia logarítmico de 20 Hz a 20 kHz, con las marcas de siempre (REW, Dirac).
  const x = (f) => pad.left + (Math.log10(f / 20) / Math.log10(1000)) * (width - pad.left - pad.right);
  const lo = -40, hi = 15;
  const y = (v) => pad.top + (1 - (Math.max(lo, Math.min(hi, v)) - lo) / (hi - lo)) * (height - pad.top - pad.bottom);
  const chart = svg("svg", { viewBox: `0 0 ${width} ${height}`, role: "img", "aria-label": "Respuesta en frecuencia por parlante" });
  for (const v of [-30, -20, -10, 0, 10]) {
    chart.append(svg("line", { class: "grid-line", x1: pad.left, x2: width - pad.right, y1: y(v), y2: y(v) }));
    const t = svg("text", { class: "axis-label", x: pad.left - 4, y: y(v) + 3, "text-anchor": "end" });
    t.textContent = String(v);
    chart.append(t);
  }
  chart.append(svg("line", { class: "unity", x1: pad.left, x2: width - pad.right, y1: y(0), y2: y(0) }));
  for (const f of [20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000]) {
    const t = svg("text", { class: "axis-label", x: x(f), y: height - 4, "text-anchor": "middle" });
    t.textContent = hz(f);
    chart.append(t);
  }
  const names = speakers.map((sp) => sp.name);
  results.forEach((r) => {
    const i = Math.max(0, names.indexOf(r.speaker));
    const pts = r.response_db.map((v, k) => (v == null ? null : `${x(fs[k])},${y(v)}`)).filter(Boolean).join(" ");
    chart.append(svg("polyline", { class: "line measured", points: pts, style: `stroke: var(${SERIES[i % SERIES.length]})` }));
  });
  // La ecualización vigente de cada parlante, punteada.
  speakers.forEach((sp, i) => {
    if (!sp.eq_db) return;
    const pts = sp.eq_db.map((v, k) => `${x(fs[k])},${y(v)}`).join(" ");
    chart.append(svg("polyline", { class: "line eq-line", points: pts, style: `stroke: var(${SERIES[i % SERIES.length]})` }));
  });
  box.append(chart);
  $("resp-legend").replaceChildren(...results.map((r) => {
    const i = Math.max(0, names.indexOf(r.speaker));
    return el("span", { class: "legend-item" }, el("span", { class: "legend-swatch", style: `background: var(${SERIES[i % SERIES.length]})` }), r.speaker);
  }), el("span", { class: "legend-item muted", text: "línea llena: medida · punteada: ecualización · gris: 0 dB" }));
}

// -- presets y A/B -------------------------------------------------------------------

function renderPresets(s) {
  const list = $("presets");
  const key = `${s.presets.join("|")}#${s.preset}#${s.ab && s.ab.active}`;
  if (list.dataset.key !== key) {
    list.dataset.key = key;
    list.replaceChildren(...s.presets.map((name) => {
      const load = el("button", { type: "button", class: "small-btn", text: "Cargar" });
      load.addEventListener("click", () => send("preset_load", { name }));
      load.disabled = Boolean(s.ab && s.ab.active);
      const del = el("button", { type: "button", class: "ghost small-btn danger", text: "Borrar" });
      del.addEventListener("click", () => { if (confirm(`¿Borrar el preset ${name}?`)) send("preset_delete", { name }); });
      return el("li", { class: name === s.preset ? "current" : "" },
        el("span", { class: "preset-name", text: name + (name === s.preset ? " · actual" : "") }), load, del);
    }));
    if (s.presets.length === 0) list.append(el("li", { class: "muted small", text: "Sin presets guardados." }));
    for (const id of ["ab-a", "ab-b"]) {
      const select = $(id);
      const value = select.value;
      select.replaceChildren(...s.presets.map((n) => el("option", { value: n, text: n })));
      if (s.presets.includes(value)) select.value = value;
      else if (id === "ab-b" && s.presets.length > 1) select.value = s.presets[1];
    }
  }
  const ab = s.ab;
  const active = Boolean(ab && ab.active);
  $("ab-live").hidden = !active;
  $("ab-start").disabled = active || s.presets.length < 2 || s.session.status !== "playing";
  if (active) {
    for (const button of document.querySelectorAll("[data-ab]")) {
      button.setAttribute("aria-pressed", String(button.dataset.ab === ab.playing));
    }
    const last = ab.last ? ` · la última: ${ab.last.correct ? "acertaste" : "no"} (X era ${ab.last.truth.toUpperCase()})` : "";
    $("ab-score").textContent = `${ab.a} contra ${ab.b}: ${ab.correct} de ${ab.trials} aciertos${last}`;
  }
  $("ab-result").hidden = !(ab && !ab.active);
  if (ab && !ab.active) {
    const n = ab.trials.length;
    const p = n ? binomialTail(ab.correct, n) : 1;
    $("ab-result").textContent = `Último A/B: ${ab.a} contra ${ab.b}, ${ab.correct} de ${n} aciertos. ` +
      (n === 0 ? "" : p < 0.05 ? `Significativo (p = ${p.toFixed(3)}): la diferencia se oye.`
        : `No alcanza (p = ${p.toFixed(2)}): con 20 intentos hacen falta 15 aciertos.`);
  }
}

function ingestLogs({ records, gap }) {
  if (gap) toast("Se perdieron líneas de log: el panel se atrasó.");
  if (!records.length) return;
  logSince = records.at(-1).seq;
  for (const record of records) ensureLogService(record.service);
  logRecords.push(...records);
  if (logRecords.length > LOG_CAPACITY) logRecords = logRecords.slice(-LOG_CAPACITY);
  if (!logPaused) renderLogList(false, records);
}

// La probabilidad de acertar k o más de n adivinando (ABX: 15 de 20 da p < 0,05).
function binomialTail(k, n) {
  let total = 0;
  let c = 1;
  for (let i = 0; i <= n; i++) {
    if (i >= k) total += c;
    c = (c * (n - i)) / (i + 1);
  }
  return total / 2 ** n;
}

// -- logs ------------------------------------------------------------------------------

let logRecords = [];
let logPaused = false;
let logClearedAt = 0;
let logSince = 0;
const logServices = new Set();

function ensureLogService(name) {
  if (logServices.has(name)) return;
  logServices.add(name);
  $("log-service").append(el("option", { value: name, text: name }));
}

function logMatches(record) {
  const service = $("log-service").value;
  const min = LEVELS[$("log-level").value] || 0;
  const search = $("log-search").value.trim().toLowerCase();
  return record.seq > logClearedAt
    && (!service || record.service === service)
    && (LEVELS[record.level] || 0) >= min
    && (!search || record.message.toLowerCase().includes(search) || record.service.includes(search));
}

function logLine(record) {
  const marks = { warning: "!", error: "×", critical: "×" };
  return el("li", { class: `log log-${record.level}` },
    el("time", { text: record.at.slice(11, 23) }),
    el("span", { class: "log-level", text: `${marks[record.level] || ""} ${record.level}`.trim() }),
    el("span", { class: "log-service", text: record.service }),
    el("span", { class: "log-message", text: record.message }));
}

function renderLogList(full, fresh = []) {
  const list = $("logs");
  const atBottom = list.scrollHeight - list.scrollTop - list.clientHeight < 24;
  if (full) {
    list.replaceChildren(...logRecords.filter(logMatches).slice(-LOG_CAPACITY).map(logLine));
  } else {
    for (const record of fresh) if (logMatches(record)) list.append(logLine(record));
    while (list.childElementCount > LOG_CAPACITY) list.firstElementChild.remove();
  }
  $("log-count").textContent = `${list.childElementCount} de ${logRecords.length} líneas`;
  if (!logPaused && (atBottom || full)) list.scrollTop = list.scrollHeight;
}

async function pollLogs() {
  if (live.alive) { setTimeout(pollLogs, LOGS_EVERY_MS); return; }
  try {
    const response = await fetch("/v1/command", {
      method: "POST", headers: { "Content-Type": "application/json" }, credentials: "same-origin",
      body: JSON.stringify({ v: 1, op: "logs", since: logSince, limit: 500 }),
    });
    const reply = await response.json();
    if (reply.ok) ingestLogs(reply.result);
  } catch {
    // El estado ya muestra la desconexión.
  } finally {
    setTimeout(pollLogs, LOGS_EVERY_MS);
  }
}

function downloadLogs() {
  const text = logRecords.filter(logMatches)
    .map((r) => `${r.at} ${r.level.toUpperCase().padEnd(7)} ${r.service.padEnd(13)} ${r.message}`).join("\n");
  const link = el("a", { download: `aurasync-${new Date().toISOString().slice(0, 19).replace(/:/g, "")}.log` });
  link.href = URL.createObjectURL(new Blob([text + "\n"], { type: "text/plain" }));
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(link.href), 1000);
}

function setupLogs() {
  for (const id of ["log-service", "log-level"]) $(id).addEventListener("change", () => renderLogList(true));
  $("log-search").addEventListener("input", () => renderLogList(true));
  $("log-pause").addEventListener("click", () => {
    logPaused = !logPaused;
    $("log-pause").textContent = logPaused ? "Reanudar" : "Pausar";
    $("log-pause").setAttribute("aria-pressed", String(logPaused));
    if (!logPaused) renderLogList(true);
  });
  $("log-clear").addEventListener("click", () => {
    logClearedAt = logRecords.length ? logRecords.at(-1).seq : 0;
    renderLogList(true);
  });
  $("log-download").addEventListener("click", downloadLogs);
}

// -- arranque ---------------------------------------------------------------------------

function setup() {
  buildLayout();
  $("run").addEventListener("click", () => {
    if (!latest) return;
    if (latest.session.status === "playing") send("stop");
    else send("start");
  });
  // `data-room-layout`, no `data-layout`: el <body> lleva `data-layout` con la organización
  // del panel, y el selector lo atrapaba; cada clic mandaba `layout: "pestanas"` (2026-10-01).
  for (const button of document.querySelectorAll("[data-room-layout]")) {
    button.addEventListener("click", () => {
      if (latest && latest.global.layout !== button.dataset.roomLayout) send("set", { changes: { layout: button.dataset.roomLayout } });
    });
  }
  const kind = editable($("source-kind"));
  const app = editable($("source-app"));
  const file = editable($("source-file"));
  kind.addEventListener("change", () => {
    syncSourceFields();
    if (kind.value === "app") app.focus();
    else if (kind.value === "file") file.focus();
    else sendFrom(kind, "source", { kind: kind.value });
  });
  app.addEventListener("change", () => { if (app.value) sendFrom(app, "source", { kind: "app", name: app.value }); });
  file.addEventListener("change", () => { const v = file.value.trim(); if (v) sendFrom(file, "source", { kind: "file", name: v }); });
  const volume = $("volume");
  liveRange(volume, $("volume-out"), (v) => `${v.toFixed(0)} dB`, (v) => setGlobal(volume, { volume_db: v }));
  $("scan").addEventListener("click", () => send("scan"));
  $("save").addEventListener("click", async () => {
    const reply = await send("save");
    if (reply && reply.ok) toast(`Instalación guardada en ${reply.result.path}`);
  });
  editable($("cal-mic"));
  $("cal-mic").addEventListener("change", () => sendFrom($("cal-mic"), "microphone_set", { node: $("cal-mic").value || null }));
  $("cal-run").addEventListener("click", () => {
    const cal = latest && latest.calibration;
    if (cal && ["running", "measuring"].includes(cal.state)) send("calibrate_cancel");
    else send("calibrate", { seconds: Number($("cal-seconds").value), amplitude: Number($("cal-amplitude").value) });
  });
  $("cal-apply").addEventListener("click", () => send("calibration_apply"));
  $("eq-apply").addEventListener("click", async () => {
    const reply = await send("eq_apply");
    if (reply && reply.ok) toast(reply.result.skipped.length ? `Ecualizado; sin cambios: ${reply.result.skipped.join(", ")}` : "Ecualizado. Calibrá otra vez para ver cuánto se aplanó.");
  });
  $("eq-reset").addEventListener("click", () => send("eq_reset"));
  $("cal-save").addEventListener("click", async () => {
    const reply = await send("measurement_save", { note: $("cal-note-input").value.trim() });
    if (reply && reply.ok) toast(`Medición guardada en ${reply.result.path}`);
  });
  $("preset-save").addEventListener("click", async () => {
    const name = $("preset-name").value.trim();
    if (!name) { toast("Escribí un nombre para el preset."); return; }
    const reply = await send("preset_save", { name });
    if (reply && reply.ok) $("preset-name").value = "";
  });
  $("ab-start").addEventListener("click", () => send("ab_start", { a: $("ab-a").value, b: $("ab-b").value }));
  for (const button of document.querySelectorAll("[data-ab]")) {
    button.addEventListener("click", () => send("ab_play", { which: button.dataset.ab }));
  }
  for (const button of document.querySelectorAll("[data-answer]")) {
    button.addEventListener("click", () => send("ab_answer", { x_is: button.dataset.answer }));
  }
  $("ab-stop").addEventListener("click", () => send("ab_stop"));
  $("pair-open").addEventListener("click", () => {
    $("pair-qr").src = `/pairing.svg?${Date.now()}`;
    $("pair-dialog").showModal();
  });
  setupConfig();
  setupLogs();
  $("now-calibrate").addEventListener("click", calibrateAndApply);
  $("chip-quality").addEventListener("click", () => window.aurasyncShow("[data-card=cuts]"));
  $("now-identify").addEventListener("click", identifySpeakers);
  pollState();
  pollLogs();
  openStream();
  requestAnimationFrame(animateMeters);
}

document.addEventListener("DOMContentLoaded", setup);
