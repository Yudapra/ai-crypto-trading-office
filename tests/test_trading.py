import dataclasses
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from fractions import Fraction

import tests  # noqa: F401  (sets sys.path)
from app.market_data.base import ProviderUnavailable
from app.market_data.mock import make_evidence
from app.models.common import DataLabel
from app.security.engine import SecurityEngine
from app.security.policy import SecurityPolicy
from app.services.gate import EngineSecurityGate
from app.services.robots import RobotRegistry, RobotState
from trading_engine.audit import redact
from trading_engine.mode import LiveTradingDisabled, assert_paper_mode
from trading_engine.orders import GateResult, LiveExecutor, Order, OrderStatus, Quote, Side
from trading_engine.paper_broker import BrokerConfig, PaperBroker
from trading_engine.risk import RiskConfig
from trading_engine.storage import SqliteStore

START = datetime(2026, 10, 24, 12, 0, 0, tzinfo=timezone.utc)
D = Decimal


class Clock:
    def __init__(self):
        self.now = START

    def __call__(self):
        return self.now


class FakeProvider:
    name = "fake"

    def __init__(self, clock, label=DataLabel.LIVE):
        self.clock, self.label = clock, label
        self.prices = {"TOKEN1": D("1")}
        self.evidence_kwargs = {}
        self.calls = 0
        self.fail = False

    def get_quote(self, mint):
        return Quote(mint, self.prices[mint], self.clock(), "fake", self.label.value)

    def get_evidence(self, mint, usd):
        self.calls += 1
        if self.fail:
            raise ProviderUnavailable("down")
        return make_evidence(mint, self.clock(), label=self.label, **self.evidence_kwargs)


def make_broker(clock=None, label=DataLabel.LIVE, risk=None, allow_mock=False, store=None, policy=None):
    clock = clock or Clock()
    prov = FakeProvider(clock, label)
    robots = RobotRegistry(clock)
    gate = EngineSecurityGate(prov, SecurityEngine(policy or SecurityPolicy(allow_mock_data=allow_mock)),
                              robots, clock)
    broker = PaperBroker(
        gate, prov, store=store,
        risk=risk or RiskConfig(max_position_pct=D("50"), max_daily_loss_pct=D("50")),
        config=BrokerConfig(starting_balance_usd=D("1000"), allow_mock_data=allow_mock),
        clock=clock)
    return broker, prov, gate, robots, clock


def buy(broker, oid, amount, mint="TOKEN1"):
    return broker.submit(Order(oid, mint, Side.BUY, D(str(amount))))


class TestBuySell(unittest.TestCase):
    def test_buy_fill_fee_slippage_and_accounting(self):
        b, *_ = make_broker()
        r = buy(b, "o1", 100)
        self.assertEqual(r.status, OrderStatus.FILLED)
        self.assertTrue(r.simulated)
        self.assertEqual(r.fee_usd, D("0.25"))
        self.assertEqual(r.fill_price, D("1.01"))
        self.assertEqual(r.quantity, D("99.75") / D("1.01"))
        snap = b.snapshot()
        self.assertEqual(snap["cash_usd"], "900")
        self.assertEqual(b.portfolio.positions["TOKEN1"].cost_total, D("100"))
        self.assertEqual(b.portfolio.fees_total, D("0.25"))
        self.assertEqual(len(b.trades()), 1)

    def test_sell_realized_pnl(self):
        b, prov, *_ = make_broker()
        buy(b, "o1", 100)
        qty = b.portfolio.positions["TOKEN1"].quantity
        prov.prices["TOKEN1"] = D("2")
        r = b.submit(Order("s1", "TOKEN1", Side.SELL, qty))
        self.assertEqual(r.status, OrderStatus.FILLED)
        self.assertGreater(r.realized_pnl_usd, 0)
        self.assertNotIn("TOKEN1", b.portfolio.positions)
        self.assertEqual(b.portfolio.realized_pnl(), r.realized_pnl_usd)
        self.assertEqual(b.portfolio.cash, D("900") + r.gross_usd - r.fee_usd)

    def test_cannot_sell_more_than_held(self):
        b, *_ = make_broker()
        buy(b, "o1", 100)
        r = b.submit(Order("s1", "TOKEN1", Side.SELL, D("1000000")))
        self.assertEqual(r.status, OrderStatus.REJECTED)

    def test_cannot_spend_more_than_available_funds(self):
        b, *_ = make_broker(risk=RiskConfig(max_position_pct=D("100"), max_daily_loss_pct=D("50")))
        r = buy(b, "o1", 5000)
        self.assertEqual(r.status, OrderStatus.REJECTED)
        self.assertIn("insufficient virtual funds", r.reasons)
        self.assertEqual(b.portfolio.cash, D("1000"))
        self.assertEqual(b.trades(), [])

    def test_cash_never_negative_across_orders(self):
        b, *_ = make_broker(risk=RiskConfig(max_position_pct=D("100"), max_daily_loss_pct=D("50")))
        for i in range(5):
            buy(b, f"o{i}", 300)
            self.assertGreaterEqual(b.portfolio.cash, 0)

    def test_invalid_orders_rejected_without_exception(self):
        b, *_ = make_broker()
        bad = [Order("", "TOKEN1", Side.BUY, D("10")), Order("o", "TOKEN1", Side.BUY, D("-5")),
               Order("o", "TOKEN1", Side.BUY, D("0")), Order("o", "TOKEN1", Side.BUY, D("NaN")),
               Order("o", "TOKEN1", Side.BUY, D("Infinity")), Order("o", "bad mint!", Side.BUY, D("5")),
               Order("o", "TOKEN1", "BUY", D("5"))]
        for o in bad:
            self.assertEqual(b.submit(o).status, OrderStatus.REJECTED, o)
        self.assertEqual(b.portfolio.cash, D("1000"))


class TestRisk(unittest.TestCase):
    def test_position_limit_enforced(self):
        b, *_ = make_broker(risk=RiskConfig(max_position_pct=D("10"), max_daily_loss_pct=D("50")))
        self.assertEqual(buy(b, "o1", 100).status, OrderStatus.FILLED)
        r = buy(b, "o2", 5)
        self.assertEqual(r.status, OrderStatus.REJECTED)
        self.assertTrue(any("position would exceed" in x for x in r.reasons))

    def test_default_position_limit_rejects_large_buy(self):
        b, *_ = make_broker(risk=RiskConfig())
        self.assertEqual(buy(b, "o1", 100).status, OrderStatus.REJECTED)  # 10% > default 5%
        self.assertEqual(buy(b, "o2", 50).status, OrderStatus.FILLED)

    def test_daily_loss_limit_blocks_buys(self):
        b, prov, *_ = make_broker(risk=RiskConfig(max_position_pct=D("50"), max_daily_loss_pct=D("5")))
        self.assertEqual(buy(b, "o1", 100).status, OrderStatus.FILLED)
        prov.prices["TOKEN1"] = D("0.5")
        r = buy(b, "o2", 1)
        self.assertEqual(r.status, OrderStatus.REJECTED)
        self.assertTrue(any("daily loss limit" in x for x in r.reasons))

    def test_stop_loss_sells_position(self):
        b, prov, *_ = make_broker()
        buy(b, "o1", 100)
        prov.prices["TOKEN1"] = D("0.98")
        self.assertEqual(b.enforce_stop_losses(), [])  # above stop level
        prov.prices["TOKEN1"] = D("0.9")
        res = b.enforce_stop_losses()
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0].status, OrderStatus.FILLED)
        self.assertLess(res[0].realized_pnl_usd, 0)
        self.assertNotIn("TOKEN1", b.portfolio.positions)

    def test_risk_config_validation_and_no_live_field(self):
        with self.assertRaises(ValueError):
            RiskConfig(max_position_pct=D("0"))
        with self.assertRaises(ValueError):
            RiskConfig(stop_loss_pct=D("150"))
        names = [f.name for f in dataclasses.fields(RiskConfig)] + [f.name for f in dataclasses.fields(BrokerConfig)]
        self.assertFalse(any("live" in n.lower() for n in names))


class TestSecurityCannotBeBypassed(unittest.TestCase):
    def test_concentrated_token_buy_is_rejected_and_recorded(self):
        b, prov, *_ = make_broker()
        prov.evidence_kwargs = {"top10_pct": Fraction("25.1")}
        r = buy(b, "o1", 50)
        self.assertEqual(r.status, OrderStatus.REJECTED)
        self.assertEqual(r.security_decision, "BLOCKED")
        self.assertTrue(any("holder_concentration" in x for x in r.reasons))
        self.assertEqual(b.portfolio.cash, D("1000"))
        self.assertEqual(b.trades(), [])
        events = [row["event"] for row in b.audit.recent()]
        self.assertIn("ORDER_BLOCKED_SECURITY", events)

    def test_failed_sell_simulation_prevents_buy(self):
        b, prov, *_ = make_broker()
        prov.evidence_kwargs = {"sell_status": "failed"}
        self.assertEqual(buy(b, "o1", 50).status, OrderStatus.REJECTED)

    def test_provider_outage_blocks_buy(self):
        b, prov, *_ = make_broker()
        prov.fail = True
        r = buy(b, "o1", 50)
        self.assertEqual(r.status, OrderStatus.REJECTED)
        self.assertEqual(r.security_decision, "UNKNOWN")

    def test_gate_exception_blocks_buy(self):
        class Boom:
            def evaluate_buy(self, mint, usd):
                raise RuntimeError("boom")
        clock = Clock()
        b = PaperBroker(Boom(), FakeProvider(clock), clock=clock)
        self.assertEqual(buy(b, "o1", 10).status, OrderStatus.REJECTED)

    def test_denying_gate_means_no_fill_and_allowing_gate_is_called_every_time(self):
        class Counting:
            def __init__(self, allow):
                self.allow, self.calls = allow, 0

            def evaluate_buy(self, mint, usd):
                self.calls += 1
                return GateResult(self.allow, "PASS" if self.allow else "BLOCKED", ("test",), "LIVE")
        clock = Clock()
        deny = Counting(False)
        b = PaperBroker(deny, FakeProvider(clock), clock=clock, risk=RiskConfig(max_position_pct=D("50")))
        self.assertEqual(buy(b, "o1", 10).status, OrderStatus.REJECTED)
        allow = Counting(True)
        b2 = PaperBroker(allow, FakeProvider(clock), clock=clock, risk=RiskConfig(max_position_pct=D("50")))
        buy(b2, "a", 10)
        buy(b2, "b", 10)
        self.assertEqual(allow.calls, 2)

    def test_every_buy_reevaluates_with_fresh_evidence(self):
        b, prov, *_ = make_broker()
        self.assertEqual(buy(b, "o1", 10).status, OrderStatus.FILLED)
        prov.evidence_kwargs = {"top10_pct": Fraction(40)}  # conditions worsen
        self.assertEqual(buy(b, "o2", 10).status, OrderStatus.REJECTED)
        self.assertEqual(prov.calls, 2)

    def test_sells_are_not_blocked_by_security(self):
        b, prov, *_ = make_broker()
        buy(b, "o1", 100)
        prov.evidence_kwargs = {"sell_status": "failed", "top10_pct": Fraction(90)}
        qty = b.portfolio.positions["TOKEN1"].quantity
        self.assertEqual(b.submit(Order("s1", "TOKEN1", Side.SELL, qty)).status, OrderStatus.FILLED)

    def test_robot_states_follow_security_events(self):
        b, prov, gate, robots, _ = make_broker()
        buy(b, "o1", 10)
        sec = [r for r in robots.snapshot() if r["id"] == "security_analyst"][0]
        self.assertEqual(sec["state"], RobotState.COMPLETED.value)
        prov.evidence_kwargs = {"sell_status": "failed"}
        buy(b, "o2", 10)
        sec = [r for r in robots.snapshot() if r["id"] == "security_analyst"][0]
        self.assertEqual(sec["state"], RobotState.BLOCKED.value)
        trader = [r for r in robots.snapshot() if r["id"] == "trader"][0]
        self.assertEqual(trader["state"], "IDLE")  # nothing reported it working


class TestIdempotencyAndPersistence(unittest.TestCase):
    def test_duplicate_order_not_executed_twice(self):
        b, *_ = make_broker()
        first = buy(b, "same", 100)
        second = buy(b, "same", 100)
        self.assertFalse(first.duplicate)
        self.assertTrue(second.duplicate)
        self.assertEqual(b.portfolio.cash, D("900"))
        self.assertEqual(len(b.trades()), 1)

    def test_same_id_with_different_parameters_is_rejected(self):
        b, *_ = make_broker()
        buy(b, "same", 100)
        r = buy(b, "same", 200)
        self.assertEqual(r.status, OrderStatus.REJECTED)
        self.assertEqual(b.portfolio.cash, D("900"))

    def test_state_and_idempotency_survive_restart(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "t.db")
            clock = Clock()
            b1, *_ = make_broker(clock=clock, store=SqliteStore(path))
            buy(b1, "o1", 100)
            b2, *_ = make_broker(clock=clock, store=SqliteStore(path))
            self.assertEqual(b2.portfolio.cash, D("900"))
            self.assertIn("TOKEN1", b2.portfolio.positions)
            self.assertEqual(len(b2.trades()), 1)
            again = buy(b2, "o1", 100)
            self.assertTrue(again.duplicate)
            self.assertEqual(b2.portfolio.cash, D("900"))

    def test_audit_chain_detects_tampering_and_redacts_secrets(self):
        b, *_ = make_broker()
        buy(b, "o1", 100)
        self.assertTrue(b.audit.verify_chain())
        b.audit.append("TEST", {"api_key": "SUPERSECRET", "nested": {"private_key": "abc"}, "ok": 1})
        rows = b.audit.recent(5)
        self.assertNotIn("SUPERSECRET", str(rows))
        self.assertNotIn("abc", str(rows[0]["payload"]))
        b._store._db.execute("UPDATE audit SET payload='{}' WHERE id=1")
        self.assertFalse(b.audit.verify_chain())

    def test_redact_function(self):
        self.assertEqual(redact({"Authorization": "Bearer x", "mint": "M"}),
                         {"Authorization": "[REDACTED]", "mint": "M"})


class TestMockAndStaleData(unittest.TestCase):
    def test_mock_prices_rejected_by_default(self):
        b, *_ = make_broker(label=DataLabel.MOCK, allow_mock=False)
        r = buy(b, "o1", 10)
        self.assertEqual(r.status, OrderStatus.REJECTED)
        self.assertTrue(any("MOCK" in x for x in r.reasons))

    def test_mock_trades_are_labelled_when_explicitly_allowed(self):
        b, *_ = make_broker(label=DataLabel.MOCK, allow_mock=True)
        r = buy(b, "o1", 10)
        self.assertEqual(r.status, OrderStatus.FILLED)
        self.assertEqual(r.data_label, "MOCK")
        self.assertEqual(b.trades()[0]["data_label"], "MOCK")
        self.assertTrue(b.snapshot()["simulated"])

    def test_stale_price_rejected(self):
        clock = Clock()
        b, prov, *_ = make_broker(clock=clock)
        prov.get_quote = lambda m: Quote(m, D("1"), START - timedelta(minutes=10), "fake", "LIVE")
        r = buy(b, "o1", 10)
        self.assertEqual(r.status, OrderStatus.REJECTED)
        self.assertTrue(any("stale" in x for x in r.reasons))

    def test_unknown_label_price_rejected(self):
        b, prov, *_ = make_broker()
        prov.get_quote = lambda m: Quote(m, D("1"), START, "fake", "UNKNOWN")
        self.assertEqual(buy(b, "o1", 10).status, OrderStatus.REJECTED)


class TestLiveTradingDisabled(unittest.TestCase):
    def test_only_paper_mode_accepted(self):
        self.assertEqual(assert_paper_mode("paper"), "paper")
        for v in ("live", "LIVE", "", "true", "paper "):
            with self.assertRaises(LiveTradingDisabled):
                assert_paper_mode(v)

    def test_live_executor_cannot_execute(self):
        with self.assertRaises(LiveTradingDisabled):
            LiveExecutor().fill(Order("o", "T", Side.BUY, D("1")), Quote("T", D("1"), START, "x", "LIVE"))

    def test_results_are_always_simulated(self):
        b, *_ = make_broker()
        self.assertTrue(buy(b, "o1", 10).simulated)
        self.assertEqual(b.snapshot()["mode"], "paper")


if __name__ == "__main__":
    unittest.main()
