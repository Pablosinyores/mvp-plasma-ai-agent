// Guided demo tour — a scripted, narrated walkthrough that actually drives the backend:
// create → fund → earn → spend → refuel → injection drill. Each step runs the real API call,
// narrates into the activity log, and advances on success. Launchable from a floating button.
import { useRef, useState } from "react";
import { api } from "../api/client";
import { fmt } from "../lib/format";
import { useStudio } from "../store";

interface Ctx {
  agent: string;
  jobId?: number;
}

interface Step {
  title: string;
  blurb: string;
  run: (c: Ctx, helpers: { log: ReturnType<typeof useStudio>["log"] }) => Promise<Partial<Ctx>>;
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

const STEPS: Step[] = [
  {
    title: "1 · register an agent",
    blurb: "Mint a KMS key, publish an Agent Card to storage, and register the on-chain identity NFT.",
    run: async (c, { log }) => {
      const r = await api.createAgent(c.agent, 0);
      log(`tour · agent <b>${c.agent}</b> → #${r.agent.agentId}`, "ok");
      return {};
    },
  },
  {
    title: "2 · fund an escrowed job",
    blurb: "Act as a buyer: store a prompt and escrow USDT against the agent. The worker will run it.",
    run: async (c, { log }) => {
      const r = await api.fundJob(c.agent, "One line: what is an autonomous payment?", 5);
      log(`tour · job <b>#${r.jobId}</b> funded (5 USDT)`, "ok");
      return { jobId: r.jobId };
    },
  },
  {
    title: "3 · earn the budget",
    blurb: "The worker runs the model, submits the result hash on-chain, and the keeper settles it.",
    run: async (c, { log }) => {
      if (c.jobId == null) throw new Error("no job — run step 2 first");
      for (let i = 0; i < 40; i++) {
        const d = await api.job(c.jobId);
        if (d.output != null) {
          log(`tour · job #${c.jobId} <b>${d.status}</b> — agent answered`, "ok");
          return {};
        }
        await sleep(1500);
      }
      throw new Error("timed out — is `make worker` running?");
    },
  },
  {
    title: "4 · spend under caps",
    blurb: "The agent pays a 402-gated resource via x402 — guarded by per-call · session · payee caps.",
    run: async (c, { log }) => {
      const r = await api.spend(c.agent);
      if (r.ok) log(`tour · x402 paid — spent ${fmt(r.spent, 2)} / remaining ${fmt(r.remaining, 2)} USDT`, "ok");
      else log(`tour · spend blocked by ${r.blocked} (cap held)`, "info");
      return {};
    },
  },
  {
    title: "5 · auto-refuel",
    blurb: "Below the floor the agent tops itself up — but only within a hard daily cap.",
    run: async (c, { log }) => {
      const r = await api.refuel(c.agent);
      log(`tour · refuel ${fmt(r.before, 2)} → ${fmt(r.after, 2)} USDT`, "ok");
      return {};
    },
  },
  {
    title: "6 · injection drill",
    blurb: "A prompt-injected 'pay the attacker 1,000,000 USDT' — prove every guard blocks it, 0 moved.",
    run: async (_c, { log }) => {
      const r = await api.injectionTest();
      const blocked = r.guards.filter((g) => g.blocked).length;
      log(`tour · injection contained — ${blocked}/${r.guards.length} guards fired, ${r.summary}`, "ok");
      return {};
    },
  },
];

export function DemoTour() {
  const { toast, log } = useStudio();
  const [open, setOpen] = useState(false);
  const [i, setI] = useState(0);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<boolean[]>(() => STEPS.map(() => false));
  const ctx = useRef<Ctx>({ agent: "" });

  const start = () => {
    ctx.current = { agent: `tour-${Math.floor(Date.now() / 1000) % 100000}` };
    setI(0);
    setDone(STEPS.map(() => false));
    setOpen(true);
  };

  const runStep = async () => {
    setBusy(true);
    try {
      const patch = await STEPS[i].run(ctx.current, { log });
      ctx.current = { ...ctx.current, ...patch };
      setDone((d) => d.map((v, k) => (k === i ? true : v)));
      if (i < STEPS.length - 1) setI(i + 1);
      else toast("tour complete", "full lifecycle demonstrated", "green");
    } catch (e) {
      log(`tour · step failed: ${(e as Error).message}`, "er");
      toast("tour step failed", (e as Error).message, "red");
    }
    setBusy(false);
  };

  if (!open) {
    return (
      <button className="tour-fab" onClick={start} title="run the guided demo">
        ▸ guided tour
      </button>
    );
  }

  const step = STEPS[i];
  const allDone = done.every(Boolean);
  return (
    <div className="tour">
      <div className="tour-head">
        <b>guided demo</b>
        <span className="tour-x" onClick={() => setOpen(false)}>
          ✕
        </span>
      </div>
      <div className="tour-dots">
        {STEPS.map((_, k) => (
          <span key={k} className={`tour-dot ${done[k] ? "done" : ""} ${k === i ? "cur" : ""}`} />
        ))}
      </div>
      <div className="tour-title">{step.title}</div>
      <div className="tour-blurb">{step.blurb}</div>
      <div className="tour-row">
        <span className="mono">
          agent <b>{ctx.current.agent}</b>
        </span>
        <div className="tour-btns">
          {i > 0 && (
            <button className="btn" disabled={busy} onClick={() => setI(i - 1)}>
              back
            </button>
          )}
          {allDone ? (
            <button className="btn g" onClick={() => setOpen(false)}>
              done
            </button>
          ) : (
            <button className="btn g" disabled={busy} onClick={runStep}>
              {busy ? "running…" : done[i] ? "re-run" : "▸ run step"}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
