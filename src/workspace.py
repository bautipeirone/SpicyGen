"""Protocol workspace management.

A *workspace* is a self-contained directory that holds every artifact
produced while working on a specific protocol × model combination.

Typical layout::

    workspaces/
    └── dns/
        └── gemini-3.1-flash/
            └── 20260522_165500/
                ├── manifest.json
                ├── preprocess/
                │   ├── info.json
                │   ├── summary.txt
                │   ├── report.json
                │   └── extraction.log
                ├── grammar/
                │   ├── output<ext>
                │   ├── metrics.json
                │   ├── runtime.log
                │   └── debug/
                ├── zeek-package/    # Zeek plugin scaffold
                │   └── spicy-<protocol>/
                └── pipeline.log
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.logger import logger


# Fixed sub-directory names inside a workspace.
_PREPROCESS_DIR = "preprocess"
_GRAMMAR_DIR = "grammar"
_ZEEK_PACKAGE_DIR = "zeek-package"
_MANIFEST_FILENAME = "manifest.json"

# Step names recognised by the pipeline.
STEP_PREPROCESS = "preprocess"
STEP_GRAMMAR = "grammar"
STEP_ZEEK_PACKAGE = "zeek-package"
ALL_STEPS = (STEP_PREPROCESS, STEP_GRAMMAR, STEP_ZEEK_PACKAGE)


# ---------------------------------------------------------------------------
# Manifest helpers
# ---------------------------------------------------------------------------

def _empty_step_entry() -> dict[str, Any]:
    return {"status": "pending"}


def _initial_manifest(protocol: str, model: str, target_language: str, workspace_dir: Path) -> dict[str, Any]:
    return {
        "protocol": protocol,
        "model": model,
        "target_language": target_language,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "workspace_dir": str(workspace_dir),
        "steps": {step: _empty_step_entry() for step in ALL_STEPS},
    }


def load_manifest(workspace_dir: Path) -> dict[str, Any]:
    """Read the manifest from an existing workspace."""
    manifest_path = workspace_dir / _MANIFEST_FILENAME
    if not manifest_path.exists():
        raise FileNotFoundError(f"No manifest found at {manifest_path}")
    with manifest_path.open("r") as fh:
        return json.load(fh)


def save_manifest(workspace_dir: Path, manifest: dict[str, Any]) -> None:
    """Persist the manifest back to disk."""
    manifest_path = workspace_dir / _MANIFEST_FILENAME
    with manifest_path.open("w") as fh:
        json.dump(manifest, fh, indent=2)
        fh.write("\n")


def update_step(
    workspace_dir: Path,
    step_name: str,
    *,
    status: str,
    exit_code: int | None = None,
    started_at: str | None = None,
    finished_at: str | None = None,
    outputs: dict[str, str] | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    """Update a single step entry in the manifest and return the updated manifest."""
    manifest = load_manifest(workspace_dir)
    entry: dict[str, Any] = {"status": status}
    if started_at is not None:
        entry["started_at"] = started_at
    if finished_at is not None:
        entry["finished_at"] = finished_at
    if exit_code is not None:
        entry["exit_code"] = exit_code
    if outputs is not None:
        entry["outputs"] = outputs
    if error is not None:
        entry["error"] = error
    manifest["steps"][step_name] = entry
    save_manifest(workspace_dir, manifest)
    return manifest


# ---------------------------------------------------------------------------
# Workspace creation
# ---------------------------------------------------------------------------

def create_workspace(
    protocol: str,
    model: str,
    target_language: str = "spicy",
    base_dir: Path | None = None,
    timestamp: str | None = None,
) -> Path:
    """Create the workspace directory tree and write the initial manifest.

    Returns the *workspace_dir* path (the timestamped leaf directory).
    """
    base = base_dir or Path("workspaces")
    ts = timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")

    workspace_dir = base / protocol / model / ts
    workspace_dir.mkdir(parents=True, exist_ok=True)

    # Create sub-directories for each step.
    (workspace_dir / _PREPROCESS_DIR).mkdir(exist_ok=True)
    (workspace_dir / _GRAMMAR_DIR).mkdir(exist_ok=True)
    (workspace_dir / _ZEEK_PACKAGE_DIR).mkdir(exist_ok=True)

    manifest = _initial_manifest(protocol, model, target_language, workspace_dir)
    save_manifest(workspace_dir, manifest)

    logger.info(f"Workspace created: {workspace_dir}")
    return workspace_dir


# ---------------------------------------------------------------------------
# Path helpers — canonical output locations inside a workspace
# ---------------------------------------------------------------------------

def preprocess_paths(workspace_dir: Path) -> dict[str, Path]:
    """Return a dict of canonical output paths for the preprocess step."""
    d = workspace_dir / _PREPROCESS_DIR
    return {
        "info": d / "info.json",
        "summary": d / "summary.txt",
        "report": d / "report.json",
        "debug_dir": d,
    }


def grammar_paths(workspace_dir: Path, output_extension: str = ".spicy") -> dict[str, Path]:
    """Return a dict of canonical output paths for the grammar step."""
    d = workspace_dir / _GRAMMAR_DIR
    return {
        "output": d / f"output{output_extension}",
        "metrics": d / "metrics.json",
        "debug_dir": d / "debug",
    }


def zeek_package_paths(workspace_dir: Path) -> dict[str, Path]:
    """Return a dict of canonical output paths for the zeek-package step."""
    d = workspace_dir / _ZEEK_PACKAGE_DIR
    return {
        "output_dir": d,
        "metrics": d / "metrics.json",
        "debug_dir": d / "debug",
    }


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def find_workspaces(
    base_dir: Path | None = None,
    protocol: str | None = None,
    model: str | None = None,
    target_language: str | None = None,
) -> list[Path]:
    """Find existing workspace directories, optionally filtered by protocol/model.

    Returns a list of workspace root paths sorted by creation time (newest first).
    """
    base = base_dir or Path("workspaces")
    if not base.exists():
        return []

    pattern_parts: list[str] = []
    pattern_parts.append(protocol if protocol else "*")
    pattern_parts.append(model if model else "*")
    pattern_parts.append("*")  # timestamp
    pattern_parts.append(_MANIFEST_FILENAME)

    pattern = "/".join(pattern_parts)
    manifests = sorted(base.glob(pattern), reverse=True)

    if target_language is None:
        return [m.parent for m in manifests]

    filtered: list[Path] = []
    for manifest_path in manifests:
        try:
            with manifest_path.open("r") as fh:
                manifest = json.load(fh)
        except (OSError, json.JSONDecodeError):
            continue

        manifest_target = manifest.get("target_language", "spicy")
        if manifest_target == target_language:
            filtered.append(manifest_path.parent)

    return filtered
