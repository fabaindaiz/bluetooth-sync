// The pairing link a device's QR carries: `https://…/bluetooth-sync/#d=<host>:<port>&fp=<root
// SHA-256>`. The data goes in the fragment, which the browser never sends to GitHub Pages
// (research H §3). No DOM here: test/link.test.ts runs it in Node.
import { compactFingerprint, normalizeAddress } from "../transport.ts";

export interface Link {
  address: string;
  /** Upper-case hex, no separators; "" when the link carried none. */
  fp: string;
}

export function parseLink(hash: string): Link | null {
  const params = new URLSearchParams(hash.replace(/^#/, ""));
  const d = params.get("d");
  if (!d) return null;
  const address = normalizeAddress(d);
  if (!address) return null;
  const fp = compactFingerprint(params.get("fp") ?? "");
  return { address, fp: fp.length === 64 ? fp : "" };
}

/** "hace 3 min", "hace 2 h", "hace 4 días"; "nunca" for 0. */
export function ago(then: number, now: number = Date.now()): string {
  if (!then) return "nunca";
  const s = Math.max(0, Math.round((now - then) / 1000));
  if (s < 60) return "hace un momento";
  if (s < 3600) return `hace ${Math.round(s / 60)} min`;
  if (s < 86_400) return `hace ${Math.round(s / 3600)} h`;
  const days = Math.round(s / 86_400);
  return days === 1 ? "hace 1 día" : `hace ${days} días`;
}
