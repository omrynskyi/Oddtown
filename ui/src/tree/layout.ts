// Lays out the idea tree left to right: the seed on the left, each twist or merge one column
// to the right of what it came from. A merge sits under its first parent, with a dashed link to
// its second. Pure, so it can be tested without drawing anything.

import { stratify, tree } from "d3-hierarchy";
import type { Branch } from "../town";

export const NODE_WIDTH = 230;
export const NODE_HEIGHT = 98;
const COLUMN_GAP = 64;
const ROW_GAP = 16;

/** Stands in for the seed if branches arrive before it, so the tree always has one root. */
const MISSING_ROOT = "__root";

export interface PlacedNode {
  branch: Branch | null;
  id: string;
  left: number;
  top: number;
}

export interface PlacedLink {
  from: PlacedNode;
  to: PlacedNode;
  /** True for a merge's second parent, drawn dashed. */
  extra: boolean;
}

export interface TreeLayout {
  nodes: PlacedNode[];
  links: PlacedLink[];
  width: number;
  height: number;
}

export function layoutTree(branches: Branch[]): TreeLayout {
  if (branches.length === 0) return { nodes: [], links: [], width: 0, height: 0 };
  const ordered = [...branches].sort((a, b) => a.seq - b.seq);
  const seed = ordered.find((b) => b.op === "seed");
  const rootId = seed?.id ?? MISSING_ROOT;

  // Each branch hangs off its first parent that has already arrived, or off the root if none has.
  const seen = new Set<string>([rootId]);
  const rows: { id: string; parent: string | null; branch: Branch | null }[] = seed ? [] : [{ id: MISSING_ROOT, parent: null, branch: null }];
  for (const branch of ordered) {
    if (branch.id === rootId) {
      rows.unshift({ id: branch.id, parent: null, branch });
      continue;
    }
    const parent = branch.parentIds.find((p) => seen.has(p)) ?? rootId;
    rows.push({ id: branch.id, parent, branch });
    seen.add(branch.id);
  }

  const root = stratify<(typeof rows)[number]>()
    .id((d) => d.id)
    .parentId((d) => d.parent)(rows);
  tree<(typeof rows)[number]>()
    .nodeSize([NODE_HEIGHT + ROW_GAP, NODE_WIDTH + COLUMN_GAP])
    .separation(() => 1)(root);

  const placed = root.descendants();
  const minX = Math.min(...placed.map((n) => n.x!));
  const byId = new Map<string, PlacedNode>();
  for (const n of placed) {
    byId.set(n.id!, { id: n.id!, branch: n.data.branch, left: n.y!, top: n.x! - minX });
  }

  const links: PlacedLink[] = [];
  for (const n of placed) {
    if (n.parent) links.push({ from: byId.get(n.parent.id!)!, to: byId.get(n.id!)!, extra: false });
    const firstParent = n.parent?.id;
    for (const other of n.data.branch?.parentIds ?? []) {
      if (other !== firstParent && byId.has(other)) links.push({ from: byId.get(other)!, to: byId.get(n.id!)!, extra: true });
    }
  }

  const nodes = [...byId.values()];
  return {
    nodes,
    links,
    width: Math.max(...nodes.map((n) => n.left)) + NODE_WIDTH,
    height: Math.max(...nodes.map((n) => n.top)) + NODE_HEIGHT,
  };
}
