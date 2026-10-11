import { useState } from "react";
import { CharacterCard } from "./CharacterCard";
import type { Persona } from "./events";

/** Shown once everyone has moved in: the Start run button, and a card for each character. */
export function ReadyPanel({ residents, onStart }: { residents: Persona[]; onStart: () => Promise<void> }) {
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function start() {
    setStarting(true);
    setError(null);
    try {
      await onStart();
      // The page moves on when the observer reports run.started, so the button stays disabled.
    } catch (err) {
      setError(err instanceof Error ? err.message : "The run didn't start.");
      setStarting(false);
    }
  }

  return (
    <section className="ready" aria-label="Ready to start">
      <div className="ready-action">
        <button className="start-button" type="button" onClick={start} disabled={starting} autoFocus>
          {starting ? "Starting…" : "Start run"}
        </button>
        {error && (
          <p className="ready-error" role="alert">
            {error}
          </p>
        )}
      </div>
      <ul className="cards" aria-label="The characters">
        {residents.map((persona) => (
          <li key={persona.id}>
            <CharacterCard persona={persona} />
          </li>
        ))}
      </ul>
    </section>
  );
}
