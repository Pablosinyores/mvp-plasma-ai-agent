import { fmt, short } from "../lib/format";
import type { SectionProps } from "../sections/registry";
import { useWallet } from "../wallet/WalletContext";
import { Stats } from "./Stats";

export function OverviewSection({ state }: SectionProps) {
  const { wallet, connected, connect } = useWallet();
  const funded = state.jobs.filter((j) => j.status === "FUNDED").length;
  const completed = state.jobs.filter((j) => j.status === "COMPLETED").length;

  return (
    <div className="section">
      {/* hero / connected wallet */}
      <div className="hero">
        <div className="hero-main">
          <h1>Agent control plane</h1>
          <p>
            Spin up autonomous agents with on-chain identities, fund jobs, gate spend with x402
            caps, and watch the money move — all on a local Anvil chain.
          </p>
          <div className="hero-cta">
            <a className="btn g" href="#/agents">Manage agents</a>
            <a className="btn" href="#/playground">Open playground</a>
          </div>
        </div>
        <div className="hero-wallet">
          <div className="hw-k">connected wallet</div>
          {connected && wallet ? (
            <>
              <div className="hw-label">{wallet.label}</div>
              <div className="hw-addr">{short(wallet.address)}</div>
              <div className="hw-bal">
                <div>
                  <span className="hw-bk">USDT</span>
                  <span className="hw-bv">{fmt(wallet.usdt, 2)}</span>
                </div>
                <div>
                  <span className="hw-bk">ETH</span>
                  <span className="hw-bv">{wallet.eth.toFixed(2)}</span>
                </div>
              </div>
              <div className="hw-role">{wallet.role}</div>
            </>
          ) : (
            <>
              <div className="hw-empty">No wallet connected.</div>
              <button className="btn g" onClick={() => connect()}>Connect Wallet</button>
            </>
          )}
        </div>
      </div>

      <Stats stats={state.stats} />

      {/* quick glance cards */}
      <div className="glance">
        <a className="glance-card" href="#/agents">
          <div className="gc-k">agents</div>
          <div className="gc-v">{state.stats.agentCount}</div>
          <div className="gc-sub">registered on-chain →</div>
        </a>
        <a className="glance-card" href="#/jobs">
          <div className="gc-k">jobs</div>
          <div className="gc-v">{state.stats.jobCount}</div>
          <div className="gc-sub">{funded} funded · {completed} done →</div>
        </a>
        <a className="glance-card" href="#/security">
          <div className="gc-k">guard activity</div>
          <div className="gc-v">{state.events.length}</div>
          <div className="gc-sub">spend / refuel events →</div>
        </a>
        <a className="glance-card" href="#/analytics">
          <div className="gc-k">net flow</div>
          <div className="gc-v sm">{fmt(state.stats.earned - state.stats.spent, 2)}</div>
          <div className="gc-sub">USDT earned − spent →</div>
        </a>
      </div>
    </div>
  );
}
