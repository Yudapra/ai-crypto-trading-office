"""Virtual portfolio. All amounts are simulated USD. Never lets cash go negative."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, Optional


@dataclass
class Position:
    mint: str
    quantity: Decimal
    cost_total: Decimal  # simulated USD paid including buy fees

    @property
    def avg_cost(self) -> Decimal:
        return self.cost_total / self.quantity


class InsufficientFunds(ValueError):
    pass


class Portfolio:
    def __init__(self, cash: Decimal):
        if not isinstance(cash, Decimal) or not cash.is_finite() or cash < 0:
            raise ValueError("starting cash must be a non-negative finite Decimal")
        self.starting_cash = cash
        self.cash = cash
        self.positions: Dict[str, Position] = {}
        self.realized_by_day: Dict[str, Decimal] = {}
        self.day_open_equity: Dict[str, Decimal] = {}
        self.fees_total = Decimal("0")
        self.last_marks: Dict[str, Decimal] = {}

    # --- valuation -------------------------------------------------------
    def mark(self, mint: str, price: Decimal) -> None:
        self.last_marks[mint] = price

    def position_value(self, mint: str) -> Decimal:
        pos = self.positions.get(mint)
        if not pos:
            return Decimal("0")
        return pos.quantity * self.last_marks.get(mint, pos.avg_cost)

    def equity(self) -> Decimal:
        return self.cash + sum((self.position_value(m) for m in self.positions), Decimal("0"))

    def unrealized_pnl(self) -> Decimal:
        total = Decimal("0")
        for m, pos in self.positions.items():
            total += pos.quantity * self.last_marks.get(m, pos.avg_cost) - pos.cost_total
        return total

    def realized_pnl(self) -> Decimal:
        return sum(self.realized_by_day.values(), Decimal("0"))

    def ensure_day_open(self, day: str) -> None:
        self.day_open_equity.setdefault(day, self.equity())

    # --- mutations -------------------------------------------------------
    def apply_buy(self, mint: str, spend: Decimal, fee: Decimal, quantity: Decimal) -> None:
        if spend > self.cash:
            raise InsufficientFunds("spend exceeds available virtual cash")
        self.cash -= spend
        self.fees_total += fee
        pos = self.positions.get(mint)
        if pos:
            pos.quantity += quantity
            pos.cost_total += spend
        else:
            self.positions[mint] = Position(mint, quantity, spend)

    def apply_sell(self, mint: str, quantity: Decimal, proceeds: Decimal, fee: Decimal, day: str) -> Decimal:
        pos = self.positions.get(mint)
        if not pos or quantity > pos.quantity:
            raise ValueError("cannot sell more than held")
        cost_basis = pos.avg_cost * quantity
        realized = proceeds - cost_basis
        self.cash += proceeds
        self.fees_total += fee
        pos.quantity -= quantity
        pos.cost_total -= cost_basis
        if pos.quantity == 0:
            del self.positions[mint]
        self.realized_by_day[day] = self.realized_by_day.get(day, Decimal("0")) + realized
        return realized

    # --- persistence -----------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "starting_cash": str(self.starting_cash), "cash": str(self.cash),
            "fees_total": str(self.fees_total),
            "positions": {m: {"quantity": str(p.quantity), "cost_total": str(p.cost_total)}
                          for m, p in self.positions.items()},
            "realized_by_day": {d: str(v) for d, v in self.realized_by_day.items()},
            "day_open_equity": {d: str(v) for d, v in self.day_open_equity.items()},
            "last_marks": {m: str(v) for m, v in self.last_marks.items()},
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Portfolio":
        p = cls(Decimal(d["starting_cash"]))
        p.cash = Decimal(d["cash"])
        p.fees_total = Decimal(d["fees_total"])
        p.positions = {m: Position(m, Decimal(v["quantity"]), Decimal(v["cost_total"]))
                       for m, v in d["positions"].items()}
        p.realized_by_day = {k: Decimal(v) for k, v in d["realized_by_day"].items()}
        p.day_open_equity = {k: Decimal(v) for k, v in d["day_open_equity"].items()}
        p.last_marks = {k: Decimal(v) for k, v in d["last_marks"].items()}
        return p
