"""TradeGuard + multi-pair venue tests against the local pools. Needs anvil with the venue deployed."""
import sys
from pathlib import Path

import pytest
from eth_account import Account

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sdk"))
from plasma_mvp.adapter import LocalAdapter  # noqa: E402
from plasma_mvp.trade import TradeBlocked, TradeGuard  # noqa: E402

USDC = 1_000_000


@pytest.fixture(scope="module")
def adapter():
    a = LocalAdapter()
    if not a.pools:
        pytest.skip("deployment has no swap venue")
    return a


def _agent(adapter, usdc_amount=0):
    acct = Account.create()
    adapter.fund_eth(acct.address, 0.05)
    if usdc_amount:
        adapter.mint_token("USDC", acct.address, usdc_amount)
    return acct


def test_registry_has_three_pools(adapter):
    assert adapter.find_pool("USDC", "WETH") is not None
    assert adapter.find_pool("USDC", "WXPL") is not None
    assert adapter.find_pool("WETH", "WXPL") is not None
    assert adapter.find_pool("USDC", "WETH") is adapter.find_pool("WETH", "USDC")  # order-agnostic


def test_buy_wxpl_with_usdc(adapter):
    agent = _agent(adapter, 5_000 * USDC)
    guard = TradeGuard(adapter, agent, max_notional_usdc=2_000 * USDC, session_notional_usdc=4_000 * USDC)
    res = guard.trade("USDC", "WXPL", 1_000 * USDC)
    assert res["buy"] == "WXPL"
    assert adapter.token_balance("WXPL", agent.address) >= res["minOut"] > 0
    assert adapter.token_balance("USDC", agent.address) == 4_000 * USDC


def test_buy_weth_with_usdc(adapter):
    agent = _agent(adapter, 5_000 * USDC)
    guard = TradeGuard(adapter, agent, max_notional_usdc=3_000 * USDC, session_notional_usdc=5_000 * USDC)
    res = guard.trade("USDC", "WETH", 2_000 * USDC)
    assert adapter.token_balance("WETH", agent.address) >= res["minOut"] > 0


def test_non_allowlisted_token_blocked(adapter):
    agent = _agent(adapter, 1_000 * USDC)
    guard = TradeGuard(adapter, agent, allowed_tokens=("USDC", "WETH"))  # WXPL not allowed
    with pytest.raises(TradeBlocked, match="not allow-listed"):
        guard.trade("USDC", "WXPL", 100 * USDC)


def test_per_trade_cap_blocked(adapter):
    agent = _agent(adapter, 10_000 * USDC)
    guard = TradeGuard(adapter, agent, max_notional_usdc=500 * USDC, session_notional_usdc=10_000 * USDC)
    with pytest.raises(TradeBlocked, match="per-trade cap"):
        guard.trade("USDC", "WXPL", 2_000 * USDC)


def test_session_cap_blocks_second(adapter):
    agent = _agent(adapter, 5_000 * USDC)
    guard = TradeGuard(adapter, agent, max_notional_usdc=2_000 * USDC, session_notional_usdc=2_000 * USDC)
    guard.trade("USDC", "WXPL", 2_000 * USDC)
    with pytest.raises(TradeBlocked, match="session budget"):
        guard.trade("USDC", "WXPL", 1 * USDC)


def test_recipient_pinned(adapter):
    agent = _agent(adapter, 2_000 * USDC)
    other = Account.create().address
    guard = TradeGuard(adapter, agent)
    before = adapter.token_balance("WXPL", other)
    guard.trade("USDC", "WXPL", 1_000 * USDC)
    assert adapter.token_balance("WXPL", other) == before  # output never reached another address
