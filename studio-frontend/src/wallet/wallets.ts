// Demo wallets for the local-anvil control plane. These are the well-known Anvil dev accounts
// (the deterministic mnemonic Anvil/Foundry funds on startup) — they ONLY exist on the local
// Anvil chain, so switching between them never leaves chain 31337. In demo mode the operator
// can "connect" one and switch to see the studio from different vantage points.

/** The local Anvil chain id. Wallet switching is constrained to this chain only. */
export const ANVIL_CHAIN_ID = 31337;

export interface DemoWallet {
  id: string;
  /** Anvil account index (account 0..n from the default mnemonic) */
  index: number;
  label: string;
  role: string;
  address: string;
  /** mock operator balances, purely presentational in demo mode */
  eth: number;
  usdt: number;
}

export const DEMO_WALLETS: DemoWallet[] = [
  {
    id: "operator",
    index: 0,
    label: "Operator",
    role: "deploys & funds agents",
    address: "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266",
    eth: 9998.42,
    usdt: 250.0,
  },
  {
    id: "treasury",
    index: 1,
    label: "Treasury",
    role: "holds protocol USDT",
    address: "0x70997970C51812dc3A010C7d01b50e0d17dc79C8",
    eth: 10000.0,
    usdt: 1_000_000.0,
  },
  {
    id: "provider",
    index: 2,
    label: "Provider",
    role: "fulfils & gets paid for jobs",
    address: "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC",
    eth: 9999.91,
    usdt: 75.5,
  },
  {
    id: "guest",
    index: 3,
    label: "Guest",
    role: "read-only observer",
    address: "0x90F79bf6EB2c4f870365E785982E1f101E93b906",
    eth: 10000.0,
    usdt: 0.0,
  },
];

export const DEFAULT_WALLET_ID = DEMO_WALLETS[0].id;
