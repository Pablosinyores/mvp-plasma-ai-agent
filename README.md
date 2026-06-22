# MVP — Plasma AI Agent (local-first)

A **runnable, fully-local MVP** of the Plasma Agent Studio concept: a developer describes an
AI agent, the agent gets an **on-chain identity + wallet**, **earns** stablecoin by doing escrowed
jobs, and **spends** stablecoin to pay for the work it does — with the **LLM model running locally**
(no cloud LLM keys, no testnet, no cloud hosting required).

This MVP keeps the *same conceptual design* as [`../PLASMA-AGENT-STUDIO-DESIGN.md`](../PLASMA-AGENT-STUDIO-DESIGN.md)
(Identity · Commerce/Escrow · Payment · Memory) but swaps every external dependency for a local
equivalent so the whole system runs on one laptop and is testable end-to-end.

The local emulation backbone is **[LocalStack](https://www.localstack.cloud/localstack-for-aws)**
(the tool from the reference video, `_PD4j5Ra3kY`): it runs the **same AWS services the cloud design
depends on — S3, KMS, Secrets Manager, DynamoDB, SQS — entirely on localhost**. That keeps the
architecture *identical* to production; only the endpoint URL changes when you go to real AWS.

| Full design (cloud + Plasma) | This MVP (local) |
|---|---|
| Plasma testnet/mainnet (XPL) | **Anvil** — Foundry local EVM node |
| USDT on Plasma | **MockUSDT** — an ERC-20 we deploy to Anvil |
| Cloud LLM on EC2 (Anthropic/OpenAI) | **llama.cpp container** (CPU, OpenAI-compatible) — stands in for an EC2 model node; `stub` backend for tests |
| Plasma native paymaster (gasless) | **Local relayer** account pays gas on Anvil (free) |
| AWS S3 / IPFS storage | **LocalStack S3** (same SDK calls, local endpoint) |
| Cloud KMS / Vault (key custody, secrets) | **LocalStack KMS + Secrets Manager** |
| Discovery/Billing DB | **LocalStack DynamoDB** (+ sqlite for quick metering) |
| Settle keeper / indexer (async jobs) | **LocalStack SQS** (+ optional Lambda) |
| k8s / Fly / cloud runtime | **`docker compose up`** on localhost |

> **Why this matches "keep all the same".** The cloud design's dependencies (S3, KMS, queues) stay —
> they just point at LocalStack's `http://localhost:4566` endpoint. Migration to real AWS later is a
> one-line endpoint/credentials swap, no code change. This is exactly LocalStack's purpose.

---

## What "done" looks like (the MVP demo)

```
$ studio up                         # boots anvil + deploys contracts + starts local model
$ studio create price-watcher       # scaffolds + registers an agent (on-chain identity NFT)
$ studio fund-job price-watcher \    # a buyer escrows MockUSDT for a job
      --prompt "Summarize this text" --budget 5
# → agent poll loop sees FUNDED → calls LOCAL model → uploads result → submit() → settle()
# → 5 MockUSDT released to the agent wallet
$ studio balance price-watcher      # shows the agent earned USDT, all on local chain
```

Everything above runs on `localhost`. No internet, no API keys, no real money.

---

## Repo layout (target)

```
mvp-plasma-ai-agent/
├── README.md              ← you are here
├── ARCHITECTURE.md        ← local-first architecture (components, flows, stack)
├── MILESTONES.md          ← 3 milestones, each locally testable + acceptance tests
├── DEVELOPMENT.md         ← step-by-step build guide (prereqs → run → test)
├── docker-compose.yml     ← anvil + localstack + model (llama.cpp) + (M3) dashboard
├── localstack/            ← init scripts: create S3 bucket, KMS key, DynamoDB tables, SQS queues
├── contracts/             ← Foundry project (Solidity)
│   ├── src/{MockUSDT,IdentityRegistry,Commerce}.sol
│   ├── script/Deploy.s.sol
│   └── test/*.t.sol
├── sdk/                   ← Python: ChainAdapter, x402, signer, aws (boto3→LocalStack)
│   └── plasma_mvp/
├── runtime/              ← FastAPI agent server + poll loop + on_job
├── cli/                  ← `studio` CLI (Python/Typer)
├── model/               ← model gateway (OpenAI-compatible: stub | llamacpp backends)
├── dashboard/           ← (M3) minimal web UI
└── tests/               ← end-to-end pytest scenarios per milestone
```

---

## The three milestones (summary)

| # | Milestone | Proves | Local test |
|---|---|---|---|
| **M1** | **Chain + Identity** | contracts deploy, an agent gets an on-chain identity | `forge test` + `studio create` → NFT minted, card resolves |
| **M2** | **Earning agent + local LLM** | the core loop: fund → local model executes → settle → paid | e2e script: job FUNDED→COMPLETED, agent balance ↑ |
| **M3** | **Spend + guardrails + dashboard** | autonomy + safety: agent pays, caps enforced, observable | spend-cap test blocks over-limit; dashboard shows balances/jobs |

See [`MILESTONES.md`](./MILESTONES.md) for acceptance criteria and exact commands.

---

## Quick start (once built)

```bash
# prereqs: Docker, Foundry, Python 3.11+ (model runs as a docker container — no extra install)
make up            # anvil + deploy + pull model + start runtime
make demo          # runs the end-to-end earning demo
make test          # forge tests + pytest e2e
make down
```

Start with [`DEVELOPMENT.md`](./DEVELOPMENT.md) to build it milestone by milestone.
