import type { ReplayTimeline } from "./useRun";

/** A direct, floating scrubber for replaying a saved town event by event. */
export function PlaybackBar({ timeline }: { timeline: ReplayTimeline }) {
  const progress = timeline.total ? (timeline.position / timeline.total) * 100 : 0;

  return (
    <section className="playback-bar" aria-label="Run playback">
      <div className="playback-track">
        <button
          className="playback-button"
          type="button"
          onClick={timeline.toggle}
          disabled={timeline.total === 0}
          aria-label={timeline.playing ? "Pause replay" : timeline.position === timeline.total ? "Replay from beginning" : "Play replay"}
          aria-pressed={timeline.playing}
        >
          {timeline.playing ? (
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <rect x="6" y="5" width="4" height="14" rx="1" />
              <rect x="14" y="5" width="4" height="14" rx="1" />
            </svg>
          ) : (
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="M8 5.7v12.6a1 1 0 0 0 1.54.84l9.2-6.3a1 1 0 0 0 0-1.68l-9.2-6.3A1 1 0 0 0 8 5.7Z" />
            </svg>
          )}
        </button>
        <input
          className="playback-slider"
          type="range"
          min="0"
          max={timeline.total}
          value={timeline.position}
          onChange={(event) => timeline.seek(Number(event.currentTarget.value))}
          aria-label={`Replay progress: ${timeline.position} of ${timeline.total}, ${timeline.eventType ?? "start of run"}`}
          aria-valuetext={`${timeline.position} of ${timeline.total}: ${timeline.eventType ?? "start of run"}`}
          style={{ background: `linear-gradient(to right, var(--taxi) ${progress}%, var(--kerb) ${progress}%)` }}
        />
      </div>
    </section>
  );
}
