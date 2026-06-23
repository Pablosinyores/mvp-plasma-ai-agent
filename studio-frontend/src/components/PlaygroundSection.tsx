// Agent playground — talk to your agent end to end:
// pick an agent → write a prompt → fund an escrowed job → the worker runs the model →
// we poll the job until its result lands, then stream the model's answer back into the console.
import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import { useStudio } from "../store";
import type { SectionProps } from "../sections/registry";
import { JobTimeline } from "./JobTimeline";
import { SectionHead } from "./SectionHead";

const DEFAULT_PROMPT = "In one sentence: why do autonomous agents need on-chain escrow to get paid?";
const POLL_MS = 1500;
const MAX_POLLS = 40; // ~60s ceiling; the worker tick + dispute window are short locally

export function PlaygroundSection({ state }: SectionProps) {
  const { toast, log } = useStudio();
  const agents = state.agents;
  const [agent, setAgent] = useState("");
  const [prompt, setPrompt] = useState(DEFAULT_PROMPT);
  const [budget, setBudget] = useState("5");
  const [busy, setBusy] = useState(false);
  const [jobId, setJobId] = useState<number | null>(null);
  const [status, setStatus] = useState<string>("");
  const [output, setOutput] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  // default the picker to the first agent once they load
  useEffect(() => {
    if (!agent && agents.length) setAgent(agents[0].name);
  }, [agents, agent]);

  // clean up any poll loop on unmount
  useEffect(() => () => void (timer.current && clearInterval(timer.current)), []);

  const stop = () => {
    if (timer.current) clearInterval(timer.current);
    timer.current = null;
  };

  const run = async () => {
    const name = agent || agents[0]?.name;
    if (!name) return toast("no agent", "create an agent first", "red");
    const p = prompt.trim();
    if (!p) return toast("prompt required", "type something to ask the agent", "red");

    stop();
    setBusy(true);
    setOutput(null);
    setStatus("FUNDING");
    setJobId(null);
    const b = parseFloat(budget) || 5;
    log(`playground · funding a ${b} USDT job for <b>${name}</b>…`);

    let id: number;
    try {
      const r = await api.fundJob(name, p, b);
      id = r.jobId;
      setJobId(id);
      setStatus("FUNDED");
      log(`playground · job <b>#${id}</b> funded — waiting for the worker to run the model…`, "info");
    } catch (e) {
      log(`playground · fund failed: ${(e as Error).message}`, "er");
      toast("run failed", (e as Error).message, "red");
      setBusy(false);
      setStatus("");
      return;
    }

    let polls = 0;
    timer.current = setInterval(async () => {
      polls += 1;
      try {
        const d = await api.job(id);
        setStatus(d.status);
        if (d.output != null) {
          setOutput(d.output);
          stop();
          setBusy(false);
          log(`playground · job <b>#${id}</b> answered (${d.status})`, "ok");
          toast("agent answered", `#${id} · ${d.status}`, "green");
          return;
        }
      } catch {
        /* transient — keep polling */
      }
      if (polls >= MAX_POLLS) {
        stop();
        setBusy(false);
        log(`playground · job <b>#${id}</b> timed out — is the worker running? (make worker)`, "er");
        toast("timed out", "no result yet — is `make worker` up?", "amber");
      }
    }, POLL_MS);
  };

  return (
    <div className="section">
      <SectionHead title="playground" count={agents.length ? undefined : "create an agent first"} />
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
          <label>budget (USDT)</label>
          <input className="mini" value={budget} onChange={(e) => setBudget(e.target.value)} />
          <button className="btn g" disabled={busy || !agents.length} onClick={run}>
            {busy ? "running…" : "▸ run"}
          </button>
        </div>
        <label className="play-lbl">prompt</label>
        <textarea
          rows={3}
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          placeholder="ask your agent anything…"
        />
        <div className="play-out">
          <div className="play-meta">
            {jobId != null ? (
              <>
                <span className="mono">job #{jobId}</span>
                <JobTimeline status={status} />
              </>
            ) : (
              <span className="mono">no run yet — fund a job to see the model answer</span>
            )}
          </div>
          <pre className="play-pre">
            {output ?? (busy ? "▍ waiting for the agent to run the model…" : "—")}
          </pre>
        </div>
      </div>
    </div>
  );
}
