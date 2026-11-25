import unittest

from growthos.analysis import compare_overall, target_cities, target_facts
from growthos.contracts import VOLUMES, validate_data
from growthos.errors import GrowthOSError
from growthos.evidence import UNATTRIBUTED_SCOPE_WARNING
from growthos.metrics import observed_target_actuals
from fixtures import demo_data, demo_payload, demo_targets
from test_targets import observations, plan


def with_ir_only_nulls(dataset, names):
    raw = dataset.data
    for row in raw["facts"]:
        if row["city"] in names:
            for metric in VOLUMES[2:]:
                row[metric] = None
    return validate_data(raw)


class TargetActualTests(unittest.TestCase):
    def test_observed_sum_and_truthful_ir_only_counts(self):
        data = with_ir_only_nulls(observations([
            ("Known", 40), ("Install Only", None), ("Registration Only", None), ("Partial FTU", None),
        ]), {"Install Only", "Registration Only"})
        raw = data.data
        for row in raw["facts"]:
            if row["city"] == "Install Only":
                row["registrations"] = None
            if row["city"] == "Registration Only":
                row["installs"] = None
        actual = observed_target_actuals(validate_data(raw).data["facts"])
        self.assertEqual(actual.value, 40)
        details = actual.to_dict()
        self.assertEqual(details["observed_city_count"], 1)
        self.assertEqual(details["skipped_null_ftu_city_count"], 3)
        self.assertEqual(details["install_or_registration_only_city_count"], 2)
        self.assertEqual(details["other_null_ftu_city_count"], 1)
        self.assertIsNone(details["reason"])

    def test_partial_null_actuals_no_longer_block_coverage_or_become_zero_cities(self):
        data = with_ir_only_nulls(observations([("Known", 80), ("Install Only", None)]), {"Install Only"})
        targets = plan([("Known", 100)], overall=120, fallback=100)
        result = target_cities(data, targets, "2026-10")
        self.assertEqual(result["overall"]["actual"], 80)
        self.assertEqual(result["overall"]["shortfall"], 40)
        self.assertEqual(result["coverage"]["selection_status"], "complete")
        self.assertEqual(result["coverage"]["uncovered_amount"], 20)
        self.assertEqual([r["city"] for r in result["rows"]], ["Known"])
        self.assertEqual(result["coverage"]["excluded_city_counts"]["missing_actual"], 0)
        self.assertEqual(result["fallback"]["city_count"], 1)
        self.assertEqual(result["fallback"]["observed_city_count"], 0)
        self.assertEqual(result["fallback"]["skipped_null_city_count"], 1)
        self.assertIsNone(result["fallback"]["actual"])
        self.assertEqual(result["fallback"]["unavailable_reason"], "no_observed_platform_ftu")
        self.assertTrue(any("1 city had null FTU data and only installs or registrations recorded" in w for w in result["warnings"]))
        facts = target_facts(data, targets, "2026-10")
        missing = next(r for r in facts["rows"] if r["city"] == "Install Only")
        self.assertIsNone(missing["actual"])
        self.assertIsNone(missing["variance"])
        self.assertFalse(missing["qualifies_for_shortlist"])

    def test_reported_october_inputs_replay_477_skips_and_calculate_x(self):
        # Synthetic city allocation reproduces supplied aggregate inputs, not live city data.
        values = [("Observed 000", 4898501)] + [(f"Observed {i:03}", 0) for i in range(1, 845)]
        skipped = {f"IR Only {i:03}" for i in range(477)}
        data = with_ir_only_nulls(observations(values + [(c, None) for c in sorted(skipped)]), skipped)
        targets = plan([("Observed 000", 5106339)], overall=5106339, fallback=15000)
        result = target_cities(data, targets, "2026-10")
        self.assertEqual(result["overall"]["actual"], 4898501)
        self.assertEqual(result["overall"]["shortfall"], 207838)
        details = result["overall"]["actual_coverage"]
        self.assertEqual(details["observed_city_count"], 845)
        self.assertEqual(details["skipped_null_ftu_city_count"], 477)
        self.assertEqual(details["install_or_registration_only_city_count"], 477)
        self.assertEqual(details["other_null_ftu_city_count"], 0)
        self.assertEqual(result["coverage"]["selection_status"], "complete")
        # The sole plan-matched city is only ~4.3% below its own target, so the matched queue is empty.
        self.assertEqual(result["rows"], [])
        # The 845 observed fallback cities pool to zero FTU against the single New launch target.
        self.assertEqual(result["fallback"]["city_count"], 1321)
        self.assertEqual(result["fallback"]["observed_city_count"], 844)
        self.assertEqual(result["fallback"]["skipped_null_city_count"], 477)
        self.assertEqual((result["fallback"]["target"], result["fallback"]["actual"]), (15000, 0))
        self.assertEqual((result["fallback"]["variance"], result["fallback"]["qualifies"]), (-15000, True))
        fallback = target_cities(data, targets, "2026-10", queue="fallback")
        self.assertEqual(fallback["coverage"]["selected_city_count"], 1321)
        self.assertTrue(all(r["target_source"] == "new_launch" for r in fallback["rows"]))
        self.assertTrue(all("target" not in r for r in fallback["rows"]))
        self.assertTrue(any("477 cities had null FTU data and only installs or registrations recorded" in w for w in result["warnings"]))

    def test_all_null_is_unavailable_but_observed_zero_is_valid(self):
        data = with_ir_only_nulls(observations([("Known", None), ("Missing", None)]), {"Known", "Missing"})
        targets = plan([("Known", 100)], overall=1000)
        result = target_cities(data, targets, "2026-10")
        self.assertIsNone(result["overall"]["actual"])
        self.assertIsNone(result["overall"]["shortfall"])
        self.assertEqual(result["overall"]["unavailable_reason"], "no_observed_platform_ftu")
        self.assertEqual(result["coverage"]["selection_status"], "overall_shortfall_unavailable")
        zero = with_ir_only_nulls(observations([("Known", 0), ("Missing", None)]), {"Missing"})
        result = target_cities(zero, targets, "2026-10")
        self.assertEqual(result["overall"]["actual"], 0)
        self.assertEqual(result["overall"]["shortfall"], 1000)
        self.assertIsNone(result["overall"]["unavailable_reason"])

    def test_disclosure_does_not_claim_partial_ftu_or_empty_metrics_are_ir_only(self):
        data = observations([("Known", 40), ("Partial FTU", None), ("Empty", None)])
        raw = data.data
        for row in raw["facts"]:
            if row["city"] == "Empty":
                for metric in VOLUMES:
                    row[metric] = None
        result = target_cities(validate_data(raw), plan([("Known", 100)]), "2026-10")
        details = result["overall"]["actual_coverage"]
        self.assertEqual(details["skipped_null_ftu_city_count"], 2)
        self.assertEqual(details["install_or_registration_only_city_count"], 0)
        self.assertEqual(details["other_null_ftu_city_count"], 2)
        warning = next(w for w in result["warnings"] if "cities had null FTU data" in w)
        self.assertIn("0 had only installs or registrations recorded", warning)

    def test_city_filters_do_not_change_overall_skip_counts(self):
        data = with_ir_only_nulls(observations([("Known", 40), ("Missing", None)]), {"Missing"})
        targets = plan([("Known", 100)])
        full = target_facts(data, targets, "2026-10")
        filtered = target_facts(data, targets, "2026-10", cities=["Known"])
        self.assertEqual(full["overall"], filtered["overall"])
        self.assertEqual(filtered["overall"]["actual_coverage"]["skipped_null_ftu_city_count"], 1)
        self.assertTrue(any("1 city had null FTU" in w for w in filtered["warnings"]))

    def test_strict_mom_aggregation_and_stored_nulls_are_unchanged(self):
        raw = demo_payload()
        for row in raw["facts"]:
            if row["month"] == "2026-10" and row["city_key"] == "vale":
                for metric in VOLUMES[2:]:
                    row[metric] = None
        data = validate_data(raw)
        before = data.payload
        strict = compare_overall(data, "2026-09", "2026-10")
        self.assertIsNone(strict["rows"][0]["volumes"]["platform_ftu"]["latest"]["value"])
        targets = target_cities(data, demo_targets(), "2026-10")
        self.assertEqual(targets["overall"]["actual"], 40)
        self.assertEqual(data.payload, before)
        self.assertEqual(target_cities(demo_data(), demo_targets(), "2026-10")["overall"]["actual"], 60)

    def test_unattributed_sentinel_included_in_national_target_actuals_only(self):
        raw = observations([("Harbor", 100), ("Vale", 50), ("Unplanned", 20), ("city_not_found", 30)]).data
        for row in raw["facts"]:
            if row["city_key"] == "harbor":
                row["tier"] = "T1"
            else:
                row["tier"] = "T3"
        data = validate_data(raw)
        targets = plan([("Harbor", 120), ("Vale", 100)], overall=300, fallback=50)

        national_analysis = target_cities(data, targets, "2026-10", mode="all")
        self.assertEqual(national_analysis["overall"]["actual"], 200)  # 100 + 50 + 20 + 30 sentinel
        self.assertEqual(national_analysis["overall"]["shortfall"], 100)
        self.assertEqual(national_analysis["coverage"]["actual_city_count"], 3)  # Named cities only.
        self.assertEqual(national_analysis["fallback"]["city_count"], 1)  # Unplanned only, not sentinel.
        self.assertEqual(national_analysis["fallback"]["actual"], 20)
        self.assertNotIn("city_not_found", [r["city_key"] for r in national_analysis["rows"]])
        self.assertIn(UNATTRIBUTED_SCOPE_WARNING, national_analysis["warnings"])

        national_facts = target_facts(data, targets, "2026-10")
        self.assertEqual(national_facts["overall"]["actual"], 200)
        self.assertNotIn("city_not_found", [r["city_key"] for r in national_facts["rows"]])
        self.assertIn(UNATTRIBUTED_SCOPE_WARNING, national_facts["warnings"])

        tier_t3 = target_cities(data, targets, "2026-10", mode="all", tier="T3")
        self.assertEqual(tier_t3["overall"]["actual"], 50)  # Plan-matched T3 (Vale) only; no sentinel.
        self.assertEqual(tier_t3["coverage"]["actual_city_count"], 2)  # Vale + Unplanned; no sentinel.
        self.assertEqual(tier_t3["fallback"]["actual"], 20)
        self.assertNotIn("city_not_found", [r["city_key"] for r in tier_t3["rows"]])
        self.assertIn(UNATTRIBUTED_SCOPE_WARNING, tier_t3["warnings"])

        with self.assertRaises(GrowthOSError) as caught:
            target_facts(data, targets, "2026-10", cities=["city_not_found"])
        self.assertEqual(caught.exception.code, "CITY")

        # Sentinel stored as T3 alone does not form a T3 tier cohort.
        t1_only = validate_data({
            **raw,
            "facts": [r for r in raw["facts"] if r["city_key"] in ("harbor", "city_not_found")],
        })
        with self.assertRaises(GrowthOSError) as caught:
            target_cities(t1_only, targets, "2026-10", tier="T3")
        self.assertEqual(caught.exception.code, "COVERAGE")
