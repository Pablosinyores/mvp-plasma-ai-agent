// Demo wallet session. Holds the currently-connected wallet, lets the operator connect,
// disconnect, and switch wallets. Persisted to localStorage so a refresh keeps you logged in.
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { DEMO_WALLETS, type DemoWallet } from "./wallets";

const LS_KEY = "plasma.wallet";

interface WalletCtx {
  wallet: DemoWallet | null;
  connected: boolean;
  wallets: DemoWallet[];
  connect: (id?: string) => void;
  disconnect: () => void;
  switchTo: (id: string) => void;
}

const Ctx = createContext<WalletCtx | null>(null);

export function WalletProvider({ children }: { children: ReactNode }) {
  const [id, setId] = useState<string | null>(() => {
    const saved = localStorage.getItem(LS_KEY);
    return saved && DEMO_WALLETS.some((w) => w.id === saved) ? saved : null;
  });

  useEffect(() => {
    if (id) localStorage.setItem(LS_KEY, id);
    else localStorage.removeItem(LS_KEY);
  }, [id]);

  const connect = useCallback((next?: string) => setId(next ?? DEMO_WALLETS[0].id), []);
  const disconnect = useCallback(() => setId(null), []);
  const switchTo = useCallback((next: string) => setId(next), []);

  const wallet = useMemo(() => DEMO_WALLETS.find((w) => w.id === id) ?? null, [id]);

  return (
    <Ctx.Provider value={{ wallet, connected: !!wallet, wallets: DEMO_WALLETS, connect, disconnect, switchTo }}>
      {children}
    </Ctx.Provider>
  );
}

export function useWallet(): WalletCtx {
  const c = useContext(Ctx);
  if (!c) throw new Error("useWallet must be used within WalletProvider");
  return c;
}
