import { Html } from "@react-three/drei";
import { Canvas, useThree } from "@react-three/fiber";
import { Suspense, useCallback, useLayoutEffect, useMemo, useState } from "react";
import { Vector3 } from "three";
import { shortName } from "../names";
import type { Town } from "../town";
import { Building } from "./Building";
import { planCity, type CityPlan } from "./generate";
import { BUILDING_HEIGHTS, TILE } from "./layout";
import { HouseCards, type Agreement, type CardItem } from "./HouseCards";
import { Model } from "./Model";
import { TrafficCar } from "./TrafficCar";

/** The tallest KayKit building, in world units, for fitting the camera. */
const BUILDING_HEIGHT = 3.1;

/** The isometric city. Each run gets its own town, and each lot gets its character's building. */
export function City({
  town,
  trafficPaused = false,
  selectedId,
  onSelect,
}: {
  town: Town;
  trafficPaused?: boolean;
  /** The house whose resident is open in the side panel. */
  selectedId: string | null;
  onSelect: (id: string | null) => void;
}) {
  const plan = useMemo(() => planCity(town.runId ?? "town"), [town.runId]);

  // Characters are stored in birth order, dead ones included, so nobody's building ever changes.
  const buildings = useMemo(() => {
    const ids = Object.keys(town.characters);
    return Object.fromEntries(ids.map((id, i) => [id, plan.buildingOrder[i % plan.buildingOrder.length]]));
  }, [town.characters, plan]);

  // A card goes once it has faded.
  const [faded, setFaded] = useState<ReadonlySet<string>>(new Set());
  const fade = useCallback((key: string) => setFaded((set) => new Set(set).add(key)), []);
  const [hoveredHouse, setHoveredHouse] = useState<string | null>(null);

  /** Where a character's roof is, for letters to leave from. */
  const roofOf = (id: string): [number, number, number] => {
    const lot = plan.lots[town.characters[id].lot];
    return [lot.position[0], (BUILDING_HEIGHTS[buildings[id]] ?? 3) + 0.2, lot.position[2]];
  };

  /** What each house shows: its own line, then the letters sent to it, oldest first. */
  const cardsFor = (id: string): CardItem[] => {
    const items: (CardItem & { seq: number })[] = [];
    const speech = town.speech[id];
    if (speech) {
      const speaker = shortName(town.characters[id].persona.name);
      items.push({ key: `say-${speech.seq}`, seq: speech.seq, speaker, text: speech.text, kind: speech.lastWords ? "last" : "said" });
    }
    for (const letter of town.interactions) {
      if (letter.to !== id) continue;
      const speaker = shortName(town.characters[letter.from].persona.name);
      items.push({ key: `letter-${letter.seq}`, seq: letter.seq, speaker, text: letter.text, kind: letter.kind, from: roofOf(letter.from) });
    }
    return items.filter((item) => !faded.has(item.key)).sort((x, y) => x.seq - y.seq);
  };

  /** A positive reply belongs to both neighbors, so its celebration appears over both roofs. */
  const agreementsFor = (id: string): Agreement[] =>
    town.interactions
      .filter((item) => item.kind === "agree" && (item.from === id || item.to === id))
      .map((item) => ({ key: `agreement-${item.seq}` }));

  return (
    <Canvas
      orthographic
      shadows="percentage"
      flat
      dpr={[1, 2]}
      // The selection outline masks itself with the stencil buffer.
      gl={{ stencil: true }}
      camera={{ near: 0.1, far: 200 }}
      style={{ cursor: hoveredHouse ? "pointer" : "auto" }}
      onPointerMissed={() => onSelect(null)}
    >
      <IsometricCamera plan={plan} />
      <hemisphereLight args={["#ffffff", "#9aa3ad", 1.6]} />
      <directionalLight
        position={[7, 14, 5]}
        intensity={2.2}
        castShadow
        shadow-mapSize={[2048, 2048]}
        shadow-camera-left={-14}
        shadow-camera-right={14}
        shadow-camera-top={14}
        shadow-camera-bottom={-14}
        shadow-bias={-0.0005}
      />
      <group>
        <Suspense fallback={null}>
          {plan.scenery.map((piece, i) => (
            <Model key={i} name={piece.model} position={piece.position} rotation={[0, piece.rotation, 0]} />
          ))}
          {plan.traffic.map((route, i) => (
            <TrafficCar key={`${route.model}-${i}`} route={route} paused={trafficPaused} />
          ))}
          {plan.lots.map((lot, i) => {
            const id = town.lots[i];
            return id ? (
              <Building
                key={id}
                model={buildings[id]}
                lot={lot}
                selected={selectedId === id}
                onHover={() => setHoveredHouse(id)}
                onLeave={() => setHoveredHouse((current) => (current === id ? null : current))}
                onSelect={() => onSelect(id)}
              />
            ) : (
              <Model key={`empty-${i}`} name={lot.model} position={lot.position} />
            );
          })}
        </Suspense>
        {plan.lots.map((lot, i) => {
          const id = town.lots[i];
          if (!id) return null;
          return (
            <HouseCards
              key={id}
              position={[lot.position[0], (BUILDING_HEIGHTS[buildings[id]] ?? 3) + 0.35, lot.position[2]]}
              items={cardsFor(id)}
              agreements={agreementsFor(id)}
              onDone={fade}
            />
          );
        })}
        {hoveredHouse && town.characters[hoveredHouse] && (
          <Html
            position={[
              plan.lots[town.characters[hoveredHouse].lot].position[0],
              (BUILDING_HEIGHTS[buildings[hoveredHouse]] ?? 3) / 2,
              plan.lots[town.characters[hoveredHouse].lot].position[2],
            ]}
            zIndexRange={[20, 11]}
            style={{ pointerEvents: "none" }}
          >
            <div className="house-name">{town.characters[hoveredHouse].persona.name}</div>
          </Html>
        )}
      </group>
    </Canvas>
  );
}

/** Space kept clear at the top of the screen for the title. */
const TITLE_SPACE = 150;

/** Looks down at the town from the corner and zooms so every block fits below the title. */
function IsometricCamera({ plan }: { plan: CityPlan }) {
  const camera = useThree((s) => s.camera);
  const size = useThree((s) => s.size);

  useLayoutEffect(() => {
    camera.position.set(12, 11, 12);
    camera.lookAt(0, 0, 0);
    camera.updateMatrixWorld();

    // Measure the town as the camera sees it: every block's corners, and the tops of the lots.
    const lotKeys = new Set(plan.lots.map((l) => `${l.position[0]},${l.position[2]}`));
    let [minX, maxX, minY, maxY] = [Infinity, -Infinity, Infinity, -Infinity];
    const point = new Vector3();
    for (const [x, z] of plan.blocks) {
      const tall = lotKeys.has(`${x * TILE},${z * TILE}`);
      for (const [dx, dz] of [[-1, -1], [-1, 1], [1, -1], [1, 1]]) {
        for (const y of tall ? [0, BUILDING_HEIGHT] : [0]) {
          point.set(x * TILE + dx, y, z * TILE + dz).applyMatrix4(camera.matrixWorldInverse);
          [minX, maxX, minY, maxY] = [Math.min(minX, point.x), Math.max(maxX, point.x), Math.min(minY, point.y), Math.max(maxY, point.y)];
        }
      }
    }

    // Center the town in the space under the title, with a little margin.
    camera.translateX((minX + maxX) / 2);
    camera.translateY((minY + maxY) / 2);
    const room = Math.max(size.height - TITLE_SPACE, size.height * 0.5);
    camera.zoom = Math.min(size.width / ((maxX - minX) * 1.12), room / ((maxY - minY) * 1.12));
    camera.setViewOffset(size.width, size.height, 0, -(size.height - room) / 2, size.width, size.height);
    camera.updateProjectionMatrix();
  }, [camera, size, plan]);

  return null;
}
