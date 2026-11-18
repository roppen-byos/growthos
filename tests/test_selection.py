import unittest

from growthos.contracts import validate_data
from growthos.errors import GrowthOSError
from growthos.selection import prepare_comparison
from fixtures import demo_data, demo_payload


class SelectionTests(unittest.TestCase):
    def test_default_and_explicit_periods_and_labels(self):
        dataset = demo_data()
        default = prepare_comparison(dataset)
        self.assertEqual((default.previous, default.latest, default.label), ("2026-09", "2026-10", "MoM"))
        self.assertEqual(len(default.period_rows), 4)
        explicit = prepare_comparison(dataset, "2026-08", "2026-10")
        self.assertEqual(explicit.label, "period comparison")
        self.assertEqual({r["month"] for r in explicit.period_rows}, {"2026-08", "2026-10"})
        self.assertEqual([p[0]["city_key"] for p in explicit.pairs], ["harbor", "vale"])

    def test_period_errors_and_single_available_month(self):
        for previous, latest, code in (
            ("2026-09", None, "PERIOD"), ("2026-10", "2026-09", "PERIOD"),
            ("2026-07", "2026-10", "COVERAGE"), ("bad", "2026-10", "MONTH"),
        ):
            with self.subTest(previous=previous, latest=latest), self.assertRaises(GrowthOSError) as raised:
                prepare_comparison(demo_data(), previous, latest)
            self.assertEqual(raised.exception.code, code)
        raw = demo_payload()
        raw["months"] = ["2026-10"]
        raw["facts"] = [r for r in raw["facts"] if r["month"] == "2026-10"]
        raw["month_metadata"] = [m for m in raw["month_metadata"] if m["month"] == "2026-10"]
        with self.assertRaises(GrowthOSError) as raised:
            prepare_comparison(validate_data(raw))
        self.assertEqual(raised.exception.code, "COVERAGE")

    def test_filters_keep_unfiltered_evidence_and_latest_tier_cohorts(self):
        raw = demo_payload()
        raw["facts"][-1].update(tier="T2", city="Vale Updated")
        prepared = prepare_comparison(validate_data(raw), tier_filter="T2", cities=["VALE", "vale"])
        self.assertEqual(len(prepared.all_pairs), 2)
        self.assertEqual(len(prepared.period_rows), 4)
        self.assertEqual(len(prepared.pairs), 1)
        meta, before, after = prepared.pairs[0]
        self.assertEqual((meta["city"], meta["tier"]), ("Vale Updated", "T2"))
        self.assertEqual((before["tier"], after["tier"]), ("T3", "T2"))
        self.assertEqual((before["platform_ftu"], after["platform_ftu"]), (10, 20))
        self.assertTrue(prepared.reclassified)
        self.assertFalse(prepare_comparison(validate_data(raw), tier_filter="T1").reclassified)
        self.assertEqual(prepare_comparison(validate_data(raw), tier_filter="T3").pairs, [])
        with self.assertRaises(GrowthOSError) as raised:
            prepare_comparison(validate_data(raw), tier_filter="T1", cities=["vale"])
        self.assertEqual(raised.exception.code, "CITY")

    def test_absent_observations_are_retained_as_none(self):
        raw = demo_payload()
        raw["facts"] = [r for r in raw["facts"] if (r["month"], r["city_key"]) not in (
            ("2026-09", "vale"), ("2026-10", "harbor"),
        )]
        prepared = prepare_comparison(validate_data(raw))
        pairs = {meta["city_key"]: (meta, before, after) for meta, before, after in prepared.pairs}
        self.assertIsNone(pairs["vale"][1])
        self.assertIsNone(pairs["harbor"][2])
        self.assertEqual(pairs["harbor"][0]["platform_ftu"], 30)
        self.assertEqual(pairs["vale"][0]["platform_ftu"], 20)
        self.assertFalse(prepared.reclassified)

if __name__ == "__main__":
    unittest.main()
