// Job detail — fetched live from GET /api/jobs/{id}. Shows the escrow facts (client/provider/
// budget/status), the content-addressing proof (on-chain resultHash vs the S3 URI it commits to),
// and the actual model output the agent stored. If the job isn't submitted yet, it polls until the
// result lands, mirroring the playground's poll-until-answer loop.
import { useEffect, useRef, useState } from "react";
import { api } from "../../api/client";
import { fmt, short } from "../../lib/format";
import type { JobDetail } from "../../types";
import { JobTimeline } from "../JobTimeline";

const POLL_MS = 1500;
const MAX_POLLS = 40;
const PENDING = new Set(["FUNDED", "OPEN", "SUBMITTED"]);

export function JobDetailModal({ jobId, onClose }: { jobId: number; onClose: () => void }) {
  const [d, setD] = useState<JobDetail | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    let alive = true;
    let polls = 0;
    const stop = () => timer.current && clearInterval(timer.current);

    const tick = async () => {
      try {
        const j = await api.job(jobId);
        if (!alive) return;
        setD(j);
        // stop once the answer has landed or we hit a terminal/!pending state
        if (j.output != null || !PENDING.has(j.status) || ++polls >= MAX_POLLS) stop();
      } catch (e) {
        if (alive) setErr((e as Error).message);
        stop();
      }
    };

    tick();
    timer.current = setInterval(tick, POLL_MS);
    return () => {
      alive = false;
      stop();
    };
  }, [jobId]);

  return (
    <div className="modal">
      <h3>job · #{jobId}</h3>
      <div className="sub">escrow detail · on-chain receipt → content-addressed result</div>

      {err && <pre style={{ color: "var(--red)" }}>{err}</pre>}

      {!d && !err && <pre>▍ loading job #{jobId}…</pre>}

      {d && (
        <>
          <div className="play-meta" style={{ marginBottom: 12 }}>
            <span className={`pill p-${d.status}`}>{d.status}</span>
            <JobTimeline status={d.status} />
          </div>

          <div className="kv">budget&nbsp;&nbsp;&nbsp;<b>{fmt(d.budget)}</b>&nbsp;<span className="mono">USDT</span></div>
          <div className="kv">client&nbsp;&nbsp;&nbsp;<span className="mono" title={d.client}>{short(d.client)}</span></div>
          <div className="kv">provider&nbsp;<span className="mono" title={d.provider}>{short(d.provider)}</span></div>

          <label>on-chain result hash</label>
          <pre style={{ fontSize: 11 }}>{d.resultHash}</pre>

          <label>
            storage pointer{" "}
            {d.verified === true && <span style={{ color: "var(--green)" }}>✓ verified</span>}
            {d.verified === false && <span style={{ color: "var(--red)" }}>✗ hash mismatch</span>}
          </label>
          <pre style={{ fontSize: 11 }}>{d.uri || "— not submitted yet —"}</pre>

          <label>model output</label>
          <pre>
            {d.output ?? (PENDING.has(d.status) ? "▍ waiting for the agent to run the model…" : "— no result —")}
          </pre>
        </>
      )}

      <div className="row">
        <button className="btn" onClick={onClose}>close</button>
      </div>
    </div>
  );
}
