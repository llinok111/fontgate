"""Error taxonomy and the exit-code contract.

The exit codes are part of the CLI's public interface and are asserted in
tests, because CI pipelines branch on them:

===== ==========================================================
code  meaning
===== ==========================================================
0     every requested check passed
1     at least one check failed (findings at ``error`` severity)
2     the command line itself was wrong
3     an input could not be read (missing, corrupt, unsupported)
4     a size budget was exceeded while all other checks passed
===== ==========================================================
"""

from __future__ import annotations

EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_USAGE = 2
EXIT_INPUT = 3
EXIT_BUDGET = 4


class FontgateError(Exception):
    """Base class for all errors that should reach the user without a traceback."""

    code = "error"
    exit_code = EXIT_INPUT


class UsageError(FontgateError):
    code = "usage"
    exit_code = EXIT_USAGE


class InputError(FontgateError):
    code = "input"
    exit_code = EXIT_INPUT


class UnsupportedFormatError(InputError):
    code = "unsupported-format"


class TextReadError(InputError):
    code = "text-read"


class BudgetExceeded(FontgateError):
    code = "budget-exceeded"
    exit_code = EXIT_BUDGET


class MissingDependencyError(FontgateError):
    code = "missing-dependency"
    exit_code = EXIT_INPUT
