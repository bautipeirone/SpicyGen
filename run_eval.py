#!/usr/bin/env python3
"""
Evaluation runner for generate_grammar.py.

Executes a configurable matrix of (protocol × model × config × target_language)
runs, tracks state for resume/retry, and produces aggregated reports.

Usage:
    python run_eval.py evals/example.yaml
    python run_eval.py evals/example.yaml --dry-run
    python run_eval.py evals/example.yaml --debug --parallelism 2
    python run_eval.py evals/example.yaml --resume --eval-dir outputs/20260605_...
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import textwrap
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_OUTPUT_BASE = Path("outputs")
STATE_FILENAME = "eval_state.json"
REPORT_FILENAME = "report.jsonl"
SUMMARY_FILENAME = "summary.md"
EVAL_CONFIG_FILENAME = "eval_config.yaml"
README_FILENAME = "README.md"
RUNS_DIRNAME = "runs"

MODEL_INDEX_FILE = Path("model_index.json")

GLOBAL_DEFAULT_MODELS: list[str] = []
GLOBAL_DEFAULT_CONFIGS: list[str] = []
GLOBAL_DEFAULT_TARGET_LANGUAGES: list[str] = ["spicy"]
GLOBAL_DEFAULT_NO_SPEC: bool = False


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class RunSpec:
    """A single evaluation run specification."""
    protocol: str
    model: str
    config: str | None        # Path to config YAML, or None for default
    target_language: str
    spec_file: str | None     # Custom spec file override, or None
    no_spec: bool             # Run without specification

    @property
    def run_id(self) -> str:
        config_label = Path(self.config).stem if self.config else "noconfig"
        return f"{self.protocol}__{self.model}__{config_label}__{self.target_language}"


@dataclass
class RunResult:
    """Status of a single run."""
    status: str = "pending"          # pending | running | completed | failed
    success: bool | None = None      # None until completed
    error: str | None = None
    started_at: str | None = None
    finished_at: str | None = None


@dataclass
class EvalState:
    """Persistent state for the full evaluation session."""
    runs: dict[str, dict[str, Any]] = field(default_factory=dict)

    def get_run(self, run_id: str) -> RunResult:
        data = self.runs.get(run_id, {})
        return RunResult(**{k: v for k, v in data.items() if k in RunResult.__dataclass_fields__})

    def set_run(self, run_id: str, result: RunResult) -> None:
        self.runs[run_id] = asdict(result)

    def save(self, path: Path) -> None:
        path.write_text(json.dumps({"runs": self.runs}, indent=2))

    @classmethod
    def load(cls, path: Path) -> "EvalState":
        if not path.exists():
            return cls()
        data = json.loads(path.read_text())
        return cls(runs=data.get("runs", {}))


# ---------------------------------------------------------------------------
# YAML parsing & matrix expansion
# ---------------------------------------------------------------------------

def load_eval_config(path: Path) -> dict[str, Any]:
    """Load and validate the evaluation YAML config."""
    with path.open("r") as f:
        config = yaml.safe_load(f)

    if not isinstance(config, dict):
        raise ValueError(f"Eval config must be a YAML mapping, got {type(config).__name__}")
    if "protocols" not in config or not config["protocols"]:
        raise ValueError("Eval config must define at least one protocol under 'protocols'")

    return config


def expand_matrix(config: dict[str, Any]) -> list[RunSpec]:
    """Expand the eval config into a flat list of RunSpec objects."""
    defaults = config.get("defaults", {})

    default_models = defaults.get("models", GLOBAL_DEFAULT_MODELS)
    default_configs = defaults.get("configs", GLOBAL_DEFAULT_CONFIGS)
    default_target_langs = defaults.get("target_languages", GLOBAL_DEFAULT_TARGET_LANGUAGES)
    default_no_spec = defaults.get("no_spec", GLOBAL_DEFAULT_NO_SPEC)
    default_spec_file = defaults.get("spec_file", None)

    runs: list[RunSpec] = []

    for protocol_name, proto_config in config["protocols"].items():
        if proto_config is None:
            proto_config = {}

        models = proto_config.get("models", default_models)
        configs = proto_config.get("configs", default_configs)
        target_langs = proto_config.get("target_languages", default_target_langs)
        no_spec = proto_config.get("no_spec", default_no_spec)
        spec_file = proto_config.get("spec_file", default_spec_file)

        if not models:
            raise ValueError(
                f"No models specified for protocol '{protocol_name}' and no global defaults set. "
                f"Add models under 'defaults.models' or 'protocols.{protocol_name}.models'."
            )

        # Ensure configs list is never empty — use [None] to represent "no config file"
        if not configs:
            configs = [None]

        for model in models:
            for cfg in configs:
                for tl in target_langs:
                    runs.append(RunSpec(
                        protocol=protocol_name,
                        model=model,
                        config=cfg,
                        target_language=tl,
                        spec_file=spec_file,
                        no_spec=no_spec,
                    ))

    return runs


# ---------------------------------------------------------------------------
# Model index helpers
# ---------------------------------------------------------------------------

def load_model_index() -> dict[str, Any]:
    """Load model_index.json for cost information."""
    if not MODEL_INDEX_FILE.exists():
        return {}
    return json.loads(MODEL_INDEX_FILE.read_text())


def get_model_display_name(model_index: dict, model_id: str) -> str:
    entry = model_index.get(model_id, {})
    return entry.get("name", model_id)


def get_model_price(model_index: dict, model_id: str) -> tuple[float, float] | None:
    """Returns (input_price_per_1k, output_price_per_1k) or None."""
    entry = model_index.get(model_id, {})
    price = entry.get("price")
    if price and len(price) >= 2:
        try:
            return (float(price[0]), float(price[1]))
        except (ValueError, TypeError):
            return None
    return None


# ---------------------------------------------------------------------------
# Target language extension helper
# ---------------------------------------------------------------------------

# Extension lookup mapping (avoids importing project code which may have heavy deps)
_TARGET_LANGUAGE_EXTENSIONS: dict[str, str] = {
    "spicy": ".spicy",
    "3d": ".3d",
}


def get_source_extension(target_language: str) -> str:
    return _TARGET_LANGUAGE_EXTENSIONS.get(target_language, f".{target_language}")


# ---------------------------------------------------------------------------
# Single run execution
# ---------------------------------------------------------------------------

def execute_run(
    run: RunSpec,
    run_dir: Path,
    debug: bool,
) -> tuple[bool, str | None]:
    """
    Execute a single generate_grammar.py run as a subprocess.

    Returns (success, error_message).
    """
    run_dir.mkdir(parents=True, exist_ok=True)

    ext = get_source_extension(run.target_language)
    output_file = run_dir / f"output{ext}"
    metrics_file = run_dir / "metrics.json"

    cmd = [
        sys.executable, "generate_grammar.py",
        "-m", run.model,
        "-t", run.target_language,
        "-o", str(output_file),
        "--metrics-file", str(metrics_file),
    ]

    if run.config:
        cmd.extend(["-c", run.config])

    if run.no_spec:
        cmd.append("--no-spec")
    elif run.spec_file:
        cmd.extend(["--spec-file", run.spec_file])

    if debug:
        debug_dir = run_dir / "debug"
        debug_dir.mkdir(parents=True, exist_ok=True)
        cmd.extend(["--debug-dir", str(debug_dir)])

    cmd.append(run.protocol)

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=3600,  # 1 hour timeout per run
        )

        # Save stdout/stderr for debugging
        if result.stdout:
            (run_dir / "stdout.txt").write_text(result.stdout)
        if result.stderr:
            (run_dir / "stderr.txt").write_text(result.stderr)

        if result.returncode != 0:
            error_msg = result.stderr.strip()[-500:] if result.stderr else f"Exit code {result.returncode}"
            return False, error_msg

        return True, None

    except subprocess.TimeoutExpired:
        return False, "Run timed out after 3600 seconds"
    except Exception as e:
        return False, str(e)


# ---------------------------------------------------------------------------
# Report aggregation
# ---------------------------------------------------------------------------

def aggregate_report(eval_dir: Path) -> list[dict[str, Any]]:
    """Collect all per-run metrics.json files into a single report."""
    runs_dir = eval_dir / RUNS_DIRNAME
    all_metrics: list[dict[str, Any]] = []

    if not runs_dir.exists():
        return all_metrics

    for metrics_file in sorted(runs_dir.rglob("metrics.json")):
        try:
            data = json.loads(metrics_file.read_text())
            # Tag with run_id for traceability
            run_id = metrics_file.parent.name
            data["run_id"] = run_id
            all_metrics.append(data)
        except (json.JSONDecodeError, OSError):
            continue

    return all_metrics


def write_report_jsonl(eval_dir: Path, metrics: list[dict[str, Any]]) -> None:
    """Write aggregated metrics as JSONL."""
    report_path = eval_dir / REPORT_FILENAME
    with report_path.open("w") as f:
        for entry in metrics:
            f.write(json.dumps(entry) + "\n")


# ---------------------------------------------------------------------------
# Summary generation
# ---------------------------------------------------------------------------

def generate_summary(
    eval_dir: Path,
    config: dict[str, Any],
    state: EvalState,
    metrics: list[dict[str, Any]],
    model_index: dict[str, Any],
) -> None:
    """Generate a human-readable summary.md with comparison tables."""
    lines: list[str] = []
    name = config.get("name", "Unnamed Evaluation")
    note = config.get("note", "")

    lines.append(f"# Evaluation Summary: {name}")
    lines.append("")
    if note:
        lines.append(f"> {note}")
        lines.append("")

    # --- Overall stats ---
    total = len(state.runs)
    completed = sum(1 for r in state.runs.values() if r.get("status") == "completed")
    failed = sum(1 for r in state.runs.values() if r.get("status") == "failed")
    pending = sum(1 for r in state.runs.values() if r.get("status") == "pending")
    successful = sum(1 for r in state.runs.values()
                     if r.get("status") == "completed" and r.get("success"))

    lines.append("## Overview")
    lines.append("")
    lines.append(f"| Metric | Value |")
    lines.append(f"|--------|-------|")
    lines.append(f"| Total runs | {total} |")
    lines.append(f"| Completed | {completed} |")
    lines.append(f"| Successful | {successful} |")
    lines.append(f"| Failed | {failed} |")
    lines.append(f"| Pending | {pending} |")
    lines.append(f"| Success rate | {successful}/{completed} ({_pct(successful, completed)}) |")
    lines.append("")

    if not metrics:
        lines.append("*No metrics data available yet.*")
        (eval_dir / SUMMARY_FILENAME).write_text("\n".join(lines))
        return

    # --- Results table ---
    lines.append("## Results")
    lines.append("")
    lines.append(
        "| Protocol | Model | Config | Target | Success | Iterations | "
        "Best Iter | Compile@1 | Time (s) | Cost ($) |"
    )
    lines.append(
        "|----------|-------|--------|--------|---------|------------|"
        "-----------|-----------|----------|----------|"
    )

    for m in metrics:
        protocol = m.get("protocol", "?")
        model = m.get("model", "?")
        config_label = m.get("run_id", "").split("__")[2] if "__" in m.get("run_id", "") else "?"
        target = m.get("target_language", "?")
        success = "✅" if m.get("success") else "❌"
        iterations = len(m.get("iterations", []))
        best_iter = m.get("best_iteration", "—")
        first_compile = m.get("first_successful_compile_iteration", "—")
        elapsed = m.get("time_elapsed_s", "—")
        cost = _extract_cost(m)
        cost_str = f"{cost:.4f}" if cost is not None else "—"

        lines.append(
            f"| {protocol} | {model} | {config_label} | {target} | {success} | "
            f"{iterations} | {best_iter} | {first_compile} | {elapsed} | {cost_str} |"
        )

    lines.append("")

    # --- Per-model aggregate stats ---
    lines.append("## Per-Model Statistics")
    lines.append("")

    model_stats: dict[str, dict[str, Any]] = {}
    for m in metrics:
        model = m.get("model", "?")
        if model not in model_stats:
            model_stats[model] = {
                "total": 0, "success": 0, "total_time": 0.0,
                "total_cost": 0.0, "total_iterations": 0,
            }
        stats = model_stats[model]
        stats["total"] += 1
        if m.get("success"):
            stats["success"] += 1
        stats["total_time"] += m.get("time_elapsed_s", 0)
        cost = _extract_cost(m)
        if cost is not None:
            stats["total_cost"] += cost
        stats["total_iterations"] += len(m.get("iterations", []))

    lines.append("| Model | Runs | Successes | Success Rate | Avg Time (s) | Avg Iterations | Total Cost ($) |")
    lines.append("|-------|------|-----------|--------------|--------------|----------------|----------------|")

    for model, stats in sorted(model_stats.items()):
        avg_time = stats["total_time"] / stats["total"] if stats["total"] else 0
        avg_iters = stats["total_iterations"] / stats["total"] if stats["total"] else 0
        lines.append(
            f"| {model} | {stats['total']} | {stats['success']} | "
            f"{_pct(stats['success'], stats['total'])} | "
            f"{avg_time:.1f} | {avg_iters:.1f} | {stats['total_cost']:.4f} |"
        )

    lines.append("")

    # --- Per-protocol aggregate stats ---
    lines.append("## Per-Protocol Statistics")
    lines.append("")

    proto_stats: dict[str, dict[str, Any]] = {}
    for m in metrics:
        protocol = m.get("protocol", "?")
        if protocol not in proto_stats:
            proto_stats[protocol] = {
                "total": 0, "success": 0, "total_time": 0.0, "total_iterations": 0,
            }
        stats = proto_stats[protocol]
        stats["total"] += 1
        if m.get("success"):
            stats["success"] += 1
        stats["total_time"] += m.get("time_elapsed_s", 0)
        stats["total_iterations"] += len(m.get("iterations", []))

    lines.append("| Protocol | Runs | Successes | Success Rate | Avg Time (s) | Avg Iterations |")
    lines.append("|----------|------|-----------|--------------|--------------|----------------|")

    for protocol, stats in sorted(proto_stats.items()):
        avg_time = stats["total_time"] / stats["total"] if stats["total"] else 0
        avg_iters = stats["total_iterations"] / stats["total"] if stats["total"] else 0
        lines.append(
            f"| {protocol} | {stats['total']} | {stats['success']} | "
            f"{_pct(stats['success'], stats['total'])} | "
            f"{avg_time:.1f} | {avg_iters:.1f} |"
        )

    lines.append("")

    # --- Failed runs ---
    failed_entries = [
        (run_id, data) for run_id, data in state.runs.items()
        if data.get("status") == "failed"
    ]
    if failed_entries:
        lines.append("## Failed Runs")
        lines.append("")
        for run_id, data in failed_entries:
            error = data.get("error", "Unknown error")
            lines.append(f"### `{run_id}`")
            lines.append(f"```")
            lines.append(error[:500])
            lines.append(f"```")
            lines.append("")

    (eval_dir / SUMMARY_FILENAME).write_text("\n".join(lines))


def _pct(numerator: int, denominator: int) -> str:
    if denominator == 0:
        return "—"
    return f"{numerator / denominator * 100:.0f}%"


def _extract_cost(metrics: dict[str, Any]) -> float | None:
    """Extract total cost from metrics usage data."""
    usage = metrics.get("usage")
    if not usage:
        return None
    total_usage = usage.get("total_usage") or usage.get("actual_usage")
    if total_usage and "total_cost" in total_usage:
        try:
            return float(total_usage["total_cost"])
        except (ValueError, TypeError):
            return None
    return None


# ---------------------------------------------------------------------------
# README generation
# ---------------------------------------------------------------------------

def write_readme(eval_dir: Path, config: dict[str, Any], runs: list[RunSpec], debug: bool) -> None:
    """Write a README.md summarizing the evaluation session."""
    name = config.get("name", "Unnamed Evaluation")
    note = config.get("note", "No note provided.")
    defaults = config.get("defaults", {})

    protocols = sorted(set(r.protocol for r in runs))
    models = sorted(set(r.model for r in runs))
    configs = sorted(set(r.config or "default" for r in runs))
    target_langs = sorted(set(r.target_language for r in runs))

    content = textwrap.dedent(f"""\
    # Evaluation: {name}

    - **Timestamp:** {eval_dir.name}
    - **Total runs:** {len(runs)}
    - **Protocols:** {', '.join(protocols)}
    - **Models:** {', '.join(models)}
    - **Configs:** {', '.join(configs)}
    - **Target languages:** {', '.join(target_langs)}
    - **Debug:** {'enabled' if debug else 'disabled'}

    ## Note
    {note}
    """)

    (eval_dir / README_FILENAME).write_text(content)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run evaluation matrix for generate_grammar.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
        Examples:
          python run_eval.py evals/example.yaml
          python run_eval.py evals/example.yaml --dry-run
          python run_eval.py evals/example.yaml --debug --parallelism 2
          python run_eval.py evals/example.yaml --resume --eval-dir outputs/20260605_...
          python run_eval.py evals/example.yaml --retry-failed --eval-dir outputs/20260605_...
        """),
    )

    parser.add_argument(
        "eval_config",
        type=Path,
        help="Path to the evaluation YAML config file",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_BASE,
        help=f"Base output directory (default: {DEFAULT_OUTPUT_BASE})",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug output for all runs",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the run matrix without executing anything",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume an incomplete evaluation (skip completed runs)",
    )
    parser.add_argument(
        "--retry-failed",
        action="store_true",
        help="Re-run only failed runs from a previous evaluation",
    )
    parser.add_argument(
        "--eval-dir",
        type=Path,
        default=None,
        help="Path to a previous eval directory (for --resume or --retry-failed)",
    )
    parser.add_argument(
        "--parallelism",
        type=int,
        default=1,
        help="Number of parallel runs (default: 1, sequential)",
    )

    args = parser.parse_args()

    if (args.resume or args.retry_failed) and not args.eval_dir:
        parser.error("--eval-dir is required when using --resume or --retry-failed")

    return args


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------

def print_matrix(runs: list[RunSpec]) -> None:
    """Pretty-print the run matrix for --dry-run."""
    print(f"\n{'='*70}")
    print(f"  EVALUATION MATRIX — {len(runs)} runs")
    print(f"{'='*70}\n")

    header = f"  {'#':<4} {'Protocol':<15} {'Model':<22} {'Config':<18} {'Target':<8} {'Spec'}"
    print(header)
    print(f"  {'—'*4} {'—'*15} {'—'*22} {'—'*18} {'—'*8} {'—'*20}")

    for i, run in enumerate(runs, 1):
        config_label = Path(run.config).stem if run.config else "—"
        if run.no_spec:
            spec_label = "no-spec"
        elif run.spec_file:
            spec_label = Path(run.spec_file).name
        else:
            spec_label = "index"

        print(f"  {i:<4} {run.protocol:<15} {run.model:<22} {config_label:<18} "
              f"{run.target_language:<8} {spec_label}")

    print(f"\n{'='*70}\n")


def run_evaluation(
    runs: list[RunSpec],
    eval_dir: Path,
    state: EvalState,
    debug: bool,
    parallelism: int,
    resume: bool,
    retry_failed: bool,
) -> None:
    """Execute all runs in the matrix, updating state as we go."""
    runs_dir = eval_dir / RUNS_DIRNAME
    runs_dir.mkdir(parents=True, exist_ok=True)

    # Filter runs based on resume/retry mode
    runs_to_execute: list[RunSpec] = []
    for run in runs:
        existing = state.get_run(run.run_id)

        if resume and existing.status == "completed":
            print(f"  ⏭  Skipping (completed): {run.run_id}")
            continue
        if retry_failed and existing.status != "failed":
            if existing.status == "completed":
                print(f"  ⏭  Skipping (completed): {run.run_id}")
            continue

        runs_to_execute.append(run)

    if not runs_to_execute:
        print("\n  ✅ No runs to execute — all matching runs are already completed.\n")
        return

    total = len(runs_to_execute)
    print(f"\n  Executing {total} run(s) with parallelism={parallelism}...\n")

    def _execute_single(idx: int, run: RunSpec) -> tuple[str, RunResult]:
        run_dir = runs_dir / run.run_id
        prefix = f"  [{idx}/{total}]"

        print(f"{prefix} ▶ Starting: {run.run_id}")

        result = RunResult(
            status="running",
            started_at=datetime.now(timezone.utc).isoformat(),
        )
        state.set_run(run.run_id, result)
        state.save(eval_dir / STATE_FILENAME)

        success, error = execute_run(run, run_dir, debug)

        result.finished_at = datetime.now(timezone.utc).isoformat()
        if error:
            result.status = "failed"
            result.success = False
            result.error = error
            print(f"{prefix} ❌ Failed:  {run.run_id}")
            print(f"           Error: {error[:200]}")
        else:
            result.status = "completed"
            result.success = success
            status_icon = "✅" if success else "⚠️"
            print(f"{prefix} {status_icon} Done:    {run.run_id} (success={success})")

        state.set_run(run.run_id, result)
        state.save(eval_dir / STATE_FILENAME)

        return run.run_id, result

    if parallelism <= 1:
        # Sequential execution
        for idx, run in enumerate(runs_to_execute, 1):
            _execute_single(idx, run)
    else:
        # Parallel execution
        with ThreadPoolExecutor(max_workers=parallelism) as executor:
            futures = {
                executor.submit(_execute_single, idx, run): run
                for idx, run in enumerate(runs_to_execute, 1)
            }
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    run = futures[future]
                    print(f"  ❌ Unexpected error in {run.run_id}: {e}")


def main() -> None:
    args = parse_args()

    # Load eval config
    config = load_eval_config(args.eval_config)
    eval_name = config.get("name", "eval")

    # Expand the run matrix
    runs = expand_matrix(config)

    if not runs:
        print("No runs to execute — the evaluation matrix is empty.")
        sys.exit(1)

    # Dry-run mode
    if args.dry_run:
        print_matrix(runs)
        sys.exit(0)

    # Determine eval directory
    if args.eval_dir:
        eval_dir = args.eval_dir
        if not eval_dir.exists():
            print(f"Error: eval directory does not exist: {eval_dir}")
            sys.exit(1)
    else:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        # Sanitize eval name for directory
        safe_name = eval_name.replace(" ", "-").replace("/", "-")
        eval_dir = args.output_dir / f"{timestamp}_{safe_name}"

    eval_dir.mkdir(parents=True, exist_ok=True)

    # Copy eval config for reproducibility (only on fresh runs)
    config_copy_path = eval_dir / EVAL_CONFIG_FILENAME
    if not config_copy_path.exists():
        shutil.copy2(args.eval_config, config_copy_path)

    # Load or initialize state
    state_path = eval_dir / STATE_FILENAME
    state = EvalState.load(state_path)

    # Initialize pending state for new runs
    for run in runs:
        if run.run_id not in state.runs:
            state.set_run(run.run_id, RunResult(status="pending"))
    state.save(state_path)

    # Write README
    write_readme(eval_dir, config, runs, args.debug)

    print(f"\n  📁 Eval directory: {eval_dir}")
    print(f"  📋 Total runs in matrix: {len(runs)}")

    # Execute
    run_evaluation(
        runs=runs,
        eval_dir=eval_dir,
        state=state,
        debug=args.debug,
        parallelism=args.parallelism,
        resume=args.resume,
        retry_failed=args.retry_failed,
    )

    # Aggregate results
    print("\n  📊 Aggregating results...")
    model_index = load_model_index()
    all_metrics = aggregate_report(eval_dir)
    write_report_jsonl(eval_dir, all_metrics)
    generate_summary(eval_dir, config, state, all_metrics, model_index)

    print(f"  📄 Report:  {eval_dir / REPORT_FILENAME}")
    print(f"  📝 Summary: {eval_dir / SUMMARY_FILENAME}")
    print(f"\n  ✅ Evaluation complete.\n")


if __name__ == "__main__":
    main()
