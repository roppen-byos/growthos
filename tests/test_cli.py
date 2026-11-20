import io
import json
import os
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from growthos.cli import main
from growthos.errors import GrowthOSError
from growthos.evidence import catalog
from fixtures import demo_data, demo_payload, demo_targets, fixture_reader
from growthos.contracts import validate_data
from growthos.postgres import validate_reader_settings


class CliTests(unittest.TestCase):
    def setUp(self):
        patcher = patch("growthos.postgres.DEFAULT_ENV_FILE", None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def invoke(self, arguments):
        output = io.StringIO()
        with redirect_stdout(output):
            code = main(arguments)
        return code, json.loads(output.getvalue())

    def invoke_fixture(self, arguments):
        output = io.StringIO()
        with patch("growthos.cli.connect_reader", fixture_reader), redirect_stdout(output):
            code = main(arguments)
        return code, json.loads(output.getvalue())

    def test_reader_status_compare_and_verify(self):
        for operation in ("status", "compare", "verify"):
            code, output = self.invoke_fixture([operation])
            self.assertEqual(code, 0)
            self.assertTrue(output["nonproduction"])

    def test_missing_required_driver_returns_sanitized_setup_guidance(self):
        with patch.dict(os.environ, {"GROWTHOS_READER_DSN": "synthetic-secret-dsn"}, clear=True), \
                patch.dict("sys.modules", {"psycopg": None, "psycopg.conninfo": None}):
            code, output = self.invoke(["status"])
        self.assertEqual(code, 2)
        self.assertEqual(output["error"]["code"], "DEPENDENCY")
        self.assertIn("Required reporting database driver", output["error"]["message"])
        self.assertIn("pip install -e .", output["error"]["message"])
        self.assertNotIn("optional", output["error"]["message"])
        self.assertNotIn("synthetic-secret-dsn", str(output))

    def test_cli_guards_and_error_sanitization(self):
        with patch.dict(os.environ, {}, clear=True):
            code, output = self.invoke(["compare"])
        self.assertEqual(code, 2)
        self.assertEqual(output["error"]["code"], "CONFIGURATION")

        code, output = self.invoke(["rank", "--password=supersecret"])
        self.assertEqual(code, 2)
        self.assertEqual(output["error"]["code"], "ARGUMENTS")
        self.assertIn("rank --help", output["error"]["message"])
        self.assertNotIn("supersecret", str(output))

        validate_reader_settings({"host": "approved.example.invalid", "sslmode": "verify-full"})
        with self.assertRaises(GrowthOSError):
            validate_reader_settings({"host": "approved.example.invalid", "sslmode": "require"})

    def test_tiers_are_t1_to_t3_and_t4_filters_are_rejected(self):
        self.assertEqual(self.invoke_fixture(["status"])[1]["tiers"], ["T1", "T3"])
        for args in (["compare", "--group-by", "tier"], ["rank"], ["funnel"], ["share-drivers"]):
            with self.subTest(args=args):
                self.assertEqual(self.invoke_fixture([*args, "--tier", "T3"])[0], 0)
                code, output = self.invoke_fixture([*args, "--tier", "T4"])
                self.assertEqual(code, 2)
                self.assertEqual(output["error"]["code"], "ARGUMENTS")

    def test_funnel_selected_columns_and_units(self):
        columns = ["city", "platform_ftu", "platform_ftu_mom_abs", "i2r", "i2r_delta_pp", "i2r_mom_abs", "i2r_mom_pct"]
        arguments = ["funnel", "--city", "Harbor"]
        for column in columns:
            arguments.extend(("--column", column))
        code, output = self.invoke_fixture(arguments)
        self.assertEqual(code, 0)
        self.assertEqual(output["columns"], columns)
        row = output["rows"][0]
        self.assertEqual(set(row), set(columns))
        self.assertEqual(row["platform_ftu"], 40)
        self.assertEqual(row["platform_ftu_mom_abs"], 10)
        self.assertAlmostEqual(row["i2r"], 0.4)
        self.assertAlmostEqual(row["i2r_delta_pp"], -10)
        self.assertAlmostEqual(row["i2r_mom_abs"], -0.1)
        self.assertAlmostEqual(row["i2r_mom_pct"], -0.2)
        self.assertEqual(output["column_units"]["i2r_delta_pp"], "percentage_points")
        alias_code, alias_out = self.invoke_fixture([
            "funnel", "--city", "Harbor",
            "--column", "city", "--column", "platform_ftu_latest",
            "--column", "platform_ftu_delta", "--column", "platform_ftu_growth",
            "--column", "i2r_delta",
        ])
        self.assertEqual(alias_code, 0)
        self.assertEqual(alias_out["columns"], [
            "city", "platform_ftu", "platform_ftu_mom_abs", "platform_ftu_mom_pct", "i2r_delta_pp",
        ])
        self.assertEqual(alias_out["rows"][0]["platform_ftu_mom_abs"], 10)
        self.assertAlmostEqual(alias_out["rows"][0]["i2r_delta_pp"], -10)
        self.assertEqual(self.invoke_fixture(["funnel", "--column", "phone"])[0], 2)
        self.assertIn("volumes", self.invoke_fixture(["funnel"])[1]["rows"][0])

    def test_supported_operations_are_exposed(self):
        self.assertEqual(catalog(demo_data())["supported_operations"], [
            "status", "compare", "rank", "funnel", "share-drivers", "warehouse-queries", "verify",
            "target-analysis", "target-facts", "investigate", "investigate-funnel",
        ])

    def test_verify_selected_months_and_explicit_period_comparison(self):
        code, output = self.invoke_fixture(["verify", "--start", "2026-08", "--end", "2026-09"])
        self.assertEqual(code, 0)
        self.assertEqual(output["months"], ["2026-08", "2026-09"])
        self.assertEqual(self.invoke_fixture(["compare", "--previous", "2026-08", "--latest", "2026-10"])[0], 0)

    def test_target_commands_read_prior_month_and_return_shaped_output(self):
        code, analysis = self.invoke_fixture(["target-analysis", "--month", "2026-10", "--limit", "1"])
        self.assertEqual(code, 0)
        self.assertEqual(analysis["months"], ["2026-09", "2026-10"])
        self.assertEqual(analysis["row_count"], 1)
        self.assertEqual(analysis["next_offset"], 1)
        self.assertTrue({"overall", "coverage", "queue", "rows", "persistent_failure"} <= set(analysis))
        self.assertTrue(analysis["persistent_failure"]["available"])
        self.assertEqual(analysis["persistent_failure"]["previous_month"], "2026-09")

        code, page = self.invoke_fixture(["target-analysis", "--month", "2026-10", "--mode", "all", "--offset", "1"])
        self.assertEqual(code, 0)
        self.assertEqual(page["row_count"], 1)

        code, facts = self.invoke_fixture(["target-facts", "--month", "2026-10"])
        self.assertEqual(code, 0)
        self.assertEqual((facts["operation"], facts["months"]), ("target-facts", ["2026-09", "2026-10"]))
        self.assertTrue({"overall", "coverage", "rows", "persistent_failure"} <= set(facts))

    def test_target_invalid_arguments_fail_before_reader_and_hide_inputs(self):
        with patch("growthos.cli.connect_reader") as reader:
            for command, args in (
                ("target-analysis", []),
                ("target-analysis", ["--month", "bad"]),
                ("target-analysis", ["--month", "9999-12"]),
                ("target-analysis", ["--month", "2026-10", "--limit", "101"]),
                ("target-analysis", ["--month", "2026-10", "--offset", "-1"]),
                ("target-analysis", ["--month", "2026-10", "--queue", "PRIVATE_marker"]),
                ("target-facts", []),
                ("target-facts", ["--month", "9999-12"]),
                ("target-facts", ["--month", "2026-10", "--limit", "101"]),
                ("target-facts", ["--month", "2026-10", "--attainment", "PRIVATE_marker"]),
                ("target-facts", ["--month", "2026-10", "--sort", "PRIVATE_marker"]),
            ):
                with self.subTest(command=command, args=args):
                    code, output = self.invoke([command, *args])
                    self.assertEqual(code, 2)
                    self.assertNotIn("PRIVATE", str(output))
            reader.assert_not_called()

    def test_target_help_succeeds_without_opening_reader(self):
        with patch("growthos.cli.connect_reader") as reader:
            for command in ("target-analysis", "target-facts"):
                with self.subTest(command=command):
                    output = io.StringIO()
                    with redirect_stdout(output), self.assertRaises(SystemExit) as exited:
                        main([command, "--help"])
                    self.assertEqual(exited.exception.code, 0)
                    self.assertIn("--month", output.getvalue())
                    self.assertIn("--tier", output.getvalue())
                    if command == "target-analysis":
                        self.assertIn("--queue", output.getvalue())
                    if command == "target-facts":
                        self.assertIn("5% filter", " ".join(output.getvalue().split()))
            reader.assert_not_called()

    def test_target_tier_flag_scopes_output_and_rejects_invalid_tier_before_reader(self):
        for command in ("target-analysis", "target-facts"):
            code, result = self.invoke_fixture([command, "--month", "2026-10", "--tier", "t3"])
            self.assertEqual(code, 0)
            self.assertEqual(result["scope"]["tier"], "T3")
            with patch("growthos.cli.connect_reader") as reader:
                code, result = self.invoke([command, "--month", "2026-10", "--tier", "PRIVATE_marker"])
                self.assertEqual(code, 2)
                self.assertNotIn("PRIVATE", str(result))
                reader.assert_not_called()
            code, result = self.invoke_fixture([command, "--month", "2026-10", "--tier", "T2"])
            self.assertEqual(code, 2)
            self.assertEqual(result["error"]["code"], "COVERAGE")

    def test_target_queue_flag_routes_matched_and_fallback(self):
        raw = demo_payload()
        base = raw["facts"][-1]
        raw["facts"].append({**base, "city": "New Town", "city_key": "new_town",
                             "source_cities": ["New Town"], "platform_ftu": 5,
                             "platform_link_ftu": 5, "platform_auto_ftu": 0, "platform_cab_ftu": 0,
                             "installs": 1, "registrations": 1})
        with patch("fixtures.demo_data", return_value=validate_data(raw)):
            code, matched = self.invoke_fixture(["target-analysis", "--month", "2026-10", "--mode", "all"])
            self.assertEqual(code, 0)
            self.assertEqual(matched["queue"], "matched")
            self.assertNotIn("New Town", [r["city"] for r in matched["rows"]])
            code, fallback = self.invoke_fixture(["target-analysis", "--month", "2026-10", "--queue", "fallback"])
            self.assertEqual(code, 0)
            self.assertEqual(fallback["queue"], "fallback")
            self.assertIn("New Town", [r["city"] for r in fallback["rows"]])

    def test_target_cli_returns_prior_month_persistence_rows(self):
        from contextlib import contextmanager
        from growthos.targets import validate_targets
        from fixtures import FixtureReaderStore
        raw = demo_payload()
        raw["months"] = ["2026-09", "2026-10"]
        raw["month_metadata"] = [m for m in raw["month_metadata"] if m["month"] in raw["months"]]
        base = raw["facts"][-1]
        raw["facts"] = [
            {**base, "month": m, "city": "Alpha", "city_key": "alpha",
             "source_cities": ["Alpha"], "platform_ftu": actual}
            for m, actual in (("2026-09", 80), ("2026-10", 60))
        ]
        data = validate_data(raw)

        def targets(value):
            plan = demo_targets(value).data
            plan["rows"] = [r for r in plan["rows"] if r["kind"] != "city"]
            for row in plan["rows"]:
                target_value = 1000 if row["kind"] == "overall" else 100
                row["platform_ftu_target"] = target_value
                row["registrations_target"] = 2 * target_value
            plan["rows"].append({
                "month": value, "kind": "city", "city_key": "alpha", "city": "Alpha",
                "platform_ftu_target": 100, "registrations_target": 200})
            return validate_targets(plan)

        @contextmanager
        def reader():
            yield FixtureReaderStore(data, target_months=["2026-09", "2026-10"])

        with patch("fixtures.demo_targets", side_effect=targets), \
                patch("growthos.cli.connect_reader", reader):
            for command in ("target-analysis", "target-facts"):
                with self.subTest(command=command):
                    output = io.StringIO()
                    with redirect_stdout(output):
                        code = main([command, "--month", "2026-10"])
                    self.assertEqual(code, 0)
                    persistence = json.loads(output.getvalue())["persistent_failure"]
                    self.assertEqual(persistence["previous_month"], "2026-09")
                    self.assertEqual(persistence["city_count"], 1)
                    self.assertEqual(persistence["rows"][0]["city"], "Alpha")
                    self.assertEqual((persistence["rows"][0]["status"], persistence["rows"][0]["failing_since"]),
                                     ("persistent", "2026-09"))

    def test_target_error_is_sanitized_and_missing_install_does_not_disable_existing_commands(self):
        with patch("fixtures.FixtureReaderStore.load_targets", side_effect=GrowthOSError(
                "TARGETS_UNAVAILABLE", "Targets are not installed.")):
            self.assertEqual(self.invoke_fixture(["target-analysis", "--month", "2026-10"])[1]["error"]["code"], "TARGETS_UNAVAILABLE")
            for command in ("status", "compare", "rank", "funnel", "share-drivers", "verify"):
                self.assertEqual(self.invoke_fixture([command])[0], 0)
        with patch("fixtures.FixtureReaderStore.load_targets", side_effect=RuntimeError("PRIVATE_token_marker")), \
                patch("sys.stderr", io.StringIO()):
            code, result = self.invoke_fixture(["target-analysis", "--month", "2026-10"])
        self.assertEqual(code, 2)
        self.assertNotIn("PRIVATE", str(result))


if __name__ == "__main__":
    unittest.main()
