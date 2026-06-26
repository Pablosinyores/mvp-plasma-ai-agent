"""SessionManager tests — the EIP-7702 user-funded control plane behind the strategy panel.

Drives the real on-chain rail: authorize() returns the wallet payloads, the test then SIMULATES the
wallet (delegate + installSession on-chain) and asserts the standing-strategy loop fills trades FROM
the user's own address, tracks the on-chain session cap, and revokes cleanly.
"""
import sys
from pathlib import Path

import pytest
from eth_account import Account

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "sdk"))
sys.path.insert(0, str(BACKEND))
from plasma_mvp.adapter import LocalAdapter  # noqa: E402
from plasma_mvp.strategy_store import FileStrategyStore  # noqa: E402
from plasma_mvp import session as S  # noqa: E402
from studio_api.session_ctl import SessionManager  # noqa: E402

USER_PK = "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d"   # anvil #1
ONE_DAY = 86400


@pytest.fixture()
def env(tmp_path):
    a = LocalAdapter()
    if a.session_delegate_abi is None or a.find_pool("USDC", "WXPL") is None:
        pytest.skip("7702 delegate / pool not deployed")
    user = a.w3.eth.account.from_key(USER_PK)
    keeper = a.relayer
    mgr = SessionManager(a, FileStrategyStore(path=tmp_path / "s.json"), keeper=keeper)
    return a, user, keeper, mgr


def _wallet_delegate_and_install(a, user, keeper, auth_resp):
    """Stand in for the browser wallet: apply the EIP-7702 delegation, then submit installSession with
    exactly the policy the backend returned."""
    S.delegate_eoa(a, user, auth_resp["delegate"], sponsor_account=keeper)
    a.fund_eth(user.address, 5)
    a.mint_token("USDC", user.address, 2_000_000_000)
    # rebuild the policy tuple from the returned payload (the wallet would encode the same)
    p = auth_resp["install"]["policy"]
    pol = (True, int(p["expiry"]), p["fundingToken"], int(p["maxInPerTrade"]),
           int(p["sessionInCap"]), 0, int(p["maxSlippageBps"]))
    S.install_session(a, user, auth_resp["sessionKey"], pol,
                      buys=auth_resp["install"]["buys"], pools=auth_resp["install"]["pools"])


def test_authorize_returns_payloads(env):
    a, user, keeper, mgr = env
    resp = mgr.authorize(user.address, max_in_per_trade=100_000_000, session_in_cap=250_000_000)
    assert resp["delegate"] == a.session_delegate_address
    assert resp["authorization"]["address"] == a.session_delegate_address
    assert resp["install"]["policy"]["fundingToken"] == a.tokens["USDC"].address
    assert resp["install"]["buys"] == [a.tokens["WXPL"].address]
    assert resp["install"]["pools"] == [a.find_pool("USDC", "WXPL").address]
    assert mgr.get(user.address)["authorized"] is True


def test_full_user_funded_dca_flow(env):
    a, user, keeper, mgr = env
    resp = mgr.authorize(user.address, max_in_per_trade=100_000_000, session_in_cap=250_000_000)
    _wallet_delegate_and_install(a, user, keeper, resp)
    mgr.mark_installed(user.address)

    order = mgr.set_strategy(user.address, "DCA buy 40 USDC of XPL every 1 tick")
    assert order["op"] == "dca"

    u0 = a.token_balance("USDC", user.address)
    w0 = a.token_balance("WXPL", user.address)
    r = mgr.tick_active()
    tick = r[user.address]
    assert tick["action"] == "trade", tick
    assert tick["from"] == user.address, "fill executed from the user's own EOA"

    assert a.token_balance("USDC", user.address) == u0 - 40_000_000
    assert a.token_balance("WXPL", user.address) > w0
    g = mgr.get(user.address)
    assert g["rail"] == "session-7702"
    assert g["policy"]["spentIn"] == 40_000_000
    assert g["ticks"][-1]["action"] == "trade"


def test_revoke_payload_stops_loop(env):
    a, user, keeper, mgr = env
    resp = mgr.authorize(user.address, max_in_per_trade=100_000_000, session_in_cap=250_000_000)
    _wallet_delegate_and_install(a, user, keeper, resp)
    mgr.mark_installed(user.address)
    mgr.set_order(user.address, {"op": "dca", "sell": "USDC", "buy": "WXPL",
                                 "amount": 10, "everyTicks": 1})
    assert mgr.tick_active()[user.address]["action"] == "trade"

    payload = mgr.revoke_payload(user.address)
    assert payload["function"] == "revokeSession"
    assert payload["sessionKey"] == resp["sessionKey"]
    assert mgr.get(user.address)["strategy"] is None      # local loop stopped
    assert mgr.tick_active() == {}                         # nothing ticks
