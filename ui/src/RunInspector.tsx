import { useEffect, useRef, useState, type KeyboardEvent, type PointerEvent, type WheelEvent } from "react";
import {
  motion,
  useMotionValue,
  useMotionValueEvent,
  useReducedMotion,
  useSpring,
  useTransform,
  type MotionValue,
} from "motion/react";
import { getRunStatus, listRuns, stopRun, type RunSummary } from "./api";

const CARD_HEIGHT = 62;
const CARD_GAP = 10;
const CARD_PITCH = CARD_HEIGHT + CARD_GAP;
const INITIAL_ROWS = 2.5;

type TownRun = RunSummary & { state?: string };

function clamp(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value));
}

function RunStackCard({
  run,
  index,
  stageHeight,
  offset,
  reducedMotion,
  opening,
  stopping,
  onOpen,
  onStop,
}: {
  run: TownRun;
  index: number;
  stageHeight: number;
  offset: MotionValue<number>;
  reducedMotion: boolean;
  opening: string | null;
  stopping: string | null;
  onOpen: (run: RunSummary) => void;
  onStop: (runId: string) => void;
}) {
  const restingTop = Math.max(0, stageHeight - INITIAL_ROWS * CARD_PITCH);
  const exitEnd = Math.max(32, Math.min(96, restingTop - 8));
  const y = useTransform(offset, (value) => restingTop + index * CARD_PITCH - value);
  const opacity = useTransform(y, (value) => {
    if (value >= exitEnd) return 1;
    return clamp((value - 4) / Math.max(1, exitEnd - 4), 0, 1);
  });
  const scale = useTransform(y, (value) => {
    if (reducedMotion || value >= exitEnd) return 1;
    const progress = clamp((value - 4) / Math.max(1, exitEnd - 4), 0, 1);
    return 0.78 + progress * 0.22;
  });
  const rotate = useTransform(y, (value) => {
    if (reducedMotion || value >= exitEnd) return 0;
    const progress = clamp((value - 4) / Math.max(1, exitEnd - 4), 0, 1);
    return -1.2 * (1 - progress);
  });
  const filter = useTransform(y, (value) => {
    if (reducedMotion || value >= exitEnd) return "blur(0px)";
    const progress = clamp((value - 4) / Math.max(1, exitEnd - 4), 0, 1);
    return `blur(${(1 - progress) * 3.5}px)`;
  });

  return (
    <motion.div
      className="run-stack-card"
      style={{ y, opacity, scale, rotate, filter, zIndex: 100 - index }}
    >
      <article className="run-row">
        <div className="run-row-copy">
          <span>{run.seed || "Untitled idea"}</span>
        </div>
        <div className="run-actions">
          <button className="view-run-button" type="button" onClick={() => onOpen(run)} disabled={opening !== null}>
            {opening === run.run_id ? "Opening…" : "View results"}
          </button>
          {run.state && ["preparing", "ready", "running"].includes(run.state) && (
            <button className="stop-button" type="button" onClick={() => onStop(run.run_id)} disabled={stopping !== null}>
              {stopping === run.run_id ? "Stopping…" : "Stop"}
            </button>
          )}
        </div>
      </article>
    </motion.div>
  );
}

export function RunInspector({ onOpenRun }: { onOpenRun: (runId: string, seed: string | null) => Promise<void> }) {
  const stageRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<{ pointerId: number; startY: number; startOffset: number } | null>(null);
  const activeIndexRef = useRef(0);
  const reducedMotion = useReducedMotion() ?? false;
  const targetOffset = useMotionValue(0);
  const springOffset = useSpring(targetOffset, { stiffness: 190, damping: 28, mass: 0.72 });
  const displayOffset = reducedMotion ? targetOffset : springOffset;
  const [stageHeight, setStageHeight] = useState(0);
  const [activeIndex, setActiveIndex] = useState(0);
  const [runs, setRuns] = useState<TownRun[]>([]);
  const [loading, setLoading] = useState(true);
  const [stopping, setStopping] = useState<string | null>(null);
  const [opening, setOpening] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function refresh() {
    setLoading(true);
    try {
      const summaries = await listRuns();
      const withStatus = await Promise.all(
        summaries.map(async (run) => {
          try {
            return { ...run, state: (await getRunStatus(run.run_id)).state };
          } catch {
            return run;
          }
        }),
      );
      setRuns(withStatus);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't load runs.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void refresh();
  }, []);

  useEffect(() => {
    const stage = stageRef.current;
    if (!stage) return;
    const updateHeight = () => setStageHeight(stage.clientHeight);
    updateHeight();
    const observer = new ResizeObserver(updateHeight);
    observer.observe(stage);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    targetOffset.jump(0);
    springOffset.jump(0);
    activeIndexRef.current = 0;
    setActiveIndex(0);
  }, [runs.length, springOffset, targetOffset]);

  useMotionValueEvent(displayOffset, "change", (value) => {
    const next = Math.max(0, Math.floor(value / CARD_PITCH));
    if (next === activeIndexRef.current) return;
    activeIndexRef.current = next;
    setActiveIndex(next);
  });

  const maxOffset = Math.max(0, (runs.length - INITIAL_ROWS) * CARD_PITCH);

  function moveTo(value: number) {
    targetOffset.set(clamp(value, 0, maxOffset));
  }

  function handleWheel(event: WheelEvent<HTMLDivElement>) {
    event.preventDefault();
    const unit = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? Math.max(stageHeight, 1) : 1;
    moveTo(targetOffset.get() + event.deltaY * unit * 0.82);
  }

  function handleKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    const moves: Record<string, number> = {
      ArrowDown: CARD_PITCH,
      ArrowUp: -CARD_PITCH,
      PageDown: CARD_PITCH * 2,
      PageUp: -CARD_PITCH * 2,
    };
    if (event.key === "Home" || event.key === "End") {
      event.preventDefault();
      moveTo(event.key === "Home" ? 0 : maxOffset);
      return;
    }
    const amount = moves[event.key];
    if (amount === undefined) return;
    event.preventDefault();
    moveTo(targetOffset.get() + amount);
  }

  function handlePointerDown(event: PointerEvent<HTMLDivElement>) {
    if (event.pointerType === "mouse" && event.button !== 0) return;
    if ((event.target as Element).closest("button")) return;
    dragRef.current = { pointerId: event.pointerId, startY: event.clientY, startOffset: targetOffset.get() };
    event.currentTarget.setPointerCapture(event.pointerId);
  }

  function handlePointerMove(event: PointerEvent<HTMLDivElement>) {
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    moveTo(drag.startOffset + drag.startY - event.clientY);
  }

  function handlePointerEnd(event: PointerEvent<HTMLDivElement>) {
    if (dragRef.current?.pointerId !== event.pointerId) return;
    dragRef.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
  }

  async function stop(runId: string) {
    setStopping(runId);
    setError(null);
    try {
      await stopRun(runId);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't stop that run.");
    } finally {
      setStopping(null);
    }
  }

  async function open(run: RunSummary) {
    setOpening(run.run_id);
    setError(null);
    try {
      await onOpenRun(run.run_id, run.seed);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't open that town.");
      setOpening(null);
    }
  }

  return (
    <section className="run-inspector" aria-label="Recent town runs">
      <div className="run-inspector-header">
        <div className="run-heading">
          <h2>Recent towns</h2>
          <button className="refresh-button" type="button" onClick={() => void refresh()} disabled={loading || stopping !== null} aria-label="Refresh towns" title="Refresh towns">
            <svg className="refresh-icon" viewBox="0 0 24 24" aria-hidden="true">
              <path d="M20 11a8 8 0 0 0-14.6-4.6L3 9m0-5v5h5m-4 4a8 8 0 0 0 14.6 4.6L21 15m0 5v-5h-5" />
            </svg>
          </button>
        </div>
      </div>
      {loading && <p className="run-muted">Looking for towns…</p>}
      {!loading && !runs.length && <p className="run-muted">No runs yet.</p>}
      <div
        className="run-stack"
        ref={stageRef}
        role="region"
        aria-label="Recent towns. Scroll to browse older towns."
        tabIndex={runs.length ? 0 : -1}
        onWheel={handleWheel}
        onKeyDown={handleKeyDown}
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerEnd}
        onPointerCancel={handlePointerEnd}
      >
        {runs.map((run, index) =>
          index >= activeIndex - 1 && index <= activeIndex + 4 ? (
            <RunStackCard
              key={run.run_id}
              run={run}
              index={index}
              stageHeight={stageHeight}
              offset={displayOffset}
              reducedMotion={reducedMotion}
              opening={opening}
              stopping={stopping}
              onOpen={(selectedRun) => void open(selectedRun)}
              onStop={(runId) => void stop(runId)}
            />
          ) : null,
        )}
      </div>
      {error && <p className="run-error" role="alert">{error}</p>}
    </section>
  );
}
