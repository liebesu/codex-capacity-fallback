"""Core policy and execution logic.

The runner intentionally treats only an explicit server-capacity signal as
eligible for model fallback. A successful process or an unrelated failure is
returned immediately. Output that looks like a tool/action event blocks a
retry by default because replaying an agent turn can duplicate side effects.
"""

from __future__ import annotations

import os
import random
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence


class FailureKind(str, Enum):
    SUCCESS = "success"
    CAPACITY = "capacity"
    RATE_LIMIT = "rate_limit"
    NETWORK = "network"
    AUTH = "auth"
    POLICY = "policy"
    PARTIAL = "partial"
    UNKNOWN = "unknown"


CAPACITY_MARKERS = (
    "selected model is at capacity",
    "server_is_overloaded",
    "server overloaded",
    "policycapacityunavailable",
    "policy capacity unavailable",
)
RATE_LIMIT_MARKERS = (
    "rate limit",
    "rate_limit",
    "too many requests",
    "http 429",
    "status 429",
)
NETWORK_MARKERS = (
    "connection reset",
    "connection refused",
    "timed out",
    "timeout",
    "tls handshake",
    "temporary failure in name resolution",
)
AUTH_MARKERS = (
    "unauthorized",
    "authentication failed",
    "invalid api key",
    "status 401",
    "status 403",
)
POLICY_MARKERS = (
    "safety refusal",
    "policy refusal",
    "content policy",
    "usage limit reached",
    "quota exceeded",
    "billing",
)
PARTIAL_MARKERS = (
    "tool_call",
    "tool call",
    "function_call",
    "function call",
    "command_execution",
    "exec_command",
    "response.output_item.added",
    "item.started",
)


def _contains_any(text: str, markers: Iterable[str]) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in markers)


def classify_output(text: str, returncode: int | None = None) -> FailureKind:
    """Classify a command result using conservative, ordered signatures."""

    if returncode == 0:
        return FailureKind.SUCCESS
    if _contains_any(text, CAPACITY_MARKERS):
        return FailureKind.CAPACITY
    if _contains_any(text, PARTIAL_MARKERS):
        return FailureKind.PARTIAL
    if _contains_any(text, AUTH_MARKERS):
        return FailureKind.AUTH
    if _contains_any(text, POLICY_MARKERS):
        return FailureKind.POLICY
    if _contains_any(text, RATE_LIMIT_MARKERS):
        return FailureKind.RATE_LIMIT
    if _contains_any(text, NETWORK_MARKERS):
        return FailureKind.NETWORK
    return FailureKind.UNKNOWN


def _parse_retry_after(text: str) -> float | None:
    match = re.search(r"retry[- ]after\s*[:=]\s*(\d+(?:\.\d+)?)", text, re.IGNORECASE)
    if not match:
        return None
    return max(0.0, float(match.group(1)))


def _dedupe_models(models: Iterable[str]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for model in models:
        clean = model.strip()
        if clean and clean not in seen:
            result.append(clean)
            seen.add(clean)
    return tuple(result)


@dataclass(frozen=True)
class Policy:
    """Fallback policy shared by all attempts."""

    models: tuple[str, ...]
    max_attempts: int | None = None
    base_delay: float = 1.0
    max_delay: float = 30.0
    jitter: float = 0.2
    allow_partial_replay: bool = False

    def __post_init__(self) -> None:
        models = _dedupe_models(self.models)
        if not models:
            raise ValueError("at least one fallback model is required")
        if self.max_attempts is not None and self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if self.base_delay < 0 or self.max_delay < 0:
            raise ValueError("delay values must be non-negative")
        if self.jitter < 0 or self.jitter > 1:
            raise ValueError("jitter must be between 0 and 1")
        object.__setattr__(self, "models", models)

    @property
    def attempt_limit(self) -> int:
        return min(self.max_attempts or len(self.models), len(self.models))


@dataclass(frozen=True)
class Attempt:
    index: int
    model: str
    returncode: int
    kind: FailureKind
    elapsed_seconds: float
    retry_after: float | None = None


@dataclass
class RunResult:
    returncode: int
    kind: FailureKind
    attempts: list[Attempt] = field(default_factory=list)
    stdout: str = ""
    stderr: str = ""

    @property
    def succeeded(self) -> bool:
        return self.returncode == 0


def build_command(command: Sequence[str], model: str) -> list[str]:
    """Return *command* with a single ``--model``/``-m`` set to ``model``.

    Codex accepts ``--model`` on the ``exec`` subcommand. If no model flag is
    present, the flag is inserted immediately after ``exec`` when possible.
    """

    if not command:
        raise ValueError("command cannot be empty")
    result = list(command)
    for index, token in enumerate(result):
        if token == "--model" or token == "-m":
            if index + 1 >= len(result):
                raise ValueError(f"{token} must be followed by a model")
            result[index + 1] = model
            return result
        if token.startswith("--model="):
            result[index] = f"--model={model}"
            return result
    try:
        exec_index = result.index("exec")
    except ValueError:
        result.extend(["--model", model])
    else:
        result[exec_index + 1 : exec_index + 1] = ["--model", model]
    return result


def _has_partial_output(text: str) -> bool:
    return _contains_any(text, PARTIAL_MARKERS)


def _default_runner(command: Sequence[str], env: Mapping[str, str] | None, cwd: str | Path | None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        capture_output=True,
        text=True,
        env=dict(env) if env is not None else None,
        cwd=cwd,
        check=False,
    )


def run_with_fallback(
    command: Sequence[str],
    policy: Policy,
    *,
    runner: Callable[[Sequence[str], Mapping[str, str] | None, str | Path | None], subprocess.CompletedProcess[str]] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    random_fn: Callable[[], float] = random.random,
    env: Mapping[str, str] | None = None,
    cwd: str | Path | None = None,
    on_attempt: Callable[[Attempt], None] | None = None,
) -> RunResult:
    """Run a command once per configured model, with capacity-only fallback."""

    runner = runner or _default_runner
    attempts: list[Attempt] = []
    last_stdout = ""
    last_stderr = ""

    for index, model in enumerate(policy.models[: policy.attempt_limit], start=1):
        attempt_command = build_command(command, model)
        started = time.monotonic()
        completed = runner(attempt_command, env, cwd)
        elapsed = time.monotonic() - started
        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
        combined = f"{stdout}\n{stderr}"
        kind = classify_output(combined, completed.returncode)
        if kind == FailureKind.CAPACITY and _has_partial_output(combined) and not policy.allow_partial_replay:
            kind = FailureKind.PARTIAL
        retry_after = _parse_retry_after(combined)
        attempt = Attempt(index, model, completed.returncode, kind, elapsed, retry_after)
        attempts.append(attempt)
        if on_attempt:
            on_attempt(attempt)
        last_stdout, last_stderr = stdout, stderr

        if stdout:
            print(stdout, end="")
        if stderr:
            print(stderr, end="", file=sys.stderr)

        if kind != FailureKind.CAPACITY:
            return RunResult(completed.returncode, kind, attempts, stdout, stderr)
        if index >= policy.attempt_limit:
            break

        delay = retry_after if retry_after is not None else policy.base_delay * (2 ** (index - 1))
        delay = min(policy.max_delay, max(0.0, delay))
        delay += delay * policy.jitter * random_fn()
        sleep(delay)

    return RunResult(attempts[-1].returncode, attempts[-1].kind, attempts, last_stdout, last_stderr)


def default_models() -> tuple[str, ...]:
    configured = os.environ.get("CODEX_FALLBACK_MODELS", "")
    if configured:
        models = _dedupe_models(configured.split(","))
        if models:
            return models
    return ("gpt-6-astra", "gpt-6.1-sol", "gpt-6-luna")
