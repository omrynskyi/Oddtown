import { linkHorizontal } from "d3-shape";
import { useEffect, useMemo, useRef } from "react";
import { shortName } from "../names";
import { isComposted, isProposal, livingSupport, type Town } from "../town";
import { layoutTree, NODE_HEIGHT, NODE_WIDTH, type PlacedLink } from "./layout";

const OP_LABELS = { seed: "Your idea", twist: "Twist", merge: "Merge" } as const;

const curve = linkHorizontal<PlacedLink, [number, number]>()
  .source((l) => [l.from.left + NODE_WIDTH, l.from.top + NODE_HEIGHT / 2])
  .target((l) => [l.to.left, l.to.top + NODE_HEIGHT / 2]);

/** A set of branches to pick out in the tree, like one theme from the results. */
export interface TreeHighlight {
  label: string;
  ids: string[];
}

/**
 * How the idea grew: every twist and merge, colored by how many living characters back it.
 * Proposals glow with a badge, and composted branches fade.
 */
export function TreeView({
  town,
  selectedId,
  onSelect,
  highlight,
  onClearHighlight,
}: {
  town: Town;
  selectedId: string | null;
  onSelect: (branchId: string) => void;
  highlight: TreeHighlight | null;
  onClearHighlight: () => void;
}) {
  const layout = useMemo(() => layoutTree(Object.values(town.branches)), [town.branches]);
  const support = livingSupport(town);
  const lit = highlight ? new Set(highlight.ids) : null;
  const nameOf = (id: string) => (town.characters[id] ? shortName(town.characters[id].persona.name) : id);
  const scroller = useRef<HTMLDivElement>(null);

  // A highlighted theme can sit anywhere in a big tree, so bring its first branch into view.
  useEffect(() => {
    scroller.current?.querySelector('[data-dim="false"]')?.scrollIntoView({ block: "center", inline: "center", behavior: "smooth" });
  }, [highlight]);

  return (
    <section className="tree-view" aria-label="How the idea grew">
      <header className="tree-header">
        <h1 className="city-title">{town.seed}</h1>
        {highlight ? (
          <p className="city-subtitle">
            Showing {highlight.label}{" "}
            <button className="text-button" type="button" onClick={onClearHighlight}>
              Show all
            </button>
          </p>
        ) : (
          <p className="city-subtitle">
            {layout.nodes.length} {layout.nodes.length === 1 ? "version" : "versions"} of the idea so far
          </p>
        )}
      </header>
      <div className="tree-scroll" ref={scroller}>
        <div className="tree-canvas" style={{ width: layout.width, height: layout.height }}>
          <svg className="tree-links" width={layout.width} height={layout.height} aria-hidden="true">
            {layout.links.map((link) => (
              <path key={`${link.from.id}-${link.to.id}`} d={curve(link) ?? ""} className={link.extra ? "tree-link tree-link-merge" : "tree-link"} />
            ))}
          </svg>
          {layout.nodes.map(({ id, branch, left, top }) => {
            if (!branch) return null;
            const backers = support[id] ?? [];
            const authors = branch.op === "seed" ? "" : branch.authors.map(nameOf).join(" + ");
            const proposal = isProposal(town, id, support);
            const composted = isComposted(town, id, support);
            const status = proposal ? ", a proposal" : composted ? ", composted" : "";
            return (
              <button
                key={id}
                type="button"
                className="tree-node"
                data-support={Math.min(backers.length, 3)}
                data-dim={lit ? !lit.has(id) : undefined}
                data-proposal={proposal || undefined}
                data-composted={composted || undefined}
                aria-pressed={selectedId === id}
                style={{ left, top, width: NODE_WIDTH, height: NODE_HEIGHT }}
                title={branch.text}
                aria-label={`${OP_LABELS[branch.op]}${status}: ${branch.text}`}
                onClick={() => onSelect(id)}
              >
                {proposal && (
                  <span className="tree-node-badge" aria-hidden="true">
                    Proposal
                  </span>
                )}
                <span className="tree-node-meta">
                  <span className="tree-node-op">{OP_LABELS[branch.op]}</span>
                  {authors && <span> by {authors}</span>}
                  {backers.length > 0 && (
                    <span className="tree-node-support">
                      {backers.length} {backers.length === 1 ? "backer" : "backers"}
                    </span>
                  )}
                </span>
                <span className="tree-node-text">{branch.text}</span>
              </button>
            );
          })}
        </div>
      </div>
    </section>
  );
}
