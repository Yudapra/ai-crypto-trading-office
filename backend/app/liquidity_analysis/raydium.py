"""Raydium-style pool liquidity checks.

Pools differ (AMM v4 and CPMM use LP tokens; CLMM uses position NFTs). Nothing
here assumes a conventional LP token. A pool is reported LOCKED only when the
lock is VERIFIED with a method and evidence; a provider's or marketing claim is
never enough.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import List, Optional

from ..models.common import CheckResult, DataLabel, Finding, Severity
from ..security.policy import SecurityPolicy, unusable_reason

SUPPORTED_POOL_TYPES = frozenset({"amm_v4", "cpmm", "clmm"})
LIMITS = ("Pool data is a snapshot; liquidity can be withdrawn later unless withdrawal rights are "
          "verifiably restricted.")


class LockStatus(str, Enum):
    LOCKED = "LOCKED"
    NOT_LOCKED = "NOT_LOCKED"
    UNVERIFIED = "UNVERIFIED"


@dataclass(frozen=True)
class LpLockEvidence:
    claimed: Optional[bool]  # what a provider/creator CLAIMS (never trusted alone)
    verification: Optional[bool]  # True = verified locked, False = verified unlocked, None = unverified
    method: Optional[str] = None  # e.g. "lp_burn", "locker_contract", "clmm_position_lock"
    evidence: Optional[str] = None  # e.g. transaction signature or account address


@dataclass(frozen=True)
class PoolInfo:
    pool_id: str
    program_id: str
    pool_type: str  # amm_v4 | cpmm | clmm | unknown
    liquidity_usd: Optional[Decimal]
    price_impact_pct: Optional[Decimal]  # for the intended order size
    liquidity_removed_pct_recent: Optional[Decimal]
    lock: Optional[LpLockEvidence]
    fetched_at: Optional[datetime]
    provider: str
    label: DataLabel


def lock_status(lock: Optional[LpLockEvidence]) -> LockStatus:
    if lock is None:
        return LockStatus.UNVERIFIED
    if lock.verification is True and lock.method and lock.evidence:
        return LockStatus.LOCKED
    if lock.verification is False and lock.evidence:
        return LockStatus.NOT_LOCKED
    return LockStatus.UNVERIFIED  # includes "claimed locked" without verification


def check_liquidity(pool: Optional[PoolInfo], policy: SecurityPolicy, now: datetime) -> List[Finding]:
    if pool is None:
        return [Finding("liquidity", CheckResult.UNKNOWN, Severity.CRITICAL,
                        "no pool data available; liquidity safety cannot be verified", None, LIMITS)]
    ts = pool.fetched_at
    bad = unusable_reason(policy, pool.label, ts, now)
    if bad:
        return [Finding("liquidity", CheckResult.UNKNOWN, Severity.CRITICAL,
                        f"pool data unusable: {bad}", ts, LIMITS)]
    if pool.pool_type not in SUPPORTED_POOL_TYPES:
        return [Finding("liquidity", CheckResult.UNKNOWN, Severity.CRITICAL,
                        f"unsupported or unknown pool type {pool.pool_type!r}", ts, LIMITS)]

    out: List[Finding] = []
    if pool.liquidity_usd is None:
        out.append(Finding("liquidity_depth", CheckResult.UNKNOWN, Severity.CRITICAL,
                           "liquidity depth unknown", ts, LIMITS))
    elif pool.liquidity_usd < policy.min_liquidity_usd:
        out.append(Finding("liquidity_depth", CheckResult.FAIL, Severity.CRITICAL,
                           f"liquidity ${pool.liquidity_usd} below minimum ${policy.min_liquidity_usd}", ts, LIMITS))
    else:
        out.append(Finding("liquidity_depth", CheckResult.PASS, Severity.INFO,
                           f"liquidity ${pool.liquidity_usd}", ts, LIMITS))

    if pool.price_impact_pct is None:
        out.append(Finding("price_impact", CheckResult.UNKNOWN, Severity.CRITICAL,
                           "price impact unknown", ts, LIMITS))
    elif pool.price_impact_pct > policy.max_price_impact_pct:
        out.append(Finding("price_impact", CheckResult.FAIL, Severity.CRITICAL,
                           f"price impact {pool.price_impact_pct}% above {policy.max_price_impact_pct}%", ts, LIMITS))
    else:
        out.append(Finding("price_impact", CheckResult.PASS, Severity.INFO,
                           f"price impact {pool.price_impact_pct}%", ts, LIMITS))

    removed = pool.liquidity_removed_pct_recent
    if removed is None:
        out.append(Finding("liquidity_removal", CheckResult.UNKNOWN, Severity.CRITICAL,
                           "recent liquidity removal unknown", ts, LIMITS))
    elif removed > policy.max_liquidity_removed_pct:
        out.append(Finding("liquidity_removal", CheckResult.FAIL, Severity.CRITICAL,
                           f"{removed}% of liquidity removed recently (limit {policy.max_liquidity_removed_pct}%)",
                           ts, LIMITS))
    else:
        out.append(Finding("liquidity_removal", CheckResult.PASS, Severity.INFO,
                           f"{removed}% of liquidity removed recently", ts, LIMITS))

    status = lock_status(pool.lock)
    claimed = pool.lock.claimed if pool.lock else None
    note = " (a lock was claimed but is NOT verified)" if claimed and status == LockStatus.UNVERIFIED else ""
    if status == LockStatus.LOCKED:
        out.append(Finding("lp_lock", CheckResult.PASS, Severity.INFO,
                           f"liquidity LOCKED, verified via {pool.lock.method}: {pool.lock.evidence}", ts, LIMITS))
    elif status == LockStatus.NOT_LOCKED:
        out.append(Finding("lp_lock", CheckResult.FAIL if policy.require_verified_lock else CheckResult.WARN,
                           Severity.CRITICAL if policy.require_verified_lock else Severity.WARNING,
                           f"liquidity verified NOT locked: {pool.lock.evidence}", ts, LIMITS))
    else:
        out.append(Finding("lp_lock", CheckResult.UNKNOWN if policy.require_verified_lock else CheckResult.WARN,
                           Severity.CRITICAL if policy.require_verified_lock else Severity.WARNING,
                           f"lock status UNVERIFIED{note}", ts, LIMITS))
    return out
