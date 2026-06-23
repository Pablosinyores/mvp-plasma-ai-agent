export const short = (a?: string): string =>
  a ? `${a.slice(0, 6)}…${a.slice(-4)}` : "—";

export const fmt = (n: number, d = 6): string =>
  Number(n).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d });

export const nowTime = (): string =>
  new Date().toLocaleTimeString([], { hour12: false });
