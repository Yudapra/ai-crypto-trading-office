"""Adapter that lets the trading engine ask the security engine before every BUY."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Callable, Dict, Optional

from trading_engine.orders import GateResult

from ..models.common import Decision, utcnow
from ..security.engine import SecurityEngine, SecurityReport
from .robots import RobotRegistry, RobotState


class EngineSecurityGate:
    """Implements trading_engine.orders.SecurityGate.

    Evidence is fetched fresh on EVERY call (no caching), so each BUY is
    re-evaluated against current data. Any provider failure -> UNKNOWN -> blocked.
    """

    def __init__(self, provider, engine: SecurityEngine, robots: Optional[RobotRegistry] = None,
                 clock: Callable[[], datetime] = utcnow):
        self._provider = provider
        self._engine = engine
        self._robots = robots
        self._clock = clock
        self.latest: Dict[str, SecurityReport] = {}

    def assess(self, mint: str, order_usd: Decimal) -> SecurityReport:
        if self._robots:
            self._robots.update("security_analyst", RobotState.ANALYZING, f"checking {mint}")
        try:
            evidence = self._provider.get_evidence(mint, order_usd)
            report = self._engine.assess(evidence, self._clock())
        except Exception:
            if self._robots:
                self._robots.update("security_analyst", RobotState.ERROR, f"provider unavailable for {mint}")
            raise
        self.latest[mint] = report
        if self._robots:
            state = RobotState.COMPLETED if report.decision == Decision.PASS else RobotState.BLOCKED
            self._robots.update("security_analyst", state, f"{mint}: {report.decision.value}")
        return report

    def evaluate_buy(self, mint: str, order_usd: Decimal) -> GateResult:
        try:
            report = self.assess(mint, order_usd)
        except Exception:
            return GateResult(False, "UNKNOWN", ("security data provider unavailable",), "UNKNOWN")
        return GateResult(report.buy_allowed, report.decision.value, report.reasons, report.data_label.value)
