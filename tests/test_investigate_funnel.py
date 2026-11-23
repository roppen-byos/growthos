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
    FUNNEL_UNDETERMINED_WARNING,
)
from growthos.funnel_investigation import investigate_funnel


def dataset(rows):
    """Build detached MonthlyData from (month, city, tier, ftu, fe, rr, net[, registrations]) tuples."""
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
