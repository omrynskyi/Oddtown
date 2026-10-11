import { describe, expect, it } from "vitest";
import type { AnyEvent } from "../events";
import mockRun from "../fixtures/mock-run.json";
import { initialTown, townReducer, type Branch, type Town } from "../town";
import { layoutTree, NODE_HEIGHT, NODE_WIDTH } from "./layout";

const events = mockRun as AnyEvent[];
const start: Town = { ...initialTown, runId: events[0].run_id };
const branches = Object.values(events.reduce((t: Town, event) => townReducer(t, { kind: "event", event }), start).branches);

const branch = (id: string, parentIds: string[], seq: number, op: Branch["op"] = "twist"): Branch => ({
  id,
  parentIds,
  op,
  authors: ["char_a"],
  text: id,
  seq,
});

describe("tree layout", () => {
  it("places every branch once, with the seed in the first column", () => {
    const layout = layoutTree(branches);
    expect(layout.nodes.map((n) => n.id).sort()).toEqual(branches.map((b) => b.id).sort());
    const seed = layout.nodes.find((n) => n.branch?.op === "seed")!;
    expect(seed.left).toBe(0);
    expect(Math.min(...layout.nodes.map((n) => n.top))).toBe(0);
  });

  it("puts every branch one column to the right of the branch it hangs off", () => {
    const layout = layoutTree(branches);
    for (const link of layout.links.filter((l) => !l.extra)) {
      expect(link.to.left).toBeGreaterThan(link.from.left);
    }
    expect(layout.links.filter((l) => !l.extra)).toHaveLength(branches.length - 1);
  });

  it("draws a dashed link to a merge's second parent", () => {
    const layout = layoutTree(branches);
    const merges = branches.filter((b) => b.op === "merge");
    expect(merges.length).toBeGreaterThan(0);
    const extras = layout.links.filter((l) => l.extra);
    expect(extras).toHaveLength(merges.length);
    for (const merge of merges) expect(extras.some((l) => l.to.id === merge.id && l.from.id === merge.parentIds[1])).toBe(true);
  });

  it("never overlaps two nodes", () => {
    const { nodes } = layoutTree(branches);
    for (const a of nodes) {
      for (const b of nodes) {
        if (a === b) continue;
        const apart = Math.abs(a.left - b.left) >= NODE_WIDTH || Math.abs(a.top - b.top) >= NODE_HEIGHT;
        expect(apart, `${a.id} overlaps ${b.id}`).toBe(true);
      }
    }
  });

  it("still draws a tree if a branch arrives before its parent, or before the seed", () => {
    const early = layoutTree([branch("br_2", ["br_1"], 2), branch("br_1", ["br_0"], 3), branch("br_0", [], 4, "seed")]);
    expect(early.nodes.map((n) => n.id).sort()).toEqual(["br_0", "br_1", "br_2"]);
    const noSeed = layoutTree([branch("br_5", ["br_4"], 1)]);
    expect(noSeed.nodes).toHaveLength(2);
    expect(layoutTree([])).toEqual({ nodes: [], links: [], width: 0, height: 0 });
  });
});
