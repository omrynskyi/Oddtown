import { describe, expect, it } from "vitest";
import type { AnyEvent } from "./events";
import mockRun from "./fixtures/mock-run.json";
import { initialTown, livingSupport, townReducer, type Town } from "./town";

// The fixture is a full scripted run from the mock world service.
// Regenerate it with: .venv/bin/python -m observer.mock_script > ui/src/fixtures/mock-run.json
const events = mockRun as AnyEvent[];
const RUN = events[0].run_id;

function play(list: AnyEvent[], town: Town = townReducer(initialTown, { kind: "submitted", runId: RUN, seed: "a coffee subscription" })) {
  return list.reduce((t, event) => townReducer(t, { kind: "event", event }), town);
}

function upTo(type: string) {
  return events.slice(0, events.findIndex((e) => e.type === type) + 1);
}

describe("town reducer", () => {
  it("starts on the Ask screen and moves to loading once an idea is submitted", () => {
    expect(initialTown.phase).toBe("ask");
    const town = townReducer(initialTown, { kind: "submitted", runId: RUN, seed: "a coffee subscription" });
    expect(town).toMatchObject({ phase: "loading", runId: RUN, seed: "a coffee subscription" });
  });

  it("moves six characters onto six lots in birth order, then becomes ready", () => {
    const loading = play(events.slice(0, 4)); // run.created and the first three births
    expect(loading.phase).toBe("loading");
    expect(loading.lots.filter(Boolean)).toHaveLength(3);

    const ready = play(upTo("run.ready"));
    expect(ready.phase).toBe("ready");
    const born = events.filter((e) => e.type === "agent.born").slice(0, 6).map((e) => e.agent_id);
    expect(ready.lots).toEqual(born);
    expect(Object.values(ready.characters).every((c) => c.alive)).toBe(true);
  });

  it("runs, swaps the dead character's lot to its replacement, and ends", () => {
    expect(play(upTo("run.started")).phase).toBe("running");

    const town = play(events);
    expect(town.phase).toBe("ended");
    expect(town.lastSeq).toBe(events.length);

    const died = events.find((e) => e.type === "agent.died")!;
    const dead = town.characters[died.agent_id!];
    expect(dead.alive).toBe(false);
    expect(town.lots).not.toContain(died.agent_id);
    expect(town.lots[dead.lot]).toBe("char_ines");
    expect(town.characters.char_ines).toMatchObject({ alive: true, lot: dead.lot });
  });

  it("ignores repeats after a reconnect and events from other runs", () => {
    const once = play(events);
    expect(play(events, once)).toBe(once);
    const other = { ...events[1], run_id: "run_other", seq: events.length + 1 };
    expect(townReducer(once, { kind: "event", event: other })).toBe(once);
  });

  it("never moves backwards when a finished run is replayed into a later phase", () => {
    const ended = play(events);
    const replayedCreate = { ...events[0], seq: events.length + 1 };
    expect(townReducer(ended, { kind: "event", event: replayedCreate }).phase).toBe("ended");
  });

  it("rebuilds a saved run at any point on its event timeline", () => {
    const midway = upTo("run.started");
    const replayed = townReducer(initialTown, { kind: "replay", runId: RUN, seed: null, events: midway });
    expect(replayed.phase).toBe("running");
    expect(replayed.lastSeq).toBe(midway.at(-1)!.seq);
    expect(replayed.lots.filter(Boolean)).toHaveLength(6);
    expect(townReducer(initialTown, { kind: "replay", runId: RUN, seed: null, events }).phase).toBe("ended");
  });

  it("keeps each character's latest line, including its last words", () => {
    const decisions = events.filter((e) => e.type === "agent.decision");
    const firstSpeaker = decisions[0].agent_id!;
    const town = play(events.slice(0, events.indexOf(decisions[0]) + 1));
    expect(town.speech[firstSpeaker]).toEqual({ text: decisions[0].payload.say, seq: decisions[0].seq, lastWords: false });

    const end = play(events);
    const lastLine = [...decisions].reverse().find((e) => e.agent_id === firstSpeaker)!;
    const died = events.find((e) => e.type === "agent.died")!;
    const lastWords = events.find((e) => e.type === "agent.last_words")!;
    if (firstSpeaker !== died.agent_id) expect(end.speech[firstSpeaker].seq).toBe(lastLine.seq);
    expect(end.speech[died.agent_id!]).toEqual({ text: lastWords.payload.text, seq: lastWords.seq, lastWords: true });
  });

  it("ignores lines from characters who never moved in", () => {
    const town = play(upTo("run.started"));
    const stranger = { run_id: RUN, seq: town.lastSeq + 1, ts: 0, type: "agent.decision", agent_id: "char_nobody", payload: { action: "twist", say: "Hi", reason: "" } };
    expect(townReducer(town, { kind: "event", event: stranger }).speech).toEqual({});
  });

  it("collects every branch, with its parents and authors", () => {
    const town = play(events);
    const created = events.filter((e) => e.type === "branch.created");
    expect(Object.keys(town.branches)).toEqual(created.map((e) => e.payload.branch_id));
    const merge = created.find((e) => e.payload.op === "merge")!;
    expect(town.branches[merge.payload.branch_id as string]).toMatchObject({
      op: "merge",
      parentIds: merge.payload.parent_ids,
      authors: merge.payload.authors,
    });
  });

  it("counts support from living backers only, after every endorse and unendorse", () => {
    const town = play(events);
    // Replay the endorsements by hand to get each character's final picks.
    const backed: Record<string, Set<string>> = {};
    for (const e of events) {
      if (e.type === "endorse") (backed[e.agent_id!] ??= new Set()).add(e.payload.branch_id as string);
      if (e.type === "unendorse") backed[e.agent_id!].delete(e.payload.branch_id as string);
    }
    const dead = events.find((e) => e.type === "agent.died")!.agent_id!;
    const expected: Record<string, string[]> = {};
    for (const [id, set] of Object.entries(backed)) {
      if (id === dead) continue;
      for (const b of set) (expected[b] ??= []).push(id);
    }
    const support = livingSupport(town);
    for (const b of new Set([...Object.keys(expected), ...Object.keys(support)])) {
      expect([...(support[b] ?? [])].sort()).toEqual([...(expected[b] ?? [])].sort());
    }
    expect(Object.values(town.endorsements).every((list) => list.length <= 2)).toBe(true);
  });

  it("ignores event types the UI doesn't use", () => {
    const town = play(upTo("run.ready"));
    const share = { run_id: RUN, seq: town.lastSeq + 1, ts: 0, type: "human.request", agent_id: "char_dez", payload: {} };
    const next = townReducer(town, { kind: "event", event: share });
    expect({ ...next, lastSeq: town.lastSeq }).toEqual(town);
    expect(next.lastSeq).toBe(town.lastSeq + 1);
  });

  describe("letters between houses", () => {
    const base = play(upTo("run.started"));
    const send = (type: string, agent: string, payload: Record<string, unknown>, from = base) =>
      townReducer(from, { kind: "event", event: { run_id: RUN, seq: from.lastSeq + 1, ts: 0, type, agent_id: agent, payload } });

    it("makes a letter of each message, carrying the note", () => {
      for (const type of ["idea.share", "idea.pitch", "merge.propose"]) {
        const town = send(type, "char_dez", { to: "char_marg", note: " Look at this " });
        expect(town.interactions).toEqual([{ seq: base.lastSeq + 1, from: "char_dez", to: "char_marg", kind: "letter", text: "Look at this" }]);
      }
    });

    it("reads each reply as agree, disagree or mixed", () => {
      const kind = (type: string, payload: Record<string, unknown>) => send(type, "char_marg", { to: "char_dez", ...payload }).interactions[0].kind;
      expect(kind("idea.reaction", { answer: "accept" })).toBe("agree");
      expect(kind("idea.reaction", { answer: "reject" })).toBe("disagree");
      expect(kind("pitch.response", { persuaded: true })).toBe("agree");
      expect(kind("pitch.response", { persuaded: false })).toBe("disagree");
      expect(kind("merge.response", { answer: "accept" })).toBe("agree");
      expect(kind("merge.response", { answer: "reject" })).toBe("disagree");
      expect(kind("merge.response", { answer: "counter" })).toBe("mixed");
    });

    it("ignores letters to or from people who aren't in town, or to yourself", () => {
      expect(send("idea.share", "char_dez", { to: "char_nobody" }).interactions).toEqual([]);
      expect(send("idea.share", "char_nobody", { to: "char_dez" }).interactions).toEqual([]);
      expect(send("idea.share", "char_dez", { to: "char_dez" }).interactions).toEqual([]);
      expect(send("idea.share", "char_dez", {}).interactions).toEqual([]);
    });

    it("keeps only the latest few, and none from the dead after they leave", () => {
      const town = play(events);
      expect(town.interactions.length).toBeGreaterThan(0);
      expect(town.interactions.length).toBeLessThanOrEqual(8);
      const seqs = town.interactions.map((i) => i.seq);
      expect(seqs).toEqual([...seqs].sort((a, b) => a - b));
      const died = events.find((e) => e.type === "agent.died")!.agent_id!;
      const afterDeath = send("idea.share", "char_dez", { to: died }, town);
      expect(afterDeath.interactions).toEqual(town.interactions);
    });

    it("sees the mock run's agreement and disagreement", () => {
      const kinds = new Set<string>();
      events.reduce((t, event) => {
        const next = townReducer(t, { kind: "event", event });
        next.interactions.forEach((i) => kinds.add(i.kind));
        return next;
      }, townReducer(initialTown, { kind: "submitted", runId: RUN, seed: "a coffee subscription" }));
      expect(kinds).toContain("letter");
      expect(kinds).toContain("agree");
      expect(kinds).toContain("disagree");
    });
  });
});
