"""Paper broker.

Pipeline for every order (the ONLY public path to a fill is `submit`):
  validate -> idempotency -> price quote checks -> [BUY: security gate -> risk -> funds] -> fill -> persist.
There is no flag, parameter or method that skips the security gate for a BUY.
Nothing here can touch real funds.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Callable, List, Optional

from ..audit import AuditLog
from ..orders.models import (
    Fill, GateResult, Order, OrderExecutor, OrderResult, OrderStatus, PriceSource, Quote, SecurityGate, Side,
)
from ..portfolio import Portfolio
from ..risk import RiskConfig, RiskManager
from ..storage import SqliteStore


def _utcnow() -> datetime:
    from datetime import timezone
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class BrokerConfig:
    starting_balance_usd: Decimal = Decimal("1000")
    fee_bps: Decimal = Decimal("25")
    slippage_bps: Decimal = Decimal("100")
    max_quote_age_s: int = 60
    allow_mock_data: bool = False  # mock prices/evidence are rejected unless explicitly allowed

    def __post_init__(self) -> None:
        for n in ("starting_balance_usd", "fee_bps", "slippage_bps"):
            v = getattr(self, n)
            if not isinstance(v, Decimal) or not v.is_finite() or v < 0:
                raise ValueError(f"{n} must be a non-negative finite Decimal")
        if self.starting_balance_usd <= 0:
            raise ValueError("starting_balance_usd must be positive")
        if self.fee_bps >= 10000 or self.slippage_bps >= 10000:
            raise ValueError("fee_bps and slippage_bps must be below 10000")


class PaperExecutor(OrderExecutor):
    """Computes simulated fills with fees and slippage. Holds no state."""

    def __init__(self, fee_bps: Decimal, slippage_bps: Decimal):
        self._fee = fee_bps / Decimal(10000)
        self._slip = slippage_bps / Decimal(10000)

    def fill(self, order: Order, quote: Quote) -> Fill:
        if order.side == Side.BUY:
            price = quote.price_usd * (Decimal(1) + self._slip)
            fee = order.amount * self._fee
            net = order.amount - fee
            return Fill(price, net / price, net, fee)
        price = quote.price_usd * (Decimal(1) - self._slip)
        gross = order.amount * price
        return Fill(price, order.amount, gross, gross * self._fee)


class PaperBroker:
    def __init__(
        self,
        gate: SecurityGate,
        price_source: PriceSource,
        store: Optional[SqliteStore] = None,
        risk: Optional[RiskConfig] = None,
        config: Optional[BrokerConfig] = None,
        clock: Callable[[], datetime] = _utcnow,
    ):
        self.config = config or BrokerConfig()
        self._gate = gate
        self._prices = price_source
        self._clock = clock
        self._store = store or SqliteStore()
        self._executor: OrderExecutor = PaperExecutor(self.config.fee_bps, self.config.slippage_bps)
        self.risk = RiskManager(risk or RiskConfig())
        self.audit = AuditLog(self._store, clock)
        self._lock = threading.RLock()
        saved = self._store.load_state("portfolio")
        self.portfolio = Portfolio.from_dict(saved) if saved else Portfolio(self.config.starting_balance_usd)

    # ------------------------------------------------------------------
    def set_risk_config(self, cfg: RiskConfig) -> None:
        with self._lock:
            self.risk = RiskManager(cfg)
            self.audit.append("RISK_CONFIG_UPDATED", cfg.to_dict())
            self._store.commit()

    def _reject(self, order: Order, reasons: List[str], now: datetime, decision: Optional[str] = None,
                label: str = "UNKNOWN", event: str = "ORDER_REJECTED") -> OrderResult:
        res = OrderResult(order.client_order_id, OrderStatus.REJECTED, order.mint, order.side.value,
                          tuple(reasons), security_decision=decision, data_label=label, executed_at=now)
        self.audit.append(event, {"client_order_id": order.client_order_id, "mint": order.mint,
                                  "side": order.side.value, "amount": str(order.amount),
                                  "reasons": reasons, "security_decision": decision})
        return res

    def _finish(self, order: Order, res: OrderResult, now: datetime) -> OrderResult:
        self._store.put_order(order.client_order_id, order.fingerprint(), res, now)
        self._store.save_state("portfolio", self.portfolio.to_dict())
        self._store.commit()
        return res

    # ------------------------------------------------------------------
    def submit(self, order: Order) -> OrderResult:
        with self._lock:
            now = self._clock()
            try:
                order.validate()
            except ValueError as exc:
                bad = OrderResult(str(getattr(order, "client_order_id", "")), OrderStatus.REJECTED,
                                  str(getattr(order, "mint", "")), str(getattr(order, "side", "")),
                                  (f"invalid order: {exc}",), executed_at=now)
                self.audit.append("ORDER_INVALID", {"reason": str(exc)})
                self._store.commit()
                return bad

            prior = self._store.get_order(order.client_order_id)
            if prior is not None:
                fingerprint, stored = prior
                if fingerprint != order.fingerprint():
                    return OrderResult(order.client_order_id, OrderStatus.REJECTED, order.mint, order.side.value,
                                       ("client_order_id was already used with different parameters",),
                                       executed_at=now)
                self.audit.append("ORDER_DUPLICATE", {"client_order_id": order.client_order_id})
                self._store.commit()
                return OrderResult(**{**stored.__dict__, "duplicate": True})

            # --- price quote -------------------------------------------------
            try:
                quote = self._prices.get_quote(order.mint)
            except Exception:
                return self._finish(order, self._reject(order, ["price unavailable"], now), now)
            q_reason = self._quote_problem(quote, order, now)
            if q_reason:
                return self._finish(order, self._reject(order, [q_reason], now, label=str(quote.label)), now)
            self.portfolio.mark(order.mint, quote.price_usd)
            day = now.strftime("%Y-%m-%d")
            self.portfolio.ensure_day_open(day)

            if order.side == Side.BUY:
                return self._finish(order, self._buy(order, quote, now, day), now)
            return self._finish(order, self._sell(order, quote, now, day), now)

    def _quote_problem(self, quote: Quote, order: Order, now: datetime) -> Optional[str]:
        if not isinstance(quote.price_usd, Decimal) or not quote.price_usd.is_finite() or quote.price_usd <= 0:
            return "invalid price"
        if quote.mint != order.mint:
            return "quote is for a different token"
        if quote.label == "MOCK":
            if not self.config.allow_mock_data:
                return "price data is MOCK and mock data is not allowed"
        elif quote.label != "LIVE":
            return f"price data label is {quote.label}; only LIVE data is accepted"
        if quote.fetched_at is None or abs((now - quote.fetched_at).total_seconds()) > self.config.max_quote_age_s:
            return "price data is stale"
        return None

    def _buy(self, order: Order, quote: Quote, now: datetime, day: str) -> OrderResult:
        label = "MOCK" if quote.label == "MOCK" else "LIVE"
        # Security gate: ALWAYS evaluated, fresh, for every BUY.
        try:
            gate: GateResult = self._gate.evaluate_buy(order.mint, order.amount)
        except Exception:
            return self._reject(order, ["security check could not be completed"], now, decision="UNKNOWN",
                                label=label, event="ORDER_BLOCKED_SECURITY")
        if gate.data_label == "MOCK":
            label = "MOCK"
            if not self.config.allow_mock_data:
                return self._reject(order, ["security evidence is MOCK and mock data is not allowed"], now,
                                    gate.decision, label, "ORDER_BLOCKED_SECURITY")
        if not gate.allowed:
            return self._reject(order, list(gate.reasons) or ["security gate did not allow this BUY"], now,
                                gate.decision, label, "ORDER_BLOCKED_SECURITY")
        reasons = self.risk.check_buy(self.portfolio, order.mint, order.amount, day)
        if order.amount > self.portfolio.cash:
            reasons.append("insufficient virtual funds")
        if reasons:
            return self._reject(order, reasons, now, gate.decision, label, "ORDER_BLOCKED_RISK")

        fill = self._executor.fill(order, quote)
        self.portfolio.apply_buy(order.mint, order.amount, fill.fee_usd, fill.quantity)
        res = OrderResult(order.client_order_id, OrderStatus.FILLED, order.mint, "BUY", (),
                          fill.price, fill.quantity, fill.gross_usd, fill.fee_usd, None,
                          gate.decision, label, now)
        self._record_fill(res, now)
        return res

    def _sell(self, order: Order, quote: Quote, now: datetime, day: str) -> OrderResult:
        label = "MOCK" if quote.label == "MOCK" else "LIVE"
        pos = self.portfolio.positions.get(order.mint)
        if pos is None or order.amount > pos.quantity:
            return self._reject(order, ["cannot sell more than the quantity held"], now, label=label)
        fill = self._executor.fill(order, quote)
        realized = self.portfolio.apply_sell(order.mint, order.amount, fill.gross_usd - fill.fee_usd,
                                             fill.fee_usd, day)
        res = OrderResult(order.client_order_id, OrderStatus.FILLED, order.mint, "SELL", (),
                          fill.price, fill.quantity, fill.gross_usd, fill.fee_usd, realized,
                          None, label, now)
        self._record_fill(res, now)
        return res

    def _record_fill(self, res: OrderResult, now: datetime) -> None:
        self._store.add_trade(now, res)
        self.audit.append("ORDER_FILLED", {
            "client_order_id": res.client_order_id, "mint": res.mint, "side": res.side,
            "quantity": str(res.quantity), "price": str(res.fill_price), "fee_usd": str(res.fee_usd),
            "data_label": res.data_label, "simulated": True})

    # ------------------------------------------------------------------
    def enforce_stop_losses(self) -> List[OrderResult]:
        """Sell whole positions whose price fell to the stop-loss level. Idempotent per minute."""
        results: List[OrderResult] = []
        with self._lock:
            now = self._clock()
            for mint in list(self.portfolio.positions):
                try:
                    quote = self._prices.get_quote(mint)
                except Exception:
                    self.audit.append("STOP_LOSS_PRICE_UNAVAILABLE", {"mint": mint})
                    continue
                if self._quote_problem(quote, Order("x", mint, Side.SELL, Decimal(1)), now):
                    self.audit.append("STOP_LOSS_PRICE_UNUSABLE", {"mint": mint})
                    continue
                pos = self.portfolio.positions[mint]
                self.portfolio.mark(mint, quote.price_usd)
                if self.risk.stop_loss_hit(pos.avg_cost, quote.price_usd):
                    self.audit.append("STOP_LOSS_TRIGGERED", {"mint": mint, "price": str(quote.price_usd)})
                    oid = f"stoploss-{mint}-{now.strftime('%Y%m%d%H%M')}"
                    results.append(self.submit(Order(oid, mint, Side.SELL, pos.quantity)))
            self._store.save_state("portfolio", self.portfolio.to_dict())
            self._store.commit()
        return results

    # ------------------------------------------------------------------
    def snapshot(self) -> dict:
        with self._lock:
            p = self.portfolio
            return {
                "simulated": True,
                "mode": "paper",
                "cash_usd": str(p.cash),
                "equity_usd": str(p.equity()),
                "realized_pnl_usd": str(p.realized_pnl()),
                "unrealized_pnl_usd": str(p.unrealized_pnl()),
                "fees_usd": str(p.fees_total),
                "starting_balance_usd": str(p.starting_cash),
                "positions": [{"mint": m, "quantity": str(x.quantity), "avg_cost_usd": str(x.avg_cost),
                               "mark_usd": str(p.last_marks.get(m, x.avg_cost))}
                              for m, x in p.positions.items()],
            }

    def trades(self, limit: int = 100) -> List[dict]:
        return self._store.list_trades(limit)
