"""MOCK data for demos and tests. Everything it produces is labelled MOCK.

MOCK data must never be presented as live market information. Security policy
and broker both reject it unless `allow_mock_data` is explicitly enabled.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from fractions import Fraction
from typing import Callable, Dict, List, Optional, Tuple

from trading_engine.orders import Quote

from ..holder_analysis.concentration import HolderAccount, HolderSnapshot
from ..liquidity_analysis.raydium import LpLockEvidence, PoolInfo
from ..models.common import DataLabel, utcnow
from ..security.engine import CandidateEvidence
from ..security.pumpfun import PumpfunInfo
from ..security.sell_simulation import SellSimulation
from ..security.token_checks import TokenInfo
from .base import ProviderUnavailable

SUPPLY = 1_000_000_000_000


def make_holders(mint: str, top10_pct: Fraction, now: datetime, label: DataLabel,
                 supply: int = SUPPLY, complete: bool = True) -> HolderSnapshot:
    """Ten equal wallets holding `top10_pct` percent in total, plus 20 smaller wallets."""
    top_total = int(Fraction(supply) * top10_pct / 100)
    each = top_total // 10
    accounts = []
    for i in range(10):
        bal = each + (top_total - each * 10 if i == 0 else 0)
        accounts.append(HolderAccount(f"{mint}-ta{i}", f"{mint}-w{i}", bal))
    small = supply // 1000  # 0.1% each, always below the top-10 members for realistic inputs
    for i in range(20):
        accounts.append(HolderAccount(f"{mint}-tb{i}", f"{mint}-s{i}", small))
    return HolderSnapshot(mint, supply, tuple(accounts), now, "mock", label, complete)


def make_evidence(mint: str, now: datetime, *, label: DataLabel = DataLabel.MOCK,
                  top10_pct: Fraction = Fraction(15), venue: str = "raydium",
                  dev_pct: Optional[Fraction] = Fraction(2), sell_status: str = "success",
                  liquidity_usd: Decimal = Decimal("50000"), lock_verified: Optional[bool] = True,
                  mint_authority: Optional[str] = None, holders: Optional[HolderSnapshot] = None,
                  ) -> CandidateEvidence:
    """Build a SAFE-by-default evidence bundle; tests override one field at a time."""
    token = TokenInfo(mint, "spl-token", True, mint_authority, None, None, None, None, now, "mock", label)
    snap = holders if holders is not None else make_holders(mint, top10_pct, now, label)
    lock = LpLockEvidence(claimed=True, verification=lock_verified,
                          method="lp_burn" if lock_verified else None,
                          evidence="mock-burn-signature" if lock_verified else None)
    pool = PoolInfo(f"{mint}-pool", "mock-program", "cpmm", liquidity_usd, Decimal("1.5"), Decimal("0"),
                    lock, now, "mock", label)
    dev_bal = None if dev_pct is None else int(Fraction(SUPPLY) * dev_pct / 100)
    pf = PumpfunInfo(f"{mint}-dev", dev_bal, SUPPLY, True, True, now, "mock", label) if venue == "pumpfun" else None
    return CandidateEvidence(
        mint=mint, venue=venue, token=token, holders=snap,
        sell_sim=SellSimulation(sell_status, "mock simulation", now, "mock", label),
        pool=pool, pumpfun=pf, wallet_links=())


class MockMarketData:
    """Deterministic demo provider with a few clearly named scenarios."""

    name = "mock"

    def __init__(self, clock: Callable[[], datetime] = utcnow):
        self._clock = clock
        self.prices: Dict[str, Decimal] = {
            "MOCKSAFE": Decimal("0.010"), "MOCKCONCENTRATED": Decimal("0.020"),
            "MOCKNOSELL": Decimal("0.005"),
        }
        self.fetch_count = 0

    def list_candidates(self) -> List[str]:
        return list(self.prices)

    def get_quote(self, mint: str) -> Quote:
        if mint not in self.prices:
            raise ProviderUnavailable("unknown mock token")
        return Quote(mint, self.prices[mint], self._clock(), "mock", "MOCK")

    def get_evidence(self, mint: str, order_usd: Decimal) -> CandidateEvidence:
        self.fetch_count += 1
        now = self._clock()
        if mint == "MOCKSAFE":
            return make_evidence(mint, now)
        if mint == "MOCKCONCENTRATED":
            return make_evidence(mint, now, top10_pct=Fraction(251, 10))
        if mint == "MOCKNOSELL":
            return make_evidence(mint, now, sell_status="failed")
        raise ProviderUnavailable("unknown mock token")
