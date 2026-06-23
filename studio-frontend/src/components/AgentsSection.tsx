import { useState } from "react";
import { api } from "../api/client";
import { short } from "../lib/format";
import { useStudio } from "../store";
import type { SectionProps } from "../sections/registry";
import { AgentCard } from "./AgentCard";
import { SectionHead } from "./SectionHead";

export function AgentsSection({ state }: SectionProps) {
  const { toast, log } = useStudio();
  const [name, setName] = useState("");
  const [fund, setFund] = useState("");
  const [busy, setBusy] = useState(false);
  const agents = state.agents;

  const create = async () => {
    const n = name.trim();
    if (!n) return toast("name required", "enter an agent name", "red");
    setBusy(true);
    log(`create agent <b>${n}</b> — minting KMS key, publishing card, registering NFT…`);
    try {
      const r = await api.createAgent(n, parseFloat(fund) || 0);
      log(`agent <b>${n}</b> → agentId <b>#${r.agent.agentId}</b>  ${short(r.agent.address)}`, "ok");
      toast("agent created", `${n} · #${r.agent.agentId}`, "green");
      setName("");
      setFund("");
    } catch (e) {
      log(`create failed: ${(e as Error).message}`, "er");
      toast("create failed", (e as Error).message, "red");
    }
    setBusy(false);
  };

  return (
    <div className="section">
      <SectionHead title="agents" count={agents.length ? `${agents.length} registered` : undefined} />
      <div className="console">
        <span className="pfx">▸</span>
        <input
          placeholder="new agent name  (e.g. price-watcher)"
          value={name}
          autoComplete="off"
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && create()}
        />
        <input
          className="mini"
          placeholder="fund USDT (0)"
          value={fund}
          autoComplete="off"
          onChange={(e) => setFund(e.target.value)}
        />
        <button className="btn g" disabled={busy} onClick={create}>
          {busy ? "creating…" : "create agent"}
        </button>
      </div>
      {agents.length === 0 ? (
        <div className="empty">
          no agents yet — create one above to give it a KMS key, an S3 card and an on-chain identity NFT.
        </div>
      ) : (
        <div className="agrid">
          {agents.map((a) => (
            <AgentCard key={a.name} agent={a} />
          ))}
        </div>
      )}
    </div>
  );
}
