"use client";
import { useEffect, useRef } from "react";
import { useFrame } from "@react-three/fiber";
import type * as THREE from "three";
import { NEXT_IN_PIPELINE, STATIONS } from "./layout3d";
import type { ActivityEvent, RobotId } from "@/lib/types";

const FLIGHT_S = 1.4;
interface Packet { from: [number, number]; to: [number, number]; t0: number }

const pos = (id: string): [number, number] | null => {
  const s = STATIONS.find((x) => x.id === id);
  return s ? [s.x, s.z] : null;
};

/** One glowing packet per REAL "COMPLETED" event, flying from that robot to the next one in the pipeline.
 *  History that existed before the page opened is ignored, so nothing "replays" on load. */
export default function Packets({ events, loaded, online }: { events: ActivityEvent[]; loaded: boolean; online: boolean }) {
  const seen = useRef<number | null>(null);
  const list = useRef<Packet[]>([]);
  const meshes = useRef<(THREE.Mesh | null)[]>([]);
  const clockRef = useRef(0);

  useEffect(() => {
    if (!loaded) return;
    const max = events.length ? events[events.length - 1].seq : 0;
    if (seen.current === null) { seen.current = max; return; }
    if (max < seen.current) seen.current = 0; // backend restarted
    const fresh = events.filter((e) => e.seq > (seen.current as number));
    seen.current = Math.max(seen.current, max);
    if (!online) return;
    for (const e of fresh) {
      if (e.kind !== "state" || e.state !== "COMPLETED") continue;
      const next = NEXT_IN_PIPELINE[e.robot_id as RobotId];
      const a = pos(e.robot_id), b = next ? pos(next) : null;
      if (a && b && list.current.length < meshes.current.length) list.current.push({ from: a, to: b, t0: clockRef.current });
    }
  }, [events, loaded, online]);

  useFrame(({ clock }) => {
    clockRef.current = clock.getElapsedTime();
    const now = clockRef.current;
    list.current = list.current.filter((p) => now - p.t0 < FLIGHT_S);
    meshes.current.forEach((m, i) => {
      const p = list.current[i];
      if (!m) return;
      if (!p) { m.visible = false; return; }
      const k = (now - p.t0) / FLIGHT_S;
      m.visible = true;
      m.position.set(p.from[0] + (p.to[0] - p.from[0]) * k, 1.6 + Math.sin(Math.PI * k) * 1.2, p.from[1] + (p.to[1] - p.from[1]) * k);
    });
  });

  return (
    <group>
      {Array.from({ length: 12 }, (_, i) => (
        <mesh key={i} visible={false} ref={(m) => { meshes.current[i] = m; }}>
          <sphereGeometry args={[0.12, 12, 12]} />
          <meshStandardMaterial color="#9fe8ff" emissive="#5fd4ff" emissiveIntensity={2.5} />
        </mesh>
      ))}
    </group>
  );
}
