// Backend wiring. Override via .env (VITE_API_BASE / VITE_WS_URL) — see .env.example.
const fromEnv = (import.meta.env.VITE_API_BASE as string | undefined)?.replace(/\/$/, "");

export const API_BASE = fromEnv ?? "http://localhost:8080";

export const WS_URL =
  (import.meta.env.VITE_WS_URL as string | undefined) ??
  API_BASE.replace(/^http/, "ws") + "/ws";
