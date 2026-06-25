"""SwapGuard — bounded swap authority, the trading analog of X402Signer (design §24).

The agent's tool code / model output may ask to "convert USDC to WETH", but it may NEVER choose where
the output goes, which venue is used, how big the swap is, or accept unbounded slippage. SwapGuard is
the only path through which a swap can execute, and it enforces, BEFORE sending any tx:

  1. venue allow-list   — the AMM address is fixed at construction; a prompt cannot redirect to a
                          look-alike pool,
  2. recipient pinned   — output is hard-pinned to the agent's OWN address; a swap can never pay out
                          to an attacker (the classic prompt-injection drain),
  3. per-swap cap       — no single swap exceeds `max_usdc_per_swap`,
  4. session cap        — cumulative swapped USDC across the guard's life ≤ `session_usdc`,
  5. slippage bound     — `min_out` is derived from the live on-chain quote minus at most
                          `max_slippage_bps`; the caller cannot pass min_out=0 and eat a sandwich.

So even a fully compromised model that emits "swap 1e9 USDC and send WETH to <attacker>" can at worst
convert one capped slice of the agent's OWN USDC into WETH that lands back in the agent's OWN wallet,
at a bounded price. No drain path exists.
"""


class SwapBlocked(Exception):
    """Raised when a requested swap violates a SwapGuard policy bound."""


class SwapGuard:
    def __init__(
        self,
        adapter,
        account,
        max_usdc_per_swap: int,
        session_usdc: int,
        max_slippage_bps: int = 100,
    ):
        """
        adapter: a LocalAdapter with a swap venue loaded (adapter.amm is not None).
        account: the agent's eth_account LocalAccount — also the hard-pinned swap recipient.
        max_usdc_per_swap / session_usdc: base units (USDC 6dp).
        max_slippage_bps: max tolerated slippage vs the live quote, in basis points (100 = 1%).
        """
        if adapter.amm is None:
            raise SwapBlocked("deployment has no swap venue")
        if max_usdc_per_swap <= 0 or session_usdc <= 0:
            raise SwapBlocked("caps must be positive")
        if not (0 <= max_slippage_bps <= 10_000):
            raise SwapBlocked("slippage bps out of range")
        self.adapter = adapter
        self.account = account
        self.max_usdc_per_swap = int(max_usdc_per_swap)
        self.session_usdc = int(session_usdc)
        self.max_slippage_bps = int(max_slippage_bps)
        self.spent_usdc = 0

    @property
    def remaining(self) -> int:
        return self.session_usdc - self.spent_usdc

    def buy_weth(self, amount_usdc: int) -> dict:
        """Convert `amount_usdc` of the agent's own USDC to WETH, into the agent's own wallet.
        Enforces every bound, then executes. Returns {txHash, amountUsdc, minOut, quote, wethBalance}.
        """
        amount_usdc = int(amount_usdc)

        # (1) positivity + per-call cap
        if amount_usdc <= 0:
            raise SwapBlocked("swap amount must be positive")
        if amount_usdc > self.max_usdc_per_swap:
            raise SwapBlocked(
                "swap {} exceeds per-swap cap {}".format(amount_usdc, self.max_usdc_per_swap)
            )
        # (2) session cap
        if amount_usdc > self.remaining:
            raise SwapBlocked(
                "swap {} exceeds remaining session budget {}".format(amount_usdc, self.remaining)
            )
        # (3) the agent must actually hold the USDC it's spending (its own funds only)
        bal = self.adapter.usdc_balance(self.account.address)
        if bal < amount_usdc:
            raise SwapBlocked("insufficient USDC: have {}, need {}".format(bal, amount_usdc))

        # (4) slippage bound — min_out floored at quote * (1 - max_slippage)
        quote = self.adapter.quote_usdc_to_weth(amount_usdc)
        if quote <= 0:
            raise SwapBlocked("no liquidity / zero quote")
        min_out = (quote * (10_000 - self.max_slippage_bps)) // 10_000

        # (5) execute — recipient is pinned to self inside the adapter helper
        res = self.adapter.swap_usdc_for_weth(self.account, amount_usdc, min_out)
        self.spent_usdc += amount_usdc
        return {
            "txHash": res["txHash"],
            "amountUsdc": amount_usdc,
            "minOut": min_out,
            "quote": quote,
            "wethBalance": res["wethBalance"],
            "remaining": self.remaining,
        }
