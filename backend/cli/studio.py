"""`studio` — the MVP control-plane CLI (Milestone 1 commands).

  studio up                 boot anvil + localstack, seed AWS resources, build + deploy contracts
  studio down               tear everything down
  studio status             show infra + deployment health
  studio create <name>      create an agent: key in KMS, card in S3, identity NFT on-chain
  studio resolve <name>     resolve an agent's on-chain identity back to its Agent Card
  studio balance <name>     show an agent's ETH + USDT balances
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

# backend/ is the python import root (sdk/, runtime/, model/, .agent live here).
# the monorepo root is one level up and holds contracts/ + infra/ (the docker stack).
BACKEND_ROOT = Path(__file__).resolve().parents[1]
MONO_ROOT = BACKEND_ROOT.parent
REPO_ROOT = BACKEND_ROOT  # backwards-compat alias for the sdk path + AGENT_DIR below
sys.path.insert(0, str(BACKEND_ROOT / "sdk"))
sys.path.insert(0, str(BACKEND_ROOT))

import typer  # noqa: E402
from eth_utils import keccak  # noqa: E402

from plasma_mvp.adapter import LocalAdapter  # noqa: E402
from plasma_mvp.aws import Aws  # noqa: E402
from plasma_mvp.config import load_config  # noqa: E402
from plasma_mvp.keyvault import KeyVault  # noqa: E402
from plasma_mvp.registry import Registry  # noqa: E402
from plasma_mvp.storage import Storage  # noqa: E402

app = typer.Typer(add_completion=False, help="MVP Plasma AI Agent control plane")
AGENT_DIR = REPO_ROOT / ".agent"


def _sh(cmd, cwd=None, check=True):
    typer.echo("$ " + " ".join(cmd))
    return subprocess.run(cmd, cwd=cwd, check=check)


@app.command()
def up():
    """Boot infra (anvil + localstack), seed AWS resources, build + deploy contracts."""
    _sh(["docker", "compose", "up", "-d"], cwd=MONO_ROOT / "infra")
    typer.echo("waiting for services to become healthy...")
    _wait_healthy()
    _sh(["forge", "build"], cwd=MONO_ROOT / "contracts")
    cfg = load_config()
    # vm.writeJson won't create the dir — ensure it exists before deploy.
    # RELAYER_PK is read by the deploy script from the environment (load_config seeded it from .env).
    cfg.deployments_path.parent.mkdir(parents=True, exist_ok=True)
    _sh(
        ["forge", "script", "script/Deploy.s.sol:Deploy", "--rpc-url", cfg.rpc_url, "--broadcast"],
        cwd=MONO_ROOT / "contracts",
    )
    Storage().ensure_bucket()
    typer.secho("up: ready.", fg=typer.colors.GREEN)


@app.command()
def down():
    """Tear down all infra and volumes."""
    _sh(["docker", "compose", "down", "-v"], cwd=MONO_ROOT / "infra", check=False)


@app.command()
def status():
    """Show infra reachability + current deployment."""
    cfg = load_config()
    try:
        Aws(cfg).ping()
        typer.secho("localstack: reachable", fg=typer.colors.GREEN)
    except Exception as e:  # noqa: BLE001
        typer.secho("localstack: DOWN ({})".format(e), fg=typer.colors.RED)
    try:
        a = LocalAdapter(cfg)
        typer.secho("anvil: connected (chainId {})".format(cfg.chain_id), fg=typer.colors.GREEN)
        typer.echo("MockUSDT:        {}".format(a.addresses["MockUSDT"]))
        typer.echo("IdentityRegistry:{}".format(a.addresses["IdentityRegistry"]))
    except Exception as e:  # noqa: BLE001
        typer.secho("anvil/deploy: DOWN ({})".format(e), fg=typer.colors.RED)


@app.command()
def create(name: str, fund_usdt: float = typer.Option(0.0, help="MockUSDT to mint (whole tokens)")):
    """Create an agent: KMS key + S3 card + on-chain identity NFT."""
    _create_agent(name, fund_usdt)


def _create_agent(name: str, fund_usdt: float = 0.0):
    """Plain implementation (callable from other commands without Typer option wrappers)."""
    cfg = load_config()
    aws = Aws(cfg)
    kv = KeyVault(aws, cfg)
    storage = Storage(aws, cfg)
    storage.ensure_bucket()
    reg = Registry(aws, cfg)
    adapter = LocalAdapter(cfg)

    address = kv.new_agent_key(name)
    typer.echo("agent key created (KMS-encrypted): {}".format(address))

    # give the agent a tiny native-gas float so it can self-sign its own contract calls
    # (register/createJob/fund/submit/settle). Gas on Plasma is ~1e-7 gwei, so 0.001 XPL is
    # ~1000x headroom; the unused remainder stays in the agent wallet (not burned).
    adapter.fund_eth(address, 0.001)
    if fund_usdt > 0:
        adapter.mint_usdt(address, int(fund_usdt * 1_000_000))

    card = {
        "name": name,
        "description": "MVP agent {}".format(name),
        "address": address,
        "skills": [],
        "payment": {"token": "USDT", "chain": "local-anvil", "schemes": ["x402"]},
    }
    card_uri = storage.put(json.dumps(card, sort_keys=True).encode("utf-8"))

    agent_account = kv.signer_for(name)
    agent_id, tx_hash = adapter.register(agent_account, card_uri)
    reg.put_agent(name, agent_id, address, card_uri)

    AGENT_DIR.mkdir(exist_ok=True)
    (AGENT_DIR / "{}.json".format(name)).write_text(
        json.dumps({"name": name, "agentId": agent_id, "address": address, "cardURI": card_uri}, indent=2)
    )

    typer.secho("registered agent '{}' -> agentId {}".format(name, agent_id), fg=typer.colors.GREEN)
    typer.echo("cardURI: {}".format(card_uri))
    typer.echo("tx:      {}".format(tx_hash))


@app.command()
def resolve(name: str):
    """Resolve an agent's on-chain identity back to its Agent Card (proves the round-trip)."""
    cfg = load_config()
    meta = _load_agent(name)
    adapter = LocalAdapter(cfg)
    storage = Storage(cfg=cfg)
    on_chain_uri = adapter.resolve(meta["agentId"])
    owner = adapter.owner_of(meta["agentId"])
    card = json.loads(storage.get(on_chain_uri))
    typer.echo("agentId:   {}".format(meta["agentId"]))
    typer.echo("owner:     {}".format(owner))
    typer.echo("cardURI:   {}".format(on_chain_uri))
    typer.echo("card:      {}".format(json.dumps(card, indent=2)))


@app.command()
def balance(name: str):
    """Show an agent's ETH (gas) and MockUSDT balances."""
    cfg = load_config()
    meta = _load_agent(name)
    adapter = LocalAdapter(cfg)
    eth = adapter.eth_balance(meta["address"]) / 1e18
    usdt = adapter.usdt_balance(meta["address"]) / 1e6
    typer.echo("{}  ETH={:.4f}  USDT={:.6f}".format(meta["address"], eth, usdt))


@app.command("fund-job")
def fund_job(
    name: str,
    prompt: str = typer.Option(..., help="the job request the agent will run through the model"),
    budget: float = typer.Option(5.0, help="USDT escrowed for the job"),
    ttl: int = typer.Option(3600, help="seconds until the job deadline"),
):
    """Act as a buyer: store the request, create + fund an escrowed job for the agent."""
    _fund_job(name, prompt, budget, ttl)


def _fund_job(name: str, prompt: str, budget: float = 5.0, ttl: int = 3600) -> int:
    """Plain implementation (callable from `demo` without Typer option wrappers)."""
    cfg = load_config()
    meta = _load_agent(name)
    aws = Aws(cfg)
    storage = Storage(aws, cfg)
    storage.ensure_bucket()
    adapter = LocalAdapter(cfg)
    buyer = adapter.relayer

    budget_base = int(budget * 1_000_000)
    adapter.mint_usdt(buyer.address, budget_base)  # ensure the buyer holds escrow funds

    request_uri = storage.put(json.dumps({"prompt": prompt}, sort_keys=True).encode("utf-8"))
    desc_hash = bytes.fromhex(storage.hash_of(request_uri))  # content-addressed: descHash == S3 key
    now = adapter.w3.eth.get_block("latest")["timestamp"]
    job_id = adapter.create_job(buyer, meta["address"], desc_hash, now + ttl)
    adapter.fund_job(buyer, job_id, budget_base)

    typer.secho("funded job {} for agent '{}' (budget {} USDT)".format(job_id, name, budget),
                fg=typer.colors.GREEN)
    typer.echo("request: {}".format(request_uri))
    return job_id


@app.command()
def run(name: str, port: int = 8090, keeper: bool = True):
    """Run the agent runtime (poll loop + settle keeper) as an HTTP service."""
    import uvicorn

    os.environ["AGENT_NAME"] = name
    os.environ["RUN_KEEPER"] = "1" if keeper else "0"
    typer.echo("starting runtime for '{}' on :{} (model backend: {})".format(
        name, port, os.environ.get("MODEL_BACKEND", "stub")))
    uvicorn.run("runtime.app:app", host="127.0.0.1", port=port, log_level="info")


@app.command()
def demo(name: str = "demo", prompt: str = "Summarize: agents that earn stablecoins autonomously."):
    """End-to-end: create agent -> fund a job -> agent executes -> keeper settles -> agent paid."""
    from runtime.agent import AgentRuntime
    from runtime.keeper import SettleKeeper

    cfg = load_config()
    # create the agent if it doesn't exist yet
    if not (AGENT_DIR / "{}.json".format(name)).exists():
        _create_agent(name)
    meta = _load_agent(name)
    adapter = LocalAdapter(cfg)

    before = adapter.usdt_balance(meta["address"]) / 1e6
    typer.echo("agent balance before: {:.6f} USDT".format(before))

    _fund_job(name, prompt=prompt, budget=5.0, ttl=3600)

    runtime = AgentRuntime(name, cfg=cfg)
    typer.echo("model backend: {}".format(runtime.model.backend))
    done = runtime.process_funded_once()
    typer.secho("agent submitted jobs: {}".format(done), fg=typer.colors.CYAN)

    typer.echo("waiting out the {}s dispute window...".format(adapter.dispute_window))
    time.sleep(adapter.dispute_window + 1)

    settled = SettleKeeper(cfg).settle_due_once()
    typer.secho("keeper settled jobs: {}".format(settled), fg=typer.colors.CYAN)

    after = adapter.usdt_balance(meta["address"]) / 1e6
    typer.secho("agent balance after: {:.6f} USDT  (+{:.6f})".format(after, after - before),
                fg=typer.colors.GREEN)


@app.command()
def serve(port: int = 8080):
    """Launch the Studio API server — REST + WebSocket live state + bundled UI — on :8080.

    This is the backend the React studio-frontend talks to (create agents, fund jobs, spend,
    refuel, run the injection drill) over JSON + /ws, plus a self-contained fallback UI at `/`.
    """
    _serve(port)


@app.command(hidden=True)
def dashboard(port: int = 8080):
    """Deprecated alias for `serve` (kept so old `studio dashboard` muscle-memory still works)."""
    _serve(port)


def _serve(port: int):
    import uvicorn

    typer.echo("studio api on http://localhost:{}  (REST + /ws live state + UI)".format(port))
    uvicorn.run("studio_api.app:app", host="127.0.0.1", port=port, log_level="warning")


@app.command()
def demo3(name: str = "demo", port: int = 8402):
    """M3 end-to-end: agent pays a 402-gated resource (within caps), then auto-refuels below floor."""
    import threading

    import httpx
    import uvicorn

    from plasma_mvp.events import EventLog
    from plasma_mvp.keyvault import KeyVault
    from plasma_mvp.refuel import AutoRefueler, RefuelLedger
    from plasma_mvp.signer import X402Signer
    from runtime.resource import X402Client, X402ResourceServer, make_resource_app

    cfg = load_config()
    aws = Aws(cfg)
    adapter = LocalAdapter(cfg)
    events = EventLog(aws, cfg)

    if not (AGENT_DIR / "{}.json".format(name)).exists():
        _create_agent(name)
    meta = _load_agent(name)
    agent_account = KeyVault(aws, cfg).signer_for(name)

    # fund the agent so it can spend; the resource has its own receiving wallet
    adapter.mint_usdt(meta["address"], 10_000_000)
    from eth_account import Account
    payee = Account.create().address
    price = 2_000_000

    server = X402ResourceServer(adapter, pay_to=payee, price=price, events=events)
    fastapi_app = make_resource_app(server)
    cfg_uv = uvicorn.Config(fastapi_app, host="127.0.0.1", port=port, log_level="warning")
    uv = uvicorn.Server(cfg_uv)
    t = threading.Thread(target=uv.run, daemon=True)
    t.start()
    for _ in range(50):
        try:
            if httpx.get("http://127.0.0.1:{}/health".format(port), timeout=1).status_code == 200:
                break
        except Exception:  # noqa: BLE001
            time.sleep(0.1)

    typer.secho("--- x402 SPEND ---", fg=typer.colors.CYAN)
    signer = X402Signer(lambda: agent_account, max_value_per_call=3_000_000,
                        session_budget=6_000_000, allowed_payees=[payee])
    client = X402Client(httpx.Client(base_url="http://127.0.0.1:{}".format(port)), signer)
    r = client.get("/resource")
    typer.echo("resource status: {}".format(r.status_code))
    typer.echo("resource payee balance: {:.6f} USDT".format(adapter.usdt_balance(payee) / 1e6))
    typer.echo("signer spent: {:.6f} USDT (remaining {:.6f})".format(
        signer.spent / 1e6, signer.remaining / 1e6))

    typer.secho("--- AUTO-REFUEL ---", fg=typer.colors.CYAN)
    owner = adapter.relayer
    adapter.mint_usdt(owner.address, 50_000_000)
    refueler = AutoRefueler(adapter, owner_account=owner, floor=20_000_000, refill=5_000_000,
                            daily_cap=8_000_000, ledger=RefuelLedger(aws, cfg), cfg=cfg, events=events)
    before = adapter.usdt_balance(meta["address"]) / 1e6
    out1 = refueler.maybe_refuel(meta["address"])
    out2 = refueler.maybe_refuel(meta["address"])  # second push should hit the daily cap
    typer.echo("refuel #1: {}".format(out1.get("reason", "refueled +5 USDT")))
    typer.echo("refuel #2: {}".format(out2.get("reason", "refueled +5 USDT")))
    typer.echo("agent balance {:.6f} -> {:.6f} USDT".format(
        before, adapter.usdt_balance(meta["address"]) / 1e6))
    typer.secho("demo3 complete — see the dashboard (studio dashboard) for the live feed.",
                fg=typer.colors.GREEN)


def _load_agent(name: str) -> dict:
    path = AGENT_DIR / "{}.json".format(name)
    if not path.exists():
        typer.secho("no such agent '{}' (run: studio create {})".format(name, name), fg=typer.colors.RED)
        raise typer.Exit(1)
    return json.loads(path.read_text())


def _wait_healthy(timeout: int = 60):
    cfg = load_config()
    deadline = time.time() + timeout
    aws_ok = chain_ok = False
    while time.time() < deadline:
        try:
            if not aws_ok:
                Aws(cfg).ping()
                aws_ok = True
        except Exception:  # noqa: BLE001
            pass
        try:
            if not chain_ok:
                from web3 import Web3

                chain_ok = Web3(Web3.HTTPProvider(cfg.rpc_url)).is_connected()
        except Exception:  # noqa: BLE001
            pass
        if aws_ok and chain_ok:
            return
        time.sleep(2)
    raise TimeoutError("infra not healthy within {}s (aws={}, chain={})".format(timeout, aws_ok, chain_ok))


if __name__ == "__main__":
    app()
