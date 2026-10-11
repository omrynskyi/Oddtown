// Turns the observer's event stream into the state the screens draw.
// Pure, so the same events always give the same town, live or replayed.

import { isKnown, type AgentDecision, type AnyEvent, type Finalist, type Persona, type Summary } from "./events";
import { shortName } from "./names";

export const LOT_COUNT = 6;

export type Phase = "ask" | "loading" | "ready" | "running" | "ended";

const PHASE_ORDER: Phase[] = ["ask", "loading", "ready", "running", "ended"];

export interface Character {
  persona: Persona;
  alive: boolean;
  lot: number;
  /** Event when this resident moved into town, used for their town lifespan. */
  bornSeq?: number;
  /** Event when the resident left town, if they have. */
  diedSeq?: number;
}

/** The last thing a character said. `seq` changes with every new line, so the bubble can restart. */
export interface Speech {
  text: string;
  seq: number;
  lastWords: boolean;
}

/** One version of the idea. Twists have one parent, merges have two. */
export interface Branch {
  id: string;
  parentIds: string[];
  op: "seed" | "twist" | "merge";
  authors: string[];
  text: string;
  seq: number;
}

/**
 * A message between two characters, drawn as a letter flying from one house to the other.
 * A reply is a letter back, marked by how the character answered.
 */
export interface Interaction {
  /** The seq of the event it came from, which is unique, so it can key the letter. */
  seq: number;
  from: string;
  to: string;
  kind: "letter" | "agree" | "disagree" | "mixed";
  text: string;
}

/** Only the latest few are kept, since each letter is gone from the screen in a few seconds. */
const MAX_INTERACTIONS = 8;

/**
 * One line of town news, for the ticker under the title and the news list.
 * `actor` is the character it is about, and `quote` is the idea or rule it mentions.
 */
export interface FeedItem {
  /** The event it came from. One event can post two items, like a backing that changes the lead, so key on seq and kind. */
  seq: number;
  kind: "seed" | "twist" | "merge" | "back" | "drop" | "object" | "constraint" | "birth" | "death" | "lead" | "proposal" | "rest" | "end" | "summary";
  actor?: string;
  text: string;
  quote?: string;
  branchId?: string;
}

/** A character's turn: what it did, what it said, and why. */
export interface Decision {
  seq: number;
  action: AgentDecision["payload"]["action"];
  say: string;
  reason: string;
  branchId?: string;
}

export interface Objection {
  seq: number;
  agentId: string;
  branchId: string;
  reason: string;
}

export interface Reflection {
  seq: number;
  insights: string[];
  agenda: string;
  impressions: { peerId: string; impression: string }[];
}

export interface Town {
  runId: string | null;
  seed: string | null;
  phase: Phase;
  characters: Record<string, Character>;
  /** The id of the character living on each lot, or null for an empty lot. */
  lots: (string | null)[];
  /** Each character's latest line, shown in a speech bubble above its house. */
  speech: Record<string, Speech>;
  /** The latest letters between houses, oldest first. */
  interactions: Interaction[];
  /** Every version of the idea, in the order they were made. */
  branches: Record<string, Branch>;
  /** The branches each character currently backs. */
  endorsements: Record<string, string[]>;
  /** Why each character backs a branch, keyed by `backingKey`. */
  backingReasons: Record<string, string>;
  /** Every letter between houses, oldest first, for the side panel. */
  letters: Interaction[];
  /** Every turn each character took, oldest first. */
  decisions: Record<string, Decision[]>;
  objections: Objection[];
  /** Each character's latest reflection. */
  reflections: Record<string, Reflection>;
  lastWords: Record<string, string>;
  resting: string[];
  constraints: string[];
  /** Town news since the run started, oldest first. */
  feed: FeedItem[];
  /** The branch with the most living backers, once one leads outright. */
  leader: string | null;
  /** Living backers besides its authors that make a branch a proposal. Null when proposals are off. */
  quorum: number | null;
  /** Branches their authors withdrew, because nobody liked them, they were over budget, or the author died. */
  retired: string[];
  /** The branches that are proposals right now. */
  proposals: string[];
  finalists: Finalist[] | null;
  baseline: string[];
  summary: Summary | null;
  lastSeq: number;
}

export const backingKey = (agentId: string, branchId: string) => `${agentId}|${branchId}`;

export const initialTown: Town = {
  runId: null,
  seed: null,
  phase: "ask",
  characters: {},
  lots: Array(LOT_COUNT).fill(null),
  speech: {},
  interactions: [],
  branches: {},
  endorsements: {},
  backingReasons: {},
  letters: [],
  decisions: {},
  objections: [],
  reflections: {},
  lastWords: {},
  resting: [],
  constraints: [],
  feed: [],
  leader: null,
  quorum: null,
  retired: [],
  proposals: [],
  finalists: null,
  baseline: [],
  summary: null,
  lastSeq: 0,
};

export type TownAction =
  | { kind: "submitted"; runId: string; seed: string }
  | { kind: "replay"; runId: string; seed: string | null; events: AnyEvent[] }
  | { kind: "event"; event: AnyEvent }
  | { kind: "reset" };

export function townReducer(town: Town, action: TownAction): Town {
  switch (action.kind) {
    case "submitted":
      return { ...initialTown, runId: action.runId, seed: action.seed, phase: "loading" };
    case "replay":
      return action.events.reduce<Town>(
        (state, event) => townReducer(state, { kind: "event", event }),
        { ...initialTown, runId: action.runId, seed: action.seed, phase: "loading" },
      );
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
    case "agent.born": {
      const moved = moveIn(town, event.payload.persona, event.seq);
      if (moved === town || town.phase !== "running") return moved;
      const replaces = event.payload.persona.lineage?.replaces;
      const text = replaces && town.characters[replaces] ? `moved in, in place of ${nameOf(town, replaces)}` : "moved into town";
      return news(moved, { seq: event.seq, kind: "birth", actor: event.payload.persona.id, text });
    }
    case "run.ready":
      return advance(town, "ready");
    case "run.started":
      return advance({ ...town, quorum: event.payload.quorum ?? null }, "running");
    case "agent.decision": {
      const id = event.agent_id;
      const spoken = say(town, id, event.payload.say, event.seq, false);
      if (!id || !town.characters[id]) return spoken;
      const { action, say: line, reason, target_branch } = event.payload;
      const decision: Decision = { seq: event.seq, action, say: line, reason: reason ?? "", branchId: target_branch ?? undefined };
      return { ...spoken, decisions: { ...spoken.decisions, [id]: [...(spoken.decisions[id] ?? []), decision] } };
    }
    case "agent.last_words": {
      const spoken = say(town, event.agent_id, event.payload.text, event.seq, true);
      return event.agent_id ? { ...spoken, lastWords: { ...spoken.lastWords, [event.agent_id]: event.payload.text } } : spoken;
    }
    case "branch.created": {
      const { branch_id, parent_ids, op, authors, text } = event.payload;
      if (town.branches[branch_id]) return town;
      const branch: Branch = { id: branch_id, parentIds: parent_ids, op, authors, text, seq: event.seq };
      const grown = { ...town, branches: { ...town.branches, [branch_id]: branch } };
      if (op === "seed") return grown;
      const actor = authors.find((a) => town.characters[a]);
      const others = authors.filter((a) => a !== actor && town.characters[a]).map((a) => nameOf(town, a));
      const verb = op === "merge" ? `merged two ideas${others.length ? ` with ${others.join(" and ")}` : ""}` : "twisted the idea";
      return news(grown, { seq: event.seq, kind: op, actor, text: verb, quote: text, branchId: branch_id });
    }
    case "idea.share":
    case "idea.pitch":
    case "merge.propose":
      return interact(town, event.seq, event.agent_id, event.payload.to, "letter", event.payload.note);
    case "idea.reaction":
      return interact(town, event.seq, event.agent_id, event.payload.to, event.payload.answer === "accept" ? "agree" : "disagree", event.payload.text);
    case "pitch.response":
      return interact(town, event.seq, event.agent_id, event.payload.to, event.payload.persuaded ? "agree" : "disagree", event.payload.text);
    case "merge.response": {
      const { answer, text } = event.payload;
      return interact(town, event.seq, event.agent_id, event.payload.to, answer === "accept" ? "agree" : answer === "reject" ? "disagree" : "mixed", text);
    }
    case "endorse":
    case "unendorse": {
      const id = event.agent_id;
      if (!id) return town;
      const { branch_id, reason } = event.payload;
      const before = town.endorsements[id] ?? [];
      const backed = before.filter((b) => b !== branch_id);
      if (event.type === "endorse") backed.push(branch_id);
      const backs = event.type === "endorse";
      let next: Town = { ...town, endorsements: { ...town.endorsements, [id]: backed } };
      if (reason?.trim()) next = { ...next, backingReasons: { ...next.backingReasons, [backingKey(id, branch_id)]: reason } };
      const changed = backs ? !before.includes(branch_id) : before.includes(branch_id);
      // Backing a withdrawn branch brings it back.
      if (changed && backs && next.retired.includes(branch_id)) next = { ...next, retired: next.retired.filter((b) => b !== branch_id) };
      if (changed && town.characters[id] && town.branches[branch_id]) {
        next = news(next, { seq: event.seq, kind: backs ? "back" : "drop", actor: id, text: backs ? "now backs" : "stopped backing", quote: town.branches[branch_id].text, branchId: branch_id });
      }
      return lead(propose(next, event.seq), event.seq);
    }
    case "branch.withdraw": {
      const { branch_id } = event.payload;
      // Backing it brings it back, so a withdraw that lands after someone backed it changes nothing.
      if (town.retired.includes(branch_id) || livingSupport(town)[branch_id]?.length) return town;
      return { ...town, retired: [...town.retired, branch_id] };
    }
    case "objection": {
      const id = event.agent_id;
      const { branch_id, reason = "" } = event.payload;
      if (!id || !town.characters[id]) return town;
      const objected = { ...town, objections: [...town.objections, { seq: event.seq, agentId: id, branchId: branch_id, reason }] };
      return news(objected, { seq: event.seq, kind: "object", actor: id, text: "objected to", quote: town.branches[branch_id]?.text ?? branch_id, branchId: branch_id });
    }
    case "agent.reflection": {
      const id = event.agent_id;
      if (!id || !town.characters[id]) return town;
      const { insights = [], agenda = "", impressions = [] } = event.payload;
      const reflection: Reflection = { seq: event.seq, insights, agenda, impressions: impressions.map((i) => ({ peerId: i.peer_id, impression: i.impression })) };
      return { ...town, reflections: { ...town.reflections, [id]: reflection } };
    }
    case "rest": {
      const id = event.agent_id;
      if (!id || !town.characters[id] || town.resting.includes(id)) return town;
      return news({ ...town, resting: [...town.resting, id] }, { seq: event.seq, kind: "rest", actor: id, text: "has said their piece" });
    }
    case "world.event": {
      const { kind, text } = event.payload;
      if (kind === "seed") return news(town, { seq: event.seq, kind: "seed", text: "The town hears your idea", quote: text });
      if (kind !== "constraint") return town;
      return news({ ...town, constraints: [...town.constraints, text] }, { seq: event.seq, kind: "constraint", text: "New rule for the town", quote: text });
    }
    case "agent.died": {
      const id = event.agent_id;
      const character = id ? town.characters[id] : undefined;
      if (!id || !character || !character.alive) return town;
      const died = { ...town, characters: { ...town.characters, [id]: { ...character, alive: false, diedSeq: event.seq } } };
      return lead(propose(news(died, { seq: event.seq, kind: "death", actor: id, text: "left town for good" }), event.seq), event.seq);
    }
    case "run.ended": {
      const ended = { ...advance(town, "ended"), finalists: event.payload.finalists ?? [], baseline: event.payload.baseline ?? [] };
      return news(ended, { seq: event.seq, kind: "end", text: "The town has finished talking" });
    }
    case "run.summary": {
      const p = event.payload;
      const summary: Summary = {
        headline: p.headline ?? "",
        verdicts: p.verdicts ?? [],
        buckets: p.buckets ?? [],
        moments: p.moments ?? [],
        baseline: p.baseline ?? [],
        model: p.model,
        error: p.error,
      };
      if (summary.error) return { ...town, summary };
      return news({ ...town, summary }, { seq: event.seq, kind: "summary", text: "The verdict is in", quote: summary.headline });
    }
    default:
      return town;
  }
}

function nameOf(town: Town, id: string): string {
  const persona = town.characters[id]?.persona;
  return persona ? shortName(persona.name) : id;
}

function news(town: Town, item: FeedItem): Town {
  return { ...town, feed: [...town.feed, item] };
}

/** Posts the news when a different branch takes the outright lead, with at least two backers. */
function lead(town: Town, seq: number): Town {
  const ranked = Object.entries(livingSupport(town))
    .filter(([id]) => town.branches[id] && town.branches[id].op !== "seed")
    .sort((a, b) => b[1].length - a[1].length);
  const [top, next] = ranked;
  const leader = top && top[1].length >= 2 && top[1].length > (next?.[1].length ?? 0) ? top[0] : town.leader;
  if (leader === town.leader || !leader) return town;
  const backers = top[1].length;
  return news({ ...town, leader }, { seq, kind: "lead", text: `New front-runner, with ${backers} backers`, quote: town.branches[leader].text, branchId: leader });
}

/** Keeps the list of proposals, and posts the news when a branch becomes one. */
function propose(town: Town, seq: number): Town {
  const support = livingSupport(town);
  const now = Object.keys(support).filter((id) => isProposal(town, id, support));
  const fresh = now.filter((id) => !town.proposals.includes(id));
  if (fresh.length === 0 && now.length === town.proposals.length) return town;
  let next: Town = { ...town, proposals: now };
  for (const id of fresh) {
    next = news(next, { seq, kind: "proposal", text: `New proposal, with ${support[id].length} backers`, quote: town.branches[id].text, branchId: id });
  }
  return next;
}

function say(town: Town, id: string | null, text: string, seq: number, lastWords: boolean): Town {
  if (!id || !town.characters[id] || !text.trim()) return town;
  return { ...town, speech: { ...town.speech, [id]: { text, seq, lastWords } } };
}

/** Adds a letter between two characters who are both living in town. */
function interact(town: Town, seq: number, from: string | null, to: string, kind: Interaction["kind"], text = ""): Town {
  const home = (id: string | null) => (id ? town.lots[town.characters[id]?.lot ?? -1] === id : false);
  if (!from || from === to || !home(from) || !home(to)) return town;
  const letter: Interaction = { seq, from, to, kind, text: text.trim() };
  return { ...town, interactions: [...town.interactions, letter].slice(-MAX_INTERACTIONS), letters: [...town.letters, letter] };
}

/** A newcomer takes the lot of the character it replaces, otherwise the first empty lot. */
function moveIn(town: Town, persona: Persona, bornSeq: number): Town {
  if (town.characters[persona.id]) return town;
  const replaces = persona.lineage?.replaces;
  const replacedLot = replaces ? town.characters[replaces]?.lot : undefined;
  const lot = replacedLot ?? town.lots.indexOf(null);
  if (lot === -1) return town;
  const lots = [...town.lots];
  lots[lot] = persona.id;
  return { ...town, lots, characters: { ...town.characters, [persona.id]: { persona, alive: true, lot, bornSeq } } };
}

/** Who backs each branch, counting only characters who are still alive. */
export function livingSupport(town: Town): Record<string, string[]> {
  const support: Record<string, string[]> = {};
  for (const [id, branches] of Object.entries(town.endorsements)) {
    if (!town.characters[id]?.alive) continue;
    for (const branch of branches) (support[branch] ??= []).push(id);
  }
  return support;
}

/** Backed by at least the quorum of living characters besides its own authors. */
export function isProposal(town: Town, branchId: string, support = livingSupport(town)): boolean {
  const branch = town.branches[branchId];
  if (!branch || town.quorum === null) return false;
  return (support[branchId] ?? []).filter((a) => !branch.authors.includes(a)).length >= town.quorum;
}

/** Its author withdrew it and no living character backs it. */
export function isComposted(town: Town, branchId: string, support = livingSupport(town)): boolean {
  return town.retired.includes(branchId) && !support[branchId]?.length;
}
