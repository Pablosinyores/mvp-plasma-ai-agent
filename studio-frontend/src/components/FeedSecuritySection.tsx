import { fmt, short } from "../lib/format";
import type { SectionProps } from "../sections/registry";
import { SecurityDrill } from "./SecurityDrill";
import { SectionHead } from "./SectionHead";

export function FeedSecuritySection({ state }: SectionProps) {
  const events = state.events;
  return (
    <div className="section dual">
      <div>
        <SectionHead title="spend / refuel feed" />
        <div className="feed">
          {events.length === 0 ? (
            <div className="empty">no spend or refuel yet</div>
          ) : (
            events.map((e, i) => (
              <div className={`ev ${e.kind}`} key={`${e.kind}-${e.to}-${i}`}>
                <span className="tag">{e.kind}</span>
                <span className="amt">
                  {e.kind === "spend" ? "−" : "+"}
                  {fmt(e.amount)} USDT
                </span>
                <span className="flow">
                  {short(e.from)} <span className="arr">→</span> {short(e.to)}
                </span>
              </div>
            ))
          )}
        </div>
      </div>
      <div>
        <SectionHead title="security drill" />
        <SecurityDrill />
      </div>
    </div>
  );
}
