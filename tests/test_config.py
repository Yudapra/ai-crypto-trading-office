import unittest
from decimal import Decimal

import tests  # noqa: F401
from app.config import ConfigError, Settings
from trading_engine.mode import LiveTradingDisabled


class TestSettings(unittest.TestCase):
    def test_defaults_are_safe(self):
        s = Settings.from_env({})
        self.assertEqual(s.trading_mode, "paper")
        self.assertFalse(s.allow_mock_data)
        self.assertEqual(s.policy.max_top10_pct, Decimal("25"))
        self.assertIsNone(s.api_key)

    def test_live_mode_cannot_be_enabled_by_configuration(self):
        for v in ("live", "LIVE", "real", "1"):
            with self.assertRaises(LiveTradingDisabled):
                Settings.from_env({"TRADING_MODE": v})

    def test_mock_provider_requires_explicit_allow(self):
        with self.assertRaises(ConfigError):
            Settings.from_env({"DATA_PROVIDER": "mock"})
        s = Settings.from_env({"DATA_PROVIDER": "mock", "ALLOW_MOCK_DATA": "true"})
        self.assertTrue(s.policy.allow_mock_data and s.broker.allow_mock_data)

    def test_invalid_values_rejected(self):
        for env in ({"MAX_TOP10_HOLDER_PCT": "abc"}, {"MAX_TOP10_HOLDER_PCT": "150"},
                    {"MAX_POSITION_PCT": "0"}, {"API_KEY": "short"}, {"DATA_PROVIDER": "gmgn"},
                    {"ALLOW_MOCK_DATA": "maybe"}, {"PAPER_STARTING_BALANCE_USD": "-5"}):
            with self.assertRaises(ConfigError, msg=str(env)):
                Settings.from_env(env)

    def test_api_key_not_in_repr(self):
        s = Settings.from_env({"API_KEY": "x" * 32})
        self.assertNotIn("x" * 32, repr(s))


if __name__ == "__main__":
    unittest.main()
