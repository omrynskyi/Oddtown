import type { TraitAxis } from "./events";

/** What each end of a trait means, from the plan's trait table, shortened to one word to fit a card. */
export const TRAIT_ENDS: Record<TraitAxis, { name: string; low: string; high: string }> = {
  time_horizon: { name: "Time horizon", low: "Minutes", high: "Centuries" },
  tech_attitude: { name: "Technology", low: "Unplugged", high: "Automated" },
  scale: { name: "Scale", low: "Local", high: "Global" },
  sociality: { name: "Company", low: "Hermit", high: "Crowd" },
  risk: { name: "Risk", low: "Careful", high: "Gambler" },
  rationality: { name: "Reasoning", low: "Gut", high: "Logic" },
};

/** The end a trait leans toward, for screen readers, like "Leans Centuries". */
export function leaning(axis: TraitAxis, value: number): string {
  if (Math.abs(value) < 0.15) return "In the middle";
  return `Leans ${value < 0 ? TRAIT_ENDS[axis].low : TRAIT_ENDS[axis].high}`;
}
