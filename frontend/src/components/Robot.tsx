"use client";
import { useRef } from "react";
import { useFrame } from "@react-three/fiber";
import type * as THREE from "three";
import { STATE_COLOR, motionFor, poseAt, type Visual } from "@/lib/motion";

interface Props { visual: Visual; phase: number; accent: string }

const METAL = "#c9d3e6";
const DARK = "#222a3c";

function Limb({ w, h, d, color = METAL }: { w: number; h: number; d: number; color?: string }) {
  return (
    <mesh position={[0, -h / 2, 0]} castShadow>
      <boxGeometry args={[w, h, d]} />
      <meshStandardMaterial color={color} metalness={0.6} roughness={0.35} />
    </mesh>
  );
}

/** Seated humanoid robot built from primitives. Facing +z (towards its desk). Motion comes from `visual` only. */
export default function Robot({ visual, phase, accent }: Props) {
  const torso = useRef<THREE.Group>(null);
  const head = useRef<THREE.Group>(null);
  const lFore = useRef<THREE.Group>(null);
  const rFore = useRef<THREE.Group>(null);
  const visor = useRef<THREE.MeshStandardMaterial>(null);
  const chest = useRef<THREE.MeshStandardMaterial>(null);
  const color = STATE_COLOR[visual];

  useFrame(({ clock }) => {
    const t = clock.getElapsedTime();
    const p = poseAt(motionFor(visual), t, phase);
    if (torso.current) torso.current.rotation.x = p.torsoPitch;
    if (head.current) { head.current.rotation.y = p.headYaw; head.current.rotation.x = p.headPitch; }
    if (lFore.current) lFore.current.rotation.x = -0.75 + p.leftForearm;
    if (rFore.current) rFore.current.rotation.x = -0.75 + p.rightForearm;
    const glow = 0.9 + 1.4 * p.pulse;
    if (visor.current) visor.current.emissiveIntensity = glow;
    if (chest.current) chest.current.emissiveIntensity = glow;
  });

  return (
    <group>
      {/* chair */}
      <mesh position={[0, 0.46, 0]}><boxGeometry args={[0.7, 0.08, 0.7]} /><meshStandardMaterial color={DARK} /></mesh>
      <mesh position={[0, 0.9, -0.34]}><boxGeometry args={[0.66, 0.8, 0.06]} /><meshStandardMaterial color={DARK} /></mesh>
      <mesh position={[0, 0.23, 0]}><cylinderGeometry args={[0.06, 0.06, 0.46, 12]} /><meshStandardMaterial color="#3a4560" /></mesh>
      <mesh position={[0, 0.02, 0]}><cylinderGeometry args={[0.38, 0.38, 0.04, 20]} /><meshStandardMaterial color="#3a4560" /></mesh>

      {/* legs (seated): thighs forward, shins down */}
      {[-0.17, 0.17].map((x) => (
        <group key={x} position={[x, 0.56, 0.05]}>
          <mesh position={[0, 0, 0.25]}><boxGeometry args={[0.2, 0.2, 0.55]} /><meshStandardMaterial color={METAL} metalness={0.6} roughness={0.4} /></mesh>
          <group position={[0, 0, 0.5]}><Limb w={0.17} h={0.55} d={0.17} /></group>
        </group>
      ))}

      {/* torso pivots at the hip */}
      <group ref={torso} position={[0, 0.56, -0.05]}>
        <mesh position={[0, 0.4, 0]} castShadow><boxGeometry args={[0.56, 0.72, 0.34]} /><meshStandardMaterial color={METAL} metalness={0.6} roughness={0.35} /></mesh>
        <mesh position={[0, 0.5, 0.18]}><boxGeometry args={[0.2, 0.2, 0.03]} />
          <meshStandardMaterial ref={chest} color="#101521" emissive={color} emissiveIntensity={1} /></mesh>
        <mesh position={[0, 0.34, 0.18]}><boxGeometry args={[0.4, 0.05, 0.03]} /><meshStandardMaterial color={accent} emissive={accent} emissiveIntensity={0.6} /></mesh>

        {/* head */}
        <group ref={head} position={[0, 0.95, 0]}>
          <mesh castShadow><boxGeometry args={[0.38, 0.34, 0.34]} /><meshStandardMaterial color={METAL} metalness={0.6} roughness={0.3} /></mesh>
          <mesh position={[0, 0.02, 0.175]}><boxGeometry args={[0.3, 0.14, 0.02]} />
            <meshStandardMaterial ref={visor} color="#0a0d16" emissive={color} emissiveIntensity={1} /></mesh>
          <mesh position={[0, 0.3, 0]}><cylinderGeometry args={[0.012, 0.012, 0.22, 6]} /><meshStandardMaterial color="#5b6784" /></mesh>
          <mesh position={[0, 0.42, 0]}><sphereGeometry args={[0.04, 10, 10]} /><meshStandardMaterial color={accent} emissive={accent} emissiveIntensity={1.2} /></mesh>
        </group>

        {/* arms: shoulder -> upper arm (fixed) -> forearm (typing) */}
        {[
          { x: -0.36, ref: lFore }, { x: 0.36, ref: rFore },
        ].map(({ x, ref }) => (
          <group key={x} position={[x, 0.68, 0]}>
            <mesh><sphereGeometry args={[0.09, 12, 12]} /><meshStandardMaterial color="#8f9bb5" metalness={0.7} roughness={0.3} /></mesh>
            <group rotation={[-0.95, 0, 0]}>
              <Limb w={0.13} h={0.36} d={0.13} />
              <group position={[0, -0.36, 0]} ref={ref}>
                <Limb w={0.11} h={0.34} d={0.11} />
                <mesh position={[0, -0.37, 0]}><boxGeometry args={[0.12, 0.08, 0.14]} /><meshStandardMaterial color="#8f9bb5" metalness={0.7} roughness={0.3} /></mesh>
              </group>
            </group>
          </group>
        ))}
      </group>
    </group>
  );
}
