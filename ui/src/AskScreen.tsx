import { useState, type FormEvent } from "react";
import { RunInspector } from "./RunInspector";

export function AskScreen({
  onSubmit,
  onOpenRun,
}: {
  onSubmit: (seed: string) => Promise<void>;
  onOpenRun: (runId: string, seed: string | null) => Promise<void>;
}) {
  const [idea, setIdea] = useState("");
  const [building, setBuilding] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    const seed = idea.trim();
    if (!seed || building) return;
    setBuilding(true);
    setError(null);
    try {
      await onSubmit(seed);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong building the town.");
      setBuilding(false);
    }
  }

  return (
    <main className="ask">
      <div className="ask-main">
        <form className="ask-bubble" onSubmit={handleSubmit}>
          <label className="visually-hidden" htmlFor="idea">
            What idea would you like to test?
          </label>
          <input
            id="idea"
            className="ask-input"
            placeholder="What idea would you like to test?"
            value={idea}
            onChange={(e) => setIdea(e.target.value)}
            maxLength={500}
            autoComplete="off"
            autoFocus
            disabled={building}
          />
          <button className="ask-button" type="submit" disabled={!idea.trim() || building}>
            {building ? "Building…" : "Build town"}
          </button>
        </form>
        {error ? (
          <div className="ask-feedback">
            <p className="ask-error" role="alert">{error}</p>
          </div>
        ) : (
          <p className="ask-hint">Six characters will argue about it, twist it, and pick their three favorite versions.</p>
        )}
      </div>
      <div className="recent-runs-dock">
        <RunInspector onOpenRun={onOpenRun} />
      </div>
    </main>
  );
}
