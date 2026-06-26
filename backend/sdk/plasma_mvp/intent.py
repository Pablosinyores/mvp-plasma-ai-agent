"""intent — turn a natural-language trading prompt into a structured order/strategy.

Two backends, transparently:
  * LLM (model gateway): asks the model to emit strict JSON; used when MODEL_BACKEND != stub.
  * deterministic fallback: a keyword/regex parser that always works (and is what the stub model
    path uses), so the agentic trader is demoable with no model server running.

Output schema (one of):
  {"op": "swap",      "sell": SYM, "buy": SYM, "amount": float}
  {"op": "dca",       "sell": SYM, "buy": SYM, "amount": float, "everyTicks": int}
  {"op": "rebalance", "base": SYM, "quote": SYM, "targetBps": int}     # targetBps = % of value in base
  {"op": "limit",     "sell": SYM, "buy": SYM, "amount": float,
                      "when": {"sym": SYM, "cmp": "lt"|"gt", "price": float}}  # fire once when crossed
  {"op": "noop", "reason": str}

For "limit", `when.price` is USDC per 1 whole unit of `when.sym`; the trade fires once the live spot
price of `when.sym` is below ("lt") / above ("gt") that threshold, then the order is spent.

`amount` is in human units of the SELL token (e.g. 50 = 50 USDC); the trader converts to base units.
SYM is one of the allow-listed symbols (USDC / WETH / WXPL).
"""
import json
import re

SYMBOLS = ("USDC", "WETH", "WXPL")

# natural-language asset words -> canonical symbol (XPL is the native token, traded as WXPL)
_ALIASES = {
    "xpl": "WXPL", "wxpl": "WXPL", "plasma": "WXPL",
    "eth": "WETH", "weth": "WETH", "ether": "WETH", "ethereum": "WETH",
    "usdc": "USDC", "usd": "USDC", "usdt": "USDC", "dollar": "USDC", "dollars": "USDC",
    "stable": "USDC", "stablecoin": "USDC", "stables": "USDC",
}

_SYSTEM = (
    "You convert a user's crypto trading instruction into ONE JSON object and nothing else. "
    "Allowed symbols: USDC, WETH, WXPL (WXPL is wrapped XPL; treat 'XPL'/'ETH' accordingly). "
    'Schema: {"op":"swap","sell":SYM,"buy":SYM,"amount":NUMBER} for a one-off trade; '
    '{"op":"dca","sell":SYM,"buy":SYM,"amount":NUMBER,"everyTicks":INT} for recurring buys; '
    '{"op":"rebalance","base":SYM,"quote":SYM,"targetBps":INT} to hold targetBps%% of value in base; '
    '{"op":"limit","sell":SYM,"buy":SYM,"amount":NUMBER,'
    '"when":{"sym":SYM,"cmp":"lt"|"gt","price":NUMBER}} for a conditional order that fires once when '
    "the spot price of when.sym (in USDC per 1 whole token) crosses below (lt) / above (gt) price. "
    "amount is in units of the sell token. Reply with JSON only."
)


def _sym(word: str):
    return _ALIASES.get(word.lower().strip("$.,"))


def _first_json(text: str):
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:  # noqa: BLE001
        return None


def _validate(d: dict):
    """Coerce + validate a parsed dict into a clean order, or return None."""
    if not isinstance(d, dict):
        return None
    op = str(d.get("op", "")).lower()
    try:
        if op == "swap":
            return {"op": "swap", "sell": _req_sym(d["sell"]), "buy": _req_sym(d["buy"]),
                    "amount": float(d["amount"])}
        if op == "dca":
            return {"op": "dca", "sell": _req_sym(d["sell"]), "buy": _req_sym(d["buy"]),
                    "amount": float(d["amount"]), "everyTicks": max(1, int(d.get("everyTicks", 1)))}
        if op == "rebalance":
            bps = int(d["targetBps"])
            if not (0 <= bps <= 10_000):
                return None
            return {"op": "rebalance", "base": _req_sym(d["base"]), "quote": _req_sym(d["quote"]),
                    "targetBps": bps}
        if op == "limit":
            sell, buy = _req_sym(d["sell"]), _req_sym(d["buy"])
            w = d["when"]
            cmp = _norm_cmp(w["cmp"])
            if cmp is None:
                return None
            # default the watched symbol to the non-USDC leg of the trade
            watch = w.get("sym")
            watch = _req_sym(watch) if watch else (buy if buy != "USDC" else sell)
            price = float(w["price"])
            if price <= 0:
                return None
            return {"op": "limit", "sell": sell, "buy": buy, "amount": float(d["amount"]),
                    "when": {"sym": watch, "cmp": cmp, "price": price}}
    except (KeyError, ValueError, TypeError):
        return None
    return None


# comparison phrase -> canonical "lt" / "gt" (substring match handles "<", "<=", "drops below", ...)
_CMP_LT = ("<", "below", "under", "less", "drop", "dip", "fall", "lt")
_CMP_GT = (">", "above", "over", "greater", "more", "exceed", "rise", "hit", "reach", "gt")


def _norm_cmp(token) -> str:
    t = str(token).lower().strip()
    if any(w in t for w in _CMP_LT):
        return "lt"
    if any(w in t for w in _CMP_GT):
        return "gt"
    return None


def _req_sym(v: str) -> str:
    s = str(v).upper()
    if s in SYMBOLS:
        return s
    a = _sym(str(v))
    if a is None:
        raise ValueError("unknown symbol: {}".format(v))
    return a


# --- deterministic fallback ------------------------------------------------------------------------

def _find_symbols(text: str):
    """Return symbols in mention order (deduped)."""
    out = []
    for w in re.findall(r"[A-Za-z$]+", text):
        s = _sym(w)
        if s and s not in out:
            out.append(s)
    return out


def _find_amount(text: str):
    m = re.search(r"(\d+(?:\.\d+)?)", text.replace(",", ""))
    return float(m.group(1)) if m else None


# conditional clause: "... when price < 0.09", "when it drops below $0.1", "if price >= 0.12"
_WHEN_RE = re.compile(
    r"\b(?:when|once|if)\b[^<>0-9]*?"
    r"(<=|>=|<|>|below|under|above|over|less\s+than|greater\s+than|more\s+than|"
    r"drops?\s+below|dips?\s+below|falls?\s+below|rises?\s+above|exceeds?|hits|reaches)\s*"
    r"\$?(\d+(?:\.\d+)?)",
    re.I,
)


def _parse_when(prompt: str):
    """Find a price-threshold clause. Returns (cmp, price, prompt_without_clause) or None."""
    m = _WHEN_RE.search(prompt)
    if not m:
        return None
    cmp = _norm_cmp(m.group(1))
    if cmp is None:
        return None
    price = float(m.group(2))
    rest = (prompt[: m.start()] + " " + prompt[m.end():]).strip()
    return cmp, price, rest


def _parse_trade_leg(text: str):
    """Parse the trade portion of an order into (sell, buy, amount), or None.
    Handles an implicit USDC counterparty for single-asset 'buy/sell ASSET' phrasing."""
    syms = _find_symbols(text)
    amt = _find_amount(text)
    if amt is None:
        return None
    t = text.lower()
    if len(syms) >= 2:
        sell, buy = _orient(t, syms)
        return sell, buy, amt
    if len(syms) == 1:
        asset = syms[0]
        if asset != "USDC" and re.search(r"\bsell\b|\bdump\b|\bexit\b|\bclose\b", t):
            return asset, "USDC", amt          # sell ASSET -> USDC; amount in ASSET units
        if asset != "USDC":
            return "USDC", asset, amt          # buy ASSET with USDC; amount in USDC units
    return None


def fallback_parse(prompt: str):
    t = prompt.lower()
    syms = _find_symbols(prompt)

    # limit / conditional order: a price-threshold clause gates a one-off trade
    w = _parse_when(prompt)
    if w:
        cmp, price, rest = w
        leg = _parse_trade_leg(rest)
        if leg:
            sell, buy, amt = leg
            watch = buy if buy != "USDC" else sell
            return {"op": "limit", "sell": sell, "buy": buy, "amount": amt,
                    "when": {"sym": watch, "cmp": cmp, "price": price}}

    # rebalance: "rebalance", "keep", a percentage + two assets
    pct = re.search(r"(\d+(?:\.\d+)?)\s*%", t)
    if ("rebalance" in t or "keep" in t or "ratio" in t or "target" in t) and pct and len(syms) >= 2:
        base, quote = syms[0], syms[1]
        return {"op": "rebalance", "base": base, "quote": quote,
                "targetBps": int(float(pct.group(1)) * 100)}

    # dca: "dca", "every", "each tick", "recurring", "per tick"
    if re.search(r"\bdca\b|every|each\s+tick|recurring|per\s+tick", t) and len(syms) >= 2:
        every = 1
        m = re.search(r"every\s+(\d+)", t)
        if m:
            every = max(1, int(m.group(1)))
        sell, buy = _orient(t, syms)
        amt = _find_amount(prompt)
        if amt:
            return {"op": "dca", "sell": sell, "buy": buy, "amount": amt, "everyTicks": every}

    # one-off swap: "swap/buy/sell/trade/convert"
    if re.search(r"\bswap\b|\bbuy\b|\bsell\b|\btrade\b|\bconvert\b|->|for ", t) and len(syms) >= 2:
        sell, buy = _orient(t, syms)
        amt = _find_amount(prompt)
        if amt:
            return {"op": "swap", "sell": sell, "buy": buy, "amount": amt}

    return {"op": "noop", "reason": "could not parse a trade from: {!r}".format(prompt)}


def _orient(t: str, syms):
    """Decide which mentioned symbol is sold vs bought from phrasing."""
    sell, buy = syms[0], syms[1]
    # "buy X with/from/using Y" or "X for Y" / "Y -> X": flip if a buy-verb precedes the first symbol
    # Heuristic: "buy <BUY> with <SELL>"; "sell <SELL> for <BUY>"; "swap <SELL> for/to <BUY>".
    if re.search(r"buy\b", t) and re.search(r"\bwith\b|\bfrom\b|\busing\b", t):
        # first symbol is the BUY target, second is the funding (SELL) asset
        return buy, sell
    return sell, buy


# --- public entrypoint -----------------------------------------------------------------------------

def parse(prompt: str, model=None):
    """Parse `prompt` into a structured order. Tries the LLM first (unless it's the stub backend),
    then the deterministic fallback. Always returns a dict (op may be 'noop')."""
    backend = getattr(model, "backend", None)
    if model is not None and backend and backend != "stub":
        try:
            raw = model.complete(prompt, system=_SYSTEM)
            parsed = _validate(_first_json(raw) or {})
            if parsed:
                return parsed
        except Exception:  # noqa: BLE001 — fall through to the deterministic parser
            pass
    return fallback_parse(prompt)
