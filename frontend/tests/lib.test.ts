import { test } from "node:test";
import assert from "node:assert/strict";
import { parseEvents, parseRobots, parseState, OfficeApi } from "../src/lib/api.ts";
import { STATE_COLOR, motionFor, poseAt, visualState } from "../src/lib/motion.ts";
import { usd, wrap } from "../src/lib/format.ts";

const robot = (id: string, state: string) => ({ id, name: id, state, detail: "d", updated_at: "t" });
const ids = ["ceo", "research_analyst", "market_analyst", "security_analyst", "trader", "accountant"];

test("parseRobots drops unknown ids and unknown states instead of guessing", () => {
  const out = parseRobots([robot("ceo", "WORKING"), robot("intruder", "WORKING"), robot("trader", "DANCING"), null, 5]);
  assert.deepEqual(out.map((r) => r.id), ["ceo"]);
});

test("parseState returns null for garbage and for a payload without robots", () => {
  assert.equal(parseState(null), null);
  assert.equal(parseState({ robots: [] }), null);
  assert.equal(parseState("x"), null);
});

test("parseState maps the backend payload and never reports live trading unless the backend says so", () => {
  const s = parseState({
    robots: ids.map((i) => robot(i, "IDLE")), trading_mode: "paper", data_provider: "mock", mock_data_in_use: true,
    portfolio: { simulated: true, cash_usd: "1000", equity_usd: "1000", realized_pnl_usd: "0", unrealized_pnl_usd: "0" },
    last_cycle: { cycle_id: 3, status: "completed", scanned: 3, counts: { PASS: 1, BLOCKED: 2 }, orders: [{}], finished_at: null },
    latest_seq: 24,
  });
  assert.ok(s);
  assert.equal(s.robots.length, 6);
  assert.equal(s.live_trading, false);
  assert.equal(s.mock_data_in_use, true);
  assert.equal(s.last_cycle?.orders, 1);
  assert.equal(s.last_cycle?.counts.UNKNOWN, 0);
});

test("parseEvents skips malformed entries", () => {
  const e = parseEvents({ events: [{ seq: 1, ts: "t", robot_id: "ceo", kind: "state", state: "WORKING", message: "m" }, { seq: "x" }, 3], latest_seq: 9 });
  assert.equal(e.events.length, 1);
  assert.equal(e.latest_seq, 9);
});

test("OfficeApi builds URLs, rejects HTTP errors and unrecognised payloads", async () => {
  const urls: string[] = [];
  const good = new OfficeApi("http://x/", async (u) => { urls.push(u); return { ok: true, json: async () => ({ robots: ids.map((i) => robot(i, "IDLE")) }) }; });
  await good.state();
  await good.events(-5.7);
  assert.deepEqual(urls, ["http://x/api/state", "http://x/api/events?since=0&limit=100"]);
  await assert.rejects(new OfficeApi("http://x", async () => ({ ok: false, json: async () => ({}) })).state());
  await assert.rejects(new OfficeApi("http://x", async () => ({ ok: true, json: async () => ({ nope: 1 }) })).state());
});

test("offline or unknown robot shows OFFLINE, never an invented state", () => {
  assert.equal(visualState("WORKING", false), "OFFLINE");
  assert.equal(visualState(undefined, true), "OFFLINE");
  assert.equal(visualState("BLOCKED", true), "BLOCKED");
});

test("idle/offline robots do not type; working robots do; blocked robots shake and alert", () => {
  for (const v of ["IDLE", "OFFLINE", "COMPLETED"] as const) assert.equal(motionFor(v).typing, 0, v);
  assert.ok(motionFor("WORKING").typing > 0 && motionFor("WORKING").screenActive);
  assert.ok(motionFor("ANALYZING").headScan > 0);
  assert.ok(motionFor("BLOCKED").headShake > 0 && motionFor("BLOCKED").alert);
  assert.equal(motionFor("IDLE").screenActive, false);
});

test("pose is zero-motion when idle and bounded by the amplitudes when working", () => {
  const idle = poseAt(motionFor("IDLE"), 3.3);
  assert.equal(Math.abs(idle.leftForearm), 0); assert.equal(Math.abs(idle.headYaw), 0); assert.equal(idle.pulse, 0);
  const m = motionFor("WORKING");
  let maxL = 0;
  for (let t = 0; t < 5; t += 0.01) maxL = Math.max(maxL, Math.abs(poseAt(m, t).leftForearm));
  assert.ok(maxL > 0.2 * 0.95 && maxL <= m.typing + 1e-9);
});

test("every visual state has a colour", () => {
  for (const k of ["IDLE", "WORKING", "ANALYZING", "BLOCKED", "ERROR", "COMPLETED", "OFFLINE"]) assert.match(STATE_COLOR[k as keyof typeof STATE_COLOR], /^#[0-9a-f]{6}$/);
});

test("usd formats decimal strings and tolerates bad input", () => {
  assert.equal(usd("1234.5"), "$1,234.50");
  assert.equal(usd("-3"), "-$3.00");
  assert.equal(usd("abc"), "-");
  assert.equal(usd(undefined), "-");
});

test("wrap limits width and line count", () => {
  const lines = wrap("aaa bbb ccc ddd eee fff ggg", 8, 2);
  assert.ok(lines.length <= 2 && lines.every((l) => l.length <= 8));
});
