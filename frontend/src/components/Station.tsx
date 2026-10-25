"use client";
import Robot from "./Robot";
import Screen from "./Screen";
import { ROBOT_TITLE, type Station as StationDef } from "./layout3d";
import { STATE_COLOR, STATE_LABEL_ID, motionFor, type Visual } from "@/lib/motion";
import { usd, wrap } from "@/lib/format";
import type { OfficeState, Robot as RobotData } from "@/lib/types";

const ACCENT: Record<string, string> = {
  ceo: "#ffd24d", research_analyst: "#4dd2ff", market_analyst: "#7d8bff",
  security_analyst: "#ff7ab8", trader: "#4dffb0", accountant: "#ffa64d",
};

function screenLines(id: string, robot: RobotData | undefined, s: OfficeState | null, online: boolean): string[] {
  if (!online || !robot) return ["Backend tidak terhubung.", "Tidak ada data."];
  const out = wrap(robot.detail || "-", 28, 3);
  if (id === "accountant" && s?.portfolio) {
    out.push("", `Equity   ${usd(s.portfolio.equity_usd)}`, `Realized ${usd(s.portfolio.realized_pnl_usd)}`, `Cash     ${usd(s.portfolio.cash_usd)}`);
  } else if (id === "security_analyst" && s?.last_cycle) {
    const c = s.last_cycle.counts;
    out.push("", `PASS ${c.PASS}  REVIEW ${c.REVIEW_REQUIRED}`, `BLOCKED ${c.BLOCKED}  UNKNOWN ${c.UNKNOWN}`);
  } else if (id === "ceo" && s?.last_cycle) {
    out.push("", `Siklus #${s.last_cycle.cycle_id}: ${s.last_cycle.status}`, `Order simulasi: ${s.last_cycle.orders}`);
  } else if (id === "trader" && s?.rules) {
    out.push("", `Stop-loss ${s.rules.stop_loss_pct}%`, `Maks posisi ${s.rules.max_position_pct}%`);
  } else if (id === "research_analyst" && s?.last_cycle) {
    out.push("", `Kandidat diperiksa: ${s.last_cycle.scanned}`);
  }
  return out;
}

export default function Station({ def, robot, state, online, phase }: {
  def: StationDef; robot: RobotData | undefined; state: OfficeState | null; online: boolean; phase: number;
}) {
  const visual: Visual = online && robot ? robot.state : "OFFLINE";
  const color = STATE_COLOR[visual];
  const accent = ACCENT[def.id] ?? "#ffffff";
  const lines = screenLines(def.id, robot, state, online);
  const title = `${ROBOT_TITLE[def.id]} · ${STATE_LABEL_ID[visual]}`;
  const scale = def.big ? 1.25 : 1;

  return (
    <group position={[def.x, 0, def.z]} scale={scale}>
      <Robot visual={visual} phase={phase} accent={accent} />
      {/* desk, monitor and keyboard */}
      <mesh position={[0, 0.97, 0.95]} receiveShadow><boxGeometry args={[1.7, 0.06, 0.9]} /><meshStandardMaterial color="#1a2236" metalness={0.3} roughness={0.5} /></mesh>
      {[-0.75, 0.75].map((x) => (
        <mesh key={x} position={[x, 0.47, 0.95]}><boxGeometry args={[0.06, 0.94, 0.8]} /><meshStandardMaterial color="#2a3550" /></mesh>
      ))}
      <mesh position={[0, 1.02, 0.62]}><boxGeometry args={[0.6, 0.02, 0.2]} /><meshStandardMaterial color="#10141f" emissive={color} emissiveIntensity={motionFor(visual).typing > 0 ? 0.5 : 0.1} /></mesh>
      <group position={[0, 1.0, 1.2]} rotation={[-0.1, 0, 0]}>
        <mesh position={[0, 0.32, 0]}><boxGeometry args={[0.9, 0.52, 0.04]} /><meshStandardMaterial color="#0d111c" emissive={color} emissiveIntensity={0.35} /></mesh>
        <mesh position={[0, 0.05, 0]}><boxGeometry args={[0.12, 0.1, 0.1]} /><meshStandardMaterial color="#2a3550" /></mesh>
      </group>
      {/* wall screen behind the robot, readable from the camera */}
      <Screen lines={lines} title={title} color={color} active={online && motionFor(visual).screenActive}
              size={def.big ? [3.1, 1.74] : [2.2, 1.24]} position={[0, def.big ? 2.9 : 2.5, -1.35]} />
      <mesh position={[0, def.big ? 2.9 : 2.5, -1.4]}><boxGeometry args={[def.big ? 3.2 : 2.3, def.big ? 1.84 : 1.34, 0.05]} /><meshStandardMaterial color="#0b0f1a" /></mesh>
      <pointLight position={[0, 1.8, 0.3]} color={color} intensity={0.8} distance={4} />
    </group>
  );
}
