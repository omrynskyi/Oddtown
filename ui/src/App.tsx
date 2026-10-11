import { AskScreen } from "./AskScreen";
import { LOT_COUNT } from "./town";
import { useRun } from "./useRun";

export function App() {
  const { town, connection, submit } = useRun();
  const residents = town.lots.flatMap((id) => (id ? [town.characters[id]] : []));

  return (
    <div className="app">
      <header className="wordmark">Oddtown</header>
      {town.phase === "ask" ? (
        <AskScreen onSubmit={submit} />
      ) : (
        // Stand-in until the city view lands in phase 4.
        <main className="status" aria-live="polite">
          <h1 className="status-title">{town.phase === "loading" ? "Loading your city" : "Everyone has moved in"}</h1>
          <p className="status-seed">{town.seed}</p>
          <p className="status-count">
            {residents.length} of {LOT_COUNT} characters
            {connection === "reconnecting" ? ". Reconnecting to the observer…" : ""}
          </p>
          <ul className="status-list">
            {residents.map(({ persona }) => (
              <li key={persona.id}>
                <strong>{persona.name}</strong> {persona.one_liner}
              </li>
            ))}
          </ul>
        </main>
      )}
    </div>
  );
}
