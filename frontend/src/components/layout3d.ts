import type { RobotId } from "@/lib/types";

export interface Station { id: RobotId; x: number; z: number; big?: boolean }

// Back row: research, market, security. Front row: trader, accountant. CEO oversees from the far end.
export const STATIONS: Station[] = [
  { id: "ceo", x: 0, z: -5.4, big: true },
  { id: "research_analyst", x: -3.2, z: -1.0 },
  { id: "market_analyst", x: 0, z: -1.0 },
  { id: "security_analyst", x: 3.2, z: -1.0 },
  { id: "trader", x: -1.7, z: 3.2 },
  { id: "accountant", x: 1.7, z: 3.2 },
];

// Work flows along this pipeline; a completed step sends a data packet to the next robot.
export const NEXT_IN_PIPELINE: Record<RobotId, RobotId> = {
  research_analyst: "market_analyst", market_analyst: "security_analyst", security_analyst: "trader",
  trader: "accountant", accountant: "ceo", ceo: "research_analyst",
};

export const ROBOT_TITLE: Record<RobotId, string> = {
  ceo: "CEO", research_analyst: "Research Analyst", market_analyst: "Market Analyst",
  security_analyst: "Security Analyst", trader: "Trader", accountant: "Accountant",
};
