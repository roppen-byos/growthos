import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from fixtures import demo_payload, fixture_reader
from growthos.classification import normalize_city_key
from growthos.cli import main
from growthos.contracts import validate_data
from growthos.errors import GrowthOSError
from growthos.investigation import investigate
from growthos.selection import parse_investigation_group
from growthos.targets import validate_targets


def dataset(rows):
    """Build one detached dataset from (month, city, tier, ftu, registrations) rows."""
    raw = demo_payload()
    months = sorted({row[0] for row in rows})
    raw["months"] = months
    raw["month_metadata"] = [item for item in raw["month_metadata"] if item["month"] in months]
    base = raw["facts"][-1]
    raw["facts"] = [
        {**base, "month": value, "city": city, "city_key": normalize_city_key(city),
         "source_cities": [city], "tier": tier, "platform_ftu": ftu, "registrations": regs}
        for value, city, tier, ftu, regs in rows
    ]
    return validate_data(raw)


def plan(month, cities, *, overall=(None, None), fallback=(None, None)):
    rows = [{"month": month, "kind": "city", "city_key": normalize_city_key(name), "city": name,
             "platform_ftu_target": ftu, "registrations_target": regs}
            for name, ftu, regs in cities]
    rows.append({"month": month, "kind": "new_launch", "city_key": "", "city": "New launch",
                 "platform_ftu_target": fallback[0], "registrations_target": fallback[1]})
    rows.append({"month": month, "kind": "overall", "city_key": "", "city": "Grand Total",
                 "platform_ftu_target": overall[0], "registrations_target": overall[1]})
    return validate_targets({"month": month, "rows": rows})


def report(rows, plans, **selectors):
    months = selectors.pop("requested_months", sorted({row[0] for row in rows}))
    return investigate(dataset(rows), {item.data["month"]: item for item in plans},
                       requested_months=months, **selectors)


def only(result, kind, label=None):
    found = [block for block in result["blocks"] if block["kind"] == kind
             and (label is None or block["label"] == label)]
    if len(found) != 1:
        raise AssertionError(f"expected one {kind} block, found {len(found)}")
    return found[0]


class InvestigationCalculationTests(unittest.TestCase):
    def one(self, ftu_actual, reg_actual, *, target=(1000, 2000), tier="T1"):
        return report(
            [("2026-10", "Alpha", tier, ftu_actual, reg_actual)],
            [plan("2026-10", [("Alpha", *target)])],
            cities=["Alpha"],
        )

    def test_exact_five_percent_reg_miss_is_excluded_and_just_below_flags_issue(self):
        # Alpha: reg 1900 vs target 2000 -> exact -5.0% -> no_material_registration_miss
        # Bravo: reg 1899 vs target 2000 -> -5.05% (< -5%) -> registration_issue (even though contribution is 25.25%)
        # Charlie: 64-bit precision boundary just below -5% -> registration_issue
        result = report(
            [
                ("2026-10", "Alpha", "T1", 800, 1900),
                ("2026-10", "Bravo", "T1", 800, 1899),
                ("2026-10", "Charlie", "T1", 8 * 10**14, 19 * 10**15 - 1),
            ],
            [plan("2026-10", [
                ("Alpha", 1000, 2000),
                ("Bravo", 1000, 2000),
                ("Charlie", 10**15, 2 * 10**16),
            ])],
            cities=["Alpha", "Bravo", "Charlie"],
        )
        alpha = only(result, "city", "Alpha")
        bravo = only(result, "city", "Bravo")
        charlie = only(result, "city", "Charlie")
        self.assertEqual((alpha["reg_gap_pct"], alpha["contribution_pct"], alpha["verdict"], alpha["reason"]),
                         (-5.0, 25.0, "no_material_registration_miss", "registration_variance_within_5_percent"))
        self.assertEqual((bravo["reg_gap_pct"], bravo["contribution_pct"], bravo["verdict"], bravo["reason"]),
                         (-5.05, 25.25, "registration_issue", None))
        self.assertEqual((charlie["verdict"], charlie["reason"]), ("registration_issue", None))
        self.assertEqual(alpha["flags"], [])
        self.assertEqual(result["months"], ["2026-10"])

    def test_over_explained_is_retained_and_flagged(self):
        block = only(self.one(800, 1500), "city")
        self.assertEqual((block["registration_effect"], block["unexplained_gap"]),
                         (-250.0, 50.0))
        self.assertEqual(block["contribution_pct"], 125.0)
        self.assertEqual((block["verdict"], block["flags"]), ("registration_issue", ["over_explained"]))

    def test_no_registration_shortfall_is_reported_when_reg_target_met(self):
        block = only(self.one(800, 2100), "city")
        self.assertEqual(block["contribution_pct"], -25.0)
        self.assertEqual((block["verdict"], block["reason"]),
                         ("no_registration_shortfall", "registration_target_met"))
        self.assertEqual(block["flags"], [])

    def test_target_met_is_not_applicable_and_never_prioritised(self):
        block = only(self.one(1200, 1500), "city")
        self.assertEqual((block["verdict"], block["reason"]), ("not_applicable", "ftu_target_met"))
        self.assertEqual((block["shortlist_eligible"], block["shortlist_eligibility_reason"]),
                         (False, "not_above_5_percent"))

    def test_every_missing_or_nonpositive_operand_stays_undetermined(self):
        cases = (
            ({"target": (None, 2000)}, "missing_ftu_target"),
            ({"target": (1000, None)}, "missing_registration_target"),
            ({"ftu_actual": None}, "missing_ftu_actual"),
            ({"reg_actual": None}, "missing_registration_actual"),
            ({"target": (0, 2000), "ftu_actual": 0}, "nonpositive_ftu_target"),
            ({"target": (1000, 0)}, "nonpositive_registration_target"),
        )
        for overrides, reason in cases:
            with self.subTest(reason=reason):
                block = only(self.one(overrides.get("ftu_actual", 800),
                                      overrides.get("reg_actual", 1720),
                                      target=overrides.get("target", (1000, 2000))), "city")
                self.assertEqual((block["verdict"], block["reason"]), ("undetermined", reason))
                self.assertIsNone(block["contribution_pct"])

    def test_named_cities_are_independent_blocks_and_are_never_summed(self):
        result = report(
            [("2026-10", "Alpha", "T1", 800, 1720), ("2026-10", "Bravo", "T1", 400, 900)],
            [plan("2026-10", [("Alpha", 1000, 2000), ("Bravo", 500, 1000)])],
            cities=["Alpha", "Bravo"],
        )
        kinds = [(block["kind"], block["label"]) for block in result["blocks"]]
        self.assertEqual(kinds, [("city", "Alpha"), ("city", "Bravo")])
        self.assertEqual([block["ftu_target"] for block in result["blocks"]], [1000, 500])

    def test_absent_city_and_unplanned_city_keep_specific_reasons(self):
        absent = report([("2026-10", "Bravo", "T1", 800, 1720)],
                        [plan("2026-10", [("Alpha", 1000, 2000), ("Bravo", 1000, 2000)])],
                        cities=["Alpha"])
        self.assertEqual(only(absent, "city")["reason"], "no_stored_city_observation")

        unplanned = report([("2026-10", "Echo", "T1", 800, 1720)],
                           [plan("2026-10", [("Alpha", 1000, 2000)], fallback=(600, 1200))],
                           cities=["Echo"])
        self.assertEqual(only(unplanned, "city")["reason"],
                         "pooled_new_launch_target_has_no_per_city_value")
        self.assertTrue(only(unplanned, "new_launch")["cohort"])

    def test_tier_aggregates_summed_effects_and_excludes_unplanned_cities(self):
        rows = [("2026-10", "Alpha", "T1", 800, 1720),
                ("2026-10", "Bravo", "T1", 400, 860),
                ("2026-10", "Echo", "T1", 100, 100)]
        result = report(rows, [plan("2026-10", [("Alpha", 1000, 2000), ("Bravo", 500, 1000)])],
                        tier="T1", national=True)
        tier = only(result, "tier")
        self.assertEqual((tier["registration_effect"], tier["ftu_gap"], tier["contribution_pct"]),
                         (-210.0, -300, 70.0))
        # Combined reg: 2580 vs 3000 (-14.0%, strictly < -5%) -> registration_issue
        self.assertEqual((tier["reg_gap"], tier["reg_gap_pct"], tier["verdict"]),
                         (-420, -14.0, "registration_issue"))
        self.assertEqual(tier["coverage"], {"member_city_count": 2, "blocked_city_count": 0,
                                            "unplanned_city_count": 1})
        self.assertEqual([item["city"] for item in tier["members"]], ["Alpha", "Bravo"])
        self.assertEqual(tier["members"][0]["reg_gap_pct"], -14.0)
        self.assertTrue(any("Tier contribution uses only cities with an explicit target" in warning
                            for warning in result["warnings"]))

    def test_incomplete_tier_member_blocks_the_aggregate_and_names_it(self):
        rows = [("2026-10", "Alpha", "T1", 800, 1720), ("2026-10", "Bravo", "T1", 400, 860)]
        result = report(rows, [plan("2026-10", [("Alpha", 1000, 2000), ("Bravo", 500, None)])],
                        tier="T1")
        tier = only(result, "tier")
        self.assertEqual((tier["verdict"], tier["reason"]),
                         ("undetermined", "incomplete_group_member"))
        self.assertEqual(tier["blocking_city_keys"], ["bravo"])
        self.assertEqual((tier["registration_effect"], tier["ftu_gap"]), (None, -300))
        self.assertIsNone(tier["contribution_pct"])

    def test_explicit_group_is_separate_from_tier_and_requires_every_member(self):
        rows = [("2026-10", "Alpha", "T1", 800, 1720), ("2026-10", "Bravo", "T2", 400, 860),
                ("2026-10", "Echo", "T3", 100, 100)]
        plans = [plan("2026-10", [("Alpha", 1000, 2000), ("Bravo", 500, 1000)])]
        result = report(rows, plans, groups=[("South", ["Alpha", "Bravo"])])
        group = only(result, "group")
        self.assertEqual((group["registration_effect"], group["ftu_gap"], group["contribution_pct"]),
                         (-210.0, -300, 70.0))

        blocked = report(rows, plans, groups=[("Mixed", ["Alpha", "Echo"])])
        group = only(blocked, "group")
        self.assertEqual((group["verdict"], group["reason"]), ("undetermined", "incomplete_group_member"))
        self.assertEqual(group["blocking_city_keys"], ["echo"])

    def test_group_and_duplicate_selectors_are_rejected_before_reading(self):
        with self.assertRaises(GrowthOSError):
            parse_investigation_group("Only:Alpha")
        with self.assertRaises(GrowthOSError):
            parse_investigation_group("NoColon")
        result = report(
            [("2026-10", "Trivandrum", "T3", 800, 1720)],
            [plan("2026-10", [("Trivandrum", 1000, 2000)])],
            cities=["Attingal"],
        )
        block = only(result, "city")
        self.assertEqual((block["label"], block["city_key"]), ("Trivandrum", "trivandrum"))

    def test_national_uses_grand_total_and_observed_actuals_with_coverage(self):
        rows = [("2026-10", "Alpha", "T1", 800, 1720),
                ("2026-10", "Bravo", "T1", 400, None),
                ("2026-10", "Echo", "T1", 100, 100)]
        result = report(rows, [plan("2026-10", [("Alpha", 1000, 2000)], overall=(2000, 4000))],
                        national=True)
        national = only(result, "national")
        self.assertEqual((national["ftu_target"], national["ftu_actual"], national["reg_actual"]),
                         (2000, 1300, 1820))
        self.assertEqual(national["coverage"]["platform_ftu"]["skipped_null_city_count"], 0)
        self.assertEqual(national["coverage"]["registrations"]["skipped_null_city_count"], 1)
        self.assertTrue(any("National diagnosis uses the supplied Grand Total" in warning
                            for warning in result["warnings"]))

        incomplete = report([("2026-10", "Alpha", "T1", None, 1720)],
                            [plan("2026-10", [("Alpha", 1000, 2000)], overall=(1000, 2000))],
                            national=True)
        self.assertEqual(only(incomplete, "national")["reason"], "national_actual_incomplete")

    def test_pooled_new_launch_is_cohort_level_and_discloses_mismatched_coverage(self):
        rows = [("2026-10", "Charlie", "T3", 10, None), ("2026-10", "Delta", "T3", None, 10)]
        result = report(rows, [plan("2026-10", [("Alpha", 1000, 2000)], fallback=(600, 1200))],
                        national=True)
        pooled = only(result, "new_launch")
        self.assertEqual((pooled["ftu_actual"], pooled["reg_actual"]), (10, 10))
        self.assertIn("coverage_mismatch", pooled["flags"])
        self.assertEqual(pooled["coverage"]["platform_ftu"]["observed_city_count"], 1)
        self.assertEqual(pooled["coverage"]["registrations"]["observed_city_count"], 1)

    def test_history_is_independent_per_month_and_reports_marker_gaps(self):
        rows = [("2026-09", "Alpha", "T1", 800, 1720), ("2026-10", "Alpha", "T1", 800, 1710)]
        plans = [plan("2026-09", [("Alpha", 1000, 2000)]),
                 plan("2026-10", [("Alpha", 1000, 2000)])]
        result = report(rows, plans, cities=["Alpha"], requested_months=["2026-09", "2026-10"],
                        registrations_months={"2026-10"})
        self.assertIsNone(result["month"])
        september = [block for block in result["blocks"] if block["month"] == "2026-09"][0]
        october = [block for block in result["blocks"] if block["month"] == "2026-10"][0]
        self.assertEqual(september["reason"], "registration_targets_not_imported")
        self.assertEqual((october["verdict"], october["reg_gap_pct"], october["contribution_pct"]),
                         ("registration_issue", -14.5, 72.5))
        self.assertNotIn("delta", json.dumps(result))

    def test_history_month_without_actuals_is_undetermined_not_dropped(self):
        result = report([("2026-10", "Alpha", "T1", 800, 1900)],
                        [plan("2026-10", [("Alpha", 1000, 2000)])],
                        cities=["Alpha"], requested_months=["2026-09", "2026-10"])
        blocks = {block["month"]: block for block in result["blocks"]}
        self.assertEqual(sorted(blocks), ["2026-09", "2026-10"])
        self.assertEqual(blocks["2026-09"]["reason"], "no_stored_month")
        self.assertEqual((blocks["2026-10"]["verdict"], blocks["2026-10"]["reg_gap_pct"], blocks["2026-10"]["contribution_pct"]),
                         ("no_material_registration_miss", -5.0, 25.0))


class InvestigationCliTests(unittest.TestCase):
    def invoke(self, arguments):
        output = io.StringIO()
        with patch("growthos.cli.connect_reader", fixture_reader), redirect_stdout(output):
            code = main(arguments)
        return code, json.loads(output.getvalue())

    def test_default_scope_is_national_with_the_pooled_block(self):
        code, result = self.invoke(["investigate", "--month", "2026-10"])
        self.assertEqual(code, 0)
        self.assertEqual([block["kind"] for block in result["blocks"]], ["national", "new_launch"])
        self.assertEqual(result["blocks"][0]["label"], "Grand Total")

    def test_national_investigation_uses_fixture_targets_and_guides_interpretation(self):
        code, result = self.invoke(["investigate", "--month", "2026-10", "--national"])
        self.assertEqual(code, 0)
        self.assertEqual(result["operation"], "investigate")
        self.assertEqual([block["kind"] for block in result["blocks"]], ["national", "new_launch"])
        national = result["blocks"][0]
        self.assertEqual((national["ftu_target"], national["ftu_actual"]), (150, 60))
        self.assertEqual(national["verdict"], "registration_issue")
        self.assertIn("over_explained", national["flags"])
        self.assertIn("does not show that registrations caused", result["method"]["interpretation"])

    def test_invalid_selectors_fail_with_sanitized_reviewed_codes(self):
        for arguments, code in (
            (["investigate", "--month", "2026-10", "--start", "2026-09"], "PERIOD"),
            (["investigate", "--month", "2026-10", "--group", "Solo:Alpha"], "BOUNDS"),
            (["investigate", "--month", "2026-10", "--group", "Plain"], "ARGUMENTS"),
            (["investigate", "--month", "2026-10", "--city", "Mumbai"], "CITY"),
        ):
            with self.subTest(code=code):
                status, result = self.invoke(arguments)
                self.assertEqual(status, 2)
                self.assertEqual(result["error"]["code"], code)
                self.assertNotIn("Mumbai", result["error"]["message"])
                self.assertNotIn("Solo", result["error"]["message"])

    def test_duplicate_city_after_alias_resolution_is_rejected(self):
        status, result = self.invoke(["investigate", "--month", "2026-10",
                                      "--city", "Harbor", "--city", "harbor"])
        self.assertEqual(status, 2)
        self.assertEqual(result["error"]["code"], "ARGUMENTS")


if __name__ == "__main__":
    unittest.main()
