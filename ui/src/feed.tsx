import { useEffect, useRef, useState } from "react";
import { shortName } from "./names";
import type { FeedItem, Town } from "./town";

export const FEED_ICONS: Record<FeedItem["kind"], string> = {
  seed: "🌱",
  twist: "🔀",
  merge: "🧩",
  back: "🤝",
  drop: "👋",
  object: "✋",
  constraint: "⚡",
  birth: "🏠",
  death: "🪦",
  lead: "👑",
  proposal: "🌟",
  rest: "😴",
  end: "🏁",
  summary: "📜",
};

/** One line of town news: an icon, who did it, what they did, and the idea it was about. */
export function FeedLine({ item, town }: { item: FeedItem; town: Town }) {
  const persona = item.actor ? town.characters[item.actor]?.persona : undefined;
  return (
    <>
      <span className="feed-icon" aria-hidden="true">
        {FEED_ICONS[item.kind]}
      </span>
      <span className="feed-text">
        {persona && <strong>{shortName(persona.name)} </strong>}
        {item.text}
        {item.quote && <q>{item.quote}</q>}
      </span>
    </>
  );
}

/** How long each line stays up before the next one takes its place. */
const HOLD_MS = 1800;
/** Further behind than this, the ticker skips ahead instead of reading out old news. */
const MAX_BACKLOG = 3;

/** The feed item the ticker shows: each new one in turn, held long enough to read. */
export function useTicker(feed: FeedItem[]): FeedItem | null {
  const [index, setIndex] = useState(feed.length - 1);
  const shownAt = useRef(0);

  useEffect(() => {
    shownAt.current = performance.now();
  }, [index]);

  useEffect(() => {
    const last = feed.length - 1;
    // A replay scrubbed backwards, or a new run.
    if (index > last) return setIndex(last);
    if (index === last) return;
    if (last - index > MAX_BACKLOG) return setIndex(last - 1);
    const wait = Math.max(0, HOLD_MS - (performance.now() - shownAt.current));
    const timer = window.setTimeout(() => setIndex((i) => i + 1), wait);
    return () => window.clearTimeout(timer);
  }, [feed.length, index]);

  return feed[Math.min(index, feed.length - 1)] ?? null;
}

/** The latest town news, in the title's subtitle line. Clicking it opens the full list. */
export function Ticker({ town, onOpen }: { town: Town; onOpen: () => void }) {
  const item = useTicker(town.feed);
  if (!item) return null;
  return (
    <button className="ticker" type="button" onClick={onOpen} aria-label="Open the town news" aria-live="off">
      <span key={`${item.seq}-${item.kind}`} className="ticker-item">
        <FeedLine item={item} town={town} />
      </span>
    </button>
  );
}
