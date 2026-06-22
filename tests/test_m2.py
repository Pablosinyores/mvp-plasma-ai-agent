"""Milestone 2 end-to-end acceptance test — the earning loop (stub model, no Docker model needed).

Proves, against the real local stack (Anvil + LocalStack), with MODEL_BACKEND=stub:
  - a buyer escrows MockUSDT for a job,
  - the agent poll loop runs it through the model gateway, stores the result, submits on-chain,
  - the stored result's keccak == the on-chain resultHash,
  - after the dispute window the keeper settles and the agent's USDT balance increases,
  - the claimRefund path returns funds if the provider never delivers.

Requires `make up` (anvil + localstack) + contracts deployed. Skips cleanly otherwise.
"""
import json
import sys
import uuid
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from eth_utils import keccak  # noqa: E402

from plasma_mvp.adapter import LocalAdapter  # noqa: E402
from plasma_mvp.aws import Aws  # noqa: E402
from plasma_mvp.config import load_config  # noqa: E402
from plasma_mvp.keyvault import KeyVault  # noqa: E402
from plasma_mvp.storage import Storage  # noqa: E402
from runtime.agent import AgentRuntime  # noqa: E402
from runtime.keeper import SettleKeeper  # noqa: E402


@pytest.fixture(scope="module")
def stack(monkeypatch_module):
    cfg = load_config()
    if not cfg.deployments_path.exists():
        pytest.skip("contracts not deployed — run `make up` first")
    try:
        aws = Aws(cfg)
        aws.ping()
        adapter = LocalAdapter(cfg)
        assert "Commerce" in adapter.addresses
    except Exception as e:  # noqa: BLE001
        pytest.skip("local stack not reachable / Commerce missing ({}) — run `make up`".format(e))
    return {"cfg": cfg, "aws": aws, "adapter": adapter,
            "kv": KeyVault(aws, cfg), "storage": Storage(aws, cfg)}


@pytest.fixture(scope="module")
def monkeypatch_module():
    # force the deterministic stub backend for the whole module
    import os
    os.environ["MODEL_BACKEND"] = "stub"
    yield


def _new_agent(stack):
    name = "m2-" + uuid.uuid4().hex[:8]
    kv, storage, adapter = stack["kv"], stack["storage"], stack["adapter"]
    storage.ensure_bucket()
    addr = kv.new_agent_key(name)
    adapter.fund_eth(addr, 1.0)
    rt = AgentRuntime(name, cfg=stack["cfg"])
    rt.ensure_identity()
    return name, addr, rt


def _mine_past_window(adapter, extra=2):
    # fast-forward Anvil time so settle() passes without a real sleep
    adapter.w3.provider.make_request("evm_increaseTime", [adapter.dispute_window + extra])
    adapter.w3.provider.make_request("evm_mine", [])


def test_earning_loop_settles_and_pays_agent(stack):
    adapter, storage = stack["adapter"], stack["storage"]
    name, addr, rt = _new_agent(stack)
    buyer = adapter.relayer

    budget = 5_000_000  # 5 USDT
    adapter.mint_usdt(buyer.address, budget)
    before = adapter.usdt_balance(addr)

    # buyer creates + funds the job; prompt stored content-addressed (descHash == S3 key)
    req_uri = storage.put(json.dumps({"prompt": "Summarize: hello world"}, sort_keys=True).encode())
    desc_hash = bytes.fromhex(storage.hash_of(req_uri))
    now = adapter.w3.eth.get_block("latest")["timestamp"]
    job_id = adapter.create_job(buyer, addr, desc_hash, now + 3600)
    adapter.fund_job(buyer, job_id, budget)
    assert adapter.get_job(job_id)["status"] == "FUNDED"

    # agent runs one poll iteration -> SUBMITTED
    done = rt.process_funded_once()
    assert job_id in done
    job = adapter.get_job(job_id)
    assert job["status"] == "SUBMITTED"

    # the stored result matches the on-chain resultHash (integrity)
    result_bytes = storage.get(job["uri"])
    assert keccak(result_bytes) == bytes(job["resultHash"])
    assert result_bytes.startswith(b"[stub-summary]")  # came through the (stub) model gateway

    # keeper settles after the dispute window -> agent paid
    _mine_past_window(adapter)
    settled = SettleKeeper(stack["cfg"]).settle_due_once()
    assert job_id in settled
    assert adapter.get_job(job_id)["status"] == "COMPLETED"
    assert adapter.usdt_balance(addr) == before + budget


def test_claim_refund_when_provider_never_delivers(stack):
    adapter, storage = stack["adapter"], stack["storage"]
    name, addr, rt = _new_agent(stack)
    buyer = adapter.relayer

    budget = 3_000_000
    adapter.mint_usdt(buyer.address, budget)
    buyer_before = adapter.usdt_balance(buyer.address)

    req_uri = storage.put(json.dumps({"prompt": "x"}, sort_keys=True).encode())
    desc_hash = bytes.fromhex(storage.hash_of(req_uri))
    now = adapter.w3.eth.get_block("latest")["timestamp"]
    job_id = adapter.create_job(buyer, addr, desc_hash, now + 5)  # short deadline, no submission
    adapter.fund_job(buyer, job_id, budget)

    # let the deadline pass without the agent submitting
    adapter.w3.provider.make_request("evm_increaseTime", [10])
    adapter.w3.provider.make_request("evm_mine", [])
    adapter.claim_refund(buyer, job_id)

    assert adapter.get_job(job_id)["status"] == "EXPIRED"
    assert adapter.usdt_balance(buyer.address) == buyer_before  # net zero (minted then refunded)
