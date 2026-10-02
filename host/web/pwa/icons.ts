// The PWA's icons, drawn at build time: no image files in the repository and no third-party marks.
// A sky-blue square with a dot and three arcs, like sound spreading from a speaker. The PNGs are
// encoded here with node:zlib (iOS wants a PNG for the home screen; Chrome wants 192 and 512).
import { deflateSync } from "node:zlib";

const BG: [number, number, number] = [2, 132, 199]; // sky-600, as the panel's primary button
const FG: [number, number, number] = [255, 255, 255];

const CRC_TABLE = (() => {
  const table = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    table[n] = c >>> 0;
  }
  return table;
})();

function crc32(data: Uint8Array): number {
  let c = 0xffffffff;
  for (const byte of data) c = CRC_TABLE[(c ^ byte) & 0xff]! ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}

function chunk(type: string, data: Uint8Array): Buffer {
  const out = Buffer.alloc(12 + data.length);
  out.writeUInt32BE(data.length, 0);
  out.write(type, 4, "ascii");
  Buffer.from(data).copy(out, 8);
  out.writeUInt32BE(crc32(new Uint8Array(out.subarray(4, 8 + data.length))), 8 + data.length);
  return out;
}

/** An RGBA image as a PNG file. */
export function encodePng(width: number, height: number, rgba: Uint8Array): Buffer {
  const header = Buffer.alloc(13);
  header.writeUInt32BE(width, 0);
  header.writeUInt32BE(height, 4);
  header.set([8, 6, 0, 0, 0], 8); // 8 bits, RGBA, deflate, no filter method, no interlace
  const raw = Buffer.alloc((width * 4 + 1) * height);
  for (let y = 0; y < height; y++) {
    raw[y * (width * 4 + 1)] = 0; // filter: none
    Buffer.from(rgba.subarray(y * width * 4, (y + 1) * width * 4)).copy(raw, y * (width * 4 + 1) + 1);
  }
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk("IHDR", header),
    chunk("IDAT", deflateSync(raw, { level: 9 })),
    chunk("IEND", new Uint8Array(0)),
  ]);
}

/** How much of the foreground covers a point, in units of the icon's size (0..1 coordinates). */
function coverage(x: number, y: number, px: number): number {
  // The mark sits inside the central 60 %: the safe zone of a maskable icon.
  const cx = 0.38;
  const cy = 0.5;
  const r = Math.hypot(x - cx, y - cy);
  const soft = (d: number): number => Math.min(1, Math.max(0, 0.5 - d / px));
  let cover = soft(r - 0.055); // the dot
  const angle = Math.atan2(y - cy, x - cx);
  if (Math.abs(angle) < Math.PI / 3.2) {
    for (const radius of [0.14, 0.22, 0.3]) cover = Math.max(cover, soft(Math.abs(r - radius) - 0.026));
  }
  return cover;
}

/** The icon as RGBA, full-bleed (the launcher rounds or masks it). */
export function drawIcon(size: number): Uint8Array {
  const out = new Uint8Array(size * size * 4);
  const px = 1 / size;
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      // 4 samples per pixel for smooth edges.
      let a = 0;
      for (const [dx, dy] of [[0.25, 0.25], [0.75, 0.25], [0.25, 0.75], [0.75, 0.75]] as const) {
        a += coverage((x + dx) * px, (y + dy) * px, px) / 4;
      }
      const i = (y * size + x) * 4;
      for (let c = 0; c < 3; c++) out[i + c] = Math.round(BG[c]! * (1 - a) + FG[c]! * a);
      out[i + 3] = 255;
    }
  }
  return out;
}

export function iconPng(size: number): Buffer {
  return encodePng(size, size, drawIcon(size));
}

/** The same mark as SVG, with rounded corners (for browsers that show it as is). */
export function iconSvg(): string {
  const arcs = [0.14, 0.22, 0.3]
    .map((r) => {
      const a = Math.PI / 3.2;
      const x1 = (0.38 + r * Math.cos(-a)) * 512;
      const y1 = (0.5 + r * Math.sin(-a)) * 512;
      const y2 = (0.5 + r * Math.sin(a)) * 512;
      return `<path d="M${x1.toFixed(1)} ${y1.toFixed(1)}A${(r * 512).toFixed(1)} ${(r * 512).toFixed(1)} 0 0 1 ${x1.toFixed(1)} ${y2.toFixed(1)}"/>`;
    })
    .join("");
  return (
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">` +
    `<rect width="512" height="512" rx="112" fill="rgb(${BG.join(",")})"/>` +
    `<circle cx="${0.38 * 512}" cy="256" r="${0.055 * 512}" fill="#fff"/>` +
    `<g fill="none" stroke="#fff" stroke-width="${0.052 * 512}" stroke-linecap="round">${arcs}</g></svg>\n`
  );
}
