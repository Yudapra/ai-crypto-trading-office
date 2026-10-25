"""Robot status registry.

A robot's state changes ONLY when the matching backend service calls `update`
after real work started/finished. The UI must show these values as-is; a robot
never reports COMPLETED unless a service recorded it.

Listeners (see `subscribe`) are told about every change so the activity feed can record it.
"""
from __future__ import annotations

import threading
from datetime import datetime
from enum import Enum
from typing import Callable, Dict, List

from ..models.common import utcnow


class RobotState(str, Enum):
    IDLE = "IDLE"
    WORKING = "WORKING"
    ANALYZING = "ANALYZING"
    BLOCKED = "BLOCKED"
    ERROR = "ERROR"
    COMPLETED = "COMPLETED"


ROBOTS = {
    "ceo": "CEO",
    "research_analyst": "Research Analyst",
    "market_analyst": "Market Analyst",
    "security_analyst": "Security Analyst",
    "trader": "Trader",
    "accountant": "Accountant",
}


class RobotRegistry:
    def __init__(self, clock: Callable[[], datetime] = utcnow):
        self._clock = clock
        self._lock = threading.Lock()
        self._listeners: List[Callable[[str, RobotState, str], None]] = []
        self._state: Dict[str, dict] = {
            k: {"id": k, "name": v, "state": RobotState.IDLE, "detail": "no activity yet",
                "updated_at": clock()} for k, v in ROBOTS.items()}

    def subscribe(self, fn: Callable[[str, RobotState, str], None]) -> None:
        """Register a listener called as fn(robot_id, state, detail) after every update."""
        self._listeners.append(fn)

    def update(self, robot_id: str, state: RobotState, detail: str) -> None:
        if robot_id not in ROBOTS:
            raise KeyError(robot_id)
        with self._lock:
            self._state[robot_id].update(state=state, detail=detail, updated_at=self._clock())
        for fn in list(self._listeners):
            try:
                fn(robot_id, state, detail)
            except Exception:  # a broken listener must never break the work being reported
                pass

    def snapshot(self) -> List[dict]:
        with self._lock:
            return [{**s, "state": s["state"].value, "updated_at": s["updated_at"].isoformat()}
                    for s in self._state.values()]
