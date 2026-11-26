"""Month-to-date months: CLI linear whole-month estimates, exact funnel run-rates, raw on request."""

from contextlib import contextmanager, redirect_stdout
from datetime import datetime, timezone
import io
import json
import unittest
from unittest.mock import patch

from fixtures import FixtureReaderStore, demo_payload
from growthos.analysis import linear_full_month_estimate
from growthos.cli import main
from growthos.contracts import validate_data
from growthos.evidence import month_to_date


def partial_payload(month: str = "2026-10", day: int = 25) -> dict:
    """One stored month cut at `day` (extracted the next day). Fixture FTU: Sep Harbor 30/Vale 10, Oct 40/20."""
    raw = demo_payload()
    meta = next(m for m in raw["month_metadata"] if m["month"] == month)
    meta["extracted_at"] = f"{month}-{day + 1:02d}T06:00:00+00:00"
    meta["cutoff_date"] = f"{month}-{day:02d}"
    for read in meta["source_reads"]:
        read["started_at"] = f"{month}-{day + 1:02d}T05:00:00+00:00"
        read["completed_at"] = f"{month}-{day + 1:02d}T05:05:00+00:00"
    return raw


def reader_for(raw: dict):
    @contextmanager
    def reader():
        yield FixtureReaderStore(validate_data(raw))
    return reader


partial_reader = reader_for(partial_payload())


class MonthToDateTests(unittest.TestCase):
    def setUp(self):
        patcher = patch("growthos.postgres.DEFAULT_ENV_FILE", None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def invoke(self, arguments, raw=None):
        output = io.StringIO()
        reader = partial_reader if raw is None else reader_for(raw)
        with patch("growthos.cli.connect_reader", reader), redirect_stdout(output):
            code = main(arguments)
        return code, json.loads(output.getvalue())

    def stored_checksum(self, raw=None):
        """Checksum of the stored (unscaled) Sep-Oct read, as `verify` reports it."""
        code, verified = self.invoke(["verify", "--start", "2026-09", "--end", "2026-10"], raw)
        self.assertEqual(code, 0)
        return verified["result_checksum"]

    def test_scale_and_month_ended_flag(self):
        october = partial_payload()["month_metadata"]
        during = datetime(2026, 10, 26, 6, tzinfo=timezone.utc)
        after = datetime(2026, 11, 2, tzinfo=timezone.utc)
        self.assertEqual(month_to_date(october, now=during), [{
            "month": "2026-10", "cutoff_date": "2026-10-25", "days_covered": 25, "days_in_month": 31,
            "linear_scale": "31/25", "linear_scale_factor": 1.24, "month_ended": False}])
        self.assertTrue(month_to_date(october, now=after)[0]["month_ended"])
        self.assertEqual(month_to_date(demo_payload()["month_metadata"]), [])

    def test_estimate_scales_every_month_to_date_month_once(self):
        estimated, partials = linear_full_month_estimate(validate_data(partial_payload()))
        ftu = {(r["month"], r["city_key"]): r["platform_ftu"] for r in estimated.data["facts"]}
        # Unrounded: 40 x 31/25 = 49.6, 20 x 31/25 = 24.8; complete September untouched.
        self.assertEqual((ftu[("2026-10", "harbor")], ftu[("2026-10", "vale")]), (49.6, 24.8))
        self.assertEqual((ftu[("2026-09", "harbor")], ftu[("2026-09", "vale")]), (30, 10))
        self.assertEqual([p["month"] for p in partials], ["2026-10"])

    def test_rank_orders_on_estimated_deltas(self):
        # Raw Harbor/Vale deltas tie at +10; estimated deltas are +19.6 and +14.8.
        code, ranked = self.invoke(["rank", "--metric", "platform_ftu", "--top", "2"])
        self.assertEqual(code, 0)
        self.assertEqual([(r["city"], r["latest"], r["absolute_delta"]) for r in ranked["rows"]],
                         [("Harbor", 49.6, 19.6), ("Vale", 24.8, 14.8)])
        code, raw = self.invoke(["rank", "--metric", "platform_ftu", "--top", "2", "--raw-month-to-date"])
        self.assertEqual(sorted((r["city"], r["latest"], r["absolute_delta"]) for r in raw["rows"]),
                         [("Harbor", 40, 10), ("Vale", 20, 10)])

    def test_period_commands_use_estimates_and_keep_stored_checksum(self):
        for arguments in (["rank"], ["compare"], ["funnel"], ["share-drivers"]):
            with self.subTest(command=arguments[0]):
                code, output = self.invoke(arguments)
                self.assertEqual(code, 0)
                self.assertEqual(output["month_to_date"][0]["basis"], "estimate")
                self.assertEqual(output["result_checksum"], self.stored_checksum())
                self.assertTrue(any("CLI linear whole-month estimates" in w for w in output["warnings"]))
                self.assertFalse(any("Rerun without --raw-month-to-date" in w for w in output["warnings"]))

    def test_verify_and_status_report_raw_basis(self):
        code, verified = self.invoke(["verify"])
        self.assertEqual(code, 0)
        self.assertEqual(verified["month_to_date"][0]["basis"], "raw")
        stored = validate_data({**partial_payload(), "months": ["2026-09", "2026-10"],
                                "month_metadata": partial_payload()["month_metadata"][1:],
                                "facts": [r for r in partial_payload()["facts"] if r["month"] != "2026-08"]})
        self.assertEqual(verified["result_checksum"], stored.result_checksum)
        code, status = self.invoke(["status"])
        self.assertTrue(any("is month-to-date through 2026-10-25" in w for w in status["warnings"]))

    def test_default_comparison_uses_latest_stored_month_even_when_partial(self):
        code, output = self.invoke(["compare"])
        self.assertEqual(code, 0)
        self.assertEqual(output["effective_periods"], {"previous": "2026-09", "latest": "2026-10"})

    def test_complete_months_are_unchanged(self):
        code, output = self.invoke(["compare", "--previous", "2026-08", "--latest", "2026-09"])
        self.assertEqual(code, 0)
        self.assertNotIn("month_to_date", output)
        self.assertFalse(any("month-to-date" in w for w in output["warnings"]))
        code, output = self.invoke(["target-facts", "--month", "2026-09"])
        self.assertNotIn("month_to_date", output)
        self.assertEqual({r["city"]: r["actual"] for r in output["rows"]}, {"Harbor": 30, "Vale": 10})

    def test_targets_compare_whole_month_targets_with_estimates(self):
        code, output = self.invoke(["target-facts", "--month", "2026-10"])
        self.assertEqual(code, 0)
        self.assertEqual({r["city"]: r["actual"] for r in output["rows"]}, {"Harbor": 49.6, "Vale": 24.8})
        self.assertEqual(output["month_to_date"][0]["basis"], "estimate")
        self.assertEqual(output["result_checksum"], self.stored_checksum())
        code, output = self.invoke(["target-analysis", "--month", "2026-10", "--mode", "all"])
        self.assertEqual(code, 0)

    def test_month_to_date_prior_month_is_estimated_for_targets(self):
        # September stored month-to-date through the 10th: Harbor 30 x 30/10 = 90, Vale 10 x 3 = 30.
        raw = partial_payload("2026-09", 10)
        code, output = self.invoke(["target-facts", "--month", "2026-10"], raw)
        self.assertEqual(code, 0)
        self.assertEqual([(m["month"], m["basis"]) for m in output["month_to_date"]], [("2026-09", "estimate")])
        estimated, _ = linear_full_month_estimate(validate_data(raw))
        september = {r["city_key"]: r["platform_ftu"] for r in estimated.data["facts"] if r["month"] == "2026-09"}
        self.assertEqual(september, {"harbor": 90.0, "vale": 30.0})

    def test_funnel_compares_exact_daily_rates_without_rounding(self):
        code, funnel = self.invoke(["investigate-funnel", "--month", "2026-10", "--city", "Harbor"])
        self.assertEqual(code, 0)
        block = funnel["blocks"][0]
        self.assertEqual(funnel["month_to_date"][0]["basis"], "daily_rate")
        self.assertEqual(block["current_ftu_raw"], 40)  # raw month-to-date, never rounded
        adjustment = block["calendar_adjustment"]
        self.assertEqual((adjustment["prior_days"], adjustment["current_days"]), (30, 25))
        # Daily rates 40/25 vs 30/30 -> +60%, identical to the unrounded estimate 49.6 x 30/31 vs 30.
        self.assertAlmostEqual(block["ftu_change_pct"], 60.0)

    def test_funnel_with_month_to_date_prior_month(self):
        code, funnel = self.invoke(["investigate-funnel", "--month", "2026-10", "--city", "Harbor"],
                                   partial_payload("2026-09", 10))
        self.assertEqual(code, 0)
        adjustment = funnel["blocks"][0]["calendar_adjustment"]
        self.assertEqual((adjustment["prior_days"], adjustment["current_days"]), (10, 31))
        self.assertEqual(adjustment["month_to_date_months"], ["2026-09"])

    def test_investigate_uses_estimates(self):
        code, output = self.invoke(["investigate", "--month", "2026-10", "--city", "Harbor"])
        self.assertEqual(code, 0)
        self.assertEqual(output["blocks"][0]["ftu_actual"], 49.6)
        self.assertEqual(output["month_to_date"][0]["basis"], "estimate")

    def test_ratios_and_service_splits_are_unchanged_by_estimates(self):
        # Tiny counts that rounding would distort: 3 FTU split 1/1/1, 2 registrations from 3 installs, scale 30/20.
        raw = partial_payload("2026-10", 20)
        for row in raw["facts"]:
            if row["month"] == "2026-10" and row["city_key"] == "vale":
                row.update(installs=3, registrations=2, platform_ftu=3, platform_link_ftu=1,
                           platform_auto_ftu=1, platform_cab_ftu=1, fe_sessions=3, rr=1, net=1)
        _, estimated = self.invoke(["compare", "--group-by", "city", "--city", "Vale"], raw)
        _, raw_out = self.invoke(["compare", "--group-by", "city", "--city", "Vale", "--raw-month-to-date"], raw)
        ratios = lambda out: {k: v["latest"] for k, v in out["rows"][0]["ratios"].items()}
        for metric, value in ratios(raw_out).items():
            self.assertAlmostEqual(ratios(estimated)[metric], value, places=12, msg=metric)
        shares = ratios(estimated)
        self.assertAlmostEqual(shares["link_share"] + shares["auto_share"] + shares["cab_share"], 1.0, places=12)

    def test_status_and_raw_flag_on_every_period_command(self):
        _, status = self.invoke(["status"])
        self.assertEqual(status["month_to_date"][0]["basis"], "raw")
        for command in ("compare", "rank", "funnel", "share-drivers"):
            with self.subTest(command=command):
                code, output = self.invoke([command, "--raw-month-to-date"])
                self.assertEqual(code, 0)
                self.assertEqual(output["month_to_date"][0]["basis"], "raw")

    def test_target_threshold_is_exact_on_estimates(self):
        # September cut at the 15th (scale 30/15 = 2); Harbor target 80. 38 -> 76 is exactly -5% (not material);
        # 37 -> 74 is -7.5% (material).
        for raw_ftu, qualifies in ((38, False), (37, True)):
            raw = partial_payload("2026-09", 15)
            for row in raw["facts"]:
                if row["month"] == "2026-09" and row["city_key"] == "harbor":
                    row["platform_ftu"] = raw_ftu
            with self.subTest(raw_ftu=raw_ftu):
                code, facts = self.invoke(["target-facts", "--month", "2026-09", "--city", "Harbor"], raw)
                self.assertEqual(code, 0)
                self.assertEqual(facts["rows"][0]["actual"], 2.0 * raw_ftu)
                code, shortlist = self.invoke(["target-analysis", "--month", "2026-09", "--mode", "all"], raw)
                self.assertEqual(code, 0)
                self.assertEqual("Harbor" in [r["city"] for r in shortlist["rows"]], qualifies)

    def test_funnel_registration_check_and_services_use_covered_days(self):
        raw = partial_payload()
        for row in raw["facts"]:
            if row["month"] == "2026-10" and row["city_key"] == "harbor":
                row.update(platform_ftu=10, fe_sessions=300, rr=150, net=100, registrations=20)
        code, funnel = self.invoke(["investigate-funnel", "--month", "2026-10", "--city", "Harbor"], raw)
        self.assertEqual(code, 0)
        block = funnel["blocks"][0]
        self.assertAlmostEqual(block["ftu_change_pct"], -60.0)  # 10/25 per day vs 30/30
        check = block["registration_check"]
        self.assertEqual(check["verdict"], "registration_dip")
        self.assertAlmostEqual(check["registrations_change_pct"], -52.0)  # 20/25 per day vs 50/30
        self.assertIn("rr2net", block["primary_stages"])
        self.assertTrue(block["service_breakdown"])

    def test_scopes_and_history_ranges_with_month_to_date(self):
        for arguments in (["investigate-funnel", "--month", "2026-10", "--national"],
                          ["investigate-funnel", "--month", "2026-10", "--tier", "T1"],
                          ["investigate", "--month", "2026-10", "--national"],
                          ["investigate", "--month", "2026-10", "--tier", "T1"],
                          ["investigate", "--month", "2026-10", "--start", "2026-09", "--end", "2026-10", "--city", "Harbor"],
                          ["investigate-funnel", "--month", "2026-10", "--start", "2026-09", "--end", "2026-10", "--city", "Harbor"]):
            with self.subTest(arguments=" ".join(arguments)):
                code, output = self.invoke(arguments)
                self.assertEqual(code, 0)
                self.assertEqual([m["month"] for m in output["month_to_date"]], ["2026-10"])

    def test_low_coverage_and_ended_month_warnings(self):
        code, output = self.invoke(["compare"], partial_payload("2026-10", 3))
        self.assertEqual(code, 0)
        self.assertTrue(any("covers only 3 days" in w for w in output["warnings"]))
        self.assertTrue(any("has ended but is still stored month-to-date" in w for w in output["warnings"]))


if __name__ == "__main__":
    unittest.main()
