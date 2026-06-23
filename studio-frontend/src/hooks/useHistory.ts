// Buffers distinct values over time into a capped ring — turns the backend's live snapshots
// into a time series the charts can draw. De-dupes identical frames so a quiet chain doesn't
// inflate the series.
import { useEffect, useRef, useState } from "react";

export function useHistory<T>(value: T, cap = 60): T[] {
  const [hist, setHist] = useState<T[]>([]);
  const sig = useRef<string>("");
  useEffect(() => {
    const s = JSON.stringify(value);
    if (s === sig.current) return;
    sig.current = s;
    setHist((h) => [...h.slice(-(cap - 1)), value]);
  }, [value, cap]);
  return hist;
}
