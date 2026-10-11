import { describe, expect, it } from "vitest";
import { FACE_EAST, FACE_SOUTH, hides, planCity, roadOpenings, turn } from "./generate";
import { ALL_MODELS, BUILDINGS, TILE } from "./layout";

const SEEDS = Array.from({ length: 300 }, (_, i) => `run_${i.toString(16).padStart(8, "0")}`);
const tileOf = ([x, , z]: number[]) => [Math.round(x / TILE), Math.round(z / TILE)] as const;
const k = ([x, z]: readonly number[]) => `${x},${z}`;

describe("town generator", () => {
  it("gives the same town for the same run, and different towns for different runs", () => {
    expect(planCity("run_abc")).toEqual(planCity("run_abc"));
    const towns = new Set(SEEDS.slice(0, 50).map((s) => JSON.stringify(planCity(s))));
    expect(towns.size).toBe(50);
    const shapes = new Set(SEEDS.slice(0, 50).map((s) => JSON.stringify(planCity(s).blocks.map(String).sort())));
    expect(shapes.size).toBeGreaterThan(10);
  });

  it.each(SEEDS)("follows every rule for %s", (seed) => {
    const plan = planCity(seed);
    const roads = plan.scenery.filter((s) => s.model.startsWith("road_"));
    const ground = plan.scenery.filter((s) => s.model === "base");
    const props = plan.scenery.filter((s) => s.position[1] > 0);
    const roadTiles = new Set(roads.map((r) => k(tileOf(r.position))));
    const lotTiles = plan.lots.map((l) => tileOf(l.position));

    // Six lots, every one facing the camera, with a road right in front of it.
    expect(plan.lots).toHaveLength(6);
    for (const lot of plan.lots) {
      expect([FACE_SOUTH, FACE_EAST]).toContain(lot.rotation);
      const [x, z] = tileOf(lot.position);
      const front = turn([0, 1], lot.rotation);
      expect(roadTiles.has(k([x + front[0], z + front[1]])), `${seed}: lot at ${x},${z} has no road in front`).toBe(true);
    }

    // Nothing shares a block, and no building hides another.
    const blocks = [...roads, ...ground].map((s) => k(tileOf(s.position))).concat(lotTiles.map(k));
    expect(new Set(blocks).size).toBe(blocks.length);
    for (const a of lotTiles) for (const b of lotTiles) expect(hides(b as [number, number], a as [number, number])).toBe(false);

    // Every prop and car stands on a block that's really there.
    const solid = new Set(blocks);
    for (const prop of props) {
      const onBlock = [-1, 0, 1].some((dx) =>
        [-1, 0, 1].some((dz) => {
          const [bx, bz] = [Math.round(prop.position[0] / TILE) + dx, Math.round(prop.position[2] / TILE) + dz];
          return solid.has(k([bx, bz])) && Math.abs(prop.position[0] - bx * TILE) <= 1 && Math.abs(prop.position[2] - bz * TILE) <= 1;
        }),
      );
      expect(onBlock, `${seed}: ${prop.model} floats at ${prop.position}`).toBe(true);
    }

    // Roads join up: each piece opens onto its neighbors in the road, and corners open onto exactly them.
    for (const road of roads) {
      const [x, z] = tileOf(road.position);
      const openings = roadOpenings(road.model, road.rotation);
      const neighbors = [[1, 0], [-1, 0], [0, 1], [0, -1]].filter(([dx, dz]) => roadTiles.has(k([x + dx, z + dz])));
      for (const [dx, dz] of neighbors) {
        expect(openings.some(([ox, oz]) => ox === dx && oz === dz), `${seed}: ${road.model} at ${x},${z} is closed toward ${dx},${dz}`).toBe(true);
      }
    }

    // A compact block, not a long line: never more than 7 blocks wide or deep.
    const xs = blocks.map((b) => Number(b.split(",")[0]));
    const zs = blocks.map((b) => Number(b.split(",")[1]));
    expect(Math.max(...xs) - Math.min(...xs)).toBeLessThanOrEqual(6);
    expect(Math.max(...zs) - Math.min(...zs)).toBeLessThanOrEqual(6);

    // Only known models, and six neighbors get six different buildings.
    for (const piece of [...plan.scenery, ...plan.lots]) expect(ALL_MODELS).toContain(piece.model);
    expect([...plan.buildingOrder].sort()).toEqual([...BUILDINGS].sort());
  });
});
