import { Html } from "@react-three/drei";
import { useState } from "react";

/**
 * A speech bubble pinned above a house. It pops in, stays readable for a few seconds and fades
 * out, all in one CSS animation. Give it a new `key` for each new line so the animation restarts.
 */
export function SpeechBubble({
  position,
  speaker,
  text,
  lastWords,
}: {
  position: [number, number, number];
  speaker: string;
  text: string;
  lastWords: boolean;
}) {
  const [done, setDone] = useState(false);
  if (done) return null;
  return (
    <Html position={position} zIndexRange={[10, 0]} style={{ pointerEvents: "none" }}>
      <div className={lastWords ? "bubble bubble-last" : "bubble"} onAnimationEnd={() => setDone(true)}>
        <span className="bubble-speaker">{speaker}</span>
        {text}
      </div>
    </Html>
  );
}
