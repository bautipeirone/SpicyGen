#!/usr/bin/env python3
"""Pipeline orchestrator for protocol grammar extraction.

Creates an isolated workspace and runs ``preprocess_spec.py`` and
``generate_grammar.py`` as subprocesses, routing every artifact into the
workspace directory tree.

Usage examples::

    # Full pipeline (preprocess → grammar)
    python pipeline.py run dns -m gemini-3.1-flash

    # Scaffold the workspace without running anything
    python pipeline.py init dns -m gemini-3.1-flash

    # Run a single step
    python pipeline.py run dns -m gemini-3.1-flash --only preprocess
    python pipeline.py run dns -m gemini-3.1-flash --only grammar

    # Re-run only failed/pending steps
    python pipeline.py run dns -m gemini-3.1-flash --resume

    # Show workspace statuses
    python pipeline.py status
    python pipeline.py status --protocol dns
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from src.logger import logger, enable_runtime_log_file
from src.workspace import (
    ALL_STEPS,
    STEP_GRAMMAR,
    STEP_PREPROCESS,
    create_workspace,
    find_workspaces,
    grammar_paths,
    load_manifest,
    preprocess_paths,
    update_step,
)


# ---------------------------------------------------------------------------
# Step runners
# ---------------------------------------------------------------------------

def _run_preprocess(
    workspace_dir: Path,
    protocol: str,
    model: str,
) -> int:
    """Run ``preprocess_spec.py`` and route outputs into the workspace."""
    paths = preprocess_paths(workspace_dir)

    cmd = [
        sys.executable, "preprocess_spec.py",
        protocol,
        "-m", model,
        "--info-output",    str(paths["info"]),
        "--summary-output", str(paths["summary"]),
        "--report-file",    str(paths["report"]),
        "--debug-dir",      str(paths["debug_dir"]),
    ]

    logger.info(f"[preprocess] Running: {' '.join(cmd)}")
    started_at = datetime.now(timezone.utc).isoformat()
    update_step(workspace_dir, STEP_PREPROCESS, status="running", started_at=started_at)

    result = subprocess.run(cmd, cwd=str(Path(__file__).resolve().parent))
    finished_at = datetime.now(timezone.utc).isoformat()

    if result.returncode == 0:
        outputs = {k: str(v) for k, v in paths.items() if k != "debug_dir" and v.exists()}
        update_step(
            workspace_dir, STEP_PREPROCESS,
            status="success",
            exit_code=result.returncode,
            started_at=started_at,
            finished_at=finished_at,
            outputs=outputs,
        )
        logger.info("[preprocess] Completed successfully.")
    else:
        update_step(
            workspace_dir, STEP_PREPROCESS,
            status="failed",
            exit_code=result.returncode,
            started_at=started_at,
            finished_at=finished_at,
            error=f"Process exited with code {result.returncode}",
        )
        logger.error(f"[preprocess] Failed with exit code {result.returncode}.")

    return result.returncode


def _run_grammar(
    workspace_dir: Path,
    protocol: str,
    model: str,
    config: Path | None = None,
) -> int:
    """Run ``generate_grammar.py`` and route outputs into the workspace."""
    paths = grammar_paths(workspace_dir)
    paths["debug_dir"].mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable, "generate_grammar.py",
        protocol,
        "-m", model,
        "--output-file",  str(paths["output"]),
        "--metrics-file", str(paths["metrics"]),
        "--debug-dir",    str(paths["debug_dir"]),
    ]

    if config is not None:
        cmd.extend(["--config", str(config)])

    logger.info(f"[grammar] Running: {' '.join(cmd)}")
    started_at = datetime.now(timezone.utc).isoformat()
    update_step(workspace_dir, STEP_GRAMMAR, status="running", started_at=started_at)

    result = subprocess.run(cmd, cwd=str(Path(__file__).resolve().parent))
    finished_at = datetime.now(timezone.utc).isoformat()

    if result.returncode == 0:
        outputs = {k: str(v) for k, v in paths.items() if k != "debug_dir" and v.exists()}
        update_step(
            workspace_dir, STEP_GRAMMAR,
            status="success",
            exit_code=result.returncode,
            started_at=started_at,
            finished_at=finished_at,
            outputs=outputs,
        )
        logger.info("[grammar] Completed successfully.")
    else:
        update_step(
            workspace_dir, STEP_GRAMMAR,
            status="failed",
            exit_code=result.returncode,
            started_at=started_at,
            finished_at=finished_at,
            error=f"Process exited with code {result.returncode}",
        )
        logger.error(f"[grammar] Failed with exit code {result.returncode}.")

    return result.returncode


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------

def cmd_init(args: argparse.Namespace) -> int:
    """``pipeline.py init`` — scaffold a workspace without running anything."""
    workspace_dir = create_workspace(
        args.protocol,
        args.model,
        base_dir=args.workspace_root,
    )
    print(f"Workspace initialised at: {workspace_dir}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    """``pipeline.py run`` — execute the pipeline (or a subset of steps)."""
    # Determine which steps to run.
    steps_to_run: list[str] = list(ALL_STEPS)
    if args.only:
        if args.only not in ALL_STEPS:
            logger.error(f"Unknown step '{args.only}'. Valid steps: {', '.join(ALL_STEPS)}")
            return 1
        steps_to_run = [args.only]

    # Create (or reuse) the workspace.
    if args.resume:
        # In resume mode, find the latest existing workspace for this protocol+model.
        existing = find_workspaces(
            base_dir=args.workspace_root,
            protocol=args.protocol,
            model=args.model,
        )
        if not existing:
            logger.error("No existing workspace found to resume. Run without --resume first.")
            return 1
        workspace_dir = existing[0]
        logger.info(f"Resuming workspace: {workspace_dir}")
    else:
        workspace_dir = create_workspace(
            args.protocol,
            args.model,
            base_dir=args.workspace_root,
        )

    # Enable pipeline-level log file.
    enable_runtime_log_file(workspace_dir / "pipeline.log")

    manifest = load_manifest(workspace_dir)

    # In resume mode, skip steps that already succeeded.
    if args.resume:
        steps_to_run = [
            s for s in steps_to_run
            if manifest["steps"].get(s, {}).get("status") != "success"
        ]
        if not steps_to_run:
            logger.info("All steps already completed. Nothing to resume.")
            print("All steps already completed.")
            return 0
        logger.info(f"Resuming steps: {', '.join(steps_to_run)}")

    logger.info(f"Pipeline started for '{args.protocol}' with model '{args.model}'")
    logger.info(f"Workspace: {workspace_dir}")
    logger.info(f"Steps: {', '.join(steps_to_run)}")

    overall_start = time.time()
    exit_code = 0

    # ---- Preprocess ----
    if STEP_PREPROCESS in steps_to_run:
        rc = _run_preprocess(workspace_dir, args.protocol, args.model)
        if rc != 0:
            exit_code = rc
            if not args.keep_going:
                logger.error("Preprocess failed. Stopping pipeline (use --keep-going to continue).")
                return exit_code

    # ---- Grammar generation ----
    if STEP_GRAMMAR in steps_to_run:
        rc = _run_grammar(workspace_dir, args.protocol, args.model, config=args.config)
        if rc != 0:
            exit_code = rc

    elapsed = round(time.time() - overall_start, 2)
    logger.info(f"Pipeline finished in {elapsed}s (exit code {exit_code}).")
    print(f"\nWorkspace: {workspace_dir}")
    return exit_code


def cmd_status(args: argparse.Namespace) -> int:
    """``pipeline.py status`` — show workspace statuses."""
    workspaces = find_workspaces(
        base_dir=args.workspace_root,
        protocol=getattr(args, "protocol", None),
        model=getattr(args, "model", None),
    )

    if not workspaces:
        print("No workspaces found.")
        return 0

    # Table header
    print(f"{'Protocol':<12} {'Model':<22} {'Timestamp':<18} {'Preprocess':<14} {'Grammar':<14}")
    print("-" * 82)

    for ws in workspaces:
        try:
            m = load_manifest(ws)
        except Exception:
            continue

        proto = m.get("protocol", "?")
        model = m.get("model", "?")
        ts = ws.name  # timestamp directory name
        pre_status = m["steps"].get(STEP_PREPROCESS, {}).get("status", "—")
        gram_status = m["steps"].get(STEP_GRAMMAR, {}).get("status", "—")

        # Colorise statuses for terminal readability
        pre_display = _colorise_status(pre_status)
        gram_display = _colorise_status(gram_status)

        print(f"{proto:<12} {model:<22} {ts:<18} {pre_display:<23} {gram_display:<23}")

    return 0


def _colorise_status(status: str) -> str:
    """Apply ANSI colours to a status string."""
    colours = {
        "success": "\033[32m",   # green
        "failed":  "\033[31m",   # red
        "running": "\033[33m",   # yellow
        "pending": "\033[90m",   # grey
    }
    reset = "\033[0m"
    colour = colours.get(status, "")
    return f"{colour}{status}{reset}"


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Pipeline orchestrator for protocol grammar extraction.",
    )
    parser.add_argument(
        "--workspace-root",
        type=Path,
        default=None,
        help="Base directory for workspaces (default: ./workspaces).",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # ---- init ----
    init_parser = subparsers.add_parser("init", help="Scaffold a workspace without running anything.")
    init_parser.add_argument("protocol", type=str, help="Protocol name (e.g. 'dns').")
    init_parser.add_argument("-m", "--model", required=True, help="Model key from model_index.json.")

    # ---- run ----
    run_parser = subparsers.add_parser("run", help="Execute the pipeline.")
    run_parser.add_argument("protocol", type=str, help="Protocol name (e.g. 'dns').")
    run_parser.add_argument("-m", "--model", required=True, help="Model key from model_index.json.")
    run_parser.add_argument(
        "--only",
        choices=list(ALL_STEPS),
        default=None,
        help="Run only this step.",
    )
    run_parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume the latest workspace, skipping completed steps.",
    )
    run_parser.add_argument(
        "--keep-going",
        action="store_true",
        help="Continue to the next step even if the current one fails.",
    )
    run_parser.add_argument(
        "--config", "-c",
        type=Path,
        default=None,
        help="Config file for the grammar generation step.",
    )

    # ---- status ----
    status_parser = subparsers.add_parser("status", help="Show workspace statuses.")
    status_parser.add_argument("--protocol", type=str, default=None, help="Filter by protocol.")
    status_parser.add_argument("--model", "-m", type=str, default=None, help="Filter by model.")

    return parser


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    handlers = {
        "init": cmd_init,
        "run": cmd_run,
        "status": cmd_status,
    }

    return handlers[args.command](args)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        logger.info("Pipeline interrupted by user.")
        sys.exit(130)
    except Exception as exc:
        logger.error(f"Pipeline failed: {exc}")
        sys.exit(1)
