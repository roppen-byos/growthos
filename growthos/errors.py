import argparse
from contextlib import contextmanager
from enum import Enum
import json
import sys
from typing import Callable, NoReturn

MAX_OUTPUT_BYTES = 131_072


class GrowthOSError(Exception):
    """Only fixed, reviewed messages may cross the tool boundary."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def reject(code: str, message: str) -> NoReturn:
    raise GrowthOSError(code, message)


class DiagnosticStage(Enum):
    ARGUMENTS = "arguments"
    READER = "reader_lifecycle"
    CATALOG = "catalog"
    LOAD = "load"
    ANALYSIS = "analysis"
    SERIALIZATION = "serialization"
    EXECUTION = "execution"


class _UnexpectedFailure(Exception):
    def __init__(self, stage: DiagnosticStage):
        super().__init__()
        self.stage = stage if isinstance(stage, DiagnosticStage) else DiagnosticStage.EXECUTION


@contextmanager
def diagnostic_stage(stage: DiagnosticStage):
    """Mark only unexpected failures, without inspecting or formatting exceptions."""
    try:
        yield
    except (GrowthOSError, _UnexpectedFailure):
        raise
    except Exception:
        raise _UnexpectedFailure(stage) from None


class SafeParser(argparse.ArgumentParser):
    def __init__(self, *args, command_name: str | None = None, allowed_commands: tuple[str, ...] = (), **kwargs):
        self.command_name = command_name
        self.allowed_commands = allowed_commands
        self._active_command: str | None = None
        super().__init__(*args, **kwargs)

    def parse_args(self, args=None, namespace=None):
        tokens = sys.argv[1:] if args is None else args
        self._active_command = tokens[0] if tokens and tokens[0] in self.allowed_commands else None
        return super().parse_args(args, namespace)

    def error(self, message):
        cmd = self.command_name or self._active_command
        if cmd:
            reject("ARGUMENTS", f"Unsupported or missing arguments for '{cmd}'; run '{cmd} --help' for reviewed flags. Input values withheld.")
        reject("ARGUMENTS", "Unsupported or missing arguments; run --help for reviewed operations. Input values withheld.")


def execute_cli(
    fn: Callable[[], dict], *, max_bytes: int = MAX_OUTPUT_BYTES,
    failure_code: str = "OPERATION_FAILED", diagnostics: bool = False,
) -> int:
    """Emit bounded JSON; optional diagnostics contain only a fixed failure stage."""
    try:
        result = fn()
        with diagnostic_stage(DiagnosticStage.SERIALIZATION):
            output = json.dumps(result, sort_keys=True, allow_nan=False, separators=(",", ":"))
            if len(output.encode()) > max_bytes:
                reject("OUTPUT_BOUND", "Evidence exceeds the output limit; narrow the request. No partial output emitted.")
    except GrowthOSError as error:
        print(json.dumps({"error": {"code": error.code, "message": error.message}}))
        return 130 if error.code == "CANCELLED" else 2
    except KeyboardInterrupt:
        print(json.dumps({"error": {"code": "CANCELLED", "message": "Operation cancelled. If a write was in progress, inspect current data before restarting."}}))
        return 130
    except _UnexpectedFailure as error:
        return _unexpected_result(failure_code, error.stage, diagnostics)
    except Exception:
        return _unexpected_result(failure_code, DiagnosticStage.EXECUTION, diagnostics)
    print(output)
    return 0


def _unexpected_result(failure_code: str, stage: DiagnosticStage, diagnostics: bool) -> int:
    print(json.dumps({"error": {"code": failure_code, "message": "Operation failed; diagnostic details withheld."}}))
    if diagnostics:
        # Only literals and enum values cross this seam. Never inspect exception
        # messages, class names, arguments, causes, tracebacks or local variables.
        try:
            print(json.dumps({"diagnostic": {"kind": "unexpected_failure", "stage": stage.value}}), file=sys.stderr)
        except Exception:
            # A broken diagnostic sink must not replace the original safe failure.
            pass
    return 2
