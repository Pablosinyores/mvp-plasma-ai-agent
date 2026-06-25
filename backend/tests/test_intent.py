"""intent parser tests — deterministic fallback path (no model, no chain)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sdk"))
from plasma_mvp import intent  # noqa: E402


def test_swap_for_xpl():
    o = intent.parse("swap 100 USDC for XPL")
    assert o == {"op": "swap", "sell": "USDC", "buy": "WXPL", "amount": 100.0}


def test_buy_with_funding_asset_orientation():
    o = intent.parse("buy 1 WETH with USDC")
    assert o["op"] == "swap" and o["sell"] == "USDC" and o["buy"] == "WETH"


def test_dca_every_n_ticks():
    o = intent.parse("DCA buy 50 USDC of XPL every 2 ticks")
    assert o["op"] == "dca"
    assert o["sell"] == "USDC" and o["buy"] == "WXPL"
    assert o["amount"] == 50.0 and o["everyTicks"] == 2


def test_dca_default_every_one():
    o = intent.parse("trade 25 USDC into ETH each tick")
    assert o["op"] == "dca" and o["everyTicks"] == 1 and o["buy"] == "WETH"


def test_rebalance_percentage():
    o = intent.parse("rebalance to keep 60% USDC and the rest WXPL")
    assert o["op"] == "rebalance"
    assert o["base"] == "USDC" and o["quote"] == "WXPL" and o["targetBps"] == 6000


def test_xpl_and_eth_aliases():
    assert intent.parse("swap 10 stablecoin for plasma")["buy"] == "WXPL"
    assert intent.parse("convert 5 ether to usdc")["sell"] == "WETH"


def test_garbage_is_noop():
    assert intent.parse("what's the weather today?")["op"] == "noop"


def test_stub_model_routes_to_fallback():
    class StubModel:
        backend = "stub"

        def complete(self, *a, **k):  # noqa: D401
            return "[stub-summary] swap 100 USDC for XPL"

    # a stub model must not be trusted to parse; fallback handles the raw prompt instead
    o = intent.parse("swap 100 USDC for XPL", model=StubModel())
    assert o == {"op": "swap", "sell": "USDC", "buy": "WXPL", "amount": 100.0}


def test_limit_buy_when_price_below():
    o = intent.parse("buy 100 USDC of XPL when price < 0.09")
    assert o["op"] == "limit"
    assert o["sell"] == "USDC" and o["buy"] == "WXPL" and o["amount"] == 100.0
    assert o["when"] == {"sym": "WXPL", "cmp": "lt", "price": 0.09}


def test_limit_sell_when_price_above():
    o = intent.parse("sell 50 WXPL when price > 0.12")
    assert o["op"] == "limit"
    assert o["sell"] == "WXPL" and o["buy"] == "USDC" and o["amount"] == 50.0
    assert o["when"] == {"sym": "WXPL", "cmp": "gt", "price": 0.12}


def test_limit_word_comparators_and_watch_sym():
    o = intent.parse("buy 25 USDC of WETH when price drops below 1500")
    assert o["op"] == "limit" and o["amount"] == 25.0
    assert o["when"] == {"sym": "WETH", "cmp": "lt", "price": 1500.0}


def test_limit_without_amount_is_not_a_limit():
    # no trade size -> cannot size safely; must NOT silently invent one
    o = intent.parse("buy XPL when price < 0.09")
    assert o["op"] != "limit"


def test_limit_llm_json_is_used():
    class LLM:
        backend = "llamacpp"

        def complete(self, *a, **k):
            return ('ok: {"op":"limit","sell":"USDC","buy":"WXPL","amount":100,'
                    '"when":{"sym":"WXPL","cmp":"lt","price":0.09}}')

    o = intent.parse("buy xpl when it gets cheap", model=LLM())
    assert o == {"op": "limit", "sell": "USDC", "buy": "WXPL", "amount": 100.0,
                 "when": {"sym": "WXPL", "cmp": "lt", "price": 0.09}}


def test_llm_json_is_used_when_available():
    class LLM:
        backend = "llamacpp"

        def complete(self, *a, **k):
            return 'sure: {"op":"dca","sell":"USDC","buy":"WXPL","amount":40,"everyTicks":3}'

    o = intent.parse("buy some xpl regularly", model=LLM())
    assert o == {"op": "dca", "sell": "USDC", "buy": "WXPL", "amount": 40.0, "everyTicks": 3}
