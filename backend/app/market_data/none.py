"""Provider used when DATA_PROVIDER=none: it has no data, so every evaluation is UNKNOWN and no BUY can happen."""
from __future__ import annotations

from decimal import Decimal
from typing import List

from .base import ProviderUnavailable


class NullMarketData:
    name = "none"

    def list_candidates(self) -> List[str]:
        return []

    def get_quote(self, mint: str):
        raise ProviderUnavailable("no data provider configured (DATA_PROVIDER=none)")

    def get_evidence(self, mint: str, order_usd: Decimal):
        raise ProviderUnavailable("no data provider configured (DATA_PROVIDER=none)")
