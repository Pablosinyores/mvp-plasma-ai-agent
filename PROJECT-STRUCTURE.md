# Project structure

`mvp-plasma-ai-agent/` is the **monorepo root**. It holds four peer domains — backend, frontend,
contracts, and infra — each as its own modular folder. Nothing is crammed into a single bucket.

```
mvp-plasma-ai-agent/                 ← monorepo root
│
├── backend/                         ← BACKEND (Python: control plane, runtime, API)
│   ├── sdk/plasma_mvp/              ←   core SDK: chain adapter, KMS keyvault, x402 signer,
│   │                                    registry, events, refuel, storage, config
│   ├── runtime/                     ←   agent runtime, settle keeper, x402 resource server
│   ├── model/                       ←   model gateway (stub / llama.cpp backends)
│   ├── cli/studio.py                ←   `studio` CLI (create / fund-job / demo / demo3 / serve)
│   ├── studio_api/                  ←   Studio API server: REST + WebSocket live state (serves :8080)
│   │   ├── app.py                   ←     REST API + /ws broadcaster + /panel + /api/* actions
│   │   └── static/index.html        ←     zero-build fallback UI (used by demo recording)
│   ├── studio_worker.py             ←   the always-on worker: runs + settles every agent's jobs
│   ├── tests/                       ←   pytest suites (M1 / M2 / M3)
│   └── requirements*.txt            ←   python deps
│
├── studio-frontend/                 ← FRONTEND (standalone React + Vite app, serves :5173)
│                                        talks to backend over REST + WS — see its README.md
│
├── contracts/                       ← CONTRACTS (Solidity: MockUSDT/EIP-3009, IdentityRegistry,
│                                        Commerce escrow) — Foundry project
│
├── infra/                           ← INFRA (local container stack)
│   ├── docker-compose.yml           ←   anvil :8545 · localstack :4566 · llama.cpp :8081
│   └── localstack/init.sh           ←   AWS seed (S3 / KMS / DynamoDB / SQS)
│
├── scripts/                         ← orchestration: demo_record.sh, record_demo_fullscreen.sh
├── Makefile                         ← up / down / model / demo / test / fe targets
├── .env                             ← RELAYER_PK etc. (read by backend config)
└── docs: README · ARCHITECTURE · DEVELOPMENT · RUNBOOK · MILESTONES · HANDOFF
```

Runtime state lives under `backend/.agent/` (per-agent metadata). Contract deployment addresses
are written to `contracts/deployments/local.json` by `forge`.

## How the domains wire together

- **backend/** is the Python import root. Every entrypoint puts `backend/` and `backend/sdk` on the
  path, so `plasma_mvp`, `runtime`, `model`, `cli`, and `studio_api` all import as top-level packages.
- **config** (`backend/sdk/plasma_mvp/config.py`) resolves the monorepo root (one level above
  `backend/`) for cross-domain paths: `contracts/deployments/local.json`, `contracts/out`, `.env`.
- **infra/** owns the docker stack. The CLI runs `docker compose` with `cwd=infra/`; the Makefile
  uses `docker compose -f infra/docker-compose.yml`. The compose file pins
  `name: mvp-plasma-ai-agent` so container names are stable regardless of who invokes it.
- **studio-frontend/** shares **only a JSON contract** with the backend (REST + WebSocket on :8080,
  CORS-enabled). No code is shared. The backend URL is env-driven (`VITE_API_BASE`).

Two ways to view the same data:
1. **Full app** — the React SPA on :5173 (`make fe`): interactive create / fund / spend / refuel / drills.
2. **Zero-build fallback** — `backend/studio_api/static/index.html` served at `http://localhost:8080/`
   (self-contained, no Node needed; this is what the demo recording opens in Chrome).

## Run the whole thing

```bash
# from the monorepo root, venv active
make up && make model                    # infra (anvil+localstack) + model container
make serve                               # API + WS + fallback UI on :8080   (python backend/cli/studio.py serve)
make worker                              # the worker that runs + settles jobs

# frontend (separate terminal)
make fe                                  # pnpm install + dev server → http://localhost:5173
```

Clean rebuild from scratch (keeps the model download):

```bash
docker compose -f infra/docker-compose.yml down
rm -rf backend/.agent contracts/deployments/local.json
make up && make model
```
