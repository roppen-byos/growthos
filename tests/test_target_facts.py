import unittest

from growthos.analysis import target_cities, target_facts
from growthos.errors import GrowthOSError
from test_targets import observations, plan


class TargetFactsTests(unittest.TestCase):
    def setUp(self):
        self.actuals = observations([
            ("Above", 1100), ("At", 1000), ("Small Miss", 980),
            ("Exact Five", 950), ("Large Miss", 800), ("Missing", None),
        ])
        self.targets = plan([(c, 1000) for c in (
            "Above", "At", "Small Miss", "Exact Five", "Large Miss", "Missing")], overall=6000)

    def test_default_lookup_includes_above_at_small_exact_and_large_misses(self):
        result = target_facts(self.actuals, self.targets, "2026-10")
        rows = {r["city"]: r for r in result["rows"]}
        self.assertEqual(len(rows), 6)
        self.assertEqual((rows["Above"]["shortfall"], rows["Above"]["shortfall_pct"], rows["Above"]["attainment"]), (-100, -10, "above"))
        self.assertEqual(rows["At"]["shortfall"], 0)
        self.assertEqual(rows["At"]["attainment"], "at")
        self.assertEqual(rows["Small Miss"]["shortfall"], 20)
        self.assertEqual(rows["Exact Five"]["shortfall_pct"], 5)
        self.assertFalse(rows["Exact Five"]["qualifies_for_shortlist"])
        self.assertTrue(rows["Large Miss"]["qualifies_for_shortlist"])
        self.assertEqual(rows["Missing"]["attainment"], "unavailable")
        self.assertEqual(rows["Missing"]["actual_unavailable_reason"], "missing_platform_ftu")
        self.assertEqual(result["overall"]["actual"], 4830)
        self.assertEqual(result["overall"]["actual_coverage"]["skipped_null_ftu_city_count"], 1)

    def test_city_filter_preserves_month_wide_totals_and_all_target_evidence(self):
        full = target_facts(self.actuals, self.targets, "2026-10")
        filtered = target_facts(self.actuals, self.targets, "2026-10", cities=["above"])
        self.assertEqual([r["city"] for r in filtered["rows"]], ["Above"])
        for key in ("overall", "target_provenance", "reconciliation"):
            self.assertEqual(filtered[key], full[key])
        self.assertEqual(filtered["coverage"]["actual_city_count"], 6)
        self.assertEqual(filtered["coverage"]["matching_city_count"], 1)
        with self.assertRaises(GrowthOSError):
            target_facts(self.actuals, self.targets, "2026-10", cities=["Unknown City"])

    def test_attainment_filters_are_exact_not_the_five_percent_policy(self):
        expected = {"above": ["Above"], "at": ["At"],
                    "below": ["Exact Five", "Large Miss", "Small Miss"], "unavailable": ["Missing"]}
        for attainment, names in expected.items():
            with self.subTest(attainment=attainment):
                result = target_facts(self.actuals, self.targets, "2026-10", attainment=attainment)
                self.assertEqual([r["city"] for r in result["rows"]], names)
        self.assertEqual(target_facts(self.actuals, self.targets, "2026-10", cities=["Above"], attainment="below")["rows"], [])

    def test_numeric_sort_directions_null_last_and_city_ties(self):
        actuals = observations([("Beta", 700), ("Alpha", 700), ("Above", 1200), ("Missing", None)])
        targets = plan([(c, 1000) for c in ("Beta", "Alpha", "Above", "Missing")])
        descending = target_facts(actuals, targets, "2026-10", sort_by="shortfall", descending=True)
        self.assertEqual([r["city"] for r in descending["rows"]], ["Alpha", "Beta", "Above", "Missing"])
        ascending = target_facts(actuals, targets, "2026-10", sort_by="shortfall")
        self.assertEqual([r["city"] for r in ascending["rows"]], ["Above", "Alpha", "Beta", "Missing"])
        for sort_by in ("target", "actual", "shortfall-pct", "city"):
            self.assertEqual(target_facts(actuals, targets, "2026-10", sort_by=sort_by)["row_count"], 4)

    def test_percent_sort_uses_exact_ratios_when_display_floats_tie(self):
        target = 4 * ((2**63 - 1) // 4)
        actual = target - target // 4
        result = target_facts(observations([("Alpha", actual), ("Beta", actual - 1)]),
                              plan([("Alpha", target), ("Beta", target)]), "2026-10",
                              sort_by="shortfall-pct", descending=True)
        self.assertEqual(result["rows"][0]["shortfall_pct"], result["rows"][1]["shortfall_pct"])
        self.assertEqual([r["city"] for r in result["rows"]], ["Beta", "Alpha"])

    def test_zero_and_missing_targets_remain_visible_with_reasons_not_fallback(self):
        actuals = observations([("Zero", 0), ("Blank", 10), ("New City", 10)])
        targets = plan([("Zero", 0), ("Blank", None), ("Target Only", 100)], fallback=50)
        rows = {r["city"]: r for r in target_facts(actuals, targets, "2026-10")["rows"]}
        self.assertEqual(rows["Zero"]["attainment"], "at")
        self.assertEqual(rows["Zero"]["shortfall"], 0)
        self.assertIsNone(rows["Zero"]["shortfall_pct"])
        self.assertEqual(rows["Zero"]["shortfall_pct_unavailable_reason"], "nonpositive_target")
        self.assertIsNone(rows["Blank"]["target"])
        self.assertEqual(rows["Blank"]["target_source"], "city")
        self.assertEqual(rows["Blank"]["target_unavailable_reason"], "missing_target")
        self.assertEqual(rows["Target Only"]["actual_unavailable_reason"], "missing_city_observation")
        self.assertEqual(rows["New City"]["target"], 50)
        self.assertEqual(rows["New City"]["target_source"], "new_launch")

    def test_canonical_alias_filter_keeps_merged_target_and_single_actual(self):
        actuals = observations([("Trivandrum", 110)])
        targets = plan([("Trivandrum", 100)])
        raw = targets.data
        next(r for r in raw["rows"] if r["kind"] == "city")["source_targets"] = [
            {"city": "Attingal", "target": 30}, {"city": "Trivandrum", "target": 70}]
        from growthos.targets import validate_targets
        result = target_facts(actuals, validate_targets(raw), "2026-10", cities=["Attingal", "Trivandrum"])
        self.assertEqual(result["row_count"], 1)
        self.assertEqual((result["rows"][0]["target"], result["rows"][0]["actual"]), (100, 110))
        self.assertEqual(len(result["rows"][0]["source_targets"]), 2)

    def test_pagination_follows_full_filter_and_sort_and_keeps_summary(self):
        full = target_facts(self.actuals, self.targets, "2026-10", attainment="below", sort_by="shortfall", descending=True)
        rows = []
        for offset in range(3):
            page = target_facts(self.actuals, self.targets, "2026-10", attainment="below", sort_by="shortfall", descending=True, offset=offset, limit=1)
            self.assertEqual(page["coverage"], full["coverage"])
            rows.extend(page["rows"])
        self.assertEqual(rows, full["rows"])
        self.assertEqual(target_facts(self.actuals, self.targets, "2026-10", offset=99)["rows"], [])

    def test_facts_do_not_require_overall_gap_and_agree_with_selection(self):
        actuals = observations([("Alpha", 800), ("Beta", 980)])
        targets = plan([("Alpha", 1000), ("Beta", 1000)], overall=1000)
        facts = target_facts(actuals, targets, "2026-10")
        self.assertEqual(facts["overall"]["shortfall"], 0)
        self.assertEqual(facts["row_count"], 2)
        selected = target_cities(actuals, targets, "2026-10", mode="all")
        self.assertEqual([r["city"] for r in selected["rows"]], ["Alpha"])
        for key in ("target", "actual", "shortfall", "shortfall_pct", "target_source", "source_targets"):
            self.assertEqual(selected["rows"][0][key], facts["rows"][0][key])

    def test_invalid_parameters_are_rejected_without_echoing_values(self):
        for kwargs in ({"attainment": "PRIVATE_marker"}, {"sort_by": "PRIVATE_marker"},
                       {"descending": "PRIVATE_marker"}, {"limit": 101}, {"offset": -1}):
            with self.subTest(kwargs=kwargs), self.assertRaises(GrowthOSError) as caught:
                target_facts(self.actuals, self.targets, "2026-10", **kwargs)
            self.assertNotIn("PRIVATE", caught.exception.message)
