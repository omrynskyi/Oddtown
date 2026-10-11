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
  /** Who wrote the minority report, when the world service knows. */
  minority_report_by?: string | null;
  /** Each champion's reason for backing it. */
  reasons?: Record<string, string>;
}

/** One of the observer's top three, written by the summary call. */
export interface Verdict {
  branch_id: string;
  title: string;
  why: string;
  risk: string;
}

/** A theme that a group of branches share. */
export interface Bucket {
  name: string;
  emoji: string;
  blurb: string;
  branch_ids: string[];
}

/** The observer's end-of-run summary. A failed call only sets `error`. */
export interface Summary {
  headline: string;
  verdicts: Verdict[];
  buckets: Bucket[];
  moments: string[];
  baseline: string[];
  model?: string | null;
  error?: string | null;
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
/** `quorum` is how many backers besides its authors make a branch a proposal. Null, or missing on older runs, means proposals are off. */
export type RunStarted = Envelope<"run.started", { quorum?: number | null }>;
export type AgentDecision = Envelope<
  "agent.decision",
  {
    action: "twist" | "merge" | "back" | "trash" | "pitch" | "ignore";
    /** The speech bubble text, 100 characters or fewer. */
    say: string;
    reason: string;
    target_branch?: string | null;
    send_to?: string | null;
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
/** A message from one character to another. `agent_id` is the sender and `to` is the recipient. */
export type IdeaShare = Envelope<"idea.share", { to: string; note?: string }>;
export type IdeaPitch = Envelope<"idea.pitch", { to: string; note?: string }>;
export type MergePropose = Envelope<"merge.propose", { to: string; note?: string }>;
/** Replies. `agent_id` is the character answering and `to` is whoever asked. */
export type IdeaReaction = Envelope<"idea.reaction", { to: string; answer: "accept" | "reject"; text?: string }>;
export type PitchResponse = Envelope<"pitch.response", { to: string; persuaded: boolean; text?: string }>;
export type MergeResponse = Envelope<"merge.response", { to: string; answer: "accept" | "reject" | "counter"; text?: string }>;
export type Endorse = Envelope<"endorse", { branch_id: string; reason?: string }>;
export type Unendorse = Envelope<"unendorse", { branch_id: string; reason?: string }>;
/**
 * An author retiring one of its unbacked branches, and everyone composts it until someone backs it:
 * everyone it was shown to turned it down, it was over its author's budget, or its author died.
 */
export type BranchWithdraw = Envelope<"branch.withdraw", { branch_id: string; why?: "unliked" | "budget" | "died" }>;
/** A character speaking against a branch it doesn't back. */
export type Objection = Envelope<"objection", { branch_id: string; reason?: string }>;
/** What a character has concluded so far, and what it is pushing for next. */
export type AgentReflection = Envelope<"agent.reflection", { insights?: string[]; agenda?: string; impressions?: { peer_id: string; impression: string }[] }>;
/** A character that is done arguing for now. */
export type Rest = Envelope<"rest", Record<string, never>>;
export type WorldEvent = Envelope<"world.event", { kind: "seed" | "constraint" | "feedback"; text: string }>;
export type AgentLastWords = Envelope<"agent.last_words", { text: string }>;
export type AgentDied = Envelope<"agent.died", { picked: string[] }>;
export type RunEnded = Envelope<"run.ended", { finalists: Finalist[]; baseline: string[] }>;
export type RunSummary = Envelope<"run.summary", Partial<Summary>>;

/** Events the UI understands. Narrow on `type` to get the payload. */
export type KnownEvent =
  | RunCreated
  | AgentBorn
  | RunReady
  | RunStarted
  | AgentDecision
  | BranchCreated
  | IdeaShare
  | IdeaPitch
  | MergePropose
  | IdeaReaction
  | PitchResponse
  | MergeResponse
  | Endorse
  | Unendorse
  | BranchWithdraw
  | Objection
  | AgentReflection
  | Rest
  | WorldEvent
  | AgentLastWords
  | AgentDied
  | RunEnded
  | RunSummary;

/** Anything the observer sends, including types the UI ignores. */
export type AnyEvent = Envelope<string, Record<string, unknown>>;

const KNOWN_TYPES = new Set<string>([
  "run.created",
  "agent.born",
  "run.ready",
  "run.started",
  "agent.decision",
  "branch.created",
  "idea.share",
  "idea.pitch",
  "merge.propose",
  "idea.reaction",
  "pitch.response",
  "merge.response",
  "endorse",
  "unendorse",
  "branch.withdraw",
  "objection",
  "agent.reflection",
  "rest",
  "world.event",
  "agent.last_words",
  "agent.died",
  "run.ended",
  "run.summary",
]);

export function isKnown(event: AnyEvent): event is AnyEvent & KnownEvent {
  return KNOWN_TYPES.has(event.type);
}
