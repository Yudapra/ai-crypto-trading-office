"use client";
import { ROBOT_TITLE } from "./layout3d";
import { STATE_COLOR, STATE_LABEL_ID, visualState } from "@/lib/motion";
import { clock, usd } from "@/lib/format";
import type { OfficeData } from "@/lib/useOffice";
import type { RobotId } from "@/lib/types";

export function RobotList({ data }: { data: OfficeData }) {
  const robots = data.state?.robots ?? [];
  if (!robots.length) return <p className="muted">Menunggu data dari backend…</p>;
  return (
    <ul className="list">
      {robots.map((r) => {
        const v = visualState(r.state, data.online);
        return (
          <li key={r.id} className="row">
            <span className="dot" style={{ background: STATE_COLOR[v] }} />
            <div className="grow">
              <div className="rowTop"><b>{ROBOT_TITLE[r.id as RobotId] ?? r.name}</b>
                <span className="chip" style={{ borderColor: STATE_COLOR[v], color: STATE_COLOR[v] }}>{STATE_LABEL_ID[v]}</span></div>
              <div className="muted small">{r.detail}</div>
            </div>
          </li>
        );
      })}
    </ul>
  );
}

export function ActivityFeed({ data }: { data: OfficeData }) {
  const items = [...data.events].reverse().slice(0, 40);
  if (!items.length) return <p className="muted">Belum ada aktivitas. Siklus berjalan sesuai jadwal backend (CYCLE_INTERVAL_S).</p>;
  return (
    <ul className="list">
      {items.map((e) => (
        <li key={e.seq} className="row">
          <span className="time">{clock(e.ts)}</span>
          <div className="grow small"><b>{ROBOT_TITLE[e.robot_id as RobotId] ?? e.robot_id}</b> · {e.message}</div>
        </li>
      ))}
    </ul>
  );
}

export function Portfolio({ data }: { data: OfficeData }) {
  const s = data.state;
  if (!s?.portfolio) return <p className="muted">Menunggu data…</p>;
  const p = s.portfolio, c = s.last_cycle?.counts;
  const neg = (v: string) => Number(v) < 0;
  return (
    <div>
      <div className="grid">
        <div className="stat"><span className="muted small">Equity (simulasi)</span><b>{usd(p.equity_usd)}</b></div>
        <div className="stat"><span className="muted small">Kas</span><b>{usd(p.cash_usd)}</b></div>
        <div className="stat"><span className="muted small">PnL terealisasi</span><b className={neg(p.realized_pnl_usd) ? "neg" : "pos"}>{usd(p.realized_pnl_usd)}</b></div>
        <div className="stat"><span className="muted small">PnL belum terealisasi</span><b className={neg(p.unrealized_pnl_usd) ? "neg" : "pos"}>{usd(p.unrealized_pnl_usd)}</b></div>
      </div>
      {s.last_cycle && c && (
        <p className="small">Siklus #{s.last_cycle.cycle_id} ({s.last_cycle.status}): {s.last_cycle.scanned} diperiksa —
          PASS {c.PASS}, REVIEW {c.REVIEW_REQUIRED}, BLOCKED {c.BLOCKED}, UNKNOWN {c.UNKNOWN}; order simulasi {s.last_cycle.orders}.</p>
      )}
      {s.rules && (
        <p className="muted small">Aturan aktif: top-10 holder ≤ {s.rules.max_top10_holder_pct}% · developer &gt; {s.rules.dev_block_pct}% diblokir ·
          likuiditas min ${s.rules.min_liquidity_usd} · stop-loss {s.rules.stop_loss_pct}%.</p>
      )}
    </div>
  );
}
