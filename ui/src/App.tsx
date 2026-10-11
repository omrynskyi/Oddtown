import { useState } from "react";
import { AskScreen } from "./AskScreen";
import { City } from "./city/City";
import { ReadyPanel } from "./ReadyPanel";
import { PlaybackBar } from "./PlaybackBar";
import { TabSwitcher, type View } from "./TabSwitcher";
import { LOT_COUNT } from "./town";
import { TreeView } from "./tree/TreeView";
import { useRun } from "./useRun";

export function App() {
  const { town, connection, submit, start, openRun, home, timeline } = useRun();
  const residents = town.lots.flatMap((id) => (id ? [town.characters[id].persona] : []));
  const [view, setView] = useState<View>("city");
  const started = town.phase === "running" || town.phase === "ended";
  const goHome = () => {
    setView("city");
    home();
  };

  return (
    <div className="app">
      <header className="wordmark">Oddtown</header>
      {town.phase === "ask" ? (
        <AskScreen onSubmit={submit} onOpenRun={openRun} />
      ) : (
        <main className="city-screen">
          <div className="city-stage">
            <City town={town} trafficPaused={Boolean(timeline && !timeline.playing)} />
            <div className="city-overlay" aria-live="polite">
              {town.phase === "loading" && (
                <>
                  <h1 className="city-title">Loading your city</h1>
                  <p className="city-subtitle">
                    {residents.length} of {LOT_COUNT} moved in
                  </p>
                </>
              )}
              {town.phase === "ready" && (
                <>
                  <h1 className="city-title">Everyone has moved in</h1>
                  <p className="city-subtitle">{town.seed}</p>
                </>
              )}
              {(town.phase === "running" || town.phase === "ended") && (
                <>
                  <h1 className="city-title">{town.seed}</h1>
                  <p className="city-subtitle">
                    {town.phase === "running" ? "The town is talking it over" : "The town has finished talking"}
                  </p>
                </>
              )}
              {connection === "reconnecting" && <p className="city-subtitle">Reconnecting to the observer…</p>}
            </div>
          </div>
          {town.phase === "ready" && <ReadyPanel residents={residents} onStart={start} />}
          {/* The tree covers the city instead of replacing it, so the city keeps running underneath. */}
          {started && view === "tree" && <TreeView town={town} />}
          {started && <TabSwitcher view={view} onChange={setView} onHome={goHome} />}
          {timeline && <PlaybackBar timeline={timeline} />}
        </main>
      )}
    </div>
  );
}
