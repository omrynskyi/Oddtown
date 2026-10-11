// Holds the town and keeps it fed from the observer's WebSocket for the current run.

import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { createRun, runSocketUrl } from "./api";
import type { AnyEvent } from "./events";
import { initialTown, townReducer } from "./town";

export type Connection = "idle" | "connecting" | "open" | "reconnecting";

export function useRun() {
  const [town, dispatch] = useReducer(townReducer, initialTown);
  const [connection, setConnection] = useState<Connection>("idle");
  const lastSeq = useRef(0);
  lastSeq.current = town.lastSeq;

  useEffect(() => {
    if (!town.runId) return;
    const runId = town.runId;
    let socket: WebSocket | null = null;
    let retry: number | undefined;
    let attempt = 0;
    let stopped = false;

    // Reconnects pick up after the last applied event, so nothing is missed or repeated.
    const connect = () => {
      setConnection(attempt === 0 ? "connecting" : "reconnecting");
      socket = new WebSocket(runSocketUrl(runId, lastSeq.current));
      socket.onopen = () => {
        attempt = 0;
        setConnection("open");
      };
      socket.onmessage = (message) => dispatch({ kind: "event", event: JSON.parse(message.data) as AnyEvent });
      socket.onclose = () => {
        if (stopped) return;
        setConnection("reconnecting");
        retry = window.setTimeout(connect, Math.min(500 * 2 ** attempt++, 5000));
      };
    };
    connect();

    return () => {
      stopped = true;
      window.clearTimeout(retry);
      socket?.close();
      setConnection("idle");
    };
  }, [town.runId]);

  const submit = useCallback(async (seed: string) => {
    const { run_id } = await createRun(seed);
    dispatch({ kind: "submitted", runId: run_id, seed });
  }, []);

  useEffect(() => {
    if (import.meta.env.DEV) (window as unknown as { __town: unknown }).__town = town;
  }, [town]);

  return { town, connection, submit };
}
