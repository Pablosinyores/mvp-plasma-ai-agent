import { short } from "../../lib/format";
import type { ResolveResult } from "../../types";

export function ResolveModal({ r, name, onClose }: { r: ResolveResult; name: string; onClose: () => void }) {
  return (
    <div className="modal">
      <h3>identity · {name}</h3>
      <div className="sub">on-chain round-trip — NFT → cardURI → Agent Card</div>
      <div className="kv">agentId&nbsp;&nbsp;<b>#{r.agentId}</b></div>
      <div className="kv">owner&nbsp;&nbsp;&nbsp;&nbsp;<b style={{ color: "var(--blue)" }} title={r.owner}>{short(r.owner)}</b></div>
      <div className="kv">cardURI&nbsp;<span className="mono">{r.cardURI}</span></div>
      <label>agent card</label>
      <pre>{JSON.stringify(r.card, null, 2)}</pre>
      <div className="row">
        <button className="btn" onClick={onClose}>close</button>
      </div>
    </div>
  );
}
