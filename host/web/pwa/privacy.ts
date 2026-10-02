// The PWA is a public site (GitHub Pages): nothing in it may identify the user's devices, network or
// credentials (CLAUDE.md, privacy; d-7c8794-37f9bc). The build fails on any of these. The panel's
// own text must therefore never use a real-looking private address as an example.

export interface Hit {
  file: string;
  what: string;
  text: string;
}

export const PATTERNS: [string, RegExp][] = [
  ["a MAC address", /\b[0-9A-F]{2}([:_-])[0-9A-F]{2}(?:\1[0-9A-F]{2}){4}\b/gi],
  ["a private IPv4 address", /\b(?:10\.\d{1,3}|192\.168|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}(?:\.\d{1,3})?\b/g],
  ["a client token", /\basc_[0-9a-z]/gi],
  ["a Bluetooth sink name", /bluez_(?:output|input|card)\.[0-9A-F]{2}/gi],
  ["a machine of this project", /\b(?:pc-ryzen5|hp-o16)\b/gi],
  ["a research data path", /experimentos\/datos\//g],
];

/** Every match of a forbidden pattern in `files` (name → text). */
export function scan(files: Record<string, string>): Hit[] {
  const hits: Hit[] = [];
  for (const [file, text] of Object.entries(files)) {
    for (const [what, pattern] of PATTERNS) {
      for (const match of text.matchAll(new RegExp(pattern.source, pattern.flags))) {
        hits.push({ file, what, text: match[0] });
      }
    }
  }
  return hits;
}
