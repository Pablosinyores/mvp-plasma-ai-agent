# HANDOFF — MVP Plasma AI Agent

Carry this into a fresh terminal/session to continue work without losing context.

## Where things are
- **Project root:** `/Users/shivamdhakad/Desktop/projects/new_ideas/Bnb-Agent/mvp-plasma-ai-agent`
- **Git remote:** `git@github.com-nikhil:Pablosinyores/mvp-plasma-ai-agent.git`
- **Git author (local):** user.name `Pablosinyores`, user.email `nikhilbajaj0182@gmail.com`
- **HARD RULE:** never add `Co-Authored-By: Claude` / `🤖 Generated with Claude Code` / any AI attribution to commits or PRs.
- **Comms:** caveman mode default; normal prose only for docs/commits/PRs/user-facing copy.

## What was built (Milestone 3 — all DONE, 41 tests green)
1. **X402Signer** (`backend/sdk/plasma_mvp/signer.py`) — scoped signer: per-call cap, session budget, byte-equal payee allow-list. Uses `signer_factory` so tool code never holds the raw key. Enforces caps + payee BEFORE fetching key. Exceptions: `SpendCapExceeded`, `PayeeNotAllowed`.
2. **Signing-policy gate** (`backend/sdk/plasma_mvp/x402.py` — `SigningPolicy.check`) — allow only TransferWithAuthorization/ReceiveWithAuthorization; deny Permit/PermitSingle/PermitBatch/PermitTransferFrom/Permit2; reject validity window > 600s. `PolicyViolation`.
3. **x402 SPEND flow** (`runtime/resource.py`) — `X402ResourceServer` (quote / 402 body / settle), `make_resource_app` (GET /resource → 402 or settle), `X402Client` (pay + retry).
4. **EIP-3009** (`contracts/src/MockUSDT.sol`) — `transferWithAuthorization` + replay guard + ecrecover; tested in `contracts/test/Eip3009.t.sol` (4 tests).
5. **Auto-refuel** (`backend/sdk/plasma_mvp/refuel.py`) — `AutoRefueler.maybe_refuel(agent, day=)`; checks floor, then DAILY CAP in DynamoDB BEFORE transfer. `RefuelLedger` key = `agent.lower()#day`. `day=` override for determinism.
6. **Events** (`backend/sdk/plasma_mvp/events.py`) — `EventLog` over DynamoDB `spend-events`.
7. **Dashboard / API** (`backend/dashboard/app.py`) — FastAPI on `:8080`: REST `/api/*` actions (create/resolve/fund-job/spend/refuel/injection-test), WebSocket `/ws` live-push broadcaster, plus `/panel` HTML fallback + `backend/dashboard/static/index.html` zero-build page. CORS enabled for the FE.
8. **Frontend** (`studio-frontend/`) — standalone React + Vite app on `:5173`, talks to the backend over REST + WS (env `VITE_API_BASE`). See `studio-frontend/README.md`.
9. **Tests** (`backend/tests/test_m3.py`) — 11 tests; `_exploding_factory()` proves key never fetched on guardrail failure.

## Agentic trader — limit orders · persistence · strategy panel (DONE)
Built on the TradeGuard cage (model picks WHAT, guard decides ALLOWED, recipient pinned to self,
caps/slippage unchanged). All three features ship behind that invariant.

- **(a) Conditional / limit orders.** New `limit` op end to end:
  - `intent.py` — schema `{"op":"limit","sell","buy","amount","when":{"sym","cmp":"lt|gt","price"}}`,
    LLM system-prompt guidance, and a deterministic fallback for "… when price < X / > X"
    (implicit-USDC counterparty for bare "buy/sell ASSET"; `_norm_cmp` maps words/symbols → lt/gt).
  - `adapter.py` — `spot_price(sym)` = USDC per 1.0 token, from `quote_trade(sym,USDC,1 whole)/1e6`.
  - `trader.py` — `tick()` evaluates the predicate vs live spot; fires `guard.trade` once then marks
    `_swap_done`, else returns `hold` with the current price.
- **(b) Persistence.** `strategy_store.py`: `save/load/delete` keyed by agent address.
  `FileStrategyStore` (JSON, the working default — **DynamoDB/LocalStack was down**) +
  `DynamoStrategyStore` drop-in + `open_strategy_store()` factory (prefers Dynamo, falls back to file).
  `Trader(adapter, guard, store=…)` rehydrates strategy + `tick_count` + `_swap_done` on construction,
  persists on `set_strategy`/each `tick`; `clear_strategy()` deletes. Store-less Trader = old behavior.
- **(c) Strategy panel.** `backend/studio_api/strategy_ctl.py` `TraderManager` (one persisted Trader per
  agent, seeds gas+USDC, keeps last-N ticks). Endpoints in `studio_api/app.py`:
  `POST/GET/DELETE /api/agents/{name}/strategy` (path keys by **name**, consistent with the rest of the
  API; store still keys by address). Ticks driven from the broadcast loop. Frontend:
  `studio-frontend/src/components/StrategySection.tsx` (new "Strategy" nav section) — pick agent, set a
  standing prompt, Stop, and a live tick feed; api in `src/api/client.ts`, types in `src/types.ts`,
  styles in `src/styles.css`.
- **Tests (all green):** `test_intent.py` (limit/when parse), `test_trader.py` (predicate true→trade /
  false→hold / fires once / gt branch), `test_strategy_store.py` (restart rehydration, one-off & limit
  not re-fired), `test_strategy_ctl.py` (set/get/clear/drive ticks). `forge test` 35 green; the trader
  python suites 42 green. FE `npm run build` clean.
- **Still needs Docker for the real Dynamo path** (`open_strategy_store` falls back to file when down);
  the panel's live `TraderManager` signer uses the KMS-backed KeyVault, so a server-up smoke needs
  LocalStack to create agents. Plasma-testnet deploy still blocked on a funded `RELAYER_PK`.

## Stack
- **Anvil** chain `:8545`, chainId 31337.
- **LocalStack** `:4566` (S3/KMS/SecretsManager/DynamoDB/SQS), pinned `localstack/localstack:3.8.1`. Tables: `refuel-ledger`, `spend-events` (key `pk`) — created in `infra/localstack/init.sh`.
- **llama.cpp** container `:8081`, OpenAI-compatible, Qwen2.5-1.5B GGUF, ~8s/job CPU. `MODEL_BACKEND=stub|llamacpp`.
- Python 3.9, web3.py 7.x, eth_account 0.13.7, boto3, FastAPI (WebSocket), Typer. FE: React 18 + Vite + TS.

## How to run (clean slate)
```bash
cd /Users/shivamdhakad/Desktop/projects/new_ideas/Bnb-Agent/mvp-plasma-ai-agent
docker compose -f infra/docker-compose.yml down && rm -rf backend/.agent contracts/deployments/local.json
make up        # anvil + localstack fresh, contracts deployed, model container
make model     # wait for Qwen JSON at localhost:8081/v1/models
cp .env.example .env   # only if .env missing (.env is gitignored — holds Anvil priv key)
```
Verify ready: `curl -s localhost:8081/v1/models | grep Qwen`

## Run the full demo / video
```bash
# single-window text presenter (Scenes A→F):
MODEL_BACKEND=llamacpp ./scripts/demo_record.sh          # real model
MODEL_BACKEND=stub     ./scripts/demo_record.sh          # instant fallback

# full-screen recorder (Chrome dashboard LEFT + Terminal RIGHT) — RUN IN macOS Terminal, not Warp:
./scripts/record_demo_fullscreen.sh                      # output -> recordings/demo-fullscreen.mov
```
Scenes: A identity (KMS key never plaintext) · B earning loop (model runs job, agent paid) · C multi-agent marketplace · D x402 spend within caps (`demo3`) · E prompt-injection DRAIN blocked (per-call cap + payee allow-list + denied Permit types, zero moved, key never fetched) · F auto-refuel below floor + hard daily cap. Then live dashboard panel.

Key CLI: `python3 backend/cli/studio.py create <name>` / `resolve` / `balance` / `fund-job <name> --prompt ... --budget N` / `demo3 --name <name>` / `dashboard`. Worker: `python3 backend/studio_worker.py` (services ALL agents + auto-settle).

## Tests
```bash
make test-m3          # M3 suite
# full suite = 41 tests green (python + solidity)
forge test --root contracts   # solidity (EIP-3009)
```

## Git status / pending
- Repo bootstrapped: **7 clean commits**, no AI attribution, no secrets/caches committed.
- **`.env` gitignored** (has Anvil private key) → use `.env.example`.
- `.gitignore` covers: `.venv/`, `__pycache__/`, `contracts/out|cache|broadcast|lib`, `backend/.agent/`, `contracts/deployments/local.json`, `.env`, `.pytest_cache/`, `.code-review-graph/`, `graphify-out/`, `recordings/`.
- **UNCOMMITTED** (offered to `/commit`, awaiting go): `backend/studio_worker.py`, `scripts/demo_record.sh`, `scripts/record_demo_fullscreen.sh`, Makefile `worker` target, `.gitignore` updates, README link cleanup.

## Gotchas / fixes already applied
- Refuel scene needs **unique day key per take** (`day="refuel-demo-%d" % int(time.time())`) — else shared chain-day ledger hits cap and blocks.
- `/commit` skill fails on empty repo (runs `git log`) → first commit made manually, then skill for the rest.
- `git add` "contracts/ does not have a commit checked out" → nested `contracts/.git` from forge init; fixed with `rm -rf contracts/.git`.
- Pasting narration ("Say:…", "~2s") into shell errors — paste code blocks only.
- macOS fullscreen Spaces block cross-space window capture; Warp can't capture a Terminal in another Space. Full-screen recording must be run from the macOS Terminal itself (`screencapture -v -V600`), not from this assistant.
- 2-min bash timeouts (exit 143) on model-wait loops = default 120s timeout, not a stack error.

## Current state (as of this handoff)
Stack was last brought up clean: anvil + localstack fresh, contracts deployed, model serving Qwen at `:8081`. State clean (0 agents, 0 jobs, no `.agent` files, `recordings/` empty). Ready to record or to `/commit` the uncommitted scripts.

## Next step (your call)
- **Record:** run `./scripts/record_demo_fullscreen.sh` in macOS Terminal, Chrome left at `localhost:8080`.
- **Or commit:** `/commit` the uncommitted scripts/Makefile/.gitignore/README changes.
