"""TradeGuard — bounded multi-asset trading authority (generalizes SwapGuard, design §24).

The agentic trader's model/strategy proposes WHAT to trade; TradeGuard decides whether it's PERMITTED
and is the only path that touches the agent's key. Every trade passes these bounds BEFORE any tx:

  1. token allow-list  — both `sell` and `buy` must be in the allowed token set,
  2. venue allow-list  — a pool for the pair must exist in the loaded registry (no arbitrary target),
  3. recipient pinned  — output is hard-pinned to the agent's OWN address (never an attacker),
  4. per-trade cap     — no single trade sells more than `max_notional` (denominated in USDC value),
  5. session cap       — cumulative USDC-notional traded over the guard's life ≤ `session_notional`,
  6. slippage bound    — `min_out` derived from the live quote minus at most `max_slippage_bps`.

Notional is normalized to USDC value via the live pool price, so caps mean the same thing whether the
agent sells USDC, WETH, or WXPL. A hijacked strategy can at worst churn the agent's OWN balances
between allow-listed assets, in bounded slices, at bounded prices, into its OWN wallet. No drain path.
"""


class TradeBlocked(Exception):
    """Raised when a requested trade violates a TradeGuard policy bound."""


class TradeGuard:
    def __init__(
        self,
        adapter,
        account,
        allowed_tokens=("USDC", "WETH", "WXPL"),
        max_notional_usdc: int = 5_000_000_000,   # 5,000 USDC default per-trade cap
        session_notional_usdc: int = 50_000_000_000,  # 50,000 USDC default session cap
        max_slippage_bps: int = 100,
    ):
        if not adapter.pools:
            raise TradeBlocked("deployment has no swap venue")
        if max_notional_usdc <= 0 or session_notional_usdc <= 0:
            raise TradeBlocked("caps must be positive")
        if not (0 <= max_slippage_bps <= 10_000):
            raise TradeBlocked("slippage bps out of range")
        self.adapter = adapter
        self.account = account
        self.allowed = set(allowed_tokens)
        self.max_notional = int(max_notional_usdc)
        self.session_notional = int(session_notional_usdc)
        self.max_slippage_bps = int(max_slippage_bps)
        self.spent_notional = 0

    @property
    def remaining(self) -> int:
        return self.session_notional - self.spent_notional

    def _usdc_notional(self, sym: str, amount: int) -> int:
        """USDC-value of `amount` of `sym`, via the live pool price (USDC itself is 1:1)."""
        if sym == "USDC":
            return int(amount)
        # price the sell asset by quoting it into USDC
        return self.adapter.quote_trade(sym, "USDC", int(amount))

    def trade(self, sell: str, buy: str, amount_in: int) -> dict:
        """Execute a guarded trade: sell `amount_in` of `sell` for `buy`, into the agent's own wallet.
        Returns {txHash, sell, buy, amountIn, minOut, quote, notionalUsdc, buyBalance, remaining}."""
        amount_in = int(amount_in)

        # (1) token allow-list
        if sell not in self.allowed or buy not in self.allowed:
            raise TradeBlocked("token not allow-listed: {} or {}".format(sell, buy))
        if sell == buy:
            raise TradeBlocked("sell and buy are the same token")
        # (2) venue allow-list
        if self.adapter.find_pool(sell, buy) is None:
            raise TradeBlocked("no allow-listed pool for {}/{}".format(sell, buy))
        if amount_in <= 0:
            raise TradeBlocked("trade amount must be positive")

        # (3) the agent must hold what it's selling (its own funds only)
        bal = self.adapter.token_balance(sell, self.account.address)
        if bal < amount_in:
            raise TradeBlocked("insufficient {}: have {}, need {}".format(sell, bal, amount_in))

        # (4) per-trade + (5) session caps, in USDC notional
        notional = self._usdc_notional(sell, amount_in)
        if notional > self.max_notional:
            raise TradeBlocked(
                "trade notional {} USDC exceeds per-trade cap {}".format(notional, self.max_notional)
            )
        if notional > self.remaining:
            raise TradeBlocked(
                "trade notional {} exceeds remaining session budget {}".format(notional, self.remaining)
            )

        # (6) slippage floor
        quote = self.adapter.quote_trade(sell, buy, amount_in)
        if quote <= 0:
            raise TradeBlocked("no liquidity / zero quote")
        min_out = (quote * (10_000 - self.max_slippage_bps)) // 10_000

        # execute — recipient pinned to self inside the adapter helper
        res = self.adapter.trade(self.account, sell, buy, amount_in, min_out)
        self.spent_notional += notional
        return {
            "txHash": res["txHash"],
            "sell": sell,
            "buy": buy,
            "amountIn": amount_in,
            "minOut": min_out,
            "quote": quote,
            "notionalUsdc": notional,
            "buyBalance": res["buyBalance"],
            "remaining": self.remaining,
        }
