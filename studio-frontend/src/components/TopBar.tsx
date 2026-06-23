interface Props {
  chain?: number;
  connected: boolean;
}

export function TopBar({ chain, connected }: Props) {
  return (
    <div className="top">
      <div className="brand">
        <svg className="hex" viewBox="0 0 24 24" fill="none">
          <path d="M12 2l8.66 5v10L12 22 3.34 17V7L12 2z" stroke="currentColor" strokeWidth="1.6" />
        </svg>
        PLASMA <small>AGENT&nbsp;STUDIO</small>
      </div>
      <div className="spacer" />
      <span className="chip">
        chain&nbsp;<b style={{ color: "var(--ink)" }}>{chain ?? "—"}</b>
      </span>
      <span className="chip">
        backend&nbsp;<b style={{ color: "var(--ink)" }}>local&nbsp;anvil</b>
      </span>
      <span className="chip">
        <span className={`dot ${connected ? "on" : ""}`} />
        {connected ? "live" : "reconnecting…"}
      </span>
    </div>
  );
}
