import asyncio
import json
import unittest

import tests  # noqa: F401
from app.asgi import OfficeApp
from app.bootstrap import build_office
from app.config import Settings
from tests.test_office import Clock, KEY

ENV = {"DATA_PROVIDER": "mock", "ALLOW_MOCK_DATA": "true", "DATABASE_PATH": ":memory:", "API_KEY": KEY * 2,
       "CORS_ORIGINS": "https://office.example.com"}


def run(app, method, path, headers=None, body=b"", query=b""):
    sent = []
    msgs = [{"type": "http.request", "body": body, "more_body": False}]

    async def receive():
        return msgs.pop(0) if msgs else {"type": "http.disconnect"}

    async def send(m):
        sent.append(m)

    scope = {"type": "http", "method": method, "path": path, "query_string": query,
             "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]}
    asyncio.run(app(scope, receive, send))
    start, end = sent[0], sent[1]
    return start["status"], {k.decode(): v.decode() for k, v in start["headers"]}, end["body"]


class TestAsgi(unittest.TestCase):
    def setUp(self):
        self.office = build_office(Settings.from_env(ENV), clock=Clock())
        self.app = OfficeApp(lambda: self.office)
        self.addCleanup(self.office.store.close)

    def test_health_and_query_string(self):
        st, h, b = run(self.app, "GET", "/api/health")
        self.assertEqual(st, 200)
        self.assertFalse(json.loads(b)["live_trading"])
        self.assertEqual(h["content-type"], "application/json")
        st, _, b = run(self.app, "GET", "/api/events", query=b"since=0&limit=5")
        self.assertEqual(st, 200)
        st, _, _ = run(self.app, "GET", "/api/events", query=b"limit=abc")
        self.assertEqual(st, 400)

    def test_auth_required_and_body_passed_through(self):
        st, _, _ = run(self.app, "POST", "/api/scan", body=b'{"mint":"MOCKSAFE"}')
        self.assertEqual(st, 401)
        st, _, b = run(self.app, "POST", "/api/scan", headers={"X-API-Key": KEY * 2}, body=b'{"mint":"MOCKSAFE"}')
        self.assertEqual(st, 200)
        self.assertIn("report", json.loads(b))

    def test_cors_only_for_listed_origin(self):
        _, h, _ = run(self.app, "GET", "/api/health", headers={"Origin": "https://office.example.com"})
        self.assertEqual(h["access-control-allow-origin"], "https://office.example.com")
        _, h, _ = run(self.app, "GET", "/api/health", headers={"Origin": "https://evil.example.com"})
        self.assertNotIn("access-control-allow-origin", h)
        st, h, _ = run(self.app, "OPTIONS", "/api/scan", headers={"Origin": "https://office.example.com"})
        self.assertEqual(st, 204)
        st, _, _ = run(self.app, "OPTIONS", "/api/scan", headers={"Origin": "https://evil.example.com"})
        self.assertEqual(st, 403)

    def test_oversized_body_rejected(self):
        st, _, _ = run(self.app, "POST", "/api/scan", headers={"X-API-Key": KEY * 2}, body=b"x" * 20000)
        self.assertEqual(st, 413)

    def test_startup_failure_on_bad_config(self):
        def bad():
            raise ValueError("TRADING_MODE must be paper")
        app = OfficeApp(bad)
        out = []
        msgs = [{"type": "lifespan.startup"}]

        async def receive():
            return msgs.pop(0)

        async def send(m):
            out.append(m)
        asyncio.run(app({"type": "lifespan"}, receive, send))
        self.assertEqual(out[0]["type"], "lifespan.startup.failed")


if __name__ == "__main__":
    unittest.main()
