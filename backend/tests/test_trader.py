"""Trader strategy-loop tests against the live multi-pair venue (DCA cadence + rebalance convergence)."""
import sys
from pathlib import Path

import pytest
from eth_account import Account

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sdk"))
from plasma_mvp.adapter import LocalAdapter  # noqa: E402
from plasma_mvp.trade import TradeGuard  # noqa: E402
from plasma_mvp.trader import Trader  # noqa: E402

USDC = 1_000_000


@pytest.fixture(scope="module")
def adapter():
    a = LocalAdapter()
    if not a.pools:
        pytest.skip("deployment has no swap venue")
    return a


def _trader(adapter, usdc_amount, cap=3000, session=20000):
    acct = Account.create()
    adapter.fund_eth(acct.address, 0.05)
    adapter.mint_token("USDC", acct.address, usdc_amount * USDC)
    guard = TradeGuard(adapter, acct,
                       max_notional_usdc=cap * USDC, session_notional_usdc=session * USDC)
    return Trader(adapter, guard), acct


def test_dca_fires_on_cadence(adapter):
    trader, acct = _trader(adapter, 5_000)
    trader.set_strategy({"op": "dca", "sell": "USDC", "buy": "WXPL", "amount": 500, "everyTicks": 2})
    results = trader.run(4)
    actions = [r["action"] for r in results]
    # ticks 2 and 4 trade; 1 and 3 hold
    assert actions == ["hold", "trade", "hold", "trade"]
    assert adapter.token_balance("USDC", acct.address) == 4_000 * USDC  # 2 buys * 500


def test_dca_buys_wxpl(adapter):
    trader, acct = _trader(adapter, 3_000)
    trader.set_strategy({"op": "dca", "sell": "USDC", "buy": "WXPL", "amount": 1_000, "everyTicks": 1})
    trader.run(2)
    assert adapter.token_balance("WXPL", acct.address) > 0
    assert adapter.token_balance("USDC", acct.address) == 1_000 * USDC


def test_rebalance_moves_toward_target_then_holds(adapter):
    trader, acct = _trader(adapter, 5_000)
    # start all-USDC, target 40% USDC -> first tick must sell USDC into WXPL
    trader.set_strategy({"op": "rebalance", "base": "USDC", "quote": "WXPL", "targetBps": 4000})
    r1 = trader.tick()
    assert r1["action"] == "trade"
    # after converging, a later tick holds within band
    trader.run(3)
    usdc_val = adapter.token_balance("USDC", acct.address)
    wxpl_val = adapter.quote_trade("WXPL", "USDC", adapter.token_balance("WXPL", acct.address))
    total = usdc_val + wxpl_val
    assert 0.33 <= usdc_val / total <= 0.47  # landed near the 40% target


def test_swap_is_one_off(adapter):
    trader, acct = _trader(adapter, 2_000)
    trader.set_strategy({"op": "swap", "sell": "USDC", "buy": "WETH", "amount": 1_000})
    r1 = trader.tick()
    r2 = trader.tick()
    assert r1["action"] == "trade" and r2["action"] == "hold"


def test_limit_fires_when_predicate_true(adapter):
    trader, acct = _trader(adapter, 2_000)
    spot = adapter.spot_price("WXPL")
    # price < 2*spot is trivially true -> the conditional order fires this tick
    trader.set_strategy({"op": "limit", "sell": "USDC", "buy": "WXPL", "amount": 500,
                         "when": {"sym": "WXPL", "cmp": "lt", "price": spot * 2}})
    r = trader.tick()
    assert r["action"] == "trade"
    assert adapter.token_balance("WXPL", acct.address) > 0
    assert adapter.token_balance("USDC", acct.address) == 1_500 * USDC


def test_limit_holds_when_predicate_false(adapter):
    trader, acct = _trader(adapter, 2_000)
    spot = adapter.spot_price("WXPL")
    # price < spot/2 is false -> no trade, funds untouched, current price reported
    trader.set_strategy({"op": "limit", "sell": "USDC", "buy": "WXPL", "amount": 500,
                         "when": {"sym": "WXPL", "cmp": "lt", "price": spot / 2}})
    r = trader.tick()
    assert r["action"] == "hold" and r["reason"] == "limit not triggered"
    assert r["price"] > 0
    assert adapter.token_balance("WXPL", acct.address) == 0
    assert adapter.token_balance("USDC", acct.address) == 2_000 * USDC


def test_limit_fires_once_then_holds(adapter):
    trader, acct = _trader(adapter, 2_000)
    spot = adapter.spot_price("WXPL")
    trader.set_strategy({"op": "limit", "sell": "USDC", "buy": "WXPL", "amount": 500,
                         "when": {"sym": "WXPL", "cmp": "lt", "price": spot * 2}})
    r1 = trader.tick()
    r2 = trader.tick()
    assert r1["action"] == "trade" and r2["action"] == "hold"
    assert r2["reason"] == "limit order already filled"
    assert adapter.token_balance("USDC", acct.address) == 1_500 * USDC  # exactly one 500 buy


def test_limit_gt_branch_triggers(adapter):
    trader, acct = _trader(adapter, 2_000)
    spot = adapter.spot_price("WXPL")
    # price > spot/2 is true -> a "fire above" order also fills
    trader.set_strategy({"op": "limit", "sell": "USDC", "buy": "WXPL", "amount": 500,
                         "when": {"sym": "WXPL", "cmp": "gt", "price": spot / 2}})
    r = trader.tick()
    assert r["action"] == "trade" and r["price"] >= spot / 2


def test_set_strategy_retasks_live(adapter):
    trader, acct = _trader(adapter, 5_000)
    trader.set_strategy({"op": "dca", "sell": "USDC", "buy": "WXPL", "amount": 500, "everyTicks": 1})
    trader.tick()
    # re-task to a different pair mid-run
    trader.set_strategy({"op": "dca", "sell": "USDC", "buy": "WETH", "amount": 500, "everyTicks": 1})
    trader.tick()
    assert adapter.token_balance("WXPL", acct.address) > 0
    assert adapter.token_balance("WETH", acct.address) > 0
