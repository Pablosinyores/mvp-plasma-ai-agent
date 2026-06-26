"""Trader — the continuous strategy loop that turns a standing instruction into recurring trades.

Holds ONE active strategy (set from a natural-language prompt via `intent.parse`) and runs it tick by
tick. Every trade goes through TradeGuard, so the loop can run unattended without becoming a
wallet-drain: the strategy decides WHAT, the guard decides whether it's PERMITTED.

Strategies:
  * dca       — buy `amount` of `sell` into `buy` every `everyTicks` ticks.
  * rebalance — each tick, value both legs in USDC and trade the over-weight leg back toward
                `targetBps` (% of total value held in `base`), within a 5% no-trade band.
  * swap      — one-off: trade once, then the strategy is spent.
  * limit     — conditional one-off: each tick, check the live spot price of `when.sym` against the
                threshold; fire the trade once the predicate (lt/gt) holds, then the order is spent.

`set_strategy()` swaps the active strategy live — that's the "dynamic prompt" entry point: a new
instruction can change what the agent is doing without restarting the loop.
"""
import time

REBALANCE_BAND_BPS = 500  # 5% no-trade band around the target


class Trader:
    def __init__(self, adapter, guard, strategy=None, store=None):
        self.adapter = adapter
        self.guard = guard
        self.store = store
        self.strategy = strategy
        self.prompt = None
        self.tick_count = 0
        self._swap_done = False
        # Rehydrate the last standing strategy (and its fire-state) for this agent on restart, so a
        # one-off swap / triggered limit that already fired is NOT re-fired. An explicit `strategy`
        # arg wins over whatever is persisted.
        if store is not None and strategy is None:
            rec = store.load(self.address)
            if rec:
                self.strategy = rec.get("strategy")
                self.prompt = rec.get("prompt")
                self.tick_count = int(rec.get("tickCount", 0))
                self._swap_done = bool(rec.get("swapDone", False))

    @property
    def address(self) -> str:
        return self.guard.account.address

    def _record(self) -> dict:
        return {"strategy": self.strategy, "prompt": self.prompt,
                "tickCount": self.tick_count, "swapDone": self._swap_done}

    def _persist(self) -> None:
        if self.store is not None:
            self.store.save(self.address, self._record())

    def set_strategy(self, strategy: dict, prompt: str = None):
        """Install a new standing strategy (from a freshly parsed prompt). Resets one-off state and
        persists so the instruction survives a restart."""
        self.strategy = strategy
        self.prompt = prompt
        self._swap_done = False
        self._persist()
        return strategy

    def clear_strategy(self) -> None:
        """Stop the agent: drop the standing strategy and remove it from the durable store."""
        self.strategy = None
        self.prompt = None
        self._swap_done = False
        if self.store is not None:
            self.store.delete(self.address)

    # --- helpers ---------------------------------------------------------------
    def _to_base(self, sym: str, human: float) -> int:
        return int(round(float(human) * (10 ** self.adapter.token_decimals(sym))))

    def _usdc_value(self, sym: str, bal: int) -> int:
        if bal <= 0:
            return 0
        if sym == "USDC":
            return int(bal)
        return self.adapter.quote_trade(sym, "USDC", bal)

    # --- the tick --------------------------------------------------------------
    def tick(self) -> dict:
        """Advance one tick, then persist tick_count + fire-state. Returns
        {action: 'trade'|'hold'|'blocked'|'noop', ...}."""
        r = self._do_tick()
        self._persist()
        return r

    def _do_tick(self) -> dict:
        self.tick_count += 1
        s = self.strategy
        if not s or s.get("op") == "noop":
            return {"action": "noop", "tick": self.tick_count, "reason": s.get("reason") if s else "no strategy"}

        try:
            if s["op"] == "swap":
                if self._swap_done:
                    return {"action": "hold", "tick": self.tick_count, "reason": "one-off swap already done"}
                res = self.guard.trade(s["sell"], s["buy"], self._to_base(s["sell"], s["amount"]))
                self._swap_done = True
                return {"action": "trade", "tick": self.tick_count, **res}

            if s["op"] == "limit":
                if self._swap_done:
                    return {"action": "hold", "tick": self.tick_count, "reason": "limit order already filled"}
                w = s["when"]
                price = self.adapter.spot_price(w["sym"])
                triggered = price < w["price"] if w["cmp"] == "lt" else price > w["price"]
                if not triggered:
                    return {"action": "hold", "tick": self.tick_count, "reason": "limit not triggered",
                            "watch": w["sym"], "cmp": w["cmp"], "price": price, "threshold": w["price"]}
                res = self.guard.trade(s["sell"], s["buy"], self._to_base(s["sell"], s["amount"]))
                self._swap_done = True
                return {"action": "trade", "tick": self.tick_count, "price": price, **res}

            if s["op"] == "dca":
                if self.tick_count % s["everyTicks"] != 0:
                    return {"action": "hold", "tick": self.tick_count, "reason": "not a DCA tick"}
                res = self.guard.trade(s["sell"], s["buy"], self._to_base(s["sell"], s["amount"]))
                return {"action": "trade", "tick": self.tick_count, **res}

            if s["op"] == "rebalance":
                return self._rebalance_tick(s)
        except Exception as e:  # noqa: BLE001 — guard rejections + transient errors surface, loop survives
            return {"action": "blocked", "tick": self.tick_count, "reason": str(e)}

        return {"action": "noop", "tick": self.tick_count, "reason": "unknown op"}

    def _rebalance_tick(self, s: dict) -> dict:
        base, quote, bps = s["base"], s["quote"], s["targetBps"]
        addr = self.guard.account.address
        base_bal = self.adapter.token_balance(base, addr)
        quote_bal = self.adapter.token_balance(quote, addr)
        base_val = self._usdc_value(base, base_bal)
        quote_val = self._usdc_value(quote, quote_bal)
        total = base_val + quote_val
        if total == 0:
            return {"action": "noop", "tick": self.tick_count, "reason": "nothing to rebalance"}

        target_base = total * bps // 10_000
        drift = base_val - target_base          # >0 => over-weight base
        band = total * REBALANCE_BAND_BPS // 10_000
        if abs(drift) <= band:
            return {"action": "hold", "tick": self.tick_count,
                    "reason": "within band ({}% base)".format(round(100 * base_val / total))}

        if drift > 0:  # too much base -> sell base into quote, ~drift USDC of notional
            amt = drift if base == "USDC" else (base_bal * drift) // max(1, base_val)
            res = self.guard.trade(base, quote, int(amt))
        else:          # too little base -> sell quote into base, ~(-drift) USDC of notional
            need = -drift
            amt = need if quote == "USDC" else (quote_bal * need) // max(1, quote_val)
            res = self.guard.trade(quote, base, int(amt))
        return {"action": "trade", "tick": self.tick_count, **res}

    # --- continuous loop -------------------------------------------------------
    def run(self, ticks: int, interval: float = 0.0, on_tick=None):
        """Run `ticks` iterations; call `on_tick(result)` for each. Returns the list of results."""
        out = []
        for _ in range(ticks):
            r = self.tick()
            out.append(r)
            if on_tick:
                on_tick(r)
            if interval:
                time.sleep(interval)
        return out
