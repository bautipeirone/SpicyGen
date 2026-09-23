#!/usr/bin/env python3
"""Run the one-shot and Codex harnesses and write comparable CSV metrics.

The default matrix contains 24 runs: two harnesses, two context modes, and six
protocols.  Artifacts for every run are kept next to the CSV so failures can be
inspected without rerunning an expensive model request.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_PROTOCOLS = ("http1.1", "dns", "dhcp", "ident", "telnet", "tftp")
DEFAULT_MODES = ("full-context", "criteria-only")
DEFAULT_HARNESSES = ("one-shot", "codex")

HARNESS_SCRIPTS = {
    "one-shot": PROJECT_ROOT / "scripts" / "generate_grammar_one_shot.py",
    "codex": PROJECT_ROOT / "scripts" / "generate_grammar_codex_harness.py",
}

CSV_FIELDS = [
    "run",
    "harness",
    "mode",
    "protocol",
    "model",
    "runner_status",
    "process_return_code",
    "success",
    "compiled",
    "positive_accepted",
    "positive_total",
    "positive_failed",
    "positive_accept_rate",
    "negative_rejected",
    "negative_total",
    "negative_failed",
    "negative_reject_rate",
    "correct_packets",
    "total_packets",
    "packet_accuracy",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "total_cost",
    "actual_prompt_tokens",
    "actual_completion_tokens",
    "actual_total_tokens",
    "actual_total_cost",
    "token_usage_source",
    "time_elapsed_s",
    "wall_time_s",
    "termination_reason",
    "spec_source",
    "codex_output_source",
    "prompt_chars",
    "grammar_chars",
    "grammar_sha256",
    "failure_summary",
    "started_at",
    "finished_at",
    "command",
    "grammar_file",
    "metrics_file",
    "debug_dir",
    "stdout_log",
    "stderr_log",
]


@dataclass(frozen=True)
class RunSpec:
    harness: str
    mode: str
    protocol: str
    run: int
    model: str


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Compare the one-shot and Codex grammar-generation harnesses. "
            "By default this performs 24 sequential runs and writes one CSV row per run."
        )
    )
    parser.add_argument(
        "--model",
        required=True,
        help="Model passed to both harnesses unless overridden below.",
    )
    parser.add_argument(
        "--one-shot-model",
        default=None,
        help="Optional model override for the one-shot harness.",
    )
    parser.add_argument(
        "--codex-model",
        default=None,
        help="Optional model override for the Codex harness.",
    )
    parser.add_argument(
        "--protocols",
        nargs="+",
        default=list(DEFAULT_PROTOCOLS),
        help="Protocol index names to test (default: %(default)s).",
    )
    parser.add_argument(
        "--modes",
        nargs="+",
        choices=DEFAULT_MODES,
        default=list(DEFAULT_MODES),
        help="Context modes to test (default: both).",
    )
    parser.add_argument(
        "--harnesses",
        nargs="+",
        choices=DEFAULT_HARNESSES,
        default=list(DEFAULT_HARNESSES),
        help="Harnesses to test (default: both).",
    )
    parser.add_argument(
        "--repetitions",
        type=int,
        default=1,
        help="Number of runs per harness/mode/protocol combination.",
    )
    parser.add_argument(
        "--output-csv",
        type=Path,
        default=SCRIPT_DIR / "harness-comparison.csv",
        help="Destination CSV (default: %(default)s).",
    )
    parser.add_argument(
        "--artifacts-dir",
        type=Path,
        default=None,
        help="Artifact root (default: <output-csv-stem>-artifacts beside the CSV).",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Optional YAML config passed to both generation scripts.",
    )
    parser.add_argument(
        "--instructions-file",
        type=Path,
        default=None,
        help="Optional Spicy instructions passed to full-context runs.",
    )
    parser.add_argument(
        "--codex-bin",
        default="codex",
        help="Codex CLI executable name or path.",
    )
    parser.add_argument(
        "--python-bin",
        default=sys.executable,
        help="Python executable used to launch the harness scripts.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=None,
        help="Optional timeout for each individual harness run.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Reuse a run when its metrics JSON already exists.",
    )
    args = parser.parse_args(argv)
    if args.repetitions < 1:
        parser.error("--repetitions must be at least 1")
    if args.timeout_seconds is not None and args.timeout_seconds <= 0:
        parser.error("--timeout-seconds must be positive")
    return args


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def run_matrix(args: argparse.Namespace) -> list[RunSpec]:
    models = {
        "one-shot": args.one_shot_model or args.model,
        "codex": args.codex_model or args.model,
    }
    return [
        RunSpec(harness, mode, protocol, run, models[harness])
        for run in range(1, args.repetitions + 1)
        for protocol in args.protocols
        for mode in args.modes
        for harness in args.harnesses
    ]


def build_command(
    spec: RunSpec,
    args: argparse.Namespace,
    *,
    grammar_file: Path,
    metrics_file: Path,
    debug_dir: Path,
) -> list[str]:
    command = [
        args.python_bin,
        str(HARNESS_SCRIPTS[spec.harness]),
        "--mode",
        spec.mode,
        "--model",
        spec.model,
        "--output-file",
        str(grammar_file),
        "--metrics-file",
        str(metrics_file),
        "--debug-dir",
        str(debug_dir),
    ]
    if args.config:
        command.extend(["--config", str(args.config.resolve())])
    if args.instructions_file and spec.mode == "full-context":
        command.extend(["--instructions-file", str(args.instructions_file.resolve())])
    if spec.harness == "codex":
        command.extend(["--codex-bin", args.codex_bin])
    command.append(spec.protocol)
    return command


def read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise ValueError("metrics JSON must contain an object")
    return data


def number(value: Any) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def ratio(numerator: Any, denominator: Any) -> float | None:
    numerator_value = number(numerator)
    denominator_value = number(denominator)
    if numerator_value is None or denominator_value in (None, 0):
        return None
    return round(numerator_value / denominator_value, 6)


def compact_error(text: str, limit: int = 2000) -> str:
    compacted = re.sub(r"\s+", " ", text).strip()
    if len(compacted) <= limit:
        return compacted
    half = (limit - 5) // 2
    return f"{compacted[:half]} ... {compacted[-half:]}"


def usage_columns(metrics: dict[str, Any]) -> dict[str, Any]:
    """Flatten the common AutoGen-shaped usage report into CSV columns."""
    columns: dict[str, Any] = {
        "prompt_tokens": None,
        "completion_tokens": None,
        "total_tokens": None,
        "total_cost": None,
        "actual_prompt_tokens": None,
        "actual_completion_tokens": None,
        "actual_total_tokens": None,
        "actual_total_cost": None,
        "token_usage_source": "unavailable",
    }
    usage = metrics.get("usage")
    if isinstance(usage, dict):
        total_usage = usage.get("total_usage")
        actual_usage = usage.get("actual_usage")
        if isinstance(total_usage, dict):
            for key in ("prompt_tokens", "completion_tokens", "total_tokens", "total_cost"):
                columns[key] = number(total_usage.get(key))
            columns["token_usage_source"] = "metrics"
        if isinstance(actual_usage, dict):
            for key in ("prompt_tokens", "completion_tokens", "total_tokens", "total_cost"):
                columns[f"actual_{key}"] = number(actual_usage.get(key))
    return columns


def artifact_stats(grammar_file: Path, debug_dir: Path) -> dict[str, Any]:
    grammar = grammar_file.read_text(errors="replace") if grammar_file.exists() else ""
    prompt_chars = 0
    if debug_dir.exists():
        for prompt_file in debug_dir.glob("*prompt.txt"):
            prompt_chars += len(prompt_file.read_text(errors="replace"))
    return {
        "prompt_chars": prompt_chars or None,
        "grammar_chars": len(grammar) if grammar else None,
        "grammar_sha256": (
            hashlib.sha256(grammar.encode()).hexdigest() if grammar else None
        ),
    }


def metrics_columns(metrics: dict[str, Any]) -> dict[str, Any]:
    iterations = metrics.get("iterations")
    iteration = iterations[-1] if isinstance(iterations, list) and iterations else {}
    if not isinstance(iteration, dict):
        iteration = {}

    positive_accepted = number(iteration.get("positive_accepted"))
    negative_rejected = number(iteration.get("negative_rejected"))
    positive_total = number(metrics.get("positive_packets"))
    negative_total = number(metrics.get("negative_packets"))
    correct = (
        positive_accepted + negative_rejected
        if positive_accepted is not None and negative_rejected is not None
        else None
    )
    total = (
        positive_total + negative_total
        if positive_total is not None and negative_total is not None
        else None
    )
    feedback = metrics.get("validation_feedback")
    return {
        "success": metrics.get("success"),
        "compiled": iteration.get("compiled"),
        "positive_accepted": positive_accepted,
        "positive_total": positive_total,
        "positive_failed": (
            positive_total - positive_accepted
            if positive_total is not None and positive_accepted is not None
            else None
        ),
        "positive_accept_rate": ratio(positive_accepted, positive_total),
        "negative_rejected": negative_rejected,
        "negative_total": negative_total,
        "negative_failed": (
            negative_total - negative_rejected
            if negative_total is not None and negative_rejected is not None
            else None
        ),
        "negative_reject_rate": ratio(negative_rejected, negative_total),
        "correct_packets": correct,
        "total_packets": total,
        "packet_accuracy": ratio(correct, total),
        "time_elapsed_s": number(metrics.get("time_elapsed_s")),
        "termination_reason": metrics.get("termination_reason"),
        "spec_source": metrics.get("spec_source"),
        "codex_output_source": metrics.get("codex_output_source"),
        "failure_summary": (
            compact_error(feedback)
            if isinstance(feedback, str) and not metrics.get("success")
            else None
        ),
    }


def write_log(path: Path, content: str | bytes | None) -> str:
    if isinstance(content, bytes):
        rendered = content.decode(errors="replace")
    else:
        rendered = content or ""
    path.write_text(rendered)
    return rendered


def execute_run(
    spec: RunSpec,
    args: argparse.Namespace,
    artifacts_root: Path,
) -> dict[str, Any]:
    run_dir = (
        artifacts_root
        / spec.harness
        / spec.mode
        / spec.protocol
        / f"run-{spec.run:02d}"
    )
    run_dir.mkdir(parents=True, exist_ok=True)
    grammar_file = run_dir / "grammar.spicy"
    metrics_file = run_dir / "metrics.json"
    debug_dir = run_dir / "debug"
    stdout_log = run_dir / "stdout.log"
    stderr_log = run_dir / "stderr.log"
    command = build_command(
        spec,
        args,
        grammar_file=grammar_file,
        metrics_file=metrics_file,
        debug_dir=debug_dir,
    )

    started_at = utc_now()
    started = time.monotonic()
    return_code: int | None = None
    stdout = ""
    stderr = ""
    runner_status = "completed"

    if args.resume and metrics_file.exists():
        runner_status = "reused"
        return_code = 0
        stdout = stdout_log.read_text(errors="replace") if stdout_log.exists() else ""
        stderr = stderr_log.read_text(errors="replace") if stderr_log.exists() else ""
    else:
        for stale_file in (grammar_file, metrics_file, stdout_log, stderr_log):
            stale_file.unlink(missing_ok=True)
        try:
            completed = subprocess.run(
                command,
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
                check=False,
                timeout=args.timeout_seconds,
            )
            return_code = completed.returncode
            stdout = completed.stdout
            stderr = completed.stderr
            if return_code != 0:
                runner_status = "process-failed"
        except subprocess.TimeoutExpired as exc:
            runner_status = "timed-out"
            stdout = write_log(stdout_log, exc.stdout)
            stderr = write_log(stderr_log, exc.stderr)
        except OSError as exc:
            runner_status = "start-failed"
            stderr = str(exc)

    stdout = write_log(stdout_log, stdout)
    stderr = write_log(stderr_log, stderr)
    wall_time = round(time.monotonic() - started, 2)

    metrics: dict[str, Any] = {}
    if metrics_file.exists():
        try:
            metrics = read_json(metrics_file)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            runner_status = "invalid-metrics"
            stderr = f"{stderr}\nCould not read metrics: {exc}".strip()
            stderr_log.write_text(stderr)
    elif runner_status in {"completed", "reused"}:
        runner_status = "missing-metrics"

    row = {field: None for field in CSV_FIELDS}
    row.update(
        {
            "run": spec.run,
            "harness": spec.harness,
            "mode": spec.mode,
            "protocol": spec.protocol,
            "model": metrics.get("model", spec.model),
            "runner_status": runner_status,
            "process_return_code": return_code,
            "wall_time_s": wall_time,
            "started_at": started_at,
            "finished_at": utc_now(),
            "command": shlex.join(command),
            "grammar_file": str(grammar_file.resolve()),
            "metrics_file": str(metrics_file.resolve()),
            "debug_dir": str(debug_dir.resolve()),
            "stdout_log": str(stdout_log.resolve()),
            "stderr_log": str(stderr_log.resolve()),
        }
    )
    row.update(metrics_columns(metrics))
    row.update(usage_columns(metrics))
    row.update(artifact_stats(grammar_file, debug_dir))
    if runner_status not in {"completed", "reused"} and not row["failure_summary"]:
        row["failure_summary"] = compact_error(stderr or runner_status)
    return row


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    output_csv = args.output_csv.resolve()
    artifacts_root = (
        args.artifacts_dir.resolve()
        if args.artifacts_dir
        else output_csv.with_name(f"{output_csv.stem}-artifacts")
    )
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    artifacts_root.mkdir(parents=True, exist_ok=True)

    matrix = run_matrix(args)
    failed_runs = 0
    with output_csv.open("w", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=CSV_FIELDS)
        writer.writeheader()
        csv_file.flush()
        for index, spec in enumerate(matrix, start=1):
            print(
                f"[{index}/{len(matrix)}] {spec.harness} {spec.mode} "
                f"{spec.protocol} (run {spec.run}, model {spec.model})",
                file=sys.stderr,
                flush=True,
            )
            row = execute_run(spec, args, artifacts_root)
            writer.writerow(row)
            csv_file.flush()
            if row["runner_status"] not in {"completed", "reused"}:
                failed_runs += 1
            outcome = "PASS" if row["success"] is True else "FAIL"
            print(
                f"  {row['runner_status']}: validation={outcome}, "
                f"packets={row['correct_packets']}/{row['total_packets']}, "
                f"tokens={row['total_tokens']}",
                file=sys.stderr,
                flush=True,
            )

    print(f"Wrote {len(matrix)} rows to {output_csv}", file=sys.stderr)
    print(f"Artifacts: {artifacts_root}", file=sys.stderr)
    if failed_runs:
        print(f"{failed_runs} run(s) failed before producing normal metrics.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
