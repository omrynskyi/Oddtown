import { useFrame } from "@react-three/fiber";
import { useMemo, useRef, useState } from "react";
import type { Group, Mesh, MeshStandardMaterial } from "three";
import type { Placement } from "./layout";
import { Model } from "./Model";

const POP_SECONDS = 0.6;
const DUST_SECONDS = 0.9;
const DUST_PUFFS = 18;

const reducedMotion = typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

/** Overshoots past 1 and settles back, for the bounce. */
function easeOutBack(t: number) {
  const c1 = 1.9;
  return 1 + (c1 + 1) * (t - 1) ** 3 + c1 * (t - 1) ** 2;
}

/** A character's building. It pops up out of its lot with a burst of dust the moment it mounts. */
export function Building({
  model,
  lot,
  selected = false,
  onHover,
  onLeave,
  onSelect,
}: {
  model: string;
  lot: Placement;
  selected?: boolean;
  onHover?: () => void;
  onLeave?: () => void;
  onSelect?: () => void;
}) {
  const group = useRef<Group>(null);
  const start = useRef<number | null>(null);

  useFrame(({ clock }) => {
    if (!group.current) return;
    start.current ??= clock.elapsedTime;
    const t = reducedMotion ? 1 : Math.min((clock.elapsedTime - start.current) / POP_SECONDS, 1);
    // Rises with a squash and stretch: wide and flat at first, tall and thin at the top of the bounce.
    const height = Math.max(easeOutBack(t), 0.001);
    const width = Math.min(Math.max(1 / Math.sqrt(height), 0.9), 1.2);
    group.current.scale.set(width, height, width);
  });

  return (
    <group
      position={lot.position}
      onPointerOver={onHover}
      onPointerOut={onLeave}
      onClick={(event) => {
        event.stopPropagation();
        onSelect?.();
      }}
    >
      <group ref={group} rotation={[0, lot.rotation, 0]} scale={[1.2, 0.001, 1.2]}>
        <Model name={model} outlined={selected} />
      </group>
      {!reducedMotion && <Dust />}
    </group>
  );
}

/** Chunky low-poly puffs that burst out from the lot's edges and fade, then remove themselves. */
function Dust() {
  const [done, setDone] = useState(false);
  const puffs = useRef<(Mesh | null)[]>([]);
  const start = useRef<number | null>(null);
  const seeds = useMemo(
    () =>
      Array.from({ length: DUST_PUFFS }, (_, i) => ({
        angle: (i / DUST_PUFFS) * Math.PI * 2 + Math.random() * 0.35,
        reach: 0.7 + Math.random() * 0.7,
        rise: 0.25 + Math.random() * 0.5,
        size: 0.13 + Math.random() * 0.12,
        spin: (Math.random() - 0.5) * 6,
      })),
    [],
  );

  useFrame(({ clock }) => {
    start.current ??= clock.elapsedTime;
    const p = (clock.elapsedTime - start.current) / DUST_SECONDS;
    if (p >= 1) {
      setDone(true);
      return;
    }
    const out = 1 - (1 - p) ** 3;
    seeds.forEach((seed, i) => {
      const puff = puffs.current[i];
      if (!puff) return;
      const r = 1.05 + seed.reach * out;
      puff.position.set(Math.cos(seed.angle) * r, 0.12 + seed.rise * out, Math.sin(seed.angle) * r);
      puff.scale.setScalar(seed.size * (0.6 + 1.4 * out));
      puff.rotation.set(seed.spin * p, seed.spin * p * 0.7, 0);
      (puff.material as MeshStandardMaterial).opacity = 0.9 * (1 - p) ** 1.5;
    });
  });

  if (done) return null;
  return (
    <group>
      {seeds.map((_, i) => (
        <mesh key={i} ref={(m) => void (puffs.current[i] = m)} scale={0.001}>
          <icosahedronGeometry args={[1, 0]} />
          <meshStandardMaterial color="#efe9dc" transparent opacity={0} flatShading depthWrite={false} />
        </mesh>
      ))}
    </group>
  );
}
