#!/usr/bin/env python3
"""Compare packet parser results with a configurable Wireshark oracle.

Extract packets first with scripts/extract_layer.py. Its manifest.jsonl maps each
.bin file to the original capture frame and unit index. This script uses only
those files.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
from itertools import combinations
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ORACLE_SCRIPT = PROJECT_ROOT / "scripts" / "wireshark_oracle.py"
TRACE_SCRIPT = PROJECT_ROOT / "scripts" / "run_parser_trace.py"


class ComparisonError(RuntimeError):
    pass


def capture_files(path: Path) -> list[Path]:
    if path.is_file():
        return [Path(os.path.abspath(path))]
    if not path.is_dir():
        raise ComparisonError(f"Capture path does not exist: {path}")
    files = [Path(os.path.abspath(item)) for item in sorted(path.iterdir()) if item.is_file()]
    if not files:
        raise ComparisonError(f"No capture files found in {path}")
    return files


def target_specs(values: list[str]) -> dict[str, Path]:
    targets = {}
    for value in values:
        name, separator, source = value.partition("=")
        if not separator or not re.fullmatch(r"[A-Za-z0-9_.-]+", name) or not source:
            raise ComparisonError(f"Invalid target {value!r}; use NAME=PATH")
        if name in targets:
            raise ComparisonError(f"Duplicate target name: {name}")
        path = Path(os.path.abspath(source))
        if not path.is_file():
            raise ComparisonError(f"Target parser does not exist: {path}")
        targets[name] = path
    return targets


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise ComparisonError(f"Missing JSONL file: {path}")
    try:
        with path.open(encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]
    except (OSError, json.JSONDecodeError) as exc:
        raise ComparisonError(f"Could not read {path}: {exc}") from exc


def load_manifest(path: Path, captures: list[Path]) -> list[dict[str, Any]]:
    records = read_jsonl(path)
    if not records:
        raise ComparisonError(f"Extraction manifest is empty: {path}")
    allowed_captures = {str(capture) for capture in captures}
    paths = set()
    frame_units = defaultdict(set)
    frame_counts = {}
    for record in records:
        try:
            packet = Path(record["packet_path"]).absolute()
            capture = str(Path(record["capture_path"]).absolute())
            frame = int(record["frame_number"])
            unit_index = int(record.get("unit_index", 0))
            unit_count = int(record.get("unit_count", 1))
            digest = record["sha256"]
        except (KeyError, TypeError, ValueError) as exc:
            raise ComparisonError(f"Invalid manifest record: {record}") from exc
        if capture not in allowed_captures:
            raise ComparisonError(f"Manifest capture is outside the requested input: {capture}")
        if frame < 1 or unit_index < 0 or unit_count < 1 or unit_index >= unit_count or not packet.is_file():
            raise ComparisonError(f"Missing packet or invalid frame in manifest: {record}")
        frame_key = (capture, frame)
        if unit_index in frame_units[frame_key]:
            raise ComparisonError(f"Duplicate unit {unit_index} for {capture} frame {frame}")
        if frame_key in frame_counts and frame_counts[frame_key] != unit_count:
            raise ComparisonError(f"Inconsistent unit count for {capture} frame {frame}")
        if str(packet) in paths:
            raise ComparisonError(f"Duplicate packet path in manifest: {packet}")
        if hashlib.sha256(packet.read_bytes()).hexdigest() != digest:
            raise ComparisonError(f"Payload hash differs from manifest: {packet}")
        record["packet_path"] = str(packet)
        record["capture_path"] = capture
        record["frame_number"] = frame
        record["unit_index"] = unit_index
        record["unit_count"] = unit_count
        paths.add(str(packet))
        frame_units[frame_key].add(unit_index)
        frame_counts[frame_key] = unit_count
    for (capture, frame), indices in frame_units.items():
        if indices != set(range(frame_counts[capture, frame])):
            raise ComparisonError(f"Missing unit for {capture} frame {frame}")
    return records


def run_command(command: list[str], *, allowed_codes: set[int] = {0}) -> None:
    completed = subprocess.run(command, cwd=PROJECT_ROOT, text=True, capture_output=True)
    if completed.returncode not in allowed_codes:
        raise ComparisonError(
            f"Command failed ({completed.returncode}): {' '.join(command)}\n"
            f"{completed.stderr.strip() or completed.stdout.strip()}"
        )
    if completed.stdout.strip():
        print(completed.stdout.strip())


def run_oracle(
    captures: list[Path], protocol: str, args: argparse.Namespace, output_dir: Path
) -> dict[str, dict[str, Any]]:
    reports = {}
    oracle_dir = args.oracle_report_dir or output_dir / "oracle"
    if args.oracle_report_dir is None:
        oracle_dir.mkdir(parents=True, exist_ok=True)
    for capture in captures:
        report_path = oracle_dir / f"{capture.name}.json"
        if args.oracle_report_dir is None:
            command = [
                sys.executable, str(ORACLE_SCRIPT), str(capture), protocol,
                "-o", str(report_path), "--tshark", args.tshark,
            ]
            if args.oracle_filter:
                command += ["--display-filter", args.oracle_filter]
            if args.oracle_profile_config:
                command += ["--profile-config", str(args.oracle_profile_config)]
            for association in args.oracle_decode_as:
                command += ["--decode-as", association]
            for reject_filter in args.oracle_soft_reject_filter:
                command += ["--soft-reject-filter", reject_filter]
            for report_field in args.oracle_field:
                command += ["--field", report_field]
            # 1 means at least one rejection; 2 means no recognized frame.
            run_command(command, allowed_codes={0, 1, 2})
        if not report_path.is_file():
            raise ComparisonError(f"Oracle report is missing: {report_path}")
        report = json.loads(report_path.read_text(encoding="utf-8"))
        reported_capture = Path(report["metadata"]["pcap"]).resolve()
        if reported_capture != capture.resolve():
            raise ComparisonError(f"Oracle report is for a different capture: {report_path}")
        metadata = report["metadata"]
        if metadata["profile"].lower() != protocol.lower():
            raise ComparisonError(f"Oracle profile differs in reused report: {report_path}")
        if args.oracle_filter and metadata["display_filter"] != args.oracle_filter:
            raise ComparisonError(f"Oracle filter differs in reused report: {report_path}")
        if any(item not in metadata["decode_as"] for item in args.oracle_decode_as):
            raise ComparisonError(f"Oracle Decode-As settings differ in reused report: {report_path}")
        if any(item not in metadata["soft_reject_filters"] for item in args.oracle_soft_reject_filter):
            raise ComparisonError(f"Oracle soft-reject settings differ in reused report: {report_path}")
        reports[str(capture)] = report
    return reports


def run_targets(
    targets: dict[str, Path], records: list[dict[str, Any]], args: argparse.Namespace,
    output_dir: Path,
) -> tuple[dict[str, dict[str, dict[str, Any]]], dict[str, dict[str, Any]]]:
    target_dir = output_dir / "targets"
    target_dir.mkdir(parents=True, exist_ok=True)
    packet_list = output_dir / "packet-list.txt"
    packet_list.write_text("".join(f"{r['packet_path']}\n" for r in records), encoding="utf-8")
    expected_paths = {r["packet_path"] for r in records}
    results_by_target = {}
    summaries = {}
    for name, parser in targets.items():
        results_path = target_dir / f"{name}.jsonl"
        summary_path = target_dir / f"{name}.summary.json"
        command = [
            sys.executable, str(TRACE_SCRIPT), str(parser), str(Path(args.packet_dir).absolute()),
            "--packet-list", str(packet_list), "--results-jsonl", str(results_path),
            "--json-out", str(summary_path), "--show-errors", "0",
        ]
        if args.workers is not None:
            command += ["--workers", str(args.workers)]
        run_command(command)
        results = {str(Path(r["packet_path"]).absolute()): r for r in read_jsonl(results_path)}
        if set(results) != expected_paths:
            missing = len(expected_paths - set(results))
            unexpected = len(set(results) - expected_paths)
            raise ComparisonError(f"Target {name} results do not match manifest: {missing} missing, {unexpected} unexpected")
        results_by_target[name] = results
        summaries[name] = json.loads(summary_path.read_text(encoding="utf-8"))
    return results_by_target, summaries


def compare(
    captures: list[Path], records: list[dict[str, Any]],
    oracle_reports: dict[str, dict[str, Any]],
    target_results: dict[str, dict[str, dict[str, Any]]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    indexed_packets = {
        (r["capture_path"], r["frame_number"], r.get("unit_index", 0)): r
        for r in records
    }
    indexed_oracle = {}
    for capture, report in oracle_reports.items():
        for packet in report["packets"]:
            key = (capture, int(packet["frame_number"]))
            if key in indexed_oracle:
                raise ComparisonError(f"Duplicate oracle frame: {key}")
            indexed_oracle[key] = packet

    rows = []
    per_capture = defaultdict(Counter)
    target_counts = {name: Counter() for name in target_results}
    packet_frames = {(capture, frame) for capture, frame, _ in indexed_packets}
    oracle_only_keys = {
        (capture, frame, 0) for capture, frame in indexed_oracle
        if (capture, frame) not in packet_frames
    }
    for key in sorted(set(indexed_packets) | oracle_only_keys):
        capture, frame, unit_index = key
        packet = indexed_packets.get(key)
        oracle = indexed_oracle.get((capture, frame))
        verdict = oracle["verdict"] if oracle else "NO_ORACLE_FRAME"
        row = {
            "capture_path": capture, "frame_number": frame,
            "unit_index": unit_index if packet else "",
            "packet_path": packet["packet_path"] if packet else "",
            "sha256": packet["sha256"] if packet else "",
            "oracle_verdict": verdict,
            "oracle_reasons": " | ".join(oracle["rejection_reasons"]) if oracle else "",
        }
        counter = per_capture[capture]
        counter["oracle_frames"] += bool(oracle and unit_index == 0)
        counter["extracted_packets"] += bool(packet)
        counter["extracted_frames"] += bool(packet and unit_index == 0)
        counter["matched"] += bool(oracle and packet and unit_index == 0)
        counter["matched_packets"] += bool(oracle and packet)
        counter["oracle_only"] += bool(oracle and not packet)
        counter["packet_only"] += bool(packet and not oracle and unit_index == 0) if oracle_reports else 0
        if oracle and unit_index == 0:
            counter[f"oracle_{verdict.lower()}"] += 1
        statuses = []
        for name, results in target_results.items():
            result = results.get(packet["packet_path"]) if packet else None
            status = (
                "ERROR" if result and result.get("execution_error") else
                "ACCEPT" if result and result["success"] else
                "REJECT" if result else "NOT_RUN"
            )
            row[name] = status
            row[f"{name}_error"] = result.get("error", "") if result else ""
            if packet:
                target_counts[name][status.lower()] += 1
                counter[f"{name}_{status.lower()}"] += 1
                if oracle:
                    target_counts[name][f"oracle_{verdict.lower()}_target_{status.lower()}"] += 1
                statuses.append(status)
        if len(set(statuses)) > 1:
            counter["target_disagreements"] += 1
        rows.append(row)
    totals = Counter()
    for counts in per_capture.values():
        totals.update(counts)
    pairwise = {}
    for left, right in combinations(target_results, 2):
        pair_counts = Counter()
        for row in rows:
            if row["packet_path"]:
                pair_counts[f"{row[left].lower()}_{row[right].lower()}"] += 1
        pairwise[f"{left} vs {right}"] = dict(pair_counts)
    summary = {
        "totals": dict(totals),
        "targets": {name: dict(counts) for name, counts in target_counts.items()},
        "target_pairs": pairwise,
        "per_capture": {str(capture): dict(per_capture[str(capture)]) for capture in captures},
    }
    return rows, summary


def write_analysis(
    output_dir: Path, rows: list[dict[str, Any]], summary: dict[str, Any],
    args: argparse.Namespace, targets: dict[str, Path], captures: list[Path],
) -> None:
    columns = [
        "capture_path", "frame_number", "unit_index", "packet_path", "sha256",
        "oracle_verdict", "oracle_reasons",
    ]
    for name in targets:
        columns += [name, f"{name}_error"]
    csv_path = output_dir / "comparison.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    summary.update({
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "capture_paths": [str(capture) for capture in captures],
        "manifest": str(Path(args.manifest).absolute()),
        "oracle": {
            "mode": "skip" if args.skip_oracle else "reuse" if args.oracle_report_dir else "run",
            "protocol": args.protocol,
            "filter_override": args.oracle_filter,
            "profile_config": str(args.oracle_profile_config) if args.oracle_profile_config else None,
            "decode_as": args.oracle_decode_as,
            "soft_reject_filters": args.oracle_soft_reject_filter,
        },
        "target_parsers": {name: str(path) for name, path in targets.items()},
    })
    summary_path = output_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"Comparison: {csv_path}")
    print(f"Summary: {summary_path}")
    if args.skip_oracle:
        print(f"Oracle skipped; {summary['totals'].get('extracted_packets', 0)} payloads analyzed")
    else:
        print(f"Coverage: {summary['totals'].get('matched', 0)} matched, "
              f"{summary['totals'].get('oracle_only', 0)} oracle only, "
              f"{summary['totals'].get('packet_only', 0)} packet only")
    for name, counts in summary["targets"].items():
        print(f"{name}: {counts.get('accept', 0)} accepted, {counts.get('reject', 0)} rejected, "
              f"{counts.get('error', 0)} execution errors; "
              f"oracle ACCEPT / target REJECT: {counts.get('oracle_accept_target_reject', 0)}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture_path", type=Path, help="Capture file or directory of capture files")
    parser.add_argument("packet_dir", type=Path, help="Directory created by extract_layer.py")
    parser.add_argument("protocol", help="Wireshark oracle profile or protocol name")
    parser.add_argument("--target", action="append", required=True, metavar="NAME=PATH",
                        help="Parser to evaluate; repeat for multiple targets")
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--manifest", type=Path, help="Extraction manifest; defaults to PACKET_DIR/manifest.jsonl")
    oracle_mode = parser.add_mutually_exclusive_group()
    oracle_mode.add_argument("--skip-oracle", action="store_true", help="Run targets without an oracle")
    oracle_mode.add_argument("--oracle-report-dir", type=Path, help="Reuse oracle JSON reports instead of running TShark")
    parser.add_argument("--oracle-filter", help="Override the oracle display filter")
    parser.add_argument("--oracle-profile-config", type=Path, help="Oracle JSON profile config")
    parser.add_argument("--oracle-decode-as", action="append", default=[], metavar="ASSOCIATION")
    parser.add_argument("--oracle-soft-reject-filter", action="append", default=[], metavar="FILTER")
    parser.add_argument("--oracle-field", action="append", default=[], metavar="FIELD")
    parser.add_argument("--tshark", default="tshark")
    parser.add_argument("--workers", type=int, help="Workers per target parser")
    args = parser.parse_args()
    args.manifest = Path(os.path.abspath(args.manifest or args.packet_dir / "manifest.jsonl"))
    args.packet_dir = Path(os.path.abspath(args.packet_dir))
    if args.oracle_profile_config:
        args.oracle_profile_config = Path(os.path.abspath(args.oracle_profile_config))
    if args.oracle_report_dir:
        args.oracle_report_dir = Path(os.path.abspath(args.oracle_report_dir))
    return args


def main() -> int:
    args = parse_args()
    try:
        captures = capture_files(args.capture_path)
        targets = target_specs(args.target)
        records = load_manifest(args.manifest, captures)
        output_dir = args.output_dir.absolute()
        output_dir.mkdir(parents=True, exist_ok=True)
        oracle_reports = {} if args.skip_oracle else run_oracle(captures, args.protocol, args, output_dir)
        target_results, target_summaries = run_targets(targets, records, args, output_dir)
        rows, summary = compare(captures, records, oracle_reports, target_results)
        summary["target_run_summaries"] = target_summaries
        write_analysis(output_dir, rows, summary, args, targets, captures)
        return 0
    except (ComparisonError, OSError, ValueError, KeyError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
