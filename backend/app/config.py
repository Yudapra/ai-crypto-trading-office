"""Environment configuration with validation. Secrets are never printed or logged."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Mapping, Optional, Tuple
from urllib.parse import urlparse

from trading_engine.mode import assert_paper_mode
from trading_engine.paper_broker import BrokerConfig
from trading_engine.risk import RiskConfig

from .security.policy import SecurityPolicy

_BASE58 = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
_HEADER_NAME = re.compile(r"^[A-Za-z0-9-]{1,64}$")
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


class ConfigError(ValueError):
    pass


def _dec(env: Mapping[str, str], key: str, default: str) -> Decimal:
    raw = env.get(key, default)
    try:
        v = Decimal(raw)
    except InvalidOperation:
        raise ConfigError(f"{key} must be a number")
    if not v.is_finite():
        raise ConfigError(f"{key} must be finite")
    return v


def _bool(env: Mapping[str, str], key: str, default: bool) -> bool:
    raw = env.get(key)
    if raw is None:
        return default
    if raw.lower() in ("1", "true", "yes"):
        return True
    if raw.lower() in ("0", "false", "no"):
        return False
    raise ConfigError(f"{key} must be true or false")


def _url(env: Mapping[str, str], key: str, default: Optional[str] = None, required: bool = False) -> Optional[str]:
    raw = (env.get(key) or default or "").strip()
    if not raw:
        if required:
            raise ConfigError(f"{key} is required")
        return None
    parts = urlparse(raw)
    host = parts.hostname or ""
    if not host or parts.scheme not in ("https", "http"):
        raise ConfigError(f"{key} must be an http(s) URL")
    if parts.scheme == "http" and host not in _LOCAL_HOSTS:
        raise ConfigError(f"{key} must use https (plain http is only allowed for localhost)")
    return raw.rstrip("/")


@dataclass(frozen=True)
class LiveSettings:
    """Settings for DATA_PROVIDER=live. URLs and keys are hidden from repr (RPC URLs often embed keys)."""

    solana_rpc_url: str = field(repr=False)
    watchlist: Tuple[str, ...] = ()
    raydium_base: str = "https://api-v3.raydium.io"
    gmgn_base: Optional[str] = field(default=None, repr=False)
    gmgn_api_key: Optional[str] = field(default=None, repr=False)
    gmgn_auth_header: Optional[str] = None
    pumpfun_enabled: bool = False
    pumpfun_base: str = "https://frontend-api-v3.pump.fun"


@dataclass(frozen=True)
class Settings:
    trading_mode: str
    api_key: Optional[str] = field(default=None, repr=False)  # never shown in repr/logs
    allow_mock_data: bool = False
    data_provider: str = "none"
    database_path: str = "trading.db"
    policy: SecurityPolicy = field(default_factory=SecurityPolicy)
    risk: RiskConfig = field(default_factory=RiskConfig)
    broker: BrokerConfig = field(default_factory=BrokerConfig)
    cycle_interval_s: float = 0.0  # 0 = no background cycles (cycles only via the API)
    auto_buy_enabled: bool = False  # automatic SIMULATED buys are opt-in
    auto_buy_usd: Decimal = Decimal("25")
    cors_origins: Tuple[str, ...] = ()
    live: Optional[LiveSettings] = None

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "Settings":
        mode = assert_paper_mode(env.get("TRADING_MODE", "paper"))  # raises if not 'paper'
        provider = env.get("DATA_PROVIDER", "none")
        if provider not in ("none", "mock", "live"):
            raise ConfigError("DATA_PROVIDER must be 'none', 'mock' or 'live'")
        allow_mock = _bool(env, "ALLOW_MOCK_DATA", False)
        if provider == "mock" and not allow_mock:
            raise ConfigError("DATA_PROVIDER=mock requires ALLOW_MOCK_DATA=true so mock data is never "
                              "silently treated as production data")
        if provider == "live" and allow_mock:
            raise ConfigError("ALLOW_MOCK_DATA must be false when DATA_PROVIDER=live")
        api_key = env.get("API_KEY") or None
        if api_key is not None and len(api_key) < 24:
            raise ConfigError("API_KEY must be at least 24 characters")
        try:
            policy = SecurityPolicy(
                max_top10_pct=_dec(env, "MAX_TOP10_HOLDER_PCT", "25"),
                dev_warn_pct=_dec(env, "DEV_WARN_PCT", "5"),
                dev_block_pct=_dec(env, "DEV_BLOCK_PCT", "10"),
                concentration_basis=env.get("CONCENTRATION_BASIS", "raw"),
                allow_mock_data=allow_mock)
            risk = RiskConfig(
                max_position_pct=_dec(env, "MAX_POSITION_PCT", "5"),
                stop_loss_pct=_dec(env, "STOP_LOSS_PCT", "8"),
                max_daily_loss_pct=_dec(env, "MAX_DAILY_LOSS_PCT", "10"))
            broker = BrokerConfig(
                starting_balance_usd=_dec(env, "PAPER_STARTING_BALANCE_USD", "1000"),
                allow_mock_data=allow_mock)
        except ValueError as exc:
            raise ConfigError(str(exc))

        interval = _dec(env, "CYCLE_INTERVAL_S", "0")
        if interval != 0 and interval < 10:
            raise ConfigError("CYCLE_INTERVAL_S must be 0 (disabled) or at least 10")
        auto_usd = _dec(env, "AUTO_BUY_USD", "25")
        if auto_usd <= 0:
            raise ConfigError("AUTO_BUY_USD must be positive")
        origins = tuple(o.strip().rstrip("/") for o in env.get("CORS_ORIGINS", "").split(",") if o.strip())
        if "*" in origins:
            raise ConfigError("CORS_ORIGINS must list explicit origins, not '*'")

        live = cls._live_from_env(env) if provider == "live" else None
        return cls(mode, api_key, allow_mock, provider, env.get("DATABASE_PATH", "trading.db"),
                   policy, risk, broker, float(interval), _bool(env, "AUTO_BUY_ENABLED", False), auto_usd,
                   origins, live)

    @staticmethod
    def _live_from_env(env: Mapping[str, str]) -> LiveSettings:
        rpc = _url(env, "SOLANA_RPC_URL", required=True)
        assert rpc is not None
        watch = tuple(m.strip() for m in env.get("WATCHLIST_MINTS", "").split(",") if m.strip())
        for m in watch:
            if not _BASE58.match(m):
                raise ConfigError("WATCHLIST_MINTS must be comma-separated Solana token addresses")
        gmgn_key = env.get("GMGN_API_KEY") or None
        gmgn_base = _url(env, "GMGN_BASE_URL")
        gmgn_header = env.get("GMGN_AUTH_HEADER") or None
        if gmgn_key:
            # Endpoint host and header name could not be verified against live GMGN docs when this
            # integration was written, so they must be configured explicitly from GMGN's documentation.
            if not gmgn_base or not gmgn_header:
                raise ConfigError("GMGN_API_KEY requires GMGN_BASE_URL and GMGN_AUTH_HEADER "
                                  "(copy them from GMGN's official API documentation)")
            if not _HEADER_NAME.match(gmgn_header):
                raise ConfigError("GMGN_AUTH_HEADER is not a valid HTTP header name")
        elif gmgn_base or gmgn_header:
            raise ConfigError("GMGN_BASE_URL/GMGN_AUTH_HEADER are set but GMGN_API_KEY is empty")
        return LiveSettings(
            solana_rpc_url=rpc, watchlist=watch,
            raydium_base=_url(env, "RAYDIUM_API_BASE", "https://api-v3.raydium.io") or "",
            gmgn_base=gmgn_base, gmgn_api_key=gmgn_key, gmgn_auth_header=gmgn_header,
            pumpfun_enabled=_bool(env, "ALLOW_UNOFFICIAL_PUMPFUN", False),
            pumpfun_base=_url(env, "PUMPFUN_API_BASE", "https://frontend-api-v3.pump.fun") or "")
