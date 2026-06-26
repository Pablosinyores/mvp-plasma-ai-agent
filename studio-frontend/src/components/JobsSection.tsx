import { useEffect, useRef } from "react";
import { fmt, short } from "../lib/format";
import { useStudio } from "../store";
import type { SectionProps } from "../sections/registry";
import { JobTimeline } from "./JobTimeline";
import { JobDetailModal } from "./modals/JobDetailModal";
import { SectionHead } from "./SectionHead";

export function JobsSection({ state }: SectionProps) {
  const { log, openModal, closeModal } = useStudio();
  const prev = useRef<Record<number, string>>({});
  const firstPaint = useRef(true);
  const jobs = state.jobs;

  // log status transitions after commit (FUNDED → SUBMITTED → COMPLETED)
  useEffect(() => {
    for (const j of jobs) {
      const was = prev.current[j.jobId];
      if (was !== undefined && was !== j.status) {
        log(`job <b>#${j.jobId}</b> → <b>${j.status}</b>`, j.status === "COMPLETED" ? "ok" : "info", j.jobId);
      }
      prev.current[j.jobId] = j.status;
    }
    firstPaint.current = false;
  }, [jobs, log]);

  return (
    <div className="section">
      <SectionHead title="jobs" count={state.stats.jobCount ? `${state.stats.jobCount} total` : undefined} />
      <div style={{ border: "1px solid var(--line)", borderRadius: "var(--r)", overflow: "hidden" }}>
        <table className="tbl">
          <thead>
            <tr>
              <th>job</th>
              <th>status</th>
              <th>lifecycle</th>
              <th>provider</th>
              <th style={{ textAlign: "right" }}>budget</th>
            </tr>
          </thead>
          <tbody>
            {jobs.length === 0 ? (
              <tr>
                <td colSpan={5} className="mono" style={{ textAlign: "center", padding: 20 }}>
                  no jobs — fund one from an agent card
                </td>
              </tr>
            ) : (
              jobs.map((j) => {
                const was = prev.current[j.jobId];
                const changed = was !== undefined && was !== j.status;
                const isNew = was === undefined && !firstPaint.current;
                return (
                  <tr
                    key={j.jobId}
                    className={`clickable ${isNew ? "new" : ""}`}
                    onClick={() => openModal(<JobDetailModal jobId={j.jobId} onClose={closeModal} />)}
                    title="view result + on-chain receipt"
                  >
                    <td>#{j.jobId}</td>
                    <td>
                      <span className={`pill p-${j.status} ${changed ? "bump" : ""}`}>{j.status}</span>
                    </td>
                    <td>
                      <JobTimeline status={j.status} compact />
                    </td>
                    <td className="mono">{short(j.provider)}</td>
                    <td style={{ textAlign: "right" }}>
                      {fmt(j.budget)} <span className="mono">USDT</span>
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
