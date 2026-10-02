/// <reference types="vitest/config" />
// Vite builds the panel's Preact screens into the service's panel directory, next to the
// hand-written app.js (d-7c8794-6da524: gradual migration). Stable names, no hashes: index.html
// loads /static/cadena.js. Nothing in the panel directory is deleted (emptyOutDir: false).
import { writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { defineConfig, type Plugin } from "vite";
import { STAMP, stamp } from "./stamp.ts";

const root = fileURLToPath(new URL(".", import.meta.url));
const outDir = fileURLToPath(new URL("../src/aurasync/panel/", import.meta.url));

function writeStamp(): Plugin {
  return {
    name: "aurasync-stamp",
    apply: "build",
    writeBundle() {
      writeFileSync(`${outDir}${STAMP}`, stamp(root, outDir));
    },
  };
}

export default defineConfig({
  oxc: { jsx: { runtime: "automatic", importSource: "preact" } },
  plugins: [writeStamp()],
  build: {
    outDir,
    emptyOutDir: false,
    copyPublicDir: false,
    modulePreload: false,
    target: "es2022",
    sourcemap: false,
    rolldownOptions: {
      input: { cadena: fileURLToPath(new URL("src/main.tsx", import.meta.url)) },
      output: {
        format: "es",
        entryFileNames: "[name].js",
        chunkFileNames: "[name].js",
        assetFileNames: "[name][extname]",
        codeSplitting: false,
      },
    },
  },
  test: { include: ["test/**/*.test.ts"], environment: "node" },
});
