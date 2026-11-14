import { fileURLToPath } from "node:url";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

const contracts = fileURLToPath(new URL("../../packages/contracts/ts/contracts.ts", import.meta.url));
const opsTable = fileURLToPath(new URL("../../packages/contracts/schemas/geometry_ops.json", import.meta.url));

// Static build (deployable to Vercel / any static host). No secrets are read here: only VITE_* variables
// from the repository root .env files are exposed to the bundle.
export default defineConfig({
  plugins: [react()],
  envDir: "../..",
  resolve: { alias: { "@contracts": contracts, "@ops-table": opsTable } },
  server: { port: 5173, strictPort: true, fs: { allow: ["../.."] } },
  build: { target: "es2022", chunkSizeWarningLimit: 1500 },
  test: { include: ["tests/**/*.test.ts"], environment: "node" },
});
