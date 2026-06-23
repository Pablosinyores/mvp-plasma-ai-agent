// Mirrors the JSON shapes from studio_api/app.py (the FastAPI backend).

export interface Agent {
  name: string;
  agentId: number;
  address: string;
  eth: number;
  usdt: number;
}

export interface Job {
  jobId: number;
  status: "FUNDED" | "SUBMITTED" | "COMPLETED" | "REJECTED" | "REFUNDED" | string;
  provider: string;
  budget: number;
}

export interface FeedEvent {
  kind: "spend" | "refuel";
  amount: number;
  from: string;
  to: string;
}

export interface Stats {
  agentCount: number;
  jobCount: number;
  earned: number;
  spent: number;
  refueled: number;
}

export interface StudioState {
  chain?: number;
  agents: Agent[];
  jobs: Job[];
  events: FeedEvent[];
  stats: Stats;
  error?: string;
}

export interface SpendResult {
  ok: boolean;
  status?: number;
  price?: number;
  payee?: string;
  payeeBalance?: number;
  spent: number;
  remaining: number;
  blocked?: string;
  reason?: string;
}

export interface RefuelResult {
  ok: boolean;
  before: number;
  after: number;
  refuel1: { fired: boolean; reason: string };
  refuel2: { fired: boolean; reason: string };
}

export interface Guard {
  guard: string;
  blocked: boolean;
  detail: string;
}

export interface InjectionResult {
  ok: boolean;
  modelOutput: string;
  guards: Guard[];
  spent: number;
  summary: string;
}

export interface JobDetail {
  ok: boolean;
  jobId: number;
  status: Job["status"];
  provider: string;
  budget: number;
  output: string | null;
}

export interface ResolveResult {
  ok: boolean;
  agentId: number;
  owner: string;
  cardURI: string;
  card: Record<string, unknown>;
}

export const EMPTY_STATE: StudioState = {
  agents: [],
  jobs: [],
  events: [],
  stats: { agentCount: 0, jobCount: 0, earned: 0, spent: 0, refueled: 0 },
};
