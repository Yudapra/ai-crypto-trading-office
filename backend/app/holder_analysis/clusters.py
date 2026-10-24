"""Related-wallet (cluster) analysis.

Direct evidence (shared funder, direct transfer) forms clusters. Inferred
relationships are reported separately and NEVER merged into a cluster. This
check supplements, and never replaces, the raw top-10 rule.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from fractions import Fraction
from typing import Dict, List, Optional, Sequence, Tuple

from ..models.common import CheckResult, Finding, Severity
from ..security.policy import SecurityPolicy

DIRECT_KINDS = frozenset({"shared_funder", "direct_transfer"})


@dataclass(frozen=True)
class WalletLink:
    wallet_a: str
    wallet_b: str
    kind: str  # shared_funder | direct_transfer | inferred
    evidence: str = ""


@dataclass(frozen=True)
class Cluster:
    wallets: Tuple[str, ...]
    balance: int
    pct: Decimal


def _find(parent: Dict[str, str], x: str) -> str:
    while parent[x] != x:
        parent[x] = parent[parent[x]]
        x = parent[x]
    return x


def analyze_clusters(
    owners: Sequence[Tuple[str, int]],
    total_supply: int,
    links: Optional[Sequence[WalletLink]],
    policy: SecurityPolicy,
    data_timestamp: Optional[datetime],
) -> Finding:
    limitations = ("Wallet relationships are inferred from on-chain links and cannot prove common "
                   "ownership. Only direct-evidence links form clusters.")
    if links is None or total_supply <= 0:
        return Finding("related_wallet_clusters", CheckResult.UNKNOWN, Severity.INFO,
                       "no wallet-relationship data supplied; cluster analysis not performed",
                       data_timestamp, limitations)

    balances = dict(owners)
    parent: Dict[str, str] = {}
    inferred: List[WalletLink] = []
    for link in links:
        if link.kind == "inferred":
            inferred.append(link)
            continue
        if link.kind not in DIRECT_KINDS or not link.evidence:
            inferred.append(link)  # direct kinds without evidence are not trusted
            continue
        for w in (link.wallet_a, link.wallet_b):
            parent.setdefault(w, w)
        ra, rb = _find(parent, link.wallet_a), _find(parent, link.wallet_b)
        if ra != rb:
            parent[ra] = rb

    groups: Dict[str, List[str]] = {}
    for w in parent:
        groups.setdefault(_find(parent, w), []).append(w)

    clusters: List[Cluster] = []
    for members in groups.values():
        bal = sum(balances.get(w, 0) for w in members)
        if len(members) >= 2 and bal > 0:
            pct = (Decimal(bal) * 100 / Decimal(total_supply)).quantize(Decimal("0.0001"))
            clusters.append(Cluster(tuple(sorted(members)), bal, pct))
    clusters.sort(key=lambda c: -c.balance)

    inferred_txt = f"{len(inferred)} inferred/unverified link(s) reported separately, not clustered"
    if not clusters:
        return Finding("related_wallet_clusters", CheckResult.PASS, Severity.INFO,
                       f"no direct-evidence cluster with holdings; {inferred_txt}",
                       data_timestamp, limitations)

    biggest = clusters[0]
    over = Fraction(biggest.balance * 100, total_supply) > Fraction(policy.cluster_max_pct)
    desc = f"largest direct-evidence cluster: {len(biggest.wallets)} wallets hold {biggest.pct}% ; {inferred_txt}"
    if over:
        block = policy.cluster_policy == "block"
        return Finding("related_wallet_clusters", CheckResult.FAIL if block else CheckResult.WARN,
                       Severity.CRITICAL if block else Severity.WARNING,
                       f"{desc}; exceeds cluster limit {policy.cluster_max_pct}%",
                       data_timestamp, limitations)
    return Finding("related_wallet_clusters", CheckResult.PASS, Severity.INFO, desc, data_timestamp, limitations)
