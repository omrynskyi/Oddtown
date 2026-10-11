import { Clone, useGLTF } from "@react-three/drei";
import { useThree, type ThreeElements } from "@react-three/fiber";
import { useEffect, useMemo } from "react";
import { AlwaysStencilFunc, BackSide, type BufferGeometry, type Mesh, MeshBasicMaterial, NotEqualStencilFunc, ReplaceStencilOp, ShaderMaterial, Vector2 } from "three";
import { toCreasedNormals } from "three-stdlib";
import { ALL_MODELS, modelUrl } from "./layout";

// Start fetching every model as soon as the app loads, so buildings are ready to pop.
for (const model of ALL_MODELS) useGLTF.preload(modelUrl(model));

/** How thick the selection outline is, in screen pixels. */
const OUTLINE_PX = 4;

export function Model({ name, outlined = false, ...props }: { name: string; outlined?: boolean } & ThreeElements["group"]) {
  const { scene } = useGLTF(modelUrl(name));
  return (
    <group {...props}>
      <Clone
        object={scene}
        castShadow
        receiveShadow
        inject={outlined ? (object) => (object as Mesh).isMesh && <Silhouette geometry={(object as Mesh).geometry} /> : undefined}
      />
    </group>
  );
}

/*
 * The outline traces only the building's outer edge. Each piece first marks its own pixels in the
 * stencil buffer, then a slightly fattened back-face hull is drawn everywhere except those pixels,
 * so no lines show between the pieces.
 */
const mask = new MeshBasicMaterial({
  colorWrite: false,
  depthWrite: false,
  stencilWrite: true,
  stencilRef: 1,
  stencilFunc: AlwaysStencilFunc,
  stencilZPass: ReplaceStencilOp,
});

const hull = new ShaderMaterial({
  side: BackSide,
  toneMapped: false,
  stencilWrite: true,
  stencilRef: 1,
  stencilFunc: NotEqualStencilFunc,
  uniforms: { thickness: { value: OUTLINE_PX }, size: { value: new Vector2(1, 1) } },
  vertexShader: `
    uniform float thickness;
    uniform vec2 size;
    void main() {
      vec4 clipPosition = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
      vec4 clipNormal = projectionMatrix * modelViewMatrix * vec4(normal, 0.0);
      clipPosition.xy += normalize(clipNormal.xy) * thickness / size * clipPosition.w * 2.0;
      gl_Position = clipPosition;
    }`,
  fragmentShader: `void main() { gl_FragColor = vec4(1.0); }`,
});

function Silhouette({ geometry }: { geometry: BufferGeometry }) {
  const gl = useThree((s) => s.gl);
  const size = useThree((s) => s.size);
  useEffect(() => void gl.getDrawingBufferSize(hull.uniforms.size.value), [gl, size]);
  // Smoothed normals keep the hull closed at sharp corners.
  const creased = useMemo(() => toCreasedNormals(geometry, Math.PI), [geometry]);
  useEffect(() => () => creased.dispose(), [creased]);
  return (
    <>
      <mesh geometry={geometry} material={mask} renderOrder={1} />
      <mesh geometry={creased} material={hull} renderOrder={2} />
    </>
  );
}
