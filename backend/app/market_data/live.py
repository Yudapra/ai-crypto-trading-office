"""Live provider: real Solana / Raydium / (optional, unofficial) pump.fun data. Read-only, no wallet.

What is verified from live sources:       token program + authorities + extensions (Solana RPC), pool TVL and
                                          USD price (Raydium), developer balance (RPC, pump.fun creator).
What is NOT available, so stays unknown:  a complete top-10 OWNER list (RPC only sees the 20 largest accounts,
                                          so the snapshot is `complete=False`), a real sell/honeypot simulation,
                                          LP lock verification, recent liquidity removal.
Consequence: every candidate evaluates to UNKNOWN (or BLOCKED when a rule is provably violated) and no BUY
is allowed until a verified holder source and a sell simulation are plugged in. This is intentional.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Callable, List, Optional

from trading_engine.orders import Quote

from ..config import LiveSettings
from ..models.common import DataLabel
from ..security.engine import CandidateEvidence
from ..security.pumpfun import PumpfunInfo
from .base import ProviderUnavailable
from .http import JsonHttp, UrllibJsonHttp
from .pumpfun_api import PumpfunApi
from .raydium_api import RaydiumApi
from .solana_rpc import SolanaRpc


class LiveMarketData:
    name = "live"

    def __init__(self, settings: LiveSettings, clock: Callable[[], datetime], http: Optional[JsonHttp] = None):
        http = http or UrllibJsonHttp()
        self._clock = clock
        self._watch = list(settings.watchlist)
        self.rpc = SolanaRpc(settings.solana_rpc_url, http, clock)
        self.raydium = RaydiumApi(settings.raydium_base, http, clock)
        self.pumpfun: Optional[PumpfunApi] = PumpfunApi(settings.pumpfun_base, http) if settings.pumpfun_enabled else None

    def list_candidates(self) -> List[str]:
        return list(self._watch)  # discovery needs a verified source (GMGN API access); only the watchlist for now

    def get_quote(self, mint: str) -> Quote:
        return Quote(mint, self.raydium.price_usd(mint), self._clock(), self.raydium.name, DataLabel.LIVE.value)

    def get_evidence(self, mint: str, order_usd: Decimal) -> CandidateEvidence:
        token, supply = self.rpc.get_mint(mint)
        holders = self.rpc.largest_holders(mint, supply)  # complete=False by construction
        pool = self.raydium.best_pool(mint, order_usd)
        venue, pf = "raydium", None
        coin = self.pumpfun.coin(mint) if self.pumpfun else None
        if coin is not None:
            venue = "raydium" if coin.complete else "pumpfun"
            try:
                bal: Optional[int] = self.rpc.owner_balance(coin.creator, mint)
            except ProviderUnavailable:
                bal = None  # unknown developer balance -> UNKNOWN in the engine
            pf = PumpfunInfo(coin.creator, bal, supply, coin.complete, coin.complete, self._clock(),
                             "pumpfun+solana-rpc", DataLabel.LIVE)
        # sell_sim=None: no real sell simulation exists yet -> engine reports UNKNOWN and blocks BUY.
        return CandidateEvidence(mint=mint, venue=venue, token=token, holders=holders, sell_sim=None,
                                 pool=pool, pumpfun=pf, wallet_links=None)


def build_live_provider(settings: LiveSettings, clock: Callable[[], datetime],
                        http: Optional[JsonHttp] = None) -> LiveMarketData:
    return LiveMarketData(settings, clock, http)
