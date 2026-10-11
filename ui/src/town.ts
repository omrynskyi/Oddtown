// Turns the observer's event stream into the state the screens draw.
// Pure, so the same events always give the same town, live or replayed.

import { isKnown, type AnyEvent, type Persona } from "./events";

export const LOT_COUNT = 6;

export type Phase = "ask" | "loading" | "ready" | "running" | "ended";

const PHASE_ORDER: Phase[] = ["ask", "loading", "ready", "running", "ended"];

export interface Character {
  persona: Persona;
  alive: boolean;
  lot: number;
}

export interface Town {
  runId: string | null;
  seed: string | null;
  phase: Phase;
  characters: Record<string, Character>;
  /** The id of the character living on each lot, or null for an empty lot. */
  lots: (string | null)[];
  lastSeq: number;
}

export const initialTown: Town = {
  runId: null,
  seed: null,
  phase: "ask",
  characters: {},
  lots: Array(LOT_COUNT).fill(null),
  lastSeq: 0,
};

export type TownAction =
  | { kind: "submitted"; runId: string; seed: string }
  | { kind: "event"; event: AnyEvent }
  | { kind: "reset" };

export function townReducer(town: Town, action: TownAction): Town {
  switch (action.kind) {
    case "submitted":
      return { ...initialTown, runId: action.runId, seed: action.seed, phase: "loading" };
    case "reset":
      return initialTown;
    case "event": {
      const { event } = action;
      // Other runs, and anything already applied after a reconnect, are ignored.
      if (event.run_id !== town.runId || event.seq <= town.lastSeq) return town;
      return { ...applyEvent(town, event), lastSeq: event.seq };
    }
  }
}

/** Phases only move forward, so replaying a finished run never goes backwards. */
function advance(town: Town, phase: Phase): Town {
  return PHASE_ORDER.indexOf(phase) > PHASE_ORDER.indexOf(town.phase) ? { ...town, phase } : town;
}

function applyEvent(town: Town, event: AnyEvent): Town {
  if (!isKnown(event)) return town;
  switch (event.type) {
    case "run.created":
      return advance({ ...town, seed: event.payload.seed }, "loading");
    case "agent.born":
      return moveIn(town, event.payload.persona);
    case "run.ready":
      return advance(town, "ready");
    case "run.started":
      return advance(town, "running");
    case "agent.died": {
      const id = event.agent_id;
      const character = id ? town.characters[id] : undefined;
      if (!id || !character) return town;
      return { ...town, characters: { ...town.characters, [id]: { ...character, alive: false } } };
    }
    case "run.ended":
      return advance(town, "ended");
    default:
      return town;
  }
}

/** A newcomer takes the lot of the character it replaces, otherwise the first empty lot. */
function moveIn(town: Town, persona: Persona): Town {
  if (town.characters[persona.id]) return town;
  const replaces = persona.lineage?.replaces;
  const replacedLot = replaces ? town.characters[replaces]?.lot : undefined;
  const lot = replacedLot ?? town.lots.indexOf(null);
  if (lot === -1) return town;
  const lots = [...town.lots];
  lots[lot] = persona.id;
  return { ...town, lots, characters: { ...town.characters, [persona.id]: { persona, alive: true, lot } } };
}
