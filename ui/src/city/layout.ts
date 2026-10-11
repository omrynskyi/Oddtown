// Shared pieces for building the city. The town itself is generated per run in generate.ts.

/** KayKit blocks are 2 by 2 world units. */
export const TILE = 2;

export const BUILDINGS = ["A", "B", "C", "D", "E", "F", "G", "H"].map((letter) => `building_${letter}`);

export interface Placement {
  model: string;
  position: [number, number, number];
  /** Rotation around the vertical axis, in radians. KayKit buildings face +z at 0. */
  rotation: number;
}

/** Every model a town can use, so they can all be fetched before the first building pops. */
export const ALL_MODELS = [
  ...BUILDINGS,
  "base",
  "road_straight",
  "road_straight_crossing",
  "road_corner",
  "road_tsplit",
  "bush",
  "bench",
  "trash_A",
  "firehydrant",
  "dumpster",
  "car_taxi",
  "car_sedan",
  "car_hatchback",
  "car_stationwagon",
];

export function modelUrl(model: string): string {
  return `/models/${model}.gltf`;
}
