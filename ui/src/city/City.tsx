import { Canvas, useThree } from "@react-three/fiber";
import { Suspense, useLayoutEffect, useMemo } from "react";
import type { Town } from "../town";
import { Building } from "./Building";
import { assignBuildings, CENTER, LOTS, SCENERY } from "./layout";
import { Model } from "./Model";

/** The isometric city: the street and scenery are always there, and each lot gets its character's building. */
export function City({ town }: { town: Town }) {
  // Characters are stored in birth order, dead ones included.
  const buildings = useMemo(() => assignBuildings(Object.keys(town.characters)), [town.characters]);

  return (
    <Canvas orthographic shadows="percentage" flat dpr={[1, 2]} camera={{ near: 0.1, far: 200 }}>
      <IsometricCamera />
      <hemisphereLight args={["#ffffff", "#9aa3ad", 1.6]} />
      <directionalLight
        position={[7, 14, 5]}
        intensity={2.2}
        castShadow
        shadow-mapSize={[2048, 2048]}
        shadow-camera-left={-9}
        shadow-camera-right={9}
        shadow-camera-top={9}
        shadow-camera-bottom={-9}
        shadow-bias={-0.0005}
      />
      <Suspense fallback={null}>
        {SCENERY.map((piece, i) => (
          <Model key={i} name={piece.model} position={piece.position} rotation={[0, piece.rotation, 0]} />
        ))}
        {LOTS.map((lot, i) => {
          const id = town.lots[i];
          return id ? (
            <Building key={id} model={buildings[id]} lot={lot} />
          ) : (
            <Model key={`empty-${i}`} name={lot.model} position={lot.position} />
          );
        })}
      </Suspense>
    </Canvas>
  );
}

/** Space kept clear at the top of the screen for the title. */
const TITLE_SPACE = 150;

/** Looks down at the city from the corner and zooms so the whole block fits below the title. */
function IsometricCamera() {
  const camera = useThree((s) => s.camera);
  const size = useThree((s) => s.size);

  useLayoutEffect(() => {
    const [x, y, z] = CENTER;
    camera.position.set(x + 12, y + 11, z + 12);
    camera.lookAt(x, y, z);
    const room = Math.max(size.height - TITLE_SPACE, size.height * 0.5);
    camera.zoom = Math.min(size.width / 15, room / 10.5);
    // Shift the picture down so the city is centered in the space under the title.
    camera.setViewOffset(size.width, size.height, 0, -(size.height - room) / 2, size.width, size.height);
    camera.updateProjectionMatrix();
  }, [camera, size]);

  return null;
}
