import { useEffect, useState } from "react";

/** Read the current hash route fragment, e.g. "#/jobs" → "jobs". */
function readPath(): string {
  return window.location.hash.replace(/^#\/?/, "").split("?")[0];
}

/**
 * Minimal hash-based router — no dependency, survives static `dist` hosting.
 * Returns the active path fragment and a `navigate` helper.
 */
export function useHashRoute(fallback: string): [string, (path: string) => void] {
  const [path, setPath] = useState<string>(() => readPath() || fallback);

  useEffect(() => {
    const onChange = () => setPath(readPath() || fallback);
    window.addEventListener("hashchange", onChange);
    // Normalise the URL on first load so the bar always reflects a real route.
    if (!readPath()) window.location.hash = `#/${fallback}`;
    return () => window.removeEventListener("hashchange", onChange);
  }, [fallback]);

  const navigate = (next: string) => {
    if (next !== readPath()) window.location.hash = `#/${next}`;
  };

  return [path, navigate];
}
