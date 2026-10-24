import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { fileURLToPath, URL } from "node:url";
export default defineConfig(({ mode }) => ({
  base: "./",
  plugins: [react(), tailwindcss(), {
    name: "chat2local-transport",
    transformIndexHtml: () => mode === "web" ? [{ tag: "meta", attrs: { name: "chat2local-transport", content: "http" }, injectTo: "head" }] : [],
  }],
  build: mode === "web" ? {
    outDir: fileURLToPath(new URL("../../../src/chat2local/management/static", import.meta.url)),
    emptyOutDir: true,
  } : {},
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
  server: { port: 5173, strictPort: true },
}));
