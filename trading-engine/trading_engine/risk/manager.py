"""Risk limits. There is deliberately no 'live trading' field in this config."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import List

from ..portfolio import Portfolio


@dataclass(frozen=True)
class RiskConfig:
    max_position_pct: Decimal = Decimal("5")  # max value of one token as % of equity
    stop_loss_pct: Decimal = Decimal("8")  # sell when price <= avg cost * (1 - pct)
    max_daily_loss_pct: Decimal = Decimal("10")  # stop BUYs when equity fell this much vs day open
    max_open_positions: int = 10

    def __post_init__(self) -> None:
        for name in ("max_position_pct", "stop_loss_pct", "max_daily_loss_pct"):
            v = getattr(self, name)
            if not isinstance(v, Decimal) or not v.is_finite() or not (0 < v <= 100):
                raise ValueError(f"{name} must be a Decimal in (0, 100]")
        if not isinstance(self.max_open_positions, int) or self.max_open_positions < 1:
            raise ValueError("max_open_positions must be a positive integer")

    def to_dict(self) -> dict:
        return {"max_position_pct": str(self.max_position_pct), "stop_loss_pct": str(self.stop_loss_pct),
                "max_daily_loss_pct": str(self.max_daily_loss_pct),
                "max_open_positions": self.max_open_positions}


class RiskManager:
    def __init__(self, config: RiskConfig):
        self.config = config

    def check_buy(self, portfolio: Portfolio, mint: str, spend: Decimal, day: str) -> List[str]:
        """Return a list of reasons the BUY violates risk limits (empty = allowed)."""
        c = self.config
        reasons: List[str] = []
        equity = portfolio.equity()

        open_equity = portfolio.day_open_equity.get(day, equity)
        if open_equity > 0:
            loss_pct = (open_equity - equity) * 100 / open_equity
            if loss_pct >= c.max_daily_loss_pct:
                reasons.append(f"daily loss limit reached ({loss_pct:.2f}% >= {c.max_daily_loss_pct}%)")

        new_value = portfolio.position_value(mint) + spend
        if equity <= 0 or new_value * 100 / equity > c.max_position_pct:
            reasons.append(f"position would exceed {c.max_position_pct}% of equity")

        if mint not in portfolio.positions and len(portfolio.positions) >= c.max_open_positions:
            reasons.append(f"maximum open positions ({c.max_open_positions}) reached")
        return reasons

    def stop_loss_hit(self, avg_cost: Decimal, price: Decimal) -> bool:
        return price <= avg_cost * (Decimal("1") - self.config.stop_loss_pct / Decimal("100"))
