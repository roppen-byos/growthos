import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from fixtures import FixtureReaderStore, demo_payload
from growthos.classification import normalize_city_key
from growthos.cli import main
from growthos.contracts import validate_data
from growthos.evidence import (
    FUNNEL_CALENDAR_SCALING_WARNING,
    FUNNEL_NATIONAL_MATCHED_WARNING,
    FUNNEL_OVER_EXPLAINED_WARNING,
    FUNNEL_PROXY_WARNING,
)
from growthos.funnel_investigation import investigate_funnel


def dataset(rows):
    """Build detached MonthlyData from (month, city, tier, ftu, fe, rr, net) tuples."""
    raw = demo_payload()
    months = sorted({row[0] for row in rows})
    raw["months"] = months
    base_meta = raw["month_metadata"][-1]
    raw["month_metadata"] = [
        {
            **base_meta,
            "month": m,
            "source_reads": [{**item, "month": m} for item in base_meta["source_reads"]],
        }
        for m in months
    ]
    base_fact = raw["facts"][-1]
    raw["facts"] = [
        {
            **base_fact,
            "month": m,
            "city": city,
            "city_key": normalize_city_key(city),
            "source_cities": [city],
            "tier": tier,
            "platform_ftu": ftu,
            "fe_sessions": fe,
            "rr": rr,
            "net": net,
        }
        for m, city, tier, ftu, fe, rr, net in rows
    ]
    return validate_data(raw)


def only(result, kind, label=None, month_value=None):
    found = [
        block for block in result["blocks"]
        if block["kind"] == kind
        and (label is None or block["label"] == label)
        and (month_value is None or block["month"] == month_value)
    ]
    if len(found) != 1:
        raise AssertionError(f"expected one {kind} block, found {len(found)}")
    return found[0]


class FunnelInvestigationCalculationTests(unittest.TestCase):
    def test_strict_twenty_percent_boundary_uses_exact_rationals(self):
        # Jul (31d) -> Aug (31d): no calendar scaling so integer ratios are transparent.
        # Alpha: FTU 1000 -> 800 (-200). FE drops 4%: effect = -40, contribution = 40/200 = exact 20%.
        # Bravo: FTU 10^15 -> 8*10^14 (-2*10^14). FE drops just over 4% at 64-bit boundary.
        rows = [
            ("2026-07", "Alpha", "T1", 1000, 10000, 5000, 4000),
            ("2026-08", "Alpha", "T1", 800, 9600, 4800, 3840),
            ("2026-07", "Bravo", "T1", 10**15, 5 * 10**15, 2 * 10**15, 10**15),
            ("2026-08", "Bravo", "T1", 8 * 10**14, 48 * 10**14 - 1, 2 * 10**15, 10**15),
        ]
        result = investigate_funnel(dataset(rows), requested_months=["2026-08"], cities=["Alpha", "Bravo"])
        alpha = only(result, "city", "Alpha")
        bravo = only(result, "city", "Bravo")

        self.assertEqual(alpha["stages"]["fe"]["contribution_pct"], 20.0)
        self.assertEqual(alpha["stages"]["fe"]["verdict"], "below_threshold")
        self.assertFalse(alpha["stages"]["fe"]["material"])
        self.assertEqual(alpha["stages"]["fe"]["reason"], "contribution_at_or_below_threshold")
        self.assertNotIn("trigger_registration_check", alpha)
        self.assertEqual(result["thresholds"]["registration_gate"], "none_independent_target_lens")
        self.assertEqual(alpha["verdict"], "no_material_funnel_contributor")
        self.assertNotIn(FUNNEL_CALENDAR_SCALING_WARNING, result["warnings"])
        self.assertIn(FUNNEL_PROXY_WARNING, result["warnings"])

        self.assertEqual(bravo["stages"]["fe"]["verdict"], "material_contributor")
        self.assertTrue(bravo["stages"]["fe"]["material"])
        self.assertEqual(bravo["verdict"], "contributors_identified")
        self.assertEqual(bravo["material_stages"], ["fe"])

    def test_shorter_month_proportional_scaling_exposes_october_daily_dip(self):
        # Sep (30d): FTU 30,000, FE 300,000, RR 150,000 (50%), Net 120,000 (80%).
        # Oct (31d): FTU 30,070 (+70 raw, but 30,070 * 30/31 = 29,100 -> -900 dip on 30d scale!),
        # FE 300,700 (300,700 * 30/31 = 291,000 -> -3% FE -> effect = -900 -> 100% FE),
        # RR 150,350 (FE2RR still exact 50%), Net 120,280 (RR2Net still exact 80%).
        rows = [
            ("2026-09", "Alpha", "T1", 30000, 300000, 150000, 120000),
            ("2026-10", "Alpha", "T1", 30070, 300700, 150350, 120280),
        ]
        result = investigate_funnel(dataset(rows), requested_months=["2026-10"], cities=["Alpha"])
        block = only(result, "city", "Alpha")

        self.assertTrue(block["calendar_adjustment"]["adjusted"])
        self.assertEqual(block["calendar_adjustment"]["reference_days"], 30)
        self.assertEqual(block["calendar_adjustment"]["prior_scale"], 1.0)
        self.assertAlmostEqual(block["calendar_adjustment"]["current_scale"], 30 / 31)
        self.assertEqual(block["prior_ftu_scaled"], 30000.0)
        self.assertAlmostEqual(block["current_ftu_scaled"], 29100.0)
        self.assertAlmostEqual(block["ftu_change"], -900.0)
        self.assertAlmostEqual(block["stages"]["fe"]["contribution_pct"], 100.0)
        self.assertEqual(block["stages"]["fe"]["verdict"], "material_contributor")
        self.assertEqual(block["stages"]["fe2rr"]["verdict"], "no_stage_deterioration")
        self.assertEqual(block["stages"]["fe2rr"]["rate_delta_pp"], 0.0)
        self.assertEqual(block["stages"]["rr2net"]["verdict"], "no_stage_deterioration")
        self.assertIn(FUNNEL_CALENDAR_SCALING_WARNING, result["warnings"])

    def test_longer_prior_month_scales_prior_down_to_current_length(self):
        # Aug (31d) -> Sep (30d): Aug scaled by 30/31, Sep unscaled.
        rows = [
            ("2026-08", "Alpha", "T1", 3100, 31000, 15500, 12400),
            ("2026-09", "Alpha", "T1", 2700, 27000, 13500, 10800),
        ]
        result = investigate_funnel(dataset(rows), requested_months=["2026-09"], cities=["Alpha"])
        block = only(result, "city", "Alpha")
        self.assertAlmostEqual(block["prior_ftu_scaled"], 3000.0)
        self.assertEqual(block["current_ftu_scaled"], 2700.0)
        self.assertAlmostEqual(block["ftu_change"], -300.0)
        self.assertAlmostEqual(block["stages"]["fe"]["contribution_pct"], 100.0)

    def test_parallel_independent_branches_never_stop_early_and_flag_over_explained(self):
        # Jul -> Aug (31d -> 31d): FTU 1000 -> 800 (-200).
        # FE drops 25% -> effect -250 -> +125% (material + over_explained).
        # FE2RR improves 0.50 -> 0.55 (+10%) -> effect +100 -> -50% (no_stage_deterioration).
        # RR2Net drops 0.80 -> 0.72 (-10%) -> effect -100 -> +50% (material).
        rows = [
            ("2026-07", "Alpha", "T1", 1000, 10000, 5000, 4000),
            ("2026-08", "Alpha", "T1", 800, 7500, 4125, 2970),
        ]
        result = investigate_funnel(dataset(rows), requested_months=["2026-08"], cities=["Alpha"])
        block = only(result, "city", "Alpha")

        self.assertEqual(block["verdict"], "contributors_identified")
        self.assertEqual(block["material_stages"], ["fe", "rr2net"])
        self.assertEqual(block["flags"], ["over_explained"])
        self.assertIn(FUNNEL_OVER_EXPLAINED_WARNING, result["warnings"])

        self.assertAlmostEqual(block["stages"]["fe"]["contribution_pct"], 125.0)
        self.assertEqual(block["stages"]["fe"]["flags"], ["over_explained"])
        self.assertAlmostEqual(block["stages"]["fe2rr"]["contribution_pct"], -50.0)
        self.assertEqual(block["stages"]["fe2rr"]["verdict"], "no_stage_deterioration")
        self.assertAlmostEqual(block["stages"]["fe2rr"]["rate_delta_pp"], 5.0)
        self.assertAlmostEqual(block["stages"]["rr2net"]["contribution_pct"], 50.0)
        self.assertEqual(block["stages"]["rr2net"]["verdict"], "material_contributor")
        self.assertAlmostEqual(block["stages"]["rr2net"]["rate_delta_pp"], -8.0)

    def test_no_historical_ftu_dip_keeps_factual_rates_and_marks_stages_not_applicable(self):
        rows = [
            ("2026-07", "Alpha", "T1", 1000, 10000, 5000, 4000),
            ("2026-08", "Alpha", "T1", 1050, 9000, 4050, 3240),
        ]
        result = investigate_funnel(dataset(rows), requested_months=["2026-08"], cities=["Alpha"])
        block = only(result, "city", "Alpha")

        self.assertEqual(block["verdict"], "no_historical_ftu_dip")
        self.assertEqual(block["reason"], "ftu_not_declining_vs_prior_month")
        self.assertEqual(block["material_stages"], [])
        self.assertEqual(block["stages"]["fe2rr"]["verdict"], "not_applicable")
        self.assertAlmostEqual(block["stages"]["fe2rr"]["prior_value"], 0.5)
        self.assertAlmostEqual(block["stages"]["fe2rr"]["current_value"], 0.45)
        self.assertAlmostEqual(block["stages"]["fe2rr"]["rate_delta_pp"], -5.0)
        self.assertIsNone(block["stages"]["fe2rr"]["contribution_pct"])

    def test_missing_or_zero_operands_fail_independently_per_stage(self):
        # Alpha has valid FE (drops 10% -> +50% material), null current RR (blocks fe2rr & rr2net).
        rows = [
            ("2026-07", "Alpha", "T1", 1000, 10000, 5000, 4000),
            ("2026-08", "Alpha", "T1", 800, 9000, None, 3000),
        ]
        result = investigate_funnel(dataset(rows), requested_months=["2026-08"], cities=["Alpha"])
        block = only(result, "city", "Alpha")

        self.assertEqual(block["verdict"], "partial_contributors_identified")
        self.assertEqual(block["material_stages"], ["fe"])
        self.assertEqual(block["stages"]["fe"]["verdict"], "material_contributor")
        self.assertEqual((block["stages"]["fe2rr"]["verdict"], block["stages"]["fe2rr"]["reason"]),
                         ("undetermined", "missing_current_rr"))
        self.assertEqual((block["stages"]["rr2net"]["verdict"], block["stages"]["rr2net"]["reason"]),
                         ("undetermined", "missing_current_rr"))

    def test_zero_denominators_and_missing_city_observations_keep_exact_reasons(self):
        rows = [
            ("2026-07", "ZeroFE", "T1", 1000, 0, 500, 400),
            ("2026-08", "ZeroFE", "T1", 800, 500, 250, 150),
            ("2026-08", "NewOnly", "T1", 500, 5000, 2500, 2000),
            ("2026-07", "OldOnly", "T1", 500, 5000, 2500, 2000),
        ]
        result = investigate_funnel(
            dataset(rows),
            requested_months=["2026-08"],
            cities=["ZeroFE", "NewOnly", "OldOnly"],
        )
        zero_fe = only(result, "city", "ZeroFE")
        self.assertEqual((zero_fe["stages"]["fe"]["verdict"], zero_fe["stages"]["fe"]["reason"]),
                         ("undetermined", "nonpositive_prior_fe_sessions"))
        self.assertEqual((zero_fe["stages"]["fe2rr"]["verdict"], zero_fe["stages"]["fe2rr"]["reason"]),
                         ("undetermined", "nonpositive_prior_fe_sessions"))
        self.assertEqual(zero_fe["stages"]["rr2net"]["verdict"], "material_contributor")

        new_only = only(result, "city", "NewOnly")
        self.assertEqual((new_only["verdict"], new_only["reason"]), ("undetermined", "no_prior_city_observation"))
        self.assertEqual(new_only["current_ftu_raw"], 500)

        old_only = only(result, "city", "OldOnly")
        self.assertEqual((old_only["verdict"], old_only["reason"]), ("undetermined", "no_stored_city_observation"))
        self.assertEqual(old_only["prior_ftu_raw"], 500)

    def test_ftu_missing_member_blocks_tier_aggregate_and_names_blocker(self):
        rows = [
            ("2026-07", "Alpha", "T2", 600, 6000, 3000, 2400),
            ("2026-07", "Bravo", "T2", None, 4000, 2000, 1600),
            ("2026-08", "Alpha", "T2", 480, 4800, 2400, 1920),
            ("2026-08", "Bravo", "T2", 320, 3200, 1600, 1280),
        ]
        result = investigate_funnel(dataset(rows), requested_months=["2026-08"], tier="T2")
        block = only(result, "tier", "T2")
        self.assertEqual((block["verdict"], block["reason"]), ("undetermined", "incomplete_group_member"))
        self.assertEqual(block["blocking_city_keys"], ["bravo"])
        self.assertEqual(block["coverage"]["ftu_blocked_city_count"], 1)

    def test_tier_and_group_roll_up_counts_strictly_and_isolate_stage_blockers(self):
        # T2 has Alpha and Bravo; Bravo has null Net in Aug -> blocks rr2net only, not fe or fe2rr.
        rows = [
            ("2026-07", "Alpha", "T2", 600, 6000, 3000, 2400),
            ("2026-07", "Bravo", "T2", 400, 4000, 2000, 1600),
            ("2026-08", "Alpha", "T2", 480, 4800, 2400, 1920),
            ("2026-08", "Bravo", "T2", 320, 3200, 1600, None),
        ]
        result = investigate_funnel(
            dataset(rows),
            requested_months=["2026-08"],
            tier="T2",
            groups=[("Pair", ["Alpha", "Bravo"])],
        )
        for kind, label in (("tier", "T2"), ("group", "Pair")):
            block = only(result, kind, label)
            self.assertEqual(block["verdict"], "partial_contributors_identified")
            self.assertEqual(block["ftu_change"], -200.0)
            self.assertAlmostEqual(block["stages"]["fe"]["contribution_pct"], 100.0)
            self.assertEqual(block["stages"]["fe"]["verdict"], "material_contributor")
            self.assertEqual(block["stages"]["fe2rr"]["verdict"], "no_stage_deterioration")
            self.assertEqual(block["stages"]["rr2net"]["verdict"], "undetermined")
            self.assertEqual(block["stages"]["rr2net"]["reason"], "incomplete_group_member")
            self.assertEqual(block["stages"]["rr2net"]["blocking_city_keys"], ["bravo"])
            self.assertEqual(block["blocking_city_keys"], [])
            self.assertEqual(len(block["members"]), 2)

    def test_national_rolls_up_matched_cities_and_discloses_exclusions(self):
        # Alpha is in Jul+Aug; Charlie appears only in Aug (newly instrumented) -> excluded from matched national.
        rows = [
            ("2026-07", "Alpha", "T1", 1000, 10000, 5000, 4000),
            ("2026-08", "Alpha", "T1", 800, 9000, 4500, 3600),
            ("2026-08", "Charlie", "T3", 500, 50000, 25000, 20000),
        ]
        result = investigate_funnel(dataset(rows), requested_months=["2026-08"], national=True)
        block = only(result, "national")

        self.assertEqual(block["prior_ftu_raw"], 1000)
        self.assertEqual(block["current_ftu_raw"], 800)
        self.assertAlmostEqual(block["stages"]["fe"]["contribution_pct"], 50.0)
        self.assertEqual(block["stages"]["fe"]["verdict"], "material_contributor")
        self.assertEqual(block["coverage"]["platform_ftu"]["matched_city_count"], 1)
        self.assertEqual(block["coverage"]["platform_ftu"]["excluded_city_count"], 1)
        self.assertEqual(block["coverage"]["fe"]["matched_city_count"], 1)
        self.assertIn(FUNNEL_NATIONAL_MATCHED_WARNING, result["warnings"])

    def test_granular_stage_filter_and_history_gaps(self):
        rows = [
            ("2026-07", "Alpha", "T1", 1000, 10000, 5000, 4000),
            ("2026-08", "Alpha", "T1", 800, 10000, 4500, 3600),
        ]
        result = investigate_funnel(
            dataset(rows),
            requested_months=["2026-07", "2026-08"],
            available_months={"2026-07", "2026-08"},
            cities=["Alpha"],
            stages=["fe2rr"],
        )
        self.assertEqual(result["stages"], ["fe2rr"])
        jul = only(result, "city", "Alpha", month_value="2026-07")
        aug = only(result, "city", "Alpha", month_value="2026-08")

        self.assertEqual((jul["verdict"], jul["reason"]), ("undetermined", "no_prior_month"))
        self.assertEqual(list(aug["stages"]), ["fe2rr"])
        self.assertAlmostEqual(aug["stages"]["fe2rr"]["contribution_pct"], 50.0)
        self.assertEqual(aug["stages"]["fe2rr"]["verdict"], "material_contributor")


class FunnelInvestigationCliTests(unittest.TestCase):
    def invoke(self, arguments, data=None):
        rows = [
            ("2026-07", "Alpha", "T1", 1000, 10000, 5000, 4000),
            ("2026-08", "Alpha", "T1", 800, 9000, 4500, 3600),
        ]
        store = FixtureReaderStore(data or dataset(rows))

        class _Reader:
            def __enter__(self):
                return store

            def __exit__(self, *_exc):
                return False

        output = io.StringIO()
        with patch("growthos.cli.connect_reader", _Reader), redirect_stdout(output):
            code = main(arguments)
        return code, json.loads(output.getvalue())

    def test_cli_defaults_to_national_and_supports_granular_stage_flag(self):
        code, result = self.invoke(["investigate-funnel", "--month", "2026-08"])
        self.assertEqual(code, 0)
        self.assertEqual(result["operation"], "investigate-funnel")
        self.assertEqual(result["stages"], ["fe", "fe2rr", "rr2net"])
        self.assertEqual(len(result["blocks"]), 1)
        self.assertEqual(result["blocks"][0]["kind"], "national")

        code2, result2 = self.invoke([
            "investigate-funnel", "--month", "2026-08", "--city", "Alpha", "--stage", "fe2rr",
        ])
        self.assertEqual(code2, 0)
        self.assertEqual(result2["stages"], ["fe2rr"])
        self.assertEqual(list(result2["blocks"][0]["stages"]), ["fe2rr"])

    def test_invalid_selectors_fail_before_reader_and_never_echo_inputs(self):
        with patch("growthos.cli.connect_reader") as reader:
            for args, expected in (
                (["investigate-funnel", "--month", "2026-08", "--stage", "SECRET_STAGE"], "ARGUMENTS"),
                (["investigate-funnel", "--month", "2026-08", "--stage", "fe", "--stage", "fe"], "ARGUMENTS"),
                (["investigate-funnel", "--month", "2026-08", "--tier", "T4"], "ARGUMENTS"),
                (["investigate-funnel", "--month", "2026-08", "--start", "2025-09", "--end", "2026-08"], "BOUNDS"),
                (["investigate-funnel", "--month", "2026-08", "--start", "2026-07"], "PERIOD"),
            ):
                with self.subTest(args=args):
                    output = io.StringIO()
                    with redirect_stdout(output):
                        code = main(args)
                    payload = json.loads(output.getvalue())
                    self.assertEqual(code, 2)
                    self.assertEqual(payload["error"]["code"], expected)
                    self.assertNotIn("SECRET_STAGE", output.getvalue())
        reader.assert_not_called()

    def test_unavailable_requested_month_fails_closed_with_coverage(self):
        code, payload = self.invoke(["investigate-funnel", "--month", "2026-09"])
        self.assertEqual(code, 2)
        self.assertEqual(payload["error"]["code"], "COVERAGE")


if __name__ == "__main__":
    unittest.main()
