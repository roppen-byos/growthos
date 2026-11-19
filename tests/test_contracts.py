import copy
import unittest
from datetime import datetime, timezone

from growthos.contracts import MAX_FACTS, RATIOS, VOLUMES, month_range, reporting_schema_sql, validate_data
from growthos.classification import EXPLICIT_MERGES, MERGED_SOURCE_CITIES, normalize_city_key, resolve_city
from growthos.errors import GrowthOSError
from growthos.metrics import ALL_METRICS, _load_metric_registry, definitions, metric_registry
from fixtures import demo_payload

NOW = datetime(2026, 11, 7, tzinfo=timezone.utc)


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.raw = demo_payload()

    def checked(self, raw=None, **kwargs):
        return validate_data(self.raw if raw is None else raw, now=NOW, **kwargs)

    def rejects(self, code, raw=None, **kwargs):
        with self.assertRaises(GrowthOSError) as caught:
            self.checked(raw, **kwargs)
        self.assertEqual(caught.exception.code, code)

    def test_valid_data_checksum_and_merge_lineage(self):
        dataset = self.checked()
        reversed_raw = copy.deepcopy(self.raw)
        reversed_raw["facts"].reverse()
        self.assertEqual(dataset, self.checked(reversed_raw))
        for row in self.raw["facts"]:
            if row["city_key"] == "harbor":
                row["source_cities"] = ["Harbor North", "Harbor South"]
        merged = self.checked()
        self.assertEqual(merged.data["facts"][0]["installs"], 100)
        self.assertEqual(len(merged.data["facts"][0]["source_cities"]), 2)

    def test_schema_grain_and_count_validation(self):
        dup = copy.deepcopy(self.raw)
        dup["facts"].append(copy.deepcopy(dup["facts"][0]))
        self.rejects("DUPLICATE_GRAIN", dup)

        for invalid_count in (-1, 2.5, "100", True):
            bad = copy.deepcopy(self.raw)
            bad["facts"][0]["installs"] = invalid_count
            self.rejects("COUNT", bad)

        leaked = copy.deepcopy(self.raw)
        leaked["facts"][0]["mobile_supersecret"] = "9876543210"
        self.rejects("SCHEMA", leaked)

        empty = copy.deepcopy(self.raw)
        empty["facts"] = []
        self.rejects("BOUNDS", empty)
        overflow = copy.deepcopy(self.raw)
        overflow["facts"] = [overflow["facts"][0]] * (MAX_FACTS + 1)
        self.rejects("BOUNDS", overflow)

    def test_coverage_provenance_and_month_boundaries(self):
        self.assertEqual(month_range("2025-12", "2026-02"), ["2025-12", "2026-01", "2026-02"])
        with self.assertRaises(GrowthOSError):
            month_range("2026-03", "2026-02")

        missing = copy.deepcopy(self.raw)
        missing["month_metadata"][0]["source_reads"].pop()
        self.rejects("PROVENANCE", missing)
        invented_cutoff = copy.deepcopy(self.raw)
        invented_cutoff["month_metadata"][0]["source_reads"][0]["cutoff"] = None
        self.rejects("SCHEMA", invented_cutoff)
        current = copy.deepcopy(self.raw)
        current["month_metadata"][0]["month"] = "2026-11"
        self.rejects("INCOMPLETE_MONTH", current)
        boundary = copy.deepcopy(self.raw)
        october = next(m for m in boundary["month_metadata"] if m["month"] == "2026-10")
        october["extracted_at"] = "2026-10-31T10:30:00+00:00"
        for read in october["source_reads"]:
            read["started_at"] = "2026-10-31T10:00:00+00:00"
            read["completed_at"] = "2026-10-31T10:15:00+00:00"
        october["business_timezone"] = "Pacific/Kiritimati"
        self.rejects("TIMEZONE", boundary)
        october["business_timezone"] = "Asia/Kolkata"
        self.rejects("INCOMPLETE_MONTH", boundary)
        changed = copy.deepcopy(self.raw)
        changed["month_metadata"][0]["extracted_at"] = "2026-11-03T00:01:00+00:00"
        changed["facts"][0]["tier"] = "T3"
        self.checked(changed)  # No cross-month compatibility gate.
        gap = copy.deepcopy(self.raw)
        gap["months"].remove("2026-09")
        for key in ("month_metadata", "facts"):
            gap[key] = [r for r in gap[key] if r["month"] != "2026-09"]
        self.checked(gap)

    def test_merge_maps_derive_consistent_keys_and_lineage(self):
        self.assertEqual(EXPLICIT_MERGES, {
            "attingal": "trivandrum", "faridabad": "delhi", "guntur": "vijayawada",
        })
        self.assertEqual(MERGED_SOURCE_CITIES, {
            "delhi": ("Delhi", "Faridabad"),
            "trivandrum": ("Attingal", "Trivandrum"),
            "vijayawada": ("Guntur", "Vijayawada"),
        })
        for canonical, lineage in MERGED_SOURCE_CITIES.items():
            for name in lineage:
                self.assertEqual(normalize_city_key(name), canonical)

    def test_unmapped_cities_fall_back_to_t3_and_t4_is_rejected(self):
        for key, sample in (("vale", "Vale"), ("city_not_found", None), ("unknown_city", "Different City")):
            resolved = resolve_city(key, sample)
            self.assertEqual(resolved[0], key)
            self.assertEqual(resolved[2], "T3")
            self.assertEqual(resolved[3], [resolved[1]])
        for key, tier in (("bangalore", "T1"), ("ahmedabad", "T2"), ("mysore", "T3")):
            self.assertEqual(resolve_city(key)[2], tier)
        for source, target in (("faridabad", "delhi"), ("guntur", "vijayawada"), ("attingal", "trivandrum")):
            self.assertEqual(resolve_city(source)[0], target)
        self.raw["facts"][0]["tier"] = "T4"
        self.rejects("CITY")

    def test_packaged_reporting_schema_sql_is_loadable(self):
        sql = reporting_schema_sql()
        self.assertIn("CREATE TABLE irf.month_metadata", sql)
        self.assertIn("CREATE TABLE irf.facts", sql)
        self.assertIn("tier IN ('T1','T2','T3')", sql)

    def test_metric_registry_contract_shape_and_tamper_rejection(self):
        import json

        store = metric_registry()
        self.assertEqual(set(store), {"metrics", "movements", "targets", "warehouse_queries", "glossary_distinctions", "rules"})
        self.assertNotIn("version", store)
        self.assertNotIn("schema_version", store)
        self.assertEqual(tuple(store["metrics"]), ALL_METRICS)
        for m in VOLUMES:
            entry = store["metrics"][m]
            self.assertEqual(entry["unit"], "count")
            self.assertEqual(entry["subject_unit"], "installations" if m == "installs" else "customers")
        for m, (num_col, den_col) in RATIOS.items():
            entry = store["metrics"][m]
            self.assertEqual(entry["unit"], "fraction")
            self.assertEqual(entry["display_unit"], "percent")
            self.assertEqual(entry["movement_unit"], "percentage_points")
            self.assertEqual((entry["numerator"], entry["denominator"]), (num_col, den_col))
        self.assertEqual(store["metrics"]["platform_link_ftu"]["service_membership"], ["Link"])

        targets = store["targets"]
        self.assertEqual(set(targets), {
            "platform_ftu_target", "target_shortfall", "shortfall_percentage", "target_shortlist",
            "target_persistence"})
        self.assertEqual(set(targets["platform_ftu_target"]["components"]), {
            "grand_total", "city_target", "new_launch_fallback"})
        self.assertEqual(targets["target_shortfall"]["formula"], "target - actual")
        self.assertIn("5%", targets["target_shortlist"]["eligibility"])
        self.assertIn("5 percentage points", targets["target_persistence"]["comparison"])
        self.assertIn("new_miss", targets["target_persistence"]["statuses"])

        defs = definitions()
        for key in (*ALL_METRICS, "absolute_delta", "growth_fraction", "delta_pp", "link_vs_auto_share_gap", "glossary_distinctions", "rules"):
            self.assertIn(key, defs)
        for metric_id, entry in store["metrics"].items():
            self.assertNotIn("decision_refs", entry)
            self.assertNotIn("decision_refs", defs[metric_id])
        self.assertFalse(defs["glossary_distinctions"]["svn"]["supported_in_v1"])
        self.assertFalse(defs["glossary_distinctions"]["rr2net"]["supported_in_v1"])

        bad_versioned = copy.deepcopy(store)
        bad_versioned["version"] = "1.0.0"
        with self.assertRaises(GrowthOSError) as caught:
            _load_metric_registry(json.dumps(bad_versioned))
        self.assertEqual(caught.exception.code, "SCHEMA")

        bad_alias = copy.deepcopy(store)
        bad_alias["metrics"]["platform_auto_ftu"]["aliases"].append("SVN")
        with self.assertRaises(GrowthOSError):
            _load_metric_registry(json.dumps(bad_alias))

        bad_ratio = copy.deepcopy(store)
        bad_ratio["metrics"]["r2n"]["denominator"] = "installs"
        with self.assertRaises(GrowthOSError):
            _load_metric_registry(json.dumps(bad_ratio))

        # Detached snapshots: mutating a read never changes later reads.
        store["metrics"]["installs"]["label"] = "Mutated"
        definitions()["targets"]["platform_ftu_target"]["label"] = "Mutated"
        self.assertEqual(metric_registry()["metrics"]["installs"]["label"], "Installs")
        self.assertEqual(metric_registry()["targets"]["platform_ftu_target"]["label"], "Platform FTU Target")


if __name__ == "__main__":
    unittest.main()
