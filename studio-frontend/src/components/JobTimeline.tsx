// Animated job-lifecycle stepper: FUNDED → SUBMITTED → COMPLETED.
// Shared by the jobs table (compact) and the playground (full). A terminal status
// (REJECTED / REFUNDED / EXPIRED) paints the track red.
const STEPS = ["FUNDED", "SUBMITTED", "COMPLETED"] as const;
const FAILED = new Set(["REJECTED", "REFUNDED", "EXPIRED"]);

export function JobTimeline({ status, compact = false }: { status: string; compact?: boolean }) {
  const idx = STEPS.indexOf(status as (typeof STEPS)[number]);
  const failed = FAILED.has(status);
  return (
    <div className={`tl ${compact ? "compact" : ""} ${failed ? "fail" : ""}`}>
      {STEPS.map((s, i) => {
        const done = idx >= 0 && i < idx;
        const active = i === idx;
        return (
          <div key={s} className={`tl-step ${done ? "done" : ""} ${active ? "active" : ""}`}>
            <span className="tl-dot" />
            {!compact && <span className="tl-lbl">{s.toLowerCase()}</span>}
            {i < STEPS.length - 1 && <span className={`tl-bar ${done ? "done" : ""}`} />}
          </div>
        );
      })}
    </div>
  );
}
