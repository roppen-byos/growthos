import unittest

from growthos.analysis import target_cities, target_facts
from growthos.contracts import VOLUMES, validate_data
from growthos.errors import GrowthOSError
from fixtures import demo_payload
from test_targets import observations, plan


def tiered(values):
    raw = observations([(city, actual) for city, _, actual in values]).data
    tiers = {city: tier for city, tier, _ in values}
    for row in raw["facts"]:
        row["tier"] = tiers[row["city"]]
    return validate_data(raw)


class TargetTierTests(unittest.TestCase):
    def setUp(self):
        self.data = tiered([("Alpha", "T2", 60), ("Beta", "T2", 75), ("Gamma", "T2", 110),
                            ("Outside T1", "T1", 0), ("Outside T3", "T3", 0)])
        self.plan = plan([("Alpha", 100), ("Beta", 100), ("Gamma", 100),
                          ("Outside T1", 1000), ("Outside T3", 100), ("Plan Only", 10000)], overall=5000)

    def test_top_covers_tier_own_gap_not_national_gap(self):
        result = target_cities(self.data, self.plan, "2026-10", tier="t2")
        self.assertEqual(result["scope"]["tier"], "T2")
        self.assertEqual(result["overall"]["target_source"], "tier_city_target_sum")
        self.assertEqual((result["overall"]["target"], result["overall"]["actual"], result["overall"]["shortfall"]), (300, 245, 55))
        self.assertEqual([r["city"] for r in result["rows"]], ["Alpha", "Beta"])
        self.assertEqual([r["cumulative_shortfall"] for r in result["rows"]], [40, 65])
        self.assertEqual(result["coverage"]["overshoot"], 10)
        self.assertEqual(result["coverage"]["actual_city_count"], 3)
        self.assertEqual(result["reconciliation"]["national_grand_total"], 5000)
        national = target_cities(self.data, self.plan, "2026-10")
        self.assertNotIn("scope", national)
        self.assertEqual(national["overall"]["target"], 5000)

    def test_facts_city_filters_and_pages_do_not_redefine_tier_totals(self):
        full = target_facts(self.data, self.plan, "2026-10", tier="T2")
        self.assertEqual([r["city"] for r in full["rows"]], ["Alpha", "Beta", "Gamma"])
        filtered = target_facts(self.data, self.plan, "2026-10", tier="T2", cities=["Alpha"], attainment="below")
        self.assertEqual(filtered["overall"], full["overall"])
        self.assertEqual(filtered["filters"]["tier"], "T2")
        self.assertEqual(filtered["row_count"], 1)
        first = target_cities(self.data, self.plan, "2026-10", tier="T2", limit=1)
        second = target_cities(self.data, self.plan, "2026-10", tier="T2", offset=1, limit=1)
        self.assertEqual(first["coverage"], second["coverage"])
        self.assertEqual(first["coverage"]["selected_city_count"], 2)
        self.assertEqual(second["rows"][0]["cumulative_shortfall"], 65)

    def test_null_city_retains_target_and_fallback_stays_out_of_tier_calculation(self):
        raw = tiered([("Known", "T2", 80), ("Null FTU", "T2", None), ("Fallback", "T2", 0),
                      ("Outside", "T1", 1000)]).data
        for row in raw["facts"]:
            if row["city"] == "Null FTU":
                for metric in VOLUMES[2:]:
                    row[metric] = None
        result = target_cities(validate_data(raw), plan([("Known", 100), ("Null FTU", 120)], overall=9999, fallback=50), "2026-10", tier="T2")
        self.assertEqual((result["overall"]["target"], result["overall"]["actual"], result["overall"]["shortfall"]), (220, 80, 140))
        self.assertEqual(result["coverage"]["uncovered_amount"], 120)
        self.assertEqual(result["coverage"]["fallback_city_count"], 1)
        self.assertEqual(result["overall"]["actual_coverage"]["install_or_registration_only_city_count"], 1)
        self.assertEqual([r["city"] for r in result["rows"]], ["Known"])
        self.assertEqual((result["fallback"]["city_count"], result["fallback"]["target"]), (1, 50))
        self.assertEqual([r["city"] for r in target_cities(validate_data(raw), plan([("Known", 100), ("Null FTU", 120)], overall=9999, fallback=50), "2026-10", tier="T2", queue="fallback")["rows"]], ["Fallback"])

    def test_tier_with_no_plan_matched_cities_keeps_gap_unavailable(self):
        data = tiered([("Fallback A", "T2", 10), ("Fallback B", "T2", 20)])
        result = target_cities(data, plan([("Elsewhere", 100)], overall=500, fallback=40), "2026-10", tier="T2")
        self.assertIsNone(result["overall"]["target"])
        self.assertIsNone(result["overall"]["actual"])
        self.assertIsNone(result["overall"]["shortfall"])
        self.assertEqual(result["overall"]["unavailable_reason"], "no_plan_matched_cities")
        self.assertEqual(result["rows"], [])
        self.assertEqual(result["fallback"]["city_count"], 2)

    def test_missing_assigned_target_blocks_tier_x_not_observed_actual(self):
        targets = plan([("Alpha", 100), ("Beta", None), ("Gamma", 100)])
        result = target_cities(self.data, targets, "2026-10", tier="T2")
        self.assertIsNone(result["overall"]["target"])
        self.assertEqual(result["overall"]["actual"], 245)
        self.assertIsNone(result["overall"]["shortfall"])
        self.assertEqual(result["overall"]["unavailable_reason"], "missing_tier_city_target")
        self.assertEqual(result["rows"], [])
        all_rows = target_cities(self.data, targets, "2026-10", tier="T2", mode="all")
        self.assertEqual([r["city"] for r in all_rows["rows"]], ["Alpha"])

    def test_empty_tier_and_invalid_tier_fail_with_reviewed_errors(self):
        for operation in (target_cities, target_facts):
            for value, code in (("T1", "COVERAGE"), ("T4", "ARGUMENTS"), ("PRIVATE_marker", "ARGUMENTS")):
                with self.subTest(operation=operation, value=value):
                    with self.assertRaises(GrowthOSError) as caught:
                        operation(tiered([("Only T2", "T2", 10)]), plan([("Only T2", 100)]), "2026-10", tier=value)
                    self.assertEqual(caught.exception.code, code)
                    self.assertNotIn("PRIVATE", caught.exception.message)

    def test_selected_month_stored_tier_controls_ludhiana_and_moved_cities(self):
        raw = tiered([("Ludhiana", "T3", 50), ("Moved", "T3", 50), ("Current T2", "T2", 80)]).data
        raw["months"] = ["2026-09", "2026-10"]
        raw["month_metadata"] = demo_payload()["month_metadata"][1:]
        moved = next(r for r in raw["facts"] if r["city"] == "Moved")
        raw["facts"].append({**moved, "month": "2026-09", "tier": "T2"})
        data = validate_data(raw)
        targets = plan([("Ludhiana", 100), ("Moved", 100), ("Current T2", 100)])
        self.assertEqual([r["city"] for r in target_facts(data, targets, "2026-10", tier="T2")["rows"]], ["Current T2"])
        self.assertEqual([r["city"] for r in target_facts(data, targets, "2026-10", tier="T3")["rows"]], ["Ludhiana", "Moved"])

    def test_no_observed_tier_ftu_does_not_use_other_tier_actuals(self):
        data = tiered([("T2 Null", "T2", None), ("Known T1", "T1", 1000)])
        result = target_cities(data, plan([("T2 Null", 100)]), "2026-10", tier="T2")
        self.assertEqual(result["overall"]["target"], 100)
        self.assertIsNone(result["overall"]["actual"])
        self.assertIsNone(result["overall"]["shortfall"])
        self.assertEqual(result["overall"]["unavailable_reason"], "no_observed_platform_ftu")

    def test_tier_does_not_need_national_target_and_zero_gap_keeps_all_mode(self):
        result = target_cities(self.data, plan([("Alpha", 100), ("Beta", 100), ("Gamma", 100)], overall=None), "2026-10", tier="T2")
        self.assertEqual(result["overall"]["shortfall"], 55)
        data = tiered([("Alpha", "T2", 60), ("Beta", "T2", 75), ("Gamma", "T2", 1000)])
        self.assertEqual(target_cities(data, self.plan, "2026-10", tier="T2")["rows"], [])
        self.assertEqual([r["city"] for r in target_cities(data, self.plan, "2026-10", tier="T2", mode="all")["rows"]], ["Alpha", "Beta"])
