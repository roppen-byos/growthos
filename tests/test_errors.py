from contextlib import ExitStack, contextmanager, nullcontext, redirect_stderr, redirect_stdout
import io
import json
import unittest
from unittest.mock import Mock, patch

from growthos.cli import main
from growthos.errors import DiagnosticStage, GrowthOSError, diagnostic_stage, execute_cli
from fixtures import FixtureReaderStore, demo_data


SECRETS = (
    "PRIVATE_password_marker", "PRIVATE_token_marker", "postgresql://reader:PRIVATE_dsn_marker@host.invalid/db",
    "PRIVATE_query_parameter", "/private/PRIVATE_path_marker", "PRIVATE_exception_type", "PRIVATE_OBJECT_marker",
)
SAFE_FAILURE = {"error": {"code": "OPERATION_FAILED", "message": "Operation failed; diagnostic details withheld."}}


class SensitiveFailure(Exception):
    def __str__(self):
        raise AssertionError("Exception text must never be inspected")

    def __repr__(self):
        raise AssertionError("Exception repr must never be inspected")


def sensitive_failure():
    error = SensitiveFailure(*SECRETS)
    error.__cause__ = RuntimeError(" ".join(SECRETS))
    error.stage = " ".join(SECRETS)
    return error


def fail():
    raise sensitive_failure()


class DiagnosticTests(unittest.TestCase):
    def capture(self, function):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = function()
        combined = stdout.getvalue() + stderr.getvalue()
        for secret in (*SECRETS, "SensitiveFailure", "Traceback"):
            self.assertNotIn(secret, combined)
        return code, json.loads(stdout.getvalue()), stderr.getvalue()

    def assert_diagnostic(self, result, stage):
        code, output, stderr = result
        self.assertEqual((code, output), (2, SAFE_FAILURE))
        self.assertEqual(json.loads(stderr), {
            "diagnostic": {"kind": "unexpected_failure", "stage": stage},
        })
        self.assertEqual(len(stderr.splitlines()), 1)
        self.assertLessEqual(len(stderr.encode()), 128)

    def test_cli_localizes_all_runtime_stages_without_formatting_sensitive_errors(self):
        private_object = type("PRIVATE_OBJECT_marker", (), {"__repr__": lambda self: fail()})()
        for stage in ("arguments", "reader_lifecycle", "catalog", "load", "analysis", "serialization"):
            with self.subTest(stage=stage), ExitStack() as stack:
                store = Mock()
                store.load.return_value = demo_data()
                stack.enter_context(patch("growthos.cli.connect_reader", return_value=nullcontext(store)))
                arguments = ["status"] if stage == "catalog" else ["compare"]
                if stage == "arguments":
                    stack.enter_context(patch("growthos.cli.parser", side_effect=sensitive_failure()))
                elif stage == "reader_lifecycle":
                    stack.enter_context(patch("growthos.cli.connect_reader", side_effect=sensitive_failure()))
                elif stage == "catalog":
                    store.catalog.side_effect = sensitive_failure()
                elif stage == "load":
                    store.load.side_effect = sensitive_failure()
                elif stage == "analysis":
                    stack.enter_context(patch("growthos.cli.analyze", side_effect=sensitive_failure()))
                else:
                    stack.enter_context(patch("growthos.cli.analyze", return_value={"value": private_object}))
                # Diagnostics must not open a file or introduce a fallback sink.
                stack.enter_context(patch("builtins.open", side_effect=AssertionError("No file writes or reads")))
                self.assert_diagnostic(self.capture(lambda: main(arguments)), stage)

    def test_reader_cleanup_failure_is_localized_before_any_success_output(self):
        @contextmanager
        def reader():
            yield FixtureReaderStore(demo_data())
            raise sensitive_failure()

        with patch("growthos.cli.connect_reader", reader):
            self.assert_diagnostic(self.capture(lambda: main(["compare"])), "reader_lifecycle")

    def test_nested_contexts_preserve_inner_failure_and_do_not_leak_state(self):
        def nested():
            with diagnostic_stage(DiagnosticStage.READER):
                with diagnostic_stage(DiagnosticStage.LOAD):
                    fail()

        self.assert_diagnostic(self.capture(lambda: execute_cli(nested, diagnostics=True)), "load")
        self.assert_diagnostic(self.capture(lambda: execute_cli(fail, diagnostics=True)), "execution")

    def test_untrusted_stage_falls_back_to_fixed_execution_label(self):
        def invalid_stage():
            with diagnostic_stage(" ".join(SECRETS)):
                fail()

        self.assert_diagnostic(self.capture(lambda: execute_cli(invalid_stage, diagnostics=True)), "execution")

    def test_expected_errors_keep_stdout_and_do_not_emit_diagnostics(self):
        def expected():
            with diagnostic_stage(DiagnosticStage.LOAD):
                raise GrowthOSError("COVERAGE", "Requested months are unavailable; no substitute was selected.")

        code, output, stderr = self.capture(lambda: execute_cli(expected, diagnostics=True))
        self.assertEqual(code, 2)
        self.assertEqual(output, {"error": {
            "code": "COVERAGE", "message": "Requested months are unavailable; no substitute was selected.",
        }})
        self.assertEqual(stderr, "")
        code, output, stderr = self.capture(lambda: main(["rank", "--password=" + SECRETS[0]]))
        self.assertEqual((code, output["error"]["code"], stderr), (2, "ARGUMENTS", ""))

    def test_invalid_analysis_arguments_do_not_echo_sensitive_input(self):
        for flag in ("--sort", "--city", "--tier", "--column", "--previous"):
            store = FixtureReaderStore(demo_data())
            with self.subTest(flag=flag), patch("growthos.cli.connect_reader", return_value=nullcontext(store)):
                arguments = ["funnel", flag, SECRETS[1]]
                if flag == "--previous":
                    arguments += ["--latest", "2026-10"]
                code, output, stderr = self.capture(lambda: main(arguments))
                self.assertEqual(code, 2)
                self.assertIn(output["error"]["code"], ("ARGUMENTS", "CITY", "MONTH"))
                self.assertEqual(stderr, "")

    def test_cancellation_preserves_exit_codes_and_has_no_diagnostic(self):
        for error in (KeyboardInterrupt(), GrowthOSError("CANCELLED", "Operation cancelled.")):
            def cancel():
                with diagnostic_stage(DiagnosticStage.ANALYSIS):
                    raise error

            with self.subTest(kind=type(error).__name__):
                code, output, stderr = self.capture(lambda: execute_cli(cancel, diagnostics=True))
                self.assertEqual((code, output["error"]["code"], stderr), (130, "CANCELLED", ""))

    def test_output_bounds_emit_no_partial_result_or_diagnostic(self):
        code, output, stderr = self.capture(lambda: execute_cli(
            lambda: {"value": " ".join(SECRETS)}, max_bytes=8, diagnostics=True,
        ))
        self.assertEqual((code, output["error"]["code"], stderr), (2, "OUTPUT_BOUND", ""))

    def test_shared_executor_diagnostics_are_disabled_by_default(self):
        for function in (fail, lambda: {"not_json": object()}):
            code, output, stderr = self.capture(lambda: execute_cli(function))
            self.assertEqual((code, output, stderr), (2, SAFE_FAILURE, ""))
        code, output, stderr = self.capture(lambda: execute_cli(
            fail, failure_code="WAREHOUSE_FAILED", diagnostics=False,
        ))
        self.assertEqual((code, output["error"]["code"], stderr), (2, "WAREHOUSE_FAILED", ""))

    def test_broken_stderr_does_not_replace_safe_stdout_failure(self):
        class BrokenSink:
            def write(self, value):
                raise OSError(" ".join(SECRETS))

        stdout = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(BrokenSink()):
            code = execute_cli(fail, diagnostics=True)
        self.assertEqual((code, json.loads(stdout.getvalue())), (2, SAFE_FAILURE))

    def test_success_and_help_do_not_emit_diagnostics(self):
        code, output, stderr = self.capture(lambda: execute_cli(lambda: {"valid": True}, diagnostics=True))
        self.assertEqual((code, output, stderr), (0, {"valid": True}, ""))
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr), self.assertRaises(SystemExit) as raised:
            main(["--help"])
        self.assertEqual(raised.exception.code, 0)
        self.assertIn("share-drivers", stdout.getvalue())
        self.assertEqual(stderr.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
