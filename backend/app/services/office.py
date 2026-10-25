"""Office orchestration: connects the data provider, the security engine and the paper broker.

The six robots are NOT animated by a script. Each robot's state is set here, immediately before and
after the real work it represents:

  research_analyst  -> listing candidate tokens from the provider
  market_analyst    -> fetching a price quote per candidate
  security_analyst  -> the security engine evaluation (set by EngineSecurityGate)
  trader            -> submitting a simulated order / enforcing stop-loss levels
  accountant        -> reconciling the simulated portfolio after fills
  ceo               -> the whole cycle (start, outcome summary, failure)

Everything here is paper trading. There is no code path to a live exchange.
"""
from __future__ import annotations

import json
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional

from trading_engine.orders import Order, OrderResult, OrderStatus, Side
from trading_engine.paper_broker import PaperBroker
from trading_engine.storage import result_to_json

from ..models.common import Decision, utcnow
from ..security.engine import SecurityReport
from .activity import ActivityLog
from .gate import EngineSecurityGate
from .robots import RobotRegistry, RobotState


class CycleBusy(RuntimeError):
    """A cycle is already running."""


@dataclass(frozen=True)
class OfficeConfig:
    auto_buy_enabled: bool = False  # automatic simulated BUYs are opt-in
    auto_buy_usd: Decimal = Decimal("25")  # simulated USD per automatic BUY
    max_candidates: int = 10
    max_buys_per_cycle: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.auto_buy_usd, Decimal) or not self.auto_buy_usd.is_finite() or self.auto_buy_usd <= 0:
            raise ValueError("auto_buy_usd must be a positive finite Decimal")
        if self.max_candidates < 1 or self.max_buys_per_cycle < 0:
            raise ValueError("max_candidates must be >= 1 and max_buys_per_cycle >= 0")


def _money(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.01'))}"


def _short(mint: str) -> str:
    return mint if len(mint) <= 12 else f"{mint[:5]}..{mint[-4:]}"


def result_to_dict(res: OrderResult) -> Dict[str, Any]:
    return json.loads(result_to_json(res))


class OfficeService:
    def __init__(self, provider, broker: PaperBroker, gate: EngineSecurityGate, robots: RobotRegistry,
                 activity: ActivityLog, config: Optional[OfficeConfig] = None,
                 clock: Callable[[], datetime] = utcnow):
        self.provider = provider
        self.broker = broker
        self.gate = gate
        self.robots = robots
        self.activity = activity
        self.config = config or OfficeConfig()
        self._clock = clock
        self._cycle_lock = threading.Lock()
        self._cycle_no = 0
        self.last_cycle: Optional[Dict[str, Any]] = None
        robots.subscribe(self._on_robot_change)
        broker.audit.subscribe(self._on_audit_event)

    # ---- feed wiring ---------------------------------------------------
    def _on_robot_change(self, robot_id: str, state: RobotState, detail: str) -> None:
        self.activity.record(robot_id, "state", detail, state=state.value)

    _AUDIT_ROBOT = {
        "ORDER_FILLED": "trader", "ORDER_REJECTED": "trader", "ORDER_BLOCKED_SECURITY": "trader",
        "ORDER_BLOCKED_RISK": "trader", "ORDER_INVALID": "trader", "STOP_LOSS_TRIGGERED": "trader",
    }

    def _on_audit_event(self, event: str, payload: dict) -> None:
        robot = self._AUDIT_ROBOT.get(event)
        if robot is None:
            return
        mint = payload.get("mint", "")
        side = payload.get("side", "")
        if event == "ORDER_FILLED":
            msg = f"simulated {side} filled for {_short(str(mint))}"
        elif event == "STOP_LOSS_TRIGGERED":
            msg = f"stop-loss triggered for {_short(str(mint))}"
        else:
            reasons = payload.get("reasons") or []
            first = reasons[0] if reasons else event.lower()
            msg = f"{event.replace('ORDER_', '').lower().replace('_', ' ')}: {first}"
        self.activity.record(robot, "order", msg, data={"event": event, **{
            k: v for k, v in payload.items() if k in ("mint", "side", "client_order_id", "security_decision")}})

    # ---- simple reads --------------------------------------------------
    def portfolio(self) -> Dict[str, Any]:
        return self.broker.snapshot()

    def reports(self, limit: int = 20) -> List[Dict[str, Any]]:
        latest = sorted(self.gate.latest.values(), key=lambda r: r.evaluated_at, reverse=True)
        return [r.to_dict() for r in latest[:max(1, min(limit, 100))]]

    def report_for(self, mint: str) -> Optional[Dict[str, Any]]:
        rep = self.gate.latest.get(mint)
        return rep.to_dict() if rep else None

    # ---- single token scan --------------------------------------------
    def scan_token(self, mint: str, order_usd: Optional[Decimal] = None) -> Dict[str, Any]:
        """Price one token and run the full security evaluation. Never places an order."""
        usd = order_usd or self.config.auto_buy_usd
        price = self._price(mint)
        report = self._assess(mint, usd)
        return {"mint": mint, "price_usd": None if price is None else str(price),
                "report": report.to_dict() if report else None,
                "error": None if report else "security data provider unavailable"}

    def _price(self, mint: str) -> Optional[Decimal]:
        self.robots.update("market_analyst", RobotState.ANALYZING, f"pricing {_short(mint)}")
        try:
            quote = self.provider.get_quote(mint)
        except Exception:
            self.robots.update("market_analyst", RobotState.ERROR, f"price unavailable for {_short(mint)}")
            return None
        self.robots.update("market_analyst", RobotState.COMPLETED,
                           f"{_short(mint)} priced at ${quote.price_usd} ({quote.label})")
        return quote.price_usd

    def _assess(self, mint: str, usd: Decimal) -> Optional[SecurityReport]:
        try:
            report = self.gate.assess(mint, usd)
        except Exception:
            return None  # gate already set the Security Analyst to ERROR
        first = report.reasons[0] if report.reasons else "all required checks passed"
        self.activity.record("security_analyst", "decision", f"{_short(mint)}: {report.decision.value} - {first}",
                             state=report.decision.value,
                             data={"mint": mint, "decision": report.decision.value,
                                   "buy_allowed": report.buy_allowed, "data_label": report.data_label.value})
        return report

    # ---- simulated orders ---------------------------------------------
    def place_order(self, order: Order) -> OrderResult:
        """Submit a SIMULATED order through the broker (the only path to a fill)."""
        side = order.side.value
        self.robots.update("trader", RobotState.WORKING, f"submitting simulated {side} for {_short(order.mint)}")
        try:
            res = self.broker.submit(order)
        except Exception:
            self.robots.update("trader", RobotState.ERROR, f"simulated {side} failed unexpectedly")
            raise
        if res.status == OrderStatus.FILLED:
            self.robots.update("trader", RobotState.COMPLETED,
                               f"simulated {side} filled for {_short(order.mint)}"
                               + (" (duplicate request, earlier result returned)" if res.duplicate else ""))
            self._reconcile()
        else:
            why = res.reasons[0] if res.reasons else "rejected"
            self.robots.update("trader", RobotState.BLOCKED, f"{side} {_short(order.mint)} rejected: {why}")
        return res

    def enforce_stop_losses(self) -> List[OrderResult]:
        """Run the broker's stop-loss pass. Only reports activity when positions exist."""
        if not self.broker.portfolio.positions:
            return []
        self.robots.update("trader", RobotState.WORKING, "checking stop-loss levels on open positions")
        results = self.broker.enforce_stop_losses()
        if results:
            self.robots.update("trader", RobotState.COMPLETED, f"stop-loss sold {len(results)} position(s)")
            self._reconcile()
        else:
            self.robots.update("trader", RobotState.COMPLETED, "stop-loss check done, no position triggered")
        return results

    def _reconcile(self) -> Dict[str, Any]:
        self.robots.update("accountant", RobotState.WORKING, "reconciling simulated portfolio")
        snap = self.broker.snapshot()
        self.robots.update(
            "accountant", RobotState.COMPLETED,
            f"equity ${_money(Decimal(snap['equity_usd']))}, realized PnL ${_money(Decimal(snap['realized_pnl_usd']))}"
            f", cash ${_money(Decimal(snap['cash_usd']))}")
        return snap

    # ---- full cycle ---------------------------------------------------
    def run_cycle(self, auto_buy: Optional[bool] = None) -> Dict[str, Any]:
        """Discover -> price -> security check -> (optional) simulated BUY -> stop-loss -> reconcile."""
        if not self._cycle_lock.acquire(blocking=False):
            raise CycleBusy("a cycle is already running")
        try:
            return self._run_cycle_locked(self.config.auto_buy_enabled if auto_buy is None else bool(auto_buy))
        finally:
            self._cycle_lock.release()

    def _run_cycle_locked(self, auto_buy: bool) -> Dict[str, Any]:
        self._cycle_no += 1
        n = self._cycle_no
        started = self._clock()
        self.robots.update("ceo", RobotState.WORKING, f"cycle #{n} started" + (" (auto-buy ON)" if auto_buy else ""))
        summary: Dict[str, Any] = {
            "cycle_id": n, "started_at": started.isoformat(), "finished_at": None, "status": "running",
            "auto_buy": auto_buy, "scanned": 0, "counts": {d.value: 0 for d in Decision},
            "errors": 0, "orders": [], "candidates": [], "stop_loss_sells": 0}

        # 1. research
        self.robots.update("research_analyst", RobotState.WORKING, "discovering candidate tokens")
        try:
            candidates = list(self.provider.list_candidates())[: self.config.max_candidates]
        except Exception:
            self.robots.update("research_analyst", RobotState.ERROR, "candidate discovery failed")
            self.robots.update("ceo", RobotState.ERROR, f"cycle #{n} aborted: candidate discovery failed")
            summary.update(status="failed", finished_at=self._clock().isoformat(), errors=1)
            self.last_cycle = summary
            return summary
        self.robots.update("research_analyst", RobotState.COMPLETED, f"{len(candidates)} candidate token(s) found")

        # 2-4. price, security, optional simulated BUY
        buys = 0
        priced = 0
        for mint in candidates:
            entry: Dict[str, Any] = {"mint": mint, "price_usd": None, "decision": None, "buy_allowed": False,
                                     "data_label": None, "reasons": [], "order": None}
            summary["candidates"].append(entry)
            summary["scanned"] += 1
            price = self._price(mint)
            if price is not None:
                priced += 1
                entry["price_usd"] = str(price)
            report = self._assess(mint, self.config.auto_buy_usd)
            if report is None:
                summary["errors"] += 1
                entry["decision"] = "ERROR"
                continue
            summary["counts"][report.decision.value] += 1
            entry.update(decision=report.decision.value, buy_allowed=report.buy_allowed,
                         data_label=report.data_label.value, reasons=list(report.reasons[:3]))
            if auto_buy and report.buy_allowed and price is not None and buys < self.config.max_buys_per_cycle:
                oid = f"auto-{started:%Y%m%d%H%M%S}-{uuid.uuid4().hex[:8]}"
                res = self.place_order(Order(oid, mint, Side.BUY, self.config.auto_buy_usd))
                buys += 1
                entry["order"] = result_to_dict(res)
                summary["orders"].append(entry["order"])

        self.robots.update("market_analyst", RobotState.COMPLETED if priced or not candidates else RobotState.ERROR,
                           f"priced {priced} of {len(candidates)} candidate(s)")
        c = summary["counts"]
        self.robots.update("security_analyst", RobotState.COMPLETED,
                           f"{summary['scanned']} checked: {c['PASS']} pass, {c['REVIEW_REQUIRED']} review, "
                           f"{c['BLOCKED']} blocked, {c['UNKNOWN']} unknown")

        # 5. stop-loss + 6. accounting
        try:
            summary["stop_loss_sells"] = len(self.enforce_stop_losses())
        except Exception:
            summary["errors"] += 1
            self.robots.update("trader", RobotState.ERROR, "stop-loss check failed")
        snap = self._reconcile()
        summary["portfolio"] = {k: snap[k] for k in ("cash_usd", "equity_usd", "realized_pnl_usd", "unrealized_pnl_usd")}

        summary.update(status="completed", finished_at=self._clock().isoformat())
        self.robots.update(
            "ceo", RobotState.COMPLETED,
            f"cycle #{n} done: {summary['scanned']} scanned, {c['PASS']} pass, {c['BLOCKED'] + c['UNKNOWN']} "
            f"blocked/unknown, {len(summary['orders'])} simulated order(s)")
        self.activity.record("ceo", "cycle", f"cycle #{n} finished", data={
            "cycle_id": n, "scanned": summary["scanned"], "counts": c, "orders": len(summary["orders"])})
        self.last_cycle = summary
        return summary


class CycleScheduler:
    """Runs `OfficeService.run_cycle` on a fixed interval in a daemon thread."""

    def __init__(self, service: OfficeService, interval_s: float, min_interval_s: float = 10.0):
        if interval_s < min_interval_s:
            raise ValueError(f"interval must be at least {min_interval_s} seconds")
        self._service = service
        self._interval = interval_s
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="cycle-scheduler", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout)

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._service.run_cycle()
            except CycleBusy:
                pass
            except Exception:
                self._service.activity.record("ceo", "error", "scheduled cycle failed unexpectedly")
            self._stop.wait(self._interval)
