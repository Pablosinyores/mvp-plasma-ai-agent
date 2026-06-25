"""TraderManager tests — the strategy-panel control plane (set / get / clear / drive ticks).

Uses a plain local signer + the file store, so it runs without LocalStack/KMS (which the panel's
real KeyVault signer would need). Exercises the live multi-pair venue for the actual trades.
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
from studio_api.strategy_ctl import TraderManager  # noqa: E402


@pytest.fixture(scope="module")
def adapter():
    a = LocalAdapter()
    if not a.pools:
        pytest.skip("deployment has no swap venue")
    return a


def _mgr(adapter, tmp_path):
    signers = {}

    def signer_for(name):
        signers.setdefault(name, Account.create())
        return signers[name]

    return TraderManager(adapter, FileStrategyStore(path=tmp_path / "s.json"), signer_for)


def test_set_get_clear_dca(adapter, tmp_path):
    mgr = _mgr(adapter, tmp_path)
    order = mgr.set_strategy("alice", "DCA buy 100 USDC of XPL every 1 tick")
    assert order["op"] == "dca"
    g = mgr.get("alice")
    assert g["strategy"]["op"] == "dca" and g["prompt"].startswith("DCA")
    assert g["tickCount"] == 0 and g["ticks"] == []

    res = mgr.tick_active()
    assert res["alice"]["action"] == "trade"
    g2 = mgr.get("alice")
    assert g2["tickCount"] == 1 and len(g2["ticks"]) == 1
    assert g2["ticks"][0]["action"] == "trade" and g2["ticks"][0]["buy"] == "WXPL"

    mgr.clear("alice")
    assert mgr.get("alice")["strategy"] is None
    assert mgr.get("alice")["ticks"] == []
    assert mgr.tick_active() == {}              # nothing active -> no ticks run


def test_limit_panel_flow_holds_until_triggered(adapter, tmp_path):
    mgr = _mgr(adapter, tmp_path)
    spot = adapter.spot_price("WXPL")
    order = mgr.set_strategy("bob", "buy 100 USDC of WXPL when price < {}".format(round(spot / 2, 6)))
    assert order["op"] == "limit" and order["when"]["cmp"] == "lt"
    res = mgr.tick_active()["bob"]
    assert res["action"] == "hold" and res["reason"] == "limit not triggered"
    assert mgr.get("bob")["ticks"][0]["price"] > 0


def test_seed_funds_make_trade_possible(adapter, tmp_path):
    mgr = _mgr(adapter, tmp_path)
    mgr.set_strategy("carol", "swap 500 USDC for WETH")
    addr = mgr.get("carol")["address"]
    assert adapter.token_balance("USDC", addr) >= 1_000_000_000  # seeded by the manager
    res = mgr.tick_active()["carol"]
    assert res["action"] == "trade" and res["buy"] == "WETH"
