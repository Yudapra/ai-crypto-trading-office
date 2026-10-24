"""Append-only, hash-chained audit log. Sensitive keys are redacted before storage."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Any, Callable, Dict, List

_SENSITIVE = re.compile(r"(secret|password|private|seed|mnemonic|authorization|api[_-]?key|bearer|auth[_-]?token)",
                        re.IGNORECASE)


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: ("[REDACTED]" if _SENSITIVE.search(str(k)) else redact(v)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    return value


def _hash(prev: str, ts: str, event: str, payload: str) -> str:
    return hashlib.sha256(f"{prev}|{ts}|{event}|{payload}".encode()).hexdigest()


class AuditLog:
    def __init__(self, store, clock: Callable[[], datetime]):
        self._store = store
        self._clock = clock
        self._listeners: List[Callable[[str, dict], None]] = []

    def subscribe(self, fn: Callable[[str, dict], None]) -> None:
        self._listeners.append(fn)

    def append(self, event: str, payload: Dict[str, Any]) -> None:
        clean = redact(payload)
        body = json.dumps(clean, sort_keys=True, default=str)
        ts = self._clock().isoformat()
        prev = self._store.last_audit_hash()
        self._store.add_audit_row(ts, event, body, prev, _hash(prev, ts, event, body))
        for fn in self._listeners:
            try:
                fn(event, clean)
            except Exception:  # listeners must never break trading
                pass

    def verify_chain(self) -> bool:
        prev = "GENESIS"
        for row in self._store.audit_rows():
            if row["prev_hash"] != prev or row["hash"] != _hash(prev, row["ts"], row["event"], row["payload"]):
                return False
            prev = row["hash"]
        return True

    def recent(self, limit: int = 100) -> List[dict]:
        rows = self._store.audit_rows(limit)
        for r in rows:
            r["payload"] = json.loads(r["payload"])
        return rows
