"""Provider interface. Real providers (GMGN, Solana RPC, Raydium, pump.fun) must implement this.

No real provider is implemented yet: API availability and authentication must be
verified first. Providers must never invent data; if data is unavailable they
raise ProviderUnavailable (the security engine then reports UNKNOWN).
"""
from __future__ import annotations

from decimal import Decimal
from typing import List, Protocol

from ..security.engine import CandidateEvidence


class ProviderUnavailable(Exception):
    """The provider could not supply data (outage, rate limit, no access)."""


class MarketDataProvider(Protocol):
    name: str

    def list_candidates(self) -> List[str]: ...

    def get_evidence(self, mint: str, order_usd: Decimal) -> CandidateEvidence: ...

    def get_quote(self, mint: str): ...  # returns trading_engine.orders.Quote
