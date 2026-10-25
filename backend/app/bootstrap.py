"""Assembles the whole backend from validated settings. Used by the ASGI app and by tests."""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Mapping, Optional

from trading_engine.paper_broker import PaperBroker
from trading_engine.storage import SqliteStore

from .api_core import ApiCore
from .config import Settings
from .market_data.mock import MockMarketData
from .market_data.none import NullMarketData
from .models.common import utcnow
from .security.engine import SecurityEngine
from .services.activity import ActivityLog
from .services.gate import EngineSecurityGate
from .services.office import CycleScheduler, OfficeConfig, OfficeService
from .services.robots import RobotRegistry


@dataclass
class Office:
    settings: Settings
    provider: Any
    service: OfficeService
    core: ApiCore
    scheduler: Optional[CycleScheduler]
    store: SqliteStore

    def start(self) -> None:
        if self.scheduler:
            self.scheduler.start()

    def stop(self) -> None:
        if self.scheduler:
            self.scheduler.stop()
        self.store.close()


def build_provider(settings: Settings, clock: Callable[[], datetime]):
    if settings.data_provider == "none":
        return NullMarketData()
    if settings.data_provider == "mock":
        return MockMarketData(clock)
    if settings.data_provider == "live":
        from .market_data.live import build_live_provider  # imported lazily: only needed for live data

        assert settings.live is not None
        return build_live_provider(settings.live, clock)
    raise ValueError(f"unknown data provider {settings.data_provider!r}")  # pragma: no cover


def build_office(settings: Settings, clock: Callable[[], datetime] = utcnow,
                 store: Optional[SqliteStore] = None) -> Office:
    provider = build_provider(settings, clock)
    store = store or SqliteStore(settings.database_path)
    robots = RobotRegistry(clock)
    activity = ActivityLog(clock)
    gate = EngineSecurityGate(provider, SecurityEngine(settings.policy), robots, clock)
    broker = PaperBroker(gate, provider, store, settings.risk, settings.broker, clock)
    service = OfficeService(
        provider, broker, gate, robots, activity,
        OfficeConfig(auto_buy_enabled=settings.auto_buy_enabled, auto_buy_usd=settings.auto_buy_usd), clock)
    core = ApiCore(service, settings, provider.name, clock=clock)
    scheduler = CycleScheduler(service, settings.cycle_interval_s) if settings.cycle_interval_s > 0 else None
    return Office(settings, provider, service, core, scheduler, store)


def build_office_from_env(env: Optional[Mapping[str, str]] = None) -> Office:
    return build_office(Settings.from_env(os.environ if env is None else env))
