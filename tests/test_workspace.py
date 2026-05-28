"""Integration tests for the workspace lifecycle.

Covers: create_workspace → manifest → update_step → find_workspaces.
"""

import json
from pathlib import Path

import pytest

from src.workspace import (
    ALL_STEPS,
    STEP_GRAMMAR,
    STEP_PREPROCESS,
    create_workspace,
    find_workspaces,
    grammar_paths,
    load_manifest,
    preprocess_paths,
    save_manifest,
    update_step,
)


class TestCreateWorkspace:
    """Verify workspace scaffolding produces the expected structure."""

    def test_creates_directory_tree(self, tmp_workspace):
        ws = create_workspace("dns", "gemini-3.1-flash", base_dir=tmp_workspace)

        assert ws.exists()
        assert (ws / "manifest.json").exists()
        assert (ws / "preprocess").is_dir()
        assert (ws / "grammar").is_dir()

    def test_manifest_content(self, tmp_workspace):
        ws = create_workspace(
            "dhcp", "test-model", target_language="spicy", base_dir=tmp_workspace
        )
        manifest = load_manifest(ws)

        assert manifest["protocol"] == "dhcp"
        assert manifest["model"] == "test-model"
        assert manifest["target_language"] == "spicy"
        assert "created_at" in manifest
        assert set(manifest["steps"].keys()) == set(ALL_STEPS)
        for step in ALL_STEPS:
            assert manifest["steps"][step]["status"] == "pending"

    def test_deterministic_timestamp(self, tmp_workspace):
        ws = create_workspace(
            "dns", "m", base_dir=tmp_workspace, timestamp="20260101_000000"
        )
        assert ws.name == "20260101_000000"

    def test_creates_nested_model_dirs(self, tmp_workspace):
        ws = create_workspace("tcp", "gemini-3.1-flash", base_dir=tmp_workspace)
        # Path should be: base/tcp/gemini-3.1-flash/<timestamp>/
        assert ws.parent.name == "gemini-3.1-flash"
        assert ws.parent.parent.name == "tcp"


class TestUpdateStep:
    """Verify step updates are persisted correctly in the manifest."""

    def test_update_preprocess_to_running(self, tmp_workspace):
        ws = create_workspace("dns", "m", base_dir=tmp_workspace)
        manifest = update_step(ws, STEP_PREPROCESS, status="running", started_at="2026-01-01T00:00:00Z")

        assert manifest["steps"][STEP_PREPROCESS]["status"] == "running"
        assert manifest["steps"][STEP_PREPROCESS]["started_at"] == "2026-01-01T00:00:00Z"

    def test_update_grammar_to_success(self, tmp_workspace):
        ws = create_workspace("dns", "m", base_dir=tmp_workspace)
        manifest = update_step(
            ws, STEP_GRAMMAR,
            status="success",
            exit_code=0,
            started_at="2026-01-01T00:00:00Z",
            finished_at="2026-01-01T00:01:00Z",
            outputs={"output": "/some/path.spicy"},
        )

        step = manifest["steps"][STEP_GRAMMAR]
        assert step["status"] == "success"
        assert step["exit_code"] == 0
        assert step["outputs"]["output"] == "/some/path.spicy"

    def test_update_with_error(self, tmp_workspace):
        ws = create_workspace("dns", "m", base_dir=tmp_workspace)
        manifest = update_step(
            ws, STEP_PREPROCESS,
            status="failed",
            exit_code=1,
            error="Something went wrong",
        )
        assert manifest["steps"][STEP_PREPROCESS]["error"] == "Something went wrong"

    def test_update_persists_to_disk(self, tmp_workspace):
        ws = create_workspace("dns", "m", base_dir=tmp_workspace)
        update_step(ws, STEP_GRAMMAR, status="success", exit_code=0)

        # Re-load from disk
        reloaded = load_manifest(ws)
        assert reloaded["steps"][STEP_GRAMMAR]["status"] == "success"


class TestFindWorkspaces:
    """Verify workspace discovery with various filters."""

    def test_find_all(self, tmp_workspace):
        create_workspace("dns", "m1", base_dir=tmp_workspace, timestamp="20260101_000001")
        create_workspace("tcp", "m2", base_dir=tmp_workspace, timestamp="20260101_000002")

        found = find_workspaces(base_dir=tmp_workspace)
        assert len(found) == 2

    def test_filter_by_protocol(self, tmp_workspace):
        create_workspace("dns", "m1", base_dir=tmp_workspace, timestamp="20260101_000001")
        create_workspace("tcp", "m1", base_dir=tmp_workspace, timestamp="20260101_000002")

        found = find_workspaces(base_dir=tmp_workspace, protocol="dns")
        assert len(found) == 1
        manifest = load_manifest(found[0])
        assert manifest["protocol"] == "dns"

    def test_filter_by_model(self, tmp_workspace):
        create_workspace("dns", "model-a", base_dir=tmp_workspace, timestamp="20260101_000001")
        create_workspace("dns", "model-b", base_dir=tmp_workspace, timestamp="20260101_000002")

        found = find_workspaces(base_dir=tmp_workspace, model="model-a")
        assert len(found) == 1

    def test_filter_by_target_language(self, tmp_workspace):
        create_workspace("dns", "m", target_language="spicy", base_dir=tmp_workspace, timestamp="20260101_000001")
        create_workspace("dns", "m", target_language="3d", base_dir=tmp_workspace, timestamp="20260101_000002")

        found = find_workspaces(base_dir=tmp_workspace, target_language="spicy")
        assert len(found) == 1
        manifest = load_manifest(found[0])
        assert manifest["target_language"] == "spicy"

    def test_empty_base_returns_empty_list(self, tmp_workspace):
        empty_dir = tmp_workspace / "empty"
        empty_dir.mkdir()
        assert find_workspaces(base_dir=empty_dir) == []

    def test_nonexistent_base_returns_empty_list(self, tmp_workspace):
        assert find_workspaces(base_dir=tmp_workspace / "nope") == []

    def test_sorted_newest_first(self, tmp_workspace):
        create_workspace("dns", "m", base_dir=tmp_workspace, timestamp="20260101_000001")
        create_workspace("dns", "m", base_dir=tmp_workspace, timestamp="20260201_000001")

        found = find_workspaces(base_dir=tmp_workspace)
        assert found[0].name > found[1].name  # Newer timestamp comes first


class TestPathHelpers:
    """Verify canonical path helpers produce the expected structure."""

    def test_preprocess_paths(self, tmp_workspace):
        ws = create_workspace("dns", "m", base_dir=tmp_workspace)
        paths = preprocess_paths(ws)

        assert paths["info"].name == "info.json"
        assert paths["summary"].name == "summary.txt"
        assert paths["report"].name == "report.json"
        assert paths["debug_dir"].name == "preprocess"

    def test_grammar_paths_default_extension(self, tmp_workspace):
        ws = create_workspace("dns", "m", base_dir=tmp_workspace)
        paths = grammar_paths(ws)

        assert paths["output"].name == "output.spicy"
        assert paths["metrics"].name == "metrics.json"

    def test_grammar_paths_custom_extension(self, tmp_workspace):
        ws = create_workspace("dns", "m", base_dir=tmp_workspace)
        paths = grammar_paths(ws, output_extension=".3d")

        assert paths["output"].name == "output.3d"
