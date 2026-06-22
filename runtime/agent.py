"""AgentRuntime — the per-agent earning loop.

Watches for FUNDED jobs assigned to this agent, runs each through the local model, stores the result,
and submits the result hash + URI on-chain. The job's prompt is fetched from S3 using the on-chain
`descHash` directly: storage is content-addressed by keccak, so descHash IS the S3 key of the request.
"""
import json
import sys
import time
from pathlib import Path

from eth_utils import keccak

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "sdk"))

from plasma_mvp.adapter import LocalAdapter  # noqa: E402
from plasma_mvp.aws import Aws  # noqa: E402
from plasma_mvp.config import load_config  # noqa: E402
from plasma_mvp.keyvault import KeyVault  # noqa: E402
from plasma_mvp.storage import Storage  # noqa: E402

sys.path.insert(0, str(REPO_ROOT))
from model.gateway import ModelGateway  # noqa: E402

AGENT_DIR = REPO_ROOT / ".agent"


def _hexkey(value) -> str:
    """Normalize a bytes32/HexBytes to the 64-char lowercase hex key used by storage (no 0x)."""
    h = value.hex() if hasattr(value, "hex") else str(value)
    return h[2:].lower() if h.startswith("0x") else h.lower()


class AgentRuntime:
    def __init__(self, name, cfg=None, on_job=None, model=None):
        self.name = name
        self.cfg = cfg or load_config()
        self.aws = Aws(self.cfg)
        self.kv = KeyVault(self.aws, self.cfg)
        self.storage = Storage(self.aws, self.cfg)
        self.adapter = LocalAdapter(self.cfg)
        self.model = model or ModelGateway()
        self.account = self.kv.signer_for(name)  # in-memory key only
        self.on_job = on_job or self.default_on_job

    @property
    def address(self) -> str:
        return self.account.address

    def ensure_identity(self) -> int:
        """Idempotent: register an identity if this address doesn't have one yet."""
        existing = self.adapter.agent_id_of(self.address)
        if existing:
            return existing
        card = {"name": self.name, "address": self.address, "skills": []}
        card_uri = self.storage.put(json.dumps(card, sort_keys=True).encode("utf-8"))
        agent_id, _ = self.adapter.register(self.account, card_uri)
        return agent_id

    def default_on_job(self, job: dict) -> bytes:
        """Fetch the request prompt (by descHash), run the model, return result bytes."""
        prompt = ""
        try:
            req = json.loads(self.storage.get(_hexkey(job["descHash"])))
            prompt = req.get("prompt", "")
        except Exception:  # noqa: BLE001
            prompt = ""
        text = self.model.complete(prompt or "Summarize: (no prompt provided).")
        return text.encode("utf-8")

    def process_funded_once(self) -> list:
        """Run one poll iteration. Returns the list of jobIds processed (submitted)."""
        now = self.adapter.w3.eth.get_block("latest")["timestamp"]
        done = []
        for job in self.adapter.poll_funded_jobs(self.address):
            if job["expiresAt"] and now > job["expiresAt"]:
                continue  # past deadline — let the client reclaim
            result = self.on_job(job)
            uri = self.storage.put(result)
            self.adapter.submit_result(self.account, job["jobId"], keccak(result), uri)
            done.append(job["jobId"])
        return done

    def run_forever(self, interval: float = 4.0):
        self.ensure_identity()
        while True:
            try:
                processed = self.process_funded_once()
                if processed:
                    print("[{}] submitted jobs: {}".format(self.name, processed), flush=True)
            except Exception as e:  # noqa: BLE001
                print("[{}] loop error: {}".format(self.name, e), flush=True)
            time.sleep(interval)
