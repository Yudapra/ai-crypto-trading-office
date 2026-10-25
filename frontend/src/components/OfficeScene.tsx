"use client";
import { useEffect, useRef } from "react";
import { Canvas, useThree } from "@react-three/fiber";
import Station from "./Station";
import Packets from "./Packets";
import { STATIONS } from "./layout3d";
import type { OfficeData } from "@/lib/useOffice";

/** Slow sway plus drag-to-rotate. Pulls back on narrow (phone) screens so all six desks fit. */
function CameraRig() {
  const { camera, gl, size } = useThree();
  const yaw = useRef(0);
  const target = useRef(0);

  useEffect(() => {
    const el = gl.domElement;
    let down = false, lastX = 0;
    const onDown = (e: PointerEvent) => { down = true; lastX = e.clientX; };
    const onUp = () => { down = false; };
    const onMove = (e: PointerEvent) => {
      if (!down) return;
      target.current = Math.max(-0.8, Math.min(0.8, target.current + (e.clientX - lastX) * 0.004));
      lastX = e.clientX;
    };
    el.addEventListener("pointerdown", onDown);
    window.addEventListener("pointerup", onUp);
    el.addEventListener("pointermove", onMove);
    return () => {
      el.removeEventListener("pointerdown", onDown);
      window.removeEventListener("pointerup", onUp);
      el.removeEventListener("pointermove", onMove);
    };
  }, [gl]);

  useEffect(() => {
    let raf = 0;
    const loop = (ms: number) => {
      yaw.current += (target.current + Math.sin(ms / 6000) * 0.08 - yaw.current) * 0.06;
      const narrow = size.width / Math.max(1, size.height) < 0.9;
      const dist = narrow ? 17 : 12.5, height = narrow ? 11 : 8;
      camera.position.set(Math.sin(yaw.current) * dist, height, Math.cos(yaw.current) * dist - 0.5);
      camera.lookAt(0, 1.4, -0.8);
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
  }, [camera, size]);
  return null;
}

export default function OfficeScene({ data }: { data: OfficeData }) {
  const byId = new Map((data.state?.robots ?? []).map((r) => [r.id, r]));
  return (
    <Canvas shadows dpr={[1, 1.75]} camera={{ fov: 45, near: 0.1, far: 80, position: [0, 8, 12] }}
            gl={{ antialias: true, powerPreference: "high-performance" }}>
      <color attach="background" args={["#05070d"]} />
      <fog attach="fog" args={["#05070d", 22, 48]} />
      <hemisphereLight args={["#8fb4ff", "#10131f", 0.7]} />
      <directionalLight position={[6, 12, 8]} intensity={0.9} castShadow />
      <CameraRig />
      <mesh rotation={[-Math.PI / 2, 0, 0]} receiveShadow>
        <planeGeometry args={[40, 40]} />
        <meshStandardMaterial color="#0b1020" metalness={0.4} roughness={0.7} />
      </mesh>
      <gridHelper args={[40, 40, "#1c2d52", "#111b33"]} position={[0, 0.01, 0]} />
      {STATIONS.map((s, i) => (
        <Station key={s.id} def={s} robot={byId.get(s.id)} state={data.state} online={data.online} phase={i * 1.3} />
      ))}
      <Packets events={data.events} loaded={data.loaded} online={data.online} />
    </Canvas>
  );
}
