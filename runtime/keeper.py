"""SettleKeeper — permissionless settlement bot.

Finds SUBMITTED jobs whose dispute window has elapsed and calls `settle(jobId)`, releasing escrow to
the provider. Runs the relayer account (settle is permissionless; the caller only pays gas).
"""
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "sdk"))

from plasma_mvp.adapter import LocalAdapter  # noqa: E402
from plasma_mvp.config import load_config  # noqa: E402


class SettleKeeper:
    def __init__(self, cfg=None):
        self.cfg = cfg or load_config()
        self.adapter = LocalAdapter(self.cfg)

    def settle_due_once(self) -> list:
        """Settle every job past its dispute window. Returns settled jobIds."""
        settled = []
        for job in self.adapter.settleable_jobs():
            self.adapter.settle(self.adapter.relayer, job["jobId"])
            settled.append(job["jobId"])
        return settled

    def run_forever(self, interval: float = 4.0):
        while True:
            try:
                done = self.settle_due_once()
                if done:
                    print("[keeper] settled jobs: {}".format(done), flush=True)
            except Exception as e:  # noqa: BLE001
                print("[keeper] error: {}".format(e), flush=True)
            time.sleep(interval)
