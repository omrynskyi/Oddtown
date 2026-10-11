import { Clone, useGLTF } from "@react-three/drei";
import type { ThreeElements } from "@react-three/fiber";
import { ALL_MODELS, modelUrl } from "./layout";

// Start fetching every model as soon as the app loads, so buildings are ready to pop.
for (const model of ALL_MODELS) useGLTF.preload(modelUrl(model));

export function Model({ name, ...props }: { name: string } & ThreeElements["group"]) {
  const { scene } = useGLTF(modelUrl(name));
  return (
    <group {...props}>
      <Clone object={scene} castShadow receiveShadow />
    </group>
  );
}
