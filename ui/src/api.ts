// Where the observer and world service live, and the two calls the UI makes to the world.
// Point them elsewhere with VITE_OBSERVER_URL and VITE_WORLD_URL.

import type { AnyEvent } from "./events";

export const OBSERVER_URL = import.meta.env.VITE_OBSERVER_URL ?? "http://localhost:8100";
export const WORLD_URL = import.meta.env.VITE_WORLD_URL ?? "http://localhost:8200";

export function runSocketUrl(runId: string, after: number): string {
  return `${OBSERVER_URL.replace(/^http/, "ws")}/ws/${encodeURIComponent(runId)}?after=${after}`;
}

async function post<T>(path: string, body?: unknown): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${WORLD_URL}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body ?? {}),
    });
  } catch {
    throw new Error(`Couldn't reach the world service at ${WORLD_URL}. Start it, then try again.`);
  }
  if (!response.ok) {
    const detail = await response.json().then((d) => d.detail, () => null);
    throw new Error(typeof detail === "string" ? detail : `The world service answered ${response.status}.`);
  }
  return response.json();
}

export function createRun(seed: string): Promise<{ run_id: string }> {
  return post("/runs", { seed });
}

export function startRun(runId: string): Promise<{ run_id: string }> {
  return post(`/runs/${encodeURIComponent(runId)}/start`);
}

export interface RunSummary {
  run_id: string;
  events: number;
  first_ts: number;
  last_ts: number;
  seed: string | null;
}

export async function listRuns(): Promise<RunSummary[]> {
  let response: Response;
  try {
    response = await fetch(`${OBSERVER_URL}/runs`);
  } catch {
    throw new Error(`Couldn't reach the observer at ${OBSERVER_URL}.`);
  }
  if (!response.ok) throw new Error(`The observer answered ${response.status}.`);
  return response.json();
}

/** Read a prior run's durable event history so the UI can replay its results. */
export async function getRunEvents(runId: string): Promise<AnyEvent[]> {
  let response: Response;
  try {
    response = await fetch(`${OBSERVER_URL}/runs/${encodeURIComponent(runId)}/events`);
  } catch {
    throw new Error(`Couldn't reach the observer at ${OBSERVER_URL}.`);
  }
  if (!response.ok) throw new Error(`The observer answered ${response.status}.`);
  return response.json();
}

/** Ask the observer to summarize a run now. Joins the summary already being written, if there is one. */
export async function requestSummary(runId: string): Promise<AnyEvent> {
  let response: Response;
  try {
    response = await fetch(`${OBSERVER_URL}/runs/${encodeURIComponent(runId)}/summary`, { method: "POST" });
  } catch {
    throw new Error(`Couldn't reach the observer at ${OBSERVER_URL}.`);
  }
  if (!response.ok) throw new Error(`The observer answered ${response.status}.`);
  return response.json();
}

export function stopRun(runId: string): Promise<{ run_id: string }> {
  return post(`/runs/${encodeURIComponent(runId)}/stop`);
}

export function getRunStatus(runId: string): Promise<{ state: string }> {
  return fetch(`${WORLD_URL}/runs/${encodeURIComponent(runId)}`)
    .then(async (response) => {
      if (!response.ok) throw new Error(`The world service answered ${response.status}.`);
      return response.json();
    });
}
