import { useEffect, useRef, type ReactNode } from "react";
import { TraitBars } from "./CharacterCard";
import { FeedLine } from "./feed";
import { shortName } from "./names";
import { backingKey, livingSupport, type Decision, type Interaction, type Town } from "./town";

export type Selection = { kind: "character"; id: string } | { kind: "branch"; id: string } | { kind: "news" };

const OP_LABELS = { seed: "Your idea", twist: "Twist", merge: "Merge" } as const;

const ACTIONS: Record<Decision["action"], string> = {
  twist: "🔀 Twisted",
  merge: "🧩 Merged",
  back: "🤝 Backed",
  trash: "🗑️ Trashed",
  pitch: "📣 Pitched",
  ignore: "🤷 Sat out",
};

const STAMPS: Record<Interaction["kind"], string> = { letter: "✉️", agree: "👏", disagree: "🍅", mixed: "🤔" };

/** Most entries a list in the panel shows, newest first. */
const MAX_ROWS = 12;

/** A floating card down the right side of the screen, with a character, a branch or the town news. */
export function SidePanel({
  town,
  selection,
  onSelect,
  onClose,
}: {
  town: Town;
  selection: Selection;
  onSelect: (selection: Selection) => void;
  onClose: () => void;
}) {
  const scroller = useRef<HTMLDivElement>(null);
  const key = selection.kind === "news" ? "news" : `${selection.kind}-${selection.id}`;

  useEffect(() => {
    scroller.current?.scrollTo({ top: 0 });
  }, [key]);

  useEffect(() => {
    const close = (event: KeyboardEvent) => event.key === "Escape" && onClose();
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [onClose]);

  const content =
    selection.kind === "character" && town.characters[selection.id] ? (
      <CharacterDetails town={town} id={selection.id} onSelect={onSelect} />
    ) : selection.kind === "branch" && town.branches[selection.id] ? (
      <BranchDetails town={town} id={selection.id} onSelect={onSelect} />
    ) : (
      <NewsDetails town={town} onSelect={onSelect} />
    );

  return (
    <aside className="side-panel" aria-label="Details">
      <button className="side-panel-close" type="button" onClick={onClose} aria-label="Close details">
        ×
      </button>
      <div className="side-panel-scroll" ref={scroller} key={key}>
        {content}
      </div>
    </aside>
  );
}

function CharacterDetails({ town, id, onSelect }: { town: Town; id: string; onSelect: (s: Selection) => void }) {
  const { persona, alive, bornSeq, diedSeq } = town.characters[id];
  const generation = persona.lineage?.generation ?? 0;
  const replaced = persona.lineage?.replaces;
  const lifespan = bornSeq === undefined ? null : Math.max((diedSeq ?? town.lastSeq) - bornSeq, 0);
  const backing = (town.endorsements[id] ?? []).filter((b) => town.branches[b]);
  const wrote = Object.values(town.branches).filter((b) => b.op !== "seed" && b.authors.includes(id)).reverse();
  const decisions = [...(town.decisions[id] ?? [])].reverse().slice(0, MAX_ROWS);
  const letters = town.letters.filter((l) => l.from === id || l.to === id).reverse().slice(0, MAX_ROWS);
  const objections = town.objections.filter((o) => o.agentId === id).reverse();
  const reflection = town.reflections[id];
  const status = !alive ? "Remembered" : town.resting.includes(id) ? "Resting" : "At home";

  return (
    <>
      <header className="panel-header">
        <p className="panel-eyebrow">{generation === 0 ? "Original resident" : `Generation ${generation + 1}`}</p>
        <h2>{persona.name}</h2>
        <p className="panel-line">{persona.one_liner}</p>
        <div className="panel-chips">
          <span className={`chip${alive ? "" : " chip-dark"}`}>{status}</span>
          {lifespan !== null && <span className="chip">{lifespan} events in town</span>}
          {replaced && town.characters[replaced] && (
            <button className="chip chip-button" type="button" onClick={() => onSelect({ kind: "character", id: replaced })}>
              Replaced {shortName(town.characters[replaced].persona.name)}
            </button>
          )}
        </div>
      </header>

      {town.lastWords[id] && (
        <Section title="Last words">
          <p className="panel-quote panel-quote-dark">{town.lastWords[id]}</p>
        </Section>
      )}

      {persona.worldview && <p className="panel-quote">{persona.worldview}</p>}

      {reflection && (reflection.agenda || reflection.insights.length > 0) && (
        <Section title="On their mind">
          {reflection.agenda && <p className="panel-agenda">{reflection.agenda}</p>}
          {reflection.insights.length > 0 && (
            <ul className="panel-list panel-bullets">
              {reflection.insights.map((insight, i) => (
                <li key={i}>{insight}</li>
              ))}
            </ul>
          )}
        </Section>
      )}

      <Section title="Backing now" empty={backing.length ? undefined : "Not backing anything yet."}>
        <ul className="panel-list">
          {backing.map((b) => (
            <li key={b}>
              <BranchLink town={town} id={b} onSelect={onSelect} note={town.backingReasons[backingKey(id, b)]} />
            </li>
          ))}
        </ul>
      </Section>

      <Section title="Why they did it" empty={decisions.length ? undefined : "No moves yet."}>
        <ol className="panel-list">
          {decisions.map((d) => (
            <li key={d.seq} className="panel-row">
              <span className="panel-row-label">{ACTIONS[d.action]}</span>
              <p className="panel-row-say">“{d.say}”</p>
              {d.reason && <p className="panel-row-note">{d.reason}</p>}
            </li>
          ))}
        </ol>
      </Section>

      {wrote.length > 0 && (
        <Section title={`Ideas they wrote (${wrote.length})`}>
          <ul className="panel-list">
            {wrote.slice(0, MAX_ROWS).map((b) => (
              <li key={b.id}>
                <BranchLink town={town} id={b.id} onSelect={onSelect} />
              </li>
            ))}
          </ul>
        </Section>
      )}

      <Section title="Conversations" empty={letters.length ? undefined : "No letters yet."}>
        <ol className="panel-list">
          {letters.map((l) => {
            const outgoing = l.from === id;
            const peer = outgoing ? l.to : l.from;
            return (
              <li key={l.seq} className="panel-row">
                <span className="panel-row-label">
                  {STAMPS[l.kind]} {outgoing ? "To" : "From"}{" "}
                  <NameButton town={town} id={peer} onSelect={onSelect} />
                </span>
                <p className="panel-row-say">{l.text || "…"}</p>
              </li>
            );
          })}
        </ol>
      </Section>

      {objections.length > 0 && (
        <Section title="Objections">
          <ul className="panel-list">
            {objections.map((o) => (
              <li key={o.seq}>
                <BranchLink town={town} id={o.branchId} onSelect={onSelect} note={o.reason} />
              </li>
            ))}
          </ul>
        </Section>
      )}

      {reflection && reflection.impressions.length > 0 && (
        <Section title="What they think of the others">
          <ul className="panel-list">
            {reflection.impressions.map((i) => (
              <li key={i.peerId} className="panel-row">
                <span className="panel-row-label">
                  <NameButton town={town} id={i.peerId} onSelect={onSelect} />
                </span>
                <p className="panel-row-note">{i.impression}</p>
              </li>
            ))}
          </ul>
        </Section>
      )}

      <Section title="Traits">
        <TraitBars traits={persona.traits} />
      </Section>
    </>
  );
}

function BranchDetails({ town, id, onSelect }: { town: Town; id: string; onSelect: (s: Selection) => void }) {
  const branch = town.branches[id];
  const backers = livingSupport(town)[id] ?? [];
  const children = Object.values(town.branches).filter((b) => b.parentIds.includes(id));
  const objections = town.objections.filter((o) => o.branchId === id).reverse();
  const verdictIndex = town.summary?.verdicts.findIndex((v) => v.branch_id === id) ?? -1;
  const verdict = verdictIndex >= 0 ? town.summary!.verdicts[verdictIndex] : null;
  const bucket = town.summary?.buckets.find((b) => b.branch_ids.includes(id));
  const finalist = town.finalists?.find((f) => f.branch_id === id);

  return (
    <>
      <header className="panel-header">
        <p className="panel-eyebrow">
          {OP_LABELS[branch.op]}
          {branch.op !== "seed" && (
            <>
              {" by "}
              {branch.authors.map((a, i) => (
                <span key={a}>
                  {i > 0 && " + "}
                  <NameButton town={town} id={a} onSelect={onSelect} />
                </span>
              ))}
            </>
          )}
        </p>
        <p className="panel-idea">{branch.text}</p>
        <div className="panel-chips">
          <span className={`chip${backers.length ? " chip-taxi" : ""}`}>
            {backers.length} {backers.length === 1 ? "backer" : "backers"}
          </span>
          {verdict && <span className="chip chip-taxi">#{verdictIndex + 1} verdict</span>}
          {bucket && (
            <span className="chip">
              {bucket.emoji} {bucket.name}
            </span>
          )}
        </div>
      </header>

      {verdict && (
        <Section title={verdict.title}>
          <p className="panel-agenda">{verdict.why}</p>
          {verdict.risk && <p className="panel-row-note">⚠️ {verdict.risk}</p>}
        </Section>
      )}

      <Section title="Backed by" empty={backers.length ? undefined : "Nobody living backs this one."}>
        <ul className="panel-list">
          {backers.map((a) => (
            <li key={a} className="panel-row">
              <span className="panel-row-label">
                <NameButton town={town} id={a} onSelect={onSelect} />
              </span>
              {town.backingReasons[backingKey(a, id)] && <p className="panel-row-note">{town.backingReasons[backingKey(a, id)]}</p>}
            </li>
          ))}
        </ul>
      </Section>

      {(objections.length > 0 || finalist?.minority_report) && (
        <Section title="The case against">
          <ul className="panel-list">
            {finalist?.minority_report && !objections.some((o) => o.reason === finalist.minority_report) && (
              <li className="panel-row">
                <span className="panel-row-label">Minority report</span>
                <p className="panel-row-note">{finalist.minority_report}</p>
              </li>
            )}
            {objections.map((o) => (
              <li key={o.seq} className="panel-row">
                <span className="panel-row-label">
                  ✋ <NameButton town={town} id={o.agentId} onSelect={onSelect} />
                </span>
                <p className="panel-row-note">{o.reason || "Objected without a reason."}</p>
              </li>
            ))}
          </ul>
        </Section>
      )}

      {branch.parentIds.length > 0 && (
        <Section title="Grew from">
          <ul className="panel-list">
            {branch.parentIds.filter((p) => town.branches[p]).map((p) => (
              <li key={p}>
                <BranchLink town={town} id={p} onSelect={onSelect} />
              </li>
            ))}
          </ul>
        </Section>
      )}

      {children.length > 0 && (
        <Section title="Grew into">
          <ul className="panel-list">
            {children.map((b) => (
              <li key={b.id}>
                <BranchLink town={town} id={b.id} onSelect={onSelect} />
              </li>
            ))}
          </ul>
        </Section>
      )}
    </>
  );
}

function NewsDetails({ town, onSelect }: { town: Town; onSelect: (s: Selection) => void }) {
  const items = [...town.feed].reverse();
  return (
    <>
      <header className="panel-header">
        <p className="panel-eyebrow">Everything that happened</p>
        <h2>Town news</h2>
      </header>
      {items.length === 0 && <p className="panel-empty">Nothing yet. News shows up once the run starts.</p>}
      <ol className="panel-list news-list">
        {items.map((item) => {
          const target: Selection | null = item.branchId && town.branches[item.branchId]
            ? { kind: "branch", id: item.branchId }
            : item.actor && town.characters[item.actor]
              ? { kind: "character", id: item.actor }
              : null;
          return (
            <li key={`${item.seq}-${item.kind}`}>
              {target ? (
                <button className="news-row" type="button" onClick={() => onSelect(target)}>
                  <FeedLine item={item} town={town} />
                </button>
              ) : (
                <div className="news-row">
                  <FeedLine item={item} town={town} />
                </div>
              )}
            </li>
          );
        })}
      </ol>
    </>
  );
}

function Section({ title, empty, children }: { title: string; empty?: string; children?: ReactNode }) {
  return (
    <section className="panel-section">
      <h3>{title}</h3>
      {empty ? <p className="panel-empty">{empty}</p> : children}
    </section>
  );
}

function NameButton({ town, id, onSelect }: { town: Town; id: string; onSelect: (s: Selection) => void }) {
  const persona = town.characters[id]?.persona;
  if (!persona) return <span>{id === "world" ? "the town" : id}</span>;
  return (
    <button className="name-button" type="button" onClick={() => onSelect({ kind: "character", id })}>
      {shortName(persona.name)}
    </button>
  );
}

/** A small card for a branch that opens it in the panel, with an optional note under it. */
export function BranchLink({ town, id, onSelect, note }: { town: Town; id: string; onSelect: (s: Selection) => void; note?: string }) {
  const branch = town.branches[id];
  if (!branch) return null;
  return (
    <button className="branch-link" type="button" onClick={() => onSelect({ kind: "branch", id })}>
      <span className="branch-link-op">{OP_LABELS[branch.op]}</span>
      <span className="branch-link-text">{branch.text}</span>
      {note && <span className="branch-link-note">{note}</span>}
    </button>
  );
}
