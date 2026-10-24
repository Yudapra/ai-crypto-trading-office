"""pump.fun developer-holding policy (configurable, not a fraud indicator by itself)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from fractions import Fraction
from typing import Optional

from ..models.common import CheckResult, DataLabel, Finding, Severity
from .policy import SecurityPolicy, unusable_reason

LIMITS = ("Developer holdings are a configurable policy signal. The developer may hold tokens through "
          "other wallets that this check cannot see.")


@dataclass(frozen=True)
class PumpfunInfo:
    creator: str
    developer_balance: Optional[int]  # None = unknown
    total_supply: int
    bonding_curve_complete: Optional[bool]
    migrated: Optional[bool]
    fetched_at: Optional[datetime]
    provider: str
    label: DataLabel


def check_developer_holdings(info: Optional[PumpfunInfo], policy: SecurityPolicy, now: datetime) -> Finding:
    if info is None:
        return Finding("developer_holdings", CheckResult.UNKNOWN, Severity.CRITICAL,
                       "no pump.fun/developer data available", None, LIMITS)
    bad = unusable_reason(policy, info.label, info.fetched_at, now)
    if bad:
        return Finding("developer_holdings", CheckResult.UNKNOWN, Severity.CRITICAL,
                       f"developer data unusable: {bad}", info.fetched_at, LIMITS)
    if info.developer_balance is None or info.total_supply <= 0 or info.developer_balance < 0:
        return Finding("developer_holdings", CheckResult.UNKNOWN, Severity.CRITICAL,
                       "developer holdings unknown", info.fetched_at, LIMITS)
    frac = Fraction(info.developer_balance * 100, info.total_supply)
    pct = float(frac)
    if frac > Fraction(policy.dev_block_pct):
        return Finding("developer_holdings", CheckResult.FAIL, Severity.CRITICAL,
                       f"developer holds {pct:.4f}% (> {policy.dev_block_pct}% block threshold)",
                       info.fetched_at, LIMITS)
    if frac >= Fraction(policy.dev_warn_pct):
        return Finding("developer_holdings", CheckResult.WARN, Severity.WARNING,
                       f"developer holds {pct:.4f}% (>= {policy.dev_warn_pct}% warning threshold)",
                       info.fetched_at, LIMITS)
    return Finding("developer_holdings", CheckResult.PASS, Severity.INFO,
                   f"developer holds {pct:.4f}% (< {policy.dev_warn_pct}%)", info.fetched_at, LIMITS)
