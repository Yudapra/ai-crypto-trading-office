"""Order types and the interfaces the broker depends on."""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Optional, Protocol, Tuple

from ..mode import LiveTradingDisabled

_ID_RE = re.compile(r"^[A-Za-z0-9_\-:.]{1,80}$")


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderStatus(str, Enum):
    FILLED = "FILLED"
    REJECTED = "REJECTED"


@dataclass(frozen=True)
class Order:
    """BUY: `amount` is USD to spend (fee included). SELL: `amount` is token quantity."""

    client_order_id: str
    mint: str
    side: Side
    amount: Decimal

    def validate(self) -> None:
        if not isinstance(self.client_order_id, str) or not _ID_RE.match(self.client_order_id):
            raise ValueError("invalid client_order_id")
        if not isinstance(self.mint, str) or not _ID_RE.match(self.mint):
            raise ValueError("invalid mint")
        if not isinstance(self.side, Side):
            raise ValueError("invalid side")
        if not isinstance(self.amount, Decimal):
            raise ValueError("amount must be a Decimal")
        try:
            finite = self.amount.is_finite()
        except InvalidOperation:  # pragma: no cover
            finite = False
        if not finite or self.amount <= 0:
            raise ValueError("amount must be a positive finite number")

    def fingerprint(self) -> str:
        return f"{self.mint}|{self.side.value}|{self.amount}"


@dataclass(frozen=True)
class Quote:
    mint: str
    price_usd: Decimal
    fetched_at: Optional[datetime]
    provider: str
    label: str  # "LIVE" | "MOCK" | "STALE" | "UNKNOWN"


@dataclass(frozen=True)
class GateResult:
    allowed: bool
    decision: str
    reasons: Tuple[str, ...]
    data_label: str


@dataclass(frozen=True)
class OrderResult:
    client_order_id: str
    status: OrderStatus
    mint: str
    side: str
    reasons: Tuple[str, ...] = ()
    fill_price: Optional[Decimal] = None
    quantity: Optional[Decimal] = None
    gross_usd: Optional[Decimal] = None
    fee_usd: Optional[Decimal] = None
    realized_pnl_usd: Optional[Decimal] = None
    security_decision: Optional[str] = None
    data_label: str = "UNKNOWN"
    executed_at: Optional[datetime] = None
    duplicate: bool = False
    simulated: bool = True  # always True: nothing here touches real funds


class SecurityGate(Protocol):
    def evaluate_buy(self, mint: str, order_usd: Decimal) -> GateResult: ...


class PriceSource(Protocol):
    def get_quote(self, mint: str) -> Quote: ...


class OrderExecutor(ABC):
    """Executes an already-approved order. Only the broker holds an executor."""

    @abstractmethod
    def fill(self, order: Order, quote: Quote) -> "Fill": ...


@dataclass(frozen=True)
class Fill:
    price: Decimal
    quantity: Decimal
    gross_usd: Decimal
    fee_usd: Decimal


class LiveExecutor(OrderExecutor):
    """Placeholder for a future live executor. It can never execute anything."""

    def fill(self, order: Order, quote: Quote) -> Fill:
        raise LiveTradingDisabled("Live trading is disabled.")
