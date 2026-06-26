"""Example agent: a market-intel provider wired through the model gateway, pluggable storage, and
the funded-job runtime loop.

Run the earning loop directly:
    STORAGE_BACKEND=local python -m examples.market_intel_agent

Or serve it over HTTP (health/status/job endpoints) via the runtime app:
    AGENT_NAME=market-intel uvicorn runtime.app:app --port 8546
"""
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "sdk"))
sys.path.insert(0, str(REPO_ROOT))

from runtime.agent import AgentRuntime, _hexkey  # noqa: E402


def build_on_job(runtime: AgentRuntime):
    """Produce a concise market-intel report for the funded job's prompt, via the LLM gateway."""

    def on_job(job: dict) -> bytes:
        prompt = ""
        try:
            req = json.loads(runtime.storage.get(_hexkey(job["descHash"])))
            prompt = req.get("prompt", "")
        except Exception:  # noqa: BLE001
            prompt = ""
        ask = "Produce a concise market-intel report for: {}".format(prompt or "(unspecified topic)")
        return runtime.model.complete(ask).encode("utf-8")

    return on_job


def main():
    rt = AgentRuntime(name="market-intel")
    rt.on_job = build_on_job(rt)
    rt.run_forever(interval=4.0)


if __name__ == "__main__":
    main()
