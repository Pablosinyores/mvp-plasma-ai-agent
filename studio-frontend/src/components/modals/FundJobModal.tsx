import { useState } from "react";
import { api } from "../../api/client";
import { useStudio } from "../../store";

const DEFAULT_PROMPT =
  "Summarize in one sentence: agents that earn and spend stablecoins autonomously under on-chain escrow.";

export function FundJobModal({ name, onClose }: { name: string; onClose: () => void }) {
  const { toast, log } = useStudio();
  const [prompt, setPrompt] = useState(DEFAULT_PROMPT);
  const [budget, setBudget] = useState("5");
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    setBusy(true);
    const b = parseFloat(budget) || 5;
    log(`fund job for <b>${name}</b> — escrowing ${b} USDT…`);
    try {
      const r = await api.fundJob(name, prompt.trim(), b);
      log(`job <b>#${r.jobId}</b> funded for <b>${name}</b> (${b} USDT) — worker will run it`, "ok");
      toast("job funded", `#${r.jobId} · ${b} USDT`, "green");
      onClose();
    } catch (e) {
      log(`fund failed: ${(e as Error).message}`, "er");
      toast("fund failed", (e as Error).message, "red");
      setBusy(false);
    }
  };

  return (
    <div className="modal">
      <h3>fund job · {name}</h3>
      <div className="sub">escrow a budget; the worker runs the model &amp; the keeper settles on completion</div>
      <label>prompt</label>
      <textarea rows={3} value={prompt} onChange={(e) => setPrompt(e.target.value)} />
      <label>budget (USDT)</label>
      <input value={budget} onChange={(e) => setBudget(e.target.value)} />
      <div className="row">
        <button className="btn" onClick={onClose}>cancel</button>
        <button className="btn g" disabled={busy} onClick={submit}>
          {busy ? "funding…" : "fund & escrow"}
        </button>
      </div>
    </div>
  );
}
