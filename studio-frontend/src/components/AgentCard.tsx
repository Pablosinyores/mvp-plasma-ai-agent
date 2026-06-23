import { useState } from "react";
import { api } from "../api/client";
import { fmt, short } from "../lib/format";
import { useStudio } from "../store";
import type { Agent } from "../types";
import { FundJobModal } from "./modals/FundJobModal";
import { ResolveModal } from "./modals/ResolveModal";

export function AgentCard({ agent }: { agent: Agent }) {
  const { toast, log, openModal, closeModal } = useStudio();
  const [busy, setBusy] = useState<string | null>(null);
  const [flash, setFlash] = useState(false);

  const doFlash = () => {
    setFlash(false);
    requestAnimationFrame(() => setFlash(true));
  };

  const copyAddr = () => {
    navigator.clipboard?.writeText(agent.address);
    toast("copied", agent.address);
  };

  const resolve = async () => {
    log(`resolve <b>${agent.name}</b> — reading identity NFT + S3 card…`);
    try {
      const r = await api.resolve(agent.name);
      openModal(<ResolveModal r={r} name={agent.name} onClose={closeModal} />);
      log(`resolved <b>${agent.name}</b> → owner ${short(r.owner)}`, "ok");
    } catch (e) {
      log(`resolve failed: ${(e as Error).message}`, "er");
      toast("resolve failed", (e as Error).message, "red");
    }
  };

  const fund = () =>
    openModal(<FundJobModal name={agent.name} onClose={closeModal} />);

  const spend = async () => {
    setBusy("spend");
    log(`x402 spend for <b>${agent.name}</b> — paying a 402-gated resource under caps…`);
    try {
      const r = await api.spend(agent.name);
      if (r.ok) {
        log(
          `paid resource — status <b>${r.status}</b>, signer spent <b>${fmt(r.spent, 2)}</b> USDT (remaining ${fmt(r.remaining, 2)})`,
          "ok",
        );
        toast("x402 paid", `spent ${fmt(r.spent, 2)} · remaining ${fmt(r.remaining, 2)} USDT`, "blue");
        doFlash();
      } else {
        log(`spend BLOCKED by <b>${r.blocked}</b> — ${r.reason} (session spent ${fmt(r.spent, 2)} USDT)`, "er");
        toast("spend blocked", `${r.blocked} — cap held`, "amber");
      }
    } catch (e) {
      log(`spend failed: ${(e as Error).message}`, "er");
      toast("spend failed", (e as Error).message, "red");
    }
    setBusy(null);
  };

  const refuel = async () => {
    setBusy("refuel");
    log(`auto-refuel for <b>${agent.name}</b> — top up below floor, hard daily cap…`);
    try {
      const r = await api.refuel(agent.name);
      const m1 = r.refuel1.fired ? "FIRED +5 USDT" : r.refuel1.reason;
      const m2 = r.refuel2.fired ? "FIRED +5 USDT" : r.refuel2.reason;
      log(
        `refuel #1 <b>${m1}</b> · refuel #2 <b>${m2}</b> · balance ${fmt(r.before, 2)} → ${fmt(r.after, 2)} USDT`,
        r.refuel1.fired ? "ok" : "info",
      );
      toast("refuel", `#1 ${m1} · #2 ${m2}`, "amber");
      doFlash();
    } catch (e) {
      log(`refuel failed: ${(e as Error).message}`, "er");
      toast("refuel failed", (e as Error).message, "red");
    }
    setBusy(null);
  };

  return (
    <div className={`agent ${flash ? "flash" : ""}`}>
      <div className="ah">
        <span className="name">{agent.name}</span>
        <span className="badge">#{agent.agentId}</span>
      </div>
      <div className="addr" title="copy address" onClick={copyAddr}>
        ⧉ {short(agent.address)}
      </div>
      <div className="bal">
        <div className="b usdt">
          <div className="bk">USDT</div>
          <div className="bv">{fmt(agent.usdt)}</div>
        </div>
        <div className="b eth">
          <div className="bk">ETH (gas)</div>
          <div className="bv">{agent.eth.toFixed(4)}</div>
        </div>
      </div>
      <div className="acts">
        <button className="btn" onClick={resolve}>resolve</button>
        <button className="btn g" onClick={fund}>fund&nbsp;job</button>
        <button className="btn" disabled={busy === "spend"} onClick={spend}>x402&nbsp;spend</button>
        <button className="btn" disabled={busy === "refuel"} onClick={refuel}>refuel</button>
      </div>
    </div>
  );
}
