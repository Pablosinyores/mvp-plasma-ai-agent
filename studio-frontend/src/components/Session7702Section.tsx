// EIP-7702 panel — "trade from your OWN wallet". The connected wallet authorizes a scoped, revocable
// session ONCE (it delegates its code to AgentSessionDelegate + installs a money-bound policy). After
// that the agent runs a standing strategy that executes FROM the user's own address: funds are debited
// from the user, output returns to the user, and every cap is enforced on-chain. The agent custodies
// nothing and a Revoke is one click. (In this local demo the backend plays the wallet's signing role
// for the well-known Anvil accounts; in production the wallet signs the delegation + install itself.)
import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import { short } from "../lib/format";
import type { SectionProps } from "../sections/registry";
import { useStudio } from "../store";
import { useWallet } from "../wallet/WalletContext";
import {
  authorizeWithInjectedWallet,
  connectedAddress,
  hasInjectedWallet,
  revokeWithInjectedWallet,
} from "../wallet/eip7702";
import type { SessionState, StrategyOrder, StrategyTick } from "../types";
import { SectionHead } from "./SectionHead";

const USDC = 1_000_000;
const DEFAULT_PROMPT = "DCA buy 40 USDC of XPL every 1 tick";
const POLL_MS = 1500;

function describe(o: StrategyOrder | null | undefined): string {
  if (!o) return "— no standing strategy —";
  switch (o.op) {
    case "dca":
      return `DCA · ${o.amount} ${o.sell} → ${o.buy} every ${o.everyTicks} tick(s)`;
    case "swap":
      return `one-off swap · ${o.amount} ${o.sell} → ${o.buy}`;
    case "limit":
      return `limit · ${o.amount} ${o.sell} → ${o.buy} when ${o.when?.sym} ${
        o.when?.cmp === "lt" ? "<" : ">"
      } ${o.when?.price} USDC`;
    case "rebalance":
      return `rebalance · ${(o.targetBps ?? 0) / 100}% ${o.base} vs ${o.quote}`;
    default:
      return o.reason ?? "noop";
  }
}

function tickDetail(t: StrategyTick): string {
  const px = t.price != null ? t.price.toFixed(6) : null;
  if (t.action === "trade") return `${t.sell ?? ""}→${t.buy ?? ""}${px ? ` @ ${px}` : ""}`;
  if (t.action === "hold") return `${t.reason ?? ""}${px ? ` (px ${px})` : ""}`;
  return t.reason ?? "";
}

export function Session7702Section(_: SectionProps) {
  const { toast, log } = useStudio();
  const { wallet, connected } = useWallet();
  const demoUser = wallet?.address ?? "";

  const [maxPer, setMaxPer] = useState(100); // USDC per trade
  const [cap, setCap] = useState(250); // USDC session cap
  const [slippage, setSlippage] = useState(100); // bps
  const [prompt, setPrompt] = useState(DEFAULT_PROMPT);
  const [busy, setBusy] = useState(false);
  const [sess, setSess] = useState<SessionState | null>(null);
  // real-wallet (production) EIP-7702 path vs the local demo affordance
  const [real, setReal] = useState(false);
  const [injectedAddr, setInjectedAddr] = useState("");
  const canReal = hasInjectedWallet();
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  // the address the panel is operating on: the connected browser wallet in real mode, else the demo one
  const user = real && injectedAddr ? injectedAddr : demoUser;

  const installed = !!sess?.installed;
  const policy = sess?.policy ?? null;

  // poll the session state for the connected wallet
  useEffect(() => {
    const stop = () => {
      if (timer.current) clearInterval(timer.current);
    };
    if (!user) {
      setSess(null);
      return stop;
    }
    let alive = true;
    const tick = async () => {
      try {
        const s = await api.sessionGet(user);
        if (alive) setSess(s);
      } catch {
        /* transient */
      }
    };
    tick();
    timer.current = setInterval(tick, POLL_MS);
    return () => {
      alive = false;
      stop();
    };
  }, [user]);

  const policyArgs = () => ({
    maxInPerTrade: Math.round(maxPer * USDC),
    sessionInCap: Math.round(cap * USDC),
    slippageBps: slippage,
    buys: ["WXPL"],
  });

  const authorize = async () => {
    setBusy(true);
    try {
      if (real) {
        // PRODUCTION: the connected browser wallet signs the 7702 delegation + installSession itself
        const addr = await connectedAddress();
        setInjectedAddr(addr);
        const a = await api.sessionAuthorize(addr, policyArgs());
        log(`7702 · session key <b>${short(a.sessionKey)}</b> minted; confirm in your wallet…`, "info");
        const hash = await authorizeWithInjectedWallet(a); // one type-4 tx: delegate + installSession
        log(`7702 · wallet delegated + installed on-chain · tx ${short(hash)}`, "ok");
        const s = await api.sessionInstalled(addr);
        setSess(s);
        toast("agent authorized", `trading from your wallet ${short(addr)}`, "green");
        return;
      }
      if (!user) return toast("connect a wallet", "pick a wallet to authorize", "red");
      // DEMO: backend plays the wallet (delegate + install + seed) for the deterministic anvil accounts
      const a = await api.sessionAuthorize(user, policyArgs());
      log(`7702 · session key <b>${short(a.sessionKey)}</b> minted for <b>${short(user)}</b>`, "info");
      const s = await api.sessionBootstrap(user);
      setSess(s);
      log(`7702 · <b>${short(user)}</b> delegated + session installed on-chain (demo)`, "ok");
      toast("agent authorized", `trading from ${short(user)}`, "green");
    } catch (e) {
      log(`7702 · authorize failed: ${(e as Error).message}`, "er");
      toast("authorize failed", (e as Error).message, "red");
    } finally {
      setBusy(false);
    }
  };

  const setStrategy = async () => {
    if (!user) return;
    const p = prompt.trim();
    if (!p) return toast("prompt required", "type a trading instruction", "red");
    setBusy(true);
    try {
      const s = await api.sessionSetStrategy(user, p);
      setSess(s);
      toast("strategy set", `${s.strategy?.op ?? "?"} from your wallet`, "green");
      log(`7702 · strategy <b>${s.strategy?.op ?? "?"}</b> running from <b>${short(user)}</b>`, "ok");
    } catch (e) {
      toast("set failed", (e as Error).message, "red");
    } finally {
      setBusy(false);
    }
  };

  const stop = async () => {
    if (!user) return;
    setBusy(true);
    try {
      await api.sessionStop(user);
      setSess((s) => (s ? { ...s, strategy: null } : s));
      toast("strategy stopped", short(user), "amber");
    } catch (e) {
      toast("stop failed", (e as Error).message, "red");
    } finally {
      setBusy(false);
    }
  };

  const revoke = async () => {
    if (!user) return;
    setBusy(true);
    try {
      if (real && sess?.sessionKey) {
        // PRODUCTION: the wallet submits revokeSession on-chain itself; backend just stops the loop
        const hash = await revokeWithInjectedWallet(user as `0x${string}`, sess.sessionKey as `0x${string}`);
        log(`7702 · revoke submitted in wallet · tx ${short(hash)}`, "info");
        await api.sessionStop(user);
      } else {
        await api.sessionRevoke(user); // DEMO: backend submits revoke on the demo wallet's behalf
      }
      const s = await api.sessionGet(user);
      setSess(s);
      log(`7702 · session revoked for <b>${short(user)}</b> — agent can no longer trade`, "info");
      toast("session revoked", "agent access killed on-chain", "amber");
    } catch (e) {
      toast("revoke failed", (e as Error).message, "red");
    } finally {
      setBusy(false);
    }
  };

  const spentPct = policy ? Math.min(100, (policy.spentIn / Math.max(1, policy.sessionInCap)) * 100) : 0;
  const ticks = sess?.ticks ?? [];

  return (
    <div className="section">
      <SectionHead title="trade from your wallet" count={connected ? undefined : "connect a wallet"} />
      <div className="play">
        <div className="sess7702-banner mono">
          {connected ? (
            <>
              wallet <b>{short(user)}</b> · {installed ? "agent AUTHORIZED — trading from your address" : "not yet authorized"}
            </>
          ) : (
            <>connect a wallet to authorize a scoped trading agent over your own funds</>
          )}
        </div>

        {!installed && (
          <>
            <label className="sess7702-mode mono">
              <input
                type="checkbox"
                checked={real}
                disabled={!canReal}
                onChange={(e) => setReal(e.target.checked)}
              />
              sign with my browser wallet (real EIP-7702)
              {!canReal && <span className="sess7702-hint"> · no injected wallet detected — using demo</span>}
            </label>
            <label className="play-lbl">session policy (enforced on-chain, oracle-free in USDC)</label>
            <div className="sess7702-policy">
              <label>
                per-trade cap
                <span className="mono">
                  <input type="number" min={1} value={maxPer} onChange={(e) => setMaxPer(+e.target.value)} /> USDC
                </span>
              </label>
              <label>
                session cap
                <span className="mono">
                  <input type="number" min={1} value={cap} onChange={(e) => setCap(+e.target.value)} /> USDC
                </span>
              </label>
              <label>
                max slippage
                <span className="mono">
                  <input type="number" min={0} max={10000} value={slippage} onChange={(e) => setSlippage(+e.target.value)} /> bps
                </span>
              </label>
            </div>
            <div className="play-ctrl">
              <button className="btn g" disabled={busy || (real ? !canReal : !connected)} onClick={authorize}>
                {busy ? "…" : real ? "▸ authorize with wallet" : "▸ authorize agent"}
              </button>
              <span className="sess7702-hint mono">buys WXPL · output pinned to your wallet · revocable</span>
            </div>
          </>
        )}

        {installed && (
          <>
            <div className="sess7702-keys mono">
              <span>session key <b>{short(sess?.sessionKey ?? "")}</b></span>
              <span>delegate <b>{short(sess?.delegate ?? "")}</b></span>
            </div>

            {policy && (
              <div className="sess7702-cap">
                <div className="sess7702-cap-bar">
                  <div className="sess7702-cap-fill" style={{ width: `${spentPct}%` }} />
                </div>
                <span className="mono">
                  spent {(policy.spentIn / USDC).toFixed(1)} / {(policy.sessionInCap / USDC).toFixed(0)} USDC
                  {" · "}≤ {(policy.maxInPerTrade / USDC).toFixed(0)}/trade · {policy.maxSlippageBps} bps
                </span>
              </div>
            )}

            <label className="play-lbl">standing prompt (decides WHAT/WHEN — caps decide ALLOWED)</label>
            <textarea rows={2} value={prompt} onChange={(e) => setPrompt(e.target.value)} />
            <div className="play-ctrl">
              <button className="btn g" disabled={busy} onClick={setStrategy}>
                {busy ? "…" : "▸ run from my wallet"}
              </button>
              <button className="btn" disabled={busy || !sess?.strategy} onClick={stop}>
                ■ stop
              </button>
              <button className="btn r" disabled={busy} onClick={revoke}>
                ⦸ revoke session
              </button>
            </div>

            <div className="strat-now">
              <span className="mono">{describe(sess?.strategy)}</span>
              {sess?.strategy && (
                <span className="strat-tag">
                  tick {sess?.tickCount ?? 0}
                  {sess?.swapDone ? " · filled" : ""}
                </span>
              )}
            </div>

            <label className="play-lbl">recent ticks (each fills from your address)</label>
            <div className="strat-ticks">
              {ticks.length === 0 ? (
                <pre className="play-pre">— no ticks yet —</pre>
              ) : (
                ticks
                  .slice()
                  .reverse()
                  .map((t, i) => (
                    <div key={i} className={`strat-tick t-${t.action}`}>
                      <span className="strat-tick-n mono">#{t.tick ?? "—"}</span>
                      <span className="strat-tick-a">{t.action}</span>
                      <span className="strat-tick-d mono">{tickDetail(t)}</span>
                      {t.from && (
                        <span className="strat-tick-x mono" title={t.from}>
                          from {short(t.from)}
                        </span>
                      )}
                    </div>
                  ))
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
