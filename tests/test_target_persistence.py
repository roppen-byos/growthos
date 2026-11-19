import unittest

from growthos.analysis import target_cities, target_facts
from growthos.classification import normalize_city_key
from growthos.contracts import previous_month, validate_data
from growthos.errors import GrowthOSError
from growthos.targets import validate_targets
from fixtures import demo_payload, demo_targets


def observations(rows):
    """rows: (month, city, tier, platform_ftu)."""
    raw = demo_payload()
    months = sorted({m for m, *_ in rows})
    raw["months"] = months
    raw["month_metadata"] = [m for m in raw["month_metadata"] if m["month"] in months]
    base = raw["facts"][-1]
    raw["facts"] = [
        {**base, "month": month, "city": city, "city_key": normalize_city_key(city),
         "tier": tier, "source_cities": [city], "platform_ftu": actual}
        for month, city, tier, actual in rows
    ]
    return validate_data(raw)


def plan(month, values, *, overall=1000, fallback=100):
    raw = demo_targets(month).data
    raw["rows"] = [r for r in raw["rows"] if r["kind"] != "city"]
    for row in raw["rows"]:
        value = overall if row["kind"] == "overall" else fallback
        row["platform_ftu_target"] = value
        row["registrations_target"] = 2 * value
    raw["rows"] += [
        {"month": month, "kind": "city", "city_key": normalize_city_key(city),
         "city": city, "platform_ftu_target": target, "registrations_target": 2 * target}
        for city, target in values
    ]
    return validate_targets(raw)


class TargetPersistenceTests(unittest.TestCase):
    def build(self, rows, september, october, **kwargs):
        return observations(rows), plan("2026-09", september, **kwargs), plan("2026-10", october, **kwargs)

    def test_widening_miss_is_listed_with_prior_evidence(self):
        data, sep, oct_ = self.build(
            [("2026-09", "Alpha", "T1", 80), ("2026-10", "Alpha", "T1", 60)],
            [("Alpha", 100)], [("Alpha", 100)])
        result = target_cities(data, oct_, "2026-10", mode="all", previous_targets=sep)
        persistence = result["persistent_failure"]
        self.assertTrue(persistence["available"])
        self.assertEqual(persistence["previous_month"], "2026-09")
        self.assertEqual((persistence["city_count"], persistence["checked_city_count"]), (1, 1))
        row = persistence["rows"][0]
        self.assertEqual((row["city"], row["failing_since"]), ("Alpha", "2026-09"))
        self.assertAlmostEqual(row["current_shortfall_pct"], 40.0)
        self.assertAlmostEqual(row["previous_shortfall_pct"], 20.0)
        self.assertAlmostEqual(row["shortfall_pct_delta"], 20.0)
        # Existing returned rows stay unchanged; the table is additive.
        self.assertNotIn("previous_shortfall_pct", result["rows"][0])

    def test_exactly_five_percentage_point_widening_is_excluded(self):
        data, sep, oct_ = self.build(
            [("2026-09", "Alpha", "T1", 90), ("2026-10", "Alpha", "T1", 85)],
            [("Alpha", 100)], [("Alpha", 100)])
        persistence = target_cities(
            data, oct_, "2026-10", mode="all", previous_targets=sep)["persistent_failure"]
        self.assertEqual(persistence["city_count"], 0)
        self.assertEqual(persistence["excluded_city_counts"]["shortfall_pct_delta_not_above_5"], 1)

    def test_prior_exactly_five_percent_is_not_a_prior_miss(self):
        data, sep, oct_ = self.build(
            [("2026-09", "Alpha", "T1", 95), ("2026-10", "Alpha", "T1", 60)],
            [("Alpha", 100)], [("Alpha", 100)])
        persistence = target_cities(
            data, oct_, "2026-10", mode="all", previous_targets=sep)["persistent_failure"]
        self.assertEqual(persistence["city_count"], 0)
        self.assertEqual(persistence["excluded_city_counts"]["prior_not_above_5_percent"], 1)

    def test_improving_but_still_failing_city_is_not_listed(self):
        data, sep, oct_ = self.build(
            [("2026-09", "Alpha", "T1", 60), ("2026-10", "Alpha", "T1", 70)],
            [("Alpha", 100)], [("Alpha", 100)])
        persistence = target_cities(
            data, oct_, "2026-10", mode="all", previous_targets=sep)["persistent_failure"]
        self.assertEqual(persistence["city_count"], 0)
        self.assertEqual(persistence["excluded_city_counts"]["shortfall_pct_delta_not_above_5"], 1)

    def test_missing_prior_operands_are_undetermined(self):
        data, sep, oct_ = self.build(
            [("2026-09", "No Actual", "T1", None), ("2026-09", "Zero Target", "T1", 10),
             ("2026-09", "Missing Target", "T1", 10),
             ("2026-10", "No Actual", "T1", 40), ("2026-10", "Zero Target", "T1", 40),
             ("2026-10", "Missing Target", "T1", 40), ("2026-10", "Absent Prior", "T1", 40)],
            [("No Actual", 100), ("Zero Target", 0)],
            [("No Actual", 100), ("Zero Target", 100), ("Missing Target", 100), ("Absent Prior", 100)])
        persistence = target_cities(
            data, oct_, "2026-10", mode="all", previous_targets=sep)["persistent_failure"]
        self.assertEqual(persistence["city_count"], 0)
        self.assertEqual(persistence["undetermined_city_count"], 4)
        counts = persistence["excluded_city_counts"]
        self.assertEqual(counts["missing_prior_actual"], 1)
        self.assertEqual(counts["nonpositive_prior_target"], 1)
        self.assertEqual(counts["missing_prior_target"], 1)
        self.assertEqual(counts["missing_prior_city_observation"], 1)

    def test_annotation_follows_the_returned_page_only(self):
        data, sep, oct_ = self.build(
            [("2026-09", "Alpha", "T1", 10), ("2026-09", "Beta", "T1", 20),
             ("2026-09", "Gamma", "T1", 30),
             ("2026-10", "Alpha", "T1", 0), ("2026-10", "Beta", "T1", 0),
             ("2026-10", "Gamma", "T1", 0)],
            [("Alpha", 100), ("Beta", 100), ("Gamma", 100)],
            [("Alpha", 100), ("Beta", 100), ("Gamma", 100)])
        persistence = target_cities(
            data, oct_, "2026-10", mode="all", limit=1, previous_targets=sep)["persistent_failure"]
        self.assertEqual((persistence["page_city_count"], persistence["city_count"]), (1, 1))
        self.assertEqual([r["city"] for r in persistence["rows"]], ["Alpha"])

    def test_facts_check_only_currently_qualifying_page_rows(self):
        data, sep, oct_ = self.build(
            [("2026-09", "Above", "T1", 100), ("2026-09", "Below", "T1", 10),
             ("2026-10", "Above", "T1", 200), ("2026-10", "Below", "T1", 20)],
            [("Above", 100), ("Below", 100)], [("Above", 100), ("Below", 100)])
        persistence = target_facts(data, oct_, "2026-10", previous_targets=sep)["persistent_failure"]
        self.assertEqual(persistence["page_city_count"], 2)
        self.assertEqual(persistence["checked_city_count"], 1)
        self.assertEqual(persistence["not_currently_qualifying_city_count"], 1)
        above = target_facts(data, oct_, "2026-10", attainment="above", previous_targets=sep)["persistent_failure"]
        self.assertEqual((above["checked_city_count"], above["not_currently_qualifying_city_count"]), (0, 1))

    def test_fallback_queue_and_absent_prior_month_are_unavailable(self):
        data, sep, oct_ = self.build(
            [("2026-09", "Alpha", "T1", 80), ("2026-10", "Alpha", "T1", 60)],
            [("Alpha", 100)], [("Alpha", 100)])
        fallback = target_cities(data, oct_, "2026-10", queue="fallback", previous_targets=sep)
        self.assertEqual(fallback["persistent_failure"]["reason"], "pooled_fallback_has_no_city_targets")
        for result in (target_cities(data, oct_, "2026-10", mode="all"), target_facts(data, oct_, "2026-10")):
            persistence = result["persistent_failure"]
            self.assertFalse(persistence["available"])
            self.assertEqual(persistence["reason"], "no_previous_month_with_targets_and_actuals")
            self.assertEqual(persistence["rows"], [])

    def test_tier_uses_selected_month_membership_only(self):
        data, sep, oct_ = self.build(
            [("2026-09", "Alpha", "T1", 10), ("2026-10", "Alpha", "T2", 0)],
            [("Alpha", 100)], [("Alpha", 100)])
        persistence = target_cities(
            data, oct_, "2026-10", mode="all", tier="T2", previous_targets=sep)["persistent_failure"]
        self.assertEqual([r["city"] for r in persistence["rows"]], ["Alpha"])
        self.assertTrue(any("prior month's stored tier is not required" in w for w in persistence["warnings"]))

    def test_prior_targets_must_be_the_immediately_previous_month(self):
        data, _, oct_ = self.build(
            [("2026-09", "Alpha", "T1", 80), ("2026-10", "Alpha", "T1", 60)],
            [("Alpha", 100)], [("Alpha", 100)])
        with self.assertRaises(GrowthOSError) as caught:
            target_cities(data, oct_, "2026-10", mode="all", previous_targets=plan("2026-08", [("Alpha", 100)]))
        self.assertEqual(caught.exception.code, "COVERAGE")

    def test_previous_month_boundaries(self):
        self.assertEqual(previous_month("2026-10"), "2026-09")
        self.assertEqual(previous_month("2026-01"), "2025-12")
        with self.assertRaises(GrowthOSError):
            previous_month("0001-01")


if __name__ == "__main__":
    unittest.main()
