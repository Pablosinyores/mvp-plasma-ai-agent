import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Standalone FE dev server. Default port 5173. The backend base URL is read from
// VITE_API_BASE at runtime (see src/config.ts) — no proxy needed since the backend enables CORS.
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, host: true },
});
