"""pump.fun coin lookup (UNOFFICIAL, opt-in via ALLOW_UNOFFICIAL_PUMPFUN=true).

pump.fun does not publish a stable public API; this uses the endpoint its own website uses
(GET {base}/coins/<mint>), which can change or block at any time. Only `creator` and `complete` are read.
Developer balance is NOT taken from pump.fun: it is read on-chain from the creator's token accounts.
Tested against recorded-shape fixtures only.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from .base import ProviderUnavailable
from .http import JsonHttp, ProviderNotFound


@dataclass(frozen=True)
class PumpCoin:
    creator: str
    complete: bool  # True = bonding curve finished (token migrated to an AMM)


class PumpfunApi:
    name = "pumpfun"

    def __init__(self, base: str, http: JsonHttp, timeout: float = 10.0):
        self._base, self._http, self._timeout = base.rstrip("/"), http, timeout

    def coin(self, mint: str) -> Optional[PumpCoin]:
        """None when pump.fun does not know the mint (not a pump.fun token)."""
        try:
            data = self._http.get_json(f"{self._base}/coins/{mint}", timeout=self._timeout)
        except ProviderNotFound:
            return None
        if not isinstance(data, dict) or not isinstance(data.get("creator"), str) or not data["creator"] \
                or not isinstance(data.get("complete"), bool):
            raise ProviderUnavailable("unexpected pump.fun response")
        return PumpCoin(data["creator"], data["complete"])
