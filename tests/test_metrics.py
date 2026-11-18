from dataclasses import FrozenInstanceError
from decimal import Decimal, localcontext
import json
import unittest

from growthos import analysis
from growthos.contracts import VOLUMES, validate_data
from growthos.metrics import (
    AggregateObservation, GroupRatioComparison, GroupVolumeComparison,
    RatioComparison, VolumeComparison, aggregate_observations,
    compare_groups, compare_pair, metrics_output,
    group_share_gap, share_gap,
)
from fixtures import demo_data


def counts(value, **overrides):
    return {**dict.fromkeys(VOLUMES, value), **overrides}


class MetricTests(unittest.TestCase):
    def test_scalar_comparisons_have_explicit_types_and_independent_values(self):
        volumes, ratios = compare_pair(
            counts(10, installs=100, registrations=20),
            counts(15, installs=80, registrations=40),
        )
        self.assertEqual(volumes["installs"], VolumeComparison(100, 80, -20, -0.2))
        self.assertEqual(volumes["registrations"], VolumeComparison(20, 40, 20, 1.0))
        self.assertEqual(ratios["i2r"], RatioComparison(0.2, 0.5, 30.0))
        self.assertEqual(ratios["r2n"], RatioComparison(0.5, 0.375, -12.5))

    def test_zero_and_missing_scalar_operands_remain_distinct(self):
        volumes, ratios = compare_pair(counts(0), counts(10))
        self.assertEqual(volumes["installs"], VolumeComparison(0, 10, 10, None))
        self.assertEqual(ratios["i2r"], RatioComparison(None, 1.0, None))
        volumes, ratios = compare_pair(None, counts(10))
        self.assertEqual(volumes["installs"], VolumeComparison(None, 10, None, None))
        self.assertEqual(ratios["i2r"], RatioComparison(None, 1.0, None))
        volumes, ratios = compare_pair(counts(10), None)
        self.assertEqual(volumes["installs"], VolumeComparison(10, None, None, None))
        self.assertEqual(ratios["i2r"], RatioComparison(1.0, None, None))

    def test_observations_keep_complete_values_separate_from_subtotals(self):
        rows = [{"city_key": "a", **counts(0)}, {"city_key": "b", **counts(10, installs=None)}]
        result = aggregate_observations(rows, {"a", "b", "c"})
        self.assertEqual(result["installs"], AggregateObservation(None, 0, 1, 2, "missing_city_or_operand"))
        self.assertEqual(result["registrations"], AggregateObservation(None, 10, 2, 1, "missing_city_or_operand"))
        complete = aggregate_observations(rows, {"a", "b"})
        self.assertEqual(complete["registrations"], AggregateObservation(10, 10, 2, 0, None))
        empty = aggregate_observations([], {"a"})
        self.assertEqual(empty["installs"], AggregateObservation(None, 0, 0, 1, "missing_city_or_operand"))

    def test_group_comparisons_retain_coverage_and_unavailable_reasons(self):
        before = aggregate_observations([{"city_key": "a", **counts(0)}], {"a"})
        after = aggregate_observations([{"city_key": "a", **counts(10)}], {"a"})
        volumes, ratios = compare_groups(before, after)
        self.assertEqual(volumes["installs"], GroupVolumeComparison(
            AggregateObservation(0, 0, 1, 0, None), AggregateObservation(10, 10, 1, 0, None),
            10, None, "zero_baseline", 10, None,
        ))
        self.assertEqual(ratios["i2r"], GroupRatioComparison(
            None, 1.0, None, "zero_denominator", None, None, 1.0, None,
        ))
        missing = aggregate_observations([], {"a"})
        volumes, ratios = compare_groups(before, missing)
        self.assertIsNone(volumes["installs"].latest.value)
        self.assertEqual(volumes["installs"].latest.known_subtotal, 0)
        self.assertIsNone(volumes["installs"].known_subtotal_delta)
        self.assertEqual(volumes["installs"].growth_unavailable_reason, "missing_operand")
        self.assertEqual(ratios["i2r"].latest_unavailable_reason, "missing_operand")
        self.assertIsNone(ratios["i2r"].known_subtotal_latest)

    def test_partial_groups_preserve_known_movements_without_claiming_totals(self):
        before = aggregate_observations([
            {"city_key": "a", **counts(10)}, {"city_key": "b", **counts(20)},
        ], {"a", "b"})
        after = aggregate_observations([
            {"city_key": "a", **counts(20)}, {"city_key": "b", **counts(30, installs=None)},
        ], {"a", "b"})
        volumes, ratios = compare_groups(before, after)
        installs = volumes["installs"]
        self.assertIsNone(installs.latest.value)
        self.assertEqual(installs.latest.known_subtotal, 20)
        self.assertIsNone(installs.absolute_delta)
        self.assertEqual(installs.known_subtotal_delta, -10)
        self.assertAlmostEqual(installs.known_subtotal_growth_fraction, -1 / 3)
        self.assertIsNone(ratios["i2r"].latest)
        self.assertEqual(ratios["i2r"].known_subtotal_latest, 2.5)
        self.assertEqual(ratios["i2r"].known_subtotal_delta_pp, 150.0)

    def test_result_records_are_frozen_including_nested_observations(self):
        observations = aggregate_observations([{"city_key": "a", **counts(10)}], {"a"})
        volumes, ratios = compare_pair(counts(10), counts(20))
        group_volumes, group_ratios = compare_groups(observations, observations)
        for record, field in (
            (observations["installs"], "value"),
            (volumes["installs"], "previous"), (ratios["i2r"], "previous"),
            (group_volumes["installs"], "previous"), (group_ratios["i2r"], "previous"),
            (group_volumes["installs"].previous, "value"),
        ):
            with self.subTest(record=type(record).__name__), self.assertRaises(FrozenInstanceError):
                setattr(record, field, 99)

    def test_output_preserves_scalar_and_group_json_shapes_and_integer_counts(self):
        large = 2**63 - 1
        output = metrics_output(*compare_pair(counts(large), counts(large - 1)))
        self.assertEqual(output["volumes"]["installs"], {
            "previous": large, "latest": large - 1, "absolute_delta": -1,
            "growth_fraction": -1 / large,
        })
        self.assertIs(type(output["volumes"]["installs"]["previous"]), int)
        observations = aggregate_observations([{"city_key": "a", **counts(10)}], {"a"})
        volumes, ratios = compare_groups(observations, observations)
        output = metrics_output(volumes, ratios)
        self.assertEqual(output["volumes"]["installs"]["previous"], {
            "value": 10, "known_subtotal": 10, "observed_city_count": 1,
            "unavailable_city_count": 0, "reason": None,
        })
        self.assertEqual(json.loads(json.dumps(output, allow_nan=False)), output)
        output["volumes"]["installs"]["previous"]["value"] = 99
        self.assertEqual(volumes["installs"].previous.value, 10)

    def test_share_gap_uses_decimal_fraction_levels_and_pp_movement(self):
        gap = share_gap(RatioComparison(0.1, 0.4, 30.0), RatioComparison(0.3, 0.2, -10.0))
        self.assertEqual(gap, RatioComparison(-0.2, 0.2, 40.0))
        repeating = share_gap(
            RatioComparison(1 / 3, 2 / 3, None), RatioComparison(1 / 7, 2 / 7, None),
        )
        with localcontext() as context:
            context.prec = 40
            previous = Decimal("0.3333333333333333") - Decimal("0.14285714285714285")
            latest = Decimal("0.6666666666666666") - Decimal("0.2857142857142857")
            self.assertEqual(repeating, RatioComparison(float(previous), float(latest), float(100 * (latest - previous))))
        near_equal = share_gap(
            RatioComparison(0.3, 0.30000000000000004, None), RatioComparison(0.3, 0.3, None),
        )
        self.assertEqual(near_equal, RatioComparison(0.0, 4e-17, 4e-15))

    def test_share_gap_preserves_missing_values_and_zero_denominators(self):
        for before, after in (
            (counts(10, platform_ftu=0), counts(20)),
            (counts(10), counts(20, platform_link_ftu=None)),
            (counts(10, platform_auto_ftu=None), counts(20)),
        ):
            with self.subTest(before=before, after=after):
                _, ratios = compare_pair(before, after)
                gap = share_gap(ratios["link_share"], ratios["auto_share"])
                self.assertIsNone(gap.delta_pp)
                if before["platform_ftu"] == 0 or before["platform_auto_ftu"] is None:
                    self.assertIsNone(gap.previous)
                    self.assertEqual(gap.latest, 0.0)
                else:
                    self.assertEqual(gap.previous, 0.0)
                    self.assertIsNone(gap.latest)

    def test_group_share_gap_keeps_strict_and_subtotal_evidence_separate(self):
        link = GroupRatioComparison(None, 0.4, None, "missing_operand", None, 0.1, 0.4, 30.0)
        auto = GroupRatioComparison(0.3, 0.2, -10.0, None, None, 0.3, 0.2, -10.0)
        self.assertEqual(group_share_gap(link, auto), GroupRatioComparison(
            None, 0.2, None, "missing_operand", None, -0.2, 0.2, 40.0,
        ))
        zero = GroupRatioComparison(None, 0.4, None, "zero_denominator", None, None, 0.4, None)
        gap = group_share_gap(zero, auto)
        self.assertEqual(gap.previous_unavailable_reason, "zero_denominator")
        self.assertIsNone(gap.known_subtotal_previous)
        self.assertIsNone(gap.known_subtotal_delta_pp)

    def test_share_gap_is_independent_of_ambient_precision_and_not_capped(self):
        with localcontext() as context:
            context.prec = 3
            gap = share_gap(
                RatioComparison(1e-19, 9.223372036854776e18, None),
                RatioComparison(0.3, 9.223372036854774e18, None),
            )
            self.assertEqual(context.prec, 3)
        with localcontext() as context:
            context.prec = 40
            previous = Decimal("1e-19") - Decimal("0.3")
            latest = Decimal("9.223372036854776e18") - Decimal("9.223372036854774e18")
            expected = RatioComparison(float(previous), float(latest), float(100 * (latest - previous)))
        self.assertEqual(gap, expected)
        self.assertEqual(gap.latest, 2000.0)

    def test_one_city_and_aggregate_share_gaps_agree_for_large_counts(self):
        raw = demo_data().data
        raw["facts"] = [r for r in raw["facts"] if r["city_key"] == "harbor"]
        for row in raw["facts"]:
            if row["month"] == "2026-09":
                row.update(platform_ftu=2**63 - 1, platform_link_ftu=2**62, platform_auto_ftu=3)
            elif row["month"] == "2026-10":
                row.update(platform_ftu=7, platform_link_ftu=3, platform_auto_ftu=5)
        result = analysis.share_drivers(validate_data(raw))
        overall = result["overall_service_mix"]["shares"]["link_vs_auto_share_gap"]
        for city in result["top_growth_cities"] + result["top_decline_cities"]:
            self.assertEqual(city["link_vs_auto_share_gap_delta_pp"], overall["delta_pp"])


if __name__ == "__main__":
    unittest.main()
