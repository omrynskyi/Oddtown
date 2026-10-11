// Shared pieces for building the city. The town itself is generated per run in generate.ts.

/** KayKit blocks are 2 by 2 world units. */
export const TILE = 2;

export const BUILDINGS = ["A", "B", "C", "D", "E", "F", "G", "H"].map((letter) => `building_${letter}`);

/** How tall each building is, in world units, so a speech bubble sits just above its roof. */
export const BUILDING_HEIGHTS: Record<string, number> = {
  building_A: 1.65,
  building_B: 1.65,
  building_C: 2.98,
  building_D: 2.97,
  building_E: 2.35,
  building_F: 2.35,
  building_G: 2.98,
  building_H: 3.05,
};

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
