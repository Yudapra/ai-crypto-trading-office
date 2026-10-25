export type RobotState = "IDLE" | "WORKING" | "ANALYZING" | "BLOCKED" | "ERROR" | "COMPLETED";
export const ROBOT_STATES: readonly RobotState[] = ["IDLE", "WORKING", "ANALYZING", "BLOCKED", "ERROR", "COMPLETED"];

export type RobotId = "ceo" | "research_analyst" | "market_analyst" | "security_analyst" | "trader" | "accountant";
export const ROBOT_IDS: readonly RobotId[] = [
  "ceo", "research_analyst", "market_analyst", "security_analyst", "trader", "accountant",
];

export interface Robot {
  id: RobotId;
  name: string;
  state: RobotState;
  detail: string;
  updated_at: string;
}

export interface ActivityEvent {
  seq: number;
  ts: string;
  robot_id: string; // a RobotId or "system"
  kind: string;
  state: string | null;
  message: string;
}

export interface Portfolio {
  simulated: boolean;
  cash_usd: string;
  equity_usd: string;
  realized_pnl_usd: string;
  unrealized_pnl_usd: string;
}

export interface CycleCounts {
  PASS: number;
  REVIEW_REQUIRED: number;
  BLOCKED: number;
  UNKNOWN: number;
}

export interface LastCycle {
  cycle_id: number;
  status: string;
  scanned: number;
  counts: CycleCounts;
  orders: number;
  finished_at: string | null;
}

export interface Rules {
  max_top10_holder_pct: string;
  dev_block_pct: string;
  min_liquidity_usd: string;
  max_position_pct: string;
  stop_loss_pct: string;
}

export interface OfficeState {
  server_time: string;
  trading_mode: string;
  live_trading: boolean;
  data_provider: string;
  mock_data_in_use: boolean;
  robots: Robot[];
  portfolio: Portfolio | null;
  last_cycle: LastCycle | null;
  rules: Rules | null;
  latest_seq: number;
}
