// Builds a different town for every run, seeded by the run id so a refresh or a replay of
// the same run always shows the same town.
//
// Every town is a compact block with rounded corners. A main street runs along x with a row
// of lots behind it, and a side street turns toward the camera with lots along its left side.
// What changes from run to run: the length of both streets, whether the main street ends in a
// corner or carries on past the side street, which slots get buildings and which become small
// parks, which buildings go where, and the crosswalk, cars, props and rounded corners.
//
// The rules every town follows:
//   - Buildings only face +x or +z, which is toward the camera.
//   - Every building's front is on a road.
//   - No building stands diagonally in front of another one and hides it.
//   - Every prop stands on a block.
//
// Positions here are in tiles. One tile is 2 world units, the size of a KayKit block.
//
//   x:   -1    0     1     2     3   ...
//   z=0  lot   lot   lot   lot   lot        lots face +z, onto the main street
//   z=1  lot   corner road  road  road       main street
//   z=2  lot   road  plaza plaza plaza       lots face +x, onto the side street
//   z=3  lot   road  plaza plaza plaza

import { BUILDINGS, TILE, type Placement } from "./layout";

type Dir = readonly [number, number];
const EAST: Dir = [1, 0]; // +x
const WEST: Dir = [-1, 0];
const SOUTH: Dir = [0, 1]; // +z
const NORTH: Dir = [0, -1];
const SIDES = [EAST, WEST, SOUTH, NORTH];

/** KayKit buildings face +z at rotation 0 and +x at a quarter turn. */
export const FACE_SOUTH = 0;
export const FACE_EAST = Math.PI / 2;

const QUARTER_TURNS = [0, Math.PI / 2, Math.PI, (3 * Math.PI) / 2];

export interface CityPlan {
  /** Six lots in the order characters move in, left to right across the screen. */
  lots: Placement[];
  scenery: Placement[];
  /** The building each newcomer gets, in birth order. Shuffled per run. */
  buildingOrder: string[];
  /** Every block the platform is made of, in tiles, for framing the camera. */
  blocks: [number, number][];
}

/** Small seeded random number generator (mulberry32). */
export function seededRandom(seed: string): () => number {
  let h = 0x811c9dc5;
  for (let i = 0; i < seed.length; i++) h = Math.imul(h ^ seed.charCodeAt(i), 0x01000193);
  let a = h >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** Rotates a direction the way three.js turns a model by `angle` around the vertical axis. */
export function turn([x, z]: Dir, angle: number): Dir {
  const c = Math.round(Math.cos(angle));
  const s = Math.round(Math.sin(angle));
  return [x * c + z * s + 0, -x * s + z * c + 0];
}

/**
 * The sides each road piece opens onto at rotation 0. Checked by rendering each piece from
 * above with its +x and +z sides marked, so these are the real directions, not guesses.
 */
const OPENINGS: Record<string, Dir[]> = {
  road_straight: [NORTH, SOUTH],
  road_straight_crossing: [NORTH, SOUTH],
  road_corner: [EAST, SOUTH],
  road_tsplit: [NORTH, SOUTH, EAST],
  road_junction: [NORTH, SOUTH, EAST, WEST],
};

/** Which sides a road piece opens onto, at a given rotation. */
export function roadOpenings(model: string, rotation: number): Dir[] {
  return OPENINGS[model].map((dir) => turn(dir, rotation));
}

const key = (x: number, z: number) => `${x},${z}`;
const sameSides = (a: Dir[], b: Dir[]) =>
  a.length === b.length && a.every(([x, z]) => b.some(([bx, bz]) => bx === x && bz === z));

/** The road piece and rotation that opens onto exactly these sides. */
function pieceFor(sides: Dir[]): { model: string; rotation: number } {
  for (const model of ["road_straight", "road_corner", "road_tsplit", "road_junction"]) {
    for (const rotation of QUARTER_TURNS) {
      if (sameSides(roadOpenings(model, rotation), sides)) return { model, rotation };
    }
  }
  throw new Error("no road piece fits");
}

/** True when lot `b` stands diagonally in front of lot `a`, close enough to hide its front. */
export function hides(b: [number, number], a: [number, number]): boolean {
  const dx = b[0] - a[0];
  const dz = b[1] - a[1];
  return dx >= 1 && dx <= 2 && dz >= 1 && dz <= 2;
}

const PROPS: [string, number][] = [
  ["bush", 0.38],
  ["bench", 0.16],
  ["trash_A", 0.1],
  ["firehydrant", 0.08],
  ["dumpster", 0.06],
];

export function planCity(seed: string): CityPlan {
  const rand = seededRandom(seed);
  const between = (lo: number, hi: number) => lo + Math.floor(rand() * (hi - lo + 1));
  const pick = <T,>(items: T[]) => items[Math.floor(rand() * items.length)];
  const shuffle = <T,>(items: T[]) =>
    items.map((item) => [rand(), item] as const).sort((a, b) => a[0] - b[0]).map(([, item]) => item);

  // The streets.
  const mainLength = between(4, 5);
  const sideLength = between(2, 3);
  const carriesOn = rand() < 0.35; // the main street runs one block past the side street, making a T
  const roadTiles: [number, number][] = [];
  for (let x = carriesOn ? -1 : 0; x < mainLength; x++) roadTiles.push([x, 1]);
  for (let z = 2; z < 2 + sideLength; z++) roadTiles.push([0, z]);
  const roadKeys = new Set(roadTiles.map(([x, z]) => key(x, z)));

  // The slots buildings can go in: behind the main street, and left of the side street.
  const slots: { x: number; z: number; rotation: number }[] = [];
  for (let x = carriesOn ? -1 : 0; x < mainLength; x++) slots.push({ x, z: 0, rotation: FACE_SOUTH });
  for (let z = carriesOn ? 2 : 1; z < 2 + sideLength; z++) slots.push({ x: -1, z, rotation: FACE_EAST });
  const lots = shuffle(slots).slice(0, 6);
  lots.sort((a, b) => a.x - a.z - (b.x - b.z) || a.x - b.x);
  const lotKeys = new Set(lots.map((l) => key(l.x, l.z)));

  // The platform is the whole rectangle around the streets and slots, with its corners rounded off.
  const xs = [...roadTiles.map(([x]) => x), ...slots.map((s) => s.x)];
  const zs = [...roadTiles.map(([, z]) => z), ...slots.map((s) => s.z)];
  const [minX, maxX, minZ, maxZ] = [Math.min(...xs), Math.max(...xs), Math.min(...zs), Math.max(...zs)];
  const ground = new Map<string, [number, number]>();
  for (let x = minX; x <= maxX; x++) {
    for (let z = minZ; z <= maxZ; z++) {
      if (!roadKeys.has(key(x, z)) && !lotKeys.has(key(x, z))) ground.set(key(x, z), [x, z]);
    }
  }
  for (const [cx, cz, inX, inZ] of [
    [minX, minZ, 1, 1],
    [maxX, minZ, -1, 1],
    [minX, maxZ, 1, -1],
    [maxX, maxZ, -1, -1],
  ]) {
    if (!ground.delete(key(cx, cz))) continue;
    // Sometimes round a corner a little more by taking one more block next to it.
    if (rand() < 0.4) ground.delete(rand() < 0.5 ? key(cx + inX, cz) : key(cx, cz + inZ));
  }

  const world = (x: number, z: number): [number, number, number] => [x * TILE, 0, z * TILE];
  const scenery: Placement[] = [];

  // Road pieces, chosen so each one opens onto exactly the road blocks next to it. The ends of
  // the streets run off the edge of the platform, as if the town carries on.
  const roads = roadTiles.map(([x, z]) => {
    let sides = SIDES.filter(([dx, dz]) => roadKeys.has(key(x + dx, z + dz)));
    if (sides.length === 1) sides = [sides[0], [-sides[0][0], -sides[0][1]] as Dir];
    return { x, z, ...pieceFor(sides) };
  });
  const straights = shuffle(roads.filter((r) => r.model === "road_straight"));
  const crossing = straights[0];
  for (const road of roads) {
    const model = road === crossing ? "road_straight_crossing" : road.model;
    scenery.push({ model, position: world(road.x, road.z), rotation: road.rotation });
  }
  for (const road of straights.slice(1, 3)) {
    const alongX = Math.abs(Math.sin(road.rotation)) > 0.5;
    const side = rand() < 0.5 ? -0.5 : 0.5;
    scenery.push({
      model: pick(["car_taxi", "car_sedan", "car_hatchback", "car_stationwagon"]),
      position: [road.x * TILE + (alongX ? 0 : side), 0.1, road.z * TILE + (alongX ? side : 0)],
      rotation: road.rotation,
    });
  }

  // Sidewalk blocks, including any empty slots, some with a low prop on them.
  for (const [x, z] of ground.values()) {
    scenery.push({ model: "base", position: world(x, z), rotation: 0 });
    let roll = rand();
    for (const [model, chance] of PROPS) {
      if (roll < chance) {
        const jitter = () => (rand() - 0.5) * 1.1;
        scenery.push({ model, position: [x * TILE + jitter(), 0.1, z * TILE + jitter()], rotation: pick(QUARTER_TURNS) });
        break;
      }
      roll -= chance;
    }
  }

  return {
    lots: lots.map((l) => ({ model: "base", position: world(l.x, l.z), rotation: l.rotation })),
    scenery,
    buildingOrder: shuffle(BUILDINGS),
    blocks: [...roadTiles, ...lots.map((l): [number, number] => [l.x, l.z]), ...ground.values()],
  };
}
