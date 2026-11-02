from __future__ import annotations

import subprocess

from codex_capacity_fallback.core import (
    FailureKind,
    Policy,
    build_command,
    classify_output,
    run_with_fallback,
)


def completed(code: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(["fake"], code, stdout, stderr)


def test_classification_distinguishes_capacity_from_quota_and_auth() -> None:
    assert classify_output("Selected model is at capacity", 1) is FailureKind.CAPACITY
    assert classify_output("usage limit reached", 1) is FailureKind.POLICY
    assert classify_output("invalid api key", 1) is FailureKind.AUTH
    assert classify_output("HTTP 429 rate limit", 1) is FailureKind.RATE_LIMIT


def test_build_command_replaces_existing_model_forms() -> None:
    assert build_command(["codex", "exec", "--model", "old", "prompt"], "new") == ["codex", "exec", "--model", "new", "prompt"]
    assert build_command(["codex", "exec", "-m", "old", "prompt"], "new") == ["codex", "exec", "-m", "new", "prompt"]
    assert build_command(["codex", "exec", "--model=old", "prompt"], "new") == ["codex", "exec", "--model=new", "prompt"]
    assert build_command(["codex", "exec", "prompt"], "new") == ["codex", "exec", "--model", "new", "prompt"]


def test_fallback_only_on_capacity_and_uses_next_model() -> None:
    calls: list[list[str]] = []
    responses = iter([
        completed(1, stderr="Selected model is at capacity"),
        completed(0, stdout="done"),
    ])

    def runner(command, env, cwd):
        calls.append(list(command))
        return next(responses)

    result = run_with_fallback(
        ["codex", "exec", "prompt"],
        Policy(("primary", "fallback"), base_delay=0, jitter=0),
        runner=runner,
        sleep=lambda _: None,
    )
    assert result.succeeded
    assert [call[3] for call in calls] == ["primary", "fallback"]
    assert [attempt.kind for attempt in result.attempts] == [FailureKind.CAPACITY, FailureKind.SUCCESS]


def test_non_capacity_error_does_not_switch_models() -> None:
    calls: list[list[str]] = []

    def runner(command, env, cwd):
        calls.append(list(command))
        return completed(1, stderr="invalid api key")

    result = run_with_fallback(
        ["codex", "exec", "prompt"],
        Policy(("primary", "fallback"), base_delay=0, jitter=0),
        runner=runner,
        sleep=lambda _: None,
    )
    assert result.kind is FailureKind.AUTH
    assert len(calls) == 1


def test_partial_action_blocks_replay_by_default() -> None:
    calls: list[list[str]] = []

    def runner(command, env, cwd):
        calls.append(list(command))
        return completed(1, stderr="tool_call started\nSelected model is at capacity")

    result = run_with_fallback(
        ["codex", "exec", "prompt"],
        Policy(("primary", "fallback"), base_delay=0, jitter=0),
        runner=runner,
        sleep=lambda _: None,
    )
    assert result.kind is FailureKind.PARTIAL
    assert len(calls) == 1


def test_retry_after_is_respected_and_capped() -> None:
    delays: list[float] = []
    responses = iter([
        completed(1, stderr="Selected model is at capacity\nRetry-After: 90"),
        completed(0),
    ])

    result = run_with_fallback(
        ["codex", "exec", "prompt"],
        Policy(("primary", "fallback"), base_delay=1, max_delay=5, jitter=0),
        runner=lambda command, env, cwd: next(responses),
        sleep=delays.append,
    )
    assert result.succeeded
    assert delays == [5]
