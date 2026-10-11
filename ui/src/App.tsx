import { AskScreen } from "./AskScreen";
import { City } from "./city/City";
import { LOT_COUNT } from "./town";
import { useRun } from "./useRun";

export function App() {
  const { town, connection, submit } = useRun();
  const movedIn = town.lots.filter(Boolean).length;

  return (
    <div className="app">
      <header className="wordmark">Oddtown</header>
      {town.phase === "ask" ? (
        <AskScreen onSubmit={submit} />
      ) : (
        <main className="city-screen">
          <City town={town} />
          <div className="city-overlay" aria-live="polite">
            {town.phase === "loading" ? (
              <>
                <h1 className="city-title">Loading your city</h1>
                <p className="city-subtitle">
                  {movedIn} of {LOT_COUNT} moved in
                </p>
              </>
            ) : (
              // The Start run button and character cards replace this in phase 5.
              <h1 className="city-title">Everyone has moved in</h1>
            )}
            {connection === "reconnecting" && <p className="city-subtitle">Reconnecting to the observer…</p>}
          </div>
        </main>
      )}
    </div>
  );
}
