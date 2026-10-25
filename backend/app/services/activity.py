"""In-memory activity feed.

Every entry is recorded by backend code AFTER something really happened (a robot changed state,
a security decision was made, an order was filled or rejected). The frontend animates from this
feed and from `RobotRegistry.snapshot()`, never from anything it invents itself.

The feed is a bounded ring buffer: old entries are dropped, sequence numbers keep increasing.
Persistent records live in the hash-chained audit log of the trading engine, not here.
"""
from __future__ import annotations

import json
import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Deque, Dict, List, Optional

from trading_engine.audit import redact

from ..models.common import utcnow

MAX_MESSAGE_CHARS = 300
KINDS = frozenset({"state", "cycle", "decision", "order", "error", "info"})


def _json_safe(data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Redact secret-looking keys and force everything into plain JSON types."""
    if not data:
        return {}
    return json.loads(json.dumps(redact(data), default=str))


@dataclass(frozen=True)
class ActivityEvent:
    seq: int
    ts: datetime
    robot_id: str  # one of the six robot ids, or "system"
    kind: str
    message: str
    state: Optional[str] = None
    data: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {"seq": self.seq, "ts": self.ts.isoformat(), "robot_id": self.robot_id, "kind": self.kind,
                "state": self.state, "message": self.message, "data": self.data}


class ActivityLog:
    def __init__(self, clock: Callable[[], datetime] = utcnow, capacity: int = 500):
        if capacity < 1:
            raise ValueError("capacity must be positive")
        self._clock = clock
        self._lock = threading.Lock()
        self._events: Deque[ActivityEvent] = deque(maxlen=capacity)
        self._seq = 0

    def record(self, robot_id: str, kind: str, message: str, state: Optional[str] = None,
               data: Optional[Dict[str, Any]] = None) -> ActivityEvent:
        if kind not in KINDS:
            raise ValueError(f"unknown activity kind: {kind}")
        text = " ".join(str(message).split())[:MAX_MESSAGE_CHARS]
        safe = _json_safe(data)
        with self._lock:
            self._seq += 1
            ev = ActivityEvent(self._seq, self._clock(), robot_id, kind, text, state, safe)
            self._events.append(ev)
        return ev

    def latest_seq(self) -> int:
        with self._lock:
            return self._seq

    def since(self, seq: int = 0, limit: int = 200) -> List[Dict[str, Any]]:
        """Events with a sequence number greater than `seq`, oldest first (at most `limit`)."""
        limit = max(1, min(int(limit), 500))
        with self._lock:
            return [e.to_dict() for e in self._events if e.seq > seq][:limit]
