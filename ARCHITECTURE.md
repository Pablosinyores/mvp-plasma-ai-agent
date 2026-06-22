# Architecture — MVP Plasma AI Agent (local-first)

This document is the **local-first adaptation** of [`../PLASMA-AGENT-STUDIO-DESIGN.md`](../PLASMA-AGENT-STUDIO-DESIGN.md).
It preserves the four conceptual modules — **Identity · Commerce/Escrow · Payment · Memory** — and
the same end-to-end flows, but every external dependency is replaced by a local equivalent so the
system runs and is testable on a single machine.

The **cloud services** the design depends on (S3, KMS, Secrets Manager, DynamoDB, SQS) are kept
*exactly as-is* and run locally via **[LocalStack](https://www.localstack.cloud/localstack-for-aws)**
— the AWS-emulation tool from the reference video. Your code uses the normal AWS SDK (`boto3`);
only the endpoint URL (`http://localhost:4566`) and dummy credentials change. Going to real AWS later
is a config swap, not a rewrite.

---

## 1. Design goal

> Prove the full loop **— describe → identity → earn → spend — on localhost, with the LLM running
> locally**, and nothing that needs the internet, an API key, or real money.

Once this MVP works locally, the migration to real Plasma is a *configuration swap* (RPC URL,
chain ID, USDT address, paymaster) — the code paths are identical because chain access is behind a
single `ChainAdapter` interface.

---

## 2. System context (local)

```
        Developer  ── studio CLI ──►  Control plane (local processes)
                                          │
        ┌─────────────────────── MVP PLASMA AI AGENT (localhost) ───────────────────────┐
        │  Control plane            Data plane                 Local chain (Anvil)        │
        │  • studio CLI             • Agent Runtime (FastAPI)   • MockUSDT (ERC-20)        │
        │  • Deploy script          • x402 gateway (earn/spend) • IdentityRegistry (8004)  │
        │  • (M3) Dashboard         • Local LLM gateway         • Commerce/Escrow (8183-lite)│
        │                           • Relayer (pays gas)        • Settle (permissionless)   │
        └────┬─────────────────┬───────────────┬──────────────────────┬───────────────────┘
             │                 │               │                      │
        Buyer (test       Model node      LocalStack :4566       Anvil JSON-RPC :8545
        script/curl)      llama.cpp        • S3   (storage)       (instant blocks,
             │            container :8081  • KMS  (agent keys)     free gas)
             │            (CPU, OpenAI API,• SecretsManager
             │             =EC2 model node)• DynamoDB / SQS
             │                             • SecretsManager
             │                             • DynamoDB (discovery/billing)
             │                             • SQS (settle keeper / indexer)
```

**What changed vs. the full design and why it's safe for an MVP:**

- **Anvil instead of Plasma** — same EVM, same Solidity, same web3 calls. Instant mining, free gas,
  10 pre-funded accounts. The contracts are deployed *unchanged* later to Plasma.
- **MockUSDT instead of Plasma USDT** — an OpenZeppelin ERC-20 with a `mint()` for test funding.
  6 decimals to match USDT semantics (so the decimals bug from §19.7 of the design can't bite later).
- **Local relayer instead of paymaster** — on Anvil gas is free and paid by a funded EOA, so we
  don't need MegaFuel / Plasma's native paymaster yet. The `send_tx` gas-decision tree still exists
  in the adapter; the local branch just says "relayer pays." Swapping in a real paymaster is one branch.
- **Local model instead of cloud LLM** — a **llama.cpp server container** (CPU) exposes an
  OpenAI-compatible endpoint at `http://localhost:8081/v1`. It runs in docker next to LocalStack and
  is the local stand-in for an **EC2 model node**. (MLX/Metal was considered for speed but cannot run
  in a Linux container, so the containerized model is CPU llama.cpp.) The gateway is provider-agnostic
  — a cloud LLM or a different OpenAI-compatible server is a config flip. Tests use a `stub` backend
  (no model). Default GGUF: `Qwen2.5-1.5B-Instruct` (Q4, ~1GB); override via `MODEL_GGUF`.
- **LocalStack instead of real AWS** — S3 (deliverable/memory storage), KMS + Secrets Manager
  (agent key custody — keys never sit plaintext, satisfying §17 of the design), DynamoDB (discovery
  + billing mirror), SQS (settle-keeper / indexer queues). All via `boto3` pointed at
  `http://localhost:4566` with dummy `test/test` credentials. **Same code path as production AWS.**

---

## 3. Layered architecture (four planes, all local)

```
┌──────────────────────────────────────────────────────────────────────────┐
│ 1. EXPERIENCE     studio CLI (Typer) · (M3) web dashboard                  │
├──────────────────────────────────────────────────────────────────────────┤
│ 2. CONTROL        Deploy script (Foundry) · agent scaffolder · config/.env │
├──────────────────────────────────────────────────────────────────────────┤
│ 3. DATA (per agent) Agent Runtime (FastAPI) · job poll loop · on_job ·     │
│                     x402 client+server · local LLM gateway · scoped signer │
├──────────────────────────────────────────────────────────────────────────┤
│ 4. CHAIN          ChainAdapter (web3.py) → Anvil contracts:                │
│                   MockUSDT · IdentityRegistry · Commerce · (settle keeper) │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## 4. Components (MVP inventory)

### 4.1 On-chain (Solidity, Foundry → Anvil)

| Contract | Role | MVP scope |
|---|---|---|
| `MockUSDT` | ERC-20 settlement asset | `mint(to, amt)` for test funding; 6 decimals |
| `IdentityRegistry` | ERC-8004-lite over ERC-721 | `register(cardURI) → agentId`; `cardURI(agentId)` |
| `Commerce` | ERC-8183-lite escrow + job state machine | `createJob / fund / submit / settle / claimRefund`; immutable `paymentToken` = MockUSDT |

> **Deliberately deferred** (not in MVP): ReputationRegistry, ValidationRegistry, EvaluatorRouter,
> OptimisticPolicy voting, real AppPaymaster. The MVP uses a **fixed dispute window + permissionless
> settle** for the optimistic "silence = approve" path — enough to demo trustless settlement.

### 4.2 SDK (`sdk/plasma_mvp/`, Python)

| Module | Responsibility |
|---|---|
| `ChainAdapter` | one interface for identity + commerce + payment + `send_tx` gas strategy |
| `LocalAdapter` | concrete impl against Anvil (web3.py); relayer pays gas |
| `X402Signer` | scoped signer — per-call cap, session budget, byte-equal payee; agent code never sees raw key |
| `x402` | build/verify EIP-712 / EIP-3009-style payment authorizations |
| `storage` | content-addressed store backed by **LocalStack S3**: `put(bytes) → keccak hash` key, `get(hash)` |
| `aws` | boto3 clients (S3/KMS/SecretsManager/DynamoDB/SQS) wired to LocalStack endpoint via one config |
| `keyvault` | agent key custody via **KMS-encrypted** keystore in Secrets Manager; runtime decrypts at startup only |

### 4.3 Runtime (`runtime/`, FastAPI)

- Endpoints: `/negotiate`, `/status`, `/job/{id}`, `/health`.
- Background **poll loop** (default 5s on Anvil): `poll_funded_jobs(me)` → verify → `on_job(job)` →
  `storage.put(result)` → `submit(jobId, keccak(result), uri)`.
- `on_job` is the user's logic; in the MVP it calls the **local model gateway**.

### 4.4 Model gateway (`model/`)

- Provider-agnostic interface `complete(prompt, system) → text`, backend chosen by `MODEL_BACKEND`:
  - `stub` (default) — deterministic, no model; used by all tests (Docker- and model-free).
  - `llamacpp` — OpenAI-compatible HTTP to the llama.cpp server container at `:8081/v1`.
- Same OpenAI shape means a cloud LLM (or any OpenAI-compatible server) is a pure config flip.

### 4.5 CLI (`cli/`, Typer)

`studio up | down | create <name> | fund-job <name> | balance <name> | logs <name> | status`.

### 4.6 Dashboard (`dashboard/`, M3 only)

Minimal read-only web UI: list agents, balances, jobs, and a live event/spend feed (polls the
runtime + chain). Keeps the "observe autonomous behavior" requirement (§23.26 of the design) from M3 on.

---

## 5. End-to-end flows (local)

### 5.1 Create & register (M1)
```
studio create <name>
  → scaffold agent config + keypair (Anvil account)
  → storage.put(AgentCard JSON) → cardURI (local hash)
  → adapter.register(cardURI) → IdentityRegistry mints agentId NFT
  → write .agent/<name>.json (agentId, address, endpoint)
```

### 5.2 Earn a job (M2 — the core loop)
```
buyer (test script): createJob(provider=agent, descHash) → fund(jobId, budget in MockUSDT)
agent poll loop: sees FUNDED → verify(provider==me, !expired, budget≥price)
  → result = on_job(job)  →  calls model gateway (llama.cpp container, or stub)
  → uri = storage.put(result);  submit(jobId, keccak(result), uri)
settle keeper: after dispute window, settle(jobId)  →  MockUSDT released to agent wallet
```

### 5.3 Spend / sub-task (M3)
```
agent needs a paid resource → resource returns 402 {asset:MockUSDT, amount, payTo, nonce, validUntil}
  → X402Signer.sign_payment(...)  (enforces per-call cap + session budget + exact payee)
  → retry with X-PAYMENT header → 200 + result
```

### 5.4 Auto-refuel (M3)
```
agent balance < floor → owner pre-authorized top-up sends refill (daily cap enforced) → continue
```

---

## 6. The `send_tx` gas-decision tree (kept, local branch active)

The design's Plasma-specific gas logic is preserved so the port is trivial. On Anvil only the first
branch is exercised:

```
send_tx(tx):
  if LOCAL (Anvil):           relayer EOA signs & pays gas (free)        ← MVP path
  elif plain USDT transfer:   route via Plasma native paymaster (gasless)  ← later
  elif fee payable in USDT:   attach USDT gas (custom gas token)            ← later
  else:                       route via AppPaymaster                        ← later
```

---

## 7. Security model carried into the MVP (don't skip these)

The three "teams that sink" items from the design (§24) are *in scope* for the MVP because they're
cheap to build locally and expensive to retrofit:

1. **Scoped spend control (`X402Signer`)** — per-call + session caps; tested adversarially in M3.
   This is the prompt-injection-to-wallet-drain defense.
2. **Signing-policy gate** — allow only USDT transfer/authorization types; **deny `Permit`/Permit2**
   and arbitrary approvals; validity window ≤ 600s.
3. **Submit-time re-verification** — re-check FUNDED + provider + not-expired + budget≥price before signing.

Plus: **non-pausable `claimRefund`** so escrowed funds are always recoverable after expiry.

---

## 8. Tech stack (MVP)

| Concern | Choice |
|---|---|
| Contracts | Solidity + **Foundry** (Anvil for local chain); OpenZeppelin ERC-20/721 |
| Local chain | **Anvil** (`anvil --block-time 1`) |
| SDK / runtime | **Python 3.11** (web3.py, FastAPI, pydantic) |
| Local model | **llama.cpp server container** (CPU, OpenAI API) — `Qwen2.5-1.5B-Instruct` GGUF default; `stub` backend for tests |
| CLI | Python + **Typer** |
| Cloud services (local) | **LocalStack** — S3, KMS, Secrets Manager, DynamoDB, SQS via `boto3` |
| Storage | **LocalStack S3**, content-addressed (keccak key) |
| Key custody | **LocalStack KMS + Secrets Manager** (no plaintext keys at rest) |
| Ledger / metering | **LocalStack DynamoDB** (sqlite acceptable for the quickest path) |
| Async (settle keeper / indexer) | **LocalStack SQS** (optional Lambda) |
| Dashboard (M3) | FastAPI + a single HTML/HTMX page (no heavy frontend) |
| Orchestration | **docker-compose** (anvil + localstack + model[llama.cpp] + dashboard) |
| Tests | **forge test** (contracts) + **pytest** (e2e Python) |

---

## 9. What this MVP deliberately does NOT do

- No cloud hosting, no k8s, no multi-tenant scale-to-zero (single agent at a time is fine).
- No real paymaster / gasless economics (Anvil gas is free).
- No reputation/validation/voting governance (fixed dispute window instead).
- No real USDT, no mainnet, no KYC/regulatory surface.
- No IPFS/Arweave (LocalStack S3 stands in for object storage; IPFS is a later option).
- No real AWS account, region, or bill — LocalStack emulates everything offline.

Each of these maps cleanly to a later phase in the full design (P4–P7). The MVP's job is to make the
**core loop real and testable**, not to be production.

---

## 10. Why LocalStack (mapping to the reference video)

The reference video (`_PD4j5Ra3kY`) demonstrates **LocalStack** — running AWS services on your own
machine for development and testing. We adopt it as the MVP's cloud-emulation layer for three reasons:

1. **Same architecture, zero cloud.** The full design custodies keys in KMS, stores deliverables in
   S3, mirrors discovery/billing in a DB, and runs async keepers off a queue. LocalStack provides
   all of these locally, so the MVP's component diagram is the *production* diagram — nothing is faked
   away, just pointed at `localhost:4566`.
2. **Trivial promotion to real AWS.** `boto3` clients read endpoint + credentials from config. Flip
   `AWS_ENDPOINT_URL` from LocalStack to empty and supply real creds → identical code talks to real AWS.
3. **Testability.** LocalStack starts in a container, seeds buckets/keys/tables/queues from an init
   script, and tears down clean — perfect for the per-milestone `pytest` e2e runs.
