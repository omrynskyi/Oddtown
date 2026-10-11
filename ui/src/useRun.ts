// Holds the town and keeps it fed from the observer's WebSocket for the current run.

import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { createRun, getRunEvents, requestSummary, runSocketUrl, startRun } from "./api";
import type { AnyEvent } from "./events";
import { initialTown, townReducer } from "./town";

export type Connection = "idle" | "connecting" | "open" | "reconnecting";

export interface ReplayTimeline {
  position: number;
  total: number;
  eventType: string | null;
  playing: boolean;
  seek: (position: number) => void;
  toggle: () => void;
}

export function useRun() {
  const [town, dispatch] = useReducer(townReducer, initialTown);
  const [connection, setConnection] = useState<Connection>("idle");
  const [replay, setReplay] = useState<{ runId: string; seed: string | null; events: AnyEvent[]; position: number } | null>(null);
  const [replayPlaying, setReplayPlaying] = useState(false);
  const lastSeq = useRef(0);
  const replayRef = useRef(replay);
  lastSeq.current = town.lastSeq;
  replayRef.current = replay;

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
    setReplay(null);
    dispatch({ kind: "submitted", runId: run_id, seed });
  }, []);

  const runId = town.runId;
  const start = useCallback(async () => {
    if (runId) await startRun(runId);
  }, [runId]);

  /** Summarize the run now, for a run that ended before summaries existed or whose summary failed. */
  const summarize = useCallback(async () => {
    if (!runId) return;
    const event = await requestSummary(runId);
    dispatch({ kind: "event", event });
    // A replayed run keeps it on its timeline too, so scrubbing doesn't lose it.
    setReplay((current) => {
      if (!current || current.runId !== event.run_id || current.events.some((e) => e.seq === event.seq)) return current;
      const atEnd = current.position === current.events.length;
      const events = [...current.events, event];
      return { ...current, events, position: atEnd ? events.length : current.position };
    });
  }, [runId]);

  /** Replay a saved run into the same city and tree views used for a live town. */
  const openRun = useCallback(async (runId: string, seed: string | null) => {
    const events = await getRunEvents(runId);
    setReplayPlaying(false);
    dispatch({ kind: "replay", runId, seed, events });
    setReplay({ runId, seed, events, position: events.length });
  }, []);

  const seekReplay = useCallback((position: number) => {
    const current = replayRef.current;
    if (!current) return;
    const nextPosition = Math.max(0, Math.min(position, current.events.length));
    dispatch({ kind: "replay", runId: current.runId, seed: current.seed, events: current.events.slice(0, nextPosition) });
    setReplay({ ...current, position: nextPosition });
  }, []);

  const toggleReplay = useCallback(() => {
    if (replayPlaying) {
      setReplayPlaying(false);
      return;
    }
    const current = replayRef.current;
    if (!current || current.events.length === 0) return;
    if (current.position >= current.events.length) seekReplay(0);
    setReplayPlaying(true);
  }, [replayPlaying, seekReplay]);

  useEffect(() => {
    if (!replayPlaying || !replay) return;
    if (replay.position >= replay.events.length) {
      setReplayPlaying(false);
      return;
    }
    const timer = window.setTimeout(() => seekReplay(replay.position + 1), 260);
    return () => window.clearTimeout(timer);
  }, [replay, replayPlaying, seekReplay]);

  /** Leave the current run and return to the screen where a new idea can be started. */
  const home = useCallback(() => {
    setReplayPlaying(false);
    setReplay(null);
    dispatch({ kind: "reset" });
  }, []);

  useEffect(() => {
    if (import.meta.env.DEV) (window as unknown as { __town: unknown }).__town = town;
  }, [town]);

  const timeline: ReplayTimeline | null = replay
    ? {
        position: replay.position,
        total: replay.events.length,
        eventType: replay.position > 0 ? replay.events[replay.position - 1]?.type ?? null : null,
        playing: replayPlaying,
        seek: (position) => seekReplay(position),
        toggle: toggleReplay,
      }
    : null;

  return { town, connection, submit, start, openRun, home, summarize, timeline };
}
