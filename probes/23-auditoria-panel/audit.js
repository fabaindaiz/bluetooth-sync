// In-page helpers for the panel audit. Read-only on the page except data-audit-id attributes.
window.__A = (() => {
  const cv = document.createElement("canvas");
  cv.width = cv.height = 1;
  const cx = cv.getContext("2d", { willReadFrequently: true });

  // Any CSS color (oklch included) -> [r, g, b, a] in sRGB via the canvas.
  function rgba(color) {
    if (!color || color === "none" || color === "transparent") return [0, 0, 0, 0];
    cx.clearRect(0, 0, 1, 1);
    cx.fillStyle = "rgba(1,2,3,0)";
    cx.fillStyle = color;
    cx.fillRect(0, 0, 1, 1);
    const d = cx.getImageData(0, 0, 1, 1).data;
    return [d[0], d[1], d[2], d[3] / 255];
  }
  const over = (fg, bg) => {
    const a = fg[3];
    return [0, 1, 2].map((i) => Math.round(fg[i] * a + bg[i] * (1 - a))).concat([1]);
  };
  const lin = (c) => { c /= 255; return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4; };
  const lum = (c) => 0.2126 * lin(c[0]) + 0.7152 * lin(c[1]) + 0.0722 * lin(c[2]);
  const ratio = (a, b) => { const x = lum(a), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };
  const hex = (c) => "#" + c.slice(0, 3).map((v) => v.toString(16).padStart(2, "0")).join("");

  // The opaque color behind an element: its ancestors' backgrounds composited from the root.
  function bgOf(el) {
    const layers = [];
    for (let n = el; n && n.nodeType === 1; n = n.parentElement) {
      const c = rgba(getComputedStyle(n).backgroundColor);
      if (c[3] > 0) layers.push(c);
      if (c[3] >= 1) break;
    }
    let acc = [255, 255, 255, 1];
    for (let i = layers.length - 1; i >= 0; i--) acc = over(layers[i], acc);
    return acc;
  }

  function visible(el) {
    if (!el.isConnected) return false;
    if (el.checkVisibility && !el.checkVisibility({ checkOpacity: false, checkVisibilityCSS: true })) return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  }

  const INTERACTIVE = "a[href],button,input:not([type=hidden]),select,textarea,summary,[role=button],[role=tab],[role=slider],[role=checkbox],[role=switch],[role=radio],[role=menuitem],[tabindex]:not([tabindex='-1']),[contenteditable=true]";

  function interactive(root = document) {
    return [...root.querySelectorAll(INTERACTIVE)].filter((e) => visible(e) && !e.closest("[inert]") && !e.disabled);
  }

  function desc(el) {
    const r = el.getBoundingClientRect();
    const text = (el.getAttribute("aria-label") || el.innerText || el.value || el.getAttribute("title") || "").trim().replace(/\s+/g, " ").slice(0, 50);
    let sel = el.tagName.toLowerCase();
    if (el.id) sel += "#" + el.id;
    else if (el.classList.length) sel += "." + [...el.classList].slice(0, 3).join(".");
    if (el.type && el.tagName === "INPUT") sel += `[type=${el.type}]`;
    const card = el.closest("[data-card]");
    const where = card ? card.dataset.card : el.closest("#topbar") ? "cabecera" : el.closest("#bottom-nav") ? "nav-abajo" : "otro";
    return { sel, text, where, x: Math.round(r.left), y: Math.round(r.top + scrollY), w: +r.width.toFixed(1), h: +r.height.toFixed(1) };
  }

  // The target a pointer can hit: the element, or the label that wraps/points to it.
  function targetRect(el) {
    let r = el.getBoundingClientRect();
    const lab = el.closest("label") || (el.id && document.querySelector(`label[for="${CSS.escape(el.id)}"]`));
    if (lab && (el.type === "checkbox" || el.type === "radio")) {
      const l = lab.getBoundingClientRect();
      if (l.width * l.height > r.width * r.height) r = l;
    }
    return r;
  }

  function targetSizes(min) {
    const els = interactive().filter((e) => !e.closest("#cards"));
    const rects = els.map(targetRect);
    const out = [];
    els.forEach((el, i) => {
      const r = rects[i];
      if (r.width >= min && r.height >= min) return;
      // WCAG 2.5.8 spacing exception: a 24 px circle centred on the target touches no other target.
      const cx0 = r.left + r.width / 2, cy0 = r.top + r.height / 2;
      let clash = null;
      rects.forEach((o, j) => {
        if (j === i || clash) return;
        if (els[j].contains(el) || el.contains(els[j])) return;
        const nx = Math.max(o.left, Math.min(cx0, o.right)), ny = Math.max(o.top, Math.min(cy0, o.bottom));
        const d = Math.hypot(nx - cx0, ny - cy0);
        const small = o.width < min || o.height < min;
        const ocx = o.left + o.width / 2, ocy = o.top + o.height / 2;
        if (d < min / 2 || (small && Math.hypot(ocx - cx0, ocy - cy0) < min)) clash = desc(els[j]).sel;
      });
      const inline = getComputedStyle(el).display === "inline" && el.tagName === "A";
      out.push({ ...desc(el), tw: +r.width.toFixed(1), th: +r.height.toFixed(1), spacingOk: !clash, clash, inline });
    });
    return { total: els.length, below: out };
  }

  // Non-text contrast: every small coloured shape and every SVG mark, against what is behind it.
  function graphics(scope) {
    const rows = [];
    const add = (el, kind, fg, bgEl, note) => {
      const b = bgOf(bgEl);
      const f = over(fg, b);
      rows.push({ kind, sel: desc(el).sel, where: desc(el).where, fg: hex(f), bg: hex(b), ratio: +ratio(f, b).toFixed(2), alpha: +fg[3].toFixed(2), note: note || "" });
    };
    for (const el of scope.querySelectorAll("*")) {
      if (!visible(el)) continue;
      const cs = getComputedStyle(el);
      let op = 1;
      for (let n = el; n && n !== scope; n = n.parentElement) op *= parseFloat(getComputedStyle(n).opacity);
      if (el instanceof SVGElement && ["path", "line", "polyline", "circle", "rect", "polygon", "ellipse"].includes(el.tagName)) {
        const svgEl = el.ownerSVGElement;
        if (svgEl && svgEl.getAttribute("aria-hidden") === "true" && svgEl.classList.contains("nav-icon")) continue;
        const strokeW = parseFloat(cs.strokeWidth);
        const useStroke = cs.stroke !== "none" && strokeW > 0;
        const c = rgba(useStroke ? cs.stroke : cs.fill);
        if (c[3] === 0) continue;
        c[3] *= op * parseFloat(useStroke ? cs.strokeOpacity : cs.fillOpacity);
        add(el, `svg ${el.tagName} ${useStroke ? "stroke " + strokeW : "fill"}`, c, svgEl.parentElement, [...el.classList].join(" "));
        continue;
      }
      if (el instanceof HTMLElement) {
        const r = el.getBoundingClientRect();
        const hasText = [...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim());
        const bg = rgba(cs.backgroundColor);
        const role = el.getAttribute("role");
        if (!hasText && (Math.min(r.width, r.height) <= 16 || role === "meter") && (bg[3] > 0 || cs.backgroundImage !== "none")) {
          if (cs.backgroundImage.startsWith("linear-gradient")) {
            const stops = cs.backgroundImage.match(/(rgba?\([^)]*\)|oklch\([^)]*\)|#[0-9a-f]{3,8})/gi) || [];
            for (const s of new Set(stops)) add(el, "gradiente", rgba(s), el.parentElement, "parada " + s);
          } else if (bg[3] > 0) {
            bg[3] *= op;
            add(el, "forma", bg, el.parentElement, "");
          }
        }
        // Component boundaries: inputs and selects need their border to be seen (1.4.11).
        if (el.matches("input:not([type=range]):not([type=checkbox]):not([type=radio]),select,textarea") && parseFloat(cs.borderTopWidth) > 0) {
          add(el, "borde de campo", rgba(cs.borderTopColor), el.parentElement, "");
        }
      }
    }
    // Group: same kind+selector+colors.
    const g = new Map();
    for (const r of rows) {
      const k = `${r.kind}|${r.sel}|${r.fg}|${r.bg}`;
      if (!g.has(k)) g.set(k, { ...r, n: 0 });
      g.get(k).n++;
    }
    return [...g.values()].sort((a, b) => a.ratio - b.ratio);
  }

  function focusInfo() {
    const el = document.activeElement;
    if (!el || el === document.body) return null;
    const cs = getComputedStyle(el);
    const r = el.getBoundingClientRect();
    const cxp = r.left + r.width / 2, cyp = r.top + r.height / 2;
    const hit = document.elementFromPoint(Math.min(Math.max(cxp, 0), innerWidth - 1), Math.min(Math.max(cyp, 0), innerHeight - 1));
    const inView = r.bottom > 0 && r.top < innerHeight && r.right > 0 && r.left < innerWidth;
    const covered = inView && hit && !(el === hit || el.contains(hit) || hit.contains(el) || (el.labels && [...el.labels].some((l) => l.contains(hit))));
    const coverer = covered ? desc(hit).sel + " in " + (hit.closest("#topbar") ? "cabecera" : hit.closest("#bottom-nav") ? "nav-abajo" : desc(hit).where) : null;
    const oc = rgba(cs.outlineColor);
    const b = bgOf(el.parentElement || el);
    return {
      id: el.dataset.auditId || null, ...desc(el), fv: el.matches(":focus-visible"),
      outline: `${cs.outlineStyle} ${cs.outlineWidth} ${hex(oc)}`, outlineRatio: cs.outlineStyle !== "none" ? +ratio(over(oc, b), b).toFixed(2) : null,
      shadow: cs.boxShadow !== "none", inView, covered, coverer,
      cx: r.left - 6, cy: r.top - 6, cw: r.width + 12, ch: r.height + 12,
    };
  }

  // Decodes a PNG (base64) and returns colour stats of a region (all pixels).
  async function pixels(b64) {
    const img = new Image();
    img.src = "data:image/png;base64," + b64;
    await img.decode();
    const c = document.createElement("canvas");
    c.width = img.width; c.height = img.height;
    const x = c.getContext("2d", { willReadFrequently: true });
    x.drawImage(img, 0, 0);
    return { w: img.width, h: img.height, data: Array.from(x.getImageData(0, 0, img.width, img.height).data) };
  }

  // Mutation rates of live regions and of everything, over `ms`.
  function watch(ms) {
    return new Promise((resolve) => {
      const live = [...document.querySelectorAll("[aria-live]:not([aria-live=off]),[role=status],[role=alert],[role=log],[role=timer],[role=marquee]")];
      const liveStats = live.map((n) => ({ sel: desc(n).sel, where: desc(n).where, politeness: n.getAttribute("aria-live") || ({ alert: "assertive", status: "polite", log: "polite" }[n.getAttribute("role")] || "?"), visible: visible(n), changes: 0, texts: new Set([n.textContent.trim()]) }));
      const obsLive = live.map((n, i) => {
        const o = new MutationObserver(() => {
          const t = n.textContent.trim();
          if (!liveStats[i].texts.has(t) || true) liveStats[i].changes++;
          liveStats[i].texts.add(t);
        });
        o.observe(n, { childList: true, subtree: true, characterData: true });
        return o;
      });
      const groups = new Map();
      const keyOf = (t) => {
        const e = t.nodeType === 1 ? t : t.parentElement;
        if (!e) return "?";
        const idn = e.closest("[id]");
        return (idn ? "#" + idn.id : "") + " > " + (e.className && typeof e.className === "string" ? e.tagName.toLowerCase() + "." + e.className.split(" ")[0] : e.tagName.toLowerCase());
      };
      const all = new MutationObserver((recs) => {
        const t = Math.round(performance.now());
        for (const r of recs) {
          const e = r.target.nodeType === 1 ? r.target : r.target.parentElement;
          if (e && !visible(e)) continue;
          const k = keyOf(r.target);
          if (!groups.has(k)) groups.set(k, { batches: new Set(), recs: 0, kinds: new Set() });
          const g = groups.get(k);
          g.batches.add(t); g.recs++; g.kinds.add(r.type === "attributes" ? "attr:" + r.attributeName : r.type);
        }
      });
      all.observe(document.body, { childList: true, subtree: true, characterData: true, attributes: true });
      let frames = 0;
      const t0 = performance.now();
      const raf = () => { frames++; if (performance.now() - t0 < ms) requestAnimationFrame(raf); };
      requestAnimationFrame(raf);
      setTimeout(() => {
        obsLive.forEach((o) => o.disconnect());
        all.disconnect();
        const s = ms / 1000;
        resolve({
          live: liveStats.map((l) => ({ ...l, texts: l.texts.size, perMin: +(l.changes / s * 60).toFixed(1) })),
          updaters: [...groups.entries()].map(([k, g]) => ({ k, perSec: +(g.batches.size / s).toFixed(1), recs: g.recs, kinds: [...g.kinds].join(",") })).sort((a, b) => b.perSec - a.perSec).slice(0, 25),
          fps: +(frames / s).toFixed(1),
          transitions: [...document.querySelectorAll("*")].filter((e) => visible(e) && parseFloat(getComputedStyle(e).transitionDuration) > 0).length,
          animations: document.getAnimations().length,
        });
      }, ms);
    });
  }

  function layout() {
    const view = [...document.querySelectorAll("[data-view]")].find((v) => !v.hidden);
    const cards = view ? [...view.querySelectorAll(":scope [data-card]")].filter(visible) : [];
    const vr = view ? view.getBoundingClientRect() : null;
    const blank = cards.map((c) => {
      const r = c.getBoundingClientRect();
      let bottom = r.top;
      for (const ch of c.children) if (visible(ch)) bottom = Math.max(bottom, ch.getBoundingClientRect().bottom);
      return { card: c.dataset.card, w: Math.round(r.width), h: Math.round(r.height), blankBelow: Math.round(r.bottom - bottom - parseFloat(getComputedStyle(c).paddingBottom)) };
    });
    const top = document.getElementById("topbar").getBoundingClientRect();
    const warn = document.getElementById("warnings");
    const bn = document.getElementById("bottom-nav");
    return {
      vw: innerWidth, vh: innerHeight, scrollH: document.documentElement.scrollHeight,
      viewLeft: vr && Math.round(vr.left), viewWidth: vr && Math.round(vr.width),
      header: Math.round(top.height), headerPos: getComputedStyle(document.getElementById("topbar")).position,
      warnings: warn && !warn.hidden ? Math.round(warn.getBoundingClientRect().height) : 0,
      bottomNav: bn && visible(bn) ? Math.round(bn.getBoundingClientRect().height) : 0,
      hscroll: document.documentElement.scrollWidth > innerWidth, cards: blank,
    };
  }

  function tagAll() {
    let i = 0;
    for (const e of interactive()) if (!e.closest("#cards")) e.dataset.auditId = String(i++);
    return interactive().filter((e) => !e.closest("#cards")).map((e) => ({ id: e.dataset.auditId, ...desc(e) }));
  }

  function clickableNotFocusable() {
    const out = [];
    for (const e of document.querySelectorAll("body *")) {
      if (!visible(e) || e.closest("#cards")) continue;
      if (getComputedStyle(e).cursor !== "pointer") continue;
      if (e.matches(INTERACTIVE) || e.closest(INTERACTIVE) || e.closest("label") || e.tabIndex >= 0) continue;
      if (e.parentElement && getComputedStyle(e.parentElement).cursor === "pointer" && !e.parentElement.matches(INTERACTIVE)) continue;
      out.push(desc(e));
    }
    return out;
  }

  return { rgba, ratio, hex, bgOf, interactive, desc, targetSizes, graphics, focusInfo, pixels, watch, layout, tagAll, clickableNotFocusable, over };
})();
