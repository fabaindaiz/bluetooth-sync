import { inflateSync } from "node:zlib";
import { describe, expect, it } from "vitest";
import { manifest, pwaIndex, serviceWorker } from "../pwa/build.ts";
import { iconPng } from "../pwa/icons.ts";
import { scan } from "../pwa/privacy.ts";
import { busy } from "../src/remote.ts";
import { fresh, statusLine, withoutVersion } from "../src/pwa.ts";

describe("the public build", () => {
  it("finds what must never be published", () => {
    const leaks = ["90:F2:60:75:4A:83", "192.168.1.50", "10.0.0.7", "172.20.1.2", "asc_942e018c_x", "bluez_output.90_F2_60", "PC-Ryzen5"];
    for (const leak of leaks) expect(scan({ "a.js": `x = "${leak}"` }), leak).not.toEqual([]);
    expect(scan({ "a.js": `fetch("https://127.0.0.1:8443/v1/hello"); "aurasync.local:8443"; t.replace("bluez_output.", "")` })).toEqual([]);
  });

  it("index.html: relative paths, the remote mode and the manifest", () => {
    const html = '<title>aurasync · panel</title><link href="/static/tailwind.css"><script src="/static/app.js"></script>';
    const out = pwaIndex(html);
    expect(out).toContain('href="./tailwind.css"');
    expect(out).toContain('<meta name="aurasync-mode" content="remote">');
    expect(out).toContain('rel="manifest"');
    expect(out).not.toContain("/static/");
    expect(() => pwaIndex("<title>otro</title>")).toThrow();
  });

  it("the manifest works under any path", () => {
    const m = JSON.parse(manifest());
    expect([m.start_url, m.scope, m.display]).toEqual(["./", "./", "standalone"]);
  });

  it("sw.js gets every file and a version that changes with the content only", () => {
    const template = 'const VERSION = "__AURASYNC_VERSION__";\nconst ENTRIES = __AURASYNC_FILES__;\n';
    const a = serviceWorker(template, [["index.html", "aa"]], "1.0");
    expect(a).toContain('const ENTRIES = [["index.html","aa"]];');
    expect(serviceWorker(template, [["index.html", "aa"]], "1.0")).toBe(a);
    expect(serviceWorker(template, [["index.html", "bb"]], "1.0")).not.toBe(a);
  });

  it("the icons are real PNGs of their size", () => {
    const png = iconPng(48);
    expect(png.subarray(1, 4).toString()).toBe("PNG");
    expect(png.readUInt32BE(16)).toBe(48);
    const idat = png.indexOf("IDAT");
    const pixels = inflateSync(png.subarray(idat + 4, idat + 4 + png.readUInt32BE(idat - 4)));
    expect(pixels.length).toBe((48 * 4 + 1) * 48);
  });
});

describe("updates", () => {
  it("never in the middle of a calibration or a blind A/B", () => {
    expect(busy(null)).toBe(false);
    expect(busy({ calibration: { state: "done" }, ab: { active: false } })).toBe(false);
    expect(busy({ calibration: { state: "measuring" } })).toBe(true);
    expect(busy({ calibration: { state: "running" } })).toBe(true);
    expect(busy({ ab: { active: true } })).toBe(true);
  });

  it("the reload address changes and is cleaned after", () => {
    const url = fresh("https://x.github.io/bluetooth-sync/?layout=inicio#v=escuchar", "1.0-abc");
    expect(url).toBe("https://x.github.io/bluetooth-sync/?layout=inicio&v=1.0-abc#v=escuchar");
    expect(withoutVersion(url)).toBe("https://x.github.io/bluetooth-sync/?layout=inicio#v=escuchar");
  });

  it("the status line", () => {
    expect(statusLine({ sw: "active", cached: 9, total: 10, upd: "ready", version: "", build: { version: "0.0.0", commit: "abc" } })).toBe(
      "sw active 9/10  upd ready  0.0.0 abc",
    );
  });
});
