// Panel de aurasync sobre el servicio de control (spec del servicio §15). Sin build ni
// dependencias. Habla solo el contrato de control.py: lee GET /v1/state y manda cada orden
// como el mensaje crudo por POST /v1/command, igual que viajaría por serie.
// Todo pasa por un solo transporte, `window.aurasync.api` (host/web/src/transport.ts, en
// cadena.js): con la cookie en el panel que sirve el equipo, y con el token del cliente
// (`Authorization: Bearer`, el stream por ticket) en la PWA de GitHub Pages (d-7c8794-37f9bc).
// Los textos que vienen del servicio o del log se insertan con textContent, nunca como HTML.
"use strict";

const $ = (id) => document.getElementById(id);

const ROLE_POSITIONS = {
  FL: [22, 26], FR: [78, 26], RL: [22, 80], RR: [78, 80], FC: [50, 20], RC: [50, 82],
};
const ROLE_NAMES = {
  FL: "frontal izquierdo", FR: "frontal derecho", FC: "centro",
  RL: "trasero izquierdo", RR: "trasero derecho", RC: "trasero (surround)",
  SL: "lateral izquierdo", SR: "lateral derecho", WL: "ancho izquierdo", WR: "ancho derecho",
  OF: "exterior, al frente", OL: "exterior, a la izquierda", OR: "exterior, a la derecha", OB: "exterior, atrás",
};
const roleName = (r) => ROLE_NAMES[r] || r;
// Las distribuciones que el servicio ofrece (`roles`), con su nombre; una desconocida va con su clave.
const LAYOUT_NAMES = {
  quad: ["Cuadrafonía", "FL FR RL RR"], lcrs: ["Películas", "L C R S"], "5.0": ["5.0", "L C R SL SR"],
  hex: ["Hexágono", "6"], "7.0": ["7.0", "L C R SL SR RL RR"], octagon: ["Octógono", "8"], rings: ["Dos anillos", "4 + 4"],
  auto: ["Automático", "N parlantes"],
};
const SERVICE_STATUS = {
  running: ["", "corriendo"], starting: ["", "iniciando"], stopping: ["", "deteniendo"],
  stopped: ["", "detenido"], failed: ["", "falló"], unavailable: ["", "no disponible"],
};
const SPEAKER_STATUS = {
  playing: ["", "sonando"], connected: ["", "conectado"], lost: ["", "perdido"],
  disconnected: ["", "desconectado"], unknown: ["", "sin observar"],
  // `output` del servicio (parlantes virtuales, fase 1).
  virtual: ["", "virtual"], absent: ["", "sin conectar"],
};
const KIND_NAMES = { proceso: "proceso", tarea: "tarea", sistema: "sistema (solo se observa)" };
const LEVELS = { debug: 10, info: 20, warning: 30, error: 40, critical: 50 };
const SILENCE = -120;
const LOG_CAPACITY = 2000;
const STATE_EVERY_MS = 500;
const LOGS_EVERY_MS = 1000;
const SERIES = ["--s1", "--s2", "--s3", "--s4", "--s5", "--s6", "--s7", "--s8"];
// Desde cuántos parlantes las tarjetas por parlante van plegadas y aparecen las acciones por grupo
// (experimentos/16 §7): con 3 se ven enteras, como siempre.
const FOLD_FROM = 4;

let latest = null;
let online = false;
let renderQueued = false;
// El transporte (transport.ts): null hasta que hay un equipo elegido. `stopped`: el equipo ya no
// reconoce la credencial (401) y no tiene sentido seguir preguntando.
let api = null;
let stopped = false;

// Lo que reciben las pantallas en Preact (cadena.js, compilado de host/web): app.js tiene el único
// EventSource y la consulta de respaldo, y les pasa cada estado y cada evento del stream como un
// evento del documento. Ellas mandan sus avisos por `aurasync:toast` (d-7c8794-6da524).
function share(name, detail) {
  document.dispatchEvent(new CustomEvent(`aurasync:${name}`, { detail }));
}

// -- organizaciones -------------------------------------------------------------
// Las mismas tarjetas, repartidas de cuatro maneras. Se elige con ?layout= (y queda
// recordada); docs/research/10-panel-de-control.md mide las cuatro con las tareas típicas.

const CARDS_ALL = ["now", "presets", "quick", "monitor", "ab", "chain", "room", "speakers", "devices", "spatial", "calibration", "response", "estimator",
  "health", "cuts", "levels", "input", "services", "logs", "config"];
const WIDE = new Set(["chain", "speakers", "devices", "services", "logs", "config", "calibration", "estimator", "spatial"]);
const LAYOUTS = {
  pagina: { nav: "none", views: [{ id: "todo", label: "Todo", icon: "list", cards: CARDS_ALL }] },
  pestanas: { nav: "tabs", views: [
    // La pantalla principal: lo de todos los días arriba (estado, efectos, presets, parlantes,
    // niveles); comparar y analizar la entrada, más abajo (research/10 §3, escenarios).
    { id: "escuchar", label: "Escuchar", icon: "play", cards: ["now", "presets", "quick", "levels", "ab", "input", "monitor"] },
    // La cadena entera (spec 2026-10-02 §7.1): cada etapa, sus algoritmos y sus perillas. Es lo que
    // antes era Ajustes → Sonido, y mucho más; la dibuja cadena.js (Preact, host/web).
    { id: "cadena", label: "Cadena", icon: "chain", cards: ["chain"] },
    // Primero conectar, después ajustar cada parlante, al final la sala.
    { id: "parlantes", label: "Parlantes", icon: "speaker", cards: ["devices", "speakers", "room", "spatial"] },
    { id: "calibrar", label: "Calibrar", icon: "target", cards: ["calibration", "response", "estimator"] },
    { id: "diagnostico", label: "Diagnóstico", icon: "activity", cards: ["health", "cuts", "services", "logs"],
      columns: { left: ["health", "cuts"], right: ["services"], bottom: ["logs"] } },
    { id: "ajustes", label: "Ajustes", icon: "sliders", cards: ["config"] },
  ] },
  inicio: { nav: "hub", views: [
    { id: "inicio", label: "Inicio", icon: "home", cards: ["now", "presets", "quick", "hub", "monitor"] },
    { id: "cadena", label: "Cadena", icon: "chain", cards: ["chain"] },
    { id: "parlantes", label: "Sala y parlantes", icon: "speaker", cards: ["devices", "speakers", "room", "spatial"] },
    { id: "calibrar", label: "Calibrar", icon: "target", cards: ["calibration", "response", "estimator"] },
    { id: "comparar", label: "A/B ciego", icon: "compare", cards: ["ab", "levels"] },
    { id: "diagnostico", label: "Diagnóstico", icon: "activity", cards: ["health", "cuts", "input", "services", "logs"],
      columns: { left: ["health", "cuts", "input"], right: ["services"], bottom: ["logs"] } },
    { id: "ajustes", label: "Ajustes", icon: "sliders", cards: ["config"] },
  ] },
  lateral: { nav: "sidebar", views: [
    { id: "sonido", label: "Sonido", icon: "play", cards: ["now", "presets", "quick", "levels", "ab", "input", "monitor"] },
    { id: "cadena", label: "Cadena", icon: "chain", cards: ["chain"] },
    { id: "sala", label: "Sala", icon: "speaker", cards: ["devices", "speakers", "room", "spatial"] },
    { id: "medir", label: "Medir", icon: "target", cards: ["calibration", "response", "estimator", "health", "cuts"] },
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
  battery: "M3 8h15v8H3zM21 11v2M6 11v2",
  unlink: "M9 15l6-6M10 6l1-1a4 4 0 0 1 6 6l-1 1M14 18l-1 1a4 4 0 0 1-6-6l1-1M3 3l18 18",
  radio: "M5 12a7 7 0 0 1 14 0M8.5 12a3.5 3.5 0 0 1 7 0M12 12v8",
  chain: "M4 7h4v4H4zM16 13h4v4h-4zM8 9h3a2 2 0 0 1 2 2v2a2 2 0 0 0 2 2h1",
};

function icon(name, cls = "nav-icon h-5 w-5") {
  const node = svg("svg", { viewBox: "0 0 24 24", class: cls, fill: "none", stroke: "currentColor",
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

// Los números visibles van con coma decimal, como los documentos (Intl, sin dependencias).
const NUMBER_FORMATS = new Map();
function nf(value, digits = 1) {
  if (value == null || Number.isNaN(Number(value))) return "—";
  let f = NUMBER_FORMATS.get(digits);
  if (!f) {
    f = new Intl.NumberFormat("es", { minimumFractionDigits: digits, maximumFractionDigits: digits, useGrouping: false });
    NUMBER_FORMATS.set(digits, f);
  }
  return f.format(Number(value));
}
const fmt = (value, digits = 1) => nf(value, digits);

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
  if (!api) { toast("Todavía no hay un equipo conectado."); return null; }
  const reply = await api.raw({ op, ...args });
  if (reply === null) {
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

// "En camino" (research/11 D §4): el valor cambia en pantalla al instante, pero el oído lo nota
// después de la latencia de escrito a oído. Mientras tanto el control lleva `data-pending`.
// Dura la latencia medida por la última calibración (`latency.measured_ms`); sin medir, la
// conocida (`latency.known_ms`, una cota inferior), y sin estado todavía, 500 ms.
const DEFAULT_PENDING_MS = 500;
const pendingTimers = new WeakMap();

function pendingMs() {
  const l = latest && latest.latency;
  return (l && (l.measured_ms ?? l.known_ms)) ?? DEFAULT_PENDING_MS;
}

function markPending(node) {
  if (!node) return;
  node.setAttribute("data-pending", node.dataset.pendingStyle || "dots");
  clearTimeout(pendingTimers.get(node));
  pendingTimers.set(node, setTimeout(() => node.removeAttribute("data-pending"), pendingMs()));
}

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
  if (stopped) return;
  if (live.alive || document.hidden) { setTimeout(pollState, STATE_EVERY_MS); return; }
  const reply = await api.state();
  if (stopped) return;
  if (reply === null) setOnline(false);
  else if (reply.ok) {
    latest = reply.result;
    setOnline(true);
    if (!renderQueued) {
      renderQueued = true;
      requestAnimationFrame(() => { renderQueued = false; render(latest); });
    }
  }
  // Un 401 lo atiende onAccess (deja de consultar); un 403 o un 429 se reintentan.
  setTimeout(pollState, STATE_EVERY_MS);
}

// Lo que el transporte avisa (transport.ts): credencial revocada, permiso insuficiente, demasiados
// intentos. Sin conexión lo dice setOnline.
function onAccess(event) {
  const e = event.detail;
  if (e.kind === "unauthorized") {
    stopped = true;
    closeStream();
    const remote = window.aurasync && window.aurasync.mode === "remote";
    setOnline(false, remote
      ? `${deviceName()} ya no reconoce a este navegador: volvé a emparejar.`
      : "Sin autorización: abrí el link con el token que imprime aurasync service.");
  } else if (e.kind === "rate_limited") {
    toast(e.message);
  }
}

function deviceName() {
  const device = window.aurasync && window.aurasync.device;
  return device ? device.name : "aurasync";
}

// -- stream en vivo (spec §17) ---------------------------------------------------
// El estado, los niveles a 20 Hz, la entrada a 10 Hz y los logs llegan por Server-Sent Events.
// Mientras el stream vive, las consultas periódicas se saltan; si falla 3 veces seguidas, el
// panel vuelve a consultar y lo dice ("Consultando").
const live = { source: null, opening: false, alive: false, failures: 0, meters: null, metersAt: 0, input: null, radio: null, shownMode: "", retry: null };
const STREAM_MAX_FAILURES = 3;

async function openStream() {
  if (typeof EventSource === "undefined" || live.failures >= STREAM_MAX_FAILURES || stopped || !api) return;
  // Con la pestaña oculta no se abre: el stream seguía mandando ~28 eventos/s que nadie veía,
  // y cada pestaña ocupa una de las 6 conexiones HTTP/1.1 del navegador (research/11 D §6).
  if (document.hidden || live.source || live.opening) return;
  // En la PWA, primero un ticket de un solo uso (EventSource no manda cabeceras).
  live.opening = true;
  const source = await api.stream(logSince);
  live.opening = false;
  if (!source) {
    live.failures += 1;
    if (live.failures < STREAM_MAX_FAILURES && !stopped) live.retry = setTimeout(openStream, 1000 * live.failures);
    return;
  }
  if (document.hidden || stopped) { source.close(); return; }
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
  source.addEventListener("quality", (e) => share("quality", JSON.parse(e.data)));
  source.addEventListener("chain", (e) => share("chain", JSON.parse(e.data)));
  source.addEventListener("radio", (e) => { live.radio = JSON.parse(e.data); share("radio", live.radio); });
  source.addEventListener("error", () => {
    source.close();
    if (live.source === source) live.source = null;
    live.alive = false;
    live.failures += 1;
    setOnline(online);
    if (live.failures < STREAM_MAX_FAILURES) live.retry = setTimeout(openStream, 1000 * live.failures);
  });
}

function closeStream() {
  clearTimeout(live.retry);
  if (live.source) live.source.close();
  live.source = null;
  live.alive = false;
}

// Oculta: se cierra el stream y tampoco se consulta. Visible: se reabre desde el último log
// visto (`since=`), así los logs no quedan con hueco; el stream manda el estado al conectar.
function onVisibility() {
  if (stopped) return;
  if (document.hidden) closeStream();
  else { live.failures = 0; openStream(); }
}

// Para los tests: el estado del stream visto desde el navegador.
window.aurasyncStream = () => ({ open: Boolean(live.source), readyState: live.source ? live.source.readyState : null, alive: live.alive });

function setOnline(value, message) {
  const mode = `${value}|${live.alive}`;
  if (online === value && live.shownMode === mode && !message) return;
  live.shownMode = mode;
  online = value;
  $("connection").textContent = value ? (live.alive ? "En vivo" : "Consultando") : "Desconectado";
  // Revocado (onAccess), el aviso queda: no es una desconexión que se arregle sola.
  if (!value && !message && !stopped) message = `Sin conexión con ${deviceName()}. Reintentando…`;
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
  renderAlerts(s);
  renderSync(s);
  renderChart(s);
  renderMeters(s);
  renderInput(s);
  renderMicrophones(s);
  renderConfig(s);
  renderCalibration(s);
  renderEstimator(s);
  window.aurasyncLastSpeakers = s.speakers.map((sp) => sp.name);
  renderSpatial(s);
  renderMonitor(s);
  renderPresets(s);
  share("state", s);
}

function renderTop(s) {
  $("engine-badge").hidden = !s.service.simulated && !s.service.demo;
  $("engine-badge").textContent = s.service.demo ? "DEMO" : "SIMULADO";
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
  renderRoomLayouts(s);
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
  if (source.kind === "file" || source.kind === "multichannel") syncValue($("source-file"), source.name || "");
  syncSourceFields();
  syncValue($("volume"), s.global.volume_db);
  setText($("volume-out"), `${nf($("volume").value, 0)} dB`);
  const l = s.latency;
  $("latency-total").textContent = l.measured_ms != null ? `${Math.round(l.measured_ms)} ms` : `≥ ${Math.round(l.known_ms)} ms`;
  $("chip-preset").hidden = !s.preset;
  $("chip-preset").textContent = s.preset ? `Preset: ${s.preset}` : "";
}

function syncSourceFields() {
  const kind = $("source-kind").value;
  $("source-app").hidden = kind !== "app";
  // Un WAV estéreo, o uno multicanal: un canal por parlante, en el orden de la sala (un render de afuera).
  $("source-file").hidden = kind !== "file" && kind !== "multichannel";
  $("source-file").placeholder = kind === "multichannel" ? "/home/…/render-3-canales.wav" : "/home/…/tema.wav";
}

// -- sala y parlantes -----------------------------------------------------------

let roomLayout = null;
const slots = new Map();

function renderRoomLayouts(s) {
  const box = $("room-layouts");
  const layouts = Object.keys(s.roles || {});
  const key = layouts.join("|");
  if (box.dataset.key !== key) {
    box.dataset.key = key;
    // Con más de tres, en una grilla que se acomoda al ancho (en el teléfono no entran en una fila).
    box.classList.toggle("layout-grid", layouts.length > 3);
    box.replaceChildren(...layouts.map((id) => {
      const [label, roles] = LAYOUT_NAMES[id] || [id, ""];
      return el("button", { type: "button", "data-room-layout": id, class: "seg" }, label, " ", el("small", { class: "muted", text: roles }));
    }));
  }
  for (const button of box.querySelectorAll("[data-room-layout]")) {
    button.setAttribute("aria-pressed", String(button.dataset.roomLayout === s.global.layout));
  }
}

// Dónde va cada parlante en el plano (en % del recuadro). Un rol con ángulo (`role_places`, desde
// que las distribuciones salen de ángulos) va en un círculo: el frente arriba, el anillo exterior
// más afuera. Sin ángulo, el lugar fijo de los roles de siempre; y si no, el de su mezcla.
const clampPlace = ([x, y]) => [Math.max(13, Math.min(87, x)), Math.max(12, Math.min(90, y))];

function mixPlace(pan, ambience) {
  // La misma escala que los roles de siempre: FL (pan −0,7, ambiente 0,15) cae en 22 %, 26 %.
  return clampPlace([50 + pan * 40, 26 + ((ambience - 0.15) / 0.4) * 54]);
}

function rolePlace(s, role) {
  const p = s.role_places && s.role_places[s.global.layout] && s.role_places[s.global.layout][role];
  if (p && p.angle_deg != null) {
    const r = p.lift ? 42 : 32;
    const t = (p.angle_deg * Math.PI) / 180;
    return clampPlace([50 + r * Math.sin(t), 52 - r * Math.cos(t)]);
  }
  if (ROLE_POSITIONS[role]) return ROLE_POSITIONS[role];
  return p ? mixPlace(p.pan, p.ambience) : [50, 50];
}

function renderRoom(s) {
  const room = $("room");
  if (roomLayout !== s.global.layout) {
    for (const slot of slots.values()) slot.box.remove();
    slots.clear();
    const roles = s.roles[s.global.layout] || [];
    room.toggleAttribute("data-many", roles.length > 6);
    for (const role of roles) {
      const [x, y] = rolePlace(s, role);
      const name = el("div", { class: "slot-name" });
      const box = el("div", { class: "slot", title: roleName(role) }, el("div", { class: "slot-channel", text: role }), name);
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
    slot.box.classList.toggle("lost", Boolean(speaker && s.session.status === "playing" && speakerState(s, speaker) === "lost"));
  }
  // Los parlantes sin rol (con 5 a 8 siempre hay): un recuadro donde los ponen su pan y su ambiente,
  // con la misma escala que los roles (FL en pan −0,7 y ambiente 0,15 cae en su lugar; RL, con 0,55).
  // Antes quedaban como texto al pie (experimentos/16 §7).
  const custom = s.speakers.filter((sp) => sp.role == null);
  const key = JSON.stringify(custom.map((sp) => [sp.name, sp.pan, sp.ambience]));
  if (room.dataset.custom !== key) {
    room.dataset.custom = key;
    for (const old of room.querySelectorAll(".slot.custom")) old.remove();
    for (const sp of custom) {
      const box = el("div", { class: "slot custom", title: `${sp.name}: sin rol (pan ${nf(sp.pan, 2)}, ambiente ${nf(sp.ambience, 2)})`, "data-custom": sp.name },
        el("div", { class: "slot-name", text: sp.name.replace("JBL ", "") }));
      const [x, y] = mixPlace(sp.pan, sp.ambience);
      box.style.left = `${x}%`;
      box.style.top = `${y}%`;
      room.append(box);
    }
  }
  let note = room.querySelector(".room-custom");
  if (!note) { note = el("div", { class: "room-custom" }); room.append(note); }
  note.textContent = custom.length ? `sin rol: ${custom.length}` : "";
  // Con una sala que no es la de esta cantidad de parlantes (3 en cuadrafonía), decirlo y ofrecer la
  // salida, en vez de solo «sin rol: 1» (probes/18-usabilidad).
  let hint = $("room-hint");
  if (!hint) {
    hint = el("p", { id: "room-hint", class: "callout small mt-2", role: "status" });
    room.after(hint);
  }
  const offer = custom.length > 0 && s.global.layout !== "auto";
  hint.hidden = !offer;
  if (offer) {
    const fix = el("button", { type: "button", class: "btn small-btn ml-1", "data-room-auto": "", text: "Usar «Automático»" });
    fix.addEventListener("click", () => send("set", { changes: { layout: "auto" } }));
    const what = custom.length === 1 ? "1 parlante quedó sin lugar" : `${custom.length} parlantes quedaron sin lugar`;
    hint.replaceChildren(`${what} en esta sala (tiene lugar para ${s.speakers.length - custom.length}). «Automático» los reparte a todos alrededor.`, fix);
  }
  const scan = $("scan");
  scan.disabled = s.scanning;
  scan.textContent = s.scanning ? "Buscando…" : "Buscar parlantes";
}

const speakerRows = new Map();

function speakerState(s, sp) {
  // El servicio nuevo dice qué pasa con la salida; uno viejo no manda `output` y se deduce como siempre.
  // `output` es null sin sesión: un parlante virtual sigue siendo virtual.
  if (sp.output) return sp.output;
  if (sp.output_kind === "virtual") return "virtual";
  if (s.session.status === "playing") return sp.playing ? "playing" : "lost";
  if (sp.connected === null) return "unknown";
  return sp.connected ? "connected" : "disconnected";
}

function rangeCell(min, max, step, label, format, onValue, neutral = null) {
  const input = el("input", { type: "range", min, max, step, "aria-label": label });
  const out = el("output", { class: "num" });
  let reset = null;
  const syncReset = () => { if (reset) reset.classList.toggle("invisible", Number(input.value) === neutral); };
  liveRange(input, out, format, (v) => { syncReset(); markPending(out); return onValue(v); });
  if (neutral != null) {
    // Convención de consola: doble clic vuelve al valor neutro, y una marca lo muestra. En un
    // teléfono el primer toque mueve el deslizador, así que además hay un ↺ visible de 44 px
    // mientras el valor no es el neutro.
    const back = () => { input.value = String(neutral); out.textContent = format(neutral); syncReset(); markPending(out); onValue(neutral); };
    input.setAttribute("list", "tick-zero");
    input.title = `${label}: doble clic vuelve a ${format(neutral)}`;
    input.addEventListener("dblclick", back);
    reset = el("button", { type: "button", class: "reset-btn invisible", text: "↺",
      "aria-label": `${label}: volver a ${format(neutral)}`, title: `Volver a ${format(neutral)}` });
    reset.addEventListener("click", back);
  }
  const cell = el("div", { class: "hstack" }, input, el("span", { class: "w-14 shrink-0 text-right text-xs tabular-nums" }, out),
    ...(reset ? [reset] : []));
  return { input, out, reset, cell, syncReset };
}

function speakerRow(speaker) {
  const name = speaker.name;
  const current = () => latest && latest.speakers.find((sp) => sp.name === name);
  const role = editable(el("select", { "aria-label": `Rol de ${name}` }));
  // Qué parlante es: la ecualización confía en la banda del fabricante antes que en el micrófono.
  const kind = editable(el("select", { "aria-label": `Tipo de ${name}` }));
  kind.addEventListener("change", () => setSpeaker(kind, name, { kind: kind.value }));
  role.addEventListener("change", () => { if (role.value) sendFrom(role, "assign", { speaker: name, role: role.value }); });
  const pan = rangeCell("-1", "1", "0.05", `Pan de ${name}`, (v) => (v === 0 ? "C" : v < 0 ? `I ${nf(-v, 2)}` : `D ${nf(v, 2)}`),
    (v) => setSpeaker(pan.input, name, { pan: v }), 0);
  const ambience = rangeCell("0", "1", "0.05", `Ambiente de ${name}`, (v) => nf(v, 2),
    (v) => setSpeaker(ambience.input, name, { ambience: v }));
  const gain = rangeCell("-40", "6", "0.5", `Volumen de ${name}`, (v) => `${nf(v, 1)} dB`,
    (v) => setSpeaker(gain.input, name, { gain_db: v }), 0);
  const delay = editable(el("input", { type: "number", min: "0", max: "100", step: "0.1", class: "num-input",
    "aria-label": `Retardo de ${name} en ms` }));
  delay.addEventListener("change", () => setSpeaker(delay, name, { delay_ms: Number(delay.value) }));
  const delayNow = el("div", { class: "speaker-sub" });
  const mute = el("button", { type: "button", class: "ghost small-btn" });
  mute.addEventListener("click", () => { const sp = current(); if (sp) { markPending(mute); send("set", { speaker: name, changes: { muted: !sp.muted } }); } });
  const tone = el("button", { type: "button", class: "ghost small-btn", text: "Tono" });
  tone.addEventListener("click", () => send("tone", { speaker: name, seconds: 2 }));
  // Entrar y salir de la sesión que suena (fase 2): cada botón se muestra solo cuando corresponde.
  const join = el("button", { type: "button", class: "ghost small-btn", text: "Hacer entrar", hidden: "" });
  const retry = el("button", { type: "button", class: "ghost small-btn", text: "Reintentar", hidden: "" });
  const leave = el("button", { type: "button", class: "ghost small-btn", text: "Sacar", hidden: "" });
  for (const [button, op] of [[join, "speaker_join"], [retry, "speaker_join"], [leave, "speaker_leave"]]) {
    button.addEventListener("click", () => { markPending(button); send(op, { speaker: name }); });
  }
  const remove = el("button", { type: "button", class: "ghost small-btn danger", text: "Quitar" });
  // Sin confirm(): se quita al instante y «Deshacer» lo vuelve a agregar con lo que tenía.
  remove.addEventListener("click", () => withUndo(`${name}: quitado de la instalación (se guarda con «Guardar instalación»)`,
    () => send("speaker_remove", { speaker: name })));
  // En el teléfono, con 4 parlantes o más, la fila es una tarjeta plegada (CSS: .speakers-table[data-many]).
  const fold = el("button", { type: "button", class: "fold-btn", "aria-label": `Ajustes de ${name}`, "aria-expanded": "false" });
  const row = {
    tr: el("tr", { "data-speaker": name }), name: el("div", { class: "speaker-name" }), sub: el("div", { class: "speaker-sub" }),
    status: el("span", { class: "status" }), role, kind, pan, ambience, gain, delay, delayNow, mute, tone, join, retry, leave, remove, fold,
  };
  fold.addEventListener("click", () => setFolded(row.tr, fold, !row.tr.hasAttribute("data-folded"), name));
  row.tr.append(
    el("td", { class: "cell-name" }, fold, el("div", { class: "min-w-0" }, row.name, row.sub)),
    el("td", { class: "cell-status" }, row.status),
    el("td", { class: "cell-kind", "data-label": "Tipo" }, kind),
    el("td", { class: "cell-role", "data-label": "Rol" }, role),
    el("td", { class: "cell-pan", "data-label": "Pan" }, pan.cell),
    el("td", { class: "cell-ambience", "data-label": "Ambiente" }, ambience.cell),
    el("td", { class: "cell-volume", "data-label": "Volumen" }, gain.cell),
    el("td", { class: "num cell-delay", "data-label": "Retardo (ms)" }, delay, delayNow),
    el("td", { class: "cell-actions" }, el("div", { class: "hstack" }, join, retry, leave, tone, mute, remove)),
  );
  return row;
}

// Plegar o desplegar la tarjeta de un parlante. Lo que la persona abrió queda abierto mientras
// la página viva (por nombre), aunque la tarjeta se vuelva a dibujar.
const unfolded = new Set();
function setFolded(node, button, folded, key) {
  node.toggleAttribute("data-folded", folded);
  button.setAttribute("aria-expanded", String(!folded));
  if (key) { if (folded) unfolded.delete(key); else unfolded.add(key); }
}

function syncFold(node, button, many, key) {
  const folded = many && !unfolded.has(key);
  if (node.hasAttribute("data-folded") !== folded) {
    node.toggleAttribute("data-folded", folded);
    button.setAttribute("aria-expanded", String(!folded));
  }
}

function setStatus(node, table, state, extra = "") {
  const [, label] = table[state] || ["", state];
  node.className = `status status-${state}`;
  // Un punto de color y la palabra: el color nunca va solo (WCAG 1.4.1).
  node.replaceChildren(el("span", { class: "status-dot", "aria-hidden": "true" }), label + extra);
}

// Entrar y salir con la sesión sonando (spec 2026-10-05 §5): «Sacar» a quien suena, «Hacer entrar» a
// quien no está (o lo perdimos y el servicio todavía lo reintenta), «Reintentar» cuando el servicio se rindió.
function syncJoinButtons(row, s, sp) {
  const playing = s.session.status === "playing";
  const state = speakerState(s, sp);
  const real = sp.output_kind !== "virtual";
  const gaveUp = state === "lost" && sp.rejoin === "gave_up";
  row.leave.hidden = !(playing && real && state === "playing");
  row.retry.hidden = !(playing && real && gaveUp);
  row.join.hidden = !(playing && real && (state === "absent" || (state === "lost" && !gaveUp)));
  // Un Bluetooth sin enlace no puede entrar: primero se conecta (Dispositivos).
  const offline = sp.output_kind === "bluetooth" && sp.connected === false;
  for (const button of [row.join, row.retry]) {
    button.disabled = offline;
    button.title = offline ? "Conectalo primero (Dispositivos)" : "";
  }
}

// Lo que entró con el lazo de recalibración apagado puede haber movido la alineación: avisa hasta
// que se recalibre, se encienda el lazo o la sesión pare.
let joinWarning = false;
let lastOutputs = null;
function trackJoins(s) {
  const playing = s.session.status === "playing";
  const outputs = new Map(s.speakers.map((sp) => [sp.name, sp.output]));
  if (!playing || s.recalibration.active) joinWarning = false;
  else if (lastOutputs && s.speakers.some((sp) => sp.output === "playing" && lastOutputs.has(sp.name) && lastOutputs.get(sp.name) !== "playing")) joinWarning = true;
  lastOutputs = playing ? outputs : null;
  const cal = s.calibration;
  if (cal && cal.state === "done" && !cal.stale && cal.measured_at !== trackJoins.calAt) { trackJoins.calAt = cal.measured_at; if (trackJoins.seen) joinWarning = false; }
  trackJoins.seen = true;
  const box = $("join-warning");
  box.hidden = !joinWarning;
  box.textContent = joinWarning ? "Entró un parlante con el lazo de «Mantener sincronía» apagado: la alineación puede haber cambiado, recalibrá." : "";
}

function renderSpeakers(s) {
  trackJoins(s);
  const body = $("speakers");
  const seen = new Set();
  const many = s.speakers.length >= FOLD_FROM;
  body.closest("table").toggleAttribute("data-many", many);
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
    syncFold(row.tr, row.fold, many, `detail:${sp.name}`);
    row.name.textContent = sp.name;
    row.sub.textContent = [sp.address, sp.codec, sp.modalias, sp.rssi_dbm != null ? `${sp.rssi_dbm} dBm` : null]
      .filter(Boolean).join(" · ");
    setStatus(row.status, SPEAKER_STATUS, speakerState(s, sp), sp.muted ? " · mudo" : "");
    syncJoinButtons(row, s, sp);
    const roles = s.roles[s.global.layout];
    if (row.role.dataset.layout !== s.global.layout) {
      row.role.replaceChildren(el("option", { value: "", text: "personalizado", disabled: "" }),
        ...roles.map((r) => el("option", { value: r, text: `${r} · ${roleName(r)}` })));
      row.role.dataset.layout = s.global.layout;
      row.role.dataset.synced = "\u0000";
    }
    syncValue(row.role, sp.role || "");
    if (!row.kind.options.length) {
      row.kind.replaceChildren(...s.speaker_kinds.map((k) => el("option", { value: k.key, text: k.label })));
    }
    syncValue(row.kind, sp.kind || sp.kind_guess || "generic");
    for (const [key, cell, format] of [
      ["pan", row.pan, (v) => (v === 0 ? "C" : v < 0 ? `I ${nf(-v, 2)}` : `D ${nf(v, 2)}`)],
      ["ambience", row.ambience, (v) => nf(v, 2)],
      ["gain_db", row.gain, (v) => `${nf(v, 1)} dB`],
    ]) {
      syncValue(cell.input, sp[key]);
      setText(cell.out, format(Number(cell.input.value)));
      cell.syncReset();
    }
    syncValue(row.delay, sp.delay_ms);
    row.delay.disabled = loop;
    row.delay.title = loop ? "Lo maneja el lazo de recalibración mientras corre" : "";
    // El retardo que suena: el de calibración más el de Haas (ambiente × retardo trasero).
    row.delayNow.textContent = sp.delay_now_ms != null ? `efectivo ${nf(sp.delay_now_ms, 2)}` : "";
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

const NARROW = window.matchMedia("(max-width: 639px)");

function quickRow(speaker) {
  const name = speaker.name;
  const current = () => latest && latest.speakers.find((sp) => sp.name === name);
  const title = el("div", { class: "speaker-name truncate" });
  const status = el("span", { class: "status text-xs" });
  const ambience = rangeCell("0", "1", "0.05", `Ambiente de ${name}`, (v) => nf(v, 2),
    (v) => setSpeaker(ambience.input, name, { ambience: v }));
  ambience.input.classList.add("q-ambience");
  const gain = rangeCell("-40", "6", "0.5", `Volumen de ${name}`, (v) => `${nf(v, 1)} dB`,
    (v) => setSpeaker(gain.input, name, { gain_db: v }), 0);
  gain.input.classList.add("q-gain");
  const mute = el("button", { type: "button", class: "btn small-btn q-mute" });
  mute.addEventListener("click", () => { const sp = current(); if (sp) { markPending(mute); send("set", { speaker: name, changes: { muted: !sp.muted } }); } });
  const tone = el("button", { type: "button", class: "btn btn-ghost small-btn q-tone", text: "Tono" });
  tone.addEventListener("click", () => send("tone", { speaker: name, seconds: 2 }));
  // Con 4 parlantes o más va plegada: a la vista el nombre, el estado, Tono y Silenciar; los
  // deslizadores al abrirla (▸). Con 3, entera como siempre y sin el botón.
  const fold = el("button", { type: "button", class: "fold-btn", "aria-label": `Ambiente y volumen de ${name}`, "aria-expanded": "true", hidden: "" });
  const box = el("div", { class: "rounded-xl border border-zinc-200 px-3 py-2 dark:border-zinc-800", "data-quick": name },
    el("div", { class: "fold-head flex items-center justify-between gap-2" },
      el("div", { class: "flex min-w-0 items-center gap-2" }, fold, el("div", { class: "fold-title flex min-w-0 items-baseline gap-2" }, title, status)),
      el("div", { class: "hstack" }, tone, mute)),
    // El número va en la línea del título, para que el deslizador tenga el ancho y el ↺ su lugar.
    el("div", { class: "fold-body grid grid-cols-2 gap-3" }, quickField("Ambiente", ambience), quickField("Volumen", gain)));
  fold.addEventListener("click", () => setFolded(box, fold, !box.hasAttribute("data-folded"), `quick:${name}`));
  return { box, title, status, ambience, gain, mute, tone, fold };
}

function quickField(title, cell) {
  cell.out.classList.add("text-xs");
  return el("div", { class: "field" },
    el("span", { class: "flex items-baseline justify-between gap-2" }, el("span", { text: title }), cell.out),
    el("div", { class: "hstack" }, cell.input, ...(cell.reset ? [cell.reset] : [])));
}

function renderQuick(s) {
  const list = $("quick");
  const seen = new Set();
  const playing = s.session.status === "playing";
  const many = s.speakers.length >= FOLD_FROM;
  renderGroup(s, many);
  for (const sp of s.speakers) {
    seen.add(sp.name);
    let row = quickRows.get(sp.name);
    if (!row) { row = quickRow(sp); quickRows.set(sp.name, row); list.append(row.box); }
    row.fold.hidden = !many;
    syncFold(row.box, row.fold, many, `quick:${sp.name}`);
    // Plegada, el nombre corto y el estado debajo: en el teléfono no entran en una línea con Tono y Silenciar.
    row.box.querySelector(".fold-title").classList.toggle("fold-stack", many || NARROW.matches);
    // En el teléfono, el nombre corto: entero se cortaba en «JBL Go 4…» y no decía cuál era (probes/18).
    const short = many || NARROW.matches;
    row.title.textContent = `${short ? sp.name.replace("JBL ", "") : sp.name}${sp.role ? ` · ${sp.role}` : ""}`;
    row.title.title = sp.name;
    setStatus(row.status, SPEAKER_STATUS, speakerState(s, sp), sp.muted ? " · mudo" : "");
    syncValue(row.ambience.input, sp.ambience);
    setText(row.ambience.out, nf(row.ambience.input.value, 2));
    syncValue(row.gain.input, sp.gain_db);
    setText(row.gain.out, `${nf(row.gain.input.value, 1)} dB`);
    row.gain.syncReset();
    // Convención de consola: el botón queda "encendido" mientras el parlante está silenciado.
    row.mute.textContent = sp.muted ? "Silenciado" : "Silenciar";
    row.mute.setAttribute("aria-pressed", String(sp.muted));
    row.tone.disabled = !playing;
  }
  for (const [name, row] of quickRows) if (!seen.has(name)) { row.box.remove(); quickRows.delete(name); }
  if (!s.speakers.length && !list.querySelector(".chart-empty")) list.append(el("p", { class: "chart-empty", text: "La instalación no tiene parlantes: agregalos en Sala." }));
}

// -- acciones por grupo (experimentos/16 §7): silenciar, activar o identificar a todos, a los de
// adelante o a los de atrás. Adelante y atrás salen del rol (F… / R…); sin rol, del ambiente: los
// roles traseros llevan 0,55 y los delanteros 0,15 o menos (control.ROLES), así que el corte va a
// 0,35. Es una regla INFERIDA mientras el servicio no traiga la posición de cada parlante.
const GROUPS = [["all", "Todos"], ["front", "Adelante"], ["rear", "Atrás"]];
let groupChoice = "all";
let groupBusy = false;

function sideOf(s, sp) {
  const p = sp.role && s.role_places && (s.role_places[s.global.layout] || {})[sp.role];
  // Con ángulo: adelante lo que está a menos de 90° del frente; los laterales (90°) van atrás.
  if (p && p.angle_deg != null) return Math.abs(p.angle_deg) < 90 ? "front" : "rear";
  if (sp.role && ROLE_POSITIONS[sp.role]) return sp.role.startsWith("R") ? "rear" : "front";
  return sp.ambience >= 0.35 ? "rear" : "front";
}

function groupMembers(s, group = groupChoice) {
  return s.speakers.filter((sp) => group === "all" || sideOf(s, sp) === group);
}

function renderGroup(s, many) {
  const box = $("quick-group");
  box.hidden = !many;
  if (!many) return;
  if (!box.childElementCount) {
    const seg = el("div", { class: "segmented", role: "group", "aria-label": "Grupo" });
    for (const [key, label] of GROUPS) {
      const b = el("button", { type: "button", class: "seg", "data-group": key, text: label });
      b.addEventListener("click", () => { groupChoice = key; if (latest) renderGroup(latest, true); });
      seg.append(b);
    }
    const act = (text, action, cls = "btn small-btn") => {
      const b = el("button", { type: "button", class: cls, "data-group-action": action, text });
      b.addEventListener("click", () => groupAction(action));
      return b;
    };
    box.append(seg, act("Silenciar", "mute"), act("Activar", "unmute"), act("Identificar", "identify", "btn btn-ghost small-btn"),
      el("span", { class: "muted small", "data-group-note": "" }));
  }
  const members = groupMembers(s);
  for (const b of box.querySelectorAll("[data-group]")) b.setAttribute("aria-pressed", String(b.dataset.group === groupChoice));
  const playing = s.session.status === "playing";
  for (const b of box.querySelectorAll("[data-group-action]")) {
    b.disabled = groupBusy || !members.length || (b.dataset.groupAction === "identify" && !playing);
  }
  setText(box.querySelector("[data-group-note]"), `${members.length} de ${s.speakers.length}`);
}

async function groupAction(action) {
  if (!latest) return;
  const members = groupMembers(latest);
  groupBusy = true;
  renderGroup(latest, true);
  try {
    if (action === "identify") {
      for (const sp of members) {
        $("now-action").textContent = `Suena: ${sp.name}`;
        toast(`Suena: ${sp.name}`);
        await send("tone", { speaker: sp.name, seconds: 1.2 });
        await new Promise((r) => setTimeout(r, 1500));
      }
      $("now-action").textContent = "";
    } else {
      const muted = action === "mute";
      for (const sp of members) if (sp.muted !== muted) await send("set", { speaker: sp.name, changes: { muted } });
    }
  } finally {
    groupBusy = false;
    if (latest) renderGroup(latest, true);
  }
}

const DEVICE_GROUPS = [
  ["connected", "Conectados"],
  ["paired", "Emparejados, sin conectar (apagados o lejos)"],
  ["seen", "Encontrados cerca, sin emparejar"],
];

function deviceRow(d, playing) {
  const actions = el("div", { class: "hstack" });
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
    // Olvidar no se puede deshacer (habría que emparejarlo de nuevo): se muestra hecho y se manda
    // cuando vence el aviso; «Deshacer» lo cancela.
    button("Olvidar", "ghost small-btn danger", () => {
      hiddenDevices.add(d.address);
      if (latest) renderDevices(latest);
      const back = () => { hiddenDevices.delete(d.address); if (latest) renderDevices(latest); };
      deferUndo(`${d.name || d.address}: olvidado (para volver a usarlo habrá que emparejarlo)`,
        async () => { await send("forget", { address: d.address }); back(); }, back);
    }, "Borra el emparejamiento");
  }
  const state = d.connected ? (d.in_installation ? "conectado · en la instalación" : "conectado") : d.paired ? "sin conectar" : "visto";
  const battery = d.battery_pct == null ? "—" : `${d.battery_pct} %`;
  return el("tr", {},
    el("td", {}, el("div", { class: "speaker-name", text: d.name || d.address }),
      el("div", { class: "speaker-sub", text: d.address })),
    el("td", { "data-label": "Estado", text: state }),
    el("td", { "data-label": "Batería", class: `num ${d.battery_pct != null && d.battery_pct <= 20 ? "text-rose-600" : ""}`, text: battery }),
    el("td", { "data-label": "Señal", class: "num", text: d.rssi_dbm != null ? `${d.rssi_dbm} dBm` : "—" }),
    el("td", {}, actions));
}

// Lo que se está olvidando (el aviso «Deshacer» sigue abierto): ya no se muestra.
const hiddenDevices = new Set();

function renderDevices(s) {
  const devices = s.devices.filter((d) => !hiddenDevices.has(d.address));
  $("devices-count").textContent = `(${devices.length})`;
  $("scan").disabled = Boolean(s.scanning);
  $("scan").textContent = s.scanning ? "Buscando…" : "Buscar cerca";
  const playing = s.session.status === "playing";
  const addVirtual = $("add-virtual");
  addVirtual.disabled = playing || s.session.status === "starting";
  addVirtual.title = addVirtual.disabled ? "Detené la sesión para agregar un parlante" : "";
  const group = (d) => (d.connected ? "connected" : d.paired ? "paired" : "seen");
  const rows = [];
  for (const [key, title] of DEVICE_GROUPS) {
    const members = devices.filter((d) => group(d) === key);
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
    error: el("div", { class: "service-error" }), actions: el("div", { class: "hstack" }), actionsKey: null,
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
  $("t-motor").textContent = h.motor_ms == null || !playing ? "—" : `${nf(h.motor_ms, 2)} ms`;
  $("t-motor-sub").textContent = h.realtime_x ? `por bloque de ${h.budget_ms} ms · ${h.realtime_x}× tiempo real` : `bloque de ${h.budget_ms} ms`;
  $("t-routing").textContent = !playing ? "—" : h.lost.length ? `${h.lost.length} perdido(s)` : "en orden";
  $("t-routing-sub").textContent = `${h.routing_repairs} stream(s) devuelto(s) a su parlante`;
  // Cuánto audio quedaba esperando a los parlantes cuando el motor volvió a escribir.
  const pipe = h.pipe_level_ms;
  $("t-pipe").textContent = pipe == null || !playing ? "—" : `${Math.round(pipe)} ms`;
  $("t-pipe").className = `tile-value ${pipe == null || !playing ? "" : pipe < 20 ? "bad" : pipe < 60 ? "warn" : "good"}`;
  // El colchón de los parlantes: uno para todos, rellenado para todos en el fondo de un corte.
  const cushion = h.output_cushion;
  const refills = !cushion ? ""
    : (cushion.refills ? ` · ${cushion.refills} relleno(s): la tubería se vaciaba y se rellenó para todos en un corte` : "")
      + (cushion.gave_up ? " · el colchón dejó de rellenar: los rellenos no alcanzaban" : "")
      + (cushion.reason ? ` · sin relleno (${cushion.reason})` : "");
  $("t-pipe-sub").textContent = (h.bt_discovering
    ? "Bluetooth está buscando dispositivos: puede cortar el audio"
    : "audio esperando en la tubería; bajo 20 ms el parlante se queda sin nada") + refills;
  // Calidad: cada xrun es un hueco o un salto en lo que suena. 0/min es lo esperado.
  const xr = Object.entries(h.xruns || {});
  const worst = xr.reduce((m, [, v]) => (v.per_min != null && v.per_min > m ? v.per_min : m), xr.length ? 0 : null);
  const tile = $("t-xruns");
  tile.textContent = !playing ? "—" : worst == null ? "midiendo…" : worst === 0 ? "sin cortes" : `${nf(worst, 0)} por minuto`;
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
  const drift = r.drift_ms_h ? Object.entries(r.drift_ms_h).map(([n, v]) => `${n} ${v >= 0 ? "+" : ""}${nf(v, 1)}`).join(" · ") : "";
  $("t-drift").textContent = drift ? `deriva ms/h (INFERIDA): ${drift}` : "";
  const l = s.latency;
  const rows = [
    ["Entrada (un bloque)", l.input_ms], ["Tubería hacia pw-play (dos bloques)", l.pipe_ms],
    ["Extractor de ambiente", l.extractor_ms], ["Buffer de pw-play", l.player_ms], ["A2DP y el parlante", l.a2dp_ms],
  ];
  $("latency").replaceChildren(
    ...rows.map(([label, ms]) => el("tr", {}, el("td", { text: label }),
      el("td", { class: "num", text: ms == null ? "sin medir" : `${nf(ms, 1)} ms` }))),
    el("tr", { class: "total" }, el("td", { text: "Conocido" }), el("td", { class: "num", text: `≥ ${nf(l.known_ms, 1)} ms` })),
    el("tr", { class: "total" }, el("td", { text: "Medida (última calibración, de escrito a oído)" }),
      el("td", { class: "num", text: l.measured_ms == null ? "calibrá para medirla" : `${nf(l.measured_ms, 0)} ms` })),
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
    "aria-label": `Retardo por parlante, entre ${nf(lo, 1)} y ${nf(hi, 1)} ms` });
  for (const v of [lo, hi]) {
    chart.append(svg("line", { class: "grid-line", x1: pad.left, x2: width - pad.right, y1: y(v), y2: y(v) }));
    const label = svg("text", { class: "axis-label", x: pad.left - 4, y: y(v) + 3, "text-anchor": "end" });
    label.textContent = nf(v, 1);
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

// Lo que se mueve: los medidores (cada cuadro) y el espectro de entrada (con cada dato nuevo).
// Solo se escriben `transform` y textos que cambiaron, y nunca se lee una medida del layout:
// antes, `clientWidth` después de escribir estilos forzaba 7 layouts por cuadro (420/s,
// research/11 D §6). Una capa de ancho completo corrida con translateX(%) se mueve en el
// compositor sin conocer el ancho de la pista (el % es del propio elemento).
// WCAG 2.2.2: Niveles y Entrada se pueden pausar; con `prefers-reduced-motion` se pintan a
// lo sumo 5 veces por segundo y sin transiciones (el CSS las apaga).
const motion = {
  reduce: typeof matchMedia === "function" ? matchMedia("(prefers-reduced-motion: reduce)") : { matches: false },
  paused: { levels: false, input: false },
  meterPaintAt: 0,
  meterTextAt: 0,
  inputFrame: null,
  inputPaintAt: 0,
};
const METER_EVERY_MS = 33; // ~30 por segundo: el dato llega a 20 Hz y la caída del pico se ve continua
const REDUCED_EVERY_MS = 200; // ≤ 5 actualizaciones por segundo
const METER_TEXT_EVERY_MS = 100; // el número y aria-valuetext: 10 por segundo alcanza para leerlos

function setText(node, text) {
  if (node.textContent !== text) node.textContent = text;
}

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
    // La pista lleva las zonas de color; la máscara (del color de la pista) tapa lo que está
    // sobre el nivel y se corre a la derecha cuando sube.
    const mask = el("div", { class: "meter-mask" });
    const peak = el("div", { class: "meter-peak-pos" }, el("div", { class: "meter-peak" }));
    const value = el("div", { class: "meter-value num" });
    const clip = el("button", { type: "button", class: "meter-clip", title: "Saturación: tocalo para apagarlo", "aria-label": `Saturación en ${name}`, "aria-pressed": "false" });
    clip.addEventListener("click", () => { clip.classList.remove("on"); clip.textContent = ""; clip.setAttribute("aria-pressed", "false"); });
    const limit = el("div", { class: "meter-limit num", title: "Reducción del limitador" });
    const label = METER_NAMES[name] || name.replace("JBL ", "");
    const track = el("div", { class: "meter-track", role: "meter", "aria-label": `Nivel ${label}`,
      "aria-valuemin": "-60", "aria-valuemax": "0", "aria-valuenow": "-60", "aria-valuetext": "sin señal" }, mask, peak);
    box.append(el("div", { class: "meter" }, el("div", { class: "meter-label" }, label, limit), track, value, clip));
    meterRows.set(name, { mask, peak, value, track, clip, limit, rms: SILENCE, held: SILENCE, heldAt: 0, at: 0, shown: "" });
  }
}

// Con stream: valores crudos cada 21 ms; la integración del RMS (300 ms) y la retención del pico
// (1,5 s, después cae 20 dB en 1,7 s) se hacen acá, contra el reloj del navegador, en cada cuadro;
// pintar puede ser más espaciado (movimiento reducido).
function integrateMeter(row, rmsDb, peakDb, now) {
  const dt = row.at ? now - row.at : 0;
  row.at = now;
  const a = dt > 0 ? Math.exp(-dt / RMS_TAU_MS) : 0;
  const power = (db) => (db <= SILENCE ? 0 : 10 ** (db / 10));
  const mixed = a * power(row.rms) + (1 - a) * power(rmsDb);
  row.rms = mixed > 0 ? 10 * Math.log10(mixed) : SILENCE;
  if (peakDb >= row.held) { row.held = peakDb; row.heldAt = now; }
  else if (now - row.heldAt > PEAK_HOLD_MS) row.held = Math.max(peakDb, row.held - PEAK_FALL_DB_PER_MS * dt);
}

function paintMeter(row, rmsDb, peakDb, withText = true) {
  const pct = rmsDb <= SILENCE ? 0 : meterPct(rmsDb);
  const transform = `translateX(${pct.toFixed(2)}%)`;
  if (row.mask.style.transform !== transform) row.mask.style.transform = transform;
  const hidePeak = peakDb <= SILENCE;
  if (row.peak.hidden !== hidePeak) row.peak.hidden = hidePeak;
  const peakTransform = `translateX(${meterPct(peakDb).toFixed(2)}%)`;
  if (!hidePeak && row.peak.style.transform !== peakTransform) row.peak.style.transform = peakTransform;
  if (peakDb >= -0.5 && !row.clip.classList.contains("on")) {
    // La saturación dice "SAT", no solo se pone roja (WCAG 1.4.1).
    row.clip.classList.add("on");
    row.clip.textContent = "SAT";
    row.clip.setAttribute("aria-pressed", "true");
  }
  if (!withText) return;
  const text = rmsDb <= SILENCE ? "—" : nf(rmsDb, 1);
  setText(row.value, text);
  const valuetext = rmsDb <= SILENCE ? "sin señal"
    : `${nf(rmsDb, 1)} dB, pico ${peakDb <= SILENCE ? "—" : nf(peakDb, 1)} dB${row.clip.classList.contains("on") ? ", saturó" : ""}`;
  if (row.shown !== valuetext) {
    row.shown = valuetext;
    row.track.setAttribute("aria-valuenow", String(Math.max(-60, Math.round(rmsDb))));
    row.track.setAttribute("aria-valuetext", valuetext);
  }
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
  if (fresh || motion.paused.levels) return; // el cuadro de animación los dibuja
  for (const [name, row] of meterRows) {
    const m = values[name];
    if (m) paintMeter(row, m.rms_db, m.peak_db);
  }
}

function animateMeters(now) {
  const frame = live.meters;
  if (frame && now - live.metersAt < 500 && meterRows.size) {
    const every = motion.reduce.matches ? REDUCED_EVERY_MS : METER_EVERY_MS;
    // 2 ms de holgura: con pantallas de 60 Hz, 33 ms cae a veces apenas antes del segundo cuadro.
    const paint = !motion.paused.levels && now - motion.meterPaintAt >= every - 2;
    const withText = paint && now - motion.meterTextAt >= Math.max(every, METER_TEXT_EVERY_MS);
    if (paint) motion.meterPaintAt = now;
    if (withText) motion.meterTextAt = now;
    for (const [name, row] of meterRows) {
      const m = frame.meters[name];
      if (!m) continue;
      integrateMeter(row, m.rms_db, m.peak_db, now);
      if (paint) paintMeter(row, row.rms, row.held, withText);
      if (withText) {
        const cut = (frame.limiter_db || {})[name];
        setText(row.limit, cut > 0.05 ? ` −${nf(cut, 1)} dB lim` : "");
      }
    }
    if (withText) setText($("meters-sync"), frame.synced ? "sincronizado con lo que suena" : "sin latencia medida: adelantado respecto de lo que suena");
    if (withText && !$("mic-check").closest("[data-view]")?.hidden) paintMicCheck();
  }
  drawInputLive(now);
  requestAnimationFrame(animateMeters);
}

function setPaused(which, paused) {
  motion.paused[which] = paused;
  const button = $(`${which}-pause`);
  button.textContent = paused ? "Reanudar" : "Pausar";
  button.setAttribute("aria-pressed", String(paused));
  if (!paused && which === "levels") {
    // Al reanudar se salta al presente: la integración vuelve a empezar con el dato de ahora.
    for (const row of meterRows.values()) { row.at = 0; row.rms = SILENCE; row.held = SILENCE; }
  }
  if (!paused && which === "input") motion.inputFrame = null;
}

// -- ahora: la pantalla principal ---

function renderNow(s) {
  const playing = s.session.status === "playing";
  $("now-preset").textContent = s.preset ? `preset: ${s.preset}` : "sin preset";
  const cuts = s.health && s.health.cuts;
  const analysis = s.input && s.input.analysis;
  const facts = [
    ["Estado", playing ? "sonando" : s.session.status === "error" ? `detenido: ${s.session.reason || "error"}` : "detenido"],
    ["Fuente", s.source ? [SOURCE_NAMES[s.source.kind] || s.source.kind, s.source.name].filter(Boolean).join(" · ") : "—"],
    ["Entrada", analysis ? `${analysis.kind}${analysis.bandwidth_hz ? ` · hasta ${nf(analysis.bandwidth_hz / 1000, 1)} kHz` : ""}` : "—"],
    ["Cortes", !cuts ? "—" : cuts.faults_1min ? `${cuts.faults_1min} en el último minuto` : cuts.faults_10min ? `${cuts.faults_10min} en 10 min` : "ninguno"],
  ];
  $("now-facts").replaceChildren(...facts.flatMap(([k, v]) => {
    const dd = el("dd", { text: v });
    if (k === "Cortes" && cuts && cuts.faults_10min) {
      dd.replaceChildren(el("button", { type: "button", class: "link", text: v }));
      dd.firstChild.addEventListener("click", () => window.aurasyncShow("[data-card=cuts]"));
    }
    return [el("dt", { text: k }), dd];
  }), ...syncRow());
  $("now-calibrate").disabled = !playing || nowBusy;
  $("now-identify").disabled = !playing || nowBusy;
}

// -- avisos donde se mira: batería baja, parlante perdido o desconectado, cortes de radio ---
// Van en Escuchar → Ahora y en la cabecera, con ícono y texto (antes la batería estaba solo en
// Parlantes, y solo en rojo).
const LOW_BATTERY_PCT = 20;

function alertsOf(s) {
  const playing = s.session.status === "playing";
  const devices = new Map((s.devices || []).map((d) => [d.address, d]));
  const radio = (s.radio && s.radio.available && s.radio.speakers) || {};
  const out = [];
  for (const sp of s.speakers) {
    const short = sp.name.replace("JBL ", "");
    const battery = (devices.get(sp.address) || {}).battery_pct;
    if (playing && speakerState(s, sp) === "lost") {
      out.push({ icon: "unlink", text: `${sp.name}: perdido, sin stream (los demás siguen)`, short: `${short}: perdido` });
    } else if (!playing && sp.connected === false) {
      out.push({ icon: "unlink", text: `${sp.name}: desconectado`, short: `${short}: desconectado` });
    }
    if (battery != null && battery <= LOW_BATTERY_PCT) {
      out.push({ icon: "battery", text: `${sp.name}: batería ${nf(battery, 0)} %`, short: `${short}: batería ${nf(battery, 0)} %` });
    }
    const drops = (radio[sp.name] || {}).drops_per_min;
    if (playing && drops > 0) {
      out.push({ icon: "radio", text: `${sp.name}: ${nf(drops, 0)} cortes de radio por minuto`, short: `${short}: radio` });
    }
  }
  return out;
}

function renderAlerts(s) {
  const alerts = alertsOf(s);
  const key = JSON.stringify(alerts);
  const list = $("now-alerts");
  if (list.dataset.key === key) return;
  list.dataset.key = key;
  list.hidden = alerts.length === 0;
  list.replaceChildren(...alerts.map((a) => el("li", { class: "alert" }, icon(a.icon, "alert-icon"), el("span", { text: a.text }))));
  const chip = $("chip-alert");
  chip.hidden = alerts.length === 0;
  chip.replaceChildren(...(alerts.length ? [icon(alerts[0].icon, "h-3.5 w-3.5"),
    el("span", { text: alerts[0].short + (alerts.length > 1 ? ` y ${alerts.length - 1} más` : "") })] : []));
}

// -- la sincronía para el oyente (spec 2026-10-02 §7.3.4) ---
// Un número, la desalineación residual (la mayor diferencia de llegada entre dos parlantes, sin
// lo que se puso a propósito), sobre una regla con las zonas de research/09 §3. Se muestra solo
// lo medido: hoy lo mide la calibración (`calibration.results[].delay_ms` es lo que faltaba
// corregir con las correcciones de ese momento). Si los retardos cambiaron después, ese número
// ya no vale y se dice "sin medición". El lazo mide lo mismo cada 20 s y el estado lo trae en
// `sync` (residual_ms, measured_at, age_s): se muestra la más nueva de las dos mediciones.
const SYNC_SCALE_MS = 15;
const SYNC_ZONES = [
  [2, "fused", "un solo sonido, entre los parlantes"],
  [5, "first", "un solo sonido, corrido hacia el que llega primero"],
  [10, "double", "en golpes secos puede oírse doble"],
  [Infinity, "haas", "zona de Haas: el más tardío se separa en los golpes; conviene calibrar"],
];

function syncMeasurement(s) {
  const loop = s.sync && s.sync.residual_ms != null ? s.sync : null;
  const cal = s.calibration;
  const calAt = cal && cal.state === "done" && cal.measured_at ? Date.parse(cal.measured_at) : null;
  if (loop && (calAt == null || Date.parse(loop.measured_at) >= calAt)) {
    return { ms: loop.residual_ms, note: `Medida por el lazo de recalibración hace ${duration(loop.age_s)}, con la música: lo que faltaba corregir en ese momento (el lazo lo corrige de a poco).` };
  }
  return calibrationSync(s, cal);
}

function calibrationSync(s, cal) {
  if (!cal || cal.state !== "done") {
    return { ms: null, note: "Calibrá para medirla." };
  }
  const good = cal.results.filter((r) => !r.silent && !r.doubtful && r.delay_ms != null);
  if (good.length < 2) return { ms: null, note: "La última calibración no midió con confianza dos parlantes o más." };
  const spread = Math.max(...good.map((r) => r.delay_ms)) - Math.min(...good.map((r) => r.delay_ms));
  const when = cal.measured_at ? `calibración de las ${cal.measured_at.slice(11, 16)}` : "última calibración";
  const moved = good.some((r) => {
    const sp = s.speakers.find((x) => x.name === r.speaker);
    return sp && r.applied_delay_ms != null && Math.abs(sp.delay_ms - r.applied_delay_ms) > 0.05;
  });
  if (moved) {
    return { ms: null, note: `Los retardos cambiaron después de la ${when} (que midió ${nf(spread, 1)} ms): calibrá otra vez para medir lo que queda.` };
  }
  const caveats = [cal.stale ? "de una sesión anterior" : "", cal.reliable ? "" : "medición dudosa"].filter(Boolean);
  return { ms: spread, note: `Medida en la ${when}${caveats.length ? ` (${caveats.join(", ")})` : ""}.` };
}

// La fila de sincronía se crea una vez (de su <template>) y se vuelve a poner al final de los datos.
let syncNodes = null;
function syncRow() {
  if (!syncNodes) syncNodes = [...$("now-sync-row").content.cloneNode(true).children];
  return syncNodes;
}

function renderSync(s) {
  syncRow();
  const { ms, note } = syncMeasurement(s);
  const box = syncNodes[1];
  const zone = ms == null ? null : SYNC_ZONES.find(([limit]) => ms < limit);
  box.dataset.zone = zone ? zone[1] : "none";
  setText(box.querySelector("#sync-value"), ms == null ? "sin medición" : `${nf(ms, 1)} ms`);
  setText(box.querySelector("#sync-phrase"), zone ? zone[2] : "");
  const pos = box.querySelector("#sync-pos");
  pos.hidden = ms == null;
  if (ms != null) pos.style.transform = `translateX(${((Math.min(ms, SYNC_SCALE_MS) / SYNC_SCALE_MS) * 100).toFixed(2)}%)`;
  const rec = s.recalibration;
  const drift = rec.drift_ms_h ? Math.max(...Object.values(rec.drift_ms_h).map(Math.abs)) : null;
  const trend = s.session.status !== "playing" ? ""
    : !rec.active ? "Deriva: sin corregir (el lazo está apagado)."
    : drift == null ? "Deriva: la corrige el lazo."
    : `Deriva estimada (INFERIDA): hasta ${nf(drift, 1)} ms/h, la corrige el lazo.`;
  // A la vista, solo lo que pide hacer algo (medir, o encender el lazo); el resto, en el título.
  const loopOff = s.session.status === "playing" && !rec.active;
  setText(box.querySelector("#sync-note"), [ms == null ? note : "", loopOff ? trend : ""].filter(Boolean).join(" "));
  box.title = [note, trend].filter(Boolean).join(" ");
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
    const applied = await withUndo(appliedMessage, () => send("calibration_apply"));
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
  xrun: "cut-xrun", input_gap: "cut-input", fade: "cut-fade", radio: "cut-radio" };
// La forma acompaña al color (WCAG 1.4.1); el CSS dibuja cada una.
const CUT_SHAPE = { "cut-fault": "círculo", "cut-xrun": "cuadrado", "cut-input": "triángulo", "cut-fade": "anillo", "cut-radio": "rombo" };
const CUT_CONTEXT = {
  loop_measuring: () => "el lazo medía",
  bt_discovering: () => "Bluetooth buscaba dispositivos",
  slow_order: (v) => `orden lenta: ${v}`,
  last_order: (v) => `después de «${v}»`,
};
const CUT_WINDOW_S = 600;

function renderCuts(s) {
  const c = s.health && s.health.cuts;
  renderRadio(s, c);
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
    const cls = CUT_CLASS[e.kind] || "cut-fault";
    const dot = el("span", { class: `cut-dot ${cls}`, "data-shape": CUT_SHAPE[cls], title: `${e.at.slice(11, 19)} · ${e.what}${e.where ? ` · ${e.where}` : ""}` });
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

// -- la radio, por parlante (spec 2026-10-02 §7.2) ---
// Un carril por parlante con los paquetes que su enlace descartó (cada uno, un corte audible de
// ~24-40 ms después de PipeWire, radio.py), los descartes por minuto y el bitpool de ahora. Verlos
// exige subir el registro de bluez5: un cambio de sistema, anotado con su reversión.

function renderRadio(s, c) {
  const radio = (live.alive && live.radio) || s.radio || { available: false, speakers: {} };
  const level = s.radio_log || { available: false, active: false };
  const button = $("radio-log");
  button.hidden = !level.available;
  button.disabled = Boolean(level.pending);
  setText(button, level.pending ? "Cambiando el registro…" : level.active ? "Desactivar registro de radio" : "Activar registro de radio");
  button.setAttribute("aria-pressed", String(Boolean(level.active)));
  const notes = [];
  if (!level.available) notes.push("Este servicio no maneja el registro de radio.");
  if (level.error) notes.push(`Error: ${level.error}.`);
  if (level.active && level.changes_file) notes.push(`Cambio anotado en ${level.changes_file}${level.mode === "heavy" ? " (registro completo: journald puede perder líneas)" : ""}.`);
  notes.push(radio.available
    ? `${radio.simulated ? "SIMULADO · " : ""}Leyendo el registro de bluez5${radio.since_s != null ? ` desde hace ${duration(radio.since_s)}` : ""}.`
    : (radio.reason || "Sin datos de la radio."));
  setText($("radio-note"), notes.join(" "));
  const events = c ? c.events.filter((e) => e.kind === "radio") : [];
  const links = radio.speakers || {};
  const names = s.speakers.map((sp) => sp.name);
  const extra = Object.keys(links).filter((k) => !names.includes(k));
  const rows = [...names, ...extra].map((name) => {
    const link = links[name];
    const lane = el("div", { class: "radio-track", role: "img" });
    const mine = events.filter((e) => e.where === name || e.where === `enlace sin identificar ${name}`);
    lane.setAttribute("aria-label", `${name}: ${mine.length} paquetes descartados en 10 minutos`);
    lane.append(...mine.map((e) => {
      const dot = el("span", { class: "cut-dot cut-radio", "data-shape": "rombo", title: `${e.at.slice(11, 19)} · ${e.detail || e.what}` });
      dot.style.left = `${Math.max(0, Math.min(100, (1 - (c.now - e.t) / CUT_WINDOW_S) * 100))}%`;
      return dot;
    }));
    const rate = link && link.drops_per_min != null ? `${nf(link.drops_per_min, 1)}/min` : "—";
    const pool = link && link.bitpool != null ? `bitpool ${link.bitpool}${link.bitpool_max != null ? ` de ${link.bitpool_max}` : ""}` : "bitpool —";
    return el("div", { class: "radio-lane", "data-radio-speaker": name },
      el("span", { class: "radio-name", title: name, text: extra.includes(name) ? `sin identificar ${name}` : name.replace("JBL ", "") }),
      lane,
      el("span", { class: "radio-rate num", title: "Descartes por minuto, en el último minuto", text: rate }),
      el("span", { class: "radio-pool num", title: "El bitpool que usa ahora el códec SBC (baja cuando el enlace no da abasto)", text: pool }));
  });
  // Sin registro ni datos, los carriles no dirían nada: solo el botón y su explicación.
  const lanes = $("radio-lanes");
  lanes.hidden = !radio.available && !level.active;
  lanes.replaceChildren(...(lanes.hidden ? [] : rows));
}

async function toggleRadioLog() {
  const level = latest && latest.radio_log;
  if (!level || !level.available || level.pending) return;
  // Sin confirm(): se deshace con el mismo botón, y lo que cambia está escrito al lado (research/11 §4.3).
  markPending($("radio-log"));
  await send("radio_log", { active: !level.active });
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
  const band = a.bandwidth_hz == null ? "—" : `${nf(a.bandwidth_hz / 1000, 1)} kHz`;
  const rows = [
    ["Tipo", a.kind],
    ["Correlación L/R", `${nf(a.correlation, 2)} (1 = mono)`],
    ["Lateral / central", `${nf(a.side_db, 1)} dB`],
    ["Balance L − R", `${nf(a.balance_db, 1)} dB`],
    ["Ancho de banda", band],
    ["Nivel", `RMS ${nf(a.rms_db, 1)} · pico ${nf(a.peak_db, 1)} dBFS`],
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

function drawInputLive(now) {
  const frame = live.input;
  const bars = $("input-bars");
  // Solo con un dato nuevo (llega a 10 Hz): antes se reescribían las 31 alturas en cada cuadro.
  if (!frame || !bars || frame === motion.inputFrame || motion.paused.input) return;
  if (motion.reduce.matches && now - motion.inputPaintAt < REDUCED_EVERY_MS) return;
  motion.inputFrame = frame;
  motion.inputPaintAt = now;
  if (bars.childElementCount !== frame.bands_db.length) {
    bars.replaceChildren(...frame.bands_db.map(() => el("div", { class: "input-bar" })));
  }
  frame.bands_db.forEach((db, i) => {
    const scale = Math.max(0, Math.min(1, (db + 80) / 80));
    bars.children[i].style.transform = `scaleY(${scale.toFixed(3)})`;
  });
  const c = frame.correlation;
  const marker = $("corr-marker");
  if (marker.hidden !== (c == null)) marker.hidden = c == null;
  if (c != null) marker.style.transform = `translateX(${(((c + 1) / 2) * 100).toFixed(2)}%)`;
  setText($("corr-value"), c == null ? "—" : nf(c, 2));
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
  for (const node of document.querySelectorAll("[data-restart]")) {
    const pending = s.config.pending_restart.includes(node.dataset.restart);
    node.textContent = pending ? "pendiente: reiniciá la sesión" : "al reiniciar";
    node.classList.toggle("pending", pending);
  }
}

// Los ajustes que cambian lo que suena (los otros esperan al reinicio y lo dicen).
const AUDIO_GLOBALS = new Set(["extract_ambience", "decorrelate", "eq_active"]);

function setupConfig() {
  for (const node of document.querySelectorAll("[data-global]")) {
    editable(node);
    const key = node.dataset.global;
    node.addEventListener("change", () => {
      if (AUDIO_GLOBALS.has(key)) markPending(node.closest(".toggle, .check"));
      let value = node.type === "checkbox" ? node.checked : node.value;
      if (node.type === "range" || node.dataset.number) value = Number(value);
      if (typeof value === "string") value = value.trim();
      setGlobal(node, { [key]: value });
    });
  }
  // Ajustes → Sonido se mudó a Cadena (spec 2026-10-02 §7.1): el enlace lleva allá.
  for (const link of document.querySelectorAll("[data-goto-card]")) {
    link.addEventListener("click", () => window.aurasyncShow(`[data-card="${link.dataset.gotoCard}"]`));
  }
}

// -- el nivel del micrófono antes de calibrar (research/11 §4.2: como Dirac Live) ----------
// Una pista de −80 a 0 dBFS con la ventana objetivo marcada, el número y el estado en palabras (y
// con una forma). Fuera de la ventana, «Calibrar» avisa y pide confirmar en vez de arrancar. El
// nivel es el que ya muestra Niveles («Micrófono»): lo mide el lazo, así que sin lazo no hay lectura.
// La ventana es ORIENTATIVA (INFERIDO, sin medir con el fifine): RMS de −55 a −10 dBFS y pico bajo
// −1 dBFS. Que el micrófono esté bajo solo se puede decir si algo suena (entrada sobre −45 dBFS):
// en silencio, −60 es el piso de ruido y no un error.
const MIC_SCALE_DB = -80;
const MIC_WINDOW = { low: -55, high: -10, clip: -1 };
const MIC_PLAYING_DB = -45;
const MIC_STATES = {
  none: "sin lectura", quiet: "sin música: no se puede comprobar", low: "demasiado bajo",
  ok: "en la ventana", high: "demasiado alto", clip: "saturado",
};
const MIC_ADVICE = {
  low: "Acercalo a los parlantes, subí su ganancia o el volumen: con tan poca señal la medición puede salir dudosa.",
  high: "Alejalo o bajá su ganancia: cerca de saturar, la medición se deforma.",
  clip: "Satura: alejalo o bajá su ganancia; una medición saturada no sirve.",
  quiet: "Poné música o la señal de prueba (Fuente) para ver si el micrófono oye los parlantes.",
};
const MIC_BLOCKING = new Set(["low", "high", "clip"]);

function micReading(s) {
  if (!s || s.session.status !== "playing") return { state: "none", why: "Iniciá la sesión para ver el nivel del micrófono." };
  const fresh = live.meters && performance.now() - live.metersAt < 500;
  const frame = (fresh ? live.meters.meters : s.meters) || {};
  const level = (name) => {
    const m = frame[name];
    if (!m) return null;
    const row = fresh ? meterRows.get(name) : null;
    return row && row.at ? { rms: row.rms, peak: Math.max(row.held, m.peak_db) } : { rms: m.rms_db, peak: m.peak_db };
  };
  const mic = level("mic");
  if (!mic) {
    const waiting = s.recalibration.active || s.recalibration.mic_check;
    return { state: "none", why: waiting
      ? "Esperando la primera lectura del micrófono…"
      : "El micrófono se abre solo unos segundos para medir su nivel: tocá «Medir el micrófono»." };
  }
  const input = Math.max(...["in L", "in R"].map((n) => (level(n) || { rms: SILENCE }).rms));
  const playing = input > MIC_PLAYING_DB;
  let state = "ok";
  if (mic.peak >= MIC_WINDOW.clip) state = "clip";
  else if (mic.rms > MIC_WINDOW.high) state = "high";
  else if (mic.rms < MIC_WINDOW.low) state = playing ? "low" : "quiet";
  return { state, rms: mic.rms, peak: mic.peak, playing };
}

// Para los tests: lo que el panel lee del micrófono en este instante (el aviso lo decide así).
window.aurasyncMicReading = () => gateReading();

function paintMicCheck() {
  const r = micReading(latest);
  if (MIC_BLOCKING.has(r.state)) lastBlocking = { reading: r, at: performance.now() };
  const box = $("mic-check");
  if (box.dataset.state !== r.state) box.dataset.state = r.state;
  const pos = $("mic-pos");
  const has = r.rms != null && r.rms > SILENCE;
  if (pos.hidden === has) pos.hidden = !has;
  if (has) {
    const pct = Math.max(0, Math.min(100, ((r.rms - MIC_SCALE_DB) / -MIC_SCALE_DB) * 100));
    const transform = `translateX(${pct.toFixed(1)}%)`;
    if (pos.style.transform !== transform) pos.style.transform = transform;
  }
  const value = has ? `${nf(r.rms, 0)} dBFS` : "—";
  setText($("mic-value"), value);
  setText($("mic-state-text"), MIC_STATES[r.state]);
  const track = $("mic-track");
  const text = has ? `${value}, ${MIC_STATES[r.state]} (ventana ${MIC_WINDOW.low} a ${MIC_WINDOW.high} dBFS)` : MIC_STATES[r.state];
  if (track.getAttribute("aria-valuetext") !== text) {
    track.setAttribute("aria-valuetext", text);
    track.setAttribute("aria-valuenow", String(has ? Math.max(MIC_SCALE_DB, Math.round(r.rms)) : MIC_SCALE_DB));
  }
  const note = r.why || MIC_ADVICE[r.state] || "";
  setText($("mic-note"), note);
  if ($("mic-note").hidden === Boolean(note)) $("mic-note").hidden = !note;
}

// Antes de calibrar: con el micrófono fuera de la ventana, avisa y pide confirmar (sin confirm()).
// La última lectura que pedía avisar, y cuándo: al terminar una calibración el lazo vuelve a empezar
// y por unos segundos no hay lectura; sin esto, el atajo calibraba sin avisar aunque el micrófono
// acabara de estar demasiado bajo (lo encontró el test instrumentado, 2026-10-04).
const GATE_MEMORY_MS = 15000;
let lastBlocking = null;

function gateReading() {
  const r = micReading(latest);
  if (MIC_BLOCKING.has(r.state)) lastBlocking = { reading: r, at: performance.now() };
  else if (r.state === "none" && lastBlocking && performance.now() - lastBlocking.at < GATE_MEMORY_MS) return lastBlocking.reading;
  return r;
}

function gateCalibration(box, go) {
  const r = gateReading();
  if (!MIC_BLOCKING.has(r.state)) {
    box.hidden = true;
    box.replaceChildren();
    return go();
  }
  const goBtn = el("button", { type: "button", class: "btn", "data-gate": "go", text: "Calibrar igual" });
  const cancel = el("button", { type: "button", class: "btn btn-ghost", "data-gate": "cancel", text: "Cancelar" });
  box.replaceChildren(
    el("p", { class: "m-0", "data-gate-text": "" }, el("b", { text: `El micrófono está ${MIC_STATES[r.state]}` }),
      ` (${nf(r.rms, 0)} dBFS; la ventana es de ${MIC_WINDOW.low} a ${MIC_WINDOW.high} dBFS). ${MIC_ADVICE[r.state]}`),
    el("div", { class: "row" }, goBtn, cancel));
  box.hidden = false;
  goBtn.addEventListener("click", () => { box.hidden = true; box.replaceChildren(); go(); });
  cancel.addEventListener("click", () => { box.hidden = true; box.replaceChildren(); });
  return null;
}

// -- calibración --------------------------------------------------------------------

// Con el lazo apagado (por defecto) nadie lee el micrófono: al mirar esta verificación se pide
// abrirlo unos segundos (`mic_check`), nunca de continuo: puede ser el micrófono del portátil.
// Se pide al entrar (o cuando se puede: sesión sonando, sin lazo ni calibración) y con el botón.
let micCheckEligible = false;

function micCheckPossible(s) {
  const cal = s.calibration;
  // Solo para alguien que mira el panel: una pestaña en segundo plano no abre el micrófono.
  return document.visibilityState === "visible"
    && s.session.status === "playing" && !s.recalibration.active
    && !(cal && ["running", "measuring"].includes(cal.state))
    && s.recalibration.mic_check !== undefined; // un servicio anterior no tiene `mic_check`
}

function askMicCheck() {
  if (api) api.raw({ op: "mic_check" });
}

function renderMicCheck(s) {
  const visible = !$("mic-check").closest("[data-view]")?.hidden;
  const possible = micCheckPossible(s);
  const eligible = visible && possible;
  if (eligible && !micCheckEligible) askMicCheck();
  micCheckEligible = eligible;
  const button = $("mic-recheck");
  const show = possible && !s.recalibration.mic_check;
  if (button.hidden === show) button.hidden = !show;
}

function renderCalibration(s) {
  renderMicCheck(s);
  paintMicCheck();
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
  $("eq-reset").disabled = eqClearing || !s.speakers.some((sp) => sp.eq_db);
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
  // Quién queda fuera de la calibración: los que no suenan ahora (virtuales, sin conectar, perdidos).
  const left = playing ? s.speakers.filter((sp) => sp.output && sp.output !== "playing") : [];
  const leftBox = $("cal-left-out");
  leftBox.hidden = left.length === 0;
  leftBox.textContent = left.length ? `Fuera de la calibración (no suenan): ${left.map((sp) => `${sp.name} (${SPEAKER_STATUS[sp.output][1]})`).join(", ")}.` : "";
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

const hz = (f) => (f >= 1000 ? `${nf(f / 1000, f >= 10000 ? 0 : 1)} kHz` : `${Math.round(f)} Hz`);

// Qué tercios de la respuesta medida se dibujan tenues (clase `low-coherence`), como el *blanking*
// de Smaart: ahí la medición es poco confiable (research/11 §4.1). El criterio es el **error
// estimado de cada tercio** (`results[].response_error_db`, 1σ en dB, null donde no se pudo
// calcular): tenue si pasa de 1 dB o falta. La γ² sola no sirve de umbral: con N parlantes
// sonando a la vez cae a ~1/N aunque la curva esté bien medida (experimentos/11, paso 2). Si la
// calibración no trae el error, se usa la γ² (`results[].coherence`) con 0,5 como respaldo.
const MAX_ERROR_DB = 1;
const LOW_COHERENCE = 0.5;
// Los parlantes que la leyenda escondió (con 8 curvas no se lee ninguna: se eligen 1 o 2).
const respHidden = new Set();

function isLowCoherence(result, k) {
  if (Array.isArray(result.response_error_db)) {
    const e = result.response_error_db[k];
    return e == null || e > MAX_ERROR_DB;
  }
  const g = Array.isArray(result.coherence) ? result.coherence[k] : null;
  return g != null && g < LOW_COHERENCE;
}

// Un tramo por tercio: del punto medio con el anterior al punto medio con el siguiente, así cada
// tercio se puede atenuar solo.
function bandPolylines(values, xs, y, result, color, name) {
  const pts = values.map((v, k) => (v == null ? null : [xs[k], y(v)]));
  const mid = (a, b) => [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
  const out = [];
  pts.forEach((p, k) => {
    if (!p) return;
    const left = k > 0 && pts[k - 1] ? mid(pts[k - 1], p) : p;
    const right = k + 1 < pts.length && pts[k + 1] ? mid(p, pts[k + 1]) : p;
    const low = isLowCoherence(result, k);
    out.push(svg("polyline", { class: `line measured band${low ? " low-coherence" : ""}`, "data-band": String(k),
      "data-speaker": name, points: [left, p, right].map((q) => `${q[0]},${q[1]}`).join(" "), style: `stroke: var(${color})` }));
  });
  return out;
}

// La respuesta de cada parlante: escala logarítmica en frecuencia, de −40 a +15 dB.
function renderResponse(cal, speakers) {
  const box = $("resp-chart");
  const results = (cal && cal.results) || [];
  $("resp-box").hidden = !(results.length && cal.response_hz);
  $("resp-empty").hidden = !$("resp-box").hidden;
  if ($("resp-box").hidden) return;
  const key = JSON.stringify([results.map((r) => [r.response_db, r.coherence, r.response_error_db]), speakers.map((sp) => sp.eq_db), [...respHidden]]);
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
  const xs = fs.map((f) => x(f));
  let anyCoherence = false;
  results.forEach((r) => {
    if (respHidden.has(r.speaker)) return;
    const i = Math.max(0, names.indexOf(r.speaker));
    const color = SERIES[i % SERIES.length];
    if (Array.isArray(r.coherence) || Array.isArray(r.response_error_db)) {
      anyCoherence = true;
      chart.append(...bandPolylines(r.response_db, xs, y, r, color, r.speaker));
      return;
    }
    const pts = r.response_db.map((v, k) => (v == null ? null : `${x(fs[k])},${y(v)}`)).filter(Boolean).join(" ");
    chart.append(svg("polyline", { class: "line measured", "data-speaker": r.speaker, points: pts, style: `stroke: var(${color})` }));
  });
  // La ecualización vigente de cada parlante, punteada.
  speakers.forEach((sp, i) => {
    if (!sp.eq_db || respHidden.has(sp.name)) return;
    const pts = sp.eq_db.map((v, k) => `${x(fs[k])},${y(v)}`).join(" ");
    chart.append(svg("polyline", { class: "line eq-line", points: pts, style: `stroke: var(${SERIES[i % SERIES.length]})` }));
  });
  box.append(chart);
  // Cada parlante de la leyenda es un botón: esconde o muestra su curva (con 8 se eligen 1 o 2).
  $("resp-legend").replaceChildren(...results.map((r) => {
    const i = Math.max(0, names.indexOf(r.speaker));
    const shown = !respHidden.has(r.speaker);
    const b = el("button", { type: "button", class: "legend-item legend-btn", "aria-pressed": String(shown),
      title: shown ? `Esconder la curva de ${r.speaker}` : `Mostrar la curva de ${r.speaker}` },
    el("span", { class: "legend-swatch", style: `background: var(${SERIES[i % SERIES.length]})` }), r.speaker);
    b.addEventListener("click", () => {
      if (respHidden.has(r.speaker)) respHidden.delete(r.speaker);
      else respHidden.add(r.speaker);
      if (latest) renderResponse(latest.calibration, latest.speakers);
    });
    return b;
  }), el("span", { class: "legend-item muted", text: `línea llena: medida · punteada: ecualización · gris: 0 dB${anyCoherence ? ` · tenue: coherencia baja (γ² < ${nf(LOW_COHERENCE, 1)}), poco confiable` : ""}` }));
}

// -- deshacer en vez de confirm() (research/11 §4.3; host/web/src/undo.ts) -----------------
// Cargar un preset, quitar un parlante y aplicar una calibración se hacen al instante, y el aviso
// «Deshacer» (10 s) vuelve a poner el estado de antes. Olvidar un dispositivo, borrar un preset y
// quitar la ecualización no tienen vuelta en el contrato: se muestran hechos y se mandan al vencer
// el aviso (o al irse de la página).

function undoer() { return (window.aurasync && window.aurasync.undo) || null; }

// El estado artístico de ahora: el del servicio (no `latest`, que puede venir un cuadro atrás) y
// las elecciones de la cadena.
async function captureNow() {
  const u = undoer();
  if (!u || !api) return null;
  const [state, chain] = await Promise.all([api.state(), api.raw({ op: "chain" })]);
  if (!state || !state.ok) return null;
  return u.capture(state.result, chain && chain.ok ? chain.result : null);
}

function offerRestore(message, before) {
  const u = undoer();
  if (!u || !before) return;
  u.offer({ message, undo: async () => {
    const now = await captureNow();
    return now ? u.restore(before, now) : ["sin conexión"];
  } });
}

async function withUndo(message, action) {
  const before = await captureNow();
  const reply = await action();
  if (reply && reply.ok) offerRestore(typeof message === "function" ? message(reply) : message, before);
  return reply;
}

function deferUndo(message, commit, revert) {
  const u = undoer();
  if (!u) { commit(); return; }
  u.offer({ message, commit, undo: async () => { await revert(); return []; } });
}

function appliedMessage(reply) {
  const skipped = reply.result.skipped || [];
  return `Alineación aplicada${skipped.length ? `; quedaron como estaban: ${skipped.join(", ")}` : ""}`;
}

// Quitar la ecualización: al instante, y «Deshacer» vuelve a escribir las curvas (`set` escribe
// `eq_db` desde el 2026-10-04; antes se borraban al vencer el aviso, research/10 §7.1).
let eqClearing = false;
async function clearEq() {
  eqClearing = true;
  $("eq-reset").disabled = true;
  try {
    await withUndo("Ecualización quitada", () => send("eq_reset"));
  } finally {
    eqClearing = false;
  }
}

// -- presets y A/B -------------------------------------------------------------------

// Los presets que se están borrando (el aviso «Deshacer» sigue abierto): ya no se muestran.
const hiddenPresets = new Set();

function renderPresets(s) {
  const list = $("presets");
  const presets = s.presets.filter((n) => !hiddenPresets.has(n));
  const key = `${presets.join("|")}#${s.preset}#${s.ab && s.ab.active}`;
  if (list.dataset.key !== key) {
    list.dataset.key = key;
    list.replaceChildren(...presets.map((name) => {
      const load = el("button", { type: "button", class: "small-btn", text: "Cargar" });
      // Cargar pisa los ajustes de cada parlante: «Deshacer» los vuelve a poner (research/11 §4.3).
      load.addEventListener("click", () => withUndo(`Preset «${name}» cargado`, () => send("preset_load", { name })));
      load.disabled = Boolean(s.ab && s.ab.active);
      const del = el("button", { type: "button", class: "ghost small-btn danger", text: "Borrar" });
      del.addEventListener("click", () => {
        // Un preset borrado no se puede volver a escribir desde el panel: se borra al vencer el aviso.
        hiddenPresets.add(name);
        if (latest) renderPresets(latest);
        const back = () => { hiddenPresets.delete(name); if (latest) renderPresets(latest); };
        deferUndo(`Preset «${name}» borrado`, async () => { await send("preset_delete", { name }); back(); }, back);
      });
      return el("li", { class: name === s.preset ? "current" : "" },
        el("span", { class: "preset-name", text: name + (name === s.preset ? " · actual" : "") }), load, del);
    }));
    if (presets.length === 0) list.append(el("li", { class: "muted small", text: "Sin presets guardados." }));
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
  // Por qué no se puede empezar, en vez de un botón gris sin explicación (probes/18-usabilidad).
  $("ab-why").textContent = active ? ""
    : s.presets.length < 2 ? `Hacen falta dos presets para comparar (hay ${s.presets.length}): guardá cómo suena ahora en Presets, cambiá algo y guardá otro.`
    : s.session.status !== "playing" ? "Iniciá la reproducción para comparar." : "";
  if (active) {
    for (const button of document.querySelectorAll("[data-ab]")) {
      button.setAttribute("aria-pressed", String(button.dataset.ab === ab.playing));
    }
    const last = ab.last ? ` · la última: ${ab.last.correct ? "acertaste" : "no"} (X era ${ab.last.truth.toUpperCase()})` : "";
    $("ab-score").textContent = `${ab.a} contra ${ab.b}: ${ab.correct} de ${ab.trials} aciertos${last}`;
    renderAbLoudness($("ab-loudness"), ab);
  }
  $("ab-result").hidden = !(ab && !ab.active);
  if (ab && !ab.active) {
    const n = ab.trials.length;
    const p = n ? binomialTail(ab.correct, n) : 1;
    $("ab-result").textContent = `Último A/B: ${ab.a} contra ${ab.b}, ${ab.correct} de ${n} aciertos. ` +
      (n === 0 ? "" : p < 0.05 ? `Significativo (p = ${nf(p, 3)}): la diferencia se oye.`
        : `No alcanza (p = ${nf(p, 2)}): con 20 intentos hacen falta 15 aciertos.`);
    if (ab.loudness_lu) {
      const box = el("span", { class: "ab-loudness block mt-1" });
      renderAbLoudness(box, ab);
      $("ab-result").append(box);
    }
  }
}

// El A/B con la sonoridad igualada (spec 2026-10-02 §7.3.5): el más fuerte suele ganar por ser más
// fuerte. Se muestra la sonoridad de corto plazo de A y de B (la mide el servicio mientras suena
// cada uno, nunca X) y su diferencia; sobre 0,5 LU, un aviso con forma y texto.
const AB_LOUDNESS_WARN_LU = 0.5;

function renderAbLoudness(node, ab) {
  const l = ab.loudness_lu || {};
  const lufs = (v) => (v == null ? "midiendo…" : `${nf(v, 1)} LUFS`);
  const parts = [el("span", { text: `Sonoridad: A ${lufs(l.a)} · B ${lufs(l.b)}` })];
  if (l.diff != null) {
    const off = Math.abs(l.diff) > AB_LOUDNESS_WARN_LU;
    parts.push(el("span", { class: off ? "ab-diff ab-diff-warn" : "ab-diff", "data-warn": off ? "1" : "0",
      text: ` · diferencia ${l.diff > 0 ? "+" : ""}${nf(l.diff, 1)} LU` }));
    if (off) {
      parts.push(el("span", { class: "ab-warn" }, el("span", { class: "q-shape", "data-shape": "triángulo", "aria-hidden": "true" }),
        ab.match_loudness
          ? ` Difieren más de ${nf(AB_LOUDNESS_WARN_LU, 1)} LU aun igualadas: la comparación favorece al más fuerte.`
          : ` Difieren más de ${nf(AB_LOUDNESS_WARN_LU, 1)} LU: el más fuerte suele parecer mejor. Empezá con «Igualar la sonoridad».`));
    }
  }
  const comp = Object.entries(ab.compensation_db || {}).filter(([, db]) => Math.abs(db) > 0.05);
  if (ab.match_loudness) {
    parts.push(el("span", { class: "muted", text: comp.length
      ? ` · igualada: ${comp.map(([w, db]) => `${w.toUpperCase()} ${nf(db, 1)} dB`).join(", ")}`
      : " · igualada (sin diferencia que compensar todavía)" }));
  }
  if (l.a == null || l.b == null) {
    parts.push(el("span", { class: "muted", text: " · cada uno se mide 3 s después de empezar a sonar" }));
  }
  node.replaceChildren(...parts);
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
  if (stopped) return;
  if (live.alive || document.hidden) { setTimeout(pollLogs, LOGS_EVERY_MS); return; }
  const reply = await api.raw({ op: "logs", since: logSince, limit: 500 });
  // Sin respuesta, el estado ya muestra la desconexión.
  if (reply && reply.ok) ingestLogs(reply.result);
  setTimeout(pollLogs, LOGS_EVERY_MS);
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
  // Los botones los dibuja renderRoomLayouts: un solo oyente en el grupo.
  $("room-layouts").addEventListener("click", (event) => {
    const button = event.target.closest("[data-room-layout]");
    if (button && latest && latest.global.layout !== button.dataset.roomLayout) send("set", { changes: { layout: button.dataset.roomLayout } });
  });
  const kind = editable($("source-kind"));
  const app = editable($("source-app"));
  const file = editable($("source-file"));
  kind.addEventListener("change", () => {
    syncSourceFields();
    if (kind.value === "app") app.focus();
    else if (kind.value === "file" || kind.value === "multichannel") file.focus();
    else sendFrom(kind, "source", { kind: kind.value });
  });
  app.addEventListener("change", () => { if (app.value) sendFrom(app, "source", { kind: "app", name: app.value }); });
  file.addEventListener("change", () => { const v = file.value.trim(); if (v) sendFrom(file, "source", { kind: kind.value === "multichannel" ? "multichannel" : "file", name: v }); });
  const volume = $("volume");
  liveRange(volume, $("volume-out"), (v) => `${nf(v, 0)} dB`, (v) => { markPending($("volume-out")); return setGlobal(volume, { volume_db: v }); });
  $("scan").addEventListener("click", () => send("scan"));
  $("add-virtual").addEventListener("click", () => send("speaker_add_virtual"));
  $("save").addEventListener("click", async () => {
    const reply = await send("save");
    if (reply && reply.ok) toast(`Instalación guardada en ${reply.result.path}`);
  });
  editable($("cal-mic"));
  $("cal-mic").addEventListener("change", () => sendFrom($("cal-mic"), "microphone_set", { node: $("cal-mic").value || null }));
  $("mic-recheck").addEventListener("click", askMicCheck);
  document.addEventListener("visibilitychange", () => { if (latest) renderMicCheck(latest); });
  $("cal-run").addEventListener("click", () => {
    const cal = latest && latest.calibration;
    if (cal && ["running", "measuring"].includes(cal.state)) send("calibrate_cancel");
    else gateCalibration($("cal-gate"), () => send("calibrate", { seconds: Number($("cal-seconds").value), amplitude: Number($("cal-amplitude").value) }));
  });
  $("cal-apply").addEventListener("click", () => withUndo(appliedMessage, () => send("calibration_apply")));
  $("eq-apply").addEventListener("click", async () => {
    const reply = await send("eq_apply");
    if (reply && reply.ok) toast(reply.result.skipped.length ? `Ecualizado; sin cambios: ${reply.result.skipped.join(", ")}` : "Ecualizado. Calibrá otra vez para ver cuánto se aplanó.");
  });
  $("eq-reset").addEventListener("click", clearEq);
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
  $("ab-start").addEventListener("click", () => send("ab_start", { a: $("ab-a").value, b: $("ab-b").value, match_loudness: $("ab-match").checked }));
  for (const button of document.querySelectorAll("[data-ab]")) {
    button.addEventListener("click", () => send("ab_play", { which: button.dataset.ab }));
  }
  for (const button of document.querySelectorAll("[data-answer]")) {
    button.addEventListener("click", () => send("ab_answer", { x_is: button.dataset.answer }));
  }
  $("ab-stop").addEventListener("click", () => send("ab_stop"));
  $("radio-log").addEventListener("click", toggleRadioLog);
  $("pair-open").addEventListener("click", async () => {
    $("pair-dialog").showModal();
    $("pair-qr").hidden = true;
    $("pair-note").textContent = "Cargando el código…";
    const url = api ? await api.imageUrl("/pairing.svg") : null;
    if (url) {
      $("pair-qr").src = url;
      $("pair-qr").hidden = false;
      $("pair-note").textContent = "El código abre la app y pide acceso; no lleva ningún token.";
    } else {
      $("pair-note").textContent = "Para el teléfono hace falta HTTPS: poné \"tls\": true en service.json y reiniciá el servicio.";
    }
  });
  setupConfig();
  setupLogs();
  setupEstimator();
  setupSpatial();
  setupMonitor();
  $("now-calibrate").addEventListener("click", () => gateCalibration($("now-gate"), calibrateAndApply));
  $("chip-quality").addEventListener("click", () => window.aurasyncShow("[data-card=cuts]"));
  $("now-identify").addEventListener("click", identifySpeakers);
  $("chip-alert").addEventListener("click", () => {
    window.aurasyncShow("[data-card=now]");
    $("now-alerts").scrollIntoView({ block: "nearest" });
  });
  $("levels-pause").addEventListener("click", () => setPaused("levels", !motion.paused.levels));
  $("input-pause").addEventListener("click", () => setPaused("input", !motion.paused.input));
  document.addEventListener("visibilitychange", onVisibility);
  document.addEventListener("aurasync:toast", (e) => toast(e.detail));
  document.addEventListener("aurasync:access", onAccess);
  requestAnimationFrame(animateMeters);
  // El transporte lo pone cadena.js (se carga después de este archivo, antes de DOMContentLoaded).
  // En la PWA llega cuando hay un equipo elegido.
  if (!window.aurasync) {
    setOnline(false, "No se cargó cadena.js, que conecta el panel con el equipo: recargá la página.");
    return;
  }
  window.aurasync.ready.then((ready) => {
    api = ready;
    pollState();
    pollLogs();
    openStream();
  });
}

// -- sincronía sugerida (spec 2026-10-03) -----------------------------------------
// El estimador base del servicio junta las mediciones de todos los micrófonos y sugiere retardos
// absolutos; nada cambia hasta "Aplicar". Cada perilla dice su recomendación, cómo cambia el
// sonido y una figura SIMULADA, una por una y en conjunto (d-7c8794-0a4586).

let estLast = null;
let estExplainLoading = 0;

const SERIES_STYLE = {
  // El trazo distingue las series, no solo el color (WCAG 1.4.1).
  recommended: { stroke: "#0284c7", width: 2.5, dash: "" },
  current: { stroke: "#d97706", width: 2, dash: "6 3" },
  other: { stroke: "#a1a1aa", width: 1.5, dash: "2 3" },
};

function fmtMs(v) {
  return v === null || v === undefined ? "—" : Number(v).toFixed(2);
}

function renderEstimator(s) {
  const b = s.sync_suggestion || null;
  estLast = b;
  const has = !!(b && b.delays_ms && Object.keys(b.delays_ms).length);
  $("est-apply").disabled = !has || b.applied;
  $("est-applied").hidden = !(b && b.applied);
  $("est-table").hidden = !has;
  if (!has) {
    const why = b && b.reason ? b.reason : "falta una medición de al menos dos parlantes";
    $("est-reason").textContent = `Todavía no hay sugerencia: ${why}.`;
    $("est-rows").replaceChildren();
    $("est-summary").textContent = "";
    return;
  }
  $("est-reason").textContent = b.reason ? `Aviso: ${b.reason}.` : "";
  const rows = Object.entries(b.delays_ms).map(([name, delay]) => {
    const now = b.current_ms[name];
    const change = now === undefined ? null : delay - now;
    return el("tr", {},
      el("td", { text: name }),
      el("td", { class: "num", text: fmtMs(now) }),
      el("td", { class: "num", text: fmtMs(delay) }),
      el("td", { class: "num", text: change === null ? "—" : `${change >= 0 ? "+" : ""}${change.toFixed(2)}` }),
      el("td", { class: "num", text: fmtMs(b.sigma_ms[name]) }));
  });
  $("est-rows").replaceChildren(...rows);
  const parts = [];
  if (b.spread_now_ms !== null) parts.push(`Desalineación estimada ahora: ${fmtMs(b.spread_now_ms)} ms`);
  if (b.spread_after_ms !== null) parts.push(`después de aplicar: ±${fmtMs(b.spread_after_ms)} ms`);
  parts.push(`alineado para «${b.anchor}»`);
  parts.push(`${b.based_on} mediciones`);
  $("est-summary").textContent = `${parts.join(" · ")}.`;
}

async function applyEstimator() {
  if (!estLast) return;
  const reply = await send("sync_apply", { suggestion_id: estLast.id });
  if (reply && reply.ok) toast(`Sugerencia ${estLast.id} aplicada.`);
}

async function loadExplain(expect = null) {
  const token = ++estExplainLoading;
  for (let i = 0; i < 60 && token === estExplainLoading; i++) {
    const reply = await send("sync_explain");
    const e = reply && reply.ok ? reply.result : null;
    const fresh = e && e.docs && (!expect || Object.entries(expect).every(([k, v]) => e.settings[k] === v));
    if (fresh) {
      renderExplain(e);
      return;
    }
    await new Promise((r) => setTimeout(r, 1000));
  }
}

function settingControl(id, doc, value) {
  const choices = Object.keys(doc.sounds_choices || {});
  let input;
  if (choices.length) {
    input = el("select", { "aria-label": doc.title });
    for (const c of choices) {
      const opt = el("option", { value: c, text: c });
      if (String(value) === c) opt.selected = true;
      input.append(opt);
    }
  } else {
    input = el("input", { type: "number", step: "any", class: "num-input", value: String(value), "aria-label": doc.title });
  }
  input.addEventListener("change", async () => {
    let v = input.value;
    if (choices.length) v = v === "True" ? true : v === "False" ? false : v;
    else v = Number(v);
    const reply = await send("sync_set", { changes: { [id]: v } });
    if (reply && reply.ok) loadExplain({ [id]: v });
  });
  return input;
}

function renderExplain(e) {
  $("est-together-text").textContent = e.together.text;
  $("est-together-fig").replaceChildren(drawFigure(e.together.figure));
  const nodes = Object.entries(e.docs).map(([id, doc]) => {
    const value = e.settings[id];
    const rec = typeof doc.recommended === "number" ? `${doc.recommended} ${doc.unit || ""}`.trim() : String(doc.recommended);
    const box = el("div", { "data-setting": id, class: "setting" },
      el("div", { class: "row" }, el("strong", { text: doc.title }), settingControl(id, doc, value)),
      el("p", { class: "muted small", text: doc.summary }),
      el("p", { class: "small est-recommended", text: `Recomendado: ${rec}. ${doc.why_recommended}` }));
    const choices = Object.entries(doc.sounds_choices || {});
    if (choices.length) {
      box.append(el("ul", { class: "small" }, ...choices.map(([c, t]) => el("li", { text: `${c}: ${t}` }))));
    } else {
      box.append(el("p", { class: "small", text: `Más bajo: ${doc.sounds_low}` }));
      box.append(el("p", { class: "small", text: `Más alto: ${doc.sounds_high}` }));
    }
    box.append(el("p", { class: "muted small", text: doc.help }));
    if (doc.figure) {
      box.append(drawFigure(doc.figure));
      box.append(el("span", { class: "chip chip-warn est-evidence", text: doc.figure.evidence }));
    }
    return box;
  });
  $("est-settings").replaceChildren(...nodes);
}

function drawFigure(fig) {
  if (fig.kind === "room") return drawRoom(fig);
  const W = 320, H = 140, L = 34, B = 20, T = 8, R = 8;
  const all = fig.series.flatMap((s) => s.points).filter((p) => p[1] !== null);
  const xs = all.map((p) => p[0]), ys = all.map((p) => p[1]);
  const x0 = Math.min(...xs, 0), x1 = Math.max(...xs, 1);
  const y1 = Math.max(...ys, 0.01) * 1.1;
  const X = (x) => L + ((x - x0) / (x1 - x0 || 1)) * (W - L - R);
  const Y = (y) => H - B - (y / y1) * (H - B - T);
  const root = svg("svg", { viewBox: `0 0 ${W} ${H}`, width: "100%", role: "img", class: "mt-1" });
  const title = svg("title");
  title.textContent = fig.caption;
  root.append(title);
  root.append(svg("line", { x1: L, y1: H - B, x2: W - R, y2: H - B, stroke: "currentColor", "stroke-opacity": "0.4" }));
  root.append(svg("line", { x1: L, y1: T, x2: L, y2: H - B, stroke: "currentColor", "stroke-opacity": "0.4" }));
  const yl = svg("text", { x: 2, y: T + 8, "font-size": "9", fill: "currentColor" });
  yl.textContent = `${y1.toFixed(2)} ms`;
  root.append(yl);
  if (fig.kind !== "bars") {
    const xl = svg("text", { x: W - R, y: H - 4, "font-size": "9", fill: "currentColor", "text-anchor": "end" });
    xl.textContent = fig.x;
    root.append(xl);
  }
  // Barras: una franja por lugar (categoría), las series lado a lado dentro de ella, y el nombre
  // del lugar debajo. Las marcas de una línea de tiempo son líneas verticales.
  const cats = [...new Set(fig.series.flatMap((s) => s.points.map((p) => p[0])))].sort((a, b) => a - b);
  const band = (W - L - R) / Math.max(cats.length, 1);
  const bw = (band * 0.8) / Math.max(fig.series.length, 1);
  for (const m of fig.marks || []) {
    if (fig.kind === "bars") {
      const ci = cats.indexOf(m.x);
      if (ci < 0) continue;
      const t = svg("text", { x: L + ci * band + band / 2, y: H - 6, "font-size": "9", fill: "currentColor", "text-anchor": "middle" });
      t.textContent = m.label;
      root.append(t);
    } else {
      root.append(svg("line", { x1: X(m.x), y1: T, x2: X(m.x), y2: H - B, stroke: "currentColor", "stroke-dasharray": "1 3" }));
    }
  }
  fig.series.forEach((s, k) => {
    const st = SERIES_STYLE[s.style] || SERIES_STYLE.other;
    if (fig.kind === "bars") {
      for (const [x, y] of s.points) {
        if (y === null) continue;
        const left = L + cats.indexOf(x) * band + band * 0.1 + k * bw;
        root.append(svg("rect", { x: left.toFixed(1), y: Y(y), width: Math.max(bw - 2, 1).toFixed(1), height: H - B - Y(y),
          fill: st.stroke, "fill-opacity": s.style === "other" ? "0.4" : "0.85", stroke: st.stroke, "stroke-dasharray": st.dash }));
      }
      return;
    }
    let path = [];
    const flush = () => {
      if (path.length > 1) root.append(svg("polyline", { points: path.join(" "), fill: "none", stroke: st.stroke,
        "stroke-width": st.width, "stroke-dasharray": st.dash }));
      path = [];
    };
    for (const [x, y] of s.points) {
      if (y === null) flush();
      else path.push(`${X(x).toFixed(1)},${Y(y).toFixed(1)}`);
    }
    flush();
  });
  const legend = el("ul", { class: "muted small flex flex-wrap gap-x-4" },
    ...fig.series.map((s) => el("li", { text: `${s.style === "recommended" ? "━" : s.style === "current" ? "╍" : "┈"} ${s.label}` })));
  return el("figure", {}, root, el("figcaption", { class: "muted small", text: fig.caption }), legend);
}

function setupEstimator() {
  $("est-apply").addEventListener("click", applyEstimator);
  $("est-explain").addEventListener("toggle", () => { if ($("est-explain").open) loadExplain(); });
  $("est-here").addEventListener("click", measureHere);
}

// Medir desde este dispositivo (sync/fromHere.ts): 8 s de micrófono, medidos acá contra la sonda.
// La sonda sube en 50 ms y el anillo del servidor tiene que tener lo que se va a grabar: un respiro.
const PROBE_SETTLE_MS = 2500;
async function measureHere() {
  const button = $("est-here");
  const note = $("est-here-note");
  const run = window.aurasync && window.aurasync.syncHere;
  if (!run) { note.textContent = "Este panel no puede medir desde aquí."; return; }
  button.disabled = true;
  if (!(latest && latest.global && latest.global.probe)) {
    // Se mide contra la sonda: se enciende acá mismo en vez de mandar a Ajustes (probes/18-usabilidad).
    note.dataset.state = "running";
    note.textContent = "Encendiendo la sonda (se mide contra ella; queda encendida, se apaga en Ajustes)…";
    const reply = await send("set", { changes: { probe: true } });
    if (!reply || !reply.ok) { note.dataset.state = "failed"; note.textContent = "No se pudo encender la sonda."; button.disabled = false; return; }
    await new Promise((r) => setTimeout(r, PROBE_SETTLE_MS));
  }
  note.dataset.state = "running";
  note.textContent = "Grabando 8 s… quedate quieto y en silencio.";
  try {
    const result = await run({ role: $("est-here-role").value });
    note.textContent = result.message;
    note.dataset.state = result.ok ? "done" : "failed";
  } catch (err) {
    note.textContent = `No se pudo medir: ${err && err.message ? err.message : err}`;
    note.dataset.state = "failed";
  } finally {
    button.disabled = false;
  }
}

// -- espacial (spec 2026-10-04) ------------------------------------------------------
// Cada parlante principal o ambiental, el modo clásico/espacial con sus perillas explicadas
// (recomendación, cómo suena, figura SIMULADA) y la pieza vista desde arriba.

let spatialKey = null;
let spatialLoading = 0;

function renderSpatial(s) {
  const kinds = s.speakers.map((sp) => `${sp.name}:${sp.role_kind}:${sp.pan}:${sp.ambience}`).join("|");
  const render = (s.chain_summary && s.chain_summary.spatial) || "classic";
  for (const id of ["spatial-render", "now-render"]) if (!$(id).matches(":focus")) $(id).value = render;
  const rows = s.speakers.map((sp) => {
    const kind = sp.role_kind || "principal";
    const seg = (value, label) => {
      const b = el("button", { type: "button", class: "seg", "data-role-kind": value, "aria-pressed": String(kind === value), text: label });
      b.addEventListener("click", () => send("set", { speaker: sp.name, changes: { role_kind: value } }));
      return b;
    };
    return el("li", { "data-speaker": sp.name, class: "row" }, el("span", { text: sp.name }), seg("principal", "Principal"), seg("ambient", "Ambiental"));
  });
  $("spatial-speakers").replaceChildren(...rows);
  const key = `${kinds}#${render}`;
  if (key !== spatialKey) {
    spatialKey = key;
    loadSpatial();
  }
}

async function loadSpatial() {
  const token = ++spatialLoading;
  for (let i = 0; i < 60 && token === spatialLoading; i++) {
    // Una pestaña oculta no pregunta: espera a volver a verse, sin gastar intentos.
    if (document.hidden) {
      i--;
      await new Promise((r) => setTimeout(r, 1000));
      continue;
    }
    const reply = await send("spatial_explain");
    const e = reply && reply.ok ? reply.result : null;
    if (e && e.docs) {
      renderSpatialExplain(e);
      return;
    }
    await new Promise((r) => setTimeout(r, 1000));
  }
}

function spatialParam(name, value) {
  return send("chain_set", { stage: "spatial", params: { [name]: value } });
}

function renderSpatialExplain(e) {
  if (!$("spatial-character").matches(":focus")) $("spatial-character").value = String(e.params.character);
  $("spatial-room").replaceChildren(drawFigure(e.room));
  const nodes = Object.entries(e.docs).filter(([id]) => id !== "render").map(([id, doc]) => {
    const value = e.params[id];
    let input;
    if (typeof value === "boolean") {
      input = el("input", { type: "checkbox", "aria-label": doc.title });
      input.checked = value;
      input.addEventListener("change", () => spatialParam(id, input.checked));
    } else {
      input = el("input", { type: "number", step: "any", class: "num-input", value: String(value), "aria-label": doc.title });
      input.addEventListener("change", () => spatialParam(id, Number(input.value)));
    }
    const rec = typeof doc.recommended === "number" ? `${doc.recommended} ${doc.unit || ""}`.trim() : String(doc.recommended);
    const box = el("div", { "data-setting": id, class: "setting" },
      el("div", { class: "row" }, el("strong", { text: doc.title }), input),
      el("p", { class: "muted small", text: doc.summary }),
      el("p", { class: "small est-recommended", text: `Recomendado: ${rec}. ${doc.why_recommended}` }));
    const choices = Object.entries(doc.sounds_choices || {});
    if (choices.length) box.append(el("ul", { class: "small" }, ...choices.map(([c, t]) => el("li", { text: `${c}: ${t}` }))));
    else {
      box.append(el("p", { class: "small", text: `Más bajo: ${doc.sounds_low}` }));
      box.append(el("p", { class: "small", text: `Más alto: ${doc.sounds_high}` }));
    }
    if (doc.figure) {
      box.append(drawFigure(doc.figure));
      box.append(el("span", { class: "chip chip-warn est-evidence", text: doc.figure.evidence }));
    }
    return box;
  });
  // El modo también se explica, arriba de todo.
  const render = e.docs.render;
  const head = el("div", { "data-setting": "render", class: "setting" },
    el("strong", { text: render.title }),
    el("p", { class: "small est-recommended", text: `Recomendado: ${render.recommended}. ${render.why_recommended}` }),
    el("ul", { class: "small" }, ...Object.entries(render.sounds_choices).map(([c, t]) => el("li", { text: `${c}: ${t}` }))),
    drawFigure(render.figure),
    el("span", { class: "chip chip-warn est-evidence", text: render.figure.evidence }));
  $("spatial-docs").replaceChildren(head, ...nodes);
}

function drawRoom(fig) {
  // La pieza desde arriba: el oyente al centro, 0° arriba, positivos a la derecha.
  const W = 220, C = 110, R = 80;
  const root = svg("svg", { viewBox: `0 0 ${W} ${W}`, width: "220", role: "img" });
  const title = svg("title");
  title.textContent = fig.caption;
  root.append(title);
  root.append(svg("circle", { cx: C, cy: C, r: R, fill: "none", stroke: "currentColor", "stroke-opacity": "0.3" }));
  root.append(svg("circle", { cx: C, cy: C, r: 5, fill: "currentColor" }));
  const ambients = fig.series.filter((s) => s.points[0][0] === null);
  let k = 0;
  for (const s of fig.series) {
    let a = s.points[0][0];
    let r = R;
    if (a === null) {
      a = 45 + (360 * k) / Math.max(ambients.length, 1);
      k += 1;
      r = R + 18;
    }
    const t = (a * Math.PI) / 180;
    const x = C + r * Math.sin(t), y = C - r * Math.cos(t);
    const principal = s.style !== "other";
    root.append(svg("circle", { cx: x.toFixed(1), cy: y.toFixed(1), r: 8, "data-speaker": s.label,
      fill: principal ? "#0284c7" : "none", stroke: principal ? "#0284c7" : "#a1a1aa", "stroke-dasharray": principal ? "" : "3 2", "stroke-width": 2 }));
    const label = svg("text", { x: x.toFixed(1), y: (y + 20).toFixed(1), "font-size": "9", fill: "currentColor", "text-anchor": "middle" });
    label.textContent = `${s.label}${principal ? "" : " (ambiental)"}`;
    root.append(label);
  }
  return el("figure", {}, root, el("figcaption", { class: "muted small", text: fig.caption }));
}

function setupSpatial() {
  for (const id of ["spatial-render", "now-render"]) $(id).addEventListener("change", () => send("chain_set", { stage: "spatial", algorithm: $(id).value }));
  $("spatial-character").addEventListener("change", () => spatialParam("character", Number($("spatial-character").value)));
  $("spatial-all-principal").addEventListener("click", () => {
    for (const p of (window.aurasyncLastSpeakers || [])) send("set", { speaker: p, changes: { role_kind: "principal" } });
  });
}

// -- monitor de audífonos (spec 2026-10-04-headphone-monitor-design.md) ---------------
// Qué se oye, en qué salida y a qué nivel; y lo que PipeWire hizo de verdad (se comprueba con
// pw-dump después de abrir: un destino pedido no es un destino logrado).

function monitorStateText(m) {
  const name = (node) => ((m.candidates || []).find((c) => c.node === node) || {}).description || node;
  if (m.state === "off") return "Apagado.";
  if (m.state === "waiting") return `Elegido ${name(m.target)}: se enciende al reproducir.`;
  if (m.state === "opening") return "Abriendo…";
  if (m.state === "failed") return `No se pudo: ${m.error}`;
  if (!m.reached) return `PipeWire lo mandó a ${m.routed_to ? name(m.routed_to) : "ninguna salida"}, no a ${name(m.target)}.`;
  const cushion = m.cushion_ms == null ? "" : ` Colchón ${Math.round(m.cushion_ms)} ms · rellenos ${m.refills || 0}${m.refills ? " (la salida se quedó sin audio y se rellenó)" : ""}${m.trims ? ` · recortes ${m.trims}` : ""}.`;
  return `Llega a ${name(m.target)}.${m.drops ? ` Se descartaron ${m.drops} bloques: la salida no da abasto.` : ""}${cushion}${monitorMatchText(m)}`;
}

// Los modos se oyen al mismo volumen: la mezcla y el binaural se igualan a la entrada al volumen
// elegido (loudness_match.py). Un servicio anterior no manda `match`: no se dice nada.
function monitorMatchText(m) {
  if (!m.match) return "";
  if (m.match === "unmeasured") return " Compensación binaural sin medir para este HRTF.";
  if (m.match === "measuring") return " Igualando…";
  // `frozen`: una pausa, un corte, una calibración o todos los parlantes en silencio.
  return ` Nivel igualado: ${nf(m.makeup_db, 1)} dB${m.match === "frozen" ? " (en espera)" : ""}.`;
}

function monitorLevelText(value, device) {
  return `${Math.round(Number(value))} ${device ? "%" : "dB"}`;
}

function renderMonitor(s) {
  const m = s.monitor;
  if (!m) return;
  const select = $("monitor-target");
  const key = JSON.stringify([m.candidates, m.target]);
  if (select.dataset.key !== key && !isEditing(select)) {
    select.dataset.key = key;
    const known = (m.candidates || []).some((c) => c.node === m.target);
    select.replaceChildren(
      el("option", { value: "", text: "elegí una salida" }),
      ...(m.candidates || []).map((c) => el("option", { value: c.node, text: c.description })),
      ...(m.target && !known ? [el("option", { value: m.target, text: `${m.target} (no está)` })] : []),
    );
    select.value = m.target || "";
  }
  if (!isEditing($("monitor-mode"))) $("monitor-mode").value = m.mode;
  // Un servicio anterior no manda `volume_control`: es por software, como era.
  const control = m.volume_control || "software";
  if (!isEditing($("monitor-volume-control"))) $("monitor-volume-control").value = control;
  const gain = $("monitor-gain");
  // Del audífono: el nivel es el volumen real de la salida, en %, leído de vuelta (también lo
  // que se cambió con los botones). Por software: la ganancia, en dB.
  const device = control === "device";
  if (gain.dataset.control !== control) {
    gain.dataset.control = control;
    gain.min = device ? "0" : "-40";
    gain.max = device ? "100" : "0";
  }
  if (!isEditing(gain)) {
    const real = device ? (m.device_volume_pct ?? m.device_volume_set_pct) : m.gain_db;
    if (real != null) gain.value = String(real);
  }
  $("monitor-gain-value").textContent = monitorLevelText(gain.value, device);
  const state = $("monitor-state");
  state.textContent = monitorStateText(m) + (device && m.device_volume_reason ? ` Volumen del audífono: ${m.device_volume_reason}.` : "");
  state.dataset.state = m.state === "on" && !m.reached ? "elsewhere" : m.state;
  state.classList.toggle("warn", state.dataset.state === "failed" || state.dataset.state === "elsewhere");
}

// Solo el deslizador manda un nivel: cambiar el modo, la salida o quién controla el volumen
// nunca lo lleva. Si lo llevara, el volumen leído de una salida (subido con sus botones) se
// le pediría a la nueva, que subiría sola (revisión, 2026-10-05).
function sendMonitor(extra = {}) {
  const mode = $("monitor-mode").value;
  const target = $("monitor-target").value || null;
  if (mode !== "off" && !target) {
    $("monitor-state").textContent = "Elegí una salida primero.";
    return;
  }
  sendFrom($("monitor-state"), "monitor_set", { mode, target, ...extra });
}

// La unidad que el deslizador muestra ahora (la del estado), no la del selector: recién cambiado
// el selector, el deslizador sigue en la unidad anterior hasta el próximo estado.
function monitorSliderIsDevice() {
  return ($("monitor-gain").dataset.control || $("monitor-volume-control").value) === "device";
}

function setupMonitor() {
  $("monitor-mode").addEventListener("change", () => sendMonitor());
  $("monitor-target").addEventListener("change", () => { if ($("monitor-mode").value !== "off") sendMonitor(); });
  $("monitor-volume-control").addEventListener("change", () => sendMonitor({ volume_control: $("monitor-volume-control").value }));
  $("monitor-gain").addEventListener("input", () => {
    $("monitor-gain-value").textContent = monitorLevelText($("monitor-gain").value, monitorSliderIsDevice());
  });
  $("monitor-gain").addEventListener("change", () => {
    if ($("monitor-mode").value === "off") return;
    const level = Number($("monitor-gain").value);
    sendMonitor(monitorSliderIsDevice() ? { device_volume_pct: level } : { gain_db: level });
  });
}

document.addEventListener("DOMContentLoaded", setup);
