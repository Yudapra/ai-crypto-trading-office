import threading
import unittest
from datetime import datetime, timezone
from decimal import Decimal

import tests  # noqa: F401  (sets sys.path)
from app.bootstrap import build_office
from app.config import Settings
from app.market_data.base import ProviderUnavailable
from app.services.activity import ActivityLog
from app.services.office import CycleBusy, CycleScheduler, OfficeConfig
from app.services.robots import ROBOTS, RobotRegistry, RobotState
from trading_engine.orders import Order, OrderStatus, Side

START = datetime(2026, 10, 24, 12, 0, 0, tzinfo=timezone.utc)
D = Decimal
KEY = "k" * 32


class Clock:
    def __init__(self):
        self.now = START

    def __call__(self):
        return self.now


_OFFICES = []


def make_office(auto_buy=False, extra_env=None):
    env = {"DATA_PROVIDER": "mock", "ALLOW_MOCK_DATA": "true", "DATABASE_PATH": ":memory:", "API_KEY": KEY,
           "AUTO_BUY_ENABLED": "true" if auto_buy else "false"}
    env.update(extra_env or {})
    office = build_office(Settings.from_env(env), clock=Clock())
    _OFFICES.append(office)
    return office


def tearDownModule():
    while _OFFICES:
        _OFFICES.pop().stop()


def states(office):
    return {r["id"]: r["state"] for r in office.service.robots.snapshot()}


def events(office, **kw):
    return office.service.activity.since(0, 500)


class TestActivityLog(unittest.TestCase):
    def test_sequence_is_increasing_and_since_filters(self):
        log = ActivityLog(Clock())
        a = log.record("ceo", "info", "one")
        b = log.record("ceo", "info", "two")
        self.assertEqual((a.seq, b.seq), (1, 2))
        self.assertEqual([e["message"] for e in log.since(1)], ["two"])
        self.assertEqual(log.latest_seq(), 2)

    def test_ring_buffer_drops_old_events_but_keeps_counting(self):
        log = ActivityLog(Clock(), capacity=3)
        for i in range(10):
            log.record("ceo", "info", f"m{i}")
        got = log.since(0)
        self.assertEqual([e["message"] for e in got], ["m7", "m8", "m9"])
        self.assertEqual(log.latest_seq(), 10)

    def test_secret_keys_are_redacted_and_messages_truncated(self):
        log = ActivityLog(Clock())
        ev = log.record("ceo", "info", "x" * 1000, data={"api_key": "SECRET123", "ok": 1})
        self.assertEqual(len(ev.message), 300)
        self.assertEqual(ev.data["api_key"], "[REDACTED]")
        self.assertNotIn("SECRET123", str(log.since(0)))

    def test_unknown_kind_rejected(self):
        with self.assertRaises(ValueError):
            ActivityLog(Clock()).record("ceo", "bogus", "m")


class TestRobotRegistryListeners(unittest.TestCase):
    def test_listener_called_and_broken_listener_ignored(self):
        reg = RobotRegistry(Clock())
        seen = []
        reg.subscribe(lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
        reg.subscribe(lambda rid, st, detail: seen.append((rid, st, detail)))
        reg.update("trader", RobotState.WORKING, "x")  # must not raise
        self.assertEqual(seen, [("trader", RobotState.WORKING, "x")])
        self.assertEqual(reg.snapshot()[4]["state"], "WORKING")

    def test_unknown_robot_rejected(self):
        with self.assertRaises(KeyError):
            RobotRegistry(Clock()).update("hacker", RobotState.WORKING, "x")


class TestScanTruthfulness(unittest.TestCase):
    def test_fresh_office_has_idle_robots_and_empty_feed(self):
        o = make_office()
        self.assertEqual(set(states(o)), set(ROBOTS))
        self.assertTrue(all(s == "IDLE" for s in states(o).values()))
        self.assertEqual(events(o), [])

    def test_scan_updates_only_the_robots_that_worked(self):
        o = make_office()
        out = o.service.scan_token("MOCKSAFE")
        s = states(o)
        self.assertEqual(out["report"]["decision"], "PASS")
        self.assertEqual(s["market_analyst"], "COMPLETED")
        self.assertEqual(s["security_analyst"], "COMPLETED")
        for idle in ("ceo", "research_analyst", "trader", "accountant"):
            self.assertEqual(s[idle], "IDLE", idle)
        self.assertEqual(o.service.portfolio()["cash_usd"], "1000")

    def test_security_robot_shows_blocked_for_failed_rule(self):
        o = make_office()
        out = o.service.scan_token("MOCKCONCENTRATED")
        self.assertEqual(out["report"]["decision"], "BLOCKED")
        self.assertEqual(states(o)["security_analyst"], "BLOCKED")
        self.assertIn("holder_concentration", " ".join(out["report"]["reasons"]))

    def test_provider_failure_marks_robots_error_not_completed(self):
        o = make_office()
        out = o.service.scan_token("DOESNOTEXIST")
        self.assertIsNone(out["report"])
        self.assertEqual(states(o)["market_analyst"], "ERROR")
        self.assertEqual(states(o)["security_analyst"], "ERROR")

    def test_feed_contains_state_and_decision_events_in_order(self):
        o = make_office()
        o.service.scan_token("MOCKSAFE")
        ev = events(o)
        kinds = [(e["robot_id"], e["kind"]) for e in ev]
        self.assertIn(("market_analyst", "state"), kinds)
        self.assertIn(("security_analyst", "decision"), kinds)
        self.assertEqual([e["seq"] for e in ev], sorted(e["seq"] for e in ev))
        self.assertLess(kinds.index(("market_analyst", "state")), kinds.index(("security_analyst", "decision")))


class TestCycle(unittest.TestCase):
    def test_cycle_without_auto_buy_scans_everything_and_buys_nothing(self):
        o = make_office()
        c = o.service.run_cycle()
        self.assertEqual(c["status"], "completed")
        self.assertEqual(c["scanned"], 3)
        self.assertEqual(c["counts"], {"PASS": 1, "REVIEW_REQUIRED": 0, "BLOCKED": 2, "UNKNOWN": 0})
        self.assertEqual(c["orders"], [])
        self.assertEqual(o.service.portfolio()["positions"], [])
        s = states(o)
        for rid in ("ceo", "research_analyst", "market_analyst", "security_analyst", "accountant"):
            self.assertEqual(s[rid], "COMPLETED", rid)
        self.assertEqual(s["trader"], "IDLE")  # nothing to trade, no fake activity

    def test_cycle_with_auto_buy_buys_only_the_token_that_passed(self):
        o = make_office(auto_buy=True)
        c = o.service.run_cycle()
        self.assertEqual(len(c["orders"]), 1)
        order = c["orders"][0]
        self.assertEqual((order["mint"], order["status"], order["simulated"]), ("MOCKSAFE", "FILLED", True))
        snap = o.service.portfolio()
        self.assertEqual([p["mint"] for p in snap["positions"]], ["MOCKSAFE"])
        self.assertLess(Decimal(snap["cash_usd"]), D("1000"))
        self.assertEqual(states(o)["trader"], "COMPLETED")
        self.assertTrue(any(e["kind"] == "order" and "filled" in e["message"] for e in events(o)))

    def test_auto_buy_can_be_requested_per_cycle_even_when_disabled_by_default(self):
        o = make_office(auto_buy=False)
        self.assertEqual(len(o.service.run_cycle(auto_buy=True)["orders"]), 1)

    def test_max_buys_per_cycle_is_respected(self):
        o = make_office(auto_buy=True)
        o.service.config = OfficeConfig(True, D("25"), 10, 0)
        self.assertEqual(o.service.run_cycle()["orders"], [])

    def test_research_failure_aborts_cycle_with_ceo_error(self):
        o = make_office()

        class Down:
            name = "mock"

            def list_candidates(self):
                raise ProviderUnavailable("down")

        o.service.provider = Down()
        c = o.service.run_cycle()
        self.assertEqual(c["status"], "failed")
        self.assertEqual(states(o)["ceo"], "ERROR")
        self.assertEqual(states(o)["research_analyst"], "ERROR")

    def test_concurrent_cycle_is_rejected(self):
        o = make_office()
        gate_open, started = threading.Event(), threading.Event()
        real = o.service.provider

        class Slow:
            name = "mock"

            def list_candidates(self):
                started.set()
                gate_open.wait(5)
                return real.list_candidates()

            def get_quote(self, mint):
                return real.get_quote(mint)

        o.service.provider = Slow()
        t = threading.Thread(target=o.service.run_cycle)
        t.start()
        self.assertTrue(started.wait(5))
        with self.assertRaises(CycleBusy):
            o.service.run_cycle()
        gate_open.set()
        t.join(5)
        self.assertEqual(o.service.run_cycle()["status"], "completed")  # lock was released


class TestSecurityCannotBeBypassed(unittest.TestCase):
    def test_manual_buy_of_concentrated_token_is_rejected_and_trader_shows_blocked(self):
        o = make_office()
        res = o.service.place_order(Order("t1", "MOCKCONCENTRATED", Side.BUY, D("25")))
        self.assertEqual(res.status, OrderStatus.REJECTED)
        self.assertEqual(res.security_decision, "BLOCKED")
        self.assertEqual(states(o)["trader"], "BLOCKED")
        self.assertEqual(o.service.portfolio()["positions"], [])

    def test_manual_buy_when_sell_simulation_failed_is_rejected(self):
        o = make_office()
        res = o.service.place_order(Order("t2", "MOCKNOSELL", Side.BUY, D("25")))
        self.assertEqual(res.status, OrderStatus.REJECTED)

    def test_filled_buy_then_sell_updates_accountant(self):
        o = make_office()
        buy = o.service.place_order(Order("t3", "MOCKSAFE", Side.BUY, D("25")))
        self.assertEqual(buy.status, OrderStatus.FILLED)
        self.assertEqual(states(o)["accountant"], "COMPLETED")
        sell = o.service.place_order(Order("t4", "MOCKSAFE", Side.SELL, buy.quantity))
        self.assertEqual(sell.status, OrderStatus.FILLED)
        self.assertEqual(o.service.portfolio()["positions"], [])

    def test_audit_chain_stays_valid_after_activity(self):
        o = make_office(auto_buy=True)
        o.service.run_cycle()
        self.assertTrue(o.service.broker.audit.verify_chain())

    def test_service_has_no_live_trading_switch(self):
        o = make_office()
        for name in dir(o.service):
            self.assertNotIn("live", name.lower().replace("deliver", ""), name)


class TestScheduler(unittest.TestCase):
    def test_interval_below_minimum_is_rejected(self):
        with self.assertRaises(ValueError):
            CycleScheduler(make_office().service, 1.0)

    def test_scheduler_runs_cycles_and_stops(self):
        o = make_office()
        done = threading.Event()
        real = o.service.run_cycle

        def counted(*a, **k):
            r = real(*a, **k)
            done.set()
            return r

        o.service.run_cycle = counted
        sch = CycleScheduler(o.service, 0.05, min_interval_s=0.01)
        sch.start()
        self.assertTrue(done.wait(5))
        sch.stop()
        self.assertFalse(sch.running)
        self.assertGreaterEqual(o.service.last_cycle["cycle_id"], 1)


if __name__ == "__main__":
    unittest.main()
