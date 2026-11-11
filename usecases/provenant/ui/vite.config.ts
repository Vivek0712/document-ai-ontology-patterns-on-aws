/// <reference types="vitest" />
import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";

const contracts = fileURLToPath(new URL("../../../contracts", import.meta.url));
const repoRoot = fileURLToPath(new URL("../../..", import.meta.url));

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "VITE_");
  const apiMode = env.VITE_API_MODE === "mock" || env.VITE_API_MODE === "replay" ? env.VITE_API_MODE : "http";
  return {
    plugins: [react()],
    // mock fixtures (world.json, page renders) live in mock-public/ and are only served in mock/replay mode
    publicDir: apiMode === "http" ? "public" : "mock-public",
    resolve: { alias: { "@contracts": contracts } },
    define: {
      __RP_MOCK__: JSON.stringify(apiMode !== "http"),
      __RP_API_MODE__: JSON.stringify(apiMode),
      // amazon-cognito-identity-js expects a Node-style `global`
      global: "globalThis",
    },
    server: { port: 5188, strictPort: true, fs: { allow: [repoRoot] } },
    build: { outDir: "dist", sourcemap: true, chunkSizeWarningLimit: 900 },
    test: { environment: "node", include: ["src/**/*.test.ts"] },
  };
});
