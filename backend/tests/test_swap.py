"""SwapGuard e2e + policy tests against the local MiniAMM venue.

Requires a running anvil with the swap venue deployed (Deploy.s.sol writes USDC/WETH/MiniAMM to the
manifest). No LocalStack needed — the swap path touches only the chain.
"""
import sys
from pathlib import Path

import pytest
from eth_account import Account

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sdk"))
from plasma_mvp.adapter import LocalAdapter  # noqa: E402
from plasma_mvp.swap import SwapBlocked, SwapGuard  # noqa: E402

USDC = 1_000_000  # 1 USDC in base units (6dp)


@pytest.fixture(scope="module")
def adapter():
    a = LocalAdapter()
    if a.amm is None:
        pytest.skip("deployment has no swap venue (USDC/WETH/MiniAMM)")
    return a


def _fresh_agent(adapter, usdc_amount):
    acct = Account.create()
    adapter.fund_eth(acct.address, 0.05)  # gas float
    if usdc_amount:
        adapter.mint_usdc(acct.address, usdc_amount)
    return acct


def test_guarded_swap_buys_weth_into_own_wallet(adapter):
    agent = _fresh_agent(adapter, 5_000 * USDC)
    guard = SwapGuard(adapter, agent, max_usdc_per_swap=2_000 * USDC, session_usdc=4_000 * USDC)

    weth_before = adapter.weth_balance(agent.address)
    res = guard.buy_weth(2_000 * USDC)

    assert res["amountUsdc"] == 2_000 * USDC
    assert res["wethBalance"] > weth_before          # WETH landed in the agent's OWN wallet
    assert res["wethBalance"] >= res["minOut"]       # honored the slippage floor
    assert adapter.usdc_balance(agent.address) == 3_000 * USDC  # spent exactly the input
    assert res["remaining"] == 2_000 * USDC          # session budget debited


def test_per_swap_cap_blocks(adapter):
    agent = _fresh_agent(adapter, 10_000 * USDC)
    guard = SwapGuard(adapter, agent, max_usdc_per_swap=1_000 * USDC, session_usdc=10_000 * USDC)
    with pytest.raises(SwapBlocked, match="per-swap cap"):
        guard.buy_weth(2_000 * USDC)


def test_session_budget_blocks_second_swap(adapter):
    agent = _fresh_agent(adapter, 5_000 * USDC)
    guard = SwapGuard(adapter, agent, max_usdc_per_swap=2_000 * USDC, session_usdc=2_000 * USDC)
    guard.buy_weth(2_000 * USDC)  # uses the whole session budget
    with pytest.raises(SwapBlocked, match="session budget"):
        guard.buy_weth(1 * USDC)


def test_insufficient_usdc_blocks(adapter):
    agent = _fresh_agent(adapter, 100 * USDC)
    guard = SwapGuard(adapter, agent, max_usdc_per_swap=2_000 * USDC, session_usdc=4_000 * USDC)
    with pytest.raises(SwapBlocked, match="insufficient USDC"):
        guard.buy_weth(500 * USDC)


def test_recipient_is_pinned_to_self(adapter):
    # The guard/adapter never expose a recipient param — output can only go to the swapper.
    agent = _fresh_agent(adapter, 2_000 * USDC)
    other = Account.create().address
    guard = SwapGuard(adapter, agent, max_usdc_per_swap=2_000 * USDC, session_usdc=2_000 * USDC)
    before_other = adapter.weth_balance(other)
    guard.buy_weth(1_000 * USDC)
    assert adapter.weth_balance(other) == before_other  # nothing reached any other address
    assert adapter.weth_balance(agent.address) > 0
