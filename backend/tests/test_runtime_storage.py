"""Runtime ↔ storage wiring: the agent runtime resolves its content store from STORAGE_BACKEND, and
the example agent's on_job runs the model over a prompt fetched from that store.

No chain/AWS needed: the local backend is pure filesystem and the stub model is deterministic.
"""
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "sdk"))
sys.path.insert(0, str(REPO_ROOT))

from plasma_mvp import storage as st  # noqa: E402
from plasma_mvp.storage import content_key  # noqa: E402


def test_local_backend_put_get_aliases(tmp_path):
    prov = st.LocalStorageProvider(str(tmp_path))
    uri = prov.put(b"alias-check")  # legacy-style call site
    assert prov.get(uri) == b"alias-check"


def test_example_on_job_runs_model_over_stored_prompt(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("STORAGE_LOCAL_PATH", str(tmp_path))

    from model.gateway import ModelGateway
    from examples.market_intel_agent import build_on_job

    # a runtime stand-in carrying only what on_job touches (storage + model) — no chain/KMS
    class _RT:
        def __init__(self):
            self.storage = st.get_storage(backend="local")
            self.model = ModelGateway(backend="stub")  # deterministic, no model server

    rt = _RT()
    prompt = {"prompt": "BTC liquidity trends"}
    raw = json.dumps(prompt, sort_keys=True).encode("utf-8")
    rt.storage.put(raw)
    desc_hash = "0x" + content_key(raw)  # descHash == content key of the request

    on_job = build_on_job(rt)
    out = on_job({"descHash": desc_hash})
    assert isinstance(out, bytes) and len(out) > 0
