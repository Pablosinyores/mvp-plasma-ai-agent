"""studio_worker — run the whole studio: service every registered agent + settle, in one loop.

A live "the marketplace is open" process for demos. Every tick it:
  - discovers all agents (DynamoDB),
  - for each, runs any FUNDED jobs through the local model and submits the result,
  - settles any SUBMITTED job whose dispute window has elapsed (permissionless keeper).

Honors MODEL_BACKEND (set MODEL_BACKEND=llamacpp to use the real llama.cpp container). Prints each
submit/settle so the terminal narrates itself next to the dashboard.

  python3 backend/studio_worker.py            # 2s tick
  MODEL_BACKEND=llamacpp python3 backend/studio_worker.py
"""
import os
import sys
import time
from pathlib import Path

# this file now lives at backend/studio_worker.py, so backend/ is its own parent
BACKEND_ROOT = Path(__file__).resolve().parents[0]
sys.path.insert(0, str(BACKEND_ROOT / "sdk"))
sys.path.insert(0, str(BACKEND_ROOT))

from plasma_mvp.registry import Registry  # noqa: E402
from runtime.agent import AgentRuntime  # noqa: E402
from runtime.keeper import SettleKeeper  # noqa: E402


def main(interval: float = 2.0):
    backend = os.environ.get("MODEL_BACKEND", "stub")
    print("[studio] worker up — model backend: {} — tick {}s".format(backend, interval), flush=True)
    keeper = SettleKeeper()
    runtimes = {}
    while True:
        try:
            for a in Registry().list_agents():
                name = a["name"]
                if name not in runtimes:
                    try:
                        runtimes[name] = AgentRuntime(name)
                    except Exception as e:  # noqa: BLE001
                        print("[studio] skip {} ({})".format(name, e), flush=True)
                        continue
                try:
                    done = runtimes[name].process_funded_once()
                    if done:
                        print("[{}] submitted jobs {}".format(name, done), flush=True)
                except Exception as e:  # noqa: BLE001
                    print("[{}] loop error: {}".format(name, e), flush=True)
            settled = keeper.settle_due_once()
            if settled:
                print("[keeper] settled jobs {}".format(settled), flush=True)
        except Exception as e:  # noqa: BLE001
            print("[studio] tick error: {}".format(e), flush=True)
        time.sleep(interval)


if __name__ == "__main__":
    main()
