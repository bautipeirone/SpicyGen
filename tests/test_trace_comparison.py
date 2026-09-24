"""Checks that trace comparison pairs payloads by capture frame, not list position."""

import hashlib
import importlib.util
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "analysis" / "trace-eval" / "compare_traces.py"
spec = importlib.util.spec_from_file_location("compare_traces", MODULE_PATH)
compare_traces = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compare_traces)


def test_manifest_detects_stale_payload(tmp_path):
    capture = tmp_path / "trace.pcap"
    capture.write_bytes(b"pcap")
    packet = tmp_path / "packet.bin"
    packet.write_bytes(b"changed")
    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text(
        '{"packet_path": "%s", "capture_path": "%s", "frame_number": 7, "sha256": "%s"}\n'
        % (packet, capture, hashlib.sha256(b"original").hexdigest())
    )

    with pytest.raises(compare_traces.ComparisonError, match="hash differs"):
        compare_traces.load_manifest(manifest, [capture])


def test_compare_keeps_unpaired_frames_and_soft_rejects_separate(tmp_path):
    capture = tmp_path / "trace.pcap"
    capture.write_bytes(b"pcap")
    capture_name = str(capture)
    first = str(tmp_path / "first.bin")
    second = str(tmp_path / "second.bin")
    records = [
        {"capture_path": capture_name, "frame_number": 10, "packet_path": first, "sha256": "a"},
        {"capture_path": capture_name, "frame_number": 20, "packet_path": second, "sha256": "b"},
    ]
    oracle = {capture_name: {"packets": [
        {"frame_number": 10, "verdict": "ACCEPT", "rejection_reasons": []},
        {"frame_number": 30, "verdict": "SOFT_REJECT", "rejection_reasons": ["checksum"]},
    ]}}
    targets = {
        "strict": {first: {"success": False, "error": "parse error"},
                   second: {"success": True, "error": ""}},
        "permissive": {first: {"success": True, "error": ""},
                       second: {"success": True, "error": ""}},
    }

    rows, summary = compare_traces.compare([capture], records, oracle, targets)

    assert [row["frame_number"] for row in rows] == [10, 20, 30]
    assert summary["totals"]["matched"] == 1
    assert summary["totals"]["packet_only"] == 1
    assert summary["totals"]["oracle_only"] == 1
    assert summary["totals"]["target_disagreements"] == 1
    assert summary["targets"]["strict"]["oracle_accept_target_reject"] == 1
    assert summary["per_capture"][capture_name]["strict_reject"] == 1
    assert summary["target_pairs"]["strict vs permissive"]["reject_accept"] == 1
    assert rows[2]["oracle_verdict"] == "SOFT_REJECT"
    assert rows[2]["strict"] == "NOT_RUN"
