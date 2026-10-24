"""Mandatory top-10 holder concentration rule.

METHODOLOGY (documented, deterministic)
1. Evidence must be usable: present, fresh, not unlabelled, not MOCK (unless the
   policy allows it), flagged `complete` by the provider, with a positive supply.
2. Token accounts are de-duplicated by token-account address. The same address
   listed twice with identical data is counted once (noted); conflicting data
   makes the snapshot UNKNOWN. Balances may never sum to more than the supply.
3. Balances are aggregated per OWNER, so several token accounts controlled by
   one owner cannot hide a large holder.
4. RAW top-10 = the ten largest owners, nothing excluded.
5. ADJUSTED top-10 = same, but owners that are protocol-controlled
   (pool vault, bonding curve, burn, treasury) are excluded ONLY when every one
   of their accounts carries explicit `kind_evidence`. Every exclusion is listed
   with its reason. Unverified protocol accounts are never excluded.
6. The rule is applied to the figure chosen by `policy.concentration_basis`
   ("raw" by default). Both figures are always reported.
7. Comparison uses exact rational arithmetic, so exactly 25% passes and
   25.0001% does not.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from fractions import Fraction
from typing import Dict, List, Optional, Tuple

from ..models.common import CheckResult, DataLabel, Finding, Severity, effective_label
from ..security.policy import SecurityPolicy, unusable_reason

PROTOCOL_KINDS = frozenset({"pool_vault", "bonding_curve", "burn", "treasury"})
VALID_KINDS = PROTOCOL_KINDS | {"wallet", "unknown"}


@dataclass(frozen=True)
class HolderAccount:
    address: str  # token account address
    owner: str  # owner wallet or program; used for aggregation
    balance: int  # raw token units
    kind: str = "wallet"
    kind_evidence: Optional[str] = None


@dataclass(frozen=True)
class HolderSnapshot:
    mint: str
    total_supply: int
    accounts: Tuple[HolderAccount, ...]
    fetched_at: Optional[datetime]
    provider: str
    label: DataLabel
    # Provider asserts the list contains every account that could rank in the
    # top 10 owners. If it cannot assert this, the snapshot is incomplete.
    complete: bool = False


@dataclass(frozen=True)
class Exclusion:
    owner: str
    balance: int
    kind: str
    reason: str


@dataclass(frozen=True)
class ConcentrationReport:
    result: CheckResult
    reason: str
    threshold_pct: Decimal
    basis: str
    raw_top10_pct: Optional[Decimal] = None
    adjusted_top10_pct: Optional[Decimal] = None
    top10_raw: Tuple[Tuple[str, int], ...] = ()
    exclusions: Tuple[Exclusion, ...] = ()
    notes: Tuple[str, ...] = ()
    data_label: DataLabel = DataLabel.UNKNOWN
    fetched_at: Optional[datetime] = None
    owners: Tuple[Tuple[str, int], ...] = ()  # all aggregated owners, for cluster analysis
    total_supply: int = 0

    def finding(self) -> Finding:
        severity = Severity.INFO if self.result == CheckResult.PASS else Severity.CRITICAL
        parts = [self.reason]
        if self.raw_top10_pct is not None:
            parts.append(f"raw top-10 = {self.raw_top10_pct}%")
        if self.adjusted_top10_pct is not None:
            parts.append(f"adjusted top-10 = {self.adjusted_top10_pct}%")
        parts.append(f"threshold = {self.threshold_pct}% (basis: {self.basis})")
        if self.exclusions:
            parts.append("exclusions: " + "; ".join(
                f"{e.owner} ({e.kind}, {e.balance}): {e.reason}" for e in self.exclusions))
        if self.notes:
            parts.append("notes: " + "; ".join(self.notes))
        return Finding(
            check="holder_concentration",
            result=self.result,
            severity=severity,
            evidence=" | ".join(parts),
            data_timestamp=self.fetched_at,
            limitations=("Based on one provider snapshot; ownership by different wallets of the "
                         "same person cannot be proven from balances alone."),
        )


class _Unusable(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _pct(numerator: int, supply: int) -> Decimal:
    return (Decimal(numerator) * 100 / Decimal(supply)).quantize(Decimal("0.0001"))


def _unknown(policy: SecurityPolicy, reason: str, snap: Optional[HolderSnapshot], now: datetime,
             notes: Tuple[str, ...] = ()) -> ConcentrationReport:
    label = DataLabel.UNKNOWN
    if snap is not None:
        label = effective_label(snap.label, snap.fetched_at, now, policy.max_data_age_s)
    return ConcentrationReport(
        result=CheckResult.UNKNOWN, reason=reason, threshold_pct=policy.max_top10_pct,
        basis=policy.concentration_basis, data_label=label,
        fetched_at=snap.fetched_at if snap else None, notes=notes,
        total_supply=snap.total_supply if snap else 0)


def _dedupe(snap: HolderSnapshot) -> Tuple[List[HolderAccount], List[str]]:
    seen: Dict[str, HolderAccount] = {}
    notes: List[str] = []
    for acc in snap.accounts:
        if not isinstance(acc.balance, int) or isinstance(acc.balance, bool) or acc.balance < 0:
            raise _Unusable(f"invalid balance for account {acc.address}")
        if not acc.address or not acc.owner:
            raise _Unusable("account without address or owner")
        if acc.kind not in VALID_KINDS:
            raise _Unusable(f"invalid account kind '{acc.kind}'")
        prev = seen.get(acc.address)
        if prev is None:
            seen[acc.address] = acc
        elif prev == acc:
            notes.append(f"duplicate listing of {acc.address} counted once")
        else:
            raise _Unusable(f"conflicting data for token account {acc.address}")
    return list(seen.values()), notes


def _aggregate(accounts: List[HolderAccount]) -> Dict[str, dict]:
    owners: Dict[str, dict] = {}
    for acc in accounts:
        o = owners.setdefault(acc.owner, {"balance": 0, "kinds": set(), "verified": True, "evidence": []})
        o["balance"] += acc.balance
        o["kinds"].add(acc.kind)
        if not acc.kind_evidence:
            o["verified"] = False
        else:
            o["evidence"].append(acc.kind_evidence)
    return owners


def _rank(items: List[Tuple[str, int]]) -> List[Tuple[str, int]]:
    # Deterministic: balance descending, then owner address.
    return sorted(items, key=lambda kv: (-kv[1], kv[0]))


def analyze_holder_concentration(
    snapshot: Optional[HolderSnapshot],
    policy: SecurityPolicy,
    now: datetime,
) -> ConcentrationReport:
    if snapshot is None:
        return _unknown(policy, "no holder data available", None, now)
    bad = unusable_reason(policy, snapshot.label, snapshot.fetched_at, now)
    if bad:
        return _unknown(policy, f"holder {bad}", snapshot, now)
    if not isinstance(snapshot.total_supply, int) or snapshot.total_supply <= 0:
        return _unknown(policy, "total supply missing or not positive", snapshot, now)
    if not snapshot.complete:
        return _unknown(policy, "holder list is incomplete (provider did not assert completeness)",
                        snapshot, now)
    if not snapshot.accounts:
        return _unknown(policy, "holder list is empty", snapshot, now)

    try:
        accounts, notes = _dedupe(snapshot)
    except _Unusable as exc:
        return _unknown(policy, exc.reason, snapshot, now)

    supply = snapshot.total_supply
    if sum(a.balance for a in accounts) > supply:
        return _unknown(policy, "listed balances exceed total supply (inconsistent or double-counted data)",
                        snapshot, now, tuple(notes))

    owners = _aggregate(accounts)
    ranked_all = _rank([(o, d["balance"]) for o, d in owners.items()])
    top10 = ranked_all[:10]
    raw_sum = sum(b for _, b in top10)

    exclusions: List[Exclusion] = []
    kept: List[Tuple[str, int]] = []
    for owner, bal in ranked_all:
        d = owners[owner]
        kinds = d["kinds"]
        if len(kinds) > 1:
            notes.append(f"owner {owner} has mixed account kinds {sorted(kinds)}; counted as ordinary holder")
            kept.append((owner, bal))
            continue
        kind = next(iter(kinds))
        if kind in PROTOCOL_KINDS:
            if d["verified"]:
                exclusions.append(Exclusion(owner, bal, kind, "verified protocol-controlled account: "
                                            + "; ".join(d["evidence"])))
                continue
            notes.append(f"owner {owner} looks like {kind} but has no evidence; NOT excluded")
        kept.append((owner, bal))
    adj_sum = sum(b for _, b in kept[:10])

    raw_pct = _pct(raw_sum, supply)
    adj_pct = _pct(adj_sum, supply)
    basis_sum = raw_sum if policy.concentration_basis == "raw" else adj_sum
    limit = Fraction(policy.max_top10_pct)
    passes = Fraction(basis_sum * 100, supply) <= limit

    if passes:
        result, reason = CheckResult.PASS, "top-10 concentration within limit"
    else:
        result, reason = CheckResult.FAIL, "top-10 concentration ABOVE limit"
    if policy.concentration_basis == "raw" and not passes and Fraction(adj_sum * 100, supply) <= limit:
        notes.append("adjusted figure is within the limit, but the policy basis is 'raw'")

    return ConcentrationReport(
        result=result, reason=reason, threshold_pct=policy.max_top10_pct,
        basis=policy.concentration_basis, raw_top10_pct=raw_pct, adjusted_top10_pct=adj_pct,
        top10_raw=tuple(top10), exclusions=tuple(exclusions), notes=tuple(notes),
        data_label=effective_label(snapshot.label, snapshot.fetched_at, now, policy.max_data_age_s),
        fetched_at=snapshot.fetched_at, owners=tuple(ranked_all), total_supply=supply)
