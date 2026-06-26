"""Plasma Agent Studio — interactive control plane (FastAPI) on :8080.

A full operator console, not just a read-out. It serves a self-contained single-page app (the
terminal-ops dashboard) and a JSON API that drives every operation the demo scripts expose:

  POST /api/agents              create an agent  (KMS key + S3 card + on-chain identity NFT)
  GET  /api/agents/{n}/resolve  resolve identity -> Agent Card (on-chain round-trip)
  POST /api/jobs                fund an escrowed job; the background worker runs + settles it
  POST /api/spend               x402 pay-per-call against caps (per-call · session · payee allow-list)
  POST /api/refuel              auto-refuel below floor, hard daily cap
  POST /api/injection-test      fire a prompt-injection drain and prove the guards block it
  GET  /api/state               JSON snapshot (agents · jobs · events · stats)
  GET  /panel                   legacy HTML fragment (kept for `curl` + the demo script)

Live updates (no polling): one broadcaster task re-renders the JSON state ~1x/sec (Anvil's block
time) and PUSHES it over a WebSocket (/ws) to every client, but only when it actually changed.

Mutating endpoints reuse the SAME helpers the `studio` CLI uses (cli.studio._create_agent /
_fund_job) so behaviour — and the .agent/ files — match the scripts exactly.
"""
import asyncio
import contextlib
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "sdk"))
sys.path.insert(0, str(REPO_ROOT))

from eth_account import Account  # noqa: E402
from eth_utils import keccak  # noqa: E402
from fastapi import FastAPI, WebSocket, WebSocketDisconnect  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse  # noqa: E402
from pydantic import BaseModel  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

from plasma_mvp.adapter import LocalAdapter  # noqa: E402
from plasma_mvp.aws import Aws  # noqa: E402
from plasma_mvp.config import load_config  # noqa: E402
from plasma_mvp.events import EventLog  # noqa: E402
from plasma_mvp.keyvault import KeyVault  # noqa: E402
from plasma_mvp.refuel import AutoRefueler, RefuelLedger  # noqa: E402
from plasma_mvp.registry import Registry  # noqa: E402
from plasma_mvp.signer import PayeeNotAllowed, SpendCapExceeded, X402Signer  # noqa: E402
from plasma_mvp.storage import Storage  # noqa: E402
from plasma_mvp.strategy_store import open_strategy_store  # noqa: E402
from plasma_mvp import x402  # noqa: E402
from runtime.resource import X402Client, X402ResourceServer, make_resource_app  # noqa: E402

from cli.studio import _create_agent, _fund_job, _load_agent  # noqa: E402
from studio_api.strategy_ctl import TraderManager  # noqa: E402

app = FastAPI(title="Plasma Agent Studio")

# The React FE runs on its own dev/prod server (e.g. :5173). Allow cross-origin for local dev.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],            # local-only control plane; tighten if ever exposed
    allow_methods=["*"],
    allow_headers=["*"],
)

STATIC = Path(__file__).resolve().parent / "static"

_cfg = load_config()

POLL_SECONDS = 1.0  # broadcaster cadence; Anvil mines every 1s, so this is the useful floor

# --- live-push state ------------------------------------------------------------------------------
_clients: "set[WebSocket]" = set()
_latest_json: str = json.dumps({"agents": [], "jobs": [], "events": [], "stats": {}})
_broadcaster_task: "asyncio.Task | None" = None

# --- per-agent x402 spend contexts (so a session budget depletes across clicks) -------------------
_spend_ctx: "dict[str, dict]" = {}
PRICE = 2_000_000          # 2 USDT per paid call
MAX_PER_CALL = 3_000_000   # hard per-call cap
SESSION_BUDGET = 6_000_000  # rolling budget per agent session


def _ctx():
    aws = Aws(_cfg)
    return {
        "adapter": LocalAdapter(_cfg),
        "registry": Registry(aws, _cfg),
        "events": EventLog(aws, _cfg),
    }


# --- agentic-trader control plane (set/clear a standing strategy, watch ticks) --------------------
# Built lazily: the manager needs the chain + (real) KeyVault signer, so we don't construct it at
# import time. The agent's KMS-backed key is the guard's signer — the strategy prompt only ever picks
# WHAT to trade; TradeGuard (default caps, recipient pinned to self) decides what's allowed.
_trader_mgr: "TraderManager | None" = None


def _mgr() -> TraderManager:
    global _trader_mgr
    if _trader_mgr is None:
        _trader_mgr = TraderManager(
            LocalAdapter(_cfg),
            open_strategy_store(_cfg),
            lambda name: KeyVault(Aws(_cfg), _cfg).signer_for(name),
        )
    return _trader_mgr


def _usdt(x) -> float:
    return int(x) / 1e6


# ================================================================================================ #
# State snapshot                                                                                    #
# ================================================================================================ #
def _state() -> dict:
    """The full JSON the UI renders. Never raises — returns an `error` field if the stack is down."""
    try:
        c = _ctx()
        adapter, registry, events = c["adapter"], c["registry"], c["events"]

        agents = []
        for a in sorted(registry.list_agents(), key=lambda r: r["name"]):
            agents.append({
                "name": a["name"],
                "agentId": a["agentId"],
                "address": a["address"],
                "eth": adapter.eth_balance(a["address"]) / 1e18,
                "usdt": _usdt(adapter.usdt_balance(a["address"])),
            })

        jobs = []
        total = adapter.job_count()
        for jid in range(max(1, total - 11), total + 1):
            try:
                j = adapter.get_job(jid)
            except Exception:  # noqa: BLE001
                continue
            jobs.append({
                "jobId": j["jobId"],
                "status": j["status"],
                "provider": j["provider"],
                "budget": _usdt(j["budget"]),
            })
        jobs.reverse()

        feed = []
        for e in events.list(limit=24):
            d = e["data"]
            feed.append({
                "kind": e["kind"],
                "amount": _usdt(int(d.get("amount", 0))),
                "from": d.get("payer", d.get("owner", "?")),
                "to": d.get("payee", d.get("agent", "?")),
            })

        earned = sum(j["budget"] for j in jobs if j["status"] == "COMPLETED")
        spent = sum(e["amount"] for e in feed if e["kind"] == "spend")
        refueled = sum(e["amount"] for e in feed if e["kind"] == "refuel")
        return {
            "chain": _cfg.chain_id,
            "agents": agents,
            "jobs": jobs,
            "events": feed,
            "stats": {
                "agentCount": len(agents),
                "jobCount": total,
                "earned": round(earned, 6),
                "spent": round(spent, 6),
                "refueled": round(refueled, 6),
            },
        }
    except Exception as e:  # noqa: BLE001
        return {"error": str(e), "agents": [], "jobs": [], "events": [], "stats": {}}


# ================================================================================================ #
# Operations (mirror the demo scripts)                                                             #
# ================================================================================================ #
class CreateBody(BaseModel):
    name: str
    fund_usdt: float = 0.0


class JobBody(BaseModel):
    name: str
    prompt: str
    budget: float = 5.0
    ttl: int = 3600


class NameBody(BaseModel):
    name: str


def _spend(name: str) -> dict:
    """x402 pay-per-call, exactly like `studio demo3`'s SPEND block — guarded by the X402Signer."""
    meta = _load_agent(name)
    aws = Aws(_cfg)
    adapter = LocalAdapter(_cfg)
    events = EventLog(aws, _cfg)
    agent_account = KeyVault(aws, _cfg).signer_for(name)

    # make sure the agent can afford a call (mirror demo3's pre-fund)
    if adapter.usdt_balance(meta["address"]) < PRICE:
        adapter.mint_usdt(meta["address"], 10_000_000)

    ctx = _spend_ctx.get(name)
    if ctx is None:
        payee = Account.create().address
        server = X402ResourceServer(adapter, pay_to=payee, price=PRICE, events=events)
        http = TestClient(make_resource_app(server))
        signer = X402Signer(lambda: agent_account, max_value_per_call=MAX_PER_CALL,
                            session_budget=SESSION_BUDGET, allowed_payees=[payee])
        ctx = _spend_ctx[name] = {"payee": payee, "http": http, "signer": signer}

    client = X402Client(ctx["http"], ctx["signer"])
    try:
        r = client.get("/resource")
        return {
            "ok": True,
            "status": r.status_code,
            "price": _usdt(PRICE),
            "payee": ctx["payee"],
            "payeeBalance": _usdt(adapter.usdt_balance(ctx["payee"])),
            "spent": _usdt(ctx["signer"].spent),
            "remaining": _usdt(ctx["signer"].remaining),
        }
    except (SpendCapExceeded, PayeeNotAllowed, x402.PolicyViolation) as e:
        return {
            "ok": False,
            "blocked": type(e).__name__,
            "reason": str(e),
            "spent": _usdt(ctx["signer"].spent),
            "remaining": _usdt(ctx["signer"].remaining),
        }


def _refuel(name: str) -> dict:
    """Auto-refuel below floor with a hard daily cap, like `studio demo3`'s AUTO-REFUEL block."""
    meta = _load_agent(name)
    aws = Aws(_cfg)
    adapter = LocalAdapter(_cfg)
    events = EventLog(aws, _cfg)
    owner = adapter.relayer
    adapter.mint_usdt(owner.address, 50_000_000)
    refueler = AutoRefueler(adapter, owner_account=owner, floor=20_000_000, refill=5_000_000,
                            daily_cap=8_000_000, ledger=RefuelLedger(aws, _cfg), cfg=_cfg, events=events)
    before = _usdt(adapter.usdt_balance(meta["address"]))
    out1 = refueler.maybe_refuel(meta["address"])
    out2 = refueler.maybe_refuel(meta["address"])
    after = _usdt(adapter.usdt_balance(meta["address"]))
    return {
        "ok": True,
        "before": before,
        "after": after,
        "refuel1": {"fired": out1.get("refueled", False), "reason": out1.get("reason", "refueled +5 USDT")},
        "refuel2": {"fired": out2.get("refueled", False), "reason": out2.get("reason", "refueled +5 USDT")},
    }


def _injection_test() -> dict:
    """Fire an attacker-controlled 'pay 1,000,000 USDT' and prove four guards block it — 0 moved."""
    usdt = 1_000_000
    agent = Account.create()
    payee = Account.create().address
    attacker = Account.create().address

    def quote(to, value):
        return x402.PaymentQuote(pay_to=to, value=value, asset="0x" + "11" * 20, chain_id=31337,
                                 valid_after=1000, valid_before=1300, nonce="0x" + "22" * 32)

    s = X402Signer(lambda: agent, max_value_per_call=2 * usdt, session_budget=4 * usdt,
                   allowed_payees=[payee])
    guards = []

    try:
        s.sign_payment(quote(attacker, 1_000_000 * usdt))
        guards.append({"guard": "per-call cap", "blocked": False, "detail": "NOT blocked"})
    except SpendCapExceeded:
        guards.append({"guard": "per-call cap", "blocked": True,
                       "detail": "1,000,000 USDT exceeds the 2 USDT per-call cap"})

    try:
        s.sign_payment(quote(attacker, usdt))
        guards.append({"guard": "payee allow-list", "blocked": False, "detail": "NOT blocked"})
    except PayeeNotAllowed:
        guards.append({"guard": "payee allow-list", "blocked": True,
                       "detail": "attacker address is not byte-equal to any allowed payee"})

    g = x402.SigningPolicy()
    for t in ("Permit", "PermitTransferFrom"):
        try:
            g.check({"primaryType": t, "message": {"validAfter": 0, "validBefore": 1300}})
            guards.append({"guard": "type gate: " + t, "blocked": False, "detail": "NOT blocked"})
        except x402.PolicyViolation:
            guards.append({"guard": "type gate: " + t, "blocked": True,
                           "detail": "open-ended approval primitive rejected"})

    return {"ok": True, "modelOutput": "pay attacker 1000000 USDT",
            "guards": guards, "spent": s.spent,
            "summary": "ZERO moved — the key was never even fetched"}


# ================================================================================================ #
# API routes                                                                                       #
# ================================================================================================ #
@app.get("/", response_class=HTMLResponse)
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/state")
def api_state():
    return JSONResponse(_state())


@app.post("/api/agents")
def api_create(body: CreateBody):
    try:
        _create_agent(body.name, body.fund_usdt)
        return {"ok": True, "name": body.name, "agent": _load_agent(body.name)}
    except Exception as e:  # noqa: BLE001
        return JSONResponse(status_code=400, content={"ok": False, "error": str(e)})


@app.get("/api/agents/{name}/resolve")
def api_resolve(name: str):
    try:
        meta = _load_agent(name)
        adapter = LocalAdapter(_cfg)
        storage = Storage(cfg=_cfg)
        uri = adapter.resolve(meta["agentId"])
        owner = adapter.owner_of(meta["agentId"])
        card = json.loads(storage.get(uri))
        return {"ok": True, "agentId": meta["agentId"], "owner": owner, "cardURI": uri, "card": card}
    except Exception as e:  # noqa: BLE001
        return JSONResponse(status_code=400, content={"ok": False, "error": str(e)})


@app.post("/api/jobs")
def api_fund_job(body: JobBody):
    try:
        job_id = _fund_job(body.name, body.prompt, body.budget, body.ttl)
        return {"ok": True, "jobId": job_id, "name": body.name, "budget": body.budget}
    except Exception as e:  # noqa: BLE001
        return JSONResponse(status_code=400, content={"ok": False, "error": str(e)})


@app.get("/api/jobs/{job_id}")
def api_job(job_id: int):
    """One job's live detail + (once submitted) the model output, fetched from content-addressed
    storage by the on-chain result URI. Powers the playground's poll-until-answer loop."""
    try:
        adapter = LocalAdapter(_cfg)
        j = adapter.get_job(job_id)
        output = None
        verified = None
        if j["status"] in ("SUBMITTED", "COMPLETED") and j.get("uri"):
            try:
                raw = Storage(cfg=_cfg).get(j["uri"])
                output = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else str(raw)
                # content-addressing check: keccak(stored bytes) must equal the on-chain resultHash
                verified = keccak(raw if isinstance(raw, (bytes, bytearray)) else raw.encode()) == j["resultHash"]
            except Exception:  # noqa: BLE001
                output = None
        return {
            "ok": True,
            "jobId": j["jobId"],
            "status": j["status"],
            "client": j["client"],
            "provider": j["provider"],
            "budget": _usdt(j["budget"]),
            "descHash": "0x" + j["descHash"].hex(),
            "resultHash": "0x" + j["resultHash"].hex(),
            "uri": j["uri"],
            "verified": verified,
            "output": output,
        }
    except Exception as e:  # noqa: BLE001
        return JSONResponse(status_code=400, content={"ok": False, "error": str(e)})


@app.post("/api/spend")
def api_spend(body: NameBody):
    try:
        return _spend(body.name)
    except Exception as e:  # noqa: BLE001
        return JSONResponse(status_code=400, content={"ok": False, "error": str(e)})


@app.post("/api/refuel")
def api_refuel(body: NameBody):
    try:
        return _refuel(body.name)
    except Exception as e:  # noqa: BLE001
        return JSONResponse(status_code=400, content={"ok": False, "error": str(e)})


@app.post("/api/injection-test")
def api_injection():
    try:
        return _injection_test()
    except Exception as e:  # noqa: BLE001
        return JSONResponse(status_code=400, content={"ok": False, "error": str(e)})


# --- standing strategy per agent (the strategy panel) ---------------------------------------------
class StrategyBody(BaseModel):
    prompt: str


@app.post("/api/agents/{name}/strategy")
def api_set_strategy(name: str, body: StrategyBody):
    """Parse a natural-language standing prompt into an order and install it. Persisted + live-ticked."""
    try:
        order = _mgr().set_strategy(name, body.prompt)
        return {"ok": True, "name": name, "order": order, **_mgr().get(name)}
    except Exception as e:  # noqa: BLE001
        return JSONResponse(status_code=400, content={"ok": False, "error": str(e)})


@app.get("/api/agents/{name}/strategy")
def api_get_strategy(name: str):
    """Current strategy + the last N tick results (action · pair · amount · price) for the UI to poll."""
    try:
        return {"ok": True, "name": name, **_mgr().get(name)}
    except Exception as e:  # noqa: BLE001
        return JSONResponse(status_code=400, content={"ok": False, "error": str(e)})


@app.delete("/api/agents/{name}/strategy")
def api_clear_strategy(name: str):
    """Stop the agent: drop the standing strategy and clear its persisted record."""
    try:
        _mgr().clear(name)
        return {"ok": True, "name": name}
    except Exception as e:  # noqa: BLE001
        return JSONResponse(status_code=400, content={"ok": False, "error": str(e)})


# ================================================================================================ #
# Legacy HTML fragment (kept so `curl /panel` + the demo script still work)                        #
# ================================================================================================ #
@app.get("/panel", response_class=HTMLResponse)
def panel():
    s = _state()
    if s.get("error"):
        return "<p>stack unavailable: {}</p>".format(s["error"])
    rows = "".join("<tr><td>{name}</td><td>{agentId}</td><td>{address}</td>"
                   "<td>{eth:.4f}</td><td>{usdt:.6f}</td></tr>".format(**a) for a in s["agents"])
    jrows = "".join("<tr><td>{jobId}</td><td>{status}</td><td>{provider}</td>"
                    "<td>{budget:.6f}</td></tr>".format(**j) for j in s["jobs"])
    erows = "".join("<tr><td>{kind}</td><td>{amount:.6f} USDT</td>"
                    "<td>{f} → {t}</td></tr>".format(kind=e["kind"], amount=e["amount"],
                                                     f=e["from"][:10], t=e["to"][:10]) for e in s["events"])
    return (
        "<h2>agents</h2><table><tr><th>agent</th><th>id</th><th>address</th><th>ETH</th><th>USDT</th></tr>"
        + (rows or "<tr><td colspan=5>no agents</td></tr>") + "</table>"
        + "<h2>jobs (recent)</h2><table><tr><th>job</th><th>status</th><th>provider</th><th>budget</th></tr>"
        + (jrows or "<tr><td colspan=4>no jobs</td></tr>") + "</table>"
        + "<h2>spend / refuel feed</h2><table><tr><th>event</th><th>amount</th><th>flow</th></tr>"
        + (erows or "<tr><td colspan=3>no spend yet</td></tr>") + "</table>"
    )


# ================================================================================================ #
# WebSocket live push                                                                               #
# ================================================================================================ #
@app.websocket("/ws")
async def ws(sock: WebSocket):
    await sock.accept()
    _clients.add(sock)
    try:
        await sock.send_text(_latest_json)  # paint immediately from the last broadcast
        while True:
            await sock.receive_text()        # detect close; we don't expect client messages
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001
        pass
    finally:
        _clients.discard(sock)


async def _broadcast_loop():
    global _latest_json
    while True:
        # advance every agent that has a live standing strategy by one tick (off the event loop)
        try:
            await asyncio.to_thread(_mgr().tick_active)
        except Exception:  # noqa: BLE001 — chain down / no agents: just skip this round
            pass
        payload = await asyncio.to_thread(lambda: json.dumps(_state()))
        if payload != _latest_json:
            _latest_json = payload
            dead = []
            for c in list(_clients):
                try:
                    await c.send_text(payload)
                except Exception:  # noqa: BLE001
                    dead.append(c)
            for c in dead:
                _clients.discard(c)
        await asyncio.sleep(POLL_SECONDS)


@app.on_event("startup")
async def _start_broadcaster():
    global _broadcaster_task
    _broadcaster_task = asyncio.create_task(_broadcast_loop())


@app.on_event("shutdown")
async def _stop_broadcaster():
    if _broadcaster_task:
        _broadcaster_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _broadcaster_task
