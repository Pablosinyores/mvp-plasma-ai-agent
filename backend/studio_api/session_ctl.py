"""SessionManager — control plane for the EIP-7702 "trade from the user's own address" rail.

The mirror of TraderManager, but for user-funded trading. Per connected WALLET (a user EOA) it:

  * authorize()  — mints a fresh server-side SESSION KEY and returns the payloads the wallet must sign:
                   the EIP-7702 delegation authorization and the installSession policy (token allow-list,
                   per-trade cap, session cap, slippage floor, expiry). The wallet delegates + installs
                   on-chain; the backend never holds the user's key and cannot widen any bound.
  * set_strategy / get / clear — the same standing-prompt surface as the agent rail, but every fill runs
                   through SessionExecutor: signed by the session key, relayed by the keeper, executed
                   FROM the user's EOA with output pinned back to the user.
  * revoke_payload() — the calldata the wallet submits to kill the session on-chain.

Security: the prompt only chooses WHAT/WHEN. The on-chain delegate is the cage. A compromised backend
(session key + keeper) can at most churn the user's own allow-listed assets in capped slices into the
user's own wallet — never redirect, never exceed caps, never touch a non-allow-listed token/pool.
"""
from eth_account import Account
from web3 import Web3

from plasma_mvp import intent
from plasma_mvp import session as S
from plasma_mvp.trader import Trader

_TICK_FIELDS = ("tick", "action", "reason", "price", "threshold", "watch", "cmp",
                "sell", "buy", "amountIn", "rail", "from", "spentIn", "txHash")

# DEMO-ONLY: the studio's demo wallets are the deterministic Anvil dev accounts. Locally we can play
# the wallet's role (sign the 7702 delegation + installSession) on its behalf so the panel works
# end-to-end without a real browser wallet. These keys are public test keys and exist only on Anvil.
# In production the connected wallet signs these itself and the backend NEVER sees a user key.
DEMO_ANVIL_KEYS = {
    "0xf39fd6e51aad88f6f4ce6ab8827279cfffb92266": "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80",
    "0x70997970c51812dc3a010c7d01b50e0d17dc79c8": "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d",
    "0x3c44cdddb6a900fa2b585dd299e03d12fa4293bc": "0x5de4111afa1a4b94908f83103eb1f1706367c2e68ca870fc3fb9a804cdab365a",
    "0x90f79bf6eb2c4f870365e785982e1f101e93b906": "0x7c852118294e51e653712a81e05800f419141751be58f605c371e15141b007a6",
}


def shape_tick(r: dict) -> dict:
    return {k: r[k] for k in _TICK_FIELDS if k in r}


class _UserRef:
    """Lightweight stand-in for a signing account: SessionExecutor/Trader only read `.address` on the
    user rail (the user never signs a trade — the session key does, the keeper relays)."""

    def __init__(self, address: str):
        self.address = Web3.to_checksum_address(address)


class SessionManager:
    def __init__(self, adapter, store, keeper=None, ticks_kept: int = 20):
        if adapter.session_delegate_abi is None:
            raise RuntimeError("deployment has no AgentSessionDelegate (7702 rail unavailable)")
        self.adapter = adapter
        self.store = store
        self.keeper = keeper or adapter.relayer   # relays executeTrade, pays gas only
        self.ticks_kept = ticks_kept
        self._sessions = {}                        # user(lower) -> entry

    def _now(self) -> int:
        return self.adapter.w3.eth.get_block("latest")["timestamp"]

    def _entry(self, user: str):
        return self._sessions.get(Web3.to_checksum_address(user).lower())

    # --- authorize: hand the wallet what it must sign ---------------------------
    def authorize(self, user: str, *, max_in_per_trade: int, session_in_cap: int,
                  slippage_bps: int = 100, expiry_secs: int = 86400, buys=("WXPL",),
                  funding: str = "USDC") -> dict:
        user = Web3.to_checksum_address(user)
        buys = list(buys)
        funding_addr = self.adapter.tokens[funding].address
        buy_addrs = [self.adapter.tokens[b].address for b in buys]
        pool_addrs = []
        for b in buys:
            pool = self.adapter.find_pool(funding, b)
            if pool is None:
                raise RuntimeError("no {}/{} pool to allow-list".format(funding, b))
            pool_addrs.append(pool.address)

        sess = Account.create()  # a throwaway session key; custodies nothing
        expiry = self._now() + int(expiry_secs)
        policy_tuple = S.build_policy(funding_addr, max_in_per_trade, session_in_cap, expiry, slippage_bps)

        # the executor + trader are live now; they only start firing once the wallet installs on-chain
        ex = S.SessionExecutor(self.adapter, _UserRef(user), sess, self.keeper)
        trader = Trader(self.adapter, ex, store=self.store)
        self._sessions[user.lower()] = {
            "user": user, "session": sess, "executor": ex, "trader": trader, "ticks": [],
            "funding": funding, "buys": buys, "installed": False,
            # retained so the demo bootstrap can submit the exact installSession the wallet would
            "policyTuple": policy_tuple, "buyAddrs": buy_addrs, "poolAddrs": pool_addrs,
        }

        nonce = self.adapter.w3.eth.get_transaction_count(user)
        return {
            "user": user,
            "delegate": self.adapter.session_delegate_address,
            "sessionKey": sess.address,
            "chainId": self.adapter.cfg.chain_id,
            # what the wallet signs for EIP-7702 (delegate the user's code to the implementation)
            "authorization": {
                "chainId": self.adapter.cfg.chain_id,
                "address": self.adapter.session_delegate_address,
                "nonce": nonce,
            },
            # what the wallet submits as installSession(sessionKey, policy, buys, pools) to ITSELF
            "install": {
                "to": user,
                "function": "installSession",
                "policy": {
                    "active": True,
                    "expiry": expiry,
                    "fundingToken": funding_addr,
                    "maxInPerTrade": str(max_in_per_trade),
                    "sessionInCap": str(session_in_cap),
                    "spentIn": "0",
                    "maxSlippageBps": slippage_bps,
                },
                "buys": buy_addrs,
                "pools": pool_addrs,
            },
            "policyTuple": [str(x) if isinstance(x, int) else x for x in policy_tuple],
        }

    def mark_installed(self, user: str) -> None:
        e = self._entry(user)
        if e:
            e["installed"] = True

    # --- standing strategy on the user rail -------------------------------------
    def _require(self, user: str):
        e = self._entry(user)
        if e is None:
            raise RuntimeError("no authorized session for {}; call authorize first".format(user))
        return e

    def set_strategy(self, user: str, prompt: str, model=None) -> dict:
        e = self._require(user)
        order = intent.parse(prompt, model=model)
        e["trader"].set_strategy(order, prompt=prompt)
        return order

    def set_order(self, user: str, order: dict, prompt: str = None) -> dict:
        """Install an already-parsed order (used by tests / programmatic callers)."""
        e = self._require(user)
        e["trader"].set_strategy(order, prompt=prompt)
        return order

    def get(self, user: str) -> dict:
        e = self._entry(user)
        if e is None:
            return {"user": Web3.to_checksum_address(user), "authorized": False}
        tr = e["trader"]
        out = {
            "user": e["user"],
            "authorized": True,
            "installed": e["installed"],
            "rail": "session-7702",
            "sessionKey": e["session"].address,
            "delegate": self.adapter.session_delegate_address,
            "strategy": tr.strategy,
            "prompt": tr.prompt,
            "tickCount": tr.tick_count,
            "swapDone": tr._swap_done,
            "ticks": list(e["ticks"]),
        }
        try:
            out["policy"] = e["executor"].policy()
        except Exception:  # noqa: BLE001 — not installed yet / chain hiccup
            out["policy"] = None
        return out

    def clear(self, user: str) -> None:
        e = self._entry(user)
        if e:
            e["trader"].clear_strategy()
            e["ticks"].clear()

    def revoke_payload(self, user: str) -> dict:
        """Calldata the wallet submits to revoke on-chain. Also clears the standing strategy locally so
        the loop stops proposing immediately."""
        e = self._require(user)
        self.clear(user)
        return {"to": e["user"], "function": "revokeSession", "sessionKey": e["session"].address}

    # --- DEMO-ONLY: play the wallet locally so the panel works end-to-end --------
    def dev_bootstrap(self, user: str, *, seed_usdc: int = 2_000_000_000) -> dict:
        """Local demo: delegate + installSession on the user's behalf using the well-known Anvil key,
        and seed it with gas + USDC. Production replaces this with the real wallet's own on-chain txs."""
        e = self._require(user)
        pk = DEMO_ANVIL_KEYS.get(Web3.to_checksum_address(user).lower())
        if pk is None:
            raise RuntimeError("{} is not a known demo wallet (production wallets self-sign)".format(user))
        user_acct = self.adapter.w3.eth.account.from_key(pk)

        S.delegate_eoa(self.adapter, user_acct, self.adapter.session_delegate_address,
                       sponsor_account=self.keeper)
        self.adapter.fund_eth(user_acct.address, 1)
        if self.adapter.token_balance(e["funding"], user_acct.address) < seed_usdc // 2:
            self.adapter.mint_token(e["funding"], user_acct.address, seed_usdc)
        S.install_session(self.adapter, user_acct, e["session"].address, e["policyTuple"],
                          buys=e["buyAddrs"], pools=e["poolAddrs"])
        e["installed"] = True
        return self.get(user)

    def dev_revoke(self, user: str) -> dict:
        """Local demo: submit revokeSession on the user's behalf, then stop the loop."""
        e = self._require(user)
        pk = DEMO_ANVIL_KEYS.get(Web3.to_checksum_address(user).lower())
        if pk is None:
            raise RuntimeError("{} is not a known demo wallet".format(user))
        user_acct = self.adapter.w3.eth.account.from_key(pk)
        S.revoke_session(self.adapter, user_acct, e["session"].address)
        self.clear(user)
        e["installed"] = False
        return self.get(user)

    def tick_active(self) -> dict:
        out = {}
        for key, e in list(self._sessions.items()):
            if not e["trader"].strategy:
                continue
            try:
                r = e["trader"].tick()
            except Exception as ex:  # noqa: BLE001
                r = {"action": "blocked", "reason": str(ex)}
            buf = e["ticks"]
            buf.append(shape_tick(r))
            del buf[: -self.ticks_kept]
            out[e["user"]] = r
        return out
