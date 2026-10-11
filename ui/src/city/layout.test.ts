import { describe, expect, it } from "vitest";
import { assignBuildings, BUILDINGS, buildingFor, LOTS, SCENERY } from "./layout";

const CAST = ["char_marg", "char_dez", "char_anselm", "char_priya", "char_tamsin", "char_rook"];

describe("city layout", () => {
  it("has six lots on distinct tiles that nothing else sits on", () => {
    expect(LOTS).toHaveLength(6);
    const key = ([x, , z]: number[]) => `${x},${z}`;
    const lotTiles = LOTS.map((l) => key(l.position));
    expect(new Set(lotTiles).size).toBe(6);
    const tiles = SCENERY.filter((s) => s.position[1] === 0).map((s) => key(s.position));
    expect(tiles.filter((t) => lotTiles.includes(t))).toEqual([]);
  });

  it("gives a character the same preferred building every time", () => {
    for (const id of CAST) {
      expect(buildingFor(id)).toBe(buildingFor(id));
      expect(BUILDINGS).toContain(buildingFor(id));
    }
  });

  it("gives six neighbors six different buildings", () => {
    const assigned = assignBuildings(CAST);
    expect(new Set(Object.values(assigned)).size).toBe(6);
  });

  it("never changes an existing character's building when someone new moves in", () => {
    const before = assignBuildings(CAST);
    const after = assignBuildings([...CAST, "char_ines", "char_newer", "char_newest"]);
    for (const id of CAST) expect(after[id]).toBe(before[id]);
    expect(new Set(Object.values(after).slice(0, 8)).size).toBe(8);
  });
});
