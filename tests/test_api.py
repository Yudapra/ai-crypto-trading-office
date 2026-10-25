import json
import unittest
from decimal import Decimal

import tests  # noqa: F401  (sets sys.path)
from app.bootstrap import build_office
from app.config import Settings
from tests.test_office import Clock, KEY, _OFFICES, make_office, tearDownModule  # noqa: F401

AUTH = {"X-API-Key": KEY}


def call(office, method, path, query=None, headers=None, body=None):
    raw = b"" if body is None else (body if isinstance(body, bytes) else json.dumps(body).encode())
    return office.core.dispatch(method, path, query or {}, headers or {}, raw)


class TestReadEndpoints(unittest.TestCase):
    def test_health_reports_paper_mode_and_no_secrets(self):
        o = make_office()
        r = call(o, "GET", "/api/health")
        self.assertEqual(r.status, 200)
        self.assertFalse(r.body["live_trading"])
        self.assertEqual(r.body["trading_mode"], "paper")
        self.assertTrue(r.body["mock_data_in_use"])
        self.assertTrue(r.body["api_key_configured"])
        self.assertNotIn(KEY, json.dumps(r.body))
        self.assertEqual(r.headers["Cache-Control"], "no-store")

    def test_state_has_all_six_robots_rules_and_portfolio(self):
        o = make_office()
        r = call(o, "GET", "/api/state")
        self.assertEqual(r.status, 200)
        self.assertEqual([x["id"] for x in r.body["robots"]],
                         ["ceo", "research_analyst", "market_analyst", "security_analyst", "trader", "accountant"])
        self.assertEqual(r.body["rules"]["max_top10_holder_pct"], "25")
        self.assertEqual(r.body["rules"]["dev_block_pct"], "10")
        self.assertTrue(r.body["portfolio"]["simulated"])
        self.assertFalse(r.body["live_trading"])
        self.assertNotIn(KEY, json.dumps(r.body))

    def test_events_since_and_limit(self):
        o = make_office()
        call(o, "POST", "/api/scan", headers=AUTH, body={"mint": "MOCKSAFE"})
        r = call(o, "GET", "/api/events")
        n = len(r.body["events"])
        self.assertGreater(n, 2)
        self.assertEqual(r.body["latest_seq"], r.body["events"][-1]["seq"])
        mid = r.body["events"][1]["seq"]
        r2 = call(o, "GET", "/api/events", query={"since": str(mid)})
        self.assertEqual(len(r2.body["events"]), n - 2)
        r3 = call(o, "GET", "/api/events", query={"limit": "1"})
        self.assertEqual(len(r3.body["events"]), 1)

    def test_bad_query_parameters_rejected(self):
        o = make_office()
        for q in ({"since": "abc"}, {"since": "-1"}, {"limit": "0"}, {"limit": "9999"}):
            self.assertEqual(call(o, "GET", "/api/events", query=q).status, 400, q)

    def test_report_endpoint_404_then_200_and_validates_mint(self):
        o = make_office()
        self.assertEqual(call(o, "GET", "/api/reports/MOCKSAFE").status, 404)
        call(o, "POST", "/api/scan", headers=AUTH, body={"mint": "MOCKSAFE"})
        r = call(o, "GET", "/api/reports/MOCKSAFE")
        self.assertEqual((r.status, r.body["report"]["decision"]), (200, "PASS"))
        self.assertEqual(call(o, "GET", "/api/reports/bad%20mint").status, 400)

    def test_audit_verify(self):
        o = make_office(auto_buy=True)
        call(o, "POST", "/api/cycle", headers=AUTH, body={})
        r = call(o, "GET", "/api/audit/verify")
        self.assertTrue(r.body["chain_valid"])

    def test_trades_lists_simulated_fills(self):
        o = make_office(auto_buy=True)
        call(o, "POST", "/api/cycle", headers=AUTH, body={})
        r = call(o, "GET", "/api/trades")
        self.assertTrue(r.body["simulated"])
        self.assertEqual(len(r.body["trades"]), 1)


class TestAuthentication(unittest.TestCase):
    PROTECTED = (("POST", "/api/scan", {"mint": "MOCKSAFE"}), ("POST", "/api/cycle", {}),
                 ("POST", "/api/orders", {"mint": "MOCKSAFE", "side": "BUY", "amount": "25"}))

    def test_missing_or_wrong_key_is_401_and_does_nothing(self):
        o = make_office()
        for method, path, body in self.PROTECTED:
            self.assertEqual(call(o, method, path, body=body).status, 401, path)
            self.assertEqual(call(o, method, path, headers={"X-API-Key": "w" * 32}, body=body).status, 401, path)
            self.assertEqual(call(o, method, path, headers={"X-API-Key": ""}, body=body).status, 401, path)
        self.assertEqual(o.service.activity.latest_seq(), 0)  # nothing ran
        self.assertEqual(o.service.portfolio()["positions"], [])

    def test_endpoints_are_disabled_when_no_api_key_is_configured(self):
        env = {"DATA_PROVIDER": "mock", "ALLOW_MOCK_DATA": "true", "DATABASE_PATH": ":memory:"}
        o = build_office(Settings.from_env(env), clock=Clock())
        _OFFICES.append(o)
        for method, path, body in self.PROTECTED:
            r = call(o, method, path, headers=AUTH, body=body)
            self.assertEqual(r.status, 503, path)
            self.assertEqual(r.body["error"]["code"], "api_key_not_configured")

    def test_header_name_is_case_insensitive_and_correct_key_works(self):
        o = make_office()
        r = call(o, "POST", "/api/scan", headers={"x-api-key": KEY}, body={"mint": "MOCKSAFE"})
        self.assertEqual(r.status, 200)

    def test_read_endpoints_need_no_key(self):
        o = make_office()
        for path in ("/api/health", "/api/state", "/api/events", "/api/trades", "/api/audit/verify"):
            self.assertEqual(call(o, "GET", path).status, 200, path)


class TestRoutingAndValidation(unittest.TestCase):
    def test_unknown_path_and_wrong_method(self):
        o = make_office()
        self.assertEqual(call(o, "GET", "/api/nope").status, 404)
        self.assertEqual(call(o, "GET", "/").status, 404)
        r = call(o, "GET", "/api/scan")
        self.assertEqual(r.status, 405)
        self.assertEqual(r.headers["Allow"], "POST")
        self.assertEqual(call(o, "POST", "/api/state").status, 405)
        self.assertEqual(call(o, "DELETE", "/api/orders", headers=AUTH).status, 405)

    def test_trailing_slash_is_accepted(self):
        self.assertEqual(call(make_office(), "GET", "/api/health/").status, 200)

    def test_body_validation(self):
        o = make_office()
        self.assertEqual(call(o, "POST", "/api/scan", headers=AUTH, body=b"{not json").status, 400)
        self.assertEqual(call(o, "POST", "/api/scan", headers=AUTH, body=b"[1,2]").status, 400)
        self.assertEqual(call(o, "POST", "/api/scan", headers=AUTH, body=b"x" * 9000).status, 413)
        self.assertEqual(call(o, "POST", "/api/scan", headers=AUTH, body={}).status, 400)  # mint missing
        self.assertEqual(call(o, "POST", "/api/scan", headers=AUTH, body={"mint": 5}).status, 400)

    def test_order_field_validation(self):
        o = make_office()
        good = {"mint": "MOCKSAFE", "side": "BUY", "amount": "25"}
        self.assertEqual(call(o, "POST", "/api/orders", headers=AUTH, body={**good, "side": "HOLD"}).status, 400)
        for bad in ("-5", "0", "abc", "NaN", "Infinity", "1e99", True, None, [1]):
            r = call(o, "POST", "/api/orders", headers=AUTH, body={**good, "amount": bad})
            self.assertEqual(r.status, 400, repr(bad))
        r = call(o, "POST", "/api/orders", headers=AUTH, body={**good, "client_order_id": "has space"})
        self.assertEqual(r.status, 400)
        self.assertEqual(o.service.portfolio()["positions"], [])

    def test_cycle_auto_buy_must_be_boolean(self):
        o = make_office()
        self.assertEqual(call(o, "POST", "/api/cycle", headers=AUTH, body={"auto_buy": "yes"}).status, 400)

    def test_live_provider_requires_solana_address_format(self):
        env = {"DATA_PROVIDER": "live", "SOLANA_RPC_URL": "https://rpc.example.org", "DATABASE_PATH": ":memory:",
               "API_KEY": KEY}
        o = build_office(Settings.from_env(env), clock=Clock())
        _OFFICES.append(o)
        self.assertEqual(call(o, "POST", "/api/scan", headers=AUTH, body={"mint": "MOCKSAFE"}).status, 400)
        self.assertEqual(call(o, "POST", "/api/scan", headers=AUTH, body={"mint": "../../etc/passwd"}).status, 400)


class TestActions(unittest.TestCase):
    def test_scan_returns_report_and_places_no_order(self):
        o = make_office()
        r = call(o, "POST", "/api/scan", headers=AUTH, body={"mint": "MOCKCONCENTRATED", "order_usd": 10})
        self.assertEqual(r.status, 200)
        self.assertEqual(r.body["report"]["decision"], "BLOCKED")
        self.assertEqual(r.body["report"]["holder_concentration"]["threshold_pct"], "25")
        self.assertEqual(o.service.portfolio()["positions"], [])

    def test_order_filled_is_marked_simulated(self):
        o = make_office()
        r = call(o, "POST", "/api/orders", headers=AUTH,
                 body={"client_order_id": "c1", "mint": "MOCKSAFE", "side": "BUY", "amount": "25"})
        self.assertEqual(r.status, 200)
        self.assertTrue(r.body["simulated"])
        self.assertEqual(r.body["order"]["status"], "FILLED")
        self.assertEqual(r.body["order"]["data_label"], "MOCK")
        self.assertTrue(r.body["order"]["simulated"])

    def test_order_blocked_by_security_is_rejected_not_filled(self):
        o = make_office()
        r = call(o, "POST", "/api/orders", headers=AUTH,
                 body={"mint": "MOCKCONCENTRATED", "side": "BUY", "amount": "25"})
        self.assertEqual(r.status, 200)
        self.assertEqual(r.body["order"]["status"], "REJECTED")
        self.assertEqual(r.body["order"]["security_decision"], "BLOCKED")
        self.assertEqual(o.service.portfolio()["positions"], [])

    def test_duplicate_client_order_id_is_idempotent(self):
        o = make_office()
        body = {"client_order_id": "dup1", "mint": "MOCKSAFE", "side": "BUY", "amount": "25"}
        a = call(o, "POST", "/api/orders", headers=AUTH, body=body)
        b = call(o, "POST", "/api/orders", headers=AUTH, body=body)
        self.assertFalse(a.body["order"]["duplicate"])
        self.assertTrue(b.body["order"]["duplicate"])
        self.assertEqual(len(o.service.portfolio()["positions"]), 1)
        self.assertEqual(Decimal(o.service.portfolio()["positions"][0]["quantity"]),
                         Decimal(a.body["order"]["quantity"]))

    def test_cycle_endpoint_returns_summary(self):
        o = make_office()
        r = call(o, "POST", "/api/cycle", headers=AUTH, body={})
        self.assertEqual(r.status, 200)
        self.assertEqual(r.body["cycle"]["scanned"], 3)
        self.assertEqual(call(o, "GET", "/api/state").body["last_cycle"]["cycle_id"], 1)


class TestErrorHandling(unittest.TestCase):
    def test_unexpected_exception_returns_generic_500_without_details(self):
        o = make_office()

        def boom(*a, **k):
            raise RuntimeError("secret internal path /srv/app/db.sqlite password=hunter2")

        o.service.scan_token = boom
        r = call(o, "POST", "/api/scan", headers=AUTH, body={"mint": "MOCKSAFE"})
        self.assertEqual(r.status, 500)
        text = json.dumps(r.body)
        self.assertNotIn("hunter2", text)
        self.assertNotIn("/srv/app", text)
        self.assertEqual(r.body["error"]["code"], "internal_error")


if __name__ == "__main__":
    unittest.main()
