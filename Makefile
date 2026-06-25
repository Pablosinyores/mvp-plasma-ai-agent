.PHONY: up down status build deploy create resolve test test-contracts test-aws test-e2e test-e2e-m2 test-m3 model demo demo3 serve serve-dev dashboard worker clean fe fe-build

# Monorepo layout: backend/ (python) · studio-frontend/ (FE) · contracts/ · infra/ (docker stack)
COMPOSE := docker compose -f infra/docker-compose.yml

# --- infra ---
up:            ## boot infra, seed AWS, build + deploy contracts
	python3 backend/cli/studio.py up

down:          ## tear down infra + volumes
	python3 backend/cli/studio.py down

status:        ## show infra + deployment health
	python3 backend/cli/studio.py status

# --- contracts ---
build:
	cd contracts && forge build

deploy:
	cd contracts && forge script script/Deploy.s.sol:Deploy --rpc-url $${RPC_URL:-http://localhost:8545} --broadcast

# --- agent ops ---
create:        ## make NAME=alpha
	python3 backend/cli/studio.py create $(NAME)

resolve:
	python3 backend/cli/studio.py resolve $(NAME)

# --- model (M2) — llama.cpp container on :8081 (CPU; stands in for an EC2 model node) ---
model:         ## start the llama.cpp model container (first run pulls image + downloads GGUF)
	$(COMPOSE) up -d model
	@echo "model server on http://localhost:8081/v1 — set MODEL_BACKEND=llamacpp to use it"

model-logs:
	$(COMPOSE) logs -f model

demo:          ## end-to-end earning loop (MODEL_BACKEND: stub by default, llamacpp for real model)
	python3 backend/cli/studio.py demo

demo3:         ## M3 e2e — x402 spend within caps + auto-refuel below floor (needs `make up`)
	python3 backend/cli/studio.py demo3

serve:         ## launch the Studio API server (REST + WebSocket + UI) on :8080
	python3 backend/cli/studio.py serve

serve-dev:     ## same as serve, but auto-restarts on backend/ code changes (dev only)
	python3 backend/cli/studio.py serve --reload

dashboard: serve   ## deprecated alias for `serve`

worker:        ## run the studio worker — services every agent + settles (honors MODEL_BACKEND)
	python3 backend/studio_worker.py

# --- frontend (studio-frontend/) ---
fe:            ## run the React FE dev server on :5173 (needs the backend on :8080)
	cd studio-frontend && pnpm install && pnpm dev

fe-build:      ## production bundle -> studio-frontend/dist
	cd studio-frontend && pnpm install && pnpm build

# --- tests ---
# `test` runs everything that needs no Docker; `test-e2e` is the LocalStack-backed run.
test: test-contracts test-aws

test-contracts:           ## solidity unit tests (no docker)
	cd contracts && forge test

test-aws:                 ## AWS code path via moto (no docker)
	pytest -q backend/tests/test_m1_aws_moto.py

test-e2e:                 ## M1 full e2e — requires `make up` (anvil + localstack) first
	pytest -q backend/tests/test_m1.py

test-e2e-m2:              ## M2 earning-loop e2e (stub model) — requires `make up` first
	pytest -q backend/tests/test_m2.py

test-m3:                  ## M3 e2e — guardrails (no docker) + x402 spend/refuel (needs `make up`)
	pytest -q backend/tests/test_m3.py

clean:
	cd contracts && forge clean
	rm -rf backend/.agent
