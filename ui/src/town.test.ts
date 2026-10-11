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

  describe("town news and history", () => {
    const town = play(events);
    const base = play(upTo("run.started"));
    const send = (type: string, agent: string | null, payload: Record<string, unknown>, from = base) =>
      townReducer(from, { kind: "event", event: { run_id: RUN, seq: from.lastSeq + 1, ts: 0, type, agent_id: agent, payload } });

    it("posts news for twists, merges, backing, the rule, the death, the lead and the end", () => {
      const kinds = new Set(town.feed.map((f) => f.kind));
      for (const kind of ["seed", "twist", "merge", "back", "drop", "constraint", "death", "birth", "lead", "end"]) expect(kinds).toContain(kind);
      const seqs = town.feed.map((f) => f.seq);
      expect(seqs).toEqual([...seqs].sort((a, b) => a - b));
    });

    it("keeps no news from before the run started", () => {
      expect(play(upTo("run.ready")).feed).toEqual([]);
    });

    it("keeps every letter and every decision with its reason", () => {
      const sent = events.filter((e) => ["idea.share", "idea.reaction", "idea.pitch", "pitch.response", "merge.propose", "merge.response"].includes(e.type));
      expect(town.letters.length).toBeGreaterThan(8);
      expect(town.letters.length).toBeLessThanOrEqual(sent.length);
      const decisions = Object.values(town.decisions).flat();
      expect(decisions).toHaveLength(events.filter((e) => e.type === "agent.decision").length);
      expect(decisions.every((d) => d.reason)).toBe(true);
    });

    it("keeps the finalists and baseline from run.ended", () => {
      expect(town.finalists?.map((f) => f.branch_id)).toEqual(["br_002", "br_001", "br_003"]);
      expect(town.baseline).toHaveLength(3);
    });

    it("records objections and backing reasons, with news for the objection", () => {
      const objected = send("objection", "char_dez", { branch_id: "br_000", reason: "Too vague" });
      expect(objected.objections).toEqual([{ seq: base.lastSeq + 1, agentId: "char_dez", branchId: "br_000", reason: "Too vague" }]);
      expect(objected.feed.at(-1)).toMatchObject({ kind: "object", actor: "char_dez", branchId: "br_000" });
      const backed = send("endorse", "char_dez", { branch_id: "br_000", reason: "It's simple" });
      expect(backed.backingReasons["char_dez|br_000"]).toBe("It's simple");
    });

    it("takes the summary, and leaves news out when it failed", () => {
      const summary = { headline: "Jars win", verdicts: [{ branch_id: "br_002", title: "Jars", why: "", risk: "" }], buckets: [], moments: [], baseline: [] };
      const done = send("run.summary", null, summary, town);
      expect(done.summary).toMatchObject(summary);
      expect(done.feed.at(-1)).toMatchObject({ kind: "summary", quote: "Jars win" });
      const failed = send("run.summary", null, { error: "timeout" }, town);
      expect(failed.summary?.error).toBe("timeout");
      expect(failed.feed).toEqual(town.feed);
    });
  });
});
