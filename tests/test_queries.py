import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from growthos.cli import main
from growthos.contracts import RATIOS, VOLUMES
from growthos.errors import GrowthOSError
from growthos.metrics import _load_metric_registry, definitions, metric_registry
from growthos.queries import (
    RESOURCE, load_queries, query_sql, queries_for_metric, warehouse_output, warehouse_queries,
)


class WarehouseQueryTests(unittest.TestCase):
    def test_packaged_queries_parse_with_metrics_and_checksums(self):
        queries = warehouse_queries()
        self.assertEqual(tuple(queries), ("installs-monthly", "registrations-monthly", "ftu-monthly"))
        self.assertEqual(queries["installs-monthly"]["metrics"], ("installs",))
        self.assertEqual(queries["registrations-monthly"]["metrics"], ("registrations",))
        self.assertEqual(queries["ftu-monthly"]["metrics"], (
            "platform_ftu", "platform_link_ftu", "platform_auto_ftu", "platform_cab_ftu",
        ))
        self.assertIn("appsflyer.canonical_appsflyer_customer_install", query_sql("installs-monthly"))
        self.assertIn("entity.users_snapshot", query_sql("registrations-monthly"))
        self.assertIn("customer.customer_order_channel_attribution_master", query_sql("ftu-monthly"))
        for entry in queries.values():
            self.assertEqual(len(entry["checksum"]), 64)
            self.assertNotIn("-- name:", entry["sql"])
            self.assertNotIn("-- metrics:", entry["sql"])

    def test_queries_cover_each_volume_metric_once_and_no_ratio(self):
        covered = [m for entry in warehouse_queries().values() for m in entry["metrics"]]
        self.assertEqual(len(covered), len(VOLUMES))
        self.assertEqual(set(covered), set(VOLUMES))
        self.assertFalse(set(covered) & set(RATIOS))
        self.assertEqual(set(queries_for_metric("installs")), {"installs-monthly"})
        self.assertEqual(len(queries_for_metric("platform_cab_ftu")), 1)

    def test_registry_references_packaged_warehouse_queries(self):
        store = metric_registry()
        self.assertEqual(store["warehouse_queries"]["resource"], RESOURCE)
        self.assertEqual(tuple(store["warehouse_queries"]["queries"]), tuple(warehouse_queries()))
        self.assertEqual(definitions()["warehouse_queries"], store["warehouse_queries"])

    def test_parser_rejects_duplicate_and_incomplete_blocks(self):
        duplicate = (
            "-- name: a\n-- metrics: installs\nSELECT 1\n"
            "-- name: a\n-- metrics: registrations\nSELECT 2\n"
        )
        missing_metrics = "-- name: a\nSELECT 1\n"
        empty_body = "-- name: a\n-- metrics: installs\n"
        for raw in (duplicate, missing_metrics, empty_body):
            with self.subTest(raw=raw), self.assertRaises(GrowthOSError) as caught:
                load_queries(raw)
            self.assertEqual(caught.exception.code, "SCHEMA")

    def test_registry_tampering_with_warehouse_references_is_rejected(self):
        store = metric_registry()
        for mutate in (
            lambda value: value["warehouse_queries"].update({"resource": "sql/other.sql"}),
            lambda value: value["warehouse_queries"]["queries"]["installs-monthly"].update(
                {"metrics": ["registrations"]}),
            lambda value: value["warehouse_queries"]["queries"].update(
                {"extra": {"metrics": ["installs"]}}),
            lambda value: value["warehouse_queries"]["queries"].pop("ftu-monthly"),
        ):
            with self.subTest(mutate=mutate):
                bad = json.loads(json.dumps(store))
                mutate(bad)
                with self.assertRaises(GrowthOSError) as caught:
                    _load_metric_registry(json.dumps(bad))
                self.assertEqual(caught.exception.code, "SCHEMA")

    def test_queries_cli_is_offline_and_filterable(self):
        with patch("growthos.cli.connect_reader") as reader:
            code, output = self.invoke([])
            self.assertEqual(code, 0)
            self.assertEqual(output["query_count"], 3)
            self.assertEqual(output["resource"], RESOURCE)
            self.assertEqual([q["id"] for q in output["queries"]], [
                "installs-monthly", "registrations-monthly", "ftu-monthly"])

            code, output = self.invoke(["--metric", "installs"])
            self.assertEqual(code, 0)
            self.assertEqual([q["id"] for q in output["queries"]], ["installs-monthly"])

            code, output = self.invoke(["--id", "ftu-monthly"])
            self.assertEqual(code, 0)
            self.assertEqual(output["query_count"], 1)
            self.assertEqual(output["queries"][0]["metrics"], [
                "platform_ftu", "platform_link_ftu", "platform_auto_ftu", "platform_cab_ftu"])

            code, output = self.invoke(["--metric", "i2r"])
            self.assertEqual(code, 2)
            self.assertEqual(output["error"]["code"], "COVERAGE")

            code, output = self.invoke(["--id", "PRIVATE_marker"])
            self.assertEqual(code, 2)
            self.assertEqual(output["error"]["code"], "ARGUMENTS")
            self.assertNotIn("PRIVATE", str(output))
            reader.assert_not_called()

    def test_warehouse_output_copies_are_detached(self):
        output = warehouse_output()
        output["queries"][0]["sql"] = "Mutated"
        self.assertNotEqual(query_sql("installs-monthly"), "Mutated")
        metric_registry()["warehouse_queries"]["queries"].clear()
        self.assertEqual(len(metric_registry()["warehouse_queries"]["queries"]), 3)

    def invoke(self, arguments):
        output = io.StringIO()
        with redirect_stdout(output):
            code = main(["warehouse-queries", *arguments])
        return code, json.loads(output.getvalue())


if __name__ == "__main__":
    unittest.main()
