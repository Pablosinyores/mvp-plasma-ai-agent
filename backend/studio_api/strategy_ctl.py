"""TraderManager — the studio's agentic-trader control plane.

Holds one persisted `Trader` per agent and exposes the operations the strategy panel needs:
set a standing prompt, read the current strategy + recent ticks, clear it, and advance every active
trader one tick (driven from the studio's broadcast loop). Kept free of FastAPI/AWS so it is unit
testable with a plain local signer and the file-backed strategy store.

Security: every trader runs behind a default-capped TradeGuard with output pinned to the agent's own
address. The prompt only chooses WHAT to trade (via intent.parse, constrained to allow-listed
symbols); it can never widen the allow-list, raise caps, change the recipient, or lower slippage.
"""
from plasma_mvp import intent
from plasma_mvp.trade import TradeGuard
from plasma_mvp.trader import Trader

# fields worth surfacing to the UI from a raw tick result
_TICK_FIELDS = ("tick", "action", "reason", "price", "threshold", "watch", "cmp",
                "sell", "buy", "amountIn", "minOut", "notionalUsdc", "txHash")


def shape_tick(r: dict) -> dict:
    """Project a raw Trader.tick() result down to a compact, UI-friendly record."""
    return {k: r[k] for k in _TICK_FIELDS if k in r}


class TraderManager:
    def __init__(self, adapter, store, signer_for, ticks_kept: int = 20,
                 seed_usdc: int = 5_000_000_000, min_usdc: int = 1_000_000_000, gas_eth: float = 0.05):
        self.adapter = adapter
        self.store = store
        self.signer_for = signer_for           # name -> account (KMS-backed in prod, local in tests)
        self.ticks_kept = ticks_kept
        self.seed_usdc = seed_usdc
        self.min_usdc = min_usdc
        self.gas_eth = gas_eth
        self._traders = {}

    def _entry(self, name: str) -> dict:
        e = self._traders.get(name)
        if e is None:
            signer = self.signer_for(name)
            guard = TradeGuard(self.adapter, signer)        # default caps — never relaxed here
            trader = Trader(self.adapter, guard, store=self.store)
            e = self._traders[name] = {"signer": signer, "trader": trader, "ticks": []}
        return e

    def _seed(self, address: str) -> None:
        """Make sure the agent can actually act: a little gas + a trading balance to sell from."""
        try:
            self.adapter.fund_eth(address, self.gas_eth)
        except Exception:  # noqa: BLE001 — already funded / relayer quirk; not fatal
            pass
        if self.adapter.token_balance("USDC", address) < self.min_usdc:
            self.adapter.mint_token("USDC", address, self.seed_usdc)

    def set_strategy(self, name: str, prompt: str, model=None) -> dict:
        e = self._entry(name)
        order = intent.parse(prompt, model=model)
        self._seed(e["signer"].address)
        e["trader"].set_strategy(order, prompt=prompt)
        return order

    def get(self, name: str) -> dict:
        e = self._entry(name)
        tr = e["trader"]
        return {
            "address": e["signer"].address,
            "strategy": tr.strategy,
            "prompt": tr.prompt,
            "tickCount": tr.tick_count,
            "swapDone": tr._swap_done,
            "ticks": list(e["ticks"]),
        }

    def clear(self, name: str) -> None:
        e = self._entry(name)
        e["trader"].clear_strategy()
        e["ticks"].clear()

    def tick_active(self) -> dict:
        """Advance every agent that has a live strategy by one tick; record the result for the UI.
        Returns {name: raw_tick} for the ticks that ran. Never raises — a bad trader is skipped."""
        out = {}
        for name, e in list(self._traders.items()):
            if not e["trader"].strategy:
                continue
            try:
                r = e["trader"].tick()
            except Exception as ex:  # noqa: BLE001 — one bad agent must not stall the loop
                r = {"action": "blocked", "reason": str(ex)}
            buf = e["ticks"]
            buf.append(shape_tick(r))
            del buf[: -self.ticks_kept]      # keep only the last N
            out[name] = r
        return out
