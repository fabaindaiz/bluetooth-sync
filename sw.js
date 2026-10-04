// aurasync's service worker (the PWA on GitHub Pages, d-7c8794-37f9bc). `npm run build:pwa`
// (host/web/pwa/build.ts) copies this file into dist-pwa/sw.js and fills in the two constants.
//
// The rules are thom-music-player's (its docs/platform-web.md, "Offline", and
// tools/service_worker.js), adapted to a panel that is small and talks to a device:
// - **Cache first, the page included.** `./`, `index.html` and either with a query are the page;
//   a listed file is served from the cache, and the network is asked only for what it lacks.
// - **An update is atomic.** A new version downloads every file, and checks each one against
//   its SHA-256, before it installs; one failure and it does not install, and the old version
//   keeps working. The old caches are deleted only once the new one is complete and active.
// - **An update downloads only what changed.** Each file is cached under `<name>?<hash>`, so a
//   file the previous version already holds is copied across instead of downloaded.
// - **A new version applies itself, never in the middle of something.** Once active, it asks
//   each page `busy?` on a message port: `idle` (or no answer in 1.5 s, an older build) is
//   reloaded into it; `busy` (a calibration or a blind A/B running) is told `updated` and
//   reloads itself when it is done. The reload starts after `activate`, never awaited inside it
//   (thom measured the two waiting on each other for 310 s in Chromium), and goes to
//   `?v=<version>`, because WebKit treats navigating to the same URL with a fragment as a jump.
// - **The device's API is never cached.** Only the files listed below, on this origin and under
//   this scope, are ever answered or stored here; any other request (the API on
//   https://<device>:8443, which is another origin) is not even intercepted.
// Not kept from thom: the COOP/COEP headers (no threads here), the quick first install that
// leaves big files for later (the whole app is a few hundred KB), and Godot's old messages.
//
// Messages from a page: `{aurasync: "status"}` on a port (answered `{version, cached, total}`).
// To a page: `{aurasync: "busy?"}` on a port, and `{aurasync: "updated", version}`.

/** @type {string} */
const VERSION = "0.0.0-7b8e4547c142";
/** Every file of the build as `[name, sha256]`, relative to this worker. The first is the page.
 * @type {[string, string][]} */
const ENTRIES = [["index.html","84035b1cf871d583d45e5b26eb9f3c45158ba89511694f675816fe84555feced"],["app.js","fb9be8c83af12957b4320cfabbeebeac60fd42d997a85ee4b27665d05a500f6c"],["apple-touch-icon.png","c2fe5ee0d74dab6df882f0958e1a794f2c47af9b7467335c32220bec6f24136e"],["build.json","b62b70ecd105e3cded07406bbe36512ef256b22e6230dacdd2be9d67728bc155"],["cadena.js","c15f09e63fd9da3258e8938301297993fa40239648f4f3c8638426c4ed44b068"],["icon-192.png","dc1b6556b094d20fa97c741244eddefa4a4993c41007d1e7f72bd48d50b7eede"],["icon-512.png","a42bf763b1adb892e0f7834c72765b44a4b37f6818889f8746ea5039dabafd84"],["icon.svg","d9d4b9c0f873e6b5d5050b2be9abf758de0fa05c5914c9113da9f90cde430e63"],["manifest.webmanifest","c9331af6834b7071726f7f579521881d76b5a1709203812128815eb534561f6b"],["tailwind.css","dfd66c25bed1aa89f6e42df89da3eab034d2e07318a703ef3de3e58faae37b51"]];

const CACHE_PREFIX = "aurasync-";
const CACHE_NAME = CACHE_PREFIX + VERSION;
const FILES = ENTRIES.map((entry) => entry[0]);
const HASHES = new Map(ENTRIES);
const PAGE = FILES[0];

/** Where `name` is kept: the hash tells this build's copy from another's. */
function keyOf(name) {
  return `${name}?${HASHES.get(name).slice(0, 16)}`;
}

async function sha256(buffer) {
  const digest = await crypto.subtle.digest("SHA-256", buffer);
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, "0")).join("");
}

/**
 * Keeps every one of `names` in `cache`, or throws. A copy with the same hash in any cache (the
 * previous version's) is taken from there; the rest are fetched past the HTTP cache and checked:
 * a CDN still serving the old file mid-deploy fails the install instead of caching a mix.
 */
async function store(cache, names) {
  await Promise.all(names.map(async (name) => {
    let response = await caches.match(keyOf(name));
    if (response === undefined) {
      const fetched = await fetch(new Request(name, { cache: "reload" }));
      if (!fetched.ok) throw new Error(`${name}: ${fetched.status}`);
      const body = await fetched.arrayBuffer();
      if (await sha256(body) !== HASHES.get(name)) throw new Error(`${name}: not this build`);
      response = new Response(body, { status: 200, headers: fetched.headers });
    }
    await cache.put(keyOf(name), response);
  }));
}

/** Those of `names` that `cache` lacks. */
async function missing(cache, names = FILES) {
  const found = await Promise.all(names.map((name) => cache.match(keyOf(name))));
  return names.filter((name, i) => found[i] === undefined);
}

self.addEventListener("install", (event) => {
  event.waitUntil((async () => {
    const cache = await caches.open(CACHE_NAME);
    await store(cache, await missing(cache));
    await self.skipWaiting();
  })());
});

self.addEventListener("activate", (event) => {
  const activated = (async () => {
    const keys = await caches.keys();
    const old = keys.filter((key) => key.startsWith(CACHE_PREFIX) && key !== CACHE_NAME);
    await Promise.all(old.map((key) => caches.delete(key)));
    await self.clients.claim();
    return old.length > 0;
  })();
  event.waitUntil(activated);
  // After activating, never inside it: a reload is a navigation this worker has to answer.
  activated.then((updated) => {
    if (updated) reloadIdle();
  });
});

/** Reloads every page that is not busy into this version; the busy ones are told. */
async function reloadIdle() {
  const pages = await self.clients.matchAll({ type: "window" });
  await Promise.all(pages.map(async (page) => {
    if (await ask(page, "busy?", 1500) === "busy") {
      page.postMessage({ aurasync: "updated", version: VERSION });
      return;
    }
    page.navigate(fresh(page.url)).catch(() => {
      // Closed meanwhile: it opens the new version next time.
    });
  }));
}

function fresh(url) {
  const address = new URL(url);
  address.searchParams.set("v", VERSION);
  return address.href;
}

/** `question` to `page`, and its answer on a port, or null after `ms`. */
function ask(page, question, ms) {
  return new Promise((resolve) => {
    const channel = new MessageChannel();
    const timer = setTimeout(() => resolve(null), ms);
    channel.port1.onmessage = (event) => {
      clearTimeout(timer);
      resolve(event.data);
    };
    page.postMessage({ aurasync: question }, [channel.port2]);
  });
}

/** The name `request` has in `FILES`, or null: only this origin, under this scope. */
function listedName(request) {
  const url = new URL(request.url);
  const scope = new URL(self.registration.scope);
  if (url.origin !== scope.origin || !url.pathname.startsWith(scope.pathname)) return null;
  const name = url.pathname.slice(scope.pathname.length);
  if (request.mode === "navigate" && (name === "" || name === PAGE)) return PAGE;
  return FILES.includes(name) ? name : null;
}

async function fromCache(event, name) {
  const cache = await caches.open(CACHE_NAME);
  const hit = await cache.match(keyOf(name));
  if (hit !== undefined) return hit;
  // Not cached yet (a first visit still installing): the network, without storing it here, so
  // nothing unchecked enters the cache.
  return fetch(event.request);
}

self.addEventListener("fetch", (event) => {
  if (event.request.method !== "GET") return;
  const name = listedName(event.request);
  // Anything not listed (the device's API above all) goes to the network untouched.
  if (name !== null) event.respondWith(fromCache(event, name));
});

self.addEventListener("message", (event) => {
  if (event.origin && event.origin !== self.location.origin) return;
  const message = event.data || {};
  if (message.aurasync === "status" && event.ports[0]) {
    event.waitUntil((async () => {
      const cache = await caches.open(CACHE_NAME);
      const lacking = await missing(cache);
      event.ports[0].postMessage({ version: VERSION, cached: FILES.length - lacking.length, total: FILES.length });
    })());
  }
});
