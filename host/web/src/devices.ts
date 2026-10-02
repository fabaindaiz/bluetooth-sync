// The devices this browser remembers (the PWA only): their address, name, root fingerprint and the
// client token each one gave it. Kept in IndexedDB, or in localStorage when IndexedDB is not
// there (a private window, a WebView); every access is in a try/catch, and with neither the app
// still opens: it just forgets on reload.
//
// The token is the client's credential and is stored as is: a page has no way to keep a secret
// from its own scripts (research H §4.3). The service keeps only its hash.

export interface Device {
  /** The service's `hello.id`: stays when the address changes. */
  id: string;
  /** `host:port` of its HTTPS listener. */
  address: string;
  name: string;
  version: string;
  /** SHA-256 of the service's root certificate, `AB:CD:…`. */
  rootSha256: string;
  /** When it last answered (ms since the epoch). */
  lastSeen: number;
  token: string | null;
  scope: string | null;
  clientId: string | null;
}

export interface Saved {
  devices: Device[];
  active: string | null;
}

const DB = "aurasync";
const STORE = "kv";
const KEY = "devices";
const LOCAL_KEY = "aurasync.devices";

function empty(): Saved {
  return { devices: [], active: null };
}

function valid(value: unknown): value is Saved {
  return Boolean(value) && Array.isArray((value as Saved).devices);
}

function openDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB, 1);
    request.onupgradeneeded = () => request.result.createObjectStore(STORE);
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
    request.onblocked = () => reject(new Error("blocked"));
  });
}

async function idb<T>(mode: IDBTransactionMode, run: (store: IDBObjectStore) => IDBRequest<T>): Promise<T> {
  const db = await openDb();
  try {
    return await new Promise<T>((resolve, reject) => {
      const tx = db.transaction(STORE, mode);
      const request = run(tx.objectStore(STORE));
      tx.oncomplete = () => resolve(request.result);
      tx.onerror = () => reject(tx.error);
      tx.onabort = () => reject(tx.error);
    });
  } finally {
    db.close();
  }
}

/** Where the last load or save went: `idb`, `local` or `none` (the status line shows it). */
export let storageKind: "idb" | "local" | "none" = "none";

export async function load(): Promise<Saved> {
  try {
    const value = await idb("readonly", (s) => s.get(KEY));
    if (valid(value)) {
      storageKind = "idb";
      return value;
    }
  } catch {
    // No IndexedDB here: try localStorage.
  }
  try {
    const text = localStorage.getItem(LOCAL_KEY);
    const value: unknown = text ? JSON.parse(text) : null;
    storageKind = "local";
    if (valid(value)) return value;
  } catch {
    storageKind = "none";
  }
  return empty();
}

export async function save(saved: Saved): Promise<void> {
  const plain: Saved = JSON.parse(JSON.stringify(saved)) as Saved;
  try {
    await idb("readwrite", (s) => s.put(plain, KEY));
    storageKind = "idb";
    // One copy only: a stale localStorage copy would come back if IndexedDB is cleared.
    try {
      localStorage.removeItem(LOCAL_KEY);
    } catch {
      // Nothing to clean.
    }
    return;
  } catch {
    // Fall back below.
  }
  try {
    localStorage.setItem(LOCAL_KEY, JSON.stringify(plain));
    storageKind = "local";
  } catch {
    storageKind = "none";
  }
}

/** `saved` with `device` added or updated (by id; an old entry at the same address is replaced). */
export function upsert(saved: Saved, device: Device): Saved {
  const devices = saved.devices.filter((d) => d.id !== device.id && d.address !== device.address);
  return { ...saved, devices: [...devices, device] };
}

export function forget(saved: Saved, id: string): Saved {
  return { devices: saved.devices.filter((d) => d.id !== id), active: saved.active === id ? null : saved.active };
}

export function activeDevice(saved: Saved): Device | null {
  return saved.devices.find((d) => d.id === saved.active) ?? null;
}

/** A name for this browser to ask with: "iPhone", "Android · Chrome", "Mac · Firefox"… */
export function browserName(ua: string = typeof navigator === "undefined" ? "" : navigator.userAgent): string {
  const device = /iPhone/.test(ua) ? "iPhone" : /iPad/.test(ua) ? "iPad" : /Android/.test(ua) ? "Android"
    : /Mac OS X|Macintosh/.test(ua) ? "Mac" : /Windows/.test(ua) ? "Windows" : /Linux/.test(ua) ? "Linux" : "Navegador";
  const browser = /Firefox\//.test(ua) ? "Firefox" : /Edg\//.test(ua) ? "Edge"
    : /Chrome\/|CriOS\//.test(ua) ? "Chrome" : /Safari\//.test(ua) ? "Safari" : "";
  return browser ? `${device} · ${browser}` : device;
}
