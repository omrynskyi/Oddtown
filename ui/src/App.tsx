import { useCallback, useEffect, useRef, useState } from "react";
import { AskScreen } from "./AskScreen";
import { City } from "./city/City";
import { Ticker } from "./feed";
import { ReadyPanel } from "./ReadyPanel";
import { PlaybackBar } from "./PlaybackBar";
import { ResultsView } from "./ResultsView";
import { SidePanel, type Selection } from "./SidePanel";
import { TabSwitcher, type View } from "./TabSwitcher";
import { LOT_COUNT } from "./town";
import { TreeView, type TreeHighlight } from "./tree/TreeView";
import { useRun } from "./useRun";

export function App() {
  const { town, connection, submit, start, openRun, home, summarize, timeline } = useRun();
  const residents = town.lots.flatMap((id) => (id ? [town.characters[id].persona] : []));
  const [view, setView] = useState<View>("city");
  const [selection, setSelection] = useState<Selection | null>(null);
  const [highlight, setHighlight] = useState<TreeHighlight | null>(null);
  const started = town.phase === "running" || town.phase === "ended";
  const ended = town.phase === "ended";
  const close = useCallback(() => setSelection(null), []);

  // A new run starts clean.
  useEffect(() => {
    setSelection(null);
    setHighlight(null);
  }, [town.runId]);

  // Each run opens its results once, when it ends or when a finished run is opened.
  const shownResults = useRef<string | null>(null);
  useEffect(() => {
    if (ended && town.runId && shownResults.current !== town.runId) {
      shownResults.current = town.runId;
      setView("results");
    }
    if (!ended && view === "results") setView("city");
  }, [ended, town.runId, view]);

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
            <City
              town={town}
              trafficPaused={Boolean(timeline && !timeline.playing)}
              selectedId={selection?.kind === "character" ? selection.id : null}
              onSelect={(id) => setSelection(id ? { kind: "character", id } : null)}
            />
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
              {started && (
                <>
                  <h1 className="city-title">{town.seed}</h1>
                  {town.feed.length ? (
                    <Ticker town={town} onOpen={() => setSelection({ kind: "news" })} />
                  ) : (
                    <p className="city-subtitle">{ended ? "The town has finished talking" : "The town is talking it over"}</p>
                  )}
                </>
              )}
              {connection === "reconnecting" && <p className="city-subtitle">Reconnecting to the observer…</p>}
            </div>
          </div>
          {town.phase === "ready" && <ReadyPanel residents={residents} onStart={start} />}
          {/* The tree and results cover the city instead of replacing it, so the city keeps running underneath. */}
          {started && view === "tree" && (
            <TreeView
              town={town}
              selectedId={selection?.kind === "branch" ? selection.id : null}
              onSelect={(id) => setSelection({ kind: "branch", id })}
              highlight={highlight}
              onClearHighlight={() => setHighlight(null)}
            />
          )}
          {ended && view === "results" && (
            <ResultsView
              town={town}
              onSelect={setSelection}
              onShowInTree={(bucket) => {
                setHighlight({ label: `${bucket.emoji} ${bucket.name}`, ids: bucket.branch_ids });
                setView("tree");
              }}
              onSummarize={summarize}
            />
          )}
          {started && (
            <TabSwitcher view={view} views={ended ? ["city", "tree", "results"] : ["city", "tree"]} onChange={setView} onHome={goHome} />
          )}
          {timeline && view !== "results" && <PlaybackBar timeline={timeline} />}
          {selection && <SidePanel town={town} selection={selection} onSelect={setSelection} onClose={close} />}
        </main>
      )}
    </div>
  );
}
