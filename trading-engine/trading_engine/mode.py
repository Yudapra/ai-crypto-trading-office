"""Trading-mode guard. Only 'paper' is accepted; there is no switch to enable live trading."""
from __future__ import annotations


class LiveTradingDisabled(RuntimeError):
    """Raised on any attempt to enable or use live trading."""


def assert_paper_mode(value: str) -> str:
    if value != "paper":
        raise LiveTradingDisabled(
            "Live trading is disabled. TRADING_MODE must be 'paper'. "
            "Enabling live trading requires a separate, explicit code change and review.")
    return value
