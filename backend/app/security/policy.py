"""Configurable security policy.

These thresholds are POLICIES, not universal indicators of fraud. Defaults are
deliberately conservative: anything unknown blocks a BUY.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Optional

from ..models.common import DataLabel, effective_label


@dataclass(frozen=True)
class SecurityPolicy:
    # Mandatory top-10 holder rule. Passes when top-10 <= max_top10_pct.
    max_top10_pct: Decimal = Decimal("25")
    # Which figure the mandatory rule is applied to: "raw" (default, conservative)
    # or "adjusted" (excludes protocol accounts that have VERIFIED evidence).
    # Both figures are always reported.
    concentration_basis: str = "raw"
    max_data_age_s: int = 300
    # Mock data is never treated as production data unless explicitly allowed.
    allow_mock_data: bool = False

    block_on_mint_authority: bool = True
    block_on_freeze_authority: bool = True
    max_transfer_fee_bps: int = 500

    # Developer holdings: < warn passes, warn..block (inclusive) = review, > block = blocked.
    dev_warn_pct: Decimal = Decimal("5")
    dev_block_pct: Decimal = Decimal("10")

    min_liquidity_usd: Decimal = Decimal("10000")
    max_price_impact_pct: Decimal = Decimal("5")
    max_liquidity_removed_pct: Decimal = Decimal("20")
    require_verified_lock: bool = False

    # Related-wallet cluster policy (applies to direct-evidence clusters only).
    cluster_max_pct: Decimal = Decimal("35")
    cluster_policy: str = "review"  # "review" or "block"

    # By default only PASS permits a BUY.
    allow_buy_on_review: bool = False

    def __post_init__(self) -> None:
        if self.concentration_basis not in ("raw", "adjusted"):
            raise ValueError("concentration_basis must be 'raw' or 'adjusted'")
        if self.cluster_policy not in ("review", "block"):
            raise ValueError("cluster_policy must be 'review' or 'block'")
        for name in ("max_top10_pct", "dev_warn_pct", "dev_block_pct", "max_price_impact_pct",
                     "max_liquidity_removed_pct", "cluster_max_pct"):
            v = getattr(self, name)
            if not isinstance(v, Decimal) or not v.is_finite() or v < 0 or v > 100:
                raise ValueError(f"{name} must be a finite Decimal between 0 and 100")
        if self.dev_warn_pct > self.dev_block_pct:
            raise ValueError("dev_warn_pct must be <= dev_block_pct")
        if self.max_data_age_s <= 0:
            raise ValueError("max_data_age_s must be positive")


def unusable_reason(
    policy: SecurityPolicy,
    label: DataLabel,
    ts: Optional[datetime],
    now: datetime,
) -> Optional[str]:
    """Return why a piece of evidence cannot be used, or None when it is usable."""
    if label == DataLabel.MOCK and not policy.allow_mock_data:
        return "data is MOCK and mock data is not allowed by policy"
    if label == DataLabel.UNKNOWN:
        return "data provenance is UNKNOWN"
    if effective_label(label, ts, now, policy.max_data_age_s) == DataLabel.STALE:
        return "data is stale or has no valid timestamp"
    return None
