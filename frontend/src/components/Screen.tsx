"use client";
import { useEffect, useMemo, useRef } from "react";
import { useFrame } from "@react-three/fiber";
import * as THREE from "three";

interface Props {
  lines: string[];
  title: string;
  color: string;
  active: boolean; // backend says this robot is working/analysing: show the scan bar
  size: [number, number];
  position: [number, number, number];
}

const W = 512;
const H = 288;

/** Wall screen showing REAL text from the backend (robot detail, balances, counts). The scan bar only moves while active. */
export default function Screen({ lines, title, color, active, size, position }: Props) {
  const { canvas, tex } = useMemo(() => {
    const c = document.createElement("canvas");
    c.width = W; c.height = H;
    const t = new THREE.CanvasTexture(c);
    t.colorSpace = THREE.SRGBColorSpace;
    return { canvas: c, tex: t };
  }, []);
  const last = useRef(0);

  const draw = (scan: number) => {
    const g = canvas.getContext("2d");
    if (!g) return;
    g.fillStyle = "#070b14"; g.fillRect(0, 0, W, H);
    g.strokeStyle = color; g.globalAlpha = 0.9; g.lineWidth = 3; g.strokeRect(6, 6, W - 12, H - 12);
    g.globalAlpha = 1;
    g.fillStyle = color; g.font = "bold 26px monospace"; g.fillText(title.toUpperCase(), 22, 44);
    g.fillStyle = "#cfe3ff"; g.font = "22px monospace";
    lines.slice(0, 7).forEach((l, i) => g.fillText(l, 22, 86 + i * 30));
    if (active) {
      g.fillStyle = color; g.globalAlpha = 0.22; g.fillRect(8, 8 + scan * (H - 24), W - 16, 8); g.globalAlpha = 1;
    }
    tex.needsUpdate = true;
  };

  useEffect(() => { draw(0); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [lines, title, color, active]);
  useEffect(() => () => tex.dispose(), [tex]);

  useFrame(({ clock }) => {
    if (!active) return;
    const t = clock.getElapsedTime();
    if (t - last.current < 0.1) return; // ~10 fps is plenty for a scan bar
    last.current = t;
    draw((t * 0.6) % 1);
  });

  return (
    <mesh position={position}>
      <planeGeometry args={size} />
      <meshBasicMaterial map={tex} toneMapped={false} />
    </mesh>
  );
}
