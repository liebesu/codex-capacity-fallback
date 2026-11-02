"""Command-line entry point."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from .core import Policy, default_models, run_with_fallback


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="codex-capacity-fallback",
        description="Retry a command on the next approved model after a capacity error.",
        epilog="Example: codex-capacity-fallback -- codex exec --json 'summarize this repo'",
    )
    parser.add_argument(
        "--models",
        default=",".join(default_models()),
        help="comma-separated model order (or set CODEX_FALLBACK_MODELS)",
    )
    parser.add_argument("--max-attempts", type=int, default=None)
    parser.add_argument("--base-delay", type=float, default=1.0)
    parser.add_argument("--max-delay", type=float, default=30.0)
    parser.add_argument("--jitter", type=float, default=0.2)
    parser.add_argument(
        "--allow-partial-replay",
        action="store_true",
        help="allow fallback after tool/action markers; use only for idempotent work",
    )
    parser.add_argument(
        "--log-file",
        type=Path,
        help="append redacted JSON attempt metadata (never command output or prompts)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the planned model order without running the command",
    )
    parser.add_argument("command", nargs=argparse.REMAINDER, help="command after --")
    return parser


def _write_log(path: Path, attempts: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for attempt in attempts:
            handle.write(json.dumps({
                "index": attempt.index,
                "model": attempt.model,
                "returncode": attempt.returncode,
                "kind": attempt.kind.value,
                "elapsed_seconds": round(attempt.elapsed_seconds, 3),
                "retry_after": attempt.retry_after,
                "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }, ensure_ascii=False) + "\n")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    if not command and not args.dry_run:
        print("error: provide a command after --", file=sys.stderr)
        return 2

    models = tuple(model.strip() for model in args.models.split(",") if model.strip())
    try:
        policy = Policy(
            models=models,
            max_attempts=args.max_attempts,
            base_delay=args.base_delay,
            max_delay=args.max_delay,
            jitter=args.jitter,
            allow_partial_replay=args.allow_partial_replay,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.dry_run:
        print(json.dumps({
            "models": list(policy.models[: policy.attempt_limit]),
            "max_attempts": policy.attempt_limit,
            "command": command,
            "partial_replay": policy.allow_partial_replay,
        }, ensure_ascii=False, indent=2))
        return 0

    result = run_with_fallback(command, policy)
    if args.log_file:
        _write_log(args.log_file, result.attempts)
    return result.returncode if result.returncode >= 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
