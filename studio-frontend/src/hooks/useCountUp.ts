import { useEffect, useRef, useState } from "react";

// Eased count-up to `target`. `decimals` controls display precision.
export function useCountUp(target: number, decimals = 0): string {
  const [val, setVal] = useState(target);
  const from = useRef(target);
  const raf = useRef<number | null>(null);

  useEffect(() => {
    const start = performance.now();
    const begin = from.current;
    const dur = 550;
    const tick = (t: number) => {
      const p = Math.min(1, (t - start) / dur);
      const e = 1 - Math.pow(1 - p, 3);
      setVal(begin + (target - begin) * e);
      if (p < 1) raf.current = requestAnimationFrame(tick);
      else from.current = target;
    };
    raf.current = requestAnimationFrame(tick);
    return () => {
      if (raf.current) cancelAnimationFrame(raf.current);
      from.current = target;
    };
  }, [target]);

  return val.toFixed(decimals);
}
