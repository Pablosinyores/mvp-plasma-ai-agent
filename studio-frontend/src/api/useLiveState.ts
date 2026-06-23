// Live state over WebSocket with auto-reconnect. Mirrors the backend broadcaster:
// the server pushes a fresh StudioState snapshot whenever on-chain/DynamoDB state changes.
import { useEffect, useRef, useState } from "react";
import { WS_URL } from "../config";
import { EMPTY_STATE, type StudioState } from "../types";

export function useLiveState() {
  const [state, setState] = useState<StudioState>(EMPTY_STATE);
  const [connected, setConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const retry = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    let closed = false;

    const connect = () => {
      const ws = new WebSocket(WS_URL);
      wsRef.current = ws;
      ws.onopen = () => setConnected(true);
      ws.onmessage = (ev) => {
        try {
          setState(JSON.parse(ev.data) as StudioState);
        } catch {
          /* ignore malformed frame */
        }
      };
      ws.onclose = () => {
        setConnected(false);
        if (!closed) retry.current = setTimeout(connect, 1000); // reconnect on worker/server restart
      };
      ws.onerror = () => ws.close();
    };

    connect();
    return () => {
      closed = true;
      if (retry.current) clearTimeout(retry.current);
      wsRef.current?.close();
    };
  }, []);

  return { state, connected };
}
