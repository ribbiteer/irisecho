import { svelte } from "@sveltejs/vite-plugin-svelte";
import { defineConfig } from "vite";

// The core serves the built UI, so the build goes straight into its package.
export default defineConfig({
  plugins: [svelte()],
  build: {
    outDir: "../core/src/irisecho_core/web",
    emptyOutDir: true,
    assetsInlineLimit: 0,
    chunkSizeWarningLimit: 800,
  },
});
