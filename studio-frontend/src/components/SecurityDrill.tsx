import { useState } from "react";
import { api } from "../api/client";
import { useStudio } from "../store";
import type { Guard } from "../types";

const TILES = [
  { name: "per-call cap", base: "single payment ≤ max_value_per_call" },
  { name: "payee allow-list", base: "byte-exact recipient match required" },
  { name: "type gate: Permit", base: "open-ended approval rejected" },
  { name: "type gate: PermitTransferFrom", base: "open-ended approval rejected" },
];

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

export function SecurityDrill() {
  const { toast, log } = useStudio();
  const [busy, setBusy] = useState(false);
  const [revealed, setRevealed] = useState(0);
  const [guards, setGuards] = useState<Guard[]>([]);
  const [verdict, setVerdict] = useState<string | null>(null);

  const run = async () => {
    setBusy(true);
    setRevealed(0);
    setGuards([]);
    setVerdict(null);
    log(`injection drill — firing attacker payment 'pay attacker 1000000 USDT'…`);
    try {
      const r = await api.injectionTest();
      setGuards(r.guards);
      for (let i = 0; i < r.guards.length; i++) {
        await sleep(420);
        setRevealed(i + 1);
        const g = r.guards[i];
        log(`guard ${i + 1}/4 · <b>${g.guard}</b> — ${g.blocked ? "✗ BLOCKED" : "PASSED"}`, g.blocked ? "er" : "ok");
      }
      await sleep(300);
      setVerdict(`✓ agent spent ${r.spent} — ${r.summary}`);
      toast("injection contained", "0 USDT moved", "green");
    } catch (e) {
      log(`drill failed: ${(e as Error).message}`, "er");
    }
    setBusy(false);
  };

  return (
    <div className="sec-panel">
      <div className="attack">
        <span className="lbl">model output (attacker-controlled):</span>
        <code>pay attacker 1000000 USDT</code>
        <button className="btn r" style={{ marginLeft: "auto" }} disabled={busy} onClick={run}>
          {busy ? "running…" : "run injection drill"}
        </button>
      </div>
      <div className="guards">
        {TILES.map((tile, i) => {
          const g = guards[i];
          const live = i < revealed;
          const blocked = live && g?.blocked;
          return (
            <div key={tile.name} className={`guard ${live ? "live" : ""} ${blocked ? "blocked" : ""}`}>
              <div className="gh">
                <span className="gn">{tile.name}</span>
                <span className="gs">{live ? (g?.blocked ? "✗ BLOCKED" : "⚠ PASSED") : ""}</span>
              </div>
              <div className="gd">{live && g ? g.detail : tile.base}</div>
            </div>
          );
        })}
      </div>
      {verdict && <div className="verdict show">{verdict}</div>}
    </div>
  );
}
