# Development guide — MVP Plasma AI Agent

Step-by-step build, from empty folder to a working local demo, milestone by milestone. Everything
runs on your laptop: **Anvil** (chain) + **LocalStack** (AWS) + a **llama.cpp model container** (CPU).

---

## 0. Prerequisites

| Tool | Why | Install |
|---|---|---|
| Docker + Docker Compose | run Anvil, LocalStack, the model container | https://docs.docker.com/get-docker/ |
| Foundry (`forge`, `anvil`, `cast`) | Solidity build/test + local chain | `curl -L https://foundry.paradigm.xyz \| bash && foundryup` |
| Python 3.11+ | SDK / runtime / CLI | https://www.python.org |
| `uv` or `pip` + `venv` | Python deps | `pip install uv` (recommended) |
| `awslocal` (optional) | LocalStack CLI convenience | `pip install awscli-local` |

The model runs as a **docker container** (no host install). Start it with `make model` — the first
run pulls `ghcr.io/ggml-org/llama.cpp:server` and downloads the GGUF (`Qwen2.5-1.5B-Instruct`, Q4,
~1 GB) into a cache volume. Override the model with `MODEL_GGUF` (e.g.
`Qwen/Qwen2.5-0.5B-Instruct-GGUF:Q4_K_M` for a lighter/faster one).

> **Why a container, not MLX?** MLX (Apple Metal) can't run inside a Linux container, so it can't
> live in LocalStack EC2 / docker. The containerized model is therefore CPU llama.cpp — the realistic
> local stand-in for an EC2 model node. **Tests never need the model** (they use the `stub` backend).
>
> **RAM note (8 GB Macs):** the container shares RAM with Anvil + LocalStack. The 1.5B Q4 model uses
> ~1–1.5 GB; if tight, switch `MODEL_GGUF` to the 0.5B model.

---

## 1. Bootstrap the workspace

```bash
cd mvp-plasma-ai-agent
python -m venv .venv && source .venv/bin/activate
# project layout (create as you go):
#   contracts/  sdk/  runtime/  cli/  model/  dashboard/  localstack/  tests/
forge init contracts --no-git
cd contracts && forge install OpenZeppelin/openzeppelin-contracts --no-git && cd ..
pip install web3 fastapi uvicorn typer pydantic boto3 httpx pytest
```

Create `.env` (loaded by SDK + compose):
```dotenv
# chain
RPC_URL=http://localhost:8545
CHAIN_ID=31337
RELAYER_PK=0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80   # anvil acct[0]
# localstack / aws
AWS_ENDPOINT_URL=http://localhost:4566
AWS_ACCESS_KEY_ID=test
AWS_SECRET_ACCESS_KEY=test
AWS_DEFAULT_REGION=us-east-1
S3_BUCKET=agent-cards
KMS_KEY_ALIAS=alias/agent-master
DDB_TABLE=agents
SQS_QUEUE=settle
# model
MODEL_BACKEND=stub                       # stub (tests) | llamacpp (real model container)
MODEL_BASE_URL=http://localhost:8081/v1
MODEL_GGUF=Qwen/Qwen2.5-1.5B-Instruct-GGUF:Q4_K_M
```

---

## 2. docker-compose (infra)

`docker-compose.yml` — `anvil` + `localstack` (started by `make up`), plus a `model` service
(llama.cpp, started separately by `make model`); dashboard added in M3:

```yaml
services:
  anvil:
    image: ghcr.io/foundry-rs/foundry:latest
    entrypoint: ["anvil", "--host", "0.0.0.0", "--block-time", "1"]
    ports: ["8545:8545"]

  localstack:
    # community v3 — newer `latest` images require a LOCALSTACK_AUTH_TOKEN even at startup
    image: localstack/localstack:3.8.1
    ports: ["4566:4566"]
    environment:
      - SERVICES=s3,kms,secretsmanager,dynamodb,sqs
      - DEBUG=0
    volumes:
      - "./localstack/init.sh:/etc/localstack/init/ready.d/init.sh"   # auto-seed on ready

  # CPU model node — stands in for an EC2 model instance (MLX/Metal can't be containerized).
  # Not started by `make up`; bring up with `make model` (pulls image + downloads GGUF once).
  model:
    image: ghcr.io/ggml-org/llama.cpp:server
    command: >
      -hf ${MODEL_GGUF:-Qwen/Qwen2.5-1.5B-Instruct-GGUF:Q4_K_M}
      -c 4096 --host 0.0.0.0 --port 8081 --jinja
    ports: ["8081:8081"]
    volumes: ["llama-cache:/root/.cache/llama.cpp"]

volumes:
  llama-cache:
```

`localstack/init.sh` (runs automatically when LocalStack is ready — seeds the AWS resources):
```bash
#!/bin/bash
awslocal s3 mb s3://agent-cards
awslocal kms create-key --description "agent-master" \
  && awslocal kms create-alias --alias-name alias/agent-master \
        --target-key-id "$(awslocal kms list-keys --query 'Keys[0].KeyId' --output text)"
awslocal dynamodb create-table --table-name agents \
  --attribute-definitions AttributeName=name,AttributeType=S \
  --key-schema AttributeName=name,KeyType=HASH --billing-mode PAY_PER_REQUEST
awslocal sqs create-queue --queue-name settle
echo "localstack seeded"
```

`make up`:
```makefile
up:    ; docker compose up -d && ./scripts/wait-healthy.sh && cd contracts && forge script script/Deploy.s.sol --rpc-url $$RPC_URL --broadcast
down:  ; docker compose down -v
test:  ; cd contracts && forge test && cd .. && pytest -q
demo:  ; python -m cli.studio demo
```

---

## 3. Milestone 1 — chain + identity + AWS emulation

**Order of work:**
1. **Contracts.** Write `MockUSDT.sol` (OZ ERC-20, 6 decimals, public `mint`), `IdentityRegistry.sol`
   (OZ ERC-721 + `register(string cardURI) returns(uint256)`, `cardURI(uint256)`), `Deploy.s.sol`.
   Tests in `contracts/test/`: mint, register, cardURI round-trip.
2. **SDK `aws.py`.** boto3 clients reading `AWS_ENDPOINT_URL` from `.env` → S3/KMS/SecretsManager/DDB.
3. **SDK `keyvault.py`.** `new_agent_key()`: generate eth key → `kms.encrypt(pk)` → store ciphertext
   in Secrets Manager `agents/<name>`; `signer_for(name)`: decrypt only in-memory at use.
4. **SDK `storage.py`.** `put(bytes)→keccak key→s3.put_object`; `get(key)→bytes`.
5. **SDK `adapter.py`.** `LocalAdapter`: `register(cardURI)`, `resolve(agentId)`, `send_tx` (relayer signs).
6. **CLI.** `studio up/down/create/resolve` wiring the above.

**Run + test:** see [`MILESTONES.md`](./MILESTONES.md) M1 acceptance block.
- No-Docker gate: `make test` = `forge test` + `pytest tests/test_m1_aws_moto.py` (AWS path via `moto`).
  Install dev deps with `pip install -r requirements-dev.txt`.
- Full gate: `make up` then `make test-e2e` (`pytest tests/test_m1.py`) against live Anvil + LocalStack.

---

## 4. Milestone 2 — earning agent + local model

**Order of work:**
1. **Contract `Commerce.sol`** (ERC-8183-lite): job struct + state machine
   `OPEN→FUNDED→SUBMITTED→COMPLETED/REJECTED/EXPIRED`; `createJob/fund/submit/settle/claimRefund`;
   immutable `paymentToken`. Foundry tests for escrow, settle-after-window, refund-after-expiry.
2. **`model/gateway.py`.** `complete(prompt, system)`, backend-pluggable: `stub` (tests) or
   `llamacpp` → POST `MODEL_BASE_URL` `/chat/completions` (OpenAI shape). Log usage to DDB.
3. **`runtime/app.py`.** FastAPI + lifespan: decrypt key, ensure identity, start poll loop. Endpoints
   `/status /job/{id} /health`.
4. **`runtime/loop.py`.** poll `poll_funded_jobs(me)` → verify → `on_job` → `storage.put` → `submit`.
5. **`runtime/keeper.py`.** read SQS / poll for submitted jobs past window → `settle(jobId)`.
6. **CLI.** `studio fund-job/balance/logs`; `studio demo` chains the happy path.

**Gate:** `forge test` + `pytest tests/test_m2.py` green; `make demo` settles a job and balance rises.

---

## 5. Milestone 3 — spend, guardrails, dashboard

**Order of work:**
1. **`sdk/x402.py`** + **`sdk/signer.py`** (`X402Signer`): build/verify payment auth; enforce
   per-call cap, session budget, byte-equal payee.
2. **Signing-policy gate:** allowlist USDT transfer/authorization types; deny Permit/Permit2; `validUntil ≤ 600s`.
3. **Paid resource demo:** a small `/resource` endpoint returning `402` + quote; agent pays and retries.
4. **Auto-refuel:** floor/refill/daily-cap (cap state in DDB) checked before any transfer.
5. **`dashboard/`:** FastAPI + one HTMX page; add `dashboard` service to compose on `:8080`.
6. **Adversarial test:** an `on_job` whose model output instructs a large payment must be capped.

**Gate:** `pytest tests/test_m3.py` green (incl. cap/policy/injection cases); dashboard shows live state.

---

## 6. Promotion path (local → real)

When the MVP is green, going to production is configuration, not rewrite:

| Local | Production swap |
|---|---|
| `RPC_URL=localhost:8545`, `CHAIN_ID=31337` | Plasma RPC + chain ID; deploy same contracts |
| `MockUSDT` | real USDT address on Plasma (set immutable in Commerce) |
| relayer-pays-gas branch in `send_tx` | Plasma native paymaster / USDT-gas / AppPaymaster branches |
| `AWS_ENDPOINT_URL=localhost:4566` (LocalStack) | unset → real AWS S3/KMS/SecretsManager/DynamoDB/SQS |
| llama.cpp model container | cloud LLM provider (Anthropic/OpenAI) behind the same gateway interface |
| docker-compose | k8s / Fly per the full design's deployment topology |

Because every boundary is behind an interface (`ChainAdapter`, model gateway, `aws` clients), each
swap is a config/credentials change. That is the whole point of building the MVP on Anvil + LocalStack
+ llama.cpp container: **identical architecture, zero external dependencies, fully testable.**

---

## 7. Common pitfalls (local)

- **LocalStack init didn't run** → resources missing. Check `docker logs localstack | grep seeded`;
  re-run `./localstack/init.sh` via `awslocal`.
- **USDT decimals** → MockUSDT must be 6 decimals to match real USDT; budget math uses `1e6` units.
- **Model cold start** → first `make model` downloads the GGUF (~1 GB) then loads on CPU; the first
  completion is slow. Tests avoid this entirely via the `stub` backend.
- **Anvil nonce/relayer** → all local txs from one relayer; serialize tx submission or manage nonces.
- **boto3 + LocalStack** → must pass `endpoint_url` AND dummy creds, else SDK hits real AWS.
- **Dispute window in tests** → use `cast rpc evm_increaseTime` + `evm_mine` to fast-forward Anvil.
