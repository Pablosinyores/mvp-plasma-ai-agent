# Milestones — MVP Plasma AI Agent

Three milestones. Each is **independently runnable and testable on localhost**, builds strictly on the
previous one, and ends with a concrete acceptance test you can execute. Stack: Anvil (chain) +
LocalStack (AWS services) + a llama.cpp model container (CPU, OpenAI-compatible; tests use a `stub`).

```
M1  Chain + Identity + cloud emulation        →  an agent exists on-chain, key in KMS
        │
        ▼
M2  Earning agent + local model (the core loop) →  fund → model works → settle → paid
        │
        ▼
M3  Spend + guardrails + dashboard             →  agent pays safely, caps hold, observable
```

---

## Milestone 1 — Foundation: local chain, identity, and AWS emulation

**Goal:** stand up the local infra and prove an agent can be given an on-chain identity, with its
key custodied through LocalStack KMS (never plaintext) and its Agent Card stored in LocalStack S3.

### Build
- `docker-compose.yml` with **anvil**, **localstack** (the **model** service is added in M2).
- `localstack/init.sh` (a LocalStack init hook) creates: S3 bucket `agent-cards`, KMS key
  `agent-master`, Secrets Manager path `agents/`, DynamoDB table `agents`, SQS queue `settle`.
- Foundry project: `MockUSDT` (ERC-20, 6 decimals, `mint`), `IdentityRegistry` (ERC-721 +
  `register(cardURI)→agentId`, `cardURI(agentId)`). `Deploy.s.sol` deploys both to Anvil.
- SDK: `LocalAdapter` (web3.py, relayer pays gas), `aws` (boto3→LocalStack), `keyvault`
  (generate keypair → encrypt with KMS → store in Secrets Manager), `storage` (S3 put/get).
- CLI: `studio up`, `studio down`, `studio create <name>`, `studio resolve <name>`.

### `studio create <name>` does
1. `keyvault.new_agent_key()` → KMS-encrypted private key saved to Secrets Manager; returns address.
2. fund that address with test ETH (Anvil) so it can transact; `MockUSDT.mint` a small float (optional).
3. build Agent Card JSON → `storage.put` to S3 → `cardURI = s3://agent-cards/<keccak>`.
4. `adapter.register(cardURI)` → `IdentityRegistry` mints `agentId` NFT to the agent address.
5. write `DynamoDB agents` row + local `.agent/<name>.json`.

### ✅ Acceptance test (local)
```bash
make up                                  # anvil + localstack healthy (model not needed for M1)
forge test                               # contract unit tests green (mint, register, cardURI)
studio create alpha                      # prints agentId + address
studio resolve alpha                     # fetches cardURI from chain, loads JSON from S3, prints it
pytest tests/test_m1.py                  # asserts:
#   - agentId NFT owner == agent address
#   - cardURI on-chain resolves to the S3 object
#   - private key is NOT retrievable in plaintext (only KMS-decryptable)
#   - DynamoDB 'agents' row exists
```
**Done when:** an agent has an on-chain identity, its card is in S3, its key is KMS-encrypted in
Secrets Manager, and all four assertions pass — all offline.

> **No-Docker fast path.** `make test` (= `forge test` + `pytest tests/test_m1_aws_moto.py`) verifies
> the contracts and the full AWS code path (S3/KMS/SecretsManager/DynamoDB) in-process via `moto` —
> no Docker required. The LocalStack-backed combined e2e (`make test-e2e`) additionally proves the
> on-chain ↔ S3 round-trip and needs `make up` first.

---

## Milestone 2 — The core loop: an earning agent powered by a local model

**Goal:** the headline demo. A buyer escrows MockUSDT for a job; the agent's poll loop detects it,
runs the job through the **local model** (llama.cpp container, or the `stub` backend in tests),
uploads the result to S3, submits on-chain, and after the dispute window the job settles and USDT is
released to the agent.

### Build
- Contract: `Commerce` (ERC-8183-lite) — `createJob(provider, descHash, expiresAt)`,
  `fund(jobId, amount)` (pulls MockUSDT into escrow), `submit(jobId, resultHash, uri)`,
  `settle(jobId)` (permissionless, after dispute window), `claimRefund(jobId)` (non-pausable).
  Immutable `paymentToken = MockUSDT`.
- Runtime: FastAPI app + **poll loop** (`poll_funded_jobs(me)` every ~5s on Anvil) + `on_job` hook.
- Model gateway (`model/`): `complete(prompt, system) → text`, backend-pluggable (`stub` | `llamacpp`).
- `on_job` default: fetch the job prompt from S3 (by descHash), run the model, return the completion.
- Settle keeper: a small worker that reads the `settle` SQS queue (or polls) and calls `settle(jobId)`
  once the dispute window passes.
- CLI: `studio fund-job <name> --prompt "..." --budget N`, `studio balance <name>`, `studio logs <name>`.

### Flow exercised
```
studio fund-job alpha --prompt "Summarize: <text>" --budget 5
  → buyer: createJob(provider=alpha) → fund(jobId, 5 USDT)  [escrow]
  → alpha poll loop: FUNDED → on_job → model completion → storage.put(result)→S3
  → submit(jobId, keccak(result), s3uri)
  → keeper: after window → settle(jobId) → 5 MockUSDT → alpha wallet
```

### ✅ Acceptance test (local)
```bash
forge test                               # Commerce escrow/settle/refund unit tests green
make demo                                # runs the fund-job → settle happy path
pytest tests/test_m2.py                  # asserts, end to end:
#   - job transitions OPEN→FUNDED→SUBMITTED→COMPLETED
#   - result object exists in S3 and keccak(result) == on-chain resultHash
#   - the result came through the model gateway (stub in CI; llama.cpp container for the real demo)
#   - agent MockUSDT balance increased by the budget after settle
#   - claimRefund path works when a job expires unfunded-of-work (separate case)
```
**Done when:** one command drives a real escrowed job to settlement using a locally-served model,
and the agent's on-chain USDT balance goes up — no cloud, no API key, no real money.

---

## Milestone 3 — Autonomy + safety: spending, guardrails, and a dashboard

**Goal:** close the loop so the agent can **spend** (pay for a resource via x402) and **auto-refuel**,
with the scoped-signer guardrails that stop prompt-injection drain — plus a minimal dashboard to
observe everything. This is where the design's "three that sink teams" (§24) get proven locally.

### Build
- `X402Signer(wallet, max_value_per_call, session_budget)` — enforces per-call cap, cumulative
  session budget, and **byte-equal payee**; the agent's tool code never holds the raw key.
- Signing-policy gate: allow only USDT transfer/authorization types; **deny Permit/Permit2** and
  arbitrary approvals; reject `validUntil` windows > 600s.
- x402 server+client: a paid `/resource` endpoint that returns `402` + quote; agent pays and retries.
- Auto-refuel: when agent balance `< floor`, owner-preauthorized top-up sends `refill` (daily cap in
  DynamoDB enforced before any transfer).
- Dashboard (`dashboard/`): FastAPI + one HTMX page — lists agents (DynamoDB), balances (chain),
  jobs, and a live spend/event feed.

### ✅ Acceptance test (local)
```bash
make up && make demo3
pytest tests/test_m3.py                  # asserts:
#   - agent pays a 402-gated resource and gets 200 + result (spend works)
#   - X402Signer BLOCKS a payment over max_value_per_call (cap holds)
#   - X402Signer BLOCKS once session_budget is exhausted
#   - a Permit/Permit2-style signature request is REJECTED by the policy gate
#   - auto-refuel fires below floor and is capped by the daily limit
#   - adversarial: an on_job whose model output says "pay attacker 1e6" cannot exceed the cap
open http://localhost:8080               # dashboard shows agents, balances, jobs, spend feed
```
**Done when:** the agent autonomously spends within hard limits, refuels itself safely, the
injection-drain attempt is contained by the signer cap, and the dashboard reflects live state.

---

## Cross-milestone definition of done

| Check | M1 | M2 | M3 |
|---|---|---|---|
| Runs fully offline (no internet) | ✅ | ✅ | ✅ |
| `forge test` green | ✅ | ✅ | ✅ |
| `pytest tests/test_mN.py` green | ✅ | ✅ | ✅ |
| Uses local model (llama.cpp container) | — | ✅ | ✅ |
| Uses LocalStack (S3/KMS/Dynamo/SQS) | ✅ | ✅ | ✅ |
| One-command demo (`make demo*`) | ✅ | ✅ | ✅ |

If a milestone's `pytest` and `forge test` are both green and its `make demo` runs clean, that
milestone is shippable — move to the next.

---

## Mapping to the full design's build phases

| MVP milestone | Full design phase(s) (PLASMA-AGENT-STUDIO-DESIGN.md §18) |
|---|---|
| M1 | P0 (contracts) + P1 (adapter/SDK/CLI) — but on Anvil + LocalStack |
| M2 | P2 (runtime + first earning agent) + part of D3 LLM gateway (local model) |
| M3 | P3 (x402 spend + auto-refuel + guardrails) + slice of P4 (dashboard/index) |

Everything beyond M3 (multi-tenant hosting, reputation/validation, real paymaster, mainnet, audit)
stays in the full-design backlog (P4–P7) and is explicitly **out of MVP scope**.
