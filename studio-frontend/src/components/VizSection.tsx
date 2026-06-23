// Data-viz section — pure inline SVG, no chart lib.
//  • a multi-series sparkline of earned / spent / refueled over the live snapshot history
//  • per-agent USDT balance bars
//  • a compact spend-vs-refuel split bar
import { fmt } from "../lib/format";
import { useHistory } from "../hooks/useHistory";
import type { SectionProps } from "../sections/registry";
import type { Stats } from "../types";
import { SectionHead } from "./SectionHead";

const W = 320;
const H = 70;

function linePath(values: number[], max: number): string {
  if (values.length < 2) return "";
  const stepX = W / (values.length - 1);
  return values
    .map((v, i) => {
      const x = i * stepX;
      const y = H - (max <= 0 ? 0 : (v / max) * (H - 6)) - 3;
      return `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
}

function Spark({ hist }: { hist: Stats[] }) {
  const earned = hist.map((s) => s.earned);
  const spent = hist.map((s) => s.spent);
  const refueled = hist.map((s) => s.refueled);
  const max = Math.max(1, ...earned, ...spent, ...refueled);
  const series = [
    { key: "earned", color: "var(--green)", vals: earned },
    { key: "spent", color: "var(--red)", vals: spent },
    { key: "refueled", color: "var(--amber)", vals: refueled },
  ];
  return (
    <div className="viz-card">
      <div className="viz-h">flow over time</div>
      {hist.length < 2 ? (
        <div className="viz-wait">collecting live samples…</div>
      ) : (
        <svg className="spark" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none">
          {series.map((s) => (
            <path key={s.key} d={linePath(s.vals, max)} fill="none" stroke={s.color} strokeWidth={1.6} />
          ))}
        </svg>
      )}
      <div className="viz-legend">
        {series.map((s) => (
          <span key={s.key} style={{ color: s.color }}>
            ● {s.key} {fmt(s.vals[s.vals.length - 1] ?? 0, 2)}
          </span>
        ))}
      </div>
    </div>
  );
}

function AgentBars({ agents }: { agents: SectionProps["state"]["agents"] }) {
  const max = Math.max(1, ...agents.map((a) => a.usdt));
  return (
    <div className="viz-card">
      <div className="viz-h">USDT by agent</div>
      {agents.length === 0 ? (
        <div className="viz-wait">no agents yet</div>
      ) : (
        <div className="bars">
          {agents.map((a) => (
            <div key={a.name} className="bar-row">
              <span className="bar-lbl">{a.name}</span>
              <span className="bar-track">
                <span className="bar-fill" style={{ width: `${(a.usdt / max) * 100}%` }} />
              </span>
              <span className="bar-val">{fmt(a.usdt, 2)}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export function VizSection({ state }: SectionProps) {
  const hist = useHistory(state.stats, 80);
  return (
    <div className="section">
      <SectionHead title="analytics" />
      <div className="viz-grid">
        <Spark hist={hist} />
        <AgentBars agents={state.agents} />
      </div>
    </div>
  );
}
