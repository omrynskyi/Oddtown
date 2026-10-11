import { linkHorizontal } from "d3-shape";
import { useMemo, useState } from "react";
import { shortName } from "../names";
import { livingSupport, type Town } from "../town";
import { layoutTree, NODE_HEIGHT, NODE_WIDTH, type PlacedLink } from "./layout";

const OP_LABELS = { seed: "Your idea", twist: "Twist", merge: "Merge" } as const;

const curve = linkHorizontal<PlacedLink, [number, number]>()
  .source((l) => [l.from.left + NODE_WIDTH, l.from.top + NODE_HEIGHT / 2])
  .target((l) => [l.to.left, l.to.top + NODE_HEIGHT / 2]);

/** How the idea grew: every twist and merge, colored by how many living characters back it. */
export function TreeView({ town }: { town: Town }) {
  const layout = useMemo(() => layoutTree(Object.values(town.branches)), [town.branches]);
  const support = livingSupport(town);
  const nameOf = (id: string) => (town.characters[id] ? shortName(town.characters[id].persona.name) : id);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const expanded = expandedId ? town.branches[expandedId] : null;

  return (
    <section className="tree-view" aria-label="How the idea grew">
      <header className="tree-header">
        <h1 className="city-title">{town.seed}</h1>
        <p className="city-subtitle">
          {layout.nodes.length} {layout.nodes.length === 1 ? "version" : "versions"} of the idea so far
        </p>
      </header>
      <div className="tree-scroll">
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
            return (
              <article
                key={id}
                className="tree-node"
                data-support={Math.min(backers.length, 3)}
                style={{ left, top, width: NODE_WIDTH, height: NODE_HEIGHT }}
                title={branch.text}
                tabIndex={0}
                aria-label={`${OP_LABELS[branch.op]}: ${branch.text}`}
              >
                <p className="tree-node-meta">
                  <span className="tree-node-op">{OP_LABELS[branch.op]}</span>
                  {authors && <span> by {authors}</span>}
                  {backers.length > 0 && (
                    <span className="tree-node-support">
                      {backers.length} {backers.length === 1 ? "backer" : "backers"}
                    </span>
                  )}
                </p>
                <p className="tree-node-text">{branch.text}</p>
                <button
                  className="tree-node-expand"
                  type="button"
                  aria-expanded={expandedId === id}
                  onClick={() => setExpandedId((current) => (current === id ? null : id))}
                >
                  {expandedId === id ? "Close" : "Read full idea"}
                </button>
              </article>
            );
          })}
        </div>
      </div>
      {expanded && (
        <aside className="tree-detail" aria-label="Expanded idea">
          <button className="tree-detail-close" type="button" onClick={() => setExpandedId(null)} aria-label="Close expanded idea">
            ×
          </button>
          <p className="tree-detail-meta">
            {OP_LABELS[expanded.op]}
            {expanded.op !== "seed" && ` by ${expanded.authors.map(nameOf).join(" + ")}`}
          </p>
          <p className="tree-detail-text">{expanded.text}</p>
        </aside>
      )}
    </section>
  );
}
