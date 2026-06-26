"""EIP-7702 session rail — the "trade from the user's own address" off-chain half.

Three concerns live here:

  1. sign_trade_intent  — produce the EIP-712 signature the agent's SESSION KEY puts on a TradeIntent.
                          The typehash/domain mirror AgentSessionDelegate.sol exactly (domain name
                          "AgentSessionDelegate", version "1", verifyingContract = the USER EOA).
  2. bootstrap helpers  — delegate_eoa (EIP-7702 type-4 SetCode), install_session / revoke_session
                          (owner-only, the user EOA sends to itself), build_policy.
  3. SessionExecutor    — a TradeGuard-shaped executor (`.account`, `.trade`) so the existing Trader
                          loop drives the user-funded rail with no changes. The agent holds only the
                          session key; a keeper relays. ALL money-bounds are enforced on-chain — this
                          code custodies nothing and cannot widen them.
"""
from eth_account.messages import encode_typed_data
from web3 import Web3

DOMAIN_NAME = "AgentSessionDelegate"
DOMAIN_VERSION = "1"

# must match TradeIntent in AgentSessionDelegate.sol (field order is significant for the typehash)
_TRADE_INTENT_TYPE = [
    {"name": "pool", "type": "address"},
    {"name": "sell", "type": "address"},
    {"name": "buy", "type": "address"},
    {"name": "amountIn", "type": "uint256"},
    {"name": "nonce", "type": "uint256"},
    {"name": "deadline", "type": "uint48"},
]


def _domain(chain_id: int, verifying_contract: str) -> dict:
    return {
        "name": DOMAIN_NAME,
        "version": DOMAIN_VERSION,
        "chainId": int(chain_id),
        "verifyingContract": Web3.to_checksum_address(verifying_contract),
    }


def sign_trade_intent(session_account, chain_id: int, verifying_contract: str, intent: dict) -> bytes:
    """Sign a TradeIntent with the session key. `verifying_contract` is the USER EOA the trade runs in.
    Returns 65-byte r||s||v matching the contract's inline ecrecover. eth_account emits canonical
    low-s signatures, so the contract's EIP-2 malleability guard always passes."""
    typed = {
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"},
            ],
            "TradeIntent": _TRADE_INTENT_TYPE,
        },
        "primaryType": "TradeIntent",
        "domain": _domain(chain_id, verifying_contract),
        "message": {
            "pool": Web3.to_checksum_address(intent["pool"]),
            "sell": Web3.to_checksum_address(intent["sell"]),
            "buy": Web3.to_checksum_address(intent["buy"]),
            "amountIn": int(intent["amountIn"]),
            "nonce": int(intent["nonce"]),
            "deadline": int(intent["deadline"]),
        },
    }
    signed = session_account.sign_message(encode_typed_data(full_message=typed))
    return bytes(signed.signature)


def build_policy(funding_token: str, max_in_per_trade: int, session_in_cap: int, expiry: int,
                 max_slippage_bps: int = 100) -> tuple:
    """The Policy struct tuple, in Solidity field order:
    (active, expiry, fundingToken, maxInPerTrade, sessionInCap, spentIn, maxSlippageBps).
    spentIn starts at 0; the contract resets it on install regardless."""
    return (
        True,
        int(expiry),
        Web3.to_checksum_address(funding_token),
        int(max_in_per_trade),
        int(session_in_cap),
        0,
        int(max_slippage_bps),
    )


def _eip1559_fees(w3, tx: dict) -> dict:
    base = w3.eth.gas_price
    tip = min(w3.to_wei(1, "gwei"), base)
    tx["maxPriorityFeePerGas"] = tip
    tx["maxFeePerGas"] = base * 2 + tip
    return tx


def delegate_eoa(adapter, user_account, delegate_address: str, sponsor_account=None) -> dict:
    """EIP-7702: delegate `user_account`'s EOA code to `delegate_address` via a type-4 SetCode tx.

    Sponsored model (default, `sponsor_account` set): the user only SIGNS the authorization
    (authorization.nonce == the user's current account nonce); the sponsor/keeper submits and pays gas.
    Self model (`sponsor_account` None): the user both authorizes and submits — then the authorization
    nonce is account_nonce + 1 because the enclosing tx consumes the account nonce first.
    """
    w3 = adapter.w3
    delegate_address = Web3.to_checksum_address(delegate_address)
    user_nonce = w3.eth.get_transaction_count(user_account.address)

    self_sponsored = sponsor_account is None or sponsor_account.address == user_account.address
    sponsor = user_account if self_sponsored else sponsor_account
    auth_nonce = user_nonce + 1 if self_sponsored else user_nonce
    tx_nonce = user_nonce if self_sponsored else w3.eth.get_transaction_count(sponsor.address)

    auth = user_account.sign_authorization(
        {"chainId": adapter.cfg.chain_id, "address": delegate_address, "nonce": auth_nonce}
    )

    # The authorization in `authorizationList` delegates the USER EOA's code independently of where
    # this tx is addressed. We point `to` at the zero address (empty, no code) so the tx itself
    # executes nothing — if we addressed it at the now-delegated user, the empty calldata would fall
    # into the delegate with no matching function and revert.
    tx = {
        "type": 4,
        "from": Web3.to_checksum_address(sponsor.address),
        "chainId": adapter.cfg.chain_id,
        "nonce": tx_nonce,
        "to": "0x0000000000000000000000000000000000000000",
        "value": 0,
        "data": b"",
        "authorizationList": [auth],
    }
    _eip1559_fees(w3, tx)
    tx["gas"] = int(w3.eth.estimate_gas(tx) * 1.3)
    signed = sponsor.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    return w3.eth.wait_for_transaction_receipt(tx_hash)


def delegated_code_address(adapter, user_address: str) -> str:
    """Return the implementation address the EOA is delegated to (EIP-7702 designator is
    0xef0100 || address), or None if the account carries no delegation."""
    code = adapter.w3.eth.get_code(Web3.to_checksum_address(user_address))
    if len(code) == 23 and bytes(code[:3]) == b"\xef\x01\x00":
        return Web3.to_checksum_address(bytes(code[3:]).hex())
    return None


def install_session(adapter, user_account, session_key: str, policy: tuple,
                    buys, pools) -> dict:
    """Owner-only: the user EOA installs a session on ITSELF (msg.sender == address(this))."""
    sc = adapter.session_at(user_account.address)
    fn = sc.functions.installSession(
        Web3.to_checksum_address(session_key),
        policy,
        [Web3.to_checksum_address(b) for b in buys],
        [Web3.to_checksum_address(p) for p in pools],
    )
    return adapter._send_fn(user_account, fn)


def revoke_session(adapter, user_account, session_key: str) -> dict:
    """Owner-only: revoke a session immediately."""
    sc = adapter.session_at(user_account.address)
    fn = sc.functions.revokeSession(Web3.to_checksum_address(session_key))
    return adapter._send_fn(user_account, fn)


class SessionExecutor:
    """TradeGuard-shaped executor for the EIP-7702 rail. Exposes `.account` (the user EOA, where funds
    live and output returns) and `.trade(sell, buy, amount_in)`, so a Trader can drive it unchanged.

    The agent signs each intent with `session_account` (the scoped session key); `keeper_account`
    relays and pays gas. The on-chain delegate is the authority: this object proposes, the cage decides.
    A revert (cap/allow-list/expiry/slippage) propagates as an exception, which the Trader records as a
    blocked tick — exactly like a TradeGuard rejection.
    """

    def __init__(self, adapter, user_account, session_account, keeper_account):
        if adapter.session_delegate_abi is None:
            raise RuntimeError("deployment has no AgentSessionDelegate")
        self.adapter = adapter
        self.account = user_account            # .address == the user EOA (Trader reads this)
        self.session = session_account
        self.keeper = keeper_account
        self.contract = adapter.session_at(user_account.address)

    @property
    def session_key(self) -> str:
        return self.session.address

    def session_nonce(self) -> int:
        return int(self.contract.functions.sessionNonce(self.session.address).call())

    def policy(self) -> dict:
        (active, expiry, funding, max_per, sess_cap, spent, slip) = \
            self.contract.functions.policies(self.session.address).call()
        return {"active": active, "expiry": expiry, "fundingToken": funding,
                "maxInPerTrade": max_per, "sessionInCap": sess_cap, "spentIn": spent,
                "maxSlippageBps": slip}

    def trade(self, sell: str, buy: str, amount_in: int) -> dict:
        """Build + session-sign + relay an executeTrade. minOut/recipient are NOT ours to set — the
        contract computes minOut from the live quote and pins output to the user. Returns a dict shaped
        like TradeGuard.trade for the Trader/log surface."""
        amount_in = int(amount_in)
        pool = self.adapter.find_pool(sell, buy)
        if pool is None:
            raise RuntimeError("no pool for {}/{}".format(sell, buy))

        sell_addr = self.adapter.tokens[sell].address
        buy_addr = self.adapter.tokens[buy].address
        pool_addr = pool.address
        nonce = self.session_nonce()
        deadline = self.adapter.w3.eth.get_block("latest")["timestamp"] + 3600

        intent = {"pool": pool_addr, "sell": sell_addr, "buy": buy_addr,
                  "amountIn": amount_in, "nonce": nonce, "deadline": deadline}
        sig = sign_trade_intent(self.session, self.adapter.cfg.chain_id,
                                self.account.address, intent)
        intent_tuple = (Web3.to_checksum_address(pool_addr), Web3.to_checksum_address(sell_addr),
                        Web3.to_checksum_address(buy_addr), amount_in, nonce, deadline)

        fn = self.contract.functions.executeTrade(intent_tuple, sig)
        receipt = self.adapter._send_fn(self.keeper, fn)
        return {
            "txHash": receipt["transactionHash"].hex(),
            "sell": sell,
            "buy": buy,
            "amountIn": amount_in,
            "rail": "session-7702",
            "from": self.account.address,
            "buyBalance": self.adapter.token_balance(buy, self.account.address),
            "spentIn": self.policy()["spentIn"],
        }
