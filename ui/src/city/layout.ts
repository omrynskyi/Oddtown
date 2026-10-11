// Where everything sits in the city. KayKit tiles are 2 by 2 units, so the city is a grid
// with a tile every 2 units. The camera looks in from the +x, +z corner, so the buildings
// line the two far edges and face it, and the near corner is an open plaza that hides nothing.
//
//   x:    -4      -2      0       2       4
//   z=-2  park    lot0    lot1    lot2    park      lots face the main street
//   z= 0  lot3    corner  road    cross   road      main street
//   z= 2  lot4    road    plaza   plaza   plaza
//   z= 4  lot5    road    plaza   plaza   plaza     lots face the side street

export const TILE = 2;

export const BUILDINGS = ["A", "B", "C", "D", "E", "F", "G", "H"].map((letter) => `building_${letter}`);

export interface Placement {
  model: string;
  position: [number, number, number];
  /** Rotation around the vertical axis, in radians. KayKit buildings face +z at 0. */
  rotation: number;
}

const FACE_MAIN_STREET = 0;
const FACE_SIDE_STREET = Math.PI / 2;
const ALONG_X = Math.PI / 2;
const ALONG_Z = 0;

/** Lots in the order characters move in: along the main street, then down the side street. */
export const LOTS: Placement[] = [
  { model: "base", position: [-2, 0, -2], rotation: FACE_MAIN_STREET },
  { model: "base", position: [0, 0, -2], rotation: FACE_MAIN_STREET },
  { model: "base", position: [2, 0, -2], rotation: FACE_MAIN_STREET },
  { model: "base", position: [-4, 0, 0], rotation: FACE_SIDE_STREET },
  { model: "base", position: [-4, 0, 2], rotation: FACE_SIDE_STREET },
  { model: "base", position: [-4, 0, 4], rotation: FACE_SIDE_STREET },
];

const tile = (model: string, x: number, z: number, rotation = 0): Placement => ({ model, position: [x, 0, z], rotation });
const prop = (model: string, x: number, z: number, rotation = 0): Placement => ({ model, position: [x, 0.1, z], rotation });

/** Streets, sidewalk tiles and props that are there from the start. */
export const SCENERY: Placement[] = [
  // Main street along x, turning toward the viewer into the side street along z.
  tile("road_corner", -2, 0, Math.PI / 2),
  tile("road_straight", 0, 0, ALONG_X),
  tile("road_straight_crossing", 2, 0, ALONG_X),
  tile("road_straight", 4, 0, ALONG_X),
  tile("road_straight", -2, 2, ALONG_Z),
  tile("road_straight", -2, 4, ALONG_Z),

  // Corner parks at both ends of the main street.
  tile("base", -4, -2),
  tile("base", 4, -2),
  prop("bush", -4.5, -2.5),
  prop("bush", -3.6, -2.6),
  prop("bush", -4.4, -1.6),
  prop("bench", 4, -1.4),
  prop("bush", 4.6, -2.6),
  prop("firehydrant", 3.3, -1.3),

  // The plaza in the near corner. Low props only, so it never hides a building.
  ...[0, 2, 4].flatMap((x) => [tile("base", x, 2), tile("base", x, 4)]),
  prop("streetlight", -0.85, 1.2, -Math.PI / 2),
  prop("streetlight", 4.6, 1.2),
  prop("bench", 1, 3.2, Math.PI),
  prop("bench", 3, 3.2, Math.PI),
  prop("bush", 0.2, 4.6),
  prop("bush", 4.6, 4.6),
  prop("trash_A", 2, 4.4),
  prop("dumpster", 4.3, 2.5, Math.PI / 2),

  // A couple of parked cars so the streets aren't empty.
  { model: "car_taxi", position: [0.6, 0.1, 0.5], rotation: Math.PI / 2 },
  { model: "car_sedan", position: [-1.5, 0.1, 3.4], rotation: 0 },
];

export const ALL_MODELS = [...new Set([...BUILDINGS, ...LOTS.map((l) => l.model), ...SCENERY.map((s) => s.model)])];

/** The middle of the city, which the camera looks at. */
export const CENTER: [number, number, number] = [0, 0.8, 1];

export function modelUrl(model: string): string {
  return `/models/${model}.gltf`;
}

/** A character's preferred building. FNV-1a hash of its id, so it's always the same one. */
export function buildingFor(characterId: string): string {
  let hash = 0x811c9dc5;
  for (let i = 0; i < characterId.length; i++) {
    hash ^= characterId.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193);
  }
  return BUILDINGS[(hash >>> 0) % BUILDINGS.length];
}

/**
 * Gives every character a building, in birth order, so neighbors don't look alike. Each gets
 * its preferred building unless someone born earlier has it, then the next free one. Dead
 * characters keep theirs, so nobody's building ever changes. Once all 8 are taken, repeats are allowed.
 */
export function assignBuildings(characterIdsInBirthOrder: string[]): Record<string, string> {
  const assigned: Record<string, string> = {};
  const taken = new Set<string>();
  for (const id of characterIdsInBirthOrder) {
    const preferred = BUILDINGS.indexOf(buildingFor(id));
    let model = BUILDINGS[preferred];
    for (let step = 0; step < BUILDINGS.length; step++) {
      const candidate = BUILDINGS[(preferred + step) % BUILDINGS.length];
      if (!taken.has(candidate)) {
        model = candidate;
        break;
      }
    }
    assigned[id] = model;
    taken.add(model);
    if (taken.size === BUILDINGS.length) taken.clear();
  }
  return assigned;
}
