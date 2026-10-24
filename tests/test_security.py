import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from fractions import Fraction

import tests  # noqa: F401  (sets sys.path)
from app.holder_analysis.clusters import WalletLink
from app.holder_analysis.concentration import HolderAccount, HolderSnapshot, analyze_holder_concentration
from app.liquidity_analysis.raydium import LockStatus, LpLockEvidence, lock_status
from app.market_data.mock import MockMarketData, make_evidence, make_holders
from app.models.common import CheckResult, DataLabel, Decision
from app.security.engine import SecurityEngine
from app.security.policy import SecurityPolicy
from app.security.pumpfun import PumpfunInfo, check_developer_holdings
from app.security.token_checks import TokenInfo

NOW = datetime(2026, 10, 24, 12, 0, 0, tzinfo=timezone.utc)
LIVE = DataLabel.LIVE
POLICY = SecurityPolicy()
ENGINE = SecurityEngine(POLICY)


def conc(top10, **kw):
    snap = make_holders("T", Fraction(top10), NOW, LIVE, **kw)
    return analyze_holder_concentration(snap, POLICY, NOW)


def finding(report, name):
    return [f for f in report.findings if f.check == name]


class TestTop10Rule(unittest.TestCase):
    def test_24_9_percent_passes(self):
        r = conc("24.9")
        self.assertEqual(r.result, CheckResult.PASS)
        self.assertEqual(r.raw_top10_pct, Decimal("24.9000"))

    def test_exactly_25_percent_passes(self):
        r = conc("25")
        self.assertEqual(r.result, CheckResult.PASS)
        self.assertEqual(r.raw_top10_pct, Decimal("25.0000"))

    def test_25_1_percent_is_blocked(self):
        r = conc("25.1")
        self.assertEqual(r.result, CheckResult.FAIL)
        rep = ENGINE.assess(make_evidence("T", NOW, label=LIVE, top10_pct=Fraction("25.1")), NOW)
        self.assertEqual(rep.decision, Decision.BLOCKED)
        self.assertFalse(rep.buy_allowed)

    def test_engine_pass_at_boundaries(self):
        for pct in ("24.9", "25"):
            rep = ENGINE.assess(make_evidence("T", NOW, label=LIVE, top10_pct=Fraction(pct)), NOW)
            self.assertEqual(rep.decision, Decision.PASS, pct)
            self.assertTrue(rep.buy_allowed)

    def test_threshold_is_configurable(self):
        pol = SecurityPolicy(max_top10_pct=Decimal("20"))
        snap = make_holders("T", Fraction(21), NOW, LIVE)
        self.assertEqual(analyze_holder_concentration(snap, pol, NOW).result, CheckResult.FAIL)

    def test_missing_holder_data_is_unknown_and_blocks_buy(self):
        ev = replace(make_evidence("T", NOW, label=LIVE), holders=None)
        rep = ENGINE.assess(ev, NOW)
        self.assertEqual(rep.decision, Decision.UNKNOWN)
        self.assertFalse(rep.buy_allowed)

    def test_stale_holder_data_is_unknown(self):
        old = make_holders("T", Fraction(10), NOW - timedelta(minutes=10), LIVE)
        r = analyze_holder_concentration(old, POLICY, NOW)
        self.assertEqual(r.result, CheckResult.UNKNOWN)
        self.assertEqual(r.data_label, DataLabel.STALE)
        rep = ENGINE.assess(replace(make_evidence("T", NOW, label=LIVE), holders=old), NOW)
        self.assertFalse(rep.buy_allowed)

    def test_incomplete_holder_list_is_unknown(self):
        snap = make_holders("T", Fraction(10), NOW, LIVE, complete=False)
        self.assertEqual(analyze_holder_concentration(snap, POLICY, NOW).result, CheckResult.UNKNOWN)

    def test_empty_or_bad_supply_is_unknown(self):
        snap = HolderSnapshot("T", 0, (HolderAccount("a", "o", 1),), NOW, "x", LIVE, True)
        self.assertEqual(analyze_holder_concentration(snap, POLICY, NOW).result, CheckResult.UNKNOWN)
        snap = HolderSnapshot("T", 100, (), NOW, "x", LIVE, True)
        self.assertEqual(analyze_holder_concentration(snap, POLICY, NOW).result, CheckResult.UNKNOWN)

    def test_duplicate_account_listing_counted_once(self):
        base = make_holders("T", Fraction(10), NOW, LIVE)
        dup = replace(base, accounts=base.accounts + (base.accounts[0],))
        r = analyze_holder_concentration(dup, POLICY, NOW)
        ref = analyze_holder_concentration(base, POLICY, NOW)
        self.assertEqual(r.raw_top10_pct, ref.raw_top10_pct)
        self.assertTrue(any("counted once" in n for n in r.notes))

    def test_conflicting_duplicate_is_unknown(self):
        base = make_holders("T", Fraction(10), NOW, LIVE)
        a = base.accounts[0]
        bad = replace(base, accounts=base.accounts + (replace(a, balance=a.balance + 1),))
        self.assertEqual(analyze_holder_concentration(bad, POLICY, NOW).result, CheckResult.UNKNOWN)

    def test_same_owner_accounts_are_aggregated(self):
        accs = [HolderAccount("t1", "whale", 1500), HolderAccount("t2", "whale", 1500)]
        accs += [HolderAccount(f"x{i}", f"o{i}", 100) for i in range(9)]
        snap = HolderSnapshot("T", 10_000, tuple(accs), NOW, "x", LIVE, True)
        r = analyze_holder_concentration(snap, POLICY, NOW)
        self.assertEqual(r.top10_raw[0], ("whale", 3000))
        self.assertEqual(r.raw_top10_pct, Decimal("39.0000"))  # 3000 + 9*100 = 3900
        self.assertEqual(r.result, CheckResult.FAIL)

    def test_balances_exceeding_supply_is_unknown(self):
        accs = (HolderAccount("t1", "a", 700), HolderAccount("t2", "b", 700))
        snap = HolderSnapshot("T", 1000, accs, NOW, "x", LIVE, True)
        self.assertEqual(analyze_holder_concentration(snap, POLICY, NOW).result, CheckResult.UNKNOWN)

    def _pool_snapshot(self, evidence):
        accs = [HolderAccount("pool-ta", "POOL", 4000, "pool_vault", evidence)]
        accs += [HolderAccount(f"ta{i}", f"w{i}", 150) for i in range(10)]
        return HolderSnapshot("T", 10_000, tuple(accs), NOW, "x", LIVE, True)

    def test_verified_pool_vault_reported_both_ways_not_silently_excluded(self):
        snap = self._pool_snapshot("pool state account verified on-chain")
        raw = analyze_holder_concentration(snap, POLICY, NOW)
        self.assertEqual(raw.raw_top10_pct, Decimal("53.5000"))
        self.assertEqual(raw.adjusted_top10_pct, Decimal("15.0000"))
        self.assertEqual(raw.result, CheckResult.FAIL)  # default basis is raw
        self.assertEqual(len(raw.exclusions), 1)
        self.assertIn("verified", raw.exclusions[0].reason)
        self.assertTrue(any("adjusted figure is within the limit" in n for n in raw.notes))
        adj = analyze_holder_concentration(snap, SecurityPolicy(concentration_basis="adjusted"), NOW)
        self.assertEqual(adj.result, CheckResult.PASS)

    def test_unverified_pool_vault_is_never_excluded(self):
        snap = self._pool_snapshot(None)
        r = analyze_holder_concentration(snap, SecurityPolicy(concentration_basis="adjusted"), NOW)
        self.assertEqual(r.exclusions, ())
        self.assertEqual(r.result, CheckResult.FAIL)
        self.assertTrue(any("NOT excluded" in n for n in r.notes))

    def test_bonding_curve_not_treated_as_ordinary_when_verified(self):
        accs = [HolderAccount("bc-ta", "BC", 5000, "bonding_curve", "bonding curve PDA verified")]
        accs += [HolderAccount(f"ta{i}", f"w{i}", 100) for i in range(10)]
        snap = HolderSnapshot("T", 10_000, tuple(accs), NOW, "x", LIVE, True)
        r = analyze_holder_concentration(snap, SecurityPolicy(concentration_basis="adjusted"), NOW)
        self.assertEqual(r.adjusted_top10_pct, Decimal("10.0000"))
        self.assertEqual(r.result, CheckResult.PASS)


class TestDeveloperHoldings(unittest.TestCase):
    def dev(self, pct):
        bal = None if pct is None else int(Fraction(10_000) * Fraction(pct) / 100)
        info = PumpfunInfo("dev", bal, 10_000, True, True, NOW, "x", LIVE)
        return check_developer_holdings(info, POLICY, NOW).result

    def test_thresholds(self):
        self.assertEqual(self.dev("4.9"), CheckResult.PASS)
        self.assertEqual(self.dev("5"), CheckResult.WARN)
        self.assertEqual(self.dev("10"), CheckResult.WARN)
        self.assertEqual(self.dev("10.1"), CheckResult.FAIL)
        self.assertEqual(self.dev(None), CheckResult.UNKNOWN)

    def test_dev_above_10_blocks_by_default(self):
        ev = make_evidence("T", NOW, label=LIVE, venue="pumpfun", dev_pct=Fraction("10.1"))
        rep = ENGINE.assess(ev, NOW)
        self.assertEqual(rep.decision, Decision.BLOCKED)
        self.assertFalse(rep.buy_allowed)

    def test_dev_unknown_blocks(self):
        ev = make_evidence("T", NOW, label=LIVE, venue="pumpfun", dev_pct=None)
        self.assertFalse(ENGINE.assess(ev, NOW).buy_allowed)

    def test_dev_warning_requires_review(self):
        ev = make_evidence("T", NOW, label=LIVE, venue="pumpfun", dev_pct=Fraction(7))
        rep = ENGINE.assess(ev, NOW)
        self.assertEqual(rep.decision, Decision.REVIEW_REQUIRED)
        self.assertFalse(rep.buy_allowed)


class TestSellAndTokenChecks(unittest.TestCase):
    def test_failed_sell_simulation_blocks(self):
        rep = ENGINE.assess(make_evidence("T", NOW, label=LIVE, sell_status="failed"), NOW)
        self.assertEqual(rep.decision, Decision.BLOCKED)
        self.assertFalse(rep.buy_allowed)

    def test_unavailable_sell_simulation_is_unknown_not_safe(self):
        rep = ENGINE.assess(make_evidence("T", NOW, label=LIVE, sell_status="unavailable"), NOW)
        self.assertEqual(rep.decision, Decision.UNKNOWN)

    def test_missing_sell_simulation_is_unknown(self):
        rep = ENGINE.assess(replace(make_evidence("T", NOW, label=LIVE), sell_sim=None), NOW)
        self.assertEqual(rep.decision, Decision.UNKNOWN)

    def test_mint_authority_blocks(self):
        rep = ENGINE.assess(make_evidence("T", NOW, label=LIVE, mint_authority="someone"), NOW)
        self.assertEqual(rep.decision, Decision.BLOCKED)

    def _token(self, **kw):
        base = dict(mint="T", program="token-2022", authorities_known=True, mint_authority=None,
                    freeze_authority=None, extensions=(), transfer_fee_bps=None,
                    transfer_fee_authority=None, fetched_at=NOW, provider="x", label=LIVE)
        base.update(kw)
        return TokenInfo(**base)

    def _assess_token(self, tok):
        return ENGINE.assess(replace(make_evidence("T", NOW, label=LIVE), token=tok), NOW)

    def test_freeze_authority_blocks(self):
        self.assertEqual(self._assess_token(self._token(freeze_authority="f")).decision, Decision.BLOCKED)

    def test_permanent_delegate_and_transfer_hook_block(self):
        for ext in ("permanent_delegate", "transfer_hook", "non_transferable"):
            rep = self._assess_token(self._token(extensions=(ext,)))
            self.assertEqual(rep.decision, Decision.BLOCKED, ext)

    def test_high_transfer_fee_blocks(self):
        tok = self._token(extensions=("transfer_fee_config",), transfer_fee_bps=2500)
        self.assertEqual(self._assess_token(tok).decision, Decision.BLOCKED)

    def test_unknown_extensions_or_program_do_not_pass(self):
        self.assertEqual(self._assess_token(self._token(extensions=None)).decision, Decision.UNKNOWN)
        self.assertEqual(self._assess_token(self._token(program=None)).decision, Decision.UNKNOWN)
        self.assertEqual(self._assess_token(self._token(extensions=("brand_new_ext",))).decision,
                         Decision.REVIEW_REQUIRED)

    def test_clean_token2022_passes(self):
        self.assertEqual(self._assess_token(self._token(extensions=("metadata_pointer",))).decision,
                         Decision.PASS)


class TestLiquidity(unittest.TestCase):
    def test_claimed_lock_without_verification_is_never_locked(self):
        lock = LpLockEvidence(claimed=True, verification=None, method="locker", evidence="sig")
        self.assertEqual(lock_status(lock), LockStatus.UNVERIFIED)
        self.assertEqual(lock_status(LpLockEvidence(True, True, None, None)), LockStatus.UNVERIFIED)
        self.assertEqual(lock_status(LpLockEvidence(True, True, "lp_burn", None)), LockStatus.UNVERIFIED)
        self.assertEqual(lock_status(None), LockStatus.UNVERIFIED)
        self.assertEqual(lock_status(LpLockEvidence(True, True, "lp_burn", "sig")), LockStatus.LOCKED)

    def test_unverified_lock_not_reported_as_pass(self):
        ev = make_evidence("T", NOW, label=LIVE, lock_verified=None)
        rep = ENGINE.assess(ev, NOW)
        f = finding(rep, "lp_lock")[0]
        self.assertNotEqual(f.result, CheckResult.PASS)
        self.assertIn("UNVERIFIED", f.evidence)
        self.assertEqual(rep.decision, Decision.REVIEW_REQUIRED)
        self.assertFalse(rep.buy_allowed)

    def test_require_verified_lock_makes_it_unknown(self):
        eng = SecurityEngine(SecurityPolicy(require_verified_lock=True))
        rep = eng.assess(make_evidence("T", NOW, label=LIVE, lock_verified=None), NOW)
        self.assertEqual(rep.decision, Decision.UNKNOWN)

    def test_low_liquidity_blocks(self):
        rep = ENGINE.assess(make_evidence("T", NOW, label=LIVE, liquidity_usd=Decimal("500")), NOW)
        self.assertEqual(rep.decision, Decision.BLOCKED)

    def test_missing_pool_is_unknown(self):
        rep = ENGINE.assess(replace(make_evidence("T", NOW, label=LIVE), pool=None), NOW)
        self.assertEqual(rep.decision, Decision.UNKNOWN)

    def test_unknown_pool_type_is_unknown(self):
        ev = make_evidence("T", NOW, label=LIVE)
        rep = ENGINE.assess(replace(ev, pool=replace(ev.pool, pool_type="unknown")), NOW)
        self.assertEqual(rep.decision, Decision.UNKNOWN)

    def test_sudden_liquidity_removal_blocks(self):
        ev = make_evidence("T", NOW, label=LIVE)
        rep = ENGINE.assess(replace(ev, pool=replace(ev.pool, liquidity_removed_pct_recent=Decimal("60"))), NOW)
        self.assertEqual(rep.decision, Decision.BLOCKED)

    def test_bonding_curve_token_needs_review(self):
        ev = make_evidence("T", NOW, label=LIVE, venue="pumpfun", dev_pct=Fraction(1))
        ev = replace(ev, pool=None, pumpfun=replace(ev.pumpfun, migrated=False))
        rep = ENGINE.assess(ev, NOW)
        self.assertEqual(rep.decision, Decision.REVIEW_REQUIRED)
        self.assertFalse(rep.buy_allowed)


class TestClustersAndMock(unittest.TestCase):
    def test_direct_cluster_over_limit_requires_review_or_blocks(self):
        links = tuple(WalletLink(f"T-w{i}", f"T-w{i+1}", "shared_funder", f"funder tx {i}") for i in range(9))
        ev = replace(make_evidence("T", NOW, label=LIVE, top10_pct=Fraction(20)), wallet_links=links)
        review = SecurityEngine(SecurityPolicy(cluster_max_pct=Decimal("10"))).assess(ev, NOW)
        self.assertEqual(review.decision, Decision.REVIEW_REQUIRED)
        block = SecurityEngine(SecurityPolicy(cluster_max_pct=Decimal("10"), cluster_policy="block")).assess(ev, NOW)
        self.assertEqual(block.decision, Decision.BLOCKED)
        # the mandatory raw top-10 result is unaffected by cluster analysis
        self.assertEqual(finding(review, "holder_concentration")[0].result, CheckResult.PASS)

    def test_inferred_links_do_not_form_clusters(self):
        links = tuple(WalletLink(f"T-w{i}", f"T-w{i+1}", "inferred", "similar timing") for i in range(9))
        ev = replace(make_evidence("T", NOW, label=LIVE, top10_pct=Fraction(20)), wallet_links=links)
        rep = SecurityEngine(SecurityPolicy(cluster_max_pct=Decimal("10"))).assess(ev, NOW)
        self.assertEqual(rep.decision, Decision.PASS)
        self.assertIn("inferred", finding(rep, "related_wallet_clusters")[0].evidence)

    def test_mock_data_blocked_by_default_and_labelled_when_allowed(self):
        ev = make_evidence("T", NOW)  # label MOCK
        rep = ENGINE.assess(ev, NOW)
        self.assertEqual(rep.decision, Decision.UNKNOWN)
        self.assertFalse(rep.buy_allowed)
        allowed = SecurityEngine(SecurityPolicy(allow_mock_data=True)).assess(ev, NOW)
        self.assertEqual(allowed.decision, Decision.PASS)
        self.assertEqual(allowed.data_label, DataLabel.MOCK)
        self.assertEqual(allowed.to_dict()["data_label"], "MOCK")

    def test_mock_provider_labels_everything_mock(self):
        prov = MockMarketData(clock=lambda: NOW)
        self.assertEqual(prov.get_quote("MOCKSAFE").label, "MOCK")
        self.assertIn(DataLabel.MOCK, {lab for lab in prov.get_evidence("MOCKSAFE", Decimal(1)).labels()})

    def test_policy_validation(self):
        with self.assertRaises(ValueError):
            SecurityPolicy(max_top10_pct=Decimal("150"))
        with self.assertRaises(ValueError):
            SecurityPolicy(concentration_basis="whatever")
        with self.assertRaises(ValueError):
            SecurityPolicy(dev_warn_pct=Decimal("20"), dev_block_pct=Decimal("10"))

    def test_internal_error_becomes_unknown(self):
        ev = make_evidence("T", NOW, label=LIVE)
        broken = replace(ev, holders=HolderSnapshot("T", 100, (object(),), NOW, "x", LIVE, True))
        rep = ENGINE.assess(broken, NOW)
        self.assertEqual(rep.decision, Decision.UNKNOWN)
        self.assertFalse(rep.buy_allowed)


if __name__ == "__main__":
    unittest.main()
