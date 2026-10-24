"""SQLite persistence: idempotency records, trade history, portfolio state, audit trail."""
from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import fields
from datetime import datetime
from decimal import Decimal
from typing import List, Optional, Tuple

from .orders.models import OrderResult, OrderStatus

_DECIMAL_FIELDS = {"fill_price", "quantity", "gross_usd", "fee_usd", "realized_pnl_usd"}


def result_to_json(r: OrderResult) -> str:
    d = {}
    for f in fields(r):
        v = getattr(r, f.name)
        if isinstance(v, Decimal):
            v = str(v)
        elif isinstance(v, datetime):
            v = v.isoformat()
        elif isinstance(v, OrderStatus):
            v = v.value
        elif isinstance(v, tuple):
            v = list(v)
        d[f.name] = v
    return json.dumps(d, sort_keys=True)


def result_from_json(s: str) -> OrderResult:
    d = json.loads(s)
    for k in _DECIMAL_FIELDS:
        if d.get(k) is not None:
            d[k] = Decimal(d[k])
    if d.get("executed_at"):
        d["executed_at"] = datetime.fromisoformat(d["executed_at"])
    d["status"] = OrderStatus(d["status"])
    d["reasons"] = tuple(d.get("reasons", ()))
    return OrderResult(**d)


class SqliteStore:
    def __init__(self, path: str = ":memory:"):
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.RLock()
        with self._lock:
            self._db.executescript("""
            CREATE TABLE IF NOT EXISTS orders(
                client_order_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, result TEXT NOT NULL, ts TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS trades(
                id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, client_order_id TEXT NOT NULL,
                mint TEXT NOT NULL, side TEXT NOT NULL, quantity TEXT NOT NULL, price TEXT NOT NULL,
                gross_usd TEXT NOT NULL, fee_usd TEXT NOT NULL, realized_pnl TEXT, data_label TEXT NOT NULL,
                simulated INTEGER NOT NULL DEFAULT 1);
            CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS audit(
                id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, event TEXT NOT NULL,
                payload TEXT NOT NULL, prev_hash TEXT NOT NULL, hash TEXT NOT NULL);
            """)
            self._db.commit()

    # orders / idempotency
    def get_order(self, client_order_id: str) -> Optional[Tuple[str, OrderResult]]:
        with self._lock:
            row = self._db.execute("SELECT fingerprint, result FROM orders WHERE client_order_id=?",
                                   (client_order_id,)).fetchone()
        return None if row is None else (row[0], result_from_json(row[1]))

    def put_order(self, client_order_id: str, fingerprint: str, result: OrderResult, ts: datetime) -> None:
        with self._lock:
            self._db.execute("INSERT OR IGNORE INTO orders VALUES (?,?,?,?)",
                             (client_order_id, fingerprint, result_to_json(result), ts.isoformat()))

    def add_trade(self, ts: datetime, r: OrderResult) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO trades(ts,client_order_id,mint,side,quantity,price,gross_usd,fee_usd,"
                "realized_pnl,data_label,simulated) VALUES (?,?,?,?,?,?,?,?,?,?,1)",
                (ts.isoformat(), r.client_order_id, r.mint, r.side, str(r.quantity), str(r.fill_price),
                 str(r.gross_usd), str(r.fee_usd),
                 None if r.realized_pnl_usd is None else str(r.realized_pnl_usd), r.data_label))

    def list_trades(self, limit: int = 100) -> List[dict]:
        with self._lock:
            cur = self._db.execute("SELECT ts,client_order_id,mint,side,quantity,price,gross_usd,fee_usd,"
                                   "realized_pnl,data_label,simulated FROM trades ORDER BY id DESC LIMIT ?",
                                   (max(1, min(limit, 1000)),))
            cols = [c[0] for c in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]

    # state
    def save_state(self, key: str, value: dict) -> None:
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO state VALUES (?,?)", (key, json.dumps(value)))

    def load_state(self, key: str) -> Optional[dict]:
        with self._lock:
            row = self._db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return None if row is None else json.loads(row[0])

    def commit(self) -> None:
        with self._lock:
            self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.commit()
            self._db.close()

    # audit (hash-chained; written by AuditLog)
    def last_audit_hash(self) -> str:
        with self._lock:
            row = self._db.execute("SELECT hash FROM audit ORDER BY id DESC LIMIT 1").fetchone()
        return row[0] if row else "GENESIS"

    def add_audit_row(self, ts: str, event: str, payload: str, prev_hash: str, h: str) -> None:
        with self._lock:
            self._db.execute("INSERT INTO audit(ts,event,payload,prev_hash,hash) VALUES (?,?,?,?,?)",
                             (ts, event, payload, prev_hash, h))

    def audit_rows(self, limit: Optional[int] = None) -> List[dict]:
        q = "SELECT id,ts,event,payload,prev_hash,hash FROM audit ORDER BY id " + ("DESC LIMIT ?" if limit else "ASC")
        with self._lock:
            cur = self._db.execute(q, (max(1, min(limit, 1000)),) if limit else ())
            cols = [c[0] for c in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
