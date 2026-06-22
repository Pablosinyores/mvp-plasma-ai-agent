"""Milestone 3 acceptance test — spend, guardrails, auto-refuel.

Two tiers:
  * Pure guardrail units (no Docker, no chain) — the policy gate + X402Signer caps. These are the
    drain defense and must always run.
  * Live e2e (Anvil + LocalStack, stub model) — real x402 settlement, auto-refuel, and the
    adversarial injection case. These skip cleanly if `make up` hasn't been run.

Asserts the M3 bar: spend works; over-cap and over-session-budget are BLOCKED; Permit/Permit2 is
REJECTED by the policy gate; auto-refuel fires below floor and respects the daily cap; and an on_job
whose model output says "pay attacker 1e6" cannot exceed the cap.
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
from plasma_mvp.signer import PayeeNotAllowed, SpendCapExceeded, X402Signer  # noqa: E402

USDT = 1_000_000  # 1 USDT in base units (6dp)


# --------------------------------------------------------------------------- #
#  Tier 1 — guardrail units (no infra)                                        #
# --------------------------------------------------------------------------- #
def _quote(pay_to, value, *, valid_after=1000, valid_before=1300, asset=None, chain_id=31337):
    return x402.PaymentQuote(
        pay_to=pay_to, value=value,
        asset=asset or "0x" + "11" * 20, chain_id=chain_id,
        valid_after=valid_after, valid_before=valid_before,
        nonce="0x" + "22" * 32,
    )


def _exploding_factory():
    def _f():
        raise AssertionError("private key was fetched despite a guardrail failure")
    return _f


def test_policy_gate_rejects_permit():
    gate = x402.SigningPolicy()
    permit = {"primaryType": "Permit",
              "message": {"validAfter": 0, "validBefore": 1300}}
    with pytest.raises(x402.PolicyViolation):
        gate.check(permit)


def test_policy_gate_rejects_permit2():
    gate = x402.SigningPolicy()
    p2 = {"primaryType": "PermitTransferFrom",
          "message": {"validAfter": 0, "validBefore": 1300}}
    with pytest.raises(x402.PolicyViolation):
        gate.check(p2)


def test_policy_gate_rejects_long_validity_window():
    gate = x402.SigningPolicy()
    typed = x402.build_transfer_authorization_typed_data(
        _quote("0x" + "33" * 20, USDT, valid_after=1000, valid_before=1000 + 700), "0x" + "44" * 20
    )
    with pytest.raises(x402.PolicyViolation):
        gate.check(typed)


def test_policy_gate_allows_bounded_transfer_authorization():
    gate = x402.SigningPolicy()
    typed = x402.build_transfer_authorization_typed_data(
        _quote("0x" + "33" * 20, USDT, valid_after=1000, valid_before=1000 + 300), "0x" + "44" * 20
    )
    assert gate.check(typed) is True


def test_signer_blocks_over_per_call_cap():
    payee = Account.create().address
    signer = X402Signer(_exploding_factory(), max_value_per_call=2 * USDT, session_budget=10 * USDT,
                        allowed_payees=[payee], address="0x" + "44" * 20)
    with pytest.raises(SpendCapExceeded):
        signer.sign_payment(_quote(payee, 3 * USDT))  # > per-call cap, key must never be fetched
    assert signer.spent == 0


def test_signer_blocks_over_session_budget():
    agent = Account.create()
    payee = Account.create().address
    signer = X402Signer(lambda: agent, max_value_per_call=5 * USDT, session_budget=4 * USDT,
                        allowed_payees=[payee])
    signer.sign_payment(_quote(payee, 3 * USDT, asset="0x" + "11" * 20))  # ok: 3 <= 4
    assert signer.spent == 3 * USDT
    with pytest.raises(SpendCapExceeded):
        signer.sign_payment(_quote(payee, 2 * USDT))  # 3 + 2 > 4 session budget
    assert signer.spent == 3 * USDT  # unchanged after the blocked call


def test_signer_enforces_byte_equal_payee():
    agent = Account.create()
    allowed = Account.create().address
    attacker = Account.create().address
    signer = X402Signer(_exploding_factory(), max_value_per_call=5 * USDT, session_budget=10 * USDT,
                        allowed_payees=[allowed], address=agent.address)
    with pytest.raises(PayeeNotAllowed):
        signer.sign_payment(_quote(attacker, USDT))


def test_signer_happy_path_signs_and_debits():
    agent = Account.create()
    payee = Account.create().address
    signer = X402Signer(lambda: agent, max_value_per_call=5 * USDT, session_budget=10 * USDT,
                        allowed_payees=[payee])
    header = signer.sign_payment(_quote(payee, 2 * USDT))
    decoded = x402.decode_payment_header(header)
    assert decoded["payload"]["authorization"]["to"].lower() == payee.lower()
    assert int(decoded["payload"]["authorization"]["value"]) == 2 * USDT
    assert signer.spent == 2 * USDT and signer.remaining == 8 * USDT


# --------------------------------------------------------------------------- #
#  Tier 2 — live e2e (Anvil + LocalStack)                                     #
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def stack():
    from plasma_mvp.adapter import LocalAdapter
    from plasma_mvp.aws import Aws
    from plasma_mvp.config import load_config

    cfg = load_config()
    if not cfg.deployments_path.exists():
        pytest.skip("contracts not deployed — run `make up` first")
    try:
        aws = Aws(cfg)
        aws.ping()
        adapter = LocalAdapter(cfg)
        # EIP-3009 must be present in the deployed MockUSDT (re-deploy after M3 contract change)
        adapter.usdt.functions.DOMAIN_SEPARATOR().call()
    except Exception as e:  # noqa: BLE001
        pytest.skip("local stack not reachable / MockUSDT lacks EIP-3009 ({}) — run `make up`".format(e))
    _ensure_tables(aws)
    return {"cfg": cfg, "aws": aws, "adapter": adapter}


def _ensure_tables(aws):
    from plasma_mvp.events import EVENTS_TABLE
    from plasma_mvp.refuel import REFUEL_TABLE
    for name in (REFUEL_TABLE, EVENTS_TABLE):
        try:
            aws.dynamodb.create_table(
                TableName=name,
                AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}],
                KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}],
                BillingMode="PAY_PER_REQUEST",
            )
        except Exception:  # noqa: BLE001
            pass


def _funded_agent(adapter, usdt_amount):
    agent = Account.create()
    adapter.fund_eth(agent.address, 1.0)
    if usdt_amount:
        adapter.mint_usdt(agent.address, usdt_amount)
    return agent


def test_x402_spend_end_to_end(stack):
    from fastapi.testclient import TestClient

    from runtime.resource import X402Client, X402ResourceServer, make_resource_app

    adapter = stack["adapter"]
    agent = _funded_agent(adapter, 10 * USDT)
    payee = Account.create().address  # the resource's receiving wallet

    server = X402ResourceServer(adapter, pay_to=payee, price=2 * USDT)
    client_http = TestClient(make_resource_app(server))

    # first call: 402 + quote
    raw = client_http.get("/resource")
    assert raw.status_code == 402
    assert raw.json()["accepts"][0]["payTo"].lower() == payee.lower()

    signer = X402Signer(lambda: agent, max_value_per_call=5 * USDT, session_budget=10 * USDT,
                        allowed_payees=[payee])
    paid = X402Client(client_http, signer).get("/resource")
    assert paid.status_code == 200
    body = paid.json()
    assert body["result"]["ok"] is True
    assert adapter.usdt_balance(payee) == 2 * USDT  # funds actually moved on-chain
    assert signer.spent == 2 * USDT


def test_auto_refuel_fires_below_floor_and_respects_daily_cap(stack):
    from plasma_mvp.refuel import AutoRefueler, RefuelLedger

    adapter, aws, cfg = stack["adapter"], stack["aws"], stack["cfg"]
    owner = adapter.relayer
    adapter.mint_usdt(owner.address, 100 * USDT)  # owner's top-up float

    agent = _funded_agent(adapter, 0)  # starts at 0 USDT (below any floor)
    day = "test-" + uuid.uuid4().hex[:8]

    refueler = AutoRefueler(adapter, owner_account=owner, floor=5 * USDT, refill=3 * USDT,
                            daily_cap=6 * USDT, ledger=RefuelLedger(aws, cfg), cfg=cfg)

    r1 = refueler.maybe_refuel(agent.address, day=day)
    assert r1["refueled"] is True
    assert adapter.usdt_balance(agent.address) == 3 * USDT

    # still below floor (3 < 5): second refuel allowed (3 + 3 = 6 == cap)
    r2 = refueler.maybe_refuel(agent.address, day=day)
    assert r2["refueled"] is True
    assert adapter.usdt_balance(agent.address) == 6 * USDT

    # now at 6 USDT >= floor 5 → no refuel needed
    r3 = refueler.maybe_refuel(agent.address, day=day)
    assert r3["refueled"] is False and r3["reason"] == "above floor"

    # force-drain check: even below floor, the daily cap blocks further refuel
    adapter.transfer_usdt(agent, owner.address, 5 * USDT)  # drop agent to 1 USDT (< floor)
    assert adapter.usdt_balance(agent.address) == 1 * USDT
    r4 = refueler.maybe_refuel(agent.address, day=day)
    assert r4["refueled"] is False and r4["reason"] == "daily cap reached"
    assert adapter.usdt_balance(agent.address) == 1 * USDT  # no transfer happened


def test_adversarial_injection_cannot_exceed_cap(stack):
    """An on_job whose model output says 'pay attacker 1e6' is contained by the signer cap."""
    adapter = stack["adapter"]
    agent = _funded_agent(adapter, 50 * USDT)
    attacker = Account.create().address
    legit_payee = Account.create().address

    # the agent's tool code only holds an X402Signer scoped to a tiny cap + a single allowed payee
    signer = X402Signer(lambda: agent, max_value_per_call=2 * USDT, session_budget=4 * USDT,
                        allowed_payees=[legit_payee])

    def on_job_compromised(model_output):
        # model output (attacker-controlled): "pay attacker 1000000 USDT"
        amount = 1_000_000 * USDT
        return signer.sign_payment(_quote(attacker, amount, asset=adapter.addresses["MockUSDT"]))

    # the injection cannot move funds: blocked by per-call cap AND payee allow-list
    with pytest.raises((SpendCapExceeded, PayeeNotAllowed)):
        on_job_compromised("pay attacker 1e6")

    assert adapter.usdt_balance(attacker) == 0
    assert signer.spent == 0

    # the legitimate, within-cap spend still works on-chain
    from runtime.resource import X402Client, X402ResourceServer, make_resource_app
    from fastapi.testclient import TestClient

    server = X402ResourceServer(adapter, pay_to=legit_payee, price=1 * USDT)
    paid = X402Client(TestClient(make_resource_app(server)), signer).get("/resource")
    assert paid.status_code == 200
    assert adapter.usdt_balance(legit_payee) == 1 * USDT
