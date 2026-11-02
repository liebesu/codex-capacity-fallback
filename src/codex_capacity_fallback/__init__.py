"""Safe, bounded model fallback for command-line Codex workflows."""

from .core import (
    Attempt,
    FailureKind,
    Policy,
    RunResult,
    build_command,
    classify_output,
    run_with_fallback,
)

__all__ = [
    "Attempt",
    "FailureKind",
    "Policy",
    "RunResult",
    "build_command",
    "classify_output",
    "run_with_fallback",
]

__version__ = "0.1.0"
