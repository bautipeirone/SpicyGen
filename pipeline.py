#!/usr/bin/env python3
"""Pipeline orchestrator for protocol grammar extraction.

Creates an isolated workspace and runs ``preprocess_spec.py``,
``generate_grammar.py``, and optionally ``create_zeek_package.py`` as
subprocesses, routing every artifact into the workspace directory tree.

Usage examples::

    # Full pipeline (preprocess → grammar → zeek-package)
    python pipeline.py run dns -m gemini-3.1-flash

    # Scaffold the workspace without running anything
    python pipeline.py init dns -m gemini-3.1-flash

    # Run a single step
    python pipeline.py run dns -m gemini-3.1-flash --only preprocess
    python pipeline.py run dns -m gemini-3.1-flash --only grammar
    python pipeline.py run dns -m gemini-3.1-flash --only zeek-package

    # Re-run only failed/pending steps
    python pipeline.py run dns -m gemini-3.1-flash --resume

    # Pass extra flags to individual steps
    python pipeline.py run dns -m gemini-3.1-flash \\
        --grammar-args "--no-spec --instructions-file prompts/custom.md" \\
        --zeek-args "--traces-dir data/dns/traces --skip-installation"

    # Show workspace statuses
    python pipeline.py status
    python pipeline.py status --protocol dns
"""

from __future__ import annotations

import argparse
import shlex
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from src.logger import logger, enable_runtime_log_file
from src.target_language import (
    DEFAULT_TARGET_LANGUAGE,
    get_target_language,
    list_supported_target_languages,
)
from src.workspace import (
    ALL_STEPS,
    STEP_GRAMMAR,
    STEP_PREPROCESS,
    STEP_ZEEK_PACKAGE,
    create_workspace,
    find_workspaces,
    grammar_paths,
    load_manifest,
    preprocess_paths,
    zeek_package_paths,
    update_step,
)


# ---------------------------------------------------------------------------
# Step runners
# ---------------------------------------------------------------------------

def _run_preprocess(
    workspace_dir: Path,
    protocol: str,
    model: str,
    extra_args: list[str] | None = None,
) -> int:
    """Run ``preprocess_spec.py`` and route outputs into the workspace."""
    paths = preprocess_paths(workspace_dir)

    cmd = [
        sys.executable, "preprocess_spec.py",
        protocol,
        "-m", model,
        "--info-output",    str(paths["info"]),
        "--summary-output", str(paths["summary"]),
        "--negative-output", str(paths["negative_packets"]),
        "--report-file",    str(paths["report"]),
        "--debug-dir",      str(paths["debug_dir"]),
    ]
    if extra_args:
        cmd.extend(extra_args)

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
    target_language: str,
    extra_args: list[str] | None = None,
) -> int:
    """Run ``generate_grammar.py`` and route outputs into the workspace.

    The runner auto-detects the preprocessed summary (``summary.txt``) from
    the workspace and passes it via ``--spec-file`` unless the caller
    already included ``--spec-file`` or ``--no-spec`` in *extra_args*.
    """
    language = get_target_language(target_language)
    paths = grammar_paths(workspace_dir, output_extension=language.source_extension)
    paths["debug_dir"].mkdir(parents=True, exist_ok=True)

    # Auto-detect the preprocessed summary, but only when the caller hasn't
    # explicitly provided --spec-file or --no-spec in extra_args.
    spec_override = False
    if extra_args:
        spec_override = any(tok in extra_args for tok in ("--spec-file", "--no-spec"))

    cmd = [
        sys.executable, "generate_grammar.py",
        protocol,
        "-m", model,
        "--target-language", target_language,
        "--output-file",  str(paths["output"]),
        "--metrics-file", str(paths["metrics"]),
        "--debug-dir",    str(paths["debug_dir"]),
    ]

    if not spec_override:
        candidate = preprocess_paths(workspace_dir)["summary"]
        if candidate.exists():
            cmd.extend(["--spec-file", str(candidate)])
            logger.info(f"[grammar] Using preprocessed summary: {candidate}")

    if extra_args:
        cmd.extend(extra_args)

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


def _run_zeek_package(
    workspace_dir: Path,
    protocol: str,
    model: str,
    extra_args: list[str] | None = None,
) -> int:
    """Run ``create_zeek_package.py`` and route outputs into the workspace.

    Auto-detects the grammar output (.spicy) and preprocessed metadata
    (info.json) from the workspace.  Both must exist or the step is skipped
    with an error.  Any additional flags (``--traces-dir``,
    ``--skip-installation``, etc.) should be provided via *extra_args*.
    """
    paths = zeek_package_paths(workspace_dir)
    g_paths = grammar_paths(workspace_dir)
    p_paths = preprocess_paths(workspace_dir)
    paths["debug_dir"].mkdir(parents=True, exist_ok=True)

    spicy_file = g_paths["output"]
    metadata_file = p_paths["info"]

    if not spicy_file.exists():
        logger.error(
            f"[zeek-package] Grammar output not found at {spicy_file}. "
            "Run the grammar step first."
        )
        update_step(
            workspace_dir, STEP_ZEEK_PACKAGE,
            status="failed",
            error=f"Grammar output not found: {spicy_file}",
        )
        return 1

    if not metadata_file.exists():
        logger.error(
            f"[zeek-package] Protocol metadata not found at {metadata_file}. "
            "Run the preprocess step first (with info extraction enabled)."
        )
        update_step(
            workspace_dir, STEP_ZEEK_PACKAGE,
            status="failed",
            error=f"Metadata file not found: {metadata_file}",
        )
        return 1

    cmd = [
        sys.executable, "create_zeek_package.py",
        str(spicy_file),
        "--metadata",    str(metadata_file),
        "--output-dir",  str(paths["output_dir"]),
        "--metrics-file", str(paths["metrics"]),
        "--debug-dir",    str(paths["debug_dir"]),
        "-m",            model,
    ]
    if extra_args:
        cmd.extend(extra_args)

    logger.info(f"[zeek-package] Running: {' '.join(cmd)}")
    started_at = datetime.now(timezone.utc).isoformat()
    update_step(workspace_dir, STEP_ZEEK_PACKAGE, status="running", started_at=started_at)

    result = subprocess.run(cmd, cwd=str(Path(__file__).resolve().parent))
    finished_at = datetime.now(timezone.utc).isoformat()

    if result.returncode == 0:
        # Discover the created package directory (create_zeek_package names it spicy-<analyzer>).
        created_packages = [
            d for d in paths["output_dir"].iterdir()
            if d.is_dir() and d.name != ".gitkeep"
        ]
        outputs = {
            "output_dir": str(paths["output_dir"]),
        }
        if paths["metrics"].exists():
            outputs["metrics"] = str(paths["metrics"])
        if created_packages:
            outputs["package_dir"] = str(created_packages[0])

        update_step(
            workspace_dir, STEP_ZEEK_PACKAGE,
            status="success",
            exit_code=result.returncode,
            started_at=started_at,
            finished_at=finished_at,
            outputs=outputs,
        )
        logger.info("[zeek-package] Completed successfully.")
    else:
        update_step(
            workspace_dir, STEP_ZEEK_PACKAGE,
            status="failed",
            exit_code=result.returncode,
            started_at=started_at,
            finished_at=finished_at,
            error=f"Process exited with code {result.returncode}",
        )
        logger.error(f"[zeek-package] Failed with exit code {result.returncode}.")

    return result.returncode


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------

def cmd_init(args: argparse.Namespace) -> int:
    """``pipeline.py init`` — scaffold a workspace without running anything."""
    workspace_dir = create_workspace(
        args.protocol,
        args.model,
        target_language=args.target_language,
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
            target_language=args.target_language,
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
            target_language=args.target_language,
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

    # Parse passthrough args for each step.
    preprocess_extra = shlex.split(args.preprocess_args) if args.preprocess_args else None
    grammar_extra = shlex.split(args.grammar_args) if args.grammar_args else None
    zeek_extra = shlex.split(args.zeek_args) if args.zeek_args else None

    # ---- Preprocess ----
    if STEP_PREPROCESS in steps_to_run:
        rc = _run_preprocess(workspace_dir, args.protocol, args.model, extra_args=preprocess_extra)
        if rc != 0:
            exit_code = rc
            if not args.keep_going:
                logger.error("Preprocess failed. Stopping pipeline (use --keep-going to continue).")
                return exit_code

    # ---- Grammar generation ----
    if STEP_GRAMMAR in steps_to_run:
        rc = _run_grammar(
            workspace_dir,
            args.protocol,
            args.model,
            args.target_language,
            extra_args=grammar_extra,
        )
        if rc != 0:
            exit_code = rc
            if not args.keep_going:
                logger.error("Grammar generation failed. Stopping pipeline (use --keep-going to continue).")
                return exit_code

    # ---- Zeek package ----
    if STEP_ZEEK_PACKAGE in steps_to_run:
        rc = _run_zeek_package(
            workspace_dir,
            args.protocol,
            args.model,
            extra_args=zeek_extra,
        )
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
        target_language=getattr(args, "target_language", None),
    )

    if not workspaces:
        print("No workspaces found.")
        return 0

    # Table header
    print(f"{'Protocol':<12} {'Model':<22} {'Language':<12} {'Timestamp':<18} {'Preprocess':<14} {'Grammar':<14} {'Zeek Pkg':<14}")
    print("-" * 110)

    for ws in workspaces:
        try:
            m = load_manifest(ws)
        except Exception:
            continue

        proto = m.get("protocol", "?")
        model = m.get("model", "?")
        target_language = m.get("target_language", DEFAULT_TARGET_LANGUAGE)
        ts = ws.name  # timestamp directory name
        pre_status = m["steps"].get(STEP_PREPROCESS, {}).get("status", "—")
        gram_status = m["steps"].get(STEP_GRAMMAR, {}).get("status", "—")
        zeek_status = m["steps"].get(STEP_ZEEK_PACKAGE, {}).get("status", "—")

        # Colorise statuses for terminal readability
        pre_display = _colorise_status(pre_status)
        gram_display = _colorise_status(gram_status)
        zeek_display = _colorise_status(zeek_status)

        print(f"{proto:<12} {model:<22} {target_language:<12} {ts:<18} {pre_display:<23} {gram_display:<23} {zeek_display:<23}")

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
    init_parser.add_argument(
        "-t", "--target-language",
        choices=list_supported_target_languages(),
        default=DEFAULT_TARGET_LANGUAGE,
        help="Target language/toolchain to use for this workspace.",
    )

    # ---- run ----
    run_parser = subparsers.add_parser("run", help="Execute the pipeline.")
    run_parser.add_argument("protocol", type=str, help="Protocol name (e.g. 'dns').")
    run_parser.add_argument("-m", "--model", required=True, help="Model key from model_index.json.")
    run_parser.add_argument(
        "-t", "--target-language",
        choices=list_supported_target_languages(),
        default=DEFAULT_TARGET_LANGUAGE,
        help="Target language/toolchain to use for grammar generation.",
    )
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
    # ---- Step-specific passthrough arguments ----
    run_parser.add_argument(
        "--preprocess-args",
        type=str,
        default=None,
        help=(
            "Extra arguments forwarded verbatim to preprocess_spec.py. "
            'Example: --preprocess-args "--no-info"'
        ),
    )
    run_parser.add_argument(
        "--grammar-args",
        type=str,
        default=None,
        help=(
            "Extra arguments forwarded verbatim to generate_grammar.py. "
            'Example: --grammar-args "--no-spec --config configs/default.yaml"'
        ),
    )
    run_parser.add_argument(
        "--zeek-args",
        type=str,
        default=None,
        help=(
            "Extra arguments forwarded verbatim to create_zeek_package.py. "
            'Example: --zeek-args "--traces-dir data/dns/traces --skip-installation"'
        ),
    )

    # ---- status ----
    status_parser = subparsers.add_parser("status", help="Show workspace statuses.")
    status_parser.add_argument("--protocol", type=str, default=None, help="Filter by protocol.")
    status_parser.add_argument("--model", "-m", type=str, default=None, help="Filter by model.")
    status_parser.add_argument(
        "--target-language", "-t",
        choices=list_supported_target_languages(),
        default=None,
        help="Filter by target language.",
    )

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
