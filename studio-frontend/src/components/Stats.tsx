import { useCountUp } from "../hooks/useCountUp";
import type { Stats as StatsT } from "../types";

function Stat({ k, value, accent, decimals, unit }: {
  k: string; value: number; accent: string; decimals: number; unit?: string;
}) {
  const shown = useCountUp(value, decimals);
  return (
    <div className="stat" style={{ ["--accent" as string]: accent }}>
      <div className="k">{k}</div>
      <div className="v">
        {shown}
        {unit && <span className="u">{unit}</span>}
      </div>
    </div>
  );
}

export function Stats({ stats }: { stats: StatsT }) {
  return (
    <div className="stats">
      <Stat k="agents" value={stats.agentCount} accent="var(--blue)" decimals={0} />
      <Stat k="jobs" value={stats.jobCount} accent="var(--violet)" decimals={0} />
      <Stat k="earned" value={stats.earned} accent="var(--green)" decimals={2} unit="USDT" />
      <Stat k="spent" value={stats.spent} accent="var(--red)" decimals={2} unit="USDT" />
      <Stat k="refueled" value={stats.refueled} accent="var(--amber)" decimals={2} unit="USDT" />
    </div>
  );
}
