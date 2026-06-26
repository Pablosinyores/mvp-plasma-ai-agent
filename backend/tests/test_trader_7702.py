"""Trader on the EIP-7702 rail — the existing strategy loop driving SessionExecutor unchanged.

Proves the rail is pluggable: a DCA / limit strategy runs through the same Trader.tick() path, but every
fill executes FROM the user's own EOA via the session delegate (funds debited from the user, output to
the user), and an over-cap tick surfaces as a `blocked` tick rather than draining anything.
"""
import pytest
from eth_account import Account

from plasma_mvp.adapter import LocalAdapter
from plasma_mvp.trader import Trader
from plasma_mvp import session as S

USER_PK = "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d"
KEEPER_PK = "0x5de4111afa1a4b94908f83103eb1f1706367c2e68ca870fc3fb9a804cdab365a"
ONE_DAY = 24 * 3600


@pytest.fixture()
def rail():
    try:
        a = LocalAdapter()
    except Exception as e:  # noqa: BLE001
        pytest.skip("no live chain: {}".format(e))
    if a.session_delegate_abi is None or a.find_pool("USDC", "WXPL") is None:
        pytest.skip("delegate/pool missing")

    user = a.w3.eth.account.from_key(USER_PK)
    keeper = a.w3.eth.account.from_key(KEEPER_PK)
    session = Account.create()

    S.delegate_eoa(a, user, a.session_delegate_address, sponsor_account=keeper)
    a.fund_eth(user.address, 5)
    a.mint_token("USDC", user.address, 1_000_000_000)

    now = a.w3.eth.get_block("latest")["timestamp"]
    pol = S.build_policy(a.tokens["USDC"].address, 100_000_000, 250_000_000, now + ONE_DAY, 100)
    S.install_session(a, user, session.address, pol,
                      buys=[a.tokens["WXPL"].address], pools=[a.find_pool("USDC", "WXPL").address])

    ex = S.SessionExecutor(a, user, session, keeper)
    return a, user, ex


def test_dca_ticks_trade_from_user_wallet(rail):
    a, user, ex = rail
    trader = Trader(a, ex)  # no store -> in-memory only
    trader.set_strategy({"op": "dca", "sell": "USDC", "buy": "WXPL", "amount": 50, "everyTicks": 1})

    usdc0 = a.token_balance("USDC", user.address)
    wxpl0 = a.token_balance("WXPL", user.address)

    results = trader.run(2)  # two DCA fills (50 USDC each)
    trades = [r for r in results if r["action"] == "trade"]
    assert len(trades) == 2, results
    assert all(t["from"] == user.address for t in trades), "every fill from the user EOA"

    assert a.token_balance("USDC", user.address) == usdc0 - 100_000_000, "user spent 100 USDC"
    assert a.token_balance("WXPL", user.address) > wxpl0, "user received WXPL"
    assert ex.policy()["spentIn"] == 100_000_000


def test_over_cap_tick_is_blocked_not_drained(rail):
    a, user, ex = rail
    trader = Trader(a, ex)
    # 200 USDC per fill exceeds the 100 USDC per-trade cap -> the contract reverts, Trader reports blocked
    trader.set_strategy({"op": "dca", "sell": "USDC", "buy": "WXPL", "amount": 200, "everyTicks": 1})

    usdc0 = a.token_balance("USDC", user.address)
    r = trader.tick()
    assert r["action"] == "blocked", r
    assert a.token_balance("USDC", user.address) == usdc0, "nothing spent on a blocked tick"
    assert ex.policy()["spentIn"] == 0


def test_limit_holds_then_fires_from_user_wallet(rail):
    a, user, ex = rail
    trader = Trader(a, ex)
    # WXPL spot ~0.10 USDC. A 'gt 1.0' limit never triggers; a 'gt 0.0' fires immediately.
    trader.set_strategy({"op": "limit", "sell": "USDC", "buy": "WXPL", "amount": 50,
                         "when": {"sym": "WXPL", "cmp": "gt", "price": 1.0}})
    r = trader.tick()
    assert r["action"] == "hold", r  # price below threshold -> holds

    trader.set_strategy({"op": "limit", "sell": "USDC", "buy": "WXPL", "amount": 50,
                         "when": {"sym": "WXPL", "cmp": "gt", "price": 0.0}})
    usdc0 = a.token_balance("USDC", user.address)
    r = trader.tick()
    assert r["action"] == "trade", r
    assert r["from"] == user.address
    assert a.token_balance("USDC", user.address) == usdc0 - 50_000_000
    # fires exactly once
    r2 = trader.tick()
    assert r2["action"] == "hold", r2
