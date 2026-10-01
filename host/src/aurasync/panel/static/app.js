// Panel de control de aurasync (docs/research/09). Sin build ni dependencias.
// Los textos que vienen del motor o del log se insertan con textContent, nunca como HTML.
"use strict";

const $ = (id) => document.getElementById(id);

const CHANNEL_POSITIONS = {
  quad: { FL: [22, 28], FR: [78, 28], RL: [22, 78], RR: [78, 78] },
  lcrs: { FL: [18, 28], FC: [50, 24], FR: [82, 28], RC: [50, 80] },
};
const CHANNEL_NAMES = {
  FL: "frontal izquierdo", FR: "frontal derecho", FC: "centro",
  RL: "trasero izquierdo", RR: "trasero derecho", RC: "trasero (surround)",
};
const SPEAKER_STATUS = {
  synced: ["✓", "sincronizado"],
  seen: ["◌", "visto, sin sincronizar"],
  lost: ["✕", "perdido"],
  unseen: ["?", "no visto"],
};
const SERVICE_STATUS = {
  running: ["✓", "corriendo"],
  starting: ["◐", "iniciando"],
  stopping: ["◑", "deteniendo"],
  stopped: ["○", "detenido"],
  failed: ["✕", "falló"],
  unavailable: ["–", "no disponible"],
};
const KIND_NAMES = { proceso: "proceso", tarea: "tarea", enlace: "enlace", sistema: "sistema (solo se observa)" };
const LEVELS = { debug: 10, info: 20, warning: 30, error: 40, critical: 50 };
const SOURCE_NAME_LABEL = { app: "Aplicación", file: "Ruta del archivo" };
const SILENCE = -120;
const LOG_CAPACITY = 2000;

let socket = null;
let nextId = 1;
const pending = new Map();
let latest = null;
let engineKind = null;
let renderQueued = false;

// -- utilidades ----------------------------------------------------------------

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
  toastTimer = setTimeout(() => { box.hidden = true; }, 4500);
}

function duration(seconds) {
  if (seconds == null) return "—";
  if (seconds < 60) return `${Math.floor(seconds)} s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ${Math.floor(seconds % 60)} s`;
  return `${Math.floor(seconds / 3600)} h ${Math.floor((seconds % 3600) / 60)} min`;
}

// -- controles que no se pisan (09 §7.4) ----------------------------------------
// Un control se escribe solo cuando cambia su valor en el motor, y nunca mientras
// se edita. "Editar" empieza con pointerdown o focus, porque en Firefox para macOS
// un clic en un <select> no le da el foco.

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
  // Un menú que se cerró sin elegir no deja el control trabado para siempre.
  for (const node of editables) {
    if (node !== event.target && !node.contains(event.target) && document.activeElement !== node) {
      node.dataset.editing = "0";
    }
  }
}, true);

function isEditing(node) {
  if (document.activeElement === node) return true;
  if (node.dataset.editing !== "1") return false;
  return Date.now() - Number(node.dataset.editingSince || 0) < 8000;
}

function syncValue(node, value) {
  const text = value == null ? "" : String(value);
  if (node.dataset.synced === text || isEditing(node)) return;
  node.value = text;
  node.dataset.synced = text;
}

function resync(node) {
  // La orden se rechazó: el próximo snapshot vuelve a escribir el valor del motor.
  node.dataset.synced = "\u0000";
  node.dataset.editing = "0";
}

// -- conexión -------------------------------------------------------------------

function send(cmd, args = {}) {
  if (!socket || socket.readyState !== WebSocket.OPEN) {
    toast("Sin conexión: la orden no se envió.");
    return Promise.resolve(false);
  }
  const id = nextId++;
  socket.send(JSON.stringify({ id, cmd, args }));
  return new Promise((resolve) => pending.set(id, resolve));
}

async function sendFrom(node, cmd, args) {
  const ok = await send(cmd, args);
  if (!ok) resync(node);
  return ok;
}

function connect() {
  socket = new WebSocket(`ws://${location.host}/ws`);
  socket.addEventListener("open", () => {
    $("connection").textContent = "Conectado";
    $("connection").classList.add("ok");
    $("disconnected").hidden = true;
    document.body.classList.remove("stale");
  });
  socket.addEventListener("message", (event) => {
    const message = JSON.parse(event.data);
    if (message.type === "hello") onHello(message);
    else if (message.type === "logs") onLogs(message);
    else if (message.type === "snapshot") {
      latest = message.data;
      if (!renderQueued) {
        renderQueued = true;
        requestAnimationFrame(() => { renderQueued = false; render(latest); });
      }
    } else if (message.type === "reply") {
      if (!message.ok) toast(message.error);
      const resolve = pending.get(message.id);
      pending.delete(message.id);
      if (resolve) resolve(message.ok);
    }
  });
  socket.addEventListener("close", () => {
    $("connection").textContent = "Desconectado";
    $("connection").classList.remove("ok");
    $("disconnected").hidden = false;
    document.body.classList.add("stale");
    for (const resolve of pending.values()) resolve(false);
    pending.clear();
    setTimeout(connect, 2000);
  });
}

function onHello(hello) {
  engineKind = hello.engine;
  $("engine-badge").hidden = hello.engine !== "simulated";
  $("pair-open").hidden = !hello.pairing;
}

function render(s) {
  renderControls(s);
  renderRoom(s);
  renderSpeakers(s);
  renderServices(s);
  renderLink(s);
  renderDrift(s.clock.history);
  renderMeters(s);
  renderConfig(s);
  renderCalibration(s);
}

// -- barra de transmisión --------------------------------------------------------

function renderControls(s) {
  const run = $("run");
  const starting = !s.engine.running && s.services.some((sv) => sv.state === "starting");
  run.textContent = s.engine.running ? "Detener" : starting ? "Iniciando…" : "Transmitir";
  run.classList.toggle("stop", s.engine.running || starting);
  for (const button of document.querySelectorAll("[data-mode]")) {
    button.setAttribute("aria-pressed", String(button.dataset.mode === s.engine.mode));
  }
  syncValue($("source-kind"), s.engine.source.kind);
  syncValue($("source-name"), s.engine.source.name || "");
  syncSourceField();
  syncValue($("master"), s.engine.master_db);
  $("master-out").textContent = `${Number($("master").value).toFixed(0)} dB`;
  $("latency-total").textContent = `≈ ${Math.round(s.latency.total_ms)} ms`;
}

function syncSourceField() {
  const kind = $("source-kind").value;
  const needsName = kind in SOURCE_NAME_LABEL;
  $("source-name-field").hidden = !needsName;
  if (needsName) $("source-name-label").textContent = SOURCE_NAME_LABEL[kind];
}

// -- parlantes y canales ----------------------------------------------------------------

let roomMode = null;
const slots = new Map();

function renderRoom(s) {
  const room = $("room");
  if (roomMode !== s.engine.mode) {
    for (const slot of slots.values()) slot.box.remove();
    slots.clear();
    for (const [channel, [x, y]] of Object.entries(CHANNEL_POSITIONS[s.engine.mode])) {
      const name = el("div", { class: "slot-name" });
      const box = el("div", { class: "slot", title: CHANNEL_NAMES[channel] },
        el("div", { class: "slot-channel", text: channel }), name);
      box.style.left = `${x}%`;
      box.style.top = `${y}%`;
      room.append(box);
      slots.set(channel, { box, name });
    }
    roomMode = s.engine.mode;
  }
  for (const [channel, slot] of slots) {
    const speaker = s.speakers.find((sp) => sp.channel === channel);
    slot.name.textContent = speaker ? speaker.name : "libre";
    slot.box.classList.toggle("filled", Boolean(speaker));
    slot.box.classList.toggle("lost", Boolean(speaker && speaker.state === "lost"));
  }
  const scan = $("scan");
  scan.disabled = s.engine.scanning;
  scan.textContent = s.engine.scanning ? "Buscando…" : "Buscar parlantes";
}

const speakerRows = new Map();

function channelOptions(select, mode) {
  select.replaceChildren(el("option", { value: "", text: "— sin canal" }));
  for (const channel of Object.keys(CHANNEL_POSITIONS[mode])) {
    select.append(el("option", { value: channel, text: `${channel} · ${CHANNEL_NAMES[channel]}` }));
  }
  select.dataset.mode = mode;
  select.dataset.synced = "\u0000";
}

function numberInput(min, max, step, label) {
  return editable(el("input", { type: "number", min, max, step, "aria-label": label, class: "num-input" }));
}

function speakerRow(speaker) {
  const address = speaker.address;
  const current = () => latest && latest.speakers.find((sp) => sp.address === address);
  const select = editable(el("select", { "aria-label": `Canal de ${speaker.name}` }));
  select.addEventListener("change", () => {
    const channel = select.value;
    sendFrom(select, channel ? "assign" : "unassign", channel ? { address, channel } : { address });
  });
  const volume = editable(el("input", { type: "range", min: "-60", max: "6", step: "1",
    "aria-label": `Volumen de ${speaker.name}` }));
  const volumeOut = el("output", { class: "num" });
  volume.addEventListener("input", () => { volumeOut.textContent = `${volume.value} dB`; });
  volume.addEventListener("change", () => sendFrom(volume, "set_speaker_volume", { address, db: Number(volume.value) }));
  const delay = numberInput("0", "100", "0.1", `Retardo de ${speaker.name} en ms`);
  delay.addEventListener("change", () => sendFrom(delay, "set_speaker_trim", { address, delay_ms: Number(delay.value) }));
  const gain = numberInput("-12", "6", "0.5", `Ganancia de ${speaker.name} en dB`);
  gain.addEventListener("change", () => sendFrom(gain, "set_speaker_trim", { address, gain_db: Number(gain.value) }));
  const mute = el("button", { type: "button", class: "ghost small-btn" });
  mute.addEventListener("click", () => {
    const sp = current();
    if (sp) send("set_mute", { address, muted: !sp.muted });
  });
  const tone = el("button", { type: "button", class: "ghost small-btn", text: "Tono" });
  tone.addEventListener("click", () => {
    const sp = current();
    if (sp && sp.channel) send("tone", { channel: sp.channel, seconds: 2 });
  });
  const row = {
    tr: el("tr"),
    name: el("div", { class: "speaker-name" }),
    sub: el("div", { class: "speaker-sub" }),
    status: el("span", { class: "status" }),
    rssi: el("td", { class: "num cell-rssi" }),
    select, volume, volumeOut, delay, gain, mute, tone,
  };
  row.tr.append(
    el("td", { class: "cell-name" }, row.name, row.sub),
    el("td", { class: "cell-status" }, row.status),
    row.rssi,
    el("td", { class: "cell-channel" }, select),
    el("td", { class: "cell-volume" }, el("div", { class: "inline" }, volume, volumeOut)),
    el("td", { class: "num cell-delay" }, delay),
    el("td", { class: "num cell-gain" }, gain),
    el("td", { class: "cell-actions" }, el("div", { class: "inline" }, tone, mute)),
  );
  return row;
}

function setStatus(node, table, state) {
  const [icon, label] = table[state] || ["?", state];
  node.className = `status status-${state}`;
  node.replaceChildren(el("span", { class: "status-icon", text: icon, "aria-hidden": "true" }), label);
}

function renderSpeakers(s) {
  const body = $("speakers");
  const seen = new Set();
  for (const speaker of s.speakers) {
    seen.add(speaker.address);
    let row = speakerRows.get(speaker.address);
    if (!row) {
      row = speakerRow(speaker);
      speakerRows.set(speaker.address, row);
      body.append(row.tr);
    }
    row.name.textContent = speaker.name;
    row.sub.textContent = `${speaker.model} · ${speaker.address}${speaker.firmware ? ` · fw ${speaker.firmware}` : ""}`;
    setStatus(row.status, SPEAKER_STATUS, speaker.state);
    row.rssi.textContent = speaker.rssi_dbm == null ? "—" : `${speaker.rssi_dbm} dBm`;
    if (row.select.dataset.mode !== s.engine.mode) channelOptions(row.select, s.engine.mode);
    syncValue(row.select, speaker.channel || "");
    syncValue(row.volume, speaker.volume_db);
    row.volumeOut.textContent = speaker.muted ? "mudo" : `${Number(row.volume.value).toFixed(0)} dB`;
    syncValue(row.delay, speaker.delay_ms);
    syncValue(row.gain, speaker.gain_db);
    row.mute.textContent = speaker.muted ? "Activar" : "Silenciar";
    row.mute.setAttribute("aria-pressed", String(speaker.muted));
    row.tone.disabled = !(s.engine.running && speaker.channel);
    row.tone.title = row.tone.disabled ? "Asigna un canal y transmite para mandar un tono" : `Tono de 2 s en ${speaker.channel}`;
  }
  for (const [address, row] of speakerRows) {
    if (!seen.has(address)) { row.tr.remove(); speakerRows.delete(address); }
  }
}

// -- servicios ------------------------------------------------------------------------

const serviceRows = new Map();

function serviceButton(text, cmd, name, cls = "ghost small-btn") {
  const button = el("button", { type: "button", class: cls, text });
  button.addEventListener("click", () => send(cmd, { name }));
  return button;
}

function serviceRow(service) {
  const name = el("button", { type: "button", class: "link-btn", title: "Ver solo los logs de este servicio" });
  name.addEventListener("click", () => {
    $("log-service").value = service.name;
    renderLogList(true);
    $("logs").scrollIntoView({ behavior: "smooth", block: "nearest" });
  });
  const row = {
    tr: el("tr"),
    name,
    kind: el("div", { class: "speaker-sub" }),
    status: el("span", { class: "status" }),
    pid: el("td", { class: "num cell-pid" }),
    uptime: el("td", { class: "num cell-uptime" }),
    restarts: el("td", { class: "num cell-restarts" }),
    detail: el("div", { class: "service-detail" }),
    error: el("div", { class: "service-error" }),
    actions: el("div", { class: "inline" }),
    actionsKey: null,
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

function renderServices(s) {
  const body = $("services");
  for (const service of s.services) {
    let row = serviceRows.get(service.name);
    if (!row) {
      row = serviceRow(service);
      serviceRows.set(service.name, row);
      body.append(row.tr);
      ensureLogService(service.name);
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
    const key = `${service.managed}|${service.state}|${engineKind}`;
    if (row.actionsKey !== key) {
      row.actionsKey = key;
      const buttons = [];
      if (service.managed) {
        if (["stopped", "failed"].includes(service.state)) buttons.push(serviceButton("Iniciar", "service_start", service.name));
        if (["running", "starting"].includes(service.state)) {
          buttons.push(serviceButton("Detener", "service_stop", service.name));
          buttons.push(serviceButton("Reiniciar", "service_restart", service.name));
        }
        if (engineKind === "simulated" && service.state === "running") {
          buttons.push(serviceButton("Simular falla", "service_fail", service.name, "ghost small-btn danger"));
        }
      }
      row.actions.replaceChildren(...buttons);
    }
  }
}

// -- salud del enlace -----------------------------------------------------------------

function renderLink(s) {
  const c = s.controller;
  const iso = c.iso_broadcaster === null ? "ISO: no observado" : c.iso_broadcaster ? "ISO: sí" : "ISO: no";
  $("t-controller").textContent = c.present ? (c.unit || "presente") : "sin enlace";
  $("t-controller-sub").textContent = c.present ? `${c.port || "?"} · ${iso}` : "inicia el servicio Controlador";
  const big = c.big;
  const bigNames = { active: `activo · ${big.num_bis} BIS`, creating: "creándose…", idle: "detenido", error: "error" };
  $("t-big").textContent = bigNames[big.state] || big.state;
  $("t-big-sub").textContent = `presentation delay ${big.presentation_delay_us / 1000} ms · intervalo ${big.sdu_interval_us / 1000} ms`;
  $("t-queue").textContent = s.engine.running ? `${s.clock.queue_sdus.toFixed(1)} SDU` : "—";
  $("t-queue-sub").textContent = `objetivo ${s.clock.queue_target} · corrección ${s.clock.ratio_ppm.toFixed(1)} ppm`;
  $("t-glitches").textContent = `${s.clock.underruns} · ${s.clock.overruns}`;
  $("t-drift").textContent = s.engine.running ? `${s.clock.drift_ppm.toFixed(1)} ppm` : "—";
  const l = s.latency;
  const rows = [
    ["Captura (un quantum)", l.capture_ms], ["LC3 (trama + algoritmo)", l.codec_ms],
    ["Transporte ISO (máximo)", l.transport_ms], ["Presentation delay", l.presentation_ms],
  ];
  $("latency").replaceChildren(
    ...rows.map(([label, ms]) => el("tr", {}, el("td", { text: label }), el("td", { class: "num", text: `${ms.toFixed(1)} ms` }))),
    el("tr", { class: "total" }, el("td", { text: "Total" }), el("td", { class: "num", text: `${l.total_ms.toFixed(1)} ms` })),
  );
}

let driftHover = null;

function renderDrift(history) {
  const box = $("drift-chart");
  box.replaceChildren();
  if (history.length < 2) {
    box.append(el("div", { class: "chart-empty", text: "Sin datos: el emisor no está transmitiendo." }));
    return;
  }
  const width = box.clientWidth || 300;
  const height = box.clientHeight || 96;
  const pad = { left: 30, right: 6, top: 6, bottom: 6 };
  let lo = Math.min(...history);
  let hi = Math.max(...history);
  if (hi - lo < 1) { lo -= 0.5; hi += 0.5; }
  const x = (i) => pad.left + (i / (history.length - 1)) * (width - pad.left - pad.right);
  const y = (v) => pad.top + (1 - (v - lo) / (hi - lo)) * (height - pad.top - pad.bottom);
  const chart = svg("svg", { viewBox: `0 0 ${width} ${height}`, role: "img",
    "aria-label": `Drift del último minuto, entre ${lo.toFixed(1)} y ${hi.toFixed(1)} ppm` });
  for (const v of [lo, hi]) {
    chart.append(svg("line", { class: "grid-line", x1: pad.left, x2: width - pad.right, y1: y(v), y2: y(v) }));
    const label = svg("text", { class: "axis-label", x: pad.left - 4, y: y(v) + 3, "text-anchor": "end" });
    label.textContent = v.toFixed(1);
    chart.append(label);
  }
  const points = history.map((v, i) => `${x(i)},${y(v)}`).join(" ");
  chart.append(svg("polygon", { class: "area",
    points: `${x(0)},${height - pad.bottom} ${points} ${x(history.length - 1)},${height - pad.bottom}` }));
  chart.append(svg("polyline", { class: "line", points }));
  chart.append(svg("circle", { class: "dot", r: 4, cx: x(history.length - 1), cy: y(history.at(-1)) }));
  if (driftHover !== null) {
    const i = Math.round(driftHover * (history.length - 1));
    chart.append(svg("line", { class: "crosshair", x1: x(i), x2: x(i), y1: pad.top, y2: height - pad.bottom }));
    chart.append(svg("circle", { class: "dot", r: 4, cx: x(i), cy: y(history[i]) }));
    const tip = el("div", { class: "chart-tip" }, el("strong", { text: `${history[i].toFixed(1)} ppm` }), " ",
      el("span", { text: `hace ${history.length - 1 - i} s` }));
    tip.style.left = `${x(i)}px`;
    box.append(tip);
  }
  box.prepend(chart);
  const track = (event) => {
    const rect = chart.getBoundingClientRect();
    const fraction = (event.clientX - rect.left - (pad.left / width) * rect.width) /
      (rect.width * (1 - (pad.left + pad.right) / width));
    driftHover = Math.min(1, Math.max(0, fraction));
  };
  chart.addEventListener("pointermove", track);
  chart.addEventListener("pointerdown", track);
  chart.addEventListener("pointerleave", () => { driftHover = null; });
}

// -- niveles ---------------------------------------------------------------------------

const meterRows = new Map();
let meterMode = null;

function renderMeters(s) {
  const box = $("meters");
  if (meterMode !== s.engine.mode) {
    box.replaceChildren();
    meterRows.clear();
    for (const channel of Object.keys(s.meters)) {
      const fill = el("div", { class: "meter-fill" });
      const peak = el("div", { class: "meter-peak" });
      const value = el("div", { class: "meter-value num" });
      const track = el("div", { class: "meter-track", role: "meter", "aria-label": `Nivel ${channel}`,
        "aria-valuemin": "-60", "aria-valuemax": "0" }, fill, peak);
      box.append(el("div", { class: "meter" }, el("div", { class: "meter-label", text: channel }), track, value));
      meterRows.set(channel, { fill, peak, value, track });
    }
    meterMode = s.engine.mode;
  }
  const pct = (v) => `${Math.max(0, Math.min(100, ((v + 60) / 60) * 100))}%`;
  for (const [channel, row] of meterRows) {
    const m = s.meters[channel];
    row.fill.style.width = m.rms_db <= SILENCE ? "0%" : pct(m.rms_db);
    row.fill.classList.toggle("hot", m.peak_db > -6 && m.peak_db < -0.5);
    row.fill.classList.toggle("clip", m.peak_db >= -0.5);
    row.peak.hidden = m.peak_db <= SILENCE;
    row.peak.style.left = pct(m.peak_db);
    row.value.textContent = m.rms_db <= SILENCE ? "—" : m.rms_db.toFixed(1);
    row.track.setAttribute("aria-valuenow", String(Math.max(-60, m.rms_db)));
  }
}

// -- configuración del motor --------------------------------------------------------------

function renderConfig(s) {
  for (const node of document.querySelectorAll("[data-config]")) {
    syncValue(node, s.config[node.dataset.config]);
  }
  const out = document.querySelector('[data-config-out="rear_delay_ms"]');
  out.textContent = `${Number(document.querySelector('[data-config="rear_delay_ms"]').value)} ms`;
}

function setupConfig() {
  for (const node of document.querySelectorAll("[data-config]")) {
    editable(node);
    node.addEventListener("change", () => {
      const key = node.dataset.config;
      let value = node.value;
      if (node.type === "range" || node.dataset.number) value = Number(value);
      if (key === "broadcast_name") value = value.trim();
      sendFrom(node, "set_config", { key, value });
    });
  }
  const rear = document.querySelector('[data-config="rear_delay_ms"]');
  rear.addEventListener("input", () => {
    document.querySelector('[data-config-out="rear_delay_ms"]').textContent = `${rear.value} ms`;
  });
}

// -- calibración ------------------------------------------------------------------------

function renderCalibration(s) {
  const cal = s.calibration;
  $("cal-run").textContent = cal.state === "running" ? "Cancelar" : "Calibrar";
  $("cal-run").disabled = cal.state !== "running" && !s.engine.running;
  $("cal-apply").disabled = cal.state !== "done";
  $("cal-progress").hidden = cal.state !== "running";
  $("cal-bar").style.width = `${Math.round(cal.progress * 100)}%`;
  $("cal-save").disabled = cal.state !== "done" || cal.simulated;
  let note = "Mide retardo y nivel de cada parlante con el micrófono. Necesita transmitir y un parlante en cada canal.";
  if (cal.state === "running") note = `Midiendo… ${Math.round(cal.progress * 100)} %`;
  else if (cal.state === "done" && cal.simulated) note = "Resultado SIMULADO: se puede aplicar a los parlantes, pero no se guarda como medición.";
  else if (cal.state === "done") note = `Medido ${cal.measured_at}.`;
  $("cal-note").textContent = note;
  $("cal-table").hidden = cal.results.length === 0;
  $("cal-results").replaceChildren(...cal.results.map((r) => el("tr", {},
    el("td", { text: r.channel }),
    el("td", { class: "num", text: r.delay_ms.toFixed(1) }),
    el("td", { class: "num", text: r.gain_db.toFixed(1) }),
    el("td", { class: "num", text: `${Math.round(r.confidence * 100)} %` }),
  )));
}

// -- logs --------------------------------------------------------------------------------

let logRecords = [];
let logPaused = false;
let logClearedAt = 0;
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
  const marks = { warning: "⚠", error: "✕", critical: "✕" };
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

function onLogs(message) {
  if (message.reset) logRecords = [];
  for (const record of message.records) ensureLogService(record.service);
  logRecords.push(...message.records);
  if (message.gap) toast("Se perdieron líneas de log: el panel se atrasó.");
  if (logRecords.length > LOG_CAPACITY) logRecords = logRecords.slice(-LOG_CAPACITY);
  renderLogList(Boolean(message.reset), message.records);
}

function downloadLogs() {
  const text = logRecords.filter(logMatches)
    .map((r) => `${r.at} ${r.level.toUpperCase().padEnd(7)} ${r.service.padEnd(10)} ${r.message}`).join("\n");
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

// -- arranque ------------------------------------------------------------------------------

function setup() {
  $("run").addEventListener("click", () => {
    const busy = latest && (latest.engine.running || latest.services.some((sv) => sv.state === "starting"));
    send(busy ? "stop" : "start");
  });
  for (const button of document.querySelectorAll("[data-mode]")) {
    button.addEventListener("click", () => {
      const mode = button.dataset.mode;
      if (!latest || latest.engine.mode === mode) return;
      const assigned = latest.speakers.some((sp) => sp.channel);
      if (assigned && !confirm("Cambiar de modo borra la asignación de canales de todos los parlantes. ¿Seguir?")) return;
      send("set_mode", { mode });
    });
  }
  const kind = editable($("source-kind"));
  const name = editable($("source-name"));
  kind.addEventListener("change", () => {
    syncSourceField();
    if (kind.value in SOURCE_NAME_LABEL) name.focus();
    else sendFrom(kind, "set_source", { kind: kind.value });
  });
  name.addEventListener("change", () => {
    const value = name.value.trim();
    if (value) sendFrom(name, "set_source", { kind: kind.value, name: value });
  });
  const master = editable($("master"));
  master.addEventListener("input", () => { $("master-out").textContent = `${master.value} dB`; });
  master.addEventListener("change", () => sendFrom(master, "set_master", { db: Number(master.value) }));
  $("scan").addEventListener("click", () => send("scan"));
  $("cal-run").addEventListener("click", () => {
    send(latest && latest.calibration.state === "running" ? "calibrate_cancel" : "calibrate");
  });
  $("cal-apply").addEventListener("click", () => send("calibration_apply"));
  $("cal-save").addEventListener("click", () => send("save_measurement"));
  $("pair-open").addEventListener("click", () => {
    $("pair-qr").src = `/pairing.svg?${Date.now()}`;
    $("pair-dialog").showModal();
  });
  setupConfig();
  setupLogs();
  connect();
}

document.addEventListener("DOMContentLoaded", setup);
