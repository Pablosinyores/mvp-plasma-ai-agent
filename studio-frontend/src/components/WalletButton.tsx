import { useEffect, useRef, useState } from "react";
import { fmt, short } from "../lib/format";
import { useStudio } from "../store";
import { useWallet } from "../wallet/WalletContext";
import { ANVIL_CHAIN_ID } from "../wallet/wallets";

export function WalletButton() {
  const { wallet, connected, wallets, connect, disconnect, switchTo } = useWallet();
  const { toast } = useStudio();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  // close on outside click / escape
  useEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    window.addEventListener("mousedown", onClick);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("mousedown", onClick);
      window.removeEventListener("keydown", onKey);
    };
  }, [open]);

  if (!connected || !wallet) {
    return (
      <button
        className="btn g wallet-connect"
        onClick={() => {
          connect();
          toast("wallet connected", `Operator · acct #0 · Anvil chain ${ANVIL_CHAIN_ID}`, "green");
        }}
      >
        Connect Wallet
      </button>
    );
  }

  const copy = () => {
    navigator.clipboard?.writeText(wallet.address);
    toast("copied", wallet.address);
  };

  return (
    <div className="wallet" ref={ref}>
      <button className={`wallet-chip ${open ? "open" : ""}`} onClick={() => setOpen((o) => !o)}>
        <span className="wjazz" style={{ background: jazz(wallet.address) }} />
        <span className="wlabel">{wallet.label}</span>
        <span className="waddr">{short(wallet.address)}</span>
        <span className="wcaret">▾</span>
      </button>

      {open && (
        <div className="wallet-menu">
          <div className="wm-head">
            <span>Anvil accounts</span>
            <span className="wm-badge">chain&nbsp;{ANVIL_CHAIN_ID}</span>
          </div>
          {wallets.map((w) => (
            <button
              key={w.id}
              className={`wm-item ${w.id === wallet.id ? "on" : ""}`}
              onClick={() => {
                if (w.id !== wallet.id) {
                  switchTo(w.id);
                  toast("wallet switched", `${w.label} · acct #${w.index} · ${short(w.address)}`, "blue");
                }
                setOpen(false);
              }}
            >
              <span className="wjazz" style={{ background: jazz(w.address) }} />
              <span className="wm-main">
                <span className="wm-label">
                  {w.label} <span className="wm-idx">acct #{w.index}</span>
                </span>
                <span className="wm-role">{w.role}</span>
              </span>
              <span className="wm-bal">{fmt(w.usdt, 2)} USDT</span>
              {w.id === wallet.id && <span className="wm-dot" />}
            </button>
          ))}
          <div className="wm-foot">
            <button className="wm-link" onClick={copy}>
              ⧉ copy address
            </button>
            <button
              className="wm-link danger"
              onClick={() => {
                disconnect();
                setOpen(false);
                toast("disconnected", "wallet session cleared", "amber");
              }}
            >
              disconnect
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

// deterministic little gradient avatar from an address — no dependency
function jazz(addr: string): string {
  const h = parseInt(addr.slice(2, 8), 16);
  const a = h % 360;
  const b = (a + 80) % 360;
  return `linear-gradient(135deg, hsl(${a} 70% 55%), hsl(${b} 70% 45%))`;
}
