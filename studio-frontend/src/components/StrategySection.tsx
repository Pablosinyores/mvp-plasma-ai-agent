// Strategy panel — give an agent a standing trading instruction and watch it act, tick by tick.
// Pick an agent → write a prompt (DCA / rebalance / one-off swap / conditional "limit" order) →
// "set strategy" parses + installs it (persisted server-side). We then poll the agent's strategy +
// recent ticks so you can watch each tick land live (action · pair · price). "stop" clears it.
import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import { short } from "../lib/format";
import type { SectionProps } from "../sections/registry";
import { useStudio } from "../store";
import type { StrategyOrder, StrategyTick } from "../types";
import { SectionHead } from "./SectionHead";

const DEFAULT_PROMPT = "DCA buy 100 USDC of XPL every 1 tick";
const POLL_MS = 1500;

function describe(o: StrategyOrder | null): string {
  if (!o) return "— no standing strategy —";
  switch (o.op) {
    case "dca":
      return `DCA · sell ${o.amount} ${o.sell} → ${o.buy} every ${o.everyTicks} tick(s)`;
    case "swap":
      return `one-off swap · ${o.amount} ${o.sell} → ${o.buy}`;
    case "rebalance":
      return `rebalance · hold ${(o.targetBps ?? 0) / 100}% ${o.base} vs ${o.quote}`;
    case "limit":
      return `limit · ${o.amount} ${o.sell} → ${o.buy} when ${o.when?.sym} ${
        o.when?.cmp === "lt" ? "<" : ">"
      } ${o.when?.price} USDC`;
    default:
      return o.reason ?? "noop — could not parse a trade";
  }
}

function tickDetail(t: StrategyTick): string {
  const px = t.price != null ? t.price.toFixed(6) : null;
  if (t.action === "trade") {
    const pair = t.sell && t.buy ? `${t.sell}→${t.buy}` : "";
    return `${pair}${px ? ` @ ${px}` : ""}`;
  }
  if (t.action === "hold") {
    return `${t.reason ?? ""}${px ? ` (px ${px})` : ""}`;
  }
  return t.reason ?? "";
}

export function StrategySection({ state }: SectionProps) {
  const { toast, log } = useStudio();
  const agents = state.agents;
  const [agent, setAgent] = useState("");
  const [prompt, setPrompt] = useState(DEFAULT_PROMPT);
  const [busy, setBusy] = useState(false);
  const [strategy, setStrategy] = useState<StrategyOrder | null>(null);
  const [ticks, setTicks] = useState<StrategyTick[]>([]);
  const [meta, setMeta] = useState<{ tickCount: number; swapDone: boolean }>({ tickCount: 0, swapDone: false });
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  // default the picker to the first agent once they load
  useEffect(() => {
    if (!agent && agents.length) setAgent(agents[0].name);
  }, [agents, agent]);

  // poll the selected agent's strategy + recent ticks
  useEffect(() => {
    const stop = () => {
      if (timer.current) clearInterval(timer.current);
    };
    if (!agent) {
      setStrategy(null);
      setTicks([]);
      return stop;
    }
    let alive = true;
    const tick = async () => {
      try {
        const s = await api.getStrategy(agent);
        if (!alive) return;
        setStrategy(s.strategy);
        setTicks(s.ticks ?? []);
        setMeta({ tickCount: s.tickCount, swapDone: s.swapDone });
      } catch {
        /* transient — keep polling */
      }
    };
    tick();
    timer.current = setInterval(tick, POLL_MS);
    return () => {
      alive = false;
      stop();
    };
  }, [agent]);

  const install = async () => {
    if (!agent) return toast("no agent", "create an agent first", "red");
    const p = prompt.trim();
    if (!p) return toast("prompt required", "type a trading instruction", "red");
    setBusy(true);
    try {
      const r = await api.setStrategy(agent, p);
      setStrategy(r.strategy);
      setTicks(r.ticks ?? []);
      setMeta({ tickCount: r.tickCount, swapDone: r.swapDone });
      const op = r.order?.op ?? r.strategy?.op ?? "?";
      log(`strategy · <b>${agent}</b> set to <b>${op}</b>`, "ok");
      toast("strategy set", `${agent} · ${op}`, "green");
    } catch (e) {
      log(`strategy · set failed: ${(e as Error).message}`, "er");
      toast("set failed", (e as Error).message, "red");
    } finally {
      setBusy(false);
    }
  };

  const stopStrategy = async () => {
    if (!agent) return;
    setBusy(true);
    try {
      await api.clearStrategy(agent);
      setStrategy(null);
      setTicks([]);
      setMeta({ tickCount: 0, swapDone: false });
      log(`strategy · <b>${agent}</b> stopped`, "info");
      toast("strategy stopped", agent, "amber");
    } catch (e) {
      toast("stop failed", (e as Error).message, "red");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="section">
      <SectionHead title="strategy" count={agents.length ? undefined : "create an agent first"} />
      <div className="play">
        <div className="play-ctrl">
          <label>agent</label>
          <select value={agent} onChange={(e) => setAgent(e.target.value)} disabled={!agents.length}>
            {agents.length === 0 && <option>— none —</option>}
            {agents.map((a) => (
              <option key={a.name} value={a.name}>
                {a.name} · #{a.agentId}
              </option>
            ))}
          </select>
          <button className="btn g" disabled={busy || !agents.length} onClick={install}>
            {busy ? "…" : "▸ set strategy"}
          </button>
          <button className="btn" disabled={busy || !strategy} onClick={stopStrategy}>
            ■ stop
          </button>
        </div>

        <label className="play-lbl">standing prompt</label>
        <textarea
          rows={2}
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          placeholder="e.g. buy 100 USDC of XPL when price < 0.09"
        />

        <div className="strat-now">
          <span className="mono">{describe(strategy)}</span>
          {strategy && (
            <span className="strat-tag">
              tick {meta.tickCount}
              {meta.swapDone ? " · filled" : ""}
            </span>
          )}
        </div>

        <label className="play-lbl">recent ticks</label>
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
                  {t.txHash && (
                    <span className="strat-tick-x mono" title={t.txHash}>
                      {short(t.txHash)}
                    </span>
                  )}
                </div>
              ))
          )}
        </div>
      </div>
    </div>
  );
}
