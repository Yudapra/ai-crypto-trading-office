import type {
  ActivityEvent, CycleCounts, LastCycle, OfficeState, Portfolio, Robot, RobotId, RobotState, Rules,
} from "./types.ts";

// The frontend only READS public, simulated data. It holds no API key: endpoints that do work
// (scan / cycle / orders) require a key that exists only on the backend host.

const STATES = ["IDLE", "WORKING", "ANALYZING", "BLOCKED", "ERROR", "COMPLETED"];
const IDS = ["ceo", "research_analyst", "market_analyst", "security_analyst", "trader", "accountant"];

type Json = Record<string, unknown>;
const isObj = (v: unknown): v is Json => typeof v === "object" && v !== null && !Array.isArray(v);
const str = (v: unknown, fallback = ""): string => (typeof v === "string" ? v : fallback);
const num = (v: unknown, fallback = 0): number => (typeof v === "number" && Number.isFinite(v) ? v : fallback);

export function parseRobots(raw: unknown): Robot[] {
  if (!Array.isArray(raw)) return [];
  const out: Robot[] = [];
  for (const r of raw) {
    if (!isObj(r) || !IDS.includes(str(r.id)) || !STATES.includes(str(r.state))) continue; // never guess a state
    out.push({
      id: r.id as RobotId, name: str(r.name, str(r.id)), state: r.state as RobotState,
      detail: str(r.detail), updated_at: str(r.updated_at),
    });
  }
  return out;
}

function parsePortfolio(raw: unknown): Portfolio | null {
  if (!isObj(raw)) return null;
  return {
    simulated: raw.simulated === true, cash_usd: str(raw.cash_usd, "0"), equity_usd: str(raw.equity_usd, "0"),
    realized_pnl_usd: str(raw.realized_pnl_usd, "0"), unrealized_pnl_usd: str(raw.unrealized_pnl_usd, "0"),
  };
}

function parseCycle(raw: unknown): LastCycle | null {
  if (!isObj(raw)) return null;
  const c = isObj(raw.counts) ? raw.counts : {};
  const counts: CycleCounts = {
    PASS: num(c.PASS), REVIEW_REQUIRED: num(c.REVIEW_REQUIRED), BLOCKED: num(c.BLOCKED), UNKNOWN: num(c.UNKNOWN),
  };
  return {
    cycle_id: num(raw.cycle_id), status: str(raw.status), scanned: num(raw.scanned), counts,
    orders: Array.isArray(raw.orders) ? raw.orders.length : 0,
    finished_at: typeof raw.finished_at === "string" ? raw.finished_at : null,
  };
}

function parseRules(raw: unknown): Rules | null {
  if (!isObj(raw)) return null;
  return {
    max_top10_holder_pct: str(raw.max_top10_holder_pct), dev_block_pct: str(raw.dev_block_pct),
    min_liquidity_usd: str(raw.min_liquidity_usd), max_position_pct: str(raw.max_position_pct),
    stop_loss_pct: str(raw.stop_loss_pct),
  };
}

/** Validates the /api/state payload. Returns null when it is not recognisable (the UI then shows OFFLINE). */
export function parseState(raw: unknown): OfficeState | null {
  if (!isObj(raw)) return null;
  const robots = parseRobots(raw.robots);
  if (robots.length === 0) return null;
  return {
    server_time: str(raw.server_time), trading_mode: str(raw.trading_mode, "paper"),
    live_trading: raw.live_trading === true, data_provider: str(raw.data_provider, "unknown"),
    mock_data_in_use: raw.mock_data_in_use === true, robots, portfolio: parsePortfolio(raw.portfolio),
    last_cycle: parseCycle(raw.last_cycle), rules: parseRules(raw.rules), latest_seq: num(raw.latest_seq),
  };
}

export function parseEvents(raw: unknown): { events: ActivityEvent[]; latest_seq: number } {
  if (!isObj(raw) || !Array.isArray(raw.events)) return { events: [], latest_seq: 0 };
  const events: ActivityEvent[] = [];
  for (const e of raw.events) {
    if (!isObj(e) || typeof e.seq !== "number") continue;
    events.push({
      seq: e.seq, ts: str(e.ts), robot_id: str(e.robot_id, "system"), kind: str(e.kind),
      state: typeof e.state === "string" ? e.state : null, message: str(e.message),
    });
  }
  return { events, latest_seq: num(raw.latest_seq) };
}

export type Fetcher = (url: string, init?: { signal?: AbortSignal }) => Promise<{ ok: boolean; json(): Promise<unknown> }>;

export class OfficeApi {
  private base: string;
  private fetcher: Fetcher;

  constructor(base: string, fetcher: Fetcher) {
    this.base = base;
    this.fetcher = fetcher;
  }

  private async get(path: string, signal?: AbortSignal): Promise<unknown> {
    const res = await this.fetcher(`${this.base.replace(/\/+$/, "")}${path}`, { signal });
    if (!res.ok) throw new Error(`HTTP error`);
    return res.json();
  }

  async state(signal?: AbortSignal): Promise<OfficeState> {
    const s = parseState(await this.get("/api/state", signal));
    if (!s) throw new Error("unrecognised state payload");
    return s;
  }

  async events(since: number, signal?: AbortSignal) {
    return parseEvents(await this.get(`/api/events?since=${Math.max(0, Math.floor(since))}&limit=100`, signal));
  }
}
