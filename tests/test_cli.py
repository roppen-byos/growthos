import io
import json
import os
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from growthos.cli import main, parser
from growthos.errors import GrowthOSError
from growthos.evidence import catalog
from fixtures import demo_data, fixture_reader
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

    def test_report_saving_is_removed(self):
        self.assertNotIn("save-report", parser().format_help())
        status = self.invoke_fixture(["status"])[1]
        self.assertNotIn("save-report", json.dumps(status))
        self.assertEqual(catalog(demo_data())["supported_operations"], [
            "status", "compare", "rank", "funnel", "share-drivers", "verify",
        ])
        with patch("growthos.cli.connect_reader") as reader, patch("growthos.cli.sys.stdin") as stdin:
            for arguments in (["save-report"], [
                "save-report", "--title", "Synthetic report", "--body-file", "body.md",
                "--reports-dir", "reports", "--start", "2026-08", "--end", "2026-09",
            ]):
                with self.subTest(arguments=arguments):
                    code, result = self.invoke(arguments)
                    self.assertEqual(code, 2)
                    self.assertEqual(result["error"]["code"], "ARGUMENTS")
            reader.assert_not_called()
            stdin.read.assert_not_called()

        from growthos import analysis
        self.assertFalse(hasattr(analysis, "save_report"))

    def test_old_snapshot_interface_removed_and_current_periods_available(self):
        self.assertEqual(self.invoke_fixture(["status", "--snapshot", "old"])[0], 2)
        code, output = self.invoke_fixture(["verify", "--start", "2026-08", "--end", "2026-09"])
        self.assertEqual(code, 0)
        self.assertEqual(output["months"], ["2026-08", "2026-09"])
        self.assertNotIn("snapshot_id", output)
        self.assertNotIn("certification", output)
        self.assertNotIn("coverage", output)
        self.assertEqual(self.invoke_fixture(["compare", "--previous", "2026-08", "--latest", "2026-10"])[0], 0)


if __name__ == "__main__":
    unittest.main()
