// Real browser-wallet EIP-7702 signing (viem over an injected EIP-1193 provider, e.g. MetaMask).
//
// This is the PRODUCTION path that replaces the local demo affordance (/dev-bootstrap). The connected
// wallet itself signs the 7702 delegation + installSession and, later, revokeSession — the backend
// never sees the user's key. Bleeding-edge: the wallet must support EIP-7702 (Pectra). When no injected
// wallet is present we fall back to the demo path in the panel.
//
// One nicety: because a 7702 authorization is applied at the START of its transaction, we delegate AND
// installSession in a SINGLE type-4 tx (authorizationList delegates the EOA, then the same tx's call to
// `to = self` runs installSession with the code already present) — one wallet confirmation, not two.
import {
  createWalletClient,
  custom,
  defineChain,
  encodeFunctionData,
  type Address,
  type Hex,
} from "viem";
import type { SessionAuthorizeResult } from "../types";

// minimal eth provider shape (we avoid pulling a global window typing dependency)
interface Eip1193Provider {
  request(args: { method: string; params?: unknown[] }): Promise<unknown>;
}
function injected(): Eip1193Provider | null {
  const eth = (globalThis as { ethereum?: Eip1193Provider }).ethereum;
  return eth ?? null;
}

export function hasInjectedWallet(): boolean {
  return injected() !== null;
}

const POLICY_COMPONENTS = [
  { name: "active", type: "bool" },
  { name: "expiry", type: "uint48" },
  { name: "fundingToken", type: "address" },
  { name: "maxInPerTrade", type: "uint128" },
  { name: "sessionInCap", type: "uint128" },
  { name: "spentIn", type: "uint128" },
  { name: "maxSlippageBps", type: "uint16" },
] as const;

const DELEGATE_ABI = [
  {
    type: "function",
    name: "installSession",
    stateMutability: "nonpayable",
    outputs: [],
    inputs: [
      { name: "key", type: "address" },
      { name: "p", type: "tuple", components: POLICY_COMPONENTS },
      { name: "buys", type: "address[]" },
      { name: "pools", type: "address[]" },
    ],
  },
  {
    type: "function",
    name: "revokeSession",
    stateMutability: "nonpayable",
    outputs: [],
    inputs: [{ name: "key", type: "address" }],
  },
] as const;

async function clientAndAccount() {
  const provider = injected();
  if (!provider) throw new Error("no injected wallet (window.ethereum)");
  const accounts = (await provider.request({ method: "eth_requestAccounts" })) as Address[];
  if (!accounts?.length) throw new Error("wallet returned no account");
  const chainIdHex = (await provider.request({ method: "eth_chainId" })) as string;
  const chainId = Number(chainIdHex);
  const chain = defineChain({
    id: chainId,
    name: `chain-${chainId}`,
    nativeCurrency: { name: "ETH", symbol: "ETH", decimals: 18 },
    rpcUrls: { default: { http: [] } },
  });
  const wallet = createWalletClient({ account: accounts[0], chain, transport: custom(provider) });
  return { wallet, account: accounts[0], chain };
}

function policyTuple(p: SessionAuthorizeResult["install"]["policy"]) {
  return {
    active: p.active as boolean,
    expiry: Number(p.expiry), // uint48 — viem represents this as number
    fundingToken: p.fundingToken as Address,
    maxInPerTrade: BigInt(p.maxInPerTrade as string),
    sessionInCap: BigInt(p.sessionInCap as string),
    spentIn: BigInt(p.spentIn as string),
    maxSlippageBps: Number(p.maxSlippageBps),
  };
}

/** The connected wallet's own account address (so the panel uses the real wallet, not a demo address). */
export async function connectedAddress(): Promise<Address> {
  const { account } = await clientAndAccount();
  return account;
}

/**
 * Delegate the connected EOA to AgentSessionDelegate AND install the session policy in ONE type-4 tx.
 * Returns the tx hash. The wallet must support EIP-7702.
 */
export async function authorizeWithInjectedWallet(payload: SessionAuthorizeResult): Promise<Hex> {
  const { wallet, account } = await clientAndAccount();
  // 7702 authorization delegating THIS account's code to the implementation; `executor: "self"`
  // tells viem the same account will send the tx (so the auth nonce is account.nonce + 1).
  const authorization = await wallet.signAuthorization({
    account,
    contractAddress: payload.delegate as Address,
    executor: "self",
  });
  const data = encodeFunctionData({
    abi: DELEGATE_ABI,
    functionName: "installSession",
    args: [
      payload.sessionKey as Address,
      policyTuple(payload.install.policy),
      payload.install.buys as Address[],
      payload.install.pools as Address[],
    ],
  });
  // single tx: delegate (authorizationList) + call installSession on self (to === account)
  return wallet.sendTransaction({ account, to: account, data, authorizationList: [authorization] });
}

/** Revoke the session on-chain from the connected wallet (it is already delegated). */
export async function revokeWithInjectedWallet(user: Address, sessionKey: Address): Promise<Hex> {
  const { wallet, account } = await clientAndAccount();
  const data = encodeFunctionData({ abi: DELEGATE_ABI, functionName: "revokeSession", args: [sessionKey] });
  return wallet.sendTransaction({ account, to: user, data });
}
