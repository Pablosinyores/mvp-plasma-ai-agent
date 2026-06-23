// Thin REST client over the FastAPI backend. Every call mirrors a `studio` CLI operation.
import { API_BASE } from "../config";
import type {
  InjectionResult,
  JobDetail,
  RefuelResult,
  ResolveResult,
  SpendResult,
  StudioState,
} from "../types";

async function req<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(API_BASE + path, {
    method: body ? "POST" : "GET",
    headers: body ? { "content-type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  let json: unknown;
  try {
    json = await res.json();
  } catch {
    throw new Error(`bad response (${res.status})`);
  }
  if (!res.ok || (json as { ok?: boolean }).ok === false) {
    throw new Error((json as { error?: string }).error ?? `request failed (${res.status})`);
  }
  return json as T;
}

export const api = {
  state: () => req<StudioState>("/api/state"),
  createAgent: (name: string, fund_usdt = 0) =>
    req<{ ok: true; agent: { agentId: number; address: string } }>("/api/agents", { name, fund_usdt }),
  resolve: (name: string) => req<ResolveResult>(`/api/agents/${name}/resolve`),
  fundJob: (name: string, prompt: string, budget = 5) =>
    req<{ ok: true; jobId: number }>("/api/jobs", { name, prompt, budget }),
  job: (id: number) => req<JobDetail>(`/api/jobs/${id}`),
  spend: (name: string) => req<SpendResult>("/api/spend", { name }),
  refuel: (name: string) => req<RefuelResult>("/api/refuel", { name }),
  injectionTest: () => req<InjectionResult>("/api/injection-test", {}),
};
