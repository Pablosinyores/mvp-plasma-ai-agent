// Cross-cutting UI services shared by every section: toasts, the activity log, and a modal.
// Kept in one small context so any component can call useStudio().toast()/log()/openModal().
import { createContext, useCallback, useContext, useRef, useState, type ReactNode } from "react";
import { nowTime } from "./lib/format";

export type Accent = "blue" | "green" | "red" | "amber" | "violet";
export type LogKind = "ok" | "er" | "info" | "dim";

export interface Toast {
  id: number;
  title: string;
  msg?: string;
  accent: Accent;
}
export interface LogEntry {
  id: number;
  time: string;
  html: string;
  kind: LogKind;
  jobId?: number; // when set, the log line is clickable into the job detail modal
}

interface StudioCtx {
  toasts: Toast[];
  logs: LogEntry[];
  modal: ReactNode | null;
  toast: (title: string, msg?: string, accent?: Accent) => void;
  log: (html: string, kind?: LogKind, jobId?: number) => void;
  openModal: (node: ReactNode) => void;
  closeModal: () => void;
}

const Ctx = createContext<StudioCtx | null>(null);

export function StudioProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [modal, setModal] = useState<ReactNode | null>(null);
  const seq = useRef(0);

  const toast = useCallback((title: string, msg?: string, accent: Accent = "blue") => {
    const id = ++seq.current;
    setToasts((t) => [...t, { id, title, msg, accent }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 3600);
  }, []);

  const log = useCallback((html: string, kind: LogKind = "dim", jobId?: number) => {
    const id = ++seq.current;
    setLogs((l) => [...l.slice(-119), { id, time: nowTime(), html, kind, jobId }]);
  }, []);

  const openModal = useCallback((node: ReactNode) => setModal(node), []);
  const closeModal = useCallback(() => setModal(null), []);

  return (
    <Ctx.Provider value={{ toasts, logs, modal, toast, log, openModal, closeModal }}>
      {children}
    </Ctx.Provider>
  );
}

export function useStudio(): StudioCtx {
  const c = useContext(Ctx);
  if (!c) throw new Error("useStudio must be used within StudioProvider");
  return c;
}
