"""Persistence tests — a standing strategy + its one-off fire-state survive a Trader restart.

The file-store unit test needs no chain; the restart tests use the live multi-pair venue.
"""
import sys
from pathlib import Path

import pytest
from eth_account import Account

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sdk"))
from plasma_mvp.adapter import LocalAdapter  # noqa: E402
from plasma_mvp.strategy_store import FileStrategyStore  # noqa: E402
from plasma_mvp.trade import TradeGuard  # noqa: E402
from plasma_mvp.trader import Trader  # noqa: E402

USDC = 1_000_000


def test_file_store_save_load_delete(tmp_path):
    store = FileStrategyStore(path=tmp_path / "s.json")
    addr = "0xAbC0000000000000000000000000000000000001"
    assert store.load(addr) is None
    rec = {"strategy": {"op": "swap"}, "prompt": "p", "tickCount": 3, "swapDone": True}
    store.save(addr, rec)
    assert store.load(addr.lower()) == rec          # address lookup is case-insensitive
    store.delete(addr)
    assert store.load(addr) is None


@pytest.fixture(scope="module")
def adapter():
    a = LocalAdapter()
    if not a.pools:
        pytest.skip("deployment has no swap venue")
    return a


def _funded_account(adapter, usdc_amount=2_000):
    acct = Account.create()
    adapter.fund_eth(acct.address, 0.05)
    adapter.mint_token("USDC", acct.address, usdc_amount * USDC)
    return acct


def _guard(adapter, acct, cap=3000, session=20000):
    return TradeGuard(adapter, acct, max_notional_usdc=cap * USDC, session_notional_usdc=session * USDC)


def test_strategy_and_fire_state_survive_restart(adapter, tmp_path):
    store = FileStrategyStore(path=tmp_path / "s.json")
    acct = _funded_account(adapter)
    t1 = Trader(adapter, _guard(adapter, acct), store=store)
    t1.set_strategy({"op": "swap", "sell": "USDC", "buy": "WXPL", "amount": 500}, prompt="buy xpl once")
    assert t1.tick()["action"] == "trade"
    usdc_after_fire = adapter.token_balance("USDC", acct.address)

    # simulate a process restart: a brand-new Trader for the same agent, same durable store
    t2 = Trader(adapter, _guard(adapter, acct), store=store)
    assert t2.strategy == {"op": "swap", "sell": "USDC", "buy": "WXPL", "amount": 500}
    assert t2.prompt == "buy xpl once"
    assert t2.tick_count == 1
    assert t2._swap_done is True
    # the one-off already fired -> it must NOT fire again
    assert t2.tick()["action"] == "hold"
    assert adapter.token_balance("USDC", acct.address) == usdc_after_fire


def test_triggered_limit_not_refired_after_restart(adapter, tmp_path):
    store = FileStrategyStore(path=tmp_path / "s.json")
    acct = _funded_account(adapter)
    spot = adapter.spot_price("WXPL")
    t1 = Trader(adapter, _guard(adapter, acct), store=store)
    t1.set_strategy({"op": "limit", "sell": "USDC", "buy": "WXPL", "amount": 500,
                     "when": {"sym": "WXPL", "cmp": "lt", "price": spot * 2}})
    assert t1.tick()["action"] == "trade"
    usdc_after = adapter.token_balance("USDC", acct.address)

    t2 = Trader(adapter, _guard(adapter, acct), store=store)
    assert t2._swap_done is True
    assert t2.tick()["action"] == "hold"            # triggered limit stays spent across restart
    assert adapter.token_balance("USDC", acct.address) == usdc_after


def test_clear_strategy_removes_from_store(adapter, tmp_path):
    store = FileStrategyStore(path=tmp_path / "s.json")
    acct = _funded_account(adapter)
    t = Trader(adapter, _guard(adapter, acct), store=store)
    t.set_strategy({"op": "dca", "sell": "USDC", "buy": "WXPL", "amount": 100, "everyTicks": 1})
    assert store.load(acct.address) is not None
    t.clear_strategy()
    assert store.load(acct.address) is None
    assert Trader(adapter, _guard(adapter, acct), store=store).strategy is None
