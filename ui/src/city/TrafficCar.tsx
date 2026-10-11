import { useFrame } from "@react-three/fiber";
import { useRef } from "react";
import type { Group } from "three";
import type { TrafficRoute } from "./generate";
import { Model } from "./Model";

const DRIVE_SECONDS = 5.2;
const CYCLE_SECONDS = 13;
const reducedMotion = typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

/** A little traffic clock: wait, cross the whole street, disappear beyond the kerb, repeat. */
export function TrafficCar({ route, paused = false }: { route: TrafficRoute; paused?: boolean }) {
  const group = useRef<Group>(null);
  const elapsed = useRef(1.25 + route.phase * CYCLE_SECONDS);

  useFrame((_, delta) => {
    const car = group.current;
    if (!car || document.hidden || reducedMotion) return;

    if (!paused) elapsed.current += delta;
    const cycleTime = elapsed.current % CYCLE_SECONDS;
    const progress = Math.min(cycleTime / DRIVE_SECONDS, 1);

    // Smooth acceleration and braking read as intentional traffic, rather than a sliding prop.
    const eased = progress * progress * (3 - 2 * progress);
    car.position.set(
      route.from[0] + (route.to[0] - route.from[0]) * eased,
      route.from[1] + Math.sin(elapsed.current * 11) * 0.008,
      route.from[2] + (route.to[2] - route.from[2]) * eased,
    );

    // The route extends beyond the platform; shrinking only at its hidden edges softens the reset.
    const edge = Math.min(progress / 0.035, (1 - progress) / 0.035, 1);
    car.scale.setScalar(Math.max(0, edge));
    car.rotation.z = Math.sin(elapsed.current * 8) * 0.004;
  });

  const parkedProgress = 0.22 + route.phase * 1.12;
  const parked: [number, number, number] = [
    route.from[0] + (route.to[0] - route.from[0]) * parkedProgress,
    route.from[1],
    route.from[2] + (route.to[2] - route.from[2]) * parkedProgress,
  ];

  return (
    <group
      ref={group}
      position={reducedMotion ? parked : route.from}
      rotation={[0, route.rotation, 0]}
      scale={reducedMotion ? 1 : 0}
    >
      <Model name={route.model} />
    </group>
  );
}
