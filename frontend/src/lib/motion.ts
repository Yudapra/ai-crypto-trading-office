import type { RobotState } from "./types.ts";

// Pure animation maths. A robot's motion is a function of the state the BACKEND reported (plus whether the
// backend is reachable). Nothing here invents activity: offline => everything idles in grey.

export type Visual = RobotState | "OFFLINE";

export const STATE_COLOR: Record<Visual, string> = {
  IDLE: "#7a8aa6", WORKING: "#37b6ff", ANALYZING: "#b57bff", BLOCKED: "#ffb020",
  ERROR: "#ff4d5e", COMPLETED: "#2fe09b", OFFLINE: "#4a5366",
};

export const STATE_LABEL_ID: Record<Visual, string> = {
  IDLE: "Siaga", WORKING: "Bekerja", ANALYZING: "Menganalisis", BLOCKED: "Diblokir",
  ERROR: "Galat", COMPLETED: "Selesai", OFFLINE: "Terputus",
};

export interface Motion {
  typing: number; // finger/forearm oscillation amplitude (rad)
  typingHz: number;
  headScan: number; // left-right head sweep amplitude (rad)
  headShake: number; // fast no-shake amplitude (rad)
  nod: number; // slow nod amplitude (rad)
  slump: number; // forward lean of torso (rad)
  screenActive: boolean; // screen shows scanning animation
  alert: boolean; // pulsing emissive
}

const BASE: Motion = {
  typing: 0, typingHz: 0, headScan: 0, headShake: 0, nod: 0, slump: 0, screenActive: false, alert: false,
};

export function motionFor(state: Visual): Motion {
  switch (state) {
    case "WORKING": return { ...BASE, typing: 0.22, typingHz: 7, nod: 0.03, screenActive: true };
    case "ANALYZING": return { ...BASE, typing: 0.12, typingHz: 3.5, headScan: 0.28, screenActive: true };
    case "BLOCKED": return { ...BASE, headShake: 0.3, alert: true };
    case "ERROR": return { ...BASE, slump: 0.25, alert: true };
    case "COMPLETED": return { ...BASE, nod: 0.12 };
    case "IDLE":
    case "OFFLINE":
    default: return { ...BASE };
  }
}

export interface Pose {
  leftForearm: number; rightForearm: number; headYaw: number; headPitch: number; torsoPitch: number;
  pulse: number; // 0..1 emissive pulse
}

export function poseAt(m: Motion, t: number, phase = 0): Pose {
  const w = 2 * Math.PI * m.typingHz;
  const left = m.typing * Math.sin(w * t + phase);
  const right = m.typing * Math.sin(w * t + phase + Math.PI * 0.8);
  const yaw = m.headScan * Math.sin(1.1 * t + phase) + m.headShake * Math.sin(9 * t + phase);
  const pitch = m.nod * Math.sin(1.6 * t + phase);
  const breathe = 0.012 * Math.sin(0.9 * t + phase);
  return {
    leftForearm: left, rightForearm: right, headYaw: yaw, headPitch: pitch, torsoPitch: m.slump + breathe,
    pulse: m.alert ? 0.5 + 0.5 * Math.sin(5 * t + phase) : 0,
  };
}

/** Which state to show for a robot: unknown robot or unreachable backend => OFFLINE (never a made-up state). */
export function visualState(state: RobotState | undefined, online: boolean): Visual {
  return online && state ? state : "OFFLINE";
}
