import io
import json
import unittest
from contextlib import redirect_stdout
from fractions import Fraction
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
    FUNNEL_UNDETERMINED_WARNING,
)
from growthos.funnel_investigation import investigate_funnel


def dataset(rows):
    """Build detached MonthlyData from (month, city, tier, ftu, fe, rr, net[, registrations[, services]]) tuples."""
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
    facts = []
    for row in rows:
        m, city, tier, ftu, fe, rr, net = row[:7]
        regs = row[7] if len(row) > 7 else base_fact["registrations"]
        services = row[8] if len(row) > 8 else {}
        facts.append({
            **base_fact,
            "month": m,
            "city": city,
            "city_key": normalize_city_key(city),
            "source_cities": [city],
            "tier": tier,
            "registrations": regs,
            "platform_ftu": ftu,
            "fe_sessions": fe,
            "rr": rr,
            "net": net,
            **services,
        })
    raw["facts"] = facts
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
    def test_reference_sheet_counterfactuals_and_top_70_selection_on_platform_ftu(self):
        # Reference sheet rates/volumes (FTU drops by the same 1200 -> 544 = -656):
        #   FE only:     1200 * (8000/10000) = 960  -> effect -240 -> 240/656 = 36.58536585%
        #   FE2RR only:  1200 * (0.17/0.20)  = 1020 -> effect -180 -> 180/656 = 27.43902439%
        #   RR2Net only: 1200 * (0.40/0.60)  = 800  -> effect -400 -> 400/656 = 60.97560976%
        # All 3 > 20%; descending cumulative > 70% picks RR2Net (60.98%) + FE (36.59%) = 97.56%.
        rows = [
            ("2026-07", "SheetCity", "T1", 1200, 10000, 2000, 1200),
            ("2026-08", "SheetCity", "T1", 544, 8000, 1360, 544),
        ]
        result = investigate_funnel(dataset(rows), requested_months=["2026-08"], cities=["SheetCity"])
        block = only(result, "city", "SheetCity")

        self.assertEqual(block["verdict"], "contributors_identified")
        self.assertAlmostEqual(block["stages"]["fe"]["counterfactual_ftu"], 960.0)
        self.assertAlmostEqual(block["stages"]["fe"]["stage_effect"], -240.0)
        self.assertAlmostEqual(block["stages"]["fe"]["contribution_pct"], 24000 / 656)
        self.assertTrue(block["stages"]["fe"]["selected_as_primary"])

        self.assertAlmostEqual(block["stages"]["fe2rr"]["counterfactual_ftu"], 1020.0)
        self.assertAlmostEqual(block["stages"]["fe2rr"]["stage_effect"], -180.0)
        self.assertAlmostEqual(block["stages"]["fe2rr"]["contribution_pct"], 18000 / 656)
        self.assertTrue(block["stages"]["fe2rr"]["material"])
        self.assertFalse(block["stages"]["fe2rr"]["selected_as_primary"])

        self.assertAlmostEqual(block["stages"]["rr2net"]["counterfactual_ftu"], 800.0)
        self.assertAlmostEqual(block["stages"]["rr2net"]["stage_effect"], -400.0)
        self.assertAlmostEqual(block["stages"]["rr2net"]["contribution_pct"], 40000 / 656)
        self.assertTrue(block["stages"]["rr2net"]["selected_as_primary"])

        self.assertEqual(block["material_stages"], ["rr2net", "fe", "fe2rr"])
        self.assertEqual(block["primary_stages"], ["rr2net", "fe"])
        self.assertAlmostEqual(block["cumulative_primary_contribution_pct"], 64000 / 656)
        self.assertTrue(block["primary_coverage_reached_70_pct"])
        self.assertEqual(result["thresholds"]["cumulative_issue_above_pct"], 70)

    def test_evaluates_stages_even_when_all_customer_net_grew_during_ftu_dip(self):
        # FTU drops -15% (1000 -> 850), while city-wide FE grows +10%, FE2RR flat, RR2Net drops -7%
        # (so all-customer Net actually rose +2.3% from 4000 -> 4092).
        # Using Platform FTU denominator still independently scores RR2Net = 7/15 = +46.67% (> 20%)!
        rows = [
            ("2026-07", "SuratCase", "T2", 1000, 10000, 5000, 4000),
            ("2026-08", "SuratCase", "T2", 850, 11000, 5500, 4092),
        ]
        result = investigate_funnel(dataset(rows), requested_months=["2026-08"], cities=["SuratCase"])
        block = only(result, "city", "SuratCase")

        self.assertEqual(block["verdict"], "contributors_identified")
        self.assertEqual(block["stages"]["fe"]["verdict"], "no_stage_deterioration")
        self.assertAlmostEqual(block["stages"]["fe"]["contribution_pct"], -1000 / 15)
        self.assertEqual(block["stages"]["fe2rr"]["verdict"], "no_stage_deterioration")
        self.assertAlmostEqual(block["stages"]["rr2net"]["contribution_pct"], 700 / 15)
        self.assertEqual(block["stages"]["rr2net"]["verdict"], "material_contributor")
        self.assertEqual(block["material_stages"], ["rr2net"])
        self.assertEqual(block["primary_stages"], ["rr2net"])
        self.assertFalse(block["primary_coverage_reached_70_pct"])

    def test_cumulative_70_selection_edge_cases_single_exact_all_three_and_partial(self):
        # All Jul (31d) -> Aug (31d), FTU 1000 -> 800 (-20%):
        # SingleOver70: RR2Net drops 15% (75% > 70%), FE drops 6% (30% > 20%), FE2RR flat -> ["rr2net"].
        # Exact70: RR2Net drops 14% (exact 70.0%, NOT strictly > 70%), FE drops 5% (25% > 20%) -> ["rr2net", "fe"].
        # NeedsAllThree: RR2Net drops 7% (35%), FE drops 6% (30%), FE2RR drops 5% (25%) -> sum 35+30=65<=70, +25=90 -> all 3.
        # PartialBelow70: FE drops 9% (45%), FE2RR drops 3% (15% <= 20%), RR2Net drops 2% (10% <= 20%) -> ["fe"], reached_70=False.
        rows = [
            ("2026-07", "SingleOver70", "T1", 1000, 10000, 5000, 4000),
            ("2026-08", "SingleOver70", "T1", 800, 9400, 4700, 3196),
            ("2026-07", "Exact70", "T1", 1000, 10000, 5000, 4000),
            ("2026-08", "Exact70", "T1", 800, 9500, 4750, 3268),
            ("2026-07", "NeedsAllThree", "T1", 1000, 100000, 50000, 40000),
            ("2026-08", "NeedsAllThree", "T1", 800, 94000, 44650, 33219 + 1),  # check exact below
            ("2026-07", "PartialBelow70", "T1", 1000, 100000, 50000, 40000),
            ("2026-08", "PartialBelow70", "T1", 800, 91000, 44135, 34601),
        ]
        # Replace NeedsAllThree & PartialBelow70 with exact integer rates:
        # NeedsAllThree: Prior FE=100000, RR=50000 (0.50), Net=40000 (0.80).
        #   FE=94000 (-6% -> 30%), FE2RR=0.475 (-5% -> 25%, RR=44650), RR2Net=0.744 (-7% -> 35%, Net=44650*0.744=33219.6 -> x5: FE=500000)
        # PartialBelow70: Prior FE=100000, RR=50000 (0.50), Net=40000 (0.80).
        #   FE=91000 (-9% -> 45%), FE2RR=0.485 (-3% -> 15%, RR=44135), RR2Net=0.784 (-2% -> 10%, Net=44135*0.784=34601.84 -> x25)
        rows = [
            ("2026-07", "SingleOver70", "T1", 1000, 10000, 5000, 4000),
            ("2026-08", "SingleOver70", "T1", 800, 9400, 4700, 3196),
            ("2026-07", "Exact70", "T1", 1000, 10000, 5000, 4000),
            ("2026-08", "Exact70", "T1", 800, 9500, 4750, 3268),
            ("2026-07", "NeedsAllThree", "T1", 1000, 500000, 250000, 200000),
            ("2026-08", "NeedsAllThree", "T1", 800, 470000, 223250, 166098),
            ("2026-07", "PartialBelow70", "T1", 1000, 2500000, 1250000, 1000000),
            ("2026-08", "PartialBelow70", "T1", 800, 2275000, 1103375, 865046),
        ]
        result = investigate_funnel(
            dataset(rows),
            requested_months=["2026-08"],
            cities=["SingleOver70", "Exact70", "NeedsAllThree", "PartialBelow70"],
        )

        single = only(result, "city", "SingleOver70")
        self.assertAlmostEqual(single["stages"]["rr2net"]["contribution_pct"], 75.0)
        self.assertAlmostEqual(single["stages"]["fe"]["contribution_pct"], 30.0)
        self.assertEqual(single["material_stages"], ["rr2net", "fe"])
        self.assertEqual(single["primary_stages"], ["rr2net"])
        self.assertTrue(single["primary_coverage_reached_70_pct"])
        self.assertTrue(single["stages"]["rr2net"]["selected_as_primary"])
        self.assertFalse(single["stages"]["fe"]["selected_as_primary"])

        exact70 = only(result, "city", "Exact70")
        self.assertAlmostEqual(exact70["stages"]["rr2net"]["contribution_pct"], 70.0)
        self.assertAlmostEqual(exact70["stages"]["fe"]["contribution_pct"], 25.0)
        self.assertEqual(exact70["material_stages"], ["rr2net", "fe"])
        self.assertEqual(exact70["primary_stages"], ["rr2net", "fe"])
        self.assertAlmostEqual(exact70["cumulative_primary_contribution_pct"], 95.0)
        self.assertTrue(exact70["primary_coverage_reached_70_pct"])

        all_three = only(result, "city", "NeedsAllThree")
        self.assertAlmostEqual(all_three["stages"]["rr2net"]["contribution_pct"], 35.0)
        self.assertAlmostEqual(all_three["stages"]["fe"]["contribution_pct"], 30.0)
        self.assertAlmostEqual(all_three["stages"]["fe2rr"]["contribution_pct"], 25.0)
        self.assertEqual(all_three["material_stages"], ["rr2net", "fe", "fe2rr"])
        self.assertEqual(all_three["primary_stages"], ["rr2net", "fe", "fe2rr"])
        self.assertAlmostEqual(all_three["cumulative_primary_contribution_pct"], 90.0)
        self.assertTrue(all_three["primary_coverage_reached_70_pct"])

        partial = only(result, "city", "PartialBelow70")
        self.assertEqual(partial["verdict"], "contributors_identified")
        self.assertAlmostEqual(partial["stages"]["fe"]["contribution_pct"], 45.0)
        self.assertAlmostEqual(partial["stages"]["fe2rr"]["contribution_pct"], 15.0)
        self.assertAlmostEqual(partial["stages"]["rr2net"]["contribution_pct"], 10.0)
        self.assertEqual(partial["material_stages"], ["fe"])
        self.assertEqual(partial["primary_stages"], ["fe"])
        self.assertAlmostEqual(partial["cumulative_primary_contribution_pct"], 45.0)
        self.assertFalse(partial["primary_coverage_reached_70_pct"])
        self.assertTrue(partial["stages"]["fe"]["selected_as_primary"])

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
        self.assertFalse(alpha["stages"]["fe"]["selected_as_primary"])
        self.assertEqual(alpha["stages"]["fe"]["reason"], "contribution_at_or_below_threshold")
        self.assertNotIn("trigger_registration_check", alpha)
        self.assertEqual(result["thresholds"]["ftu_dip_below_pct"], -5)
        self.assertEqual(result["thresholds"]["registration_gate"], "registration_target_shortfall_check_above_70_stops_before_funnel")
        self.assertEqual(result["thresholds"]["persistence_gate"], "month_on_month_dip_persistence_check_persistent_prior_month_stops_before_funnel")
        self.assertEqual(alpha["verdict"], "no_material_funnel_contributor")
        self.assertEqual(alpha["primary_stages"], [])
        self.assertNotIn(FUNNEL_CALENDAR_SCALING_WARNING, result["warnings"])
        self.assertIn(FUNNEL_PROXY_WARNING, result["warnings"])

        self.assertEqual(bravo["stages"]["fe"]["verdict"], "material_contributor")
        self.assertTrue(bravo["stages"]["fe"]["material"])
        self.assertTrue(bravo["stages"]["fe"]["selected_as_primary"])
        self.assertEqual(bravo["verdict"], "contributors_identified")
        self.assertEqual(bravo["material_stages"], ["fe"])
        self.assertEqual(bravo["primary_stages"], ["fe"])
        self.assertFalse(bravo["primary_coverage_reached_70_pct"])

    def test_strict_five_percent_mom_ftu_dip_boundary_uses_exact_rationals(self):
        # Alpha: Jul -> Aug FTU 1000 -> 950 (exact -5.0%) -> no_material_ftu_dip, stages not_applicable
        # Bravo: 64-bit precision boundary just below -5.0% -> decomposes stages
        # Charlie: Sep (30d) -> Oct (31d) 30,000 -> 30,070 (scaled 29,100 -> -3.0%) -> no_material_ftu_dip
        rows = [
            ("2026-07", "Alpha", "T1", 1000, 10000, 5000, 4000),
            ("2026-08", "Alpha", "T1", 950, 9000, 4050, 3240),
            ("2026-07", "Bravo", "T1", 10**15, 10**16, 5 * 10**15, 4 * 10**15),
            ("2026-08", "Bravo", "T1", 95 * 10**13 - 1, 9 * 10**15, 45 * 10**14, 36 * 10**14),
            ("2026-09", "Charlie", "T1", 30000, 300000, 150000, 120000),
            ("2026-10", "Charlie", "T1", 30070, 300700, 150350, 120280),
        ]
        aug = investigate_funnel(dataset(rows), requested_months=["2026-08"], cities=["Alpha", "Bravo"])
        alpha = only(aug, "city", "Alpha")
        bravo = only(aug, "city", "Bravo")
        self.assertEqual((alpha["verdict"], alpha["reason"]), ("no_material_ftu_dip", "ftu_dip_within_5_percent"))
        self.assertAlmostEqual(alpha["ftu_change_pct"], -5.0)
        self.assertEqual(alpha["material_stages"], [])
        self.assertEqual(alpha["primary_stages"], [])
        self.assertEqual((alpha["stages"]["fe2rr"]["verdict"], alpha["stages"]["fe2rr"]["reason"]),
                         ("not_applicable", "no_material_ftu_dip"))
        self.assertIsNone(alpha["stages"]["fe2rr"]["contribution_pct"])
        self.assertAlmostEqual(alpha["stages"]["fe2rr"]["rate_delta_pp"], -5.0)

        self.assertEqual(bravo["verdict"], "contributors_identified")
        self.assertEqual(bravo["material_stages"], ["fe"])
        self.assertEqual(bravo["primary_stages"], ["fe"])

        oct_ = investigate_funnel(dataset(rows), requested_months=["2026-10"], cities=["Charlie"])
        charlie = only(oct_, "city", "Charlie")
        self.assertEqual((charlie["verdict"], charlie["reason"]), ("no_material_ftu_dip", "ftu_dip_within_5_percent"))
        self.assertAlmostEqual(charlie["ftu_change_pct"], -3.0)

    def test_shorter_month_proportional_scaling_exposes_october_daily_dip(self):
        # Sep (30d): FTU 30,000, FE 300,000, RR 150,000 (50%), Net 120,000 (80%).
        # Oct (31d): FTU 29,140 (-2.87% raw, within 5%, but 29,140 * 30/31 = 28,200 -> -1,800 / -6.0% scaled > 5%!),
        # FE 291,400 (291,400 * 30/31 = 282,000 -> -6% FE -> effect = -1,800 -> 100% FE),
        # RR 145,700 (FE2RR still exact 50%), Net 116,560 (RR2Net still exact 80%).
        rows = [
            ("2026-09", "Alpha", "T1", 30000, 300000, 150000, 120000),
            ("2026-10", "Alpha", "T1", 29140, 291400, 145700, 116560),
        ]
        result = investigate_funnel(dataset(rows), requested_months=["2026-10"], cities=["Alpha"])
        block = only(result, "city", "Alpha")

        self.assertTrue(block["calendar_adjustment"]["adjusted"])
        self.assertEqual(block["calendar_adjustment"]["reference_days"], 30)
        self.assertEqual(block["calendar_adjustment"]["prior_scale"], 1.0)
        self.assertAlmostEqual(block["calendar_adjustment"]["current_scale"], 30 / 31)
        self.assertEqual(block["prior_ftu_scaled"], 30000.0)
        self.assertAlmostEqual(block["current_ftu_scaled"], 28200.0)
        self.assertAlmostEqual(block["ftu_change"], -1800.0)
        self.assertAlmostEqual(block["ftu_change_pct"], -6.0)
        self.assertAlmostEqual(block["stages"]["fe"]["contribution_pct"], 100.0)
        self.assertEqual(block["stages"]["fe"]["verdict"], "material_contributor")
        self.assertEqual(block["primary_stages"], ["fe"])
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

    def test_parallel_independent_branches_flag_over_explained_and_apply_top_70_primary(self):
        # Jul -> Aug (31d -> 31d): FTU 1000 -> 800 (-200).
        # FE drops 25% -> effect -250 -> +125% (material + over_explained + sole primary since > 70%).
        # FE2RR improves 0.50 -> 0.55 (+10%) -> effect +100 -> -50% (no_stage_deterioration).
        # RR2Net drops 0.80 -> 0.72 (-10%) -> effect -100 -> +50% (material, non-primary since FE > 70%).
        rows = [
            ("2026-07", "Alpha", "T1", 1000, 10000, 5000, 4000),
            ("2026-08", "Alpha", "T1", 800, 7500, 4125, 2970),
        ]
        result = investigate_funnel(dataset(rows), requested_months=["2026-08"], cities=["Alpha"])
        block = only(result, "city", "Alpha")

        self.assertEqual(block["verdict"], "contributors_identified")
        self.assertEqual(block["material_stages"], ["fe", "rr2net"])
        self.assertEqual(block["primary_stages"], ["fe"])
        self.assertEqual(block["flags"], ["over_explained"])
        self.assertIn(FUNNEL_OVER_EXPLAINED_WARNING, result["warnings"])

        self.assertAlmostEqual(block["stages"]["fe"]["contribution_pct"], 125.0)
        self.assertEqual(block["stages"]["fe"]["flags"], ["over_explained"])
        self.assertTrue(block["stages"]["fe"]["selected_as_primary"])
        self.assertAlmostEqual(block["stages"]["fe2rr"]["contribution_pct"], -50.0)
        self.assertEqual(block["stages"]["fe2rr"]["verdict"], "no_stage_deterioration")
        self.assertAlmostEqual(block["stages"]["fe2rr"]["rate_delta_pp"], 5.0)
        self.assertAlmostEqual(block["stages"]["rr2net"]["contribution_pct"], 50.0)
        self.assertEqual(block["stages"]["rr2net"]["verdict"], "material_contributor")
        self.assertFalse(block["stages"]["rr2net"]["selected_as_primary"])
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
        self.assertEqual(block["primary_stages"], [])
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
        self.assertEqual(block["primary_stages"], ["fe"])
        self.assertFalse(block["primary_coverage_reached_70_pct"])
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
            self.assertEqual(block["primary_stages"], ["fe"])
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
        self.assertEqual(block["primary_stages"], ["fe"])
        self.assertFalse(block["primary_coverage_reached_70_pct"])
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
        self.assertEqual(jul["registration_check"]["verdict"], "not_triggered")
        self.assertEqual(list(aug["stages"]), ["fe2rr"])
        self.assertAlmostEqual(aug["stages"]["fe2rr"]["contribution_pct"], 50.0)
        self.assertEqual(aug["stages"]["fe2rr"]["verdict"], "material_contributor")
        self.assertEqual(aug["primary_stages"], ["fe2rr"])
        self.assertFalse(aug["registration_check"]["triggered"])
        self.assertEqual(aug["registration_check"]["verdict"], "not_triggered")

    def test_registration_check_triggers_only_when_fe_is_primary_and_uses_exact_5_pct_boundary(self):
        # All Jul (31d) -> Aug (31d), FTU 1000 -> 800 (-20%).
        # RegDip: FE primary (-10% -> +50%), Reg drops 10% (2000 -> 1800) -> registration_dip.
        # RegExact5: FE primary, Reg drops exact 5.0% (2000 -> 1900) -> below_threshold (not flagged).
        # RegJustOver5: FE primary, 64-bit precision just below -5.0% -> registration_dip.
        # RegFlat: FE primary, Reg flat (2000 -> 2000) -> no_registration_dip.
        # RegZeroCurr: FE primary, Reg 2000 -> 0 (-100%) -> registration_dip.
        # FeMaterialNotPrimary: RR2Net 75% (>70%), FE 30% (>20%, non-primary), Reg crashes -40% -> not_triggered!
        # NoFtuDip: FTU flat, Reg crashes -40% -> not_triggered!
        rows = [
            ("2026-07", "RegDip", "T1", 1000, 10000, 5000, 4000, 2000),
            ("2026-08", "RegDip", "T1", 800, 9000, 4500, 3600, 1800),
            ("2026-07", "RegExact5", "T1", 1000, 10000, 5000, 4000, 2000),
            ("2026-08", "RegExact5", "T1", 800, 9000, 4500, 3600, 1900),
            ("2026-07", "RegJustOver5", "T1", 1000, 10000, 5000, 4000, 10**15),
            ("2026-08", "RegJustOver5", "T1", 800, 9000, 4500, 3600, 95 * 10**13 - 1),
            ("2026-07", "RegFlat", "T1", 1000, 10000, 5000, 4000, 2000),
            ("2026-08", "RegFlat", "T1", 800, 9000, 4500, 3600, 2000),
            ("2026-07", "RegZeroCurr", "T1", 1000, 10000, 5000, 4000, 2000),
            ("2026-08", "RegZeroCurr", "T1", 800, 9000, 4500, 3600, 0),
            ("2026-07", "FeMaterialNotPrimary", "T1", 1000, 10000, 5000, 4000, 2000),
            ("2026-08", "FeMaterialNotPrimary", "T1", 800, 9400, 4700, 3196, 1200),
            ("2026-07", "NoFtuDip", "T1", 1000, 10000, 5000, 4000, 2000),
            ("2026-08", "NoFtuDip", "T1", 1000, 8000, 4000, 3200, 1200),
        ]
        result = investigate_funnel(
            dataset(rows),
            requested_months=["2026-08"],
            cities=[
                "RegDip", "RegExact5", "RegJustOver5", "RegFlat",
                "RegZeroCurr", "FeMaterialNotPrimary", "NoFtuDip",
            ],
        )
        self.assertEqual(result["thresholds"]["registration_dip_below_pct"], -5)

        dip = only(result, "city", "RegDip")["registration_check"]
        self.assertTrue(dip["triggered"])
        self.assertEqual(dip["verdict"], "registration_dip")
        self.assertIsNone(dip["reason"])
        self.assertEqual(dip["prior_registrations_raw"], 2000)
        self.assertEqual(dip["current_registrations_raw"], 1800)
        self.assertAlmostEqual(dip["registrations_change"], -200.0)
        self.assertAlmostEqual(dip["registrations_change_pct"], -10.0)

        exact5 = only(result, "city", "RegExact5")["registration_check"]
        self.assertTrue(exact5["triggered"])
        self.assertEqual((exact5["verdict"], exact5["reason"]), ("below_threshold", "registration_dip_within_5_percent"))
        self.assertAlmostEqual(exact5["registrations_change_pct"], -5.0)

        just_over = only(result, "city", "RegJustOver5")["registration_check"]
        self.assertTrue(just_over["triggered"])
        self.assertEqual(just_over["verdict"], "registration_dip")

        flat = only(result, "city", "RegFlat")["registration_check"]
        self.assertTrue(flat["triggered"])
        self.assertEqual((flat["verdict"], flat["reason"]), ("no_registration_dip", "registrations_not_declining_vs_prior_month"))
        self.assertEqual(flat["registrations_change_pct"], 0.0)

        zero_curr = only(result, "city", "RegZeroCurr")["registration_check"]
        self.assertTrue(zero_curr["triggered"])
        self.assertEqual(zero_curr["verdict"], "registration_dip")
        self.assertAlmostEqual(zero_curr["registrations_change_pct"], -100.0)

        non_primary_block = only(result, "city", "FeMaterialNotPrimary")
        self.assertEqual(non_primary_block["material_stages"], ["rr2net", "fe"])
        self.assertEqual(non_primary_block["primary_stages"], ["rr2net"])
        non_primary = non_primary_block["registration_check"]
        self.assertFalse(non_primary["triggered"])
        self.assertEqual((non_primary["verdict"], non_primary["reason"]), ("not_triggered", "fe_not_primary"))
        self.assertIsNone(non_primary["prior_registrations_raw"])
        self.assertIsNone(non_primary["registrations_change_pct"])

        no_dip = only(result, "city", "NoFtuDip")["registration_check"]
        self.assertFalse(no_dip["triggered"])
        self.assertEqual((no_dip["verdict"], no_dip["reason"]), ("not_triggered", "fe_not_primary"))

    def test_registration_check_applies_shorter_month_calendar_scaling(self):
        # Sep (30d) -> Oct (31d): Oct scaled by 30/31.
        # Reg 30,000 -> 29,140 (-2.87% raw, but 29,140 * 30/31 = 28,200 -> -6.0% scaled > 5% -> registration_dip!).
        rows = [
            ("2026-09", "Alpha", "T1", 30000, 300000, 150000, 120000, 30000),
            ("2026-10", "Alpha", "T1", 29140, 291400, 145700, 116560, 29140),
        ]
        result = investigate_funnel(dataset(rows), requested_months=["2026-10"], cities=["Alpha"])
        reg = only(result, "city", "Alpha")["registration_check"]
        self.assertTrue(reg["triggered"])
        self.assertEqual(reg["verdict"], "registration_dip")
        self.assertEqual(reg["prior_registrations_scaled"], 30000.0)
        self.assertAlmostEqual(reg["current_registrations_scaled"], 28200.0)
        self.assertAlmostEqual(reg["registrations_change"], -1800.0)
        self.assertAlmostEqual(reg["registrations_change_pct"], -6.0)

    def test_registration_check_missing_or_zero_operands_isolate_from_funnel_stages(self):
        # Missing/nonpositive registrations never block fe/fe2rr/rr2net or downgrade block verdict.
        # NonFeNullReg has null registrations, but FE is NOT primary -> not_triggered and no undetermined warning.
        rows = [
            ("2026-07", "MissingPrior", "T1", 1000, 10000, 5000, 4000, None),
            ("2026-08", "MissingPrior", "T1", 800, 9000, 4500, 3600, 1800),
            ("2026-07", "MissingCurr", "T1", 1000, 10000, 5000, 4000, 2000),
            ("2026-08", "MissingCurr", "T1", 800, 9000, 4500, 3600, None),
            ("2026-07", "ZeroPrior", "T1", 1000, 10000, 5000, 4000, 0),
            ("2026-08", "ZeroPrior", "T1", 800, 9000, 4500, 3600, 1800),
            ("2026-07", "NonFeNullReg", "T1", 1000, 10000, 5000, 4000, None),
            ("2026-08", "NonFeNullReg", "T1", 800, 10000, 5000, 3000, None),
        ]
        result = investigate_funnel(
            dataset(rows),
            requested_months=["2026-08"],
            cities=["MissingPrior", "MissingCurr", "ZeroPrior"],
        )
        self.assertIn(FUNNEL_UNDETERMINED_WARNING, result["warnings"])
        for city, expected_reason in (
            ("MissingPrior", "missing_prior_registrations"),
            ("MissingCurr", "missing_current_registrations"),
            ("ZeroPrior", "nonpositive_prior_registrations"),
        ):
            block = only(result, "city", city)
            self.assertEqual(block["verdict"], "contributors_identified")
            self.assertEqual(block["primary_stages"], ["fe"])
            self.assertEqual(block["stages"]["fe"]["verdict"], "material_contributor")
            self.assertTrue(block["registration_check"]["triggered"])
            self.assertEqual(
                (block["registration_check"]["verdict"], block["registration_check"]["reason"]),
                ("undetermined", expected_reason),
            )

        clean_non_fe = investigate_funnel(
            dataset(rows), requested_months=["2026-08"], cities=["NonFeNullReg"],
        )
        non_fe_block = only(clean_non_fe, "city", "NonFeNullReg")
        self.assertEqual(non_fe_block["primary_stages"], ["rr2net"])
        self.assertEqual(non_fe_block["registration_check"]["verdict"], "not_triggered")
        self.assertNotIn(FUNNEL_UNDETERMINED_WARNING, clean_non_fe["warnings"])

    def test_registration_check_tier_group_and_national_scopes(self):
        # T2 has Alpha (reg 1000->800, -20%) and Bravo (reg 1000->None) -> blocks aggregate reg check only.
        # PairOk has Alpha + Charlie (reg 1000->800 + 1000->960 = 2000->1760 = -12% -> registration_dip).
        # National excludes Delta (Aug-only) from matched registration rollup and exposes coverage["registrations"].
        rows = [
            ("2026-07", "Alpha", "T2", 600, 6000, 3000, 2400, 1000),
            ("2026-07", "Bravo", "T2", 400, 4000, 2000, 1600, 1000),
            ("2026-08", "Alpha", "T2", 480, 4800, 2400, 1920, 800),
            ("2026-08", "Bravo", "T2", 320, 3200, 1600, 1280, None),
            ("2026-07", "Charlie", "T1", 400, 4000, 2000, 1600, 1000),
            ("2026-08", "Charlie", "T1", 320, 3200, 1600, 1280, 960),
            ("2026-08", "Delta", "T3", 500, 50000, 25000, 20000, 9000),
        ]
        result = investigate_funnel(
            dataset(rows),
            requested_months=["2026-08"],
            tier="T2",
            groups=[("PairOk", ["Alpha", "Charlie"])],
            national=True,
        )
        t2 = only(result, "tier", "T2")
        self.assertEqual(t2["verdict"], "contributors_identified")
        self.assertEqual(t2["primary_stages"], ["fe"])
        self.assertTrue(t2["registration_check"]["triggered"])
        self.assertEqual((t2["registration_check"]["verdict"], t2["registration_check"]["reason"]),
                         ("undetermined", "incomplete_group_member"))
        self.assertEqual(t2["registration_check"]["blocking_city_keys"], ["bravo"])
        self.assertEqual(t2["coverage"]["registrations_blocked_city_count"], 1)
        member_verdicts = {m["city"]: m["registration_check_verdict"] for m in t2["members"]}
        self.assertEqual(member_verdicts, {"Alpha": "registration_dip", "Bravo": "undetermined"})

        pair = only(result, "group", "PairOk")
        self.assertTrue(pair["registration_check"]["triggered"])
        self.assertEqual(pair["registration_check"]["verdict"], "registration_dip")
        self.assertAlmostEqual(pair["registration_check"]["registrations_change_pct"], -12.0)
        self.assertEqual(pair["coverage"]["registrations_blocked_city_count"], 0)

        nat = only(result, "national")
        self.assertTrue(nat["registration_check"]["triggered"])
        self.assertEqual(nat["registration_check"]["verdict"], "registration_dip")
        self.assertAlmostEqual(nat["registration_check"]["registrations_change_pct"], -12.0)
        self.assertEqual(nat["coverage"]["registrations"]["matched_city_count"], 2)
        self.assertEqual(nat["coverage"]["registrations"]["excluded_city_count"], 2)

        # National with FE primary but zero matched registration cities -> no_matched_registration_cities.
        no_reg_rows = [
            ("2026-07", "Alpha", "T1", 1000, 10000, 5000, 4000, None),
            ("2026-08", "Alpha", "T1", 800, 9000, 4500, 3600, 1800),
        ]
        no_reg_nat = only(
            investigate_funnel(dataset(no_reg_rows), requested_months=["2026-08"], national=True),
            "national",
        )
        self.assertTrue(no_reg_nat["registration_check"]["triggered"])
        self.assertEqual(
            (no_reg_nat["registration_check"]["verdict"], no_reg_nat["registration_check"]["reason"]),
            ("undetermined", "no_matched_registration_cities"),
        )
        self.assertEqual(no_reg_nat["coverage"]["registrations"]["matched_city_count"], 0)

        # National with FE NOT primary -> registrations omitted from coverage.
        non_fe_nat = only(
            investigate_funnel(dataset(no_reg_rows), requested_months=["2026-08"], national=True, stages=["fe2rr"]),
            "national",
        )
        self.assertFalse(non_fe_nat["registration_check"]["triggered"])
        self.assertNotIn("registrations", non_fe_nat["coverage"])

    def test_fe2rr_service_breakdown_matches_transcript_additive_math_and_no_70_cap(self):
        # Transcript FE2RR example (20% -> 17%, -3pp) with FE flat (10,000 -> 10,000) and RR2Net flat (80%):
        #   Prior RR=2,000: Auto 500 (5%), Bike 1,000 (10%), Cab 500 (5%)
        #   Curr  RR=1,700: Auto 700 (7%, +2pp -> -66.67%), Bike 700 (7%, -3pp -> +100%), Cab 300 (3%, -2pp -> +66.67%)
        # Both Bike (100%) and Cab (66.67%) are > 30% and BOTH must be returned (no 70% cap).
        # WithOther has 100->50 non-commute RR (other_services_contribution_pct = 1/6 = +16.67%).
        # ShutdownCab has Cab RR 300->0 in FE2RR -> scored as a real drop; LaunchCab 0->100 -> offset; ZeroCab 0->0 -> not_applicable.
        rows = [
            ("2026-07", "TranscriptCity", "T1", 1000, 10000, 2000, 1600, 2000,
             {"bike_rr": 1000, "bike_net": 800, "auto_rr": 500, "auto_net": 400, "cab_rr": 500, "cab_net": 400}),
            ("2026-08", "TranscriptCity", "T1", 850, 10000, 1700, 1360, 2000,
             {"bike_rr": 700, "bike_net": 560, "auto_rr": 700, "auto_net": 560, "cab_rr": 300, "cab_net": 240}),
            ("2026-07", "WithOther", "T1", 1000, 10000, 2000, 1600, 2000,
             {"bike_rr": 1000, "bike_net": 800, "auto_rr": 600, "auto_net": 480, "cab_rr": 300, "cab_net": 240}),
            ("2026-08", "WithOther", "T1", 850, 10000, 1700, 1360, 2000,
             {"bike_rr": 850, "bike_net": 680, "auto_rr": 550, "auto_net": 440, "cab_rr": 250, "cab_net": 200}),
            ("2026-07", "ShutdownCab", "T2", 1000, 10000, 2000, 1600, 2000,
             {"bike_rr": 1000, "bike_net": 800, "auto_rr": 700, "auto_net": 560, "cab_rr": 300, "cab_net": 240}),
            ("2026-08", "ShutdownCab", "T2", 850, 10000, 1700, 1360, 2000,
             {"bike_rr": 1000, "bike_net": 800, "auto_rr": 700, "auto_net": 560, "cab_rr": 0, "cab_net": 0}),
            ("2026-07", "ZeroCab", "T3", 1000, 10000, 2000, 1600, 2000,
             {"bike_rr": 1200, "bike_net": 960, "auto_rr": 800, "auto_net": 640, "cab_rr": 0, "cab_net": 0}),
            ("2026-08", "ZeroCab", "T3", 850, 10000, 1700, 1360, 2000,
             {"bike_rr": 1000, "bike_net": 800, "auto_rr": 700, "auto_net": 560, "cab_rr": 0, "cab_net": 0}),
        ]
        result = investigate_funnel(
            dataset(rows),
            requested_months=["2026-08"],
            cities=["TranscriptCity", "WithOther", "ShutdownCab", "ZeroCab"],
        )
        self.assertEqual(result["thresholds"]["service_contributor_above_pct"], 30)
        self.assertNotIn(FUNNEL_UNDETERMINED_WARNING, result["warnings"])

        tr = only(result, "city", "TranscriptCity")
        self.assertEqual(tr["primary_stages"], ["fe2rr"])
        fb = tr["service_breakdown"]["fe2rr"]
        self.assertTrue(fb["triggered"])
        self.assertEqual(fb["verdict"], "services_identified")
        self.assertEqual(fb["material_services"], ["bike", "cab"])
        self.assertAlmostEqual(fb["platform_rate_delta_pp"], -3.0)
        self.assertAlmostEqual(fb["other_services_contribution_pct"], 0.0)
        self.assertAlmostEqual(fb["services"]["bike"]["contribution_pct"], 100.0)
        self.assertAlmostEqual(fb["services"]["cab"]["contribution_pct"], 200 / 3)
        self.assertAlmostEqual(fb["services"]["auto"]["contribution_pct"], -200 / 3)
        self.assertEqual(fb["services"]["auto"]["verdict"], "no_service_deterioration")
        self.assertFalse(tr["service_breakdown"]["rr2net"]["triggered"])
        self.assertEqual(tr["service_breakdown"]["rr2net"]["reason"], "rr2net_not_primary")

        wo = only(result, "city", "WithOther")["service_breakdown"]["fe2rr"]
        self.assertAlmostEqual(wo["services"]["bike"]["contribution_pct"], 50.0)
        self.assertAlmostEqual(wo["services"]["auto"]["contribution_pct"], 50 / 3)
        self.assertAlmostEqual(wo["services"]["cab"]["contribution_pct"], 50 / 3)
        self.assertAlmostEqual(wo["other_services_contribution_pct"], 50 / 3)
        self.assertEqual(wo["material_services"], ["bike"])

        sd = only(result, "city", "ShutdownCab")["service_breakdown"]["fe2rr"]
        self.assertEqual(sd["material_services"], ["cab"])
        self.assertAlmostEqual(sd["services"]["cab"]["contribution_pct"], 100.0)

        zc = only(result, "city", "ZeroCab")["service_breakdown"]["fe2rr"]
        self.assertEqual(zc["verdict"], "services_identified")
        self.assertEqual(zc["material_services"], ["bike", "auto"])
        self.assertEqual((zc["services"]["cab"]["verdict"], zc["services"]["cab"]["reason"]),
                         ("not_applicable", "no_service_volume"))
        self.assertIsNone(zc["services"]["cab"]["contribution_pct"])

    def test_rr2net_service_breakdown_matches_transcript_offsetting_and_mix_shift(self):
        # Transcript offsetting case (Month 1 & Month 2 RR=1360: Bike 700, Auto 400, Cab 260, so mix shift = 0):
        #   Bike RR2Net 50% -> 60% (+10pp, weighted +70/1360 -> -127.27%)
        #   Auto RR2Net 60% -> 45% (-15pp, weighted -60/1360 -> +109.09%, over_explained)
        #   Cab  RR2Net 70% -> 45% (-25pp, weighted -65/1360 -> +118.18%, over_explained)
        #   Platform RR2Net: 772/1360 -> 717/1360 (delta = -55/1360 = -4.0441pp; relative -7.12% -> FTU -7% -> 100% RR2Net)
        # MixShiftCity (R_prior=62%, R_curr=50%, delta=-12pp):
        #   Bike 80%->80% (share 50%->40%): rate 0%, mix (-0.1)*(0.80-0.62)/(-0.12) = +15% -> total +15% (below_threshold)
        #   Auto 50%->40% (share 30%->30%): rate +25%, mix 0% -> total +25% (below_threshold)
        #   Cab  35%->20% (share 20%->30%): rate +37.5%, mix (+0.1)*(0.35-0.62)/(-0.12) = +22.5% -> total +60% (material_contributor)
        #   Sum of service contributions = 100%; aggregate mix_shift_contribution_pct = 37.5%.
        # PureMixCity (R_prior=72%, R_curr=56%, delta=-16pp):
        #   Bike 80%->80% (share 80%->40%): rate 0%, mix (-0.4)*(0.80-0.72)/(-0.16) = +20% (below_threshold)
        #   Cab  40%->40% (share 20%->60%): rate 0%, mix (+0.4)*(0.40-0.72)/(-0.16) = +80% (material_contributor)
        #   Aggregate mix_shift_contribution_pct = 100.0%.
        rows = [
            ("2026-07", "OffsetCity", "T1", 1000, 10000, 1360, 772, 2000,
             {"bike_rr": 700, "bike_net": 350, "auto_rr": 400, "auto_net": 240, "cab_rr": 260, "cab_net": 182}),
            ("2026-08", "OffsetCity", "T1", 900, 10000, 1360, 717, 2000,
             {"bike_rr": 700, "bike_net": 420, "auto_rr": 400, "auto_net": 180, "cab_rr": 260, "cab_net": 117}),
            ("2026-07", "MixShiftCity", "T1", 1000, 10000, 1000, 620, 2000,
             {"bike_rr": 500, "bike_net": 400, "auto_rr": 300, "auto_net": 150, "cab_rr": 200, "cab_net": 70}),
            ("2026-08", "MixShiftCity", "T1", 800, 10000, 1000, 500, 2000,
             {"bike_rr": 400, "bike_net": 320, "auto_rr": 300, "auto_net": 120, "cab_rr": 300, "cab_net": 60}),
            ("2026-07", "PureMixCity", "T1", 1000, 10000, 1000, 720, 2000,
             {"bike_rr": 800, "bike_net": 640, "auto_rr": 0, "auto_net": 0, "cab_rr": 200, "cab_net": 80}),
            ("2026-08", "PureMixCity", "T1", 800, 10000, 1000, 560, 2000,
             {"bike_rr": 400, "bike_net": 320, "auto_rr": 0, "auto_net": 0, "cab_rr": 600, "cab_net": 240}),
        ]
        result = investigate_funnel(
            dataset(rows),
            requested_months=["2026-08"],
            cities=["OffsetCity", "MixShiftCity", "PureMixCity"],
        )
        self.assertIn(FUNNEL_OVER_EXPLAINED_WARNING, result["warnings"])
        self.assertNotIn(FUNNEL_UNDETERMINED_WARNING, result["warnings"])

        off = only(result, "city", "OffsetCity")
        self.assertEqual(off["primary_stages"], ["rr2net"])
        rb = off["service_breakdown"]["rr2net"]
        self.assertTrue(rb["triggered"])
        self.assertEqual(rb["method"], "weighted_rr2net_rate_and_mix_delta")
        self.assertEqual(rb["verdict"], "services_identified")
        self.assertEqual(rb["material_services"], ["cab", "auto"])
        self.assertEqual(rb["flags"], ["over_explained"])
        self.assertAlmostEqual(rb["services"]["cab"]["contribution_pct"], 6500 / 55)
        self.assertAlmostEqual(rb["services"]["cab"]["rate_contribution_pct"], 6500 / 55)
        self.assertAlmostEqual(rb["services"]["cab"]["mix_shift_contribution_pct"], 0.0)
        self.assertAlmostEqual(rb["services"]["auto"]["contribution_pct"], 6000 / 55)
        self.assertAlmostEqual(rb["services"]["bike"]["contribution_pct"], -7000 / 55)
        self.assertEqual(rb["services"]["bike"]["verdict"], "no_service_deterioration")
        self.assertAlmostEqual(rb["mix_shift_contribution_pct"], 0.0)
        self.assertAlmostEqual(rb["other_services_contribution_pct"], 0.0)

        ms = only(result, "city", "MixShiftCity")["service_breakdown"]["rr2net"]
        self.assertEqual(ms["verdict"], "services_identified")
        self.assertEqual(ms["material_services"], ["cab"])
        self.assertAlmostEqual(ms["services"]["bike"]["rate_contribution_pct"], 0.0)
        self.assertAlmostEqual(ms["services"]["bike"]["mix_shift_contribution_pct"], 15.0)
        self.assertAlmostEqual(ms["services"]["bike"]["contribution_pct"], 15.0)
        self.assertEqual(ms["services"]["bike"]["verdict"], "below_threshold")
        self.assertAlmostEqual(ms["services"]["auto"]["rate_contribution_pct"], 25.0)
        self.assertAlmostEqual(ms["services"]["auto"]["mix_shift_contribution_pct"], 0.0)
        self.assertAlmostEqual(ms["services"]["auto"]["contribution_pct"], 25.0)
        self.assertEqual(ms["services"]["auto"]["verdict"], "below_threshold")
        self.assertAlmostEqual(ms["services"]["cab"]["rate_contribution_pct"], 37.5)
        self.assertAlmostEqual(ms["services"]["cab"]["mix_shift_contribution_pct"], 22.5)
        self.assertAlmostEqual(ms["services"]["cab"]["contribution_pct"], 60.0)
        self.assertEqual(ms["services"]["cab"]["verdict"], "material_contributor")
        self.assertAlmostEqual(ms["mix_shift_contribution_pct"], 37.5)
        self.assertAlmostEqual(ms["other_services_contribution_pct"], 0.0)

        pm = only(result, "city", "PureMixCity")["service_breakdown"]["rr2net"]
        self.assertEqual(pm["verdict"], "services_identified")
        self.assertEqual(pm["material_services"], ["cab"])
        self.assertAlmostEqual(pm["services"]["bike"]["contribution_pct"], 20.0)
        self.assertEqual(pm["services"]["bike"]["verdict"], "below_threshold")
        self.assertAlmostEqual(pm["services"]["cab"]["contribution_pct"], 80.0)
        self.assertAlmostEqual(pm["services"]["cab"]["rate_contribution_pct"], 0.0)
        self.assertAlmostEqual(pm["services"]["cab"]["mix_shift_contribution_pct"], 80.0)
        self.assertEqual(pm["services"]["cab"]["verdict"], "material_contributor")
        self.assertEqual((pm["services"]["auto"]["verdict"], pm["services"]["auto"]["reason"]),
                         ("not_applicable", "no_service_volume"))
        self.assertAlmostEqual(pm["mix_shift_contribution_pct"], 100.0)
        self.assertAlmostEqual(pm["other_services_contribution_pct"], 0.0)

    def test_service_breakdown_strict_thirty_percent_boundary_at_64_bit_precision(self):
        # Exact30FE2RR: platform FE2RR drops 1000/10000 (-10pp); bike drops 300/10000 (-3pp -> exact 30.0% -> below_threshold).
        # JustOver30FE2RR: at 10^15 scale, bike drops 3*10^14 + 1 out of 10^15 -> strictly > 30% -> material_contributor!
        # Exact30RR2Net: RR=1000 constant; bike RR=500 drops 6pp (weighted -3pp out of -10pp -> exact 30.0% -> below_threshold).
        # JustOver30RR2Net: at 10^15 scale, bike net drops 3*10^14 + 1 out of 10^15 -> strictly > 30% -> material_contributor!
        base = 10**15
        rows = [
            ("2026-07", "Exact30FE2RR", "T1", 1000, 10000, 2000, 1600, 2000,
             {"bike_rr": 1000, "bike_net": 800, "auto_rr": 1000, "auto_net": 800, "cab_rr": 0, "cab_net": 0}),
            ("2026-08", "Exact30FE2RR", "T1", 800, 10000, 1000, 800, 2000,
             {"bike_rr": 700, "bike_net": 560, "auto_rr": 300, "auto_net": 240, "cab_rr": 0, "cab_net": 0}),
            ("2026-07", "JustOver30FE2RR", "T1", 1000, 10 * base, 2 * base, base, 2000,
             {"bike_rr": base, "bike_net": base // 2, "auto_rr": base, "auto_net": base // 2, "cab_rr": 0, "cab_net": 0}),
            ("2026-08", "JustOver30FE2RR", "T1", 800, 10 * base, base, base // 2, 2000,
             {"bike_rr": 7 * 10**14 - 1, "bike_net": 3 * 10**14, "auto_rr": 3 * 10**14 + 1, "auto_net": 2 * 10**14, "cab_rr": 0, "cab_net": 0}),
            ("2026-07", "Exact30RR2Net", "T1", 1000, 10000, 1000, 800, 2000,
             {"bike_rr": 500, "bike_net": 400, "auto_rr": 500, "auto_net": 400, "cab_rr": 0, "cab_net": 0}),
            ("2026-08", "Exact30RR2Net", "T1", 800, 10000, 1000, 700, 2000,
             {"bike_rr": 500, "bike_net": 370, "auto_rr": 500, "auto_net": 330, "cab_rr": 0, "cab_net": 0}),
            ("2026-07", "JustOver30RR2Net", "T1", 1000, 10 * base, 10 * base, 8 * base, 2000,
             {"bike_rr": 5 * base, "bike_net": 4 * base, "auto_rr": 5 * base, "auto_net": 4 * base, "cab_rr": 0, "cab_net": 0}),
            ("2026-08", "JustOver30RR2Net", "T1", 800, 10 * base, 10 * base, 7 * base, 2000,
             {"bike_rr": 5 * base, "bike_net": 37 * 10**14 - 1, "auto_rr": 5 * base, "auto_net": 33 * 10**14 + 1, "cab_rr": 0, "cab_net": 0}),
        ]
        result = investigate_funnel(
            dataset(rows),
            requested_months=["2026-08"],
            cities=["Exact30FE2RR", "JustOver30FE2RR", "Exact30RR2Net", "JustOver30RR2Net"],
        )

        e_fe2rr = only(result, "city", "Exact30FE2RR")["service_breakdown"]["fe2rr"]
        self.assertEqual(e_fe2rr["services"]["bike"]["contribution_pct"], 30.0)
        self.assertEqual(e_fe2rr["services"]["bike"]["verdict"], "below_threshold")
        self.assertEqual(e_fe2rr["material_services"], ["auto"])

        o_fe2rr = only(result, "city", "JustOver30FE2RR")["service_breakdown"]["fe2rr"]
        self.assertEqual(o_fe2rr["services"]["bike"]["verdict"], "material_contributor")
        self.assertEqual(o_fe2rr["material_services"], ["auto", "bike"])

        e_rr2net = only(result, "city", "Exact30RR2Net")["service_breakdown"]["rr2net"]
        self.assertEqual(e_rr2net["services"]["bike"]["contribution_pct"], 30.0)
        self.assertEqual(e_rr2net["services"]["bike"]["verdict"], "below_threshold")
        self.assertEqual(e_rr2net["material_services"], ["auto"])

        o_rr2net = only(result, "city", "JustOver30RR2Net")["service_breakdown"]["rr2net"]
        self.assertEqual(o_rr2net["services"]["bike"]["verdict"], "material_contributor")
        self.assertEqual(o_rr2net["material_services"], ["auto", "bike"])

    def test_service_breakdown_one_sided_zero_and_isolated_nulls(self):
        # LaunchCabRR2Net: cab launched in Aug (0->200 RR, 0->40 Net at 20%), bike flat 80% ->
        #   cab is scored directly as pure mix shift w_curr*(r_curr - R_prior) = -12pp -> 100.0% material_contributor.
        # ShutdownAutoRR2Net: auto shut down in Aug (500->0 RR at 80%), bike expanded 500->1000 at 40% (R_prior=60%, delta=-20pp) ->
        #   auto (-0.5*(0.8-0.6) = -10pp -> 50%) and bike (+0.5*(0.4-0.6) = -10pp -> 50%) both material_contributor!
        # NearZeroLaunchCity (Pune-like): bike scales 115->97095 (0->35491 Net) with other services present ->
        #   near-zero prior rate cancels in total contribution so bike + auto + cab + other == 100.0%.
        # NullCabCity: cab_net is None in Aug -> only cab is undetermined (missing_current_cab_net), bike & auto still evaluate.
        # NonPrimaryNullSvc: FE drove 100% of dip (fe2rr & rr2net flat), all service counts None -> not_triggered & no warning!
        rows = [
            ("2026-07", "LaunchCabRR2Net", "T2", 1000, 10000, 1000, 800, 2000,
             {"bike_rr": 1000, "bike_net": 800, "auto_rr": 0, "auto_net": 0, "cab_rr": 0, "cab_net": 0}),
            ("2026-08", "LaunchCabRR2Net", "T2", 800, 10000, 1000, 680, 2000,
             {"bike_rr": 800, "bike_net": 640, "auto_rr": 0, "auto_net": 0, "cab_rr": 200, "cab_net": 40}),
            ("2026-07", "ShutdownAutoRR2Net", "T2", 1000, 10000, 1000, 600, 2000,
             {"bike_rr": 500, "bike_net": 200, "auto_rr": 500, "auto_net": 400, "cab_rr": 0, "cab_net": 0}),
            ("2026-08", "ShutdownAutoRR2Net", "T2", 700, 10000, 1000, 400, 2000,
             {"bike_rr": 1000, "bike_net": 400, "auto_rr": 0, "auto_net": 0, "cab_rr": 0, "cab_net": 0}),
            ("2026-07", "NearZeroLaunchCity", "T2", 130021, 546913, 269956, 152439, 139560,
             {"bike_rr": 115, "bike_net": 0, "auto_rr": 168451, "auto_net": 100709, "cab_rr": 97072, "cab_net": 49205}),
            ("2026-08", "NearZeroLaunchCity", "T2", 110932, 606089, 281844, 137488, 130574,
             {"bike_rr": 97095, "bike_net": 35491, "auto_rr": 89076, "auto_net": 53004, "cab_rr": 69626, "cab_net": 35273}),
            ("2026-07", "NullCabCity", "T2", 1000, 10000, 1000, 800, 2000,
             {"bike_rr": 600, "bike_net": 480, "auto_rr": 300, "auto_net": 240, "cab_rr": 100, "cab_net": 80}),
            ("2026-08", "NullCabCity", "T2", 800, 10000, 1000, 640, 2000,
             {"bike_rr": 600, "bike_net": 360, "auto_rr": 300, "auto_net": 210, "cab_rr": 100, "cab_net": None}),
        ]
        clean = investigate_funnel(
            dataset(rows),
            requested_months=["2026-08"],
            cities=["LaunchCabRR2Net", "ShutdownAutoRR2Net", "NearZeroLaunchCity"],
        )
        self.assertNotIn(FUNNEL_UNDETERMINED_WARNING, clean["warnings"])
        lc = only(clean, "city", "LaunchCabRR2Net")["service_breakdown"]["rr2net"]
        self.assertEqual(lc["verdict"], "services_identified")
        self.assertEqual(lc["material_services"], ["cab"])
        self.assertEqual(lc["services"]["cab"]["verdict"], "material_contributor")
        self.assertIsNone(lc["services"]["cab"]["rate_delta_pp"])
        self.assertIsNone(lc["services"]["cab"]["weighted_rate_delta_pp"])
        self.assertIsNone(lc["services"]["cab"]["rate_contribution_pct"])
        self.assertAlmostEqual(lc["services"]["cab"]["mix_shift_delta_pp"], -12.0)
        self.assertAlmostEqual(lc["services"]["cab"]["weighted_total_delta_pp"], -12.0)
        self.assertAlmostEqual(lc["services"]["cab"]["contribution_pct"], 100.0)
        self.assertAlmostEqual(lc["services"]["bike"]["contribution_pct"], 0.0)
        self.assertAlmostEqual(lc["mix_shift_contribution_pct"], 100.0)

        sd = only(clean, "city", "ShutdownAutoRR2Net")["service_breakdown"]["rr2net"]
        self.assertEqual(sd["verdict"], "services_identified")
        self.assertEqual(sd["material_services"], ["bike", "auto"])
        self.assertIsNone(sd["services"]["auto"]["rate_delta_pp"])
        self.assertAlmostEqual(sd["services"]["auto"]["mix_shift_delta_pp"], -10.0)
        self.assertAlmostEqual(sd["services"]["auto"]["contribution_pct"], 50.0)
        self.assertAlmostEqual(sd["services"]["bike"]["contribution_pct"], 50.0)
        self.assertAlmostEqual(sd["mix_shift_contribution_pct"], 100.0)

        nz = only(clean, "city", "NearZeroLaunchCity")["service_breakdown"]["rr2net"]
        self.assertEqual(nz["verdict"], "services_identified")
        self.assertEqual(nz["material_services"], ["bike"])
        r_prior = Fraction(152439, 269956)
        delta_r = Fraction(137488, 281844) - r_prior
        expected_bike = 100 * float((Fraction(35491, 281844) - Fraction(97095, 281844) * r_prior + Fraction(115, 269956) * r_prior) / delta_r)
        expected_auto = 100 * float(((Fraction(53004, 281844) - Fraction(89076, 281844) * r_prior) - (Fraction(100709, 269956) - Fraction(168451, 269956) * r_prior)) / delta_r)
        expected_cab = 100 * float(((Fraction(35273, 281844) - Fraction(69626, 281844) * r_prior) - (Fraction(49205, 269956) - Fraction(97072, 269956) * r_prior)) / delta_r)
        self.assertAlmostEqual(nz["services"]["bike"]["contribution_pct"], expected_bike, places=9)
        self.assertAlmostEqual(nz["services"]["auto"]["contribution_pct"], expected_auto, places=9)
        self.assertAlmostEqual(nz["services"]["cab"]["contribution_pct"], expected_cab, places=9)
        self.assertAlmostEqual(
            nz["services"]["bike"]["rate_contribution_pct"] + nz["services"]["bike"]["mix_shift_contribution_pct"],
            nz["services"]["bike"]["contribution_pct"],
            places=9,
        )
        self.assertAlmostEqual(
            sum(nz["services"][s]["contribution_pct"] for s in ("bike", "auto", "cab"))
            + nz["other_services_contribution_pct"],
            100.0,
            places=9,
        )

        partial = investigate_funnel(dataset(rows), requested_months=["2026-08"], cities=["NullCabCity"])
        self.assertIn(FUNNEL_UNDETERMINED_WARNING, partial["warnings"])
        nc = only(partial, "city", "NullCabCity")
        self.assertEqual(nc["verdict"], "contributors_identified")
        self.assertEqual(nc["primary_stages"], ["rr2net"])
        nc_rb = nc["service_breakdown"]["rr2net"]
        self.assertEqual(nc_rb["verdict"], "partial_services_identified")
        self.assertEqual(nc_rb["material_services"], ["bike"])
        self.assertAlmostEqual(nc_rb["services"]["bike"]["contribution_pct"], 75.0)
        self.assertAlmostEqual(nc_rb["services"]["auto"]["contribution_pct"], 18.75)
        self.assertEqual((nc_rb["services"]["cab"]["verdict"], nc_rb["services"]["cab"]["reason"]),
                         ("undetermined", "missing_current_cab_net"))
        self.assertIsNone(nc_rb["mix_shift_contribution_pct"])

        non_primary_rows = [
            ("2026-07", "FeOnlyCity", "T1", 1000, 10000, 5000, 4000, 2000,
             {"bike_rr": None, "bike_net": None, "auto_rr": None, "auto_net": None, "cab_rr": None, "cab_net": None}),
            ("2026-08", "FeOnlyCity", "T1", 800, 8000, 4000, 3200, 1600,
             {"bike_rr": None, "bike_net": None, "auto_rr": None, "auto_net": None, "cab_rr": None, "cab_net": None}),
        ]
        non_primary = investigate_funnel(dataset(non_primary_rows), requested_months=["2026-08"], cities=["FeOnlyCity"])
        self.assertNotIn(FUNNEL_UNDETERMINED_WARNING, non_primary["warnings"])
        np_block = only(non_primary, "city", "FeOnlyCity")
        self.assertEqual(np_block["primary_stages"], ["fe"])
        self.assertFalse(np_block["service_breakdown"]["fe2rr"]["triggered"])
        self.assertFalse(np_block["service_breakdown"]["rr2net"]["triggered"])

    def test_service_breakdown_tier_group_and_national_scopes(self):
        # T2 has Alpha and Bravo; Bravo has null cab_net -> blocks only cab in T2 rr2net breakdown while bike/auto evaluate.
        # PairOk has Alpha + Charlie (complete) -> rolls up counts before computing service weights/rates.
        # National excludes Delta (Aug-only) and exposes coverage["rr2net_services"] only when triggered.
        rows = [
            ("2026-07", "Alpha", "T2", 600, 6000, 600, 480, 1000,
             {"bike_rr": 300, "bike_net": 240, "auto_rr": 200, "auto_net": 160, "cab_rr": 100, "cab_net": 80}),
            ("2026-07", "Bravo", "T2", 400, 4000, 400, 320, 1000,
             {"bike_rr": 200, "bike_net": 160, "auto_rr": 100, "auto_net": 80, "cab_rr": 100, "cab_net": 80}),
            ("2026-08", "Alpha", "T2", 480, 6000, 600, 384, 1000,
             {"bike_rr": 300, "bike_net": 168, "auto_rr": 200, "auto_net": 144, "cab_rr": 100, "cab_net": 72}),
            ("2026-08", "Bravo", "T2", 320, 4000, 400, 256, 1000,
             {"bike_rr": 200, "bike_net": 112, "auto_rr": 100, "auto_net": 72, "cab_rr": 100, "cab_net": None}),
            ("2026-07", "Charlie", "T1", 400, 4000, 400, 320, 1000,
             {"bike_rr": 200, "bike_net": 160, "auto_rr": 100, "auto_net": 80, "cab_rr": 100, "cab_net": 80}),
            ("2026-08", "Charlie", "T1", 320, 4000, 400, 256, 1000,
             {"bike_rr": 200, "bike_net": 112, "auto_rr": 100, "auto_net": 72, "cab_rr": 100, "cab_net": 72}),
            ("2026-08", "Delta", "T3", 500, 50000, 25000, 20000, 9000,
             {"bike_rr": 15000, "bike_net": 12000, "auto_rr": 5000, "auto_net": 4000, "cab_rr": 5000, "cab_net": 4000}),
        ]
        result = investigate_funnel(
            dataset(rows),
            requested_months=["2026-08"],
            tier="T2",
            groups=[("PairOk", ["Alpha", "Charlie"])],
            national=True,
        )
        t2 = only(result, "tier", "T2")
        self.assertEqual(t2["primary_stages"], ["rr2net"])
        t2_rb = t2["service_breakdown"]["rr2net"]
        self.assertEqual(t2_rb["verdict"], "partial_services_identified")
        self.assertEqual(t2_rb["material_services"], ["bike"])
        self.assertEqual(t2_rb["services"]["cab"]["verdict"], "undetermined")
        self.assertEqual(t2_rb["services"]["cab"]["blocking_city_keys"], ["bravo"])
        self.assertEqual(t2_rb["blocking_city_keys"], ["bravo"])
        self.assertEqual(t2["coverage"]["rr2net_service_blocked_city_counts"], {"bike": 0, "auto": 0, "cab": 1})
        self.assertNotIn("fe2rr_service_blocked_city_counts", t2["coverage"])
        member_drivers = {m["city"]: m["rr2net_service_drivers"] for m in t2["members"]}
        self.assertEqual(member_drivers, {"Alpha": ["bike"], "Bravo": ["bike"]})

        pair = only(result, "group", "PairOk")
        pair_rb = pair["service_breakdown"]["rr2net"]
        self.assertEqual(pair_rb["verdict"], "services_identified")
        self.assertEqual(pair_rb["material_services"], ["bike"])
        self.assertAlmostEqual(pair_rb["services"]["bike"]["contribution_pct"], 75.0)
        self.assertAlmostEqual(pair_rb["services"]["auto"]["contribution_pct"], 15.0)
        self.assertAlmostEqual(pair_rb["services"]["cab"]["contribution_pct"], 10.0)
        self.assertAlmostEqual(pair_rb["mix_shift_contribution_pct"], 0.0)
        self.assertAlmostEqual(pair_rb["other_services_contribution_pct"], 0.0)

        nat = only(result, "national")
        nat_rb = nat["service_breakdown"]["rr2net"]
        self.assertTrue(nat_rb["triggered"])
        self.assertEqual(nat_rb["material_services"], ["bike"])
        self.assertEqual(nat["coverage"]["rr2net_services"]["bike"]["matched_city_count"], 3)
        self.assertEqual(nat["coverage"]["rr2net_services"]["cab"]["matched_city_count"], 2)
        self.assertIsNone(nat_rb["mix_shift_contribution_pct"])
        self.assertNotIn("fe2rr_services", nat["coverage"])


class FunnelInvestigationCliTests(unittest.TestCase):
    def invoke(self, arguments, data=None):
        rows = [
            ("2026-07", "Alpha", "T1", 1000, 10000, 5000, 4000, 2000),
            ("2026-08", "Alpha", "T1", 800, 9000, 4500, 3600, 1800),
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
        self.assertEqual(result["blocks"][0]["registration_check"]["verdict"], "registration_dip")
        self.assertAlmostEqual(result["blocks"][0]["registration_check"]["registrations_change_pct"], -10.0)

        code2, result2 = self.invoke([
            "investigate-funnel", "--month", "2026-08", "--city", "Alpha", "--stage", "fe2rr",
        ])
        self.assertEqual(code2, 0)
        self.assertEqual(result2["stages"], ["fe2rr"])
        self.assertEqual(list(result2["blocks"][0]["stages"]), ["fe2rr"])
        self.assertEqual(result2["blocks"][0]["registration_check"]["verdict"], "not_triggered")
        self.assertEqual(result2["blocks"][0]["service_breakdown"]["fe2rr"]["verdict"], "not_triggered")
        self.assertEqual(result2["blocks"][0]["service_breakdown"]["rr2net"]["verdict"], "not_triggered")

    def test_cli_surfaces_service_breakdown_when_fe2rr_or_rr2net_is_primary(self):
        rows = [
            ("2026-07", "Alpha", "T1", 1000, 10000, 2000, 1600, 2000,
             {"bike_rr": 1000, "bike_net": 800, "auto_rr": 500, "auto_net": 400, "cab_rr": 500, "cab_net": 400}),
            ("2026-08", "Alpha", "T1", 850, 10000, 1700, 1360, 2000,
             {"bike_rr": 700, "bike_net": 560, "auto_rr": 700, "auto_net": 560, "cab_rr": 300, "cab_net": 240}),
        ]
        code, result = self.invoke(
            ["investigate-funnel", "--month", "2026-08", "--city", "Alpha"],
            data=dataset(rows),
        )
        self.assertEqual(code, 0)
        sb = result["blocks"][0]["service_breakdown"]
        self.assertTrue(sb["fe2rr"]["triggered"])
        self.assertEqual(sb["fe2rr"]["material_services"], ["bike", "cab"])
        self.assertFalse(sb["rr2net"]["triggered"])

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
