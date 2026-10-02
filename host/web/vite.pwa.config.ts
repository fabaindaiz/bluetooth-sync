// `npm run build:pwa` (d-7c8794-37f9bc): the same Preact bundle as vite.config.ts, into dist-pwa/,
// and pwa/build.ts adds the rest of the panel, the manifest, the icons, the stamp and the service
// worker. dist-pwa/ is not versioned: .github/workflows/pages.yml builds and publishes it.
import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";
import { pwaFiles } from "./pwa/build.ts";

const hostDir = fileURLToPath(new URL("..", import.meta.url));
const outDir = fileURLToPath(new URL("dist-pwa/", import.meta.url));

export default defineConfig({
  oxc: { jsx: { runtime: "automatic", importSource: "preact" } },
  plugins: [pwaFiles(hostDir, outDir)],
  build: {
    outDir,
    emptyOutDir: true,
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
});
