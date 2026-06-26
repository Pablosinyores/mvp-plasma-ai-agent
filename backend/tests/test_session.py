"""EIP-7702 session rail — live integration tests against anvil.

Requires a running anvil with the venue + AgentSessionDelegate deployed (deployments/local.json). The
tests delegate a funded user EOA, install a scoped session, and assert trades execute FROM the user's
own address with funds debited from the user and output returned to the user — and that every on-chain
cap rejects a bad trade even though the keeper/session key is fully trusted to misbehave.
"""
import pytest
from eth_account import Account
from web3 import Web3
from web3.exceptions import ContractLogicError

from plasma_mvp.adapter import LocalAdapter
from plasma_mvp import session as S

# anvil default funded accounts
USER_PK = "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d"      # acct #1
KEEPER_PK = "0x5de4111afa1a4b94908f83103eb1f1706367c2e68ca870fc3fb9a804cdab365a"    # acct #2

ONE_DAY = 24 * 3600


def _adapter_or_skip():
    try:
        a = LocalAdapter()
    except Exception as e:  # noqa: BLE001
        pytest.skip("no live chain/config: {}".format(e))
    if a.session_delegate_abi is None:
        pytest.skip("AgentSessionDelegate not in deployment")
    if a.find_pool("USDC", "WXPL") is None:
        pytest.skip("USDC/WXPL pool missing")
    return a


@pytest.fixture()
def env():
    a = _adapter_or_skip()
    user = a.w3.eth.account.from_key(USER_PK)
    keeper = a.w3.eth.account.from_key(KEEPER_PK)
    session = Account.create()  # a throwaway session key holding zero funds

    # delegate the user EOA to the implementation FIRST (keeper sponsors the type-4 tx). Once
    # delegated, the delegate's receive() lets the user EOA accept the ETH top-up below.
    S.delegate_eoa(a, user, a.session_delegate_address, sponsor_account=keeper)
    assert S.delegated_code_address(a, user.address) == a.session_delegate_address, "delegation not attached"

    # fund the user EOA: ETH for the install tx + USDC to spend
    a.fund_eth(user.address, 5)
    a.mint_token("USDC", user.address, 1_000_000_000)  # 1,000 USDC (6dp)
    return a, user, keeper, session


def _install(a, user, session, *, max_per=100_000_000, cap=250_000_000, slippage=100, expiry_in=ONE_DAY):
    now = a.w3.eth.get_block("latest")["timestamp"]
    pol = S.build_policy(
        funding_token=a.tokens["USDC"].address,
        max_in_per_trade=max_per,
        session_in_cap=cap,
        expiry=now + expiry_in,
        max_slippage_bps=slippage,
    )
    S.install_session(a, user, session.address, pol,
                      buys=[a.tokens["WXPL"].address], pools=[a.find_pool("USDC", "WXPL").address])


def test_happy_trade_from_user_address(env):
    a, user, keeper, session = env
    _install(a, user, session)
    ex = S.SessionExecutor(a, user, session, keeper)

    usdc_before = a.token_balance("USDC", user.address)
    wxpl_before = a.token_balance("WXPL", user.address)
    keeper_wxpl_before = a.token_balance("WXPL", keeper.address)

    res = ex.trade("USDC", "WXPL", 50_000_000)  # 50 USDC

    assert a.token_balance("USDC", user.address) == usdc_before - 50_000_000, "user USDC debited"
    assert a.token_balance("WXPL", user.address) > wxpl_before, "user WXPL credited"
    assert a.token_balance("WXPL", keeper.address) == keeper_wxpl_before, "keeper got nothing"
    assert res["from"] == user.address
    assert ex.policy()["spentIn"] == 50_000_000, "spentIn tracks the session cap"
    assert ex.session_nonce() == 1


def test_per_trade_cap_rejected_on_chain(env):
    a, user, keeper, session = env
    _install(a, user, session, max_per=100_000_000)
    ex = S.SessionExecutor(a, user, session, keeper)
    with pytest.raises((ContractLogicError, ValueError)):
        ex.trade("USDC", "WXPL", 101_000_000)  # over per-trade cap


def test_session_cap_rejected_on_chain(env):
    a, user, keeper, session = env
    _install(a, user, session, max_per=100_000_000, cap=250_000_000)
    ex = S.SessionExecutor(a, user, session, keeper)
    ex.trade("USDC", "WXPL", 100_000_000)
    ex.trade("USDC", "WXPL", 100_000_000)  # spent 200
    with pytest.raises((ContractLogicError, ValueError)):
        ex.trade("USDC", "WXPL", 100_000_000)  # 300 > 250 cap


def test_unallowed_pool_rejected(env):
    a, user, keeper, session = env
    # install with only the WETH/WXPL pool allow-listed but funding USDC -> the USDC/WXPL trade's pool
    # is not allow-listed
    now = a.w3.eth.get_block("latest")["timestamp"]
    pol = S.build_policy(a.tokens["USDC"].address, 100_000_000, 250_000_000, now + ONE_DAY, 100)
    S.install_session(a, user, session.address, pol,
                      buys=[a.tokens["WXPL"].address], pools=[a.find_pool("WETH", "WXPL").address])
    ex = S.SessionExecutor(a, user, session, keeper)
    with pytest.raises((ContractLogicError, ValueError)):
        ex.trade("USDC", "WXPL", 10_000_000)


def test_revoke_blocks_further_trades(env):
    a, user, keeper, session = env
    _install(a, user, session)
    ex = S.SessionExecutor(a, user, session, keeper)
    ex.trade("USDC", "WXPL", 10_000_000)  # works
    S.revoke_session(a, user, session.address)
    with pytest.raises((ContractLogicError, ValueError)):
        ex.trade("USDC", "WXPL", 10_000_000)  # now inactive


def test_uninstalled_key_cannot_trade(env):
    a, user, keeper, session = env
    _install(a, user, session)
    stranger = Account.create()
    ex = S.SessionExecutor(a, user, stranger, keeper)  # stranger key never installed
    with pytest.raises((ContractLogicError, ValueError)):
        ex.trade("USDC", "WXPL", 10_000_000)
