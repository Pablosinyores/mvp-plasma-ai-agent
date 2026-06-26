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
  client: string;
  provider: string;
  budget: number;
  descHash: string;
  resultHash: string;
  uri: string;
  verified: boolean | null;
  output: string | null;
}

export interface ResolveResult {
  ok: boolean;
  agentId: number;
  owner: string;
  cardURI: string;
  card: Record<string, unknown>;
}

// --- agentic trader: a standing strategy + its live ticks (mirrors strategy_ctl.py) ---
export interface StrategyOrder {
  op: "swap" | "dca" | "rebalance" | "limit" | "noop";
  sell?: string;
  buy?: string;
  amount?: number;
  everyTicks?: number;
  base?: string;
  quote?: string;
  targetBps?: number;
  when?: { sym: string; cmp: "lt" | "gt"; price: number };
  reason?: string;
}

export interface StrategyTick {
  tick?: number;
  action: "trade" | "hold" | "blocked" | "noop";
  reason?: string;
  price?: number;
  threshold?: number;
  watch?: string;
  cmp?: string;
  sell?: string;
  buy?: string;
  amountIn?: number;
  minOut?: number;
  notionalUsdc?: number;
  txHash?: string;
  // present on the EIP-7702 user-funded rail
  rail?: string;
  from?: string;
  spentIn?: number;
}

// --- EIP-7702 "trade from your own wallet" rail (mirrors session_ctl.py) ---
export interface SessionPolicy {
  active: boolean;
  expiry: number;
  fundingToken: string;
  maxInPerTrade: number;
  sessionInCap: number;
  spentIn: number;
  maxSlippageBps: number;
}

export interface SessionAuthorizeResult {
  ok?: boolean;
  user: string;
  delegate: string;
  sessionKey: string;
  chainId: number;
  authorization: { chainId: number; address: string; nonce: number };
  install: { to: string; function: string; policy: Record<string, unknown>; buys: string[]; pools: string[] };
}

export interface SessionState {
  ok?: boolean;
  user: string;
  authorized: boolean;
  installed?: boolean;
  rail?: string;
  sessionKey?: string;
  delegate?: string;
  strategy?: StrategyOrder | null;
  prompt?: string | null;
  tickCount?: number;
  swapDone?: boolean;
  ticks?: StrategyTick[];
  policy?: SessionPolicy | null;
}

export interface StrategyState {
  ok?: boolean;
  name?: string;
  address: string;
  strategy: StrategyOrder | null;
  prompt: string | null;
  tickCount: number;
  swapDone: boolean;
  ticks: StrategyTick[];
  order?: StrategyOrder; // present on the POST response (the freshly parsed order)
}

export const EMPTY_STATE: StudioState = {
  agents: [],
  jobs: [],
  events: [],
  stats: { agentCount: 0, jobCount: 0, earned: 0, spent: 0, refueled: 0 },
};
