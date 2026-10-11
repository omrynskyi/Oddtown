// Observer event contract. Mirrors observer/events.py, which is the source of truth.

export const TRAIT_AXES = [
  "time_horizon",
  "tech_attitude",
  "scale",
  "sociality",
  "risk",
  "rationality",
] as const;

export type TraitAxis = (typeof TRAIT_AXES)[number];

/** Each trait sits between -1 and 1. */
export type Traits = Record<TraitAxis, number>;

/** Only the fields the UI draws are required. Agents may send more. */
export interface Persona {
  id: string;
  name: string;
  one_liner: string;
  traits: Traits;
  model?: string | null;
  lineage?: { generation: number; designed_by: string[]; replaces: string | null };
  worldview?: string;
  loves?: string[];
  hates?: string[];
  red_line?: string;
  voice?: string;
  [extra: string]: unknown;
}

export interface Finalist {
  branch_id: string;
  text: string;
  champions: string[];
  minority_report: string;
}

export interface Envelope<T extends string, P> {
  run_id: string;
  seq: number;
  ts: number;
  type: T;
  agent_id: string | null;
  payload: P;
}

export type RunCreated = Envelope<"run.created", { seed: string }>;
export type AgentBorn = Envelope<"agent.born", { persona: Persona }>;
export type RunReady = Envelope<"run.ready", { agent_ids: string[] }>;
export type RunStarted = Envelope<"run.started", Record<string, never>>;
export type AgentDecision = Envelope<
  "agent.decision",
  {
    action: "twist" | "merge" | "back" | "trash" | "pitch" | "ignore";
    /** The speech bubble text, 100 characters or fewer. */
    say: string;
    reason: string;
  }
>;
export type BranchCreated = Envelope<
  "branch.created",
  {
    branch_id: string;
    parent_ids: string[];
    op: "seed" | "twist" | "merge";
    authors: string[];
    text: string;
  }
>;
export type Endorse = Envelope<"endorse", { branch_id: string }>;
export type Unendorse = Envelope<"unendorse", { branch_id: string }>;
export type WorldEvent = Envelope<"world.event", { kind: "seed" | "constraint" | "feedback"; text: string }>;
export type AgentLastWords = Envelope<"agent.last_words", { text: string }>;
export type AgentDied = Envelope<"agent.died", { picked: string[] }>;
export type RunEnded = Envelope<"run.ended", { finalists: Finalist[]; baseline: string[] }>;

/** Events the UI understands. Narrow on `type` to get the payload. */
export type KnownEvent =
  | RunCreated
  | AgentBorn
  | RunReady
  | RunStarted
  | AgentDecision
  | BranchCreated
  | Endorse
  | Unendorse
  | WorldEvent
  | AgentLastWords
  | AgentDied
  | RunEnded;

/** Anything the observer sends, including types the UI ignores. */
export type AnyEvent = Envelope<string, Record<string, unknown>>;

const KNOWN_TYPES = new Set<string>([
  "run.created",
  "agent.born",
  "run.ready",
  "run.started",
  "agent.decision",
  "branch.created",
  "endorse",
  "unendorse",
  "world.event",
  "agent.last_words",
  "agent.died",
  "run.ended",
]);

export function isKnown(event: AnyEvent): event is AnyEvent & KnownEvent {
  return KNOWN_TYPES.has(event.type);
}
