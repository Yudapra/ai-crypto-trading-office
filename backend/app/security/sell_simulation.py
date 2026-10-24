"""Sell-simulation check. A failed simulation blocks; no simulation = UNKNOWN."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from ..models.common import CheckResult, DataLabel, Finding, Severity
from .policy import SecurityPolicy, unusable_reason

LIMITS = ("A passing simulation reflects one point in time and one route; it does not prove "
          "the token can always be sold or that liquidity will remain.")


@dataclass(frozen=True)
class SellSimulation:
    status: str  # "success" | "failed" | "unavailable"
    detail: str
    simulated_at: Optional[datetime]
    provider: str
    label: DataLabel


def check_sell_simulation(sim: Optional[SellSimulation], policy: SecurityPolicy, now: datetime) -> Finding:
    if sim is None:
        return Finding("sell_simulation", CheckResult.UNKNOWN, Severity.CRITICAL,
                       "no sell simulation available; sellability not evaluated", None, LIMITS)
    bad = unusable_reason(policy, sim.label, sim.simulated_at, now)
    if bad:
        return Finding("sell_simulation", CheckResult.UNKNOWN, Severity.CRITICAL,
                       f"sell simulation unusable: {bad}", sim.simulated_at, LIMITS)
    if sim.status == "success":
        return Finding("sell_simulation", CheckResult.PASS, Severity.INFO,
                       f"simulated sell succeeded: {sim.detail}", sim.simulated_at, LIMITS)
    if sim.status == "failed":
        return Finding("sell_simulation", CheckResult.FAIL, Severity.CRITICAL,
                       f"simulated sell FAILED: {sim.detail}", sim.simulated_at, LIMITS)
    return Finding("sell_simulation", CheckResult.UNKNOWN, Severity.CRITICAL,
                   f"sell simulation unavailable: {sim.detail}", sim.simulated_at, LIMITS)
