"""Raydium public API (v3) adapter: pool lookup and USD price for a token mint.

Endpoints used (public Raydium API v3, no key):
  GET {base}/pools/info/mint?mint1=<mint>&poolType=all&poolSortField=liquidity&sortType=desc&pageSize=5&page=1
  GET {base}/mint/price?mints=<mint>
Tested against recorded-shape fixtures only; the development sandbox could not reach api-v3.raydium.io.
Anything unexpected becomes ProviderUnavailable (the security engine then reports UNKNOWN).

Not provided, and reported as unknown/unverified rather than guessed: LP lock status, recent liquidity removal.
Price impact is only estimated for constant-product pools (amm_v4, cpmm) from TVL; for CLMM it is unknown.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Optional, Tuple
from urllib.parse import quote, urlencode

from ..liquidity_analysis.raydium import LpLockEvidence, PoolInfo
from ..models.common import DataLabel, utcnow
from .base import ProviderUnavailable
from .http import JsonHttp

AMM_V4 = "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8"
CPMM = "CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C"
CLMM = "CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK"
_TYPE_BY_PROGRAM = {AMM_V4: "amm_v4", CPMM: "cpmm", CLMM: "clmm"}


def _dec(value: Any) -> Optional[Decimal]:
    if isinstance(value, bool) or value is None:
        return None
    try:
        d = Decimal(str(value))
    except InvalidOperation:
        return None
    return d if d.is_finite() and d >= 0 else None


class RaydiumApi:
    name = "raydium"

    def __init__(self, base: str, http: JsonHttp, clock: Callable[[], datetime] = utcnow, timeout: float = 10.0):
        self._base, self._http, self._clock, self._timeout = base.rstrip("/"), http, clock, timeout

    def _get(self, path: str, params: dict) -> Any:
        resp = self._http.get_json(f"{self._base}{path}?{urlencode(params, quote_via=quote)}", timeout=self._timeout)
        if not isinstance(resp, dict) or resp.get("success") is not True or not isinstance(resp.get("data"), (dict, list)):
            raise ProviderUnavailable("unexpected Raydium response")
        return resp["data"]

    def price_usd(self, mint: str) -> Decimal:
        data = self._get("/mint/price", {"mints": mint})
        price = _dec(data.get(mint)) if isinstance(data, dict) else None
        if price is None or price <= 0:
            raise ProviderUnavailable("Raydium has no price for this mint")
        return price

    def best_pool(self, mint: str, order_usd: Decimal) -> Optional[PoolInfo]:
        """Deepest pool containing the mint, or None when Raydium lists no pool."""
        data = self._get("/pools/info/mint", {"mint1": mint, "poolType": "all", "poolSortField": "liquidity",
                                               "sortType": "desc", "pageSize": 5, "page": 1})
        rows = data.get("data") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            raise ProviderUnavailable("unexpected Raydium pool list")
        if not rows:
            return None
        best: Optional[Tuple[Decimal, dict]] = None
        for row in rows:
            tvl = _dec(row.get("tvl")) if isinstance(row, dict) else None
            ids = isinstance(row, dict) and isinstance(row.get("id"), str) and isinstance(row.get("programId"), str)
            if tvl is not None and ids and (best is None or tvl > best[0]):
                best = (tvl, row)
        if best is None:
            raise ProviderUnavailable("unexpected Raydium pool entry")
        tvl, row = best
        ptype = _TYPE_BY_PROGRAM.get(row["programId"], "unknown")
        impact: Optional[Decimal] = None
        if ptype in ("amm_v4", "cpmm") and tvl > 0:
            reserve = tvl / 2  # one side of a balanced constant-product pool, in USD
            impact = (order_usd / (reserve + order_usd) * 100).quantize(Decimal("0.0001"))
        return PoolInfo(row["id"], row["programId"], ptype, tvl, impact, None,
                        LpLockEvidence(claimed=None, verification=None), self._clock(), self.name, DataLabel.LIVE)
