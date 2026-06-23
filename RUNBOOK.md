# RUNBOOK — MVP Plasma AI Agent

A hands-on operator's guide: how to run the product end-to-end, what has been built across the three
milestones, and how to **manually test every MVP feature** (plus the automated suite).

Everything here runs on `localhost` — no internet, no API keys, no testnet, no real money.

- Conceptual overview & cloud↔local mapping: [`README.md`](./README.md)
- Architecture (components, flows, gas tree, security model): [`ARCHITECTURE.md`](./ARCHITECTURE.md)
- Milestone acceptance criteria: [`MILESTONES.md`](./MILESTONES.md)

---

## 1. What we have built

A local-first, autonomous on-chain AI agent studio. A developer creates an agent; the agent gets an
**on-chain identity + wallet**, **earns** stablecoin (MockUSDT) by completing escrowed jobs using a
**locally-served LLM**, and **spends** stablecoin to pay for paid resources — all under hard,
prompt-injection-proof spend guardrails, with a live dashboard to observe it.

### Feature inventory by milestone

| Milestone | Feature | Where |
|---|---|---|
| **M1** | Local chain (Anvil) + AWS emulation (LocalStack: S3, KMS, Secrets Manager, DynamoDB, SQS) | `infra/docker-compose.yml`, `infra/localstack/init.sh` |
| **M1** | `MockUSDT` (ERC-20, 6dp), `IdentityRegistry` (ERC-721), `Commerce` (ERC-8183-lite escrow) | `contracts/src/` |
| **M1** | Agent identity: KMS-encrypted key in Secrets Manager (never plaintext at rest), Agent Card in S3, NFT on-chain | `sdk/plasma_mvp/{keyvault,storage,registry,adapter}.py` |
| **M2** | The earning loop: buyer escrows → agent poll loop → local model → submit → keeper settles → agent paid | `runtime/{agent,keeper,app}.py` |
| **M2** | Pluggable model gateway: `stub` (deterministic, for tests) or `llamacpp` (real CPU container, OpenAI-compatible) | `model/gateway.py` |
| **M3** | **x402 spend**: a 402-gated paid resource; the agent pays via a signed EIP-3009 authorization and retries | `runtime/resource.py`, `sdk/plasma_mvp/x402.py` |
| **M3** | **X402Signer** — scoped signer: per-call cap, cumulative session budget, byte-equal payee; tool code never holds the raw key | `sdk/plasma_mvp/signer.py` |
| **M3** | **Signing-policy gate** — allows only transfer-authorizations; **denies Permit/Permit2/approvals**; rejects validity windows > 600s | `sdk/plasma_mvp/x402.py` |
| **M3** | EIP-3009 `transferWithAuthorization` rail on MockUSDT (the on-chain settlement path for x402) | `contracts/src/MockUSDT.sol` |
| **M3** | **Auto-refuel** — tops up an agent below a floor from an owner account; **daily cap (DynamoDB) enforced before any transfer** | `sdk/plasma_mvp/refuel.py` |
| **M3** | **Dashboard** — FastAPI + HTMX single page (`:8080`): agents, balances, jobs, live spend/refuel feed | `dashboard/app.py`, `sdk/plasma_mvp/events.py` |

> The three guardrails (scoped signer, signing-policy gate, submit-time re-verification) are the
> "three that sink teams" from the full design — built in the MVP because they're cheap locally and
> expensive to retrofit. They are tested **adversarially** (see §5.3).

---

## 2. Prerequisites

- **Docker** (Docker Desktop on macOS) — runs Anvil, LocalStack, and the model container.
- **Foundry** (`forge`, `anvil`) — Solidity build/test/deploy.
- **Python 3.9+** — a virtualenv is checked in at `./.venv` (web3 7.x, boto3, typer, fastapi, httpx).
- ~3 GB free disk if you want the **real model** (the GGUF download). The stub backend needs nothing.

> **Disk note (8 GB Macs):** if Docker's VM fills up it goes read-only. Run
> `docker system prune -af --volumes` to reclaim space.

Activate the environment for every session:

```bash
cd mvp-plasma-ai-agent
. .venv/bin/activate
```

If you need to recreate the venv:

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
```

---

## 3. Configuration

On a fresh clone, create your local config from the template (the real `.env` is gitignored):

```bash
cp .env.example .env
```

All config lives in `.env` (auto-loaded by the SDK; process env wins over the file). Key knobs:

| Var | Default | Meaning |
|---|---|---|
| `RPC_URL` | `http://localhost:8545` | Anvil endpoint |
| `CHAIN_ID` | `31337` | Anvil chain id |
| `RELAYER_PK` | Anvil account[0] | relayer/owner key (pays gas, acts as buyer/owner in demos) — **local only** |
| `AWS_ENDPOINT_URL` | `http://localhost:4566` | LocalStack endpoint (clear it to target real AWS) |
| `MODEL_BACKEND` | `stub` | `stub` (no model, used by tests) or `llamacpp` (real container) |
| `MODEL_BASE_URL` | `http://localhost:8081/v1` | model server (port 8081; dashboard is 8080) |

> **LocalStack is pinned** to `localstack/localstack:3.8.1` in `infra/docker-compose.yml` (the `latest`
> tag requires a paid token — do not switch to `latest`).

---

## 4. Run the product

### 4.1 Boot the stack

```bash
make up
```

This: starts Anvil + LocalStack via `docker compose`, waits until both are healthy, seeds the AWS
resources (`infra/localstack/init.sh`: S3 bucket, KMS key, Secrets Manager path, DynamoDB tables `agents` /
`refuel-ledger` / `spend-events`, SQS queue), builds the contracts, deploys them to Anvil, and writes
`contracts/deployments/local.json` (the address manifest the SDK reads).

Check health any time:

```bash
make status
```

### 4.2 One-command demos

```bash
make demo     # M2: create agent → fund a job → agent earns → keeper settles → agent paid
make demo3    # M3: agent pays a 402-gated resource (within caps) → auto-refuels below floor
```

### 4.3 The dashboard

```bash
make dashboard          # serves http://localhost:8080
# then open http://localhost:8080
```

The page auto-refreshes every 3 s: agents (from DynamoDB) with live ETH/USDT balances (from chain),
recent jobs (from Commerce), and the spend/refuel event feed.

### 4.4 Real local model (optional)

```bash
make model                              # pulls the llama.cpp container + GGUF (~2–3 min, ~3 GB)
MODEL_BACKEND=llamacpp make demo        # same earning loop, now powered by the real local LLM
```

### 4.5 Teardown

```bash
make down     # docker compose down -v (removes containers + volumes)
```

---

## 5. Manual testing — every MVP feature

Run these after `make up`, with the venv active. Each block states **what it proves** and the
**expected result**.

### 5.1 M1 — identity, key custody, on-chain registration

```bash
# create an agent: generates a key (KMS-encrypted into Secrets Manager), stores an Agent Card in S3,
# mints an IdentityRegistry NFT on-chain, mirrors the row into DynamoDB.
python3 backend/cli/studio.py create alpha
```
**Expect:** prints the agent address, `agentId`, the `s3://agent-cards/<keccak>` card URI, and a tx
hash.

```bash
# resolve the on-chain identity back to its Agent Card (round-trip through chain → S3).
python3 backend/cli/studio.py resolve alpha
```
**Expect:** `agentId`, owner == the agent address, the card URI, and the decoded card JSON.

```bash
# show on-chain balances.
python3 backend/cli/studio.py balance alpha
```
**Expect:** the agent has ~1 ETH (gas float) and 0 USDT.

**Prove the key is never plaintext at rest** (the M1 security assertion):

```bash
python3 - <<'PY'
import sys; sys.path.insert(0, "sdk")
from plasma_mvp.keyvault import KeyVault
kv = KeyVault()
acct = kv.signer_for("alpha")              # decrypts in-memory only (via KMS)
ct = kv.ciphertext_for("alpha")            # what is actually stored in Secrets Manager
assert acct.key not in ct                  # the raw private key is NOT in the stored ciphertext
print("OK: stored secret is ciphertext only; plaintext key never at rest")
PY
```

### 5.2 M2 — the earning loop (fund → local model → settle → paid)

```bash
# act as a buyer: store the request prompt in S3, create + fund an escrowed job for the agent.
python3 backend/cli/studio.py fund-job alpha --prompt "Summarize: agents that earn stablecoins." --budget 5
```
**Expect:** `funded job <id> for agent 'alpha' (budget 5 USDT)`.

Then drive the loop (the `demo` command runs the agent's poll iteration + waits the dispute window +
settles), or use the full one-shot:

```bash
python3 backend/cli/studio.py demo --name alpha
```
**Expect:** `agent submitted jobs: [<id>]`, then `keeper settled jobs: [<id>]`, and the agent's USDT
balance increases by the budget (e.g. `+5.000000`).

**Real model variant:** `MODEL_BACKEND=llamacpp python3 backend/cli/studio.py demo` (after `make model`) — the
result text now comes from the local llama.cpp container instead of the deterministic stub.

### 5.3 M3 — spend, guardrails, auto-refuel

**Full flow in one command:**

```bash
python3 backend/cli/studio.py demo3
```
**Expect output like:**
```
--- x402 SPEND ---
resource status: 200
resource payee balance: 2.000000 USDT       # funds actually moved on-chain
signer spent: 2.000000 USDT (remaining 4.000000)
--- AUTO-REFUEL ---
refuel #1: refueled +5 USDT
refuel #2: daily cap reached                 # the daily cap blocks the second top-up
agent balance 8.000000 -> 13.000000 USDT
```

**Guardrails, hands-on** — prove the drain defenses refuse to sign (no chain needed):

```bash
python3 - <<'PY'
import sys; sys.path.insert(0, "sdk")
from eth_account import Account
from plasma_mvp import x402
from plasma_mvp.signer import X402Signer, SpendCapExceeded, PayeeNotAllowed

USDT = 1_000_000
agent = Account.create()
payee = Account.create().address
attacker = Account.create().address

def quote(to, value):
    return x402.PaymentQuote(pay_to=to, value=value, asset="0x"+"11"*20, chain_id=31337,
                             valid_after=1000, valid_before=1300, nonce="0x"+"22"*32)

signer = X402Signer(lambda: agent, max_value_per_call=2*USDT, session_budget=4*USDT,
                    allowed_payees=[payee])

# 1) per-call cap
try: signer.sign_payment(quote(payee, 3*USDT))
except SpendCapExceeded as e: print("BLOCKED over-cap:", e)

# 2) byte-equal payee (attacker not allow-listed)
try: signer.sign_payment(quote(attacker, USDT))
except PayeeNotAllowed as e: print("BLOCKED bad-payee:", e)

# 3) policy gate denies Permit / Permit2
gate = x402.SigningPolicy()
for t in ("Permit", "PermitTransferFrom"):
    try: gate.check({"primaryType": t, "message": {"validAfter": 0, "validBefore": 1300}})
    except x402.PolicyViolation as e: print(f"REJECTED {t}:", e)

# 4) policy gate rejects validity window > 600s
try:
    td = x402.build_transfer_authorization_typed_data(
        x402.PaymentQuote(pay_to=payee, value=USDT, asset="0x"+"11"*20, chain_id=31337,
                          valid_after=1000, valid_before=1000+700, nonce="0x"+"22"*32), agent.address)
    gate.check(td)
except x402.PolicyViolation as e: print("REJECTED long-window:", e)

print("spent so far:", signer.spent, "(0 == nothing got through)")
PY
```
**Expect:** four BLOCKED/REJECTED lines and `spent so far: 0` — the signer never fetched the key and
never debited the budget for any blocked request.

**Auto-refuel daily cap, hands-on:** covered by `demo3` above (refuel #1 fires, #2 hits the cap).

### 5.4 The dashboard reflects live state

```bash
make dashboard            # leave running
curl -s http://localhost:8080/panel | sed 's/<[^>]*>/ /g' | tr -s ' '
```
**Expect:** an agents table with addresses + live balances, a jobs table, and a spend/refuel feed
showing the `spend` and `refuel` events produced by `demo3`.

---

## 6. Automated test suite

The suite is split so most of it runs **without Docker** (forge + `moto` in-process AWS), and the
live end-to-end parts skip cleanly if the stack isn't up.

```bash
# No-Docker gate — always run this first (Solidity unit tests + AWS code path via moto):
make test

# Live end-to-end (require `make up` first):
pytest tests/test_m1.py      # M1: on-chain identity ↔ S3 round-trip, no plaintext key, DynamoDB row
pytest tests/test_m2.py      # M2: earning loop OPEN→FUNDED→SUBMITTED→COMPLETED, agent paid, refund path
pytest tests/test_m3.py      # M3: x402 spend, signer caps, policy gate, auto-refuel, adversarial
```

### Expected tally (all green)

| Suite | Tests |
|---|---|
| `forge test` | **21** (incl. 4 EIP-3009) |
| `tests/test_m1.py` | 4 |
| `tests/test_m1_aws_moto.py` | 3 |
| `tests/test_m2.py` | 2 |
| `tests/test_m3.py` | 11 (8 guardrail units, Docker-free + 3 live e2e) |
| **Total** | **41 passed, 0 failed** |

`tests/test_m3.py` asserts the full M3 bar: spend works; over-cap and over-session-budget are
**BLOCKED**; Permit/Permit2 is **REJECTED** by the policy gate; auto-refuel fires below floor and
respects the daily cap; and an `on_job` whose model output says "pay attacker 1e6" **cannot exceed the
cap** (the adversarial prompt-injection case).

---

## 7. Troubleshooting

| Symptom | Fix |
|---|---|
| `pytest` skips M2/M3 e2e | the live stack isn't up — run `make up` |
| `MockUSDT lacks EIP-3009` skip | redeploy after the M3 contract change: `make up` re-runs the deploy |
| LocalStack auth/token errors | you're on the `latest` image — pin `localstack/localstack:3.8.1` |
| Docker VM read-only / writes fail | host disk full — `docker system prune -af --volumes` |
| port 8080 busy | the dashboard port; the model uses 8081 — free 8080 or pass `--port` |
| tx underpriced / gas errors | the adapter uses EIP-1559 fee fields (not `gasPrice`); don't add `gasPrice` to txs |

---

## 8. Project map

```
mvp-plasma-ai-agent/
├── contracts/        Foundry: MockUSDT (+EIP-3009), IdentityRegistry, Commerce, Deploy.s.sol, tests
├── sdk/plasma_mvp/   config, aws, storage, keyvault, registry, adapter, x402, signer, refuel, events
├── runtime/          agent (poll loop), keeper (settle), app (FastAPI), resource (x402 server+client)
├── model/            gateway.py — stub | llamacpp backends (OpenAI-compatible)
├── cli/              studio.py — up/down/status/create/resolve/balance/fund-job/run/demo/demo3/dashboard
├── dashboard/        app.py — FastAPI + HTMX observability page (:8080)
├── localstack/       init.sh — seeds S3/KMS/Secrets/DynamoDB/SQS
├── tests/            test_m1, test_m1_aws_moto, test_m2, test_m2_model_llamacpp, test_m3
└── Makefile          up/down/status/build/deploy/model/demo/demo3/dashboard/test/test-m3
```
