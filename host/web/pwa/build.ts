// `npm run build:pwa`: the panel as a PWA for GitHub Pages, in host/web/dist-pwa/ (not versioned;
// the workflow .github/workflows/pages.yml builds and publishes it). d-7c8794-37f9bc.
//
// The same panel as the one the service serves: Vite builds src/main.tsx into cadena.js as for
// the local panel, and this plugin adds the rest from host/src/aurasync/panel/ — index.html (its
// `/static/` paths made relative, so it works under /bluetooth-sync/, and marked
// `aurasync-mode=remote`), app.js and tailwind.css — plus the manifest, the icons, build.json (the
// stamp: version and commit) and sw.js with every file's SHA-256. Then it scans the output for
// anything private (pwa/privacy.ts) and fails the build on a hit.
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { readFileSync, readdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import type { Plugin } from "vite";
import { iconPng, iconSvg } from "./icons.ts";
import { scan } from "./privacy.ts";

export const PAGE = "index.html";
const COPIED = ["app.js", "tailwind.css"];

const sha256 = (data: Buffer | string): string => createHash("sha256").update(data).digest("hex");

/** Replace `from` once in `text`, or fail: a panel that changed shape must not build silently. */
function once(text: string, from: string, to: string): string {
  const at = text.indexOf(from);
  if (at < 0 || text.indexOf(from, at + from.length) >= 0) {
    throw new Error(`build:pwa: expected exactly one ${JSON.stringify(from)} in index.html`);
  }
  return text.replace(from, to);
}

export function pwaIndex(html: string): string {
  let out = html.replaceAll('"/static/', '"./');
  if (out.includes("/static/")) throw new Error("build:pwa: index.html still names /static/ outside an attribute");
  out = once(
    out,
    "<title>aurasync · panel</title>",
    [
      "<title>aurasync</title>",
      '  <meta name="aurasync-mode" content="remote">',
      '  <meta name="theme-color" content="#0284c7">',
      '  <meta name="apple-mobile-web-app-capable" content="yes">',
      '  <meta name="apple-mobile-web-app-title" content="aurasync">',
      '  <link rel="manifest" href="./manifest.webmanifest">',
      '  <link rel="icon" href="./icon.svg" type="image/svg+xml">',
      '  <link rel="apple-touch-icon" href="./apple-touch-icon.png">',
    ].join("\n"),
  );
  return out;
}

export function manifest(): string {
  const body = {
    name: "aurasync",
    short_name: "aurasync",
    description: "Panel de aurasync: se conecta por la red local al servicio de cada equipo.",
    lang: "es",
    // Relative: they resolve against the manifest's URL, so the app works under /bluetooth-sync/.
    start_url: "./",
    scope: "./",
    display: "standalone",
    background_color: "#f4f4f5",
    theme_color: "#0284c7",
    icons: [
      { src: "icon.svg", sizes: "any", type: "image/svg+xml", purpose: "any" },
      { src: "icon-192.png", sizes: "192x192", type: "image/png", purpose: "any" },
      { src: "icon-512.png", sizes: "512x512", type: "image/png", purpose: "any" },
      { src: "icon-512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
    ],
  };
  return `${JSON.stringify(body, null, 2)}\n`;
}

function hostVersion(hostDir: string): string {
  const init = readFileSync(join(hostDir, "src/aurasync/__init__.py"), "utf8");
  return /__version__\s*=\s*"([^"]+)"/.exec(init)?.[1] ?? "0.0.0";
}

/** The commit this build comes from: GitHub's, or git's, marked `-dirty` with local changes. */
function commit(hostDir: string): string {
  if (process.env["GITHUB_SHA"]) return process.env["GITHUB_SHA"].slice(0, 12);
  try {
    const head = execFileSync("git", ["rev-parse", "--short=12", "HEAD"], { cwd: hostDir, encoding: "utf8" }).trim();
    const dirty = execFileSync("git", ["status", "--porcelain", "--", "web", "src/aurasync/panel"], {
      cwd: hostDir,
      encoding: "utf8",
    }).trim();
    return dirty ? `${head}-dirty` : head;
  } catch {
    return "unknown";
  }
}

/** sw.js with its constants: the version is the content's own hash, so an unchanged build is no update. */
export function serviceWorker(template: string, entries: [string, string][], version: string): string {
  const swVersion = `${version}-${sha256(JSON.stringify(entries)).slice(0, 12)}`;
  const out = template
    .replace('"__AURASYNC_VERSION__"', JSON.stringify(swVersion))
    .replace("__AURASYNC_FILES__", JSON.stringify(entries));
  if (out.includes("__AURASYNC_")) throw new Error("build:pwa: sw.js still has a placeholder");
  return out;
}

export function pwaFiles(hostDir: string, outDir: string): Plugin {
  const panelDir = join(hostDir, "src/aurasync/panel");
  return {
    name: "aurasync-pwa",
    apply: "build",
    writeBundle() {
      const write = (name: string, data: Buffer | string): void => writeFileSync(join(outDir, name), data);
      write(PAGE, pwaIndex(readFileSync(join(panelDir, PAGE), "utf8")));
      for (const name of COPIED) write(name, readFileSync(join(panelDir, name)));
      write("manifest.webmanifest", manifest());
      write("icon.svg", iconSvg());
      write("icon-192.png", iconPng(192));
      write("icon-512.png", iconPng(512));
      write("apple-touch-icon.png", iconPng(180));
      const version = hostVersion(hostDir);
      write("build.json", `${JSON.stringify({ name: "aurasync", version, commit: commit(hostDir) }, null, 2)}\n`);
      const names = readdirSync(outDir).filter((n) => n !== "sw.js" && !n.startsWith(".")).sort();
      const ordered = [PAGE, ...names.filter((n) => n !== PAGE)];
      const entries = ordered.map((n): [string, string] => [n, sha256(readFileSync(join(outDir, n)))]);
      write("sw.js", serviceWorker(readFileSync(join(hostDir, "web/pwa/sw.js"), "utf8"), entries, version));
      const texts = Object.fromEntries(
        readdirSync(outDir)
          .filter((n) => !n.endsWith(".png"))
          .map((n) => [n, readFileSync(join(outDir, n), "utf8")]),
      );
      const hits = scan(texts);
      if (hits.length) {
        const list = hits.map((h) => `  ${h.file}: ${h.what} (${h.text})`).join("\n");
        throw new Error(`build:pwa: the public site would carry private data:\n${list}`);
      }
    },
  };
}
