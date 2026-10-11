import { Html } from "@react-three/drei";
import { useThree } from "@react-three/fiber";
import { useLayoutEffect, useRef, useState, type CSSProperties } from "react";
import { type Camera, Vector3 } from "three";

export type Point = [number, number, number];

/**
 * One line to show over a house. `said` and `last` are a character's own words. The rest are
 * letters: a plain message, or a reply marked by how it was answered. A letter has the roof it
 * was sent from, so it can fly from there.
 */
export interface CardItem {
  key: string;
  speaker: string;
  text: string;
  kind: "said" | "last" | "letter" | "agree" | "disagree" | "mixed";
  from?: Point;
}

export interface Agreement {
  key: string;
}

const STAMPS: Partial<Record<CardItem["kind"], string>> = {
  agree: "👏 Yes!",
  disagree: "🍅 Boooo!",
  mixed: "🤔 Hmm…",
};

/** Most cards a house shows at once. The oldest drop off the top. */
const MAX_CARDS = 3;

/** The envelope's size in pixels, which the letter starts at and unfolds from. */
const ENVELOPE = { width: 44, height: 32 };

/**
 * Everything a house is saying, as a stack of cards just above its roof, newest at the bottom.
 * Each one fades by itself and then calls `onDone`.
 */
export function HouseCards({
  position,
  items,
  agreements,
  onDone,
}: {
  position: Point;
  items: CardItem[];
  agreements: Agreement[];
  onDone: (key: string) => void;
}) {
  // Read the scene state here, before Html portals its children into the DOM.
  const camera = useThree((s) => s.camera);
  const size = useThree((s) => s.size);
  const canvas = useThree((s) => s.gl.domElement);
  if (items.length === 0 && agreements.length === 0) return null;
  return (
    <Html position={position} zIndexRange={[10, 0]} style={{ pointerEvents: "none" }}>
      <div className="stack">
        {agreements.map((agreement) => (
          <div key={agreement.key} className="agreement-badge" role="img" aria-label="Agreement">
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="M8.5 20H5.75A1.75 1.75 0 0 1 4 18.25v-6.5C4 10.78 4.78 10 5.75 10H8.5v10Zm0 0h7.16c.84 0 1.57-.56 1.8-1.37l1.66-5.9A1.75 1.75 0 0 0 17.44 10H14l.46-2.76A3.13 3.13 0 0 0 11.38 3L8.5 10v10Z" />
            </svg>
          </div>
        ))}
        {items.slice(-MAX_CARDS).map((item) =>
          item.from ? (
            <Letter key={item.key} item={item} from={item.from} onDone={onDone} camera={camera} size={size} canvas={canvas} />
          ) : (
            <div key={item.key} className={`house-card house-card-${item.kind}`} onAnimationEnd={() => onDone(item.key)}>
              <span className="card-from">{item.speaker}</span>
              {item.text}
            </div>
          ),
        )}
      </div>
    </Html>
  );
}

/**
 * A letter, as one SVG-backed element for its whole life. It starts as an envelope over the
 * sender's roof, flies to its place in the stack, opens its flap and unfolds into the card with
 * the text, so there is no hand-off between an envelope and a card. The flight and unfold are
 * CSS animations, which need the distance to travel and the card's size, measured once on mount.
 */
function Letter({
  item,
  from,
  onDone,
  camera,
  size,
  canvas,
}: {
  item: CardItem;
  from: Point;
  onDone: (key: string) => void;
  camera: Camera;
  size: { width: number; height: number };
  canvas: HTMLCanvasElement;
}) {
  const el = useRef<HTMLDivElement>(null);
  const [flight, setFlight] = useState<CSSProperties>();

  // Hidden until measured, so the card never shows for a frame at its landing place.
  useLayoutEffect(() => {
    if (!el.current) return;
    const card = el.current.getBoundingClientRect();
    const origin = canvas.getBoundingClientRect();
    const roof = new Vector3(...from).project(camera);
    const x = origin.left + ((roof.x + 1) / 2) * size.width;
    const y = origin.top + ((1 - roof.y) / 2) * size.height;
    setFlight({
      "--dx": `${x - (card.left + card.width / 2)}px`,
      "--dy": `${y - (card.top + card.height / 2)}px`,
      "--sx": ENVELOPE.width / card.width,
      "--sy": ENVELOPE.height / card.height,
    } as CSSProperties);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div
      ref={el}
      className={`letter letter-${item.kind}${flight ? " letter-go" : ""}`}
      style={flight}
      onAnimationEnd={(e) => {
        if (e.target === e.currentTarget && e.animationName === "letter-life") onDone(item.key);
      }}
    >
      <svg className="letter-body" aria-hidden="true">
        <rect width="100%" height="100%" rx="14" />
      </svg>
      <svg className="letter-decor" viewBox={`0 0 ${ENVELOPE.width} ${ENVELOPE.height}`} preserveAspectRatio="none" aria-hidden="true">
        <path className="letter-seam" d="M2 30 L18 16 M42 30 L26 16" />
        <path className="letter-flap" d="M2 3 H42 L22 20 Z" />
      </svg>
      <div className="letter-text">
        <span className="card-from">{item.speaker}</span>
        {item.text || "…"}
        {STAMPS[item.kind] && <span className="card-stamp">{STAMPS[item.kind]}</span>}
      </div>
    </div>
  );
}
