import { Canvas, useThree } from "@react-three/fiber";
import { Suspense, useLayoutEffect, useMemo } from "react";
import { Vector3 } from "three";
import { shortName } from "../names";
import type { Town } from "../town";
import { Building } from "./Building";
import { planCity, type CityPlan } from "./generate";
import { BUILDING_HEIGHTS, TILE } from "./layout";
import { Model } from "./Model";
import { SpeechBubble } from "./SpeechBubble";

/** The tallest KayKit building, in world units, for fitting the camera. */
const BUILDING_HEIGHT = 3.1;

/** The isometric city. Each run gets its own town, and each lot gets its character's building. */
export function City({ town }: { town: Town }) {
  const plan = useMemo(() => planCity(town.runId ?? "town"), [town.runId]);

  // Characters are stored in birth order, dead ones included, so nobody's building ever changes.
  const buildings = useMemo(() => {
    const ids = Object.keys(town.characters);
    return Object.fromEntries(ids.map((id, i) => [id, plan.buildingOrder[i % plan.buildingOrder.length]]));
  }, [town.characters, plan]);

  return (
    <Canvas orthographic shadows="percentage" flat dpr={[1, 2]} camera={{ near: 0.1, far: 200 }}>
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
          {plan.lots.map((lot, i) => {
            const id = town.lots[i];
            return id ? (
              <Building key={id} model={buildings[id]} lot={lot} />
            ) : (
              <Model key={`empty-${i}`} name={lot.model} position={lot.position} />
            );
          })}
        </Suspense>
        {plan.lots.map((lot, i) => {
          const id = town.lots[i];
          const speech = id ? town.speech[id] : undefined;
          if (!id || !speech) return null;
          // Neighbors' bubbles sit at slightly different heights so they overlap less.
          const height = (BUILDING_HEIGHTS[buildings[id]] ?? 3) + 0.35 + (i % 2) * 0.6;
          return (
            <SpeechBubble
              key={`${id}-${speech.seq}`}
              position={[lot.position[0], height, lot.position[2]]}
              speaker={shortName(town.characters[id].persona.name)}
              text={speech.text}
              lastWords={speech.lastWords}
            />
          );
        })}
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
