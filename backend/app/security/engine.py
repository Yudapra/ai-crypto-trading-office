"""Unified, deterministic security decision.

Decision rules (no AI component can override these):
  * any CRITICAL failure                  -> BLOCKED
  * else any CRITICAL evidence UNKNOWN    -> UNKNOWN   (blocks a BUY)
  * else any warning                      -> REVIEW_REQUIRED
  * else                                  -> PASS (only when every required check ran and passed)
Only PASS allows a BUY unless the policy explicitly allows buying on review.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, List, Optional, Sequence, Tuple

from ..holder_analysis.clusters import WalletLink, analyze_clusters
from ..holder_analysis.concentration import (
    ConcentrationReport,
    HolderSnapshot,
    analyze_holder_concentration,
)
from ..liquidity_analysis.raydium import PoolInfo, check_liquidity
from ..models.common import CheckResult, DataLabel, Decision, Finding, Severity, utcnow
from .policy import SecurityPolicy
from .pumpfun import PumpfunInfo, check_developer_holdings
from .sell_simulation import SellSimulation, check_sell_simulation
from .token_checks import TokenInfo, check_token


@dataclass(frozen=True)
class CandidateEvidence:
    mint: str
    venue: str  # "raydium" | "pumpfun"
    token: Optional[TokenInfo] = None
    holders: Optional[HolderSnapshot] = None
    sell_sim: Optional[SellSimulation] = None
    pool: Optional[PoolInfo] = None
    pumpfun: Optional[PumpfunInfo] = None
    wallet_links: Optional[Tuple[WalletLink, ...]] = None

    def labels(self) -> List[DataLabel]:
        parts = [self.token, self.holders, self.sell_sim, self.pool, self.pumpfun]
        return [p.label for p in parts if p is not None]


@dataclass(frozen=True)
class SecurityReport:
    mint: str
    decision: Decision
    findings: Tuple[Finding, ...]
    reasons: Tuple[str, ...]
    evaluated_at: datetime
    data_label: DataLabel
    concentration: Optional[ConcentrationReport]
    buy_allowed: bool

    def to_dict(self) -> dict:
        c = self.concentration
        return {
            "mint": self.mint,
            "decision": self.decision.value,
            "buy_allowed": self.buy_allowed,
            "data_label": self.data_label.value,
            "evaluated_at": self.evaluated_at.isoformat(),
            "reasons": list(self.reasons),
            "findings": [f.to_dict() for f in self.findings],
            "holder_concentration": None if c is None else {
                "result": c.result.value,
                "raw_top10_pct": None if c.raw_top10_pct is None else str(c.raw_top10_pct),
                "adjusted_top10_pct": None if c.adjusted_top10_pct is None else str(c.adjusted_top10_pct),
                "threshold_pct": str(c.threshold_pct),
                "basis": c.basis,
                "exclusions": [e.__dict__ for e in c.exclusions],
                "notes": list(c.notes),
            },
        }


class SecurityEngine:
    def __init__(self, policy: Optional[SecurityPolicy] = None):
        self.policy = policy or SecurityPolicy()

    @staticmethod
    def _safe(name: str, fn: Callable[[], Sequence[Finding]]) -> List[Finding]:
        try:
            return list(fn())
        except Exception:  # never leak internals; unknown is the safe answer
            return [Finding(name, CheckResult.UNKNOWN, Severity.CRITICAL,
                            "internal error while evaluating this check", None,
                            "The check could not be completed; treated as UNKNOWN.")]

    def assess(self, ev: CandidateEvidence, now: Optional[datetime] = None) -> SecurityReport:
        now = now or utcnow()
        p = self.policy
        findings: List[Finding] = []
        conc_holder: List[ConcentrationReport] = []

        findings += self._safe("token_authorities", lambda: check_token(ev.token, p, now))

        def holders() -> List[Finding]:
            rep = analyze_holder_concentration(ev.holders, p, now)
            conc_holder.append(rep)
            return [rep.finding()]
        findings += self._safe("holder_concentration", holders)

        findings += self._safe("sell_simulation", lambda: [check_sell_simulation(ev.sell_sim, p, now)])

        if ev.venue == "pumpfun" or ev.pumpfun is not None:
            findings += self._safe("developer_holdings",
                                   lambda: [check_developer_holdings(ev.pumpfun, p, now)])

        pf = ev.pumpfun
        if ev.venue == "pumpfun" and ev.pool is None and pf is not None and pf.migrated is False:
            findings.append(Finding(
                "liquidity", CheckResult.WARN, Severity.WARNING,
                "token is still on the pump.fun bonding curve; bonding-curve liquidity is not analyzed "
                "by this engine, only Raydium-type pools are", pf.fetched_at,
                "Manual review required before any BUY."))
        else:
            findings += self._safe("liquidity", lambda: check_liquidity(ev.pool, p, now))

        rep = conc_holder[0] if conc_holder else None
        if rep is not None and rep.owners:
            findings += self._safe("related_wallet_clusters", lambda: [analyze_clusters(
                rep.owners, rep.total_supply, ev.wallet_links, p, rep.fetched_at)])

        decision = self._decide(findings)
        reasons = tuple(f"{f.check}: {f.evidence}" for f in findings
                        if f.result in (CheckResult.FAIL, CheckResult.WARN)
                        or (f.result == CheckResult.UNKNOWN and f.severity == Severity.CRITICAL))
        labels = ev.labels()
        label = DataLabel.MOCK if DataLabel.MOCK in labels else (
            DataLabel.UNKNOWN if (not labels or DataLabel.UNKNOWN in labels) else
            (DataLabel.STALE if DataLabel.STALE in labels else DataLabel.LIVE))
        allowed = decision == Decision.PASS or (decision == Decision.REVIEW_REQUIRED and p.allow_buy_on_review)
        return SecurityReport(ev.mint, decision, tuple(findings), reasons, now, label, rep, allowed)

    @staticmethod
    def _decide(findings: Sequence[Finding]) -> Decision:
        if any(f.result == CheckResult.FAIL and f.severity == Severity.CRITICAL for f in findings):
            return Decision.BLOCKED
        if any(f.result == CheckResult.UNKNOWN and f.severity == Severity.CRITICAL for f in findings):
            return Decision.UNKNOWN
        if any(f.result in (CheckResult.WARN, CheckResult.FAIL) for f in findings):
            return Decision.REVIEW_REQUIRED
        required_present = {f.check for f in findings}
        needed = {"mint_authority", "freeze_authority", "token_extensions", "holder_concentration",
                  "sell_simulation", "liquidity_depth", "price_impact", "liquidity_removal", "lp_lock"}
        if not needed.issubset(required_present):
            return Decision.UNKNOWN
        return Decision.PASS
