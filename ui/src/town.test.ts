import { describe, expect, it } from "vitest";
import type { AnyEvent } from "./events";
import mockRun from "./fixtures/mock-run.json";
import { initialTown, townReducer, type Town } from "./town";

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

  it("ignores event types the UI doesn't use", () => {
    const town = play(upTo("run.ready"));
    const share = { run_id: RUN, seq: town.lastSeq + 1, ts: 0, type: "idea.share", agent_id: "char_dez", payload: {} };
    const next = townReducer(town, { kind: "event", event: share });
    expect({ ...next, lastSeq: town.lastSeq }).toEqual(town);
    expect(next.lastSeq).toBe(town.lastSeq + 1);
  });
});
