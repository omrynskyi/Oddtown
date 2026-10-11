import { useState } from "react";
import type { Bucket, Verdict } from "./events";
import { shortName } from "./names";
import type { Selection } from "./SidePanel";
import { backingKey, livingSupport, type Town } from "./town";

/** A verdict from the summary, or one the town's own finalists stand in for until it arrives. */
function verdictsOf(town: Town): Verdict[] {
  if (town.summary?.verdicts.length) return town.summary.verdicts;
  return (town.finalists ?? []).map((f) => ({ branch_id: f.branch_id, title: clip(f.text, 60), why: "", risk: f.minority_report }));
}

function clip(text: string, limit: number) {
  return text.length <= limit ? text : `${text.slice(0, limit - 1).trimEnd()}…`;
}

/** What the run came to: the verdicts, the themes, the story and how it compares to one LLM. */
export function ResultsView({
  town,
  onSelect,
  onShowInTree,
  onSummarize,
}: {
  town: Town;
  onSelect: (selection: Selection) => void;
  onShowInTree: (bucket: Bucket) => void;
  onSummarize: () => Promise<void>;
}) {
  const summary = town.summary && !town.summary.error ? town.summary : null;
  const verdicts = verdictsOf(town);
  const support = livingSupport(town);
  const baseline = summary?.baseline.length ? summary.baseline : town.baseline;
  const name = (id: string) => (town.characters[id] ? shortName(town.characters[id].persona.name) : id);

  const branches = Object.values(town.branches).filter((b) => b.op !== "seed");
  const stats = [
    { label: "versions", value: branches.length },
    { label: "twists", value: branches.filter((b) => b.op === "twist").length },
    { label: "merges", value: branches.filter((b) => b.op === "merge").length },
    { label: "letters", value: town.letters.length },
    { label: "changes of heart", value: town.feed.filter((f) => f.kind === "drop").length },
    { label: "residents", value: Object.keys(town.characters).length },
  ];

  return (
    <section className="results-view" aria-label="Results">
      <div className="results-scroll">
        <header className="results-header">
          <p className="results-eyebrow">The town's verdict on</p>
          <h1 className="city-title">{town.seed}</h1>
          <SummaryStatus town={town} onSummarize={onSummarize} />
          {summary?.headline && <p className="results-headline">{summary.headline}</p>}
        </header>

        {verdicts.length > 0 && (
          <ol className="podium" aria-label="Top three">
            {verdicts.map((v, i) => {
              const backers = support[v.branch_id] ?? town.finalists?.find((f) => f.branch_id === v.branch_id)?.champions ?? [];
              const quote = backers.map((b) => town.backingReasons[backingKey(b, v.branch_id)]).find(Boolean);
              return (
                <li key={v.branch_id} className={`verdict verdict-${i + 1}`}>
                  <span className="verdict-rank" aria-label={`Number ${i + 1}`}>
                    {i + 1}
                  </span>
                  <h2 className="verdict-title">{v.title}</h2>
                  <p className="verdict-idea">{town.branches[v.branch_id]?.text}</p>
                  {v.why && <p className="verdict-why">{v.why}</p>}
                  {quote && <p className="verdict-quote">“{quote}”</p>}
                  {v.risk && (
                    <p className="verdict-risk">
                      <strong>Watch out</strong> {v.risk}
                    </p>
                  )}
                  <div className="verdict-footer">
                    <span className="verdict-backers">
                      {backers.length ? `Backed by ${backers.map(name).join(", ")}` : "No living backers"}
                    </span>
                    <button className="text-button" type="button" onClick={() => onSelect({ kind: "branch", id: v.branch_id })}>
                      Open idea
                    </button>
                  </div>
                </li>
              );
            })}
          </ol>
        )}

        {summary && summary.buckets.length > 0 && (
          <section className="results-section">
            <h2>Every idea, sorted</h2>
            <ul className="buckets">
              {summary.buckets.map((bucket) => (
                <li key={bucket.name} className="bucket">
                  <div className="bucket-head">
                    <span className="bucket-emoji" aria-hidden="true">
                      {bucket.emoji}
                    </span>
                    <div>
                      <h3>{bucket.name}</h3>
                      <p>{bucket.blurb}</p>
                    </div>
                    <span className="bucket-count">{bucket.branch_ids.length}</span>
                  </div>
                  <ul className="bucket-ideas">
                    {bucket.branch_ids.slice(0, 3).map((id) =>
                      town.branches[id] ? (
                        <li key={id}>
                          <button type="button" onClick={() => onSelect({ kind: "branch", id })}>
                            <span>{town.branches[id].text}</span>
                          </button>
                        </li>
                      ) : null,
                    )}
                  </ul>
                  <button className="text-button" type="button" onClick={() => onShowInTree(bucket)}>
                    Show in tree
                  </button>
                </li>
              ))}
            </ul>
          </section>
        )}

        <div className="results-columns">
          {summary && summary.moments.length > 0 && (
            <section className="results-section results-card">
              <h2>How it went</h2>
              <ol className="moments">
                {summary.moments.map((moment, i) => (
                  <li key={i}>{moment}</li>
                ))}
              </ol>
            </section>
          )}
          <section className="results-section results-card">
            <h2>By the numbers</h2>
            <dl className="stats">
              {stats.map((s) => (
                <div key={s.label}>
                  <dt>{s.label}</dt>
                  <dd>{s.value}</dd>
                </div>
              ))}
            </dl>
          </section>
        </div>

        {baseline.length > 0 && verdicts.length > 0 && (
          <section className="results-section">
            <h2>The town vs one LLM</h2>
            <div className="versus">
              <div className="versus-side versus-town">
                <h3>🏘️ The town</h3>
                <ol>
                  {verdicts.map((v) => (
                    <li key={v.branch_id}>{town.branches[v.branch_id]?.text ?? v.title}</li>
                  ))}
                </ol>
              </div>
              <div className="versus-side">
                <h3>🤖 One LLM, one call</h3>
                <ol>
                  {baseline.map((idea, i) => (
                    <li key={i}>{idea}</li>
                  ))}
                </ol>
              </div>
            </div>
          </section>
        )}

        <CopyButton town={town} verdicts={verdicts} baseline={baseline} />
      </div>
    </section>
  );
}

function SummaryStatus({ town, onSummarize }: { town: Town; onSummarize: () => Promise<void> }) {
  const [asking, setAsking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (town.summary && !town.summary.error) return null;

  async function ask() {
    setAsking(true);
    setError(null);
    try {
      await onSummarize();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't reach the observer.");
    } finally {
      setAsking(false);
    }
  }

  const failed = town.summary?.error || error;
  return (
    <p className="summary-status" role="status">
      {asking ? "Writing the summary…" : failed ? "The summary didn't come through." : "The summary is on its way…"}
      <button className="text-button" type="button" onClick={ask} disabled={asking}>
        {failed ? "Try again" : "Write it now"}
      </button>
    </p>
  );
}

function CopyButton({ town, verdicts, baseline }: { town: Town; verdicts: Verdict[]; baseline: string[] }) {
  const [copied, setCopied] = useState(false);
  const summary = town.summary && !town.summary.error ? town.summary : null;

  function markdown() {
    const lines = [`# ${town.seed}`, ""];
    if (summary?.headline) lines.push(summary.headline, "");
    lines.push("## Top three", "");
    verdicts.forEach((v, i) => {
      lines.push(`${i + 1}. **${v.title}**: ${town.branches[v.branch_id]?.text ?? ""}`);
      if (v.why) lines.push(`   ${v.why}`);
      if (v.risk) lines.push(`   Watch out: ${v.risk}`);
    });
    if (summary?.buckets.length) {
      lines.push("", "## Themes", "");
      summary.buckets.forEach((b) => lines.push(`- ${b.emoji} **${b.name}** (${b.branch_ids.length}): ${b.blurb}`));
    }
    if (summary?.moments.length) {
      lines.push("", "## How it went", "");
      summary.moments.forEach((m) => lines.push(`- ${m}`));
    }
    if (baseline.length) {
      lines.push("", "## One LLM's three ideas", "");
      baseline.forEach((idea) => lines.push(`- ${idea}`));
    }
    return lines.join("\n");
  }

  async function copy() {
    try {
      await navigator.clipboard.writeText(markdown());
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    } catch {
      setCopied(false);
    }
  }

  return (
    <div className="results-actions">
      <button className="start-button copy-button" type="button" onClick={copy}>
        {copied ? "Copied!" : "Copy as Markdown"}
      </button>
    </div>
  );
}
