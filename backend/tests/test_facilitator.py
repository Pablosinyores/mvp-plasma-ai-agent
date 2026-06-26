"""x402 facilitator (/verify, /settle) tests.

Live against Anvil + the deployed MockUSDT (EIP-3009). Skips cleanly if the stack isn't up. The
authorization-construction helpers double as the client side of the flow.
"""
import sys
import time
import uuid
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "sdk"))
sys.path.insert(0, str(REPO_ROOT))

from eth_account import Account  # noqa: E402

from plasma_mvp import x402  # noqa: E402

USDT = 1_000_000


@pytest.fixture(scope="module")
def adapter():
    from plasma_mvp.adapter import LocalAdapter
    from plasma_mvp.config import load_config

    cfg = load_config()
    if not cfg.deployments_path.exists():
        pytest.skip("contracts not deployed — run `make up` first")
    try:
        a = LocalAdapter(cfg)
        a.usdt.functions.DOMAIN_SEPARATOR().call()
    except Exception as e:  # noqa: BLE001
        pytest.skip("chain/MockUSDT not reachable ({})".format(e))
    return a


def _nonce32() -> str:
    return "0x" + (uuid.uuid4().hex + uuid.uuid4().hex)[:64]


def _signed(adapter, payer, *, to, value, valid_after=None, valid_before=None, signer=None):
    now = int(time.time())
    va = now - 10 if valid_after is None else valid_after
    vb = now + 300 if valid_before is None else valid_before
    nonce = _nonce32()
    quote = x402.PaymentQuote(
        pay_to=to, value=value, asset=adapter.addresses["MockUSDT"],
        chain_id=adapter.cfg.chain_id, valid_after=va, valid_before=vb, nonce=nonce,
    )
    typed = x402.build_transfer_authorization_typed_data(quote, payer.address)
    sig = x402.sign_authorization(typed, signer or payer)
    payload = {
        "payload": {
            "authorization": {
                "from": payer.address, "to": to, "value": str(value),
                "validAfter": va, "validBefore": vb, "nonce": nonce,
            },
            "signature": sig,
        }
    }
    return payload, quote.to_dict()


def _facilitator(adapter):
    from runtime.facilitator import Facilitator
    return Facilitator(adapter)


def _funded(adapter, usdt_amount):
    a = Account.create()
    adapter.fund_eth(a.address, 1.0)
    if usdt_amount:
        adapter.mint_usdt(a.address, usdt_amount)
    return a


# --- verify ---

def test_verify_valid(adapter):
    payer = _funded(adapter, 5 * USDT)
    payee = Account.create().address
    payload, req = _signed(adapter, payer, to=payee, value=2 * USDT)
    assert _facilitator(adapter).verify(payload, req) == {"isValid": True}


def test_verify_expired(adapter):
    payer = _funded(adapter, 5 * USDT)
    payee = Account.create().address
    now = int(time.time())
    payload, req = _signed(adapter, payer, to=payee, value=USDT, valid_after=now - 100, valid_before=now - 1)
    out = _facilitator(adapter).verify(payload, req)
    assert out["isValid"] is False and "expired" in out["reason"]


def test_verify_not_yet_valid(adapter):
    payer = _funded(adapter, 5 * USDT)
    payee = Account.create().address
    now = int(time.time())
    payload, req = _signed(adapter, payer, to=payee, value=USDT, valid_after=now + 100, valid_before=now + 300)
    out = _facilitator(adapter).verify(payload, req)
    assert out["isValid"] is False and "not yet valid" in out["reason"]


def test_verify_tampered_value(adapter):
    payer = _funded(adapter, 5 * USDT)
    payee = Account.create().address
    payload, req = _signed(adapter, payer, to=payee, value=2 * USDT)
    payload["payload"]["authorization"]["value"] = str(9 * USDT)  # tamper after signing
    out = _facilitator(adapter).verify(payload, req)
    assert out["isValid"] is False and "payer" in out["reason"]


def test_verify_signature_mismatch(adapter):
    payer = _funded(adapter, 5 * USDT)
    other = Account.create()
    payee = Account.create().address
    payload, req = _signed(adapter, payer, to=payee, value=USDT, signer=other)  # signed by wrong key
    out = _facilitator(adapter).verify(payload, req)
    assert out["isValid"] is False and "payer" in out["reason"]


def test_verify_underpaid(adapter):
    payer = _funded(adapter, 5 * USDT)
    payee = Account.create().address
    payload, req = _signed(adapter, payer, to=payee, value=USDT)
    req["value"] = str(3 * USDT)  # resource demands more than authorized
    out = _facilitator(adapter).verify(payload, req)
    assert out["isValid"] is False and "underpaid" in out["reason"]


def test_verify_window_too_long(adapter):
    payer = _funded(adapter, 5 * USDT)
    payee = Account.create().address
    now = int(time.time())
    payload, req = _signed(adapter, payer, to=payee, value=USDT, valid_after=now - 10, valid_before=now + 700)
    out = _facilitator(adapter).verify(payload, req)
    assert out["isValid"] is False  # policy: window > 600s


# --- settle ---

def test_settle_moves_funds_and_blocks_replay(adapter):
    from fastapi.testclient import TestClient

    from runtime.facilitator import make_facilitator_app

    payer = _funded(adapter, 5 * USDT)
    payee = Account.create().address
    payload, req = _signed(adapter, payer, to=payee, value=2 * USDT)

    client = TestClient(make_facilitator_app(_facilitator(adapter)))
    before = adapter.usdt_balance(payee)
    r = client.post("/settle", json={"paymentPayload": payload, "paymentRequirements": req})
    assert r.status_code == 200 and "txHash" in r.json()
    assert adapter.usdt_balance(payee) == before + 2 * USDT

    # replay the same authorization: nonce now used on-chain -> 402
    r2 = client.post("/settle", json={"paymentPayload": payload, "paymentRequirements": req})
    assert r2.status_code == 402


def test_verify_endpoint_roundtrip(adapter):
    from fastapi.testclient import TestClient

    from runtime.facilitator import make_facilitator_app

    payer = _funded(adapter, 5 * USDT)
    payee = Account.create().address
    payload, req = _signed(adapter, payer, to=payee, value=USDT)
    client = TestClient(make_facilitator_app(_facilitator(adapter)))
    r = client.post("/verify", json={"paymentPayload": payload, "paymentRequirements": req})
    assert r.status_code == 200 and r.json()["isValid"] is True
