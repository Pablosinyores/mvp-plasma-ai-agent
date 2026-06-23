"""FastAPI agent server for `studio run <name>`.

Lifespan starts the agent poll loop and the settle keeper in background threads, then serves a few
read-only endpoints. The heavy lifting lives in AgentRuntime / SettleKeeper; this is just the host.
"""
import os
import sys
import threading
from contextlib import asynccontextmanager
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "sdk"))
sys.path.insert(0, str(REPO_ROOT))

from fastapi import FastAPI, HTTPException  # noqa: E402

from runtime.agent import AgentRuntime  # noqa: E402
from runtime.keeper import SettleKeeper  # noqa: E402

AGENT_NAME = os.environ.get("AGENT_NAME", "demo")
POLL_INTERVAL = float(os.environ.get("POLL_INTERVAL", "4"))
RUN_KEEPER = os.environ.get("RUN_KEEPER", "1") == "1"

_state = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    runtime = AgentRuntime(AGENT_NAME)
    runtime.ensure_identity()
    _state["runtime"] = runtime
    t = threading.Thread(target=runtime.run_forever, kwargs={"interval": POLL_INTERVAL}, daemon=True)
    t.start()
    if RUN_KEEPER:
        keeper = SettleKeeper()
        threading.Thread(target=keeper.run_forever, kwargs={"interval": POLL_INTERVAL}, daemon=True).start()
    yield


app = FastAPI(title="MVP Plasma Agent Runtime", lifespan=lifespan)


@app.get("/health")
def health():
    return {"ok": True, "agent": AGENT_NAME}


@app.get("/status")
def status():
    rt = _state.get("runtime")
    if not rt:
        raise HTTPException(503, "runtime not ready")
    return {
        "agent": AGENT_NAME,
        "address": rt.address,
        "agentId": rt.adapter.agent_id_of(rt.address),
        "usdt": rt.adapter.usdt_balance(rt.address) / 1e6,
        "model_backend": rt.model.backend,
    }


@app.get("/job/{job_id}")
def job(job_id: int):
    rt = _state.get("runtime")
    if not rt:
        raise HTTPException(503, "runtime not ready")
    return rt.adapter.get_job(job_id)
