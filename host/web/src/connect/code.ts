// The connection code (host/src/aurasync/connection_code.py, the same encoding): one short code that
// carries the device's IPv4 address, its port when it is not 8443, the 6-digit pairing code and the
// first 20 bits of its root's SHA-256, in Crockford base32 with a check symbol. Typed once in the
// PWA, it finds the device, checks it is the right one and pairs. No DOM here: test/code.test.ts.

const ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ";
const TYPED_AS: Record<string, string> = { O: "0", I: "1", L: "1", U: "V" };
const DEFAULT_PORT = 8443;
const FP_BITS = 20;
const CODE_BITS = 20;
// [first address, bits] of the private ranges (192.168/16, 10/8, 172.16/12), then any IPv4 in 32 bits.
// As numbers, not dotted text: the public build refuses anything that reads as a private address
// (pwa/privacy.ts).
const CLASSES: [number, number][] = [
  [0xc0a8_0000, 16],
  [0x0a00_0000, 24],
  [0xac10_0000, 20],
];

export interface Decoded {
  /** host:port, as normalizeAddress gives it. */
  address: string;
  pairing: string;
  /** The first five hex digits of the root's SHA-256, upper case. */
  fpPrefix: string;
}

function ip(text: string): number {
  return text.split(".").reduce((acc, part) => acc * 256 + Number(part), 0);
}

function dotted(n: number): string {
  return [24, 16, 8, 0].map((s) => Math.floor(n / 2 ** s) % 256).join(".");
}

const check = (symbols: number[]): number => symbols.reduce((acc, s, i) => acc + (i + 1) * s, 0) % 31;

const bin = (value: number, width: number): string => value.toString(2).padStart(width, "0");

function clean(text: string): string {
  return [...text.toUpperCase()].filter((c) => c !== " " && c !== "-").map((c) => TYPED_AS[c] ?? c).join("");
}

/** Whether a typed text is meant as a code rather than an address (no dot or colon in it). */
export function looksLikeCode(text: string): boolean {
  const t = clean(text);
  return t.length >= 12 && !/[.:/]/.test(text) && [...t].every((c) => ALPHABET.includes(c));
}

export function decodeCode(text: string): Decoded | null {
  if (/[.:/]/.test(text)) return null;
  const plain = clean(text);
  if (!plain || ![...plain].every((c) => ALPHABET.includes(c))) return null;
  const values = [...plain].map((c) => ALPHABET.indexOf(c));
  const symbols = values.slice(0, -1);
  if (!symbols.length || check(symbols) !== values[values.length - 1]) return null;
  const bits = symbols.map((s) => bin(s, 5)).join("");
  let pos = 0;
  const take = (n: number): number | null => {
    if (pos + n > bits.length) return null;
    const v = parseInt(bits.slice(pos, pos + n), 2);
    pos += n;
    return v;
  };
  const kind = take(2);
  if (kind === null) return null;
  let host: number | null;
  const range = CLASSES[kind];
  if (range) {
    const [base, width] = range;
    const offset = take(width);
    host = offset === null ? null : base + offset;
  } else {
    host = take(32);
  }
  const custom = take(1);
  const port = custom ? take(16) : DEFAULT_PORT;
  const pairing = take(CODE_BITS);
  const fp = take(FP_BITS);
  if (host === null || custom === null || port === null || pairing === null || fp === null) return null;
  const rest = bits.slice(pos);
  if (rest.length >= 5 || /1/.test(rest) || pairing >= 1_000_000) return null;
  return {
    address: `${dotted(host)}:${port}`,
    pairing: String(pairing).padStart(6, "0"),
    fpPrefix: fp.toString(16).toUpperCase().padStart(5, "0"),
  };
}

/** For the tests (the service makes the codes): the same as connection_code.encode. */
export function encodeCode(host: string, port: number, pairing: string, rootSha256: string): string {
  const n = ip(host);
  let bits = "";
  const found = CLASSES.findIndex(([base, width]) => n >= base && n < base + 2 ** width);
  const range = CLASSES[found];
  bits += range ? bin(found, 2) + bin(n - range[0], range[1]) : "11" + bin(n, 32);
  bits += port === DEFAULT_PORT ? "0" : "1" + bin(port, 16);
  bits += bin(Number(pairing), CODE_BITS) + bin(parseInt(rootSha256.replace(/:/g, "").slice(0, 5), 16), FP_BITS);
  bits += "0".repeat((5 - (bits.length % 5)) % 5);
  const symbols: number[] = [];
  for (let i = 0; i < bits.length; i += 5) symbols.push(parseInt(bits.slice(i, i + 5), 2));
  const text = [...symbols, check(symbols)].map((s) => ALPHABET[s]).join("");
  return text.match(/.{1,4}/g)?.join("-") ?? text;
}
