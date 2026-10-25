"""Live provider tests. A fake HTTP client replays RECORDED-SHAPE fixtures: these prove parsing and the safety
behaviour, not that the real services return exactly this (the sandbox had no network access to them)."""
import unittest
from datetime import datetime, timezone
from decimal import Decimal

import tests  # noqa: F401
from app.config import LiveSettings
from app.market_data.base import ProviderUnavailable
from app.market_data.http import ProviderNotFound
from app.market_data.live import LiveMarketData
from app.models.common import Decision
from app.security.engine import SecurityEngine

NOW = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)
MINT = "So11111111111111111111111111111111111111112"
CREATOR = "CreatorWa11et1111111111111111111111111111111"
SUPPLY = 1_000_000_000_000


class FakeHttp:
    def __init__(self, dev_pct=2, pump_known=True, tvl=50000, mint_authority=None):
        self.dev_pct, self.pump_known, self.tvl, self.mint_authority = dev_pct, pump_known, tvl, mint_authority
        self.calls = []

    def get_json(self, url, headers=None, timeout=10.0):
        self.calls.append(url)
        if "/mint/price" in url:
            return {"id": "x", "success": True, "data": {MINT: "0.0123"}}
        if "/pools/info/mint" in url:
            return {"id": "x", "success": True, "data": {"count": 2, "data": [
                {"id": "poolSmall", "programId": "CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C", "tvl": 100},
                {"id": "poolBig", "programId": "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8", "tvl": self.tvl}]}}
        if "/coins/" in url:
            if not self.pump_known:
                raise ProviderNotFound("not found")
            return {"creator": CREATOR, "complete": False}
        raise AssertionError(url)

    def post_json(self, url, body, headers=None, timeout=10.0):
        m = body["method"]
        if m == "getAccountInfo":
            return {"result": {"value": {"owner": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA", "data": {
                "program": "spl-token", "parsed": {"type": "mint", "info": {
                    "supply": str(SUPPLY), "mintAuthority": self.mint_authority, "freezeAuthority": None}}}}}}
        if m == "getTokenLargestAccounts":
            return {"result": {"value": [{"address": f"acc{i}", "amount": str(SUPPLY // 100)} for i in range(20)]}}
        if m == "getMultipleAccounts":
            return {"result": {"value": [{"data": {"parsed": {"info": {"owner": f"owner{i}", "mint": MINT}}}}
                                         for i in range(len(body["params"][0]))]}}
        if m == "getTokenAccountsByOwner":
            return {"result": {"value": [{"account": {"data": {"parsed": {"info": {
                "mint": MINT, "tokenAmount": {"amount": str(SUPPLY * self.dev_pct // 100)}}}}}}]}}
        raise AssertionError(m)


def provider(http, pump=True):
    s = LiveSettings(solana_rpc_url="https://rpc.example.org", watchlist=(MINT,), pumpfun_enabled=pump)
    return LiveMarketData(s, lambda: NOW, http)


class TestLiveProvider(unittest.TestCase):
    def test_quote_and_candidates(self):
        p = provider(FakeHttp())
        self.assertEqual(p.list_candidates(), [MINT])
        q = p.get_quote(MINT)
        self.assertEqual((q.price_usd, q.label), (Decimal("0.0123"), "LIVE"))

    def test_evidence_picks_deepest_pool_and_estimates_impact(self):
        ev = provider(FakeHttp()).get_evidence(MINT, Decimal("25"))
        self.assertEqual(ev.pool.pool_id, "poolBig")
        self.assertEqual(ev.pool.pool_type, "amm_v4")
        self.assertEqual(ev.pool.price_impact_pct, Decimal("0.0999"))  # 25 / (25000 + 25) * 100
        self.assertIsNone(ev.pool.lock.verification)  # never claims a lock it did not verify
        self.assertFalse(ev.holders.complete)
        self.assertIsNone(ev.sell_sim)
        self.assertEqual(ev.venue, "pumpfun")
        self.assertEqual(ev.pumpfun.developer_balance, SUPPLY * 2 // 100)

    def test_clean_live_token_is_never_buyable_without_holder_and_sell_data(self):
        ev = provider(FakeHttp()).get_evidence(MINT, Decimal("25"))
        report = SecurityEngine(__import__("app.security.policy", fromlist=["x"]).SecurityPolicy()).assess(ev, NOW)
        self.assertEqual(report.decision, Decision.UNKNOWN)
        self.assertFalse(report.buy_allowed)

    def test_developer_over_10_percent_blocks(self):
        ev = provider(FakeHttp(dev_pct=11)).get_evidence(MINT, Decimal("25"))
        from app.security.policy import SecurityPolicy
        report = SecurityEngine(SecurityPolicy()).assess(ev, NOW)
        self.assertEqual(report.decision, Decision.BLOCKED)
        self.assertFalse(report.buy_allowed)

    def test_mint_authority_blocks(self):
        ev = provider(FakeHttp(mint_authority="Auth1111111111111111111111111111111111111")).get_evidence(MINT, Decimal("25"))
        from app.security.policy import SecurityPolicy
        self.assertEqual(SecurityEngine(SecurityPolicy()).assess(ev, NOW).decision, Decision.BLOCKED)

    def test_pumpfun_not_called_unless_opted_in(self):
        http = FakeHttp()
        ev = provider(http, pump=False).get_evidence(MINT, Decimal("25"))
        self.assertIsNone(ev.pumpfun)
        self.assertFalse(any("/coins/" in c for c in http.calls))

    def test_unknown_to_pumpfun_means_raydium_venue(self):
        ev = provider(FakeHttp(pump_known=False)).get_evidence(MINT, Decimal("25"))
        self.assertEqual(ev.venue, "raydium")
        self.assertIsNone(ev.pumpfun)

    def test_no_pool_is_none_not_invented(self):
        class NoPool(FakeHttp):
            def get_json(self, url, headers=None, timeout=10.0):
                if "/pools/info/mint" in url:
                    return {"id": "x", "success": True, "data": {"count": 0, "data": []}}
                return super().get_json(url, headers, timeout)
        self.assertIsNone(provider(NoPool()).get_evidence(MINT, Decimal("25")).pool)

    def test_malformed_responses_raise_unavailable(self):
        class Bad(FakeHttp):
            def get_json(self, url, headers=None, timeout=10.0):
                return {"success": False}
            def post_json(self, url, body, headers=None, timeout=10.0):
                return {"error": {"code": 429}}
        p = provider(Bad())
        with self.assertRaises(ProviderUnavailable):
            p.get_quote(MINT)
        with self.assertRaises(ProviderUnavailable):
            p.get_evidence(MINT, Decimal("25"))


if __name__ == "__main__":
    unittest.main()
