// Thin REST client over the FastAPI backend. Every call mirrors a `studio` CLI operation.
import { API_BASE } from "../config";
import type {
  InjectionResult,
  JobDetail,
  RefuelResult,
  ResolveResult,
  SessionAuthorizeResult,
  SessionState,
  SpendResult,
  StrategyState,
  StudioState,
} from "../types";

export interface AuthorizePolicy {
  maxInPerTrade: number; // funding-token base units (USDC, 6dp)
  sessionInCap: number;
  slippageBps?: number;
  expirySecs?: number;
  buys?: string[];
  funding?: string;
}

async function req<T>(path: string, body?: unknown, method?: string): Promise<T> {
  const res = await fetch(API_BASE + path, {
    method: method ?? (body ? "POST" : "GET"),
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
  setStrategy: (name: string, prompt: string) =>
    req<StrategyState>(`/api/agents/${name}/strategy`, { prompt }),
  getStrategy: (name: string) => req<StrategyState>(`/api/agents/${name}/strategy`),
  clearStrategy: (name: string) =>
    req<{ ok: true }>(`/api/agents/${name}/strategy`, undefined, "DELETE"),

  // --- EIP-7702 user-funded rail (trade from the connected wallet's own address) ---
  sessionAuthorize: (user: string, policy: AuthorizePolicy) =>
    req<SessionAuthorizeResult>(`/api/session/${user}/authorize`, policy),
  // DEMO: locally play the wallet (delegate + installSession + seed funds). Prod = wallet self-signs.
  sessionBootstrap: (user: string) =>
    req<SessionState>(`/api/session/${user}/dev-bootstrap`, {}),
  // PROD: the wallet did the delegation + installSession on-chain itself; flip the session live.
  sessionInstalled: (user: string) =>
    req<SessionState>(`/api/session/${user}/installed`, {}),
  sessionSetStrategy: (user: string, prompt: string) =>
    req<SessionState>(`/api/session/${user}/strategy`, { prompt }),
  sessionGet: (user: string) => req<SessionState>(`/api/session/${user}/strategy`),
  sessionStop: (user: string) =>
    req<{ ok: true }>(`/api/session/${user}/strategy`, undefined, "DELETE"),
  sessionRevoke: (user: string) =>
    req<{ ok: true; revoke: { sessionKey: string } }>(`/api/session/${user}/revoke`, {}),
};
