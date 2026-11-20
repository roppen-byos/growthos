import copy
from decimal import Decimal
import unittest

from growthos.analysis import catalog, city_funnels, compare_overall, rank_cities, share_drivers
from growthos.contracts import PUBLIC_VOLUMES, RATIOS, VOLUMES, validate_data
from growthos.errors import GrowthOSError
from growthos.evidence import MISSING_TOTALS_WARNING, TIER_RECLASSIFIED_WARNING
from fixtures import demo_payload, demo_data


class AnalysisTests(unittest.TestCase):
    def result(self, raw=None, **kwargs):
        dataset = demo_data() if raw is None else validate_data(raw)
        return compare_overall(dataset, **kwargs)

    def test_overall_comparison_volumes_ratios_and_unmapped_t3(self):
        result = self.result()
        self.assertEqual(result["effective_periods"], {"previous": "2026-09", "latest": "2026-10"})
        self.assertEqual(result["comparison_label"], "MoM")
        volumes = result["rows"][0]["volumes"]
        expected = {
            "installs": (400, 300, -100, -0.25),
            "registrations": (80, 100, 20, 0.25),
            "platform_ftu": (40, 60, 20, 0.5),
            "platform_link_ftu": (20, 28, 8, 0.4),
            "platform_auto_ftu": (16, 28, 12, 0.75),
            "platform_cab_ftu": (9, 13, 4, 4 / 9),
        }
        for metric, values in expected.items():
            actual = volumes[metric]
            self.assertEqual(actual["previous"]["value"], values[0])
            self.assertEqual(actual["latest"]["value"], values[1])
            self.assertEqual(actual["absolute_delta"], values[2])
            self.assertAlmostEqual(actual["growth_fraction"], values[3])

        ratios = result["rows"][0]["ratios"]
        self.assertEqual(ratios["i2r"]["previous"], 0.2)
        self.assertAlmostEqual(ratios["i2r"]["latest"], 1 / 3)
        self.assertAlmostEqual(ratios["i2r"]["delta_pp"], 13 + 1 / 3)
        self.assertEqual(ratios["r2n"]["delta_pp"], 10)
        self.assertAlmostEqual(sum(ratios[m]["latest"] for m in ("link_share", "auto_share", "cab_share")), 1.15)
        self.assertEqual(result["mapping_impact"]["unmapped_city_count"], 1)
        self.assertEqual(result["mapping_impact"]["fallback_tier"], "T3")
        self.assertEqual(result["mapping_impact"]["known_volumes_by_month"]["2026-10"]["platform_ftu"], 20)

    def test_t3_combines_mapped_and_unmapped_cities_without_changing_overall(self):
        raw = demo_payload()
        for row in list(raw["facts"]):
            if row["city_key"] == "vale":
                raw["facts"].append({**row, "city_key": "mysore", "city": "Mysore", "source_cities": ["Mysore"]})
        dataset = validate_data(raw)
        result = compare_overall(dataset, group_by="tier")
        tiers = {r["tier"]: r for r in result["rows"]}
        self.assertEqual(set(tiers), {"T1", "T3"})
        self.assertEqual(tiers["T3"]["volumes"]["platform_ftu"]["latest"]["value"], 40)
        self.assertEqual(result["mapping_impact"]["unmapped_city_count"], 1)
        self.assertEqual(result["mapping_impact"]["known_volumes_by_month"]["2026-10"]["platform_ftu"], 20)
        overall = compare_overall(dataset)["rows"][0]
        for metric in VOLUMES:
            for side in ("previous", "latest"):
                self.assertEqual(sum(r["volumes"][metric][side]["value"] for r in tiers.values()),
                                 overall["volumes"][metric][side]["value"])
        for operation in (rank_cities, city_funnels):
            self.assertEqual({r["city_key"] for r in operation(dataset, tier="T3")["rows"]}, {"vale", "mysore"})
        self.assertEqual({r["tier"] for r in share_drivers(dataset, tier="T3")["tier_service_mix"]}, {"T3"})

    def test_period_selection_and_missing_or_zero_operands(self):
        period = self.result(previous="2026-08", latest="2026-10")
        self.assertEqual(period["comparison_label"], "period comparison")
        self.assertEqual(period["rows"][0]["volumes"]["installs"]["absolute_delta"], 200)

        for prev, curr in (("2026-07", "2026-10"), ("2026-10", "2026-09"), ("2026-08", None)):
            with self.assertRaises(GrowthOSError):
                self.result(previous=prev, latest=curr)

        raw = demo_payload()
        for row in raw["facts"]:
            if row["month"] == "2026-09":
                row["registrations"] = 0
        raw["facts"][4]["installs"] = None
        row_out = self.result(raw)["rows"][0]
        self.assertEqual(row_out["volumes"]["registrations"]["absolute_delta"], 100)
        self.assertIsNone(row_out["volumes"]["registrations"]["growth_fraction"])
        self.assertEqual(row_out["volumes"]["registrations"]["growth_unavailable_reason"], "zero_baseline")
        self.assertIsNone(row_out["volumes"]["installs"]["latest"]["value"])
        self.assertEqual(row_out["volumes"]["installs"]["latest"]["known_subtotal"], 100)

    def test_catalog_paging_and_bounds(self):
        raw = demo_payload()
        for i in range(26):
            row = copy.deepcopy(raw["facts"][0])
            row.update(city_key=f"test-city-{i}", city=f"Test City {i}", source_cities=[f"Test City {i}"])
            raw["facts"].append(row)
        dataset = validate_data(raw)
        first_page = catalog(dataset)
        self.assertEqual(first_page["city_count"], 28)
        self.assertEqual(len(first_page["cities"]), 25)
        self.assertEqual(first_page["next_city_offset"], 25)
        next_page = catalog(dataset, city_offset=25)
        self.assertEqual(len(next_page["cities"]), 3)
        self.assertIsNone(next_page["next_city_offset"])
        with self.assertRaises(GrowthOSError):
            catalog(dataset, city_offset=-1)

    def test_city_metrics_agree_across_commands_with_missing_observations(self):
        for scenario in ("complete", "missing_city", "null_operand", "zero_denominator"):
            with self.subTest(scenario=scenario):
                raw = demo_payload()
                if scenario == "missing_city":
                    raw["facts"] = [r for r in raw["facts"] if not (r["month"] == "2026-09" and r["city_key"] == "vale")]
                if scenario == "null_operand":
                    raw["facts"][-1]["registrations"] = None
                if scenario == "zero_denominator":
                    raw["facts"][-1]["installs"] = 0
                dataset = validate_data(raw)
                comparisons = {r["city_key"]: r for r in compare_overall(dataset, group_by="city")["rows"]}
                funnels = {r["city_key"]: r for r in city_funnels(dataset)["rows"]}
                for key, row in funnels.items():
                    compared = comparisons[key]
                    for metric in VOLUMES:
                        for side in ("previous", "latest"):
                            self.assertEqual(row["volumes"][metric][side], compared["volumes"][metric][side]["value"])
                        for field in ("absolute_delta", "growth_fraction"):
                            self.assertEqual(row["volumes"][metric][field], compared["volumes"][metric][field])
                    for metric in RATIOS:
                        for field in ("previous", "latest", "delta_pp"):
                            self.assertEqual(row["ratios"][metric][field], compared["ratios"][metric][field])
                for metric in (*VOLUMES, *RATIOS):
                    for ranked in rank_cities(dataset, metric=metric)["rows"]:
                        group = "volumes" if metric in VOLUMES else "ratios"
                        movement_fields = ("absolute_delta", "growth_fraction") if metric in VOLUMES else ("delta_pp",)
                        for field in ("previous", "latest", *movement_fields):
                            self.assertEqual(ranked[field], funnels[ranked["city_key"]][group][metric][field])

    def test_ranking_directions_and_service_driver_values(self):
        dataset = demo_data()
        for ascending, order in ((False, ["harbor", "vale"]), (True, ["vale", "harbor"])):
            for basis in ("delta", "growth"):
                ranked = rank_cities(dataset, metric="installs", ascending=ascending, basis=basis)
                self.assertEqual([r["city_key"] for r in ranked["rows"]], order)
        ratios = rank_cities(dataset, metric="i2r")["rows"]
        self.assertEqual([r["city_key"] for r in ratios], ["vale", "harbor"])
        self.assertEqual((ratios[0]["numerator_delta"], ratios[0]["denominator_delta"]), (-10, -200))

        result = share_drivers(dataset, service="link")
        mix = result["overall_service_mix"]
        link_vol = mix["volumes"]["platform_link_ftu"]
        self.assertEqual(link_vol["previous"]["value"], 20)
        self.assertEqual(link_vol["latest"]["value"], 28)
        self.assertEqual(link_vol["absolute_delta"], 8)
        self.assertEqual(link_vol["growth_fraction"], 0.4)
        self.assertEqual(link_vol["known_subtotal_delta"], 8)
        self.assertAlmostEqual(mix["shares"]["link_share"]["latest"], 28 / 60)
        self.assertAlmostEqual(mix["shares"]["link_share"]["delta_pp"], -10 / 3)
        self.assertAlmostEqual(mix["shares"]["link_vs_auto_share_gap"]["delta_pp"], -10)
        self.assertEqual([r["city_key"] for r in result["top_growth_cities"]], ["vale", "harbor"])
        self.assertEqual([r["city_key"] for r in result["top_decline_cities"]], ["harbor", "vale"])
        vale = result["top_growth_cities"][0]
        self.assertEqual(vale["target_ftu_delta"], 7)
        self.assertEqual(vale["target_delta_to_overall_delta_ratio"], 7 / 8)
        self.assertAlmostEqual(vale["latest_platform_ftu_mix"], 1 / 3)
        self.assertEqual([r["tier"] for r in result["tier_service_mix"]], ["T1", "T3"])

    def test_city_selection_order_ties_filters_and_metadata(self):
        raw = demo_payload()
        raw["facts"][3]["platform_ftu"] = 50
        raw["facts"][-1].update(platform_ftu=40, city="Vale Updated", tier="T2")
        dataset = validate_data(raw)
        # Compare breaks a latest-FTU tie by previous FTU; funnel breaks it by key.
        self.assertEqual([r["city_key"] for r in compare_overall(dataset, group_by="city", top=1)["rows"]], ["vale"])
        self.assertEqual([r["city_key"] for r in city_funnels(dataset, sort_by="platform_ftu")["rows"]], ["harbor", "vale"])
        selected = city_funnels(dataset, cities=["VALE", "harbor", "Vale"], top=1)
        self.assertEqual(selected["row_count"], 2)
        self.assertEqual(next(r for r in selected["rows"] if r["city_key"] == "vale")["city"], "Vale Updated")
        # Latest-tier cohort retains Vale's 2026-09 observation when filtered by T2 and warns.
        funnel_t2 = city_funnels(dataset, tier=" t2 ")
        self.assertIn(TIER_RECLASSIFIED_WARNING, funnel_t2["warnings"])
        filtered = funnel_t2["rows"][0]
        self.assertEqual(filtered["tier"], "T2")
        self.assertEqual(filtered["volumes"]["platform_ftu"]["previous"], 50)
        self.assertEqual(filtered["volumes"]["platform_ftu"]["latest"], 40)
        self.assertEqual(filtered["volumes"]["platform_ftu"]["absolute_delta"], -10)
        tier_cmp = compare_overall(dataset, group_by="tier")
        self.assertIn(TIER_RECLASSIFIED_WARNING, tier_cmp["warnings"])
        self.assertNotIn(TIER_RECLASSIFIED_WARNING, compare_overall(dataset)["warnings"])
        overall_row = compare_overall(dataset)["rows"][0]
        for metric in VOLUMES:
            for side in ("previous", "latest"):
                self.assertEqual(
                    sum(r["volumes"][metric][side]["value"] for r in tier_cmp["rows"]),
                    overall_row["volumes"][metric][side]["value"],
                )
        ranked_t2 = rank_cities(dataset, metric="platform_ftu", tier="T2")
        self.assertEqual([r["city_key"] for r in ranked_t2["rows"]], ["vale"])
        self.assertEqual(ranked_t2["rows"][0]["absolute_delta"], -10)
        self.assertIn(TIER_RECLASSIFIED_WARNING, ranked_t2["warnings"])
        drivers_t2 = share_drivers(dataset, tier="T2")
        self.assertEqual([r["tier"] for r in drivers_t2["tier_service_mix"]], ["T2"])
        self.assertIn(TIER_RECLASSIFIED_WARNING, drivers_t2["warnings"])
        ranked = rank_cities(dataset, metric="platform_ftu")
        self.assertEqual([r["city_key"] for r in ranked["rows"]], ["harbor", "vale"])
        self.assertEqual(share_drivers(dataset, service="bike")["filters"]["service"], "link")
        with self.assertRaises(GrowthOSError):
            city_funnels(dataset, cities=["unknown"])

    def test_service_mix_summary_enforces_completeness_and_subtotals(self):
        # 1. All-missing October Auto FTU cells stay unavailable, never zero.
        raw_all_none = demo_payload()
        for row in raw_all_none["facts"]:
            if row["month"] == "2026-10":
                row["platform_auto_ftu"] = None
        all_none_ds = validate_data(raw_all_none)
        strict = compare_overall(all_none_ds)["rows"][0]
        mix = share_drivers(all_none_ds, service="auto")
        auto_vol = mix["overall_service_mix"]["volumes"]["platform_auto_ftu"]
        auto_share = mix["overall_service_mix"]["shares"]["auto_share"]
        gap = mix["overall_service_mix"]["shares"]["link_vs_auto_share_gap"]
        self.assertEqual(auto_vol, strict["volumes"]["platform_auto_ftu"])
        self.assertEqual(auto_share, strict["ratios"]["auto_share"])
        self.assertIsNone(auto_vol["latest"]["value"])
        self.assertIsNone(auto_vol["absolute_delta"])
        self.assertIsNone(auto_vol["known_subtotal_delta"])
        self.assertIsNone(auto_share["latest"])
        self.assertIsNone(auto_share["known_subtotal_latest"])
        self.assertIsNone(gap["latest"])
        self.assertIsNone(gap["delta_pp"])
        self.assertEqual(gap["latest_unavailable_reason"], "missing_operand")
        self.assertEqual(mix["rankable_city_count"], 0)
        self.assertIn(MISSING_TOTALS_WARNING, mix["warnings"])

        # 2. Partial coverage: Vale missing Auto FTU in October; Harbor complete.
        raw_partial = demo_payload()
        raw_partial["facts"][-1]["platform_auto_ftu"] = None
        partial_ds = validate_data(raw_partial)
        partial_mix = share_drivers(partial_ds, service="auto")
        p_auto = partial_mix["overall_service_mix"]["volumes"]["platform_auto_ftu"]
        self.assertIsNone(p_auto["latest"]["value"])
        self.assertEqual(p_auto["latest"]["known_subtotal"], 20)
        self.assertEqual(p_auto["latest"]["observed_city_count"], 1)
        self.assertEqual(p_auto["latest"]["unavailable_city_count"], 1)
        self.assertIsNone(p_auto["absolute_delta"])
        self.assertEqual(p_auto["known_subtotal_delta"], 4)
        self.assertIsNone(partial_mix["overall_service_mix"]["shares"]["auto_share"]["latest"])
        self.assertAlmostEqual(partial_mix["overall_service_mix"]["shares"]["auto_share"]["known_subtotal_latest"], 20 / 60)
        tiers = {r["tier"]: r for r in partial_mix["tier_service_mix"]}
        self.assertEqual(tiers["T1"]["volumes"]["platform_auto_ftu"]["absolute_delta"], 8)
        self.assertIsNone(tiers["T1"]["target_delta_to_overall_delta_ratio"])
        self.assertAlmostEqual(tiers["T1"]["latest_platform_ftu_mix"], 40 / 60)
        self.assertIsNone(tiers["T3"]["volumes"]["platform_auto_ftu"]["absolute_delta"])
        self.assertEqual(len(partial_mix["top_growth_cities"]), 1)
        harbor = partial_mix["top_growth_cities"][0]
        self.assertEqual(harbor["city_key"], "harbor")
        self.assertEqual(harbor["target_ftu_delta"], 8)
        self.assertIsNone(harbor["target_delta_to_overall_delta_ratio"])
        self.assertAlmostEqual(harbor["latest_platform_ftu_mix"], 40 / 60)
        self.assertIn(MISSING_TOTALS_WARNING, partial_mix["warnings"])

    def test_funnel_all_columns_units_and_sort_aliases(self):
        dataset = demo_data()
        columns, expected = ["city", "city_key", "tier"], {"city": "Harbor", "city_key": "harbor", "tier": "T1"}
        units = {c: "text" for c in columns}
        full = city_funnels(dataset, cities=["Harbor"])["rows"][0]
        for group, metrics in (("volumes", PUBLIC_VOLUMES), ("ratios", RATIOS)):
            for metric in metrics:
                values = full[group][metric]
                fields = {metric: values["latest"], f"{metric}_previous": values["previous"]}
                if group == "volumes":
                    fields.update({f"{metric}_mom_abs": values["absolute_delta"], f"{metric}_mom_pct": values["growth_fraction"]})
                else:
                    delta = Decimal(str(values["delta_pp"])) / 100
                    fields.update({f"{metric}_delta_pp": values["delta_pp"], f"{metric}_mom_abs": float(delta),
                                   f"{metric}_mom_pct": float(delta / Decimal(values["previous"]))})
                columns.extend(fields)
                expected.update(fields)
                for column in fields:
                    units[column] = "percentage_points" if column.endswith("_delta_pp") else (
                        "fraction" if group == "ratios" or column.endswith("_mom_pct") else "count")
                aliases = [(metric, f"{metric}_latest"), (f"{metric}_delta", f"{metric}_mom_abs")]
                if group == "volumes":
                    aliases.append((f"{metric}_growth", f"{metric}_mom_pct"))
                else:
                    aliases.append((f"{metric}_delta", f"{metric}_delta_pp"))
                for canonical, alias in aliases:
                    for ascending in (False, True):
                        with self.subTest(metric=metric, alias=alias, ascending=ascending):
                            left = city_funnels(dataset, sort_by=canonical, ascending=ascending)["rows"]
                            right = city_funnels(dataset, sort_by=f" {alias.upper()} ", ascending=ascending)["rows"]
                            self.assertEqual(left, right)
        projected = city_funnels(dataset, cities=["Harbor"], columns=columns)
        self.assertEqual(projected["columns"], columns)
        self.assertEqual(projected["rows"], [expected])
        self.assertEqual(projected["column_units"], units)
        self.assertEqual(city_funnels(dataset, columns=["city", "tier", "city"])["columns"], ["city", "tier"])
        self.assertEqual(
            city_funnels(dataset, columns=[" platform_ftu_delta ", "platform_ftu_mom_abs", "i2r_delta"])["columns"],
            ["platform_ftu_mom_abs", "i2r_delta_pp"],
        )
        for sort in ("unknown", "city", "i2r_mom_pct"):
            with self.subTest(sort=sort), self.assertRaises(GrowthOSError):
                city_funnels(dataset, sort_by=sort)
        for tier in (None, "T2"):
            with self.subTest(tier=tier), self.assertRaises(GrowthOSError):
                city_funnels(dataset, tier=tier, sort_by="unknown")
        self.assertEqual(city_funnels(dataset, tier="T2")["row_count"], 0)

    def test_funnel_null_sort_values_stay_last_and_project_as_unavailable(self):
        raw = demo_payload()
        raw["facts"][4]["installs"] = 0
        raw["facts"] = [r for r in raw["facts"] if not (r["month"] == "2026-09" and r["city_key"] == "vale")]
        dataset = validate_data(raw)
        columns = ["city_key", "i2r", "i2r_previous", "i2r_delta_pp", "i2r_mom_abs", "i2r_mom_pct", "platform_ftu_mom_abs"]
        for ascending in (False, True):
            projected = city_funnels(dataset, sort_by="platform_ftu_delta", ascending=ascending, columns=columns)
            harbor, vale = projected["rows"]
            self.assertEqual((harbor["city_key"], vale["city_key"]), ("harbor", "vale"))
            self.assertEqual(harbor["platform_ftu_mom_abs"], 10)
            self.assertEqual(harbor["i2r_previous"], 0.5)
            self.assertIsNone(harbor["i2r"])
            self.assertEqual(vale["i2r"], 0.2)
            self.assertIsNone(vale["i2r_previous"])
            self.assertIsNone(vale["platform_ftu_mom_abs"])
            for row in (harbor, vale):
                for field in ("i2r_delta_pp", "i2r_mom_abs", "i2r_mom_pct"):
                    self.assertIsNone(row[field])

    def test_public_funnel_metrics_and_ratios_work_across_compare_rank_and_funnel(self):
        dataset = demo_data()
        overall = compare_overall(dataset)["rows"][0]
        # Sep: FE=700, RR=350, Net=275; Oct: FE=1100, RR=600, Net=456
        self.assertEqual(overall["volumes"]["fe_sessions"]["previous"]["value"], 700)
        self.assertEqual(overall["volumes"]["fe_sessions"]["latest"]["value"], 1100)
        self.assertEqual(overall["volumes"]["fe_sessions"]["absolute_delta"], 400)
        self.assertEqual(overall["volumes"]["rr"]["latest"]["value"], 600)
        self.assertEqual(overall["volumes"]["net"]["latest"]["value"], 456)
        self.assertAlmostEqual(overall["ratios"]["fe2rr"]["previous"], 350 / 700)
        self.assertAlmostEqual(overall["ratios"]["fe2rr"]["latest"], 600 / 1100)
        self.assertAlmostEqual(overall["ratios"]["rr2net"]["previous"], 275 / 350)
        self.assertAlmostEqual(overall["ratios"]["rr2net"]["latest"], 456 / 600)
        self.assertAlmostEqual(overall["ratios"]["bike_rr2net"]["latest"], 198 / 260)

        # Harbor: FE2RR 0.50 -> 0.60 (+10pp), RR2Net 0.80 -> 0.75 (-5pp)
        # Vale: FE2RR 0.50 -> 0.40 (-10pp), RR2Net 0.75 -> 0.80 (+5pp)
        ranked_fe2rr = rank_cities(dataset, metric="fe2rr")["rows"]
        self.assertEqual([r["city_key"] for r in ranked_fe2rr], ["harbor", "vale"])
        self.assertAlmostEqual(ranked_fe2rr[0]["delta_pp"], 10.0)
        self.assertAlmostEqual(ranked_fe2rr[1]["delta_pp"], -10.0)

        ranked_rr2net = rank_cities(dataset, metric="rr2net", ascending=True)["rows"]
        self.assertEqual([r["city_key"] for r in ranked_rr2net], ["harbor", "vale"])
        self.assertAlmostEqual(ranked_rr2net[0]["delta_pp"], -5.0)
        self.assertAlmostEqual(ranked_rr2net[1]["delta_pp"], 5.0)

        projected = city_funnels(
            dataset,
            cities=["Harbor"],
            columns=["city", "fe_sessions", "rr", "net", "fe2rr", "fe2rr_delta_pp", "rr2net", "rr2net_delta_pp"],
        )["rows"][0]
        self.assertEqual((projected["fe_sessions"], projected["rr"], projected["net"]), (800, 480, 360))
        self.assertAlmostEqual(projected["fe2rr"], 0.6)
        self.assertAlmostEqual(projected["fe2rr_delta_pp"], 10.0)
        self.assertAlmostEqual(projected["rr2net"], 0.75)
        self.assertAlmostEqual(projected["rr2net_delta_pp"], -5.0)


if __name__ == "__main__":
    unittest.main()
