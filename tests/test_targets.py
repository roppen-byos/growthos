import copy
from datetime import datetime, timezone
import unittest

from growthos.analysis import target_cities, target_city_review
from growthos.classification import normalize_city_key
from growthos.contracts import validate_data
from growthos.errors import GrowthOSError
from growthos.metrics import target_attainment
from growthos.selection import validate_target_request
from growthos.targets import TARGET_DB_FIELDS, target_schema_sql, targets_from_records, validate_targets
from fixtures import demo_payload, demo_targets


def observations(values):
    raw = demo_payload()
    raw["months"] = ["2026-10"]
    raw["month_metadata"] = raw["month_metadata"][-1:]
    base = raw["facts"][-1]
    raw["facts"] = [{**base, "city": city, "city_key": normalize_city_key(city),
                     "source_cities": [city], "platform_ftu": count} for city, count in values]
    return validate_data(raw)


def plan(values, overall=1000, fallback=100):
    raw = demo_targets().data
    raw["rows"] = [r for r in raw["rows"] if r["kind"] != "city"]
    for row in raw["rows"]:
        value = overall if row["kind"] == "overall" else fallback
        row["platform_ftu_target"] = value
        row["registrations_target"] = value
    raw["rows"] += [{"month": "2026-10", "kind": "city", "city_key": normalize_city_key(city),
                     "city": city, "platform_ftu_target": count,
                     "registrations_target": count}
                    for city, count in values]
    return validate_targets(raw)


class TargetAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.actuals = observations([("Zulu", 600), ("Beta", 700), ("Alpha", 700), ("Exact", 950)])
        self.targets = plan([(c, 1000) for c in ("Zulu", "Beta", "Alpha", "Exact")], overall=3450)

    def test_exact_5_percent_is_excluded_even_at_64_bit_boundary(self):
        self.assertFalse(target_attainment(1000, 950).qualifies)
        self.assertTrue(target_attainment(1000, 949).qualifies)
        large = 20 * ((2**63 - 1) // 20)
        self.assertFalse(target_attainment(large, large - large // 20).qualifies)
        self.assertTrue(target_attainment(large, large - large // 20 - 1).qualifies)
        self.assertEqual(target_attainment(1000, 949).shortfall_pct, 5.1)

    def test_descending_shortfall_city_ties_and_cumulative_overshoot(self):
        result = target_cities(self.actuals, self.targets, "2026-10")
        self.assertEqual(result["overall"]["shortfall"], 500)
        self.assertEqual([r["city"] for r in result["rows"]], ["Zulu", "Alpha"])
        self.assertEqual([r["cumulative_shortfall"] for r in result["rows"]], [400, 700])
        self.assertEqual(result["coverage"]["overshoot"], 200)
        self.assertEqual(result["coverage"]["covered_amount"], 500)
        self.assertEqual(result["coverage"]["uncovered_amount"], 0)
        self.assertEqual(result["coverage"]["excluded_city_counts"]["not_above_5_percent"], 1)

    def test_all_mode_keeps_all_eligible_and_paging_does_not_reselect(self):
        full = target_cities(self.actuals, self.targets, "2026-10", mode="all")
        self.assertEqual([r["city"] for r in full["rows"]], ["Zulu", "Alpha", "Beta"])
        self.assertEqual(full["coverage"]["selected_shortfall"], 1000)
        for mode, count in (("top", 2), ("all", 3)):
            rows = []
            for offset in range(count):
                page = target_cities(self.actuals, self.targets, "2026-10", mode=mode, offset=offset, limit=1)
                self.assertEqual(page["coverage"]["selected_city_count"], count)
                rows.extend(page["rows"])
            self.assertEqual(rows, target_cities(self.actuals, self.targets, "2026-10", mode=mode)["rows"])

    def test_insufficient_coverage_never_relaxes_threshold(self):
        targets = plan([(c, 1000) for c in ("Zulu", "Beta", "Alpha", "Exact")], overall=5000)
        result = target_cities(self.actuals, targets, "2026-10")
        self.assertEqual(len(result["rows"]), 3)
        self.assertEqual(result["coverage"]["uncovered_amount"], 1050)
        self.assertEqual(result["coverage"]["overshoot"], 0)

    def test_selection_is_not_capped_at_100_cities(self):
        names = [f"City {i:03}" for i in range(125)]
        actuals = observations([(c, 0) for c in names])
        targets = plan([(c, 100) for c in names], overall=20000)
        first = target_cities(actuals, targets, "2026-10", limit=100)
        self.assertEqual(first["coverage"]["selected_city_count"], 125)
        self.assertEqual(first["coverage"]["selected_shortfall"], 12500)
        self.assertEqual(first["coverage"]["uncovered_amount"], 7500)
        self.assertEqual(first["next_offset"], 100)
        second = target_cities(actuals, targets, "2026-10", offset=100, limit=100)
        self.assertEqual(second["row_count"], 25)
        self.assertEqual(second["rows"][0]["cumulative_shortfall"], 10100)

    def test_no_overall_shortfall_empties_top_but_not_all(self):
        for overall in (2950, 2900, 0):
            targets = plan([(c, 1000) for c in ("Zulu", "Beta", "Alpha", "Exact")], overall=overall)
            top = target_cities(self.actuals, targets, "2026-10")
            self.assertEqual(top["overall"]["shortfall"], 0)
            self.assertEqual(top["rows"], [])
            self.assertEqual(len(target_cities(self.actuals, targets, "2026-10", mode="all")["rows"]), 3)

    def test_fallback_is_pooled_and_kept_out_of_the_matched_queue(self):
        actuals = observations([("New A", 10), ("New B", 30), ("Explicit Zero", 0)])
        targets = plan([("Explicit Zero", 0)], overall=200, fallback=100)
        result = target_cities(actuals, targets, "2026-10", mode="all")
        self.assertEqual(result["rows"], [])
        self.assertEqual(result["queue"], "matched")
        self.assertEqual(result["overall"]["target"], 200)
        self.assertEqual(result["overall"]["shortfall"], 160)
        self.assertEqual(result["coverage"]["fallback_city_count"], 2)
        self.assertEqual(result["coverage"]["excluded_city_counts"]["nonpositive_target"], 1)
        summary = result["fallback"]
        self.assertEqual(
            (summary["target"], summary["actual"], summary["shortfall"], summary["shortfall_pct"], summary["qualifies"]),
            (100, 40, 60, 60, True),
        )
        self.assertEqual(
            (summary["city_count"], summary["observed_city_count"], summary["skipped_null_city_count"]),
            (2, 2, 0),
        )
        self.assertIn("combined observed", summary["disclosure"])
        self.assertFalse(any("Some target or actual operands are unavailable" in w for w in result["warnings"]))
        fallback = target_cities(actuals, targets, "2026-10", queue="fallback")
        self.assertEqual([r["city"] for r in fallback["rows"]], ["New A", "New B"])
        self.assertEqual([r["actual"] for r in fallback["rows"]], [10, 30])
        self.assertNotIn("target", fallback["rows"][0])
        self.assertEqual(fallback["coverage"]["selection_status"], "pooled_new_launch")
        self.assertEqual(fallback["overall"]["target"], 200)

    def test_missing_actuals_targets_and_nonpositive_fallback(self):
        actuals = observations([("Known", 10), ("Unknown", None), ("Blank Target", 1), ("New", 1)])
        targets = plan([("Known", 100), ("Unknown", 100), ("Blank Target", None), ("Absent", 100)], fallback=0)
        result = target_cities(actuals, targets, "2026-10", mode="all")
        self.assertEqual(result["overall"]["actual"], 12)
        self.assertEqual(result["overall"]["shortfall"], 988)
        self.assertEqual(result["coverage"]["uncovered_amount"], 898)
        self.assertEqual([r["city"] for r in result["rows"]], ["Known"])
        self.assertEqual(result["coverage"]["excluded_city_counts"], {
            "missing_actual": 2, "missing_target": 1, "nonpositive_target": 0, "not_above_5_percent": 0})
        top = target_cities(actuals, targets, "2026-10")
        self.assertEqual([r["city"] for r in top["rows"]], ["Known"])
        self.assertEqual(top["coverage"]["selection_status"], "complete")
        self.assertEqual(result["coverage"]["fallback_city_count"], 1)
        self.assertEqual(result["fallback"]["city_count"], 1)
        self.assertEqual((result["fallback"]["target"], result["fallback"]["actual"]), (0, 1))
        self.assertEqual(result["fallback"]["shortfall"], -1)
        self.assertEqual(result["fallback"]["unavailable_reason"], "nonpositive_target")
        self.assertFalse(result["fallback"]["qualifies"])
        rows = target_cities(actuals, targets, "2026-10", queue="fallback")["rows"]
        self.assertEqual([r["city"] for r in rows], ["New"])
        self.assertEqual((rows[0]["actual"], rows[0]["target_source"]), (1, "new_launch"))
        self.assertNotIn("target", rows[0])
        with self.assertRaises(GrowthOSError):
            target_cities(actuals, targets, "2026-10", queue="PRIVATE_marker")

    def test_missing_overall_target_keeps_x_unknown_and_never_substitutes_city_sum(self):
        targets = plan([("Alpha", 1000)], overall=None)
        result = target_cities(self.actuals, targets, "2026-10", mode="all")
        self.assertIsNone(result["overall"]["shortfall"])
        self.assertEqual(result["overall"]["unavailable_reason"], "missing_overall_target")
        self.assertEqual([r["city"] for r in result["rows"]], ["Alpha"])

    def test_period_bounds_and_kolkata_month_boundary(self):
        with self.assertRaises(GrowthOSError):
            target_cities(self.actuals, demo_targets("2026-09"), "2026-10")
        # UTC October 31 is already November in Kolkata.
        now = datetime(2026, 10, 31, 20, tzinfo=timezone.utc)
        validate_target_request("2026-10", "top", 0, 100, now=now)
        for args in (("2026-11", "top", 0, 25), ("2026-10", "other", 0, 25),
                     ("2026-10", "all", -1, 25), ("2026-10", "all", 0, True)):
            with self.assertRaises(GrowthOSError):
                validate_target_request(*args, now=now)

    def test_one_time_merged_review_does_not_double_actual(self):
        raw = plan([("Trivandrum", 100)]).data
        result = target_city_review(observations([("Trivandrum", 80)]), validate_targets(raw), "trivandrum")
        self.assertEqual((result["target"], result["actual"], result["shortfall"], result["shortfall_pct"]), (100, 80, 20, 20))


class TargetContractTests(unittest.TestCase):
    def test_detached_checksum_normalization_and_packaged_sql(self):
        raw = demo_targets().data
        result = validate_targets(raw)
        raw["rows"].reverse()
        self.assertEqual(result, validate_targets(raw))
        raw["rows"][0]["platform_ftu_target"] = 123
        self.assertNotEqual(result.data, raw)
        sql = target_schema_sql()
        self.assertIn("CREATE TABLE irf.targets", sql)
        self.assertIn("CREATE TABLE irf.target_imports", sql)
        self.assertNotIn("REFERENCES", sql)

    def test_dual_metric_rows_allow_independent_unavailable_targets(self):
        raw = demo_targets().data
        city = next(r for r in raw["rows"] if r["kind"] == "city")
        city["registrations_target"] = None
        result = validate_targets(raw)
        row = next(r for r in result.data["rows"] if r["kind"] == "city")
        self.assertEqual((row["platform_ftu_target"], row["registrations_target"]), (80, None))

    def test_contract_rejects_duplicates_missing_specials_and_invalid_counts(self):
        for fault in ("duplicate", "special", "negative", "boolean", "registration_negative"):
            raw = copy.deepcopy(demo_targets().data)
            city = next(r for r in raw["rows"] if r["kind"] == "city")
            if fault == "duplicate":
                raw["rows"].append(city)
            elif fault == "special":
                raw["rows"] = [r for r in raw["rows"] if r["kind"] != "overall"]
            elif fault in ("negative", "boolean"):
                city["platform_ftu_target"] = -1 if fault == "negative" else True
            else:
                city["registrations_target"] = -1
            with self.subTest(fault=fault), self.assertRaises(GrowthOSError):
                validate_targets(raw)

    def test_stored_records_are_structurally_validated_without_provenance(self):
        target = demo_targets()
        records = [{k: r[k] for k in TARGET_DB_FIELDS} for r in target.data["rows"]]
        self.assertEqual(targets_from_records(records), target)
        for fault in ("missing_special", "month", "duplicate"):
            broken = copy.deepcopy(records)
            if fault == "missing_special":
                broken = [r for r in broken if r["kind"] != "overall"]
            elif fault == "month":
                broken[0]["month"] = "2026-09"
            else:
                broken.append(copy.deepcopy(broken[0]))
            with self.subTest(fault=fault), self.assertRaises(GrowthOSError):
                targets_from_records(broken)
        # Stored targets carry no source fingerprint, so a changed value is not detectable after import.
        changed = copy.deepcopy(records)
        changed[0]["platform_ftu_target"] += 1
        targets_from_records(changed)
