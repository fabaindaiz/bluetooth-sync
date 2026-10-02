// The build's stamp (d-7c8794-6da524): which sources produced the compiled panel, and what it
// wrote. `vite build` writes it next to the output; host/scripts/web_stamp.py recomputes both
// hashes in Python, so scripts/check.sh can tell, without Node, that the versioned build
// matches host/web. Keep the two definitions identical (the Python file says the same).
import { createHash } from "node:crypto";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative, sep } from "node:path";

/** The files that decide the build, besides everything under src/. */
export const ROOT_FILES = [
  "package.json",
  "package-lock.json",
  "tsconfig.json",
  "tsconfig.node.json",
  "vite.config.ts",
  "stamp.ts",
];
/** What the build writes into the panel directory. */
export const OUTPUTS = ["cadena.js"];
export const STAMP = "cadena.build.json";

const sha256 = (data: Buffer | string): string => createHash("sha256").update(data).digest("hex");

function walk(dir: string): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir)) {
    if (name.startsWith(".")) continue;
    const path = join(dir, name);
    if (statSync(path).isDirectory()) out.push(...walk(path));
    else out.push(path);
  }
  return out;
}

/** sha256 of `"<path>\n<sha256 of its bytes>\n"` for each source, by path (POSIX, sorted). */
export function sourcesDigest(root: string): string {
  const paths = [...ROOT_FILES, ...walk(join(root, "src")).map((p) => relative(root, p).split(sep).join("/"))];
  const lines = [...new Set(paths)].sort().map((p) => `${p}\n${sha256(readFileSync(join(root, p)))}\n`);
  return sha256(lines.join(""));
}

export function stamp(root: string, outDir: string): string {
  const outputs = Object.fromEntries(OUTPUTS.map((name) => [name, sha256(readFileSync(join(outDir, name)))]));
  const body = {
    about:
      "Compilado de host/web con `npm run build` (d-7c8794-6da524). No se edita: scripts/check.sh lo comprueba sin Node con host/scripts/web_stamp.py.",
    sources_sha256: sourcesDigest(root),
    outputs,
  };
  return `${JSON.stringify(body, null, 2)}\n`;
}
