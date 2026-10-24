"""Environment configuration with validation. Secrets are never printed or logged."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Mapping, Optional

from trading_engine.mode import assert_paper_mode
from trading_engine.paper_broker import BrokerConfig
from trading_engine.risk import RiskConfig

from .security.policy import SecurityPolicy


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

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "Settings":
        mode = assert_paper_mode(env.get("TRADING_MODE", "paper"))  # raises if not 'paper'
        provider = env.get("DATA_PROVIDER", "none")
        if provider not in ("none", "mock"):
            raise ConfigError("DATA_PROVIDER must be 'none' or 'mock' (no real provider is implemented yet)")
        allow_mock = _bool(env, "ALLOW_MOCK_DATA", False)
        if provider == "mock" and not allow_mock:
            raise ConfigError("DATA_PROVIDER=mock requires ALLOW_MOCK_DATA=true so mock data is never "
                              "silently treated as production data")
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
        return cls(mode, api_key, allow_mock, provider, env.get("DATABASE_PATH", "trading.db"),
                   policy, risk, broker)
