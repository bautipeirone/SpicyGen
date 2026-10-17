#!/usr/bin/env python3
"""Compare a Spicy grammar before and after Zeek's throw/require adaptation.

Input packets are raw .bin payloads, as produced by scripts/extract_layer.py.
Each payload becomes one synthetic connection in a pcap. Both variants run over
the same pcap with the same generated Zeek integration.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import struct
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from create_zeek_package import (  # noqa: E402
    adapt_parser_to_zeek_analyzer,
    replace_require_with_logging,
    replace_runtime_throw_with_logging,
)
from src.tools.spicy.preprocess import parse_spicy_file  # noqa: E402


ANALYZER = "THROW_REQUIRE_COMPARE"
PORT = 39001
FIRST_SOURCE_PORT = 40000
MAX_BATCH = 20000
PREFIX = "ZE_EVAL"


def checksum(data: bytes) -> int:
    if len(data) % 2:
        data += b"\0"
    words = struct.unpack(f"!{len(data) // 2}H", data)
    total = sum(words)
    while total >> 16:
        total = (total & 0xffff) + (total >> 16)
    return (~total) & 0xffff


def ipv4_packet(payload: bytes, source_port: int, transport: str, seq: int) -> bytes:
    source_ip = b"\xc0\x00\x02\x01"
    target_ip = b"\xc0\x00\x02\x02"
    protocol = 17 if transport == "udp" else 6
    if transport == "udp":
        length = len(payload) + 8
        header = struct.pack("!HHHH", source_port, PORT, length, 0)
        pseudo = source_ip + target_ip + struct.pack("!BBH", 0, protocol, length)
        udp_checksum = checksum(pseudo + header + payload) or 0xffff
        segment = struct.pack("!HHHH", source_port, PORT, length, udp_checksum) + payload
    else:
        raise ValueError("Use tcp_packet() for TCP")
    ip_header = struct.pack("!BBHHHBBH4s4s", 0x45, 0, len(segment) + 20,
                            seq & 0xffff, 0, 64, protocol, 0, source_ip, target_ip)
    ip_header = ip_header[:10] + struct.pack("!H", checksum(ip_header)) + ip_header[12:]
    return b"\x02\x00\x00\x00\x00\x02\x02\x00\x00\x00\x00\x01\x08\x00" + ip_header + segment


def tcp_packet(payload: bytes, source_port: int, source_is_orig: bool,
               sequence: int, acknowledgment: int, flags: int, packet_id: int) -> bytes:
    source_ip = b"\xc0\x00\x02\x01" if source_is_orig else b"\xc0\x00\x02\x02"
    target_ip = b"\xc0\x00\x02\x02" if source_is_orig else b"\xc0\x00\x02\x01"
    src_port, dst_port = (source_port, PORT) if source_is_orig else (PORT, source_port)
    header = struct.pack("!HHLLBBHHH", src_port, dst_port, sequence, acknowledgment,
                         5 << 4, flags, 65535, 0, 0)
    segment = header + payload
    pseudo = source_ip + target_ip + struct.pack("!BBH", 0, 6, len(segment))
    segment = header[:16] + struct.pack("!H", checksum(pseudo + segment)) + header[18:] + payload
    ip_header = struct.pack("!BBHHHBBH4s4s", 0x45, 0, len(segment) + 20,
                            packet_id & 0xffff, 0, 64, 6, 0, source_ip, target_ip)
    ip_header = ip_header[:10] + struct.pack("!H", checksum(ip_header)) + ip_header[12:]
    return b"\x02\x00\x00\x00\x00\x02\x02\x00\x00\x00\x00\x01\x08\x00" + ip_header + segment


def write_capture(path: Path, packets: list[Path], transport: str) -> None:
    with path.open("wb") as out:
        out.write(struct.pack("<IHHIIII", 0xa1b2c3d4, 2, 4, 0, 0, 65549, 1))
        timestamp = 1700000000
        tick = 0

        def record(frame: bytes) -> None:
            nonlocal tick
            out.write(struct.pack("<IIII", timestamp + tick // 1000000,
                                  tick % 1000000, len(frame), len(frame)))
            out.write(frame)
            tick += 1000

        for index, packet in enumerate(packets):
            data = packet.read_bytes()
            source_port = FIRST_SOURCE_PORT + index
            if transport == "udp":
                record(ipv4_packet(data, source_port, transport, index))
            else:
                # A complete handshake makes Zeek deliver application bytes to TCP analyzers.
                record(tcp_packet(b"", source_port, True, 1000, 0, 0x02, index * 6))
                record(tcp_packet(b"", source_port, False, 2000, 1001, 0x12, index * 6 + 1))
                record(tcp_packet(b"", source_port, True, 1001, 2001, 0x10, index * 6 + 2))
                record(tcp_packet(data, source_port, True, 1001, 2001, 0x18, index * 6 + 3))
                record(tcp_packet(b"", source_port, True, 1001 + len(data), 2001,
                                  0x11, index * 6 + 4))
                record(tcp_packet(b"", source_port, False, 2001, 1002 + len(data),
                                  0x10, index * 6 + 5))


def integration_evt(module: str, unit: str, transport: str) -> str:
    return (f"import {module};\n\n"
            f"protocol analyzer {ANALYZER} over {transport.upper()}:\n"
            f"    parse originator with {module}::{unit},\n"
            f"    parse responder with {module}::{unit};\n\n"
            f"on {module}::{unit} -> event ThrowRequireCompare::parsed($conn);\n")


def monitor_script(transport: str) -> str:
    return f"""@load base/frameworks/analyzer
module ThrowRequireCompare;

global parsed: event(c: connection);

event zeek_init() &priority=5
    {{
    Analyzer::register_for_ports(Analyzer::ANALYZER_{ANALYZER}, set({PORT}/{transport}));
    }}

event parsed(c: connection)
    {{
    print fmt("{PREFIX} parsed %s", c$id$orig_p);
    }}

event analyzer_violation_info(atype: AllAnalyzers::Tag, info: AnalyzerViolationInfo)
    {{
    if ( Analyzer::name(atype) == "{ANALYZER}" && info?$c )
        print fmt("{PREFIX} violation %s %s", info$c$id$orig_p, info$reason);
    }}
"""


def run_command(command: list[str], cwd: Path, label: str) -> None:
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True)
    (cwd / f"{label}.stdout").write_text(result.stdout)
    (cwd / f"{label}.stderr").write_text(result.stderr)
    if result.returncode:
        tail = (result.stderr or result.stdout).strip()[-2000:]
        raise RuntimeError(f"{' '.join(command)} failed ({result.returncode}):\n{tail}\n"
                           f"Full output: {cwd / f'{label}.stderr'}")


def compile_variant(source: str, module: str, unit: str, transport: str,
                    output: Path) -> Path:
    output.mkdir(parents=True)
    grammar = output / "grammar.spicy"
    grammar.write_text(source)
    evt = output / "integration.evt"
    evt.write_text(integration_evt(module, unit, transport))
    plugin = output / "analyzer.hlto"
    run_command(["spicyz", "-o", str(plugin), str(grammar), str(evt)], output, "compile")
    return plugin


def read_run(output: Path, packets: list[Path]) -> list[dict]:
    result = [{"parsed": 0, "violations": [], "weirds": []} for _ in packets]
    stdout = (output / "zeek.stdout").read_text(errors="replace")
    for line in stdout.splitlines():
        if not line.startswith(f"{PREFIX} "):
            continue
        parts = line.split(" ", 3)
        if len(parts) < 3:
            continue
        kind, port_text = parts[1:3]
        try:
            index = int(port_text.split("/")[0]) - FIRST_SOURCE_PORT
        except ValueError:
            continue
        if not 0 <= index < len(result):
            continue
        if kind == "parsed":
            result[index]["parsed"] += 1
        elif kind == "violation":
            result[index]["violations"].append(parts[3] if len(parts) > 3 else "")
    weird_log = output / "weird.log"
    if weird_log.exists():
        for line in weird_log.read_text(errors="replace").splitlines():
            if not line or line.startswith("#"):
                continue
            row = json.loads(line)
            if row.get("source") != ANALYZER:
                continue
            port_text = str(row.get("id.orig_p", ""))
            try:
                index = int(port_text.split("/")[0]) - FIRST_SOURCE_PORT
            except ValueError:
                continue
            if 0 <= index < len(result):
                result[index]["weirds"].append(row.get("name", ""))
    return result


def short_reason(reason: str) -> str:
    """Drop source locations so the same error groups across variants."""
    return re.sub(r" \([^()]*\.spicy:\d+:\d+[^()]*\)$", "", reason)


def compare(grammar: Path, packet_dir: Path, output: Path, transport: str) -> dict:
    grammar = grammar.resolve()
    packet_dir = packet_dir.resolve()
    output = output.resolve()
    if not grammar.is_file():
        raise ValueError(f"Grammar not found: {grammar}")
    if not packet_dir.is_dir():
        raise ValueError(f"Packet directory not found: {packet_dir}")
    packets = sorted(path for path in packet_dir.rglob("*.bin") if path.is_file())
    if not packets:
        raise ValueError(f"No .bin packets found in {packet_dir}")
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError(f"Output directory is not empty: {output}")
    for binary in ("spicyz", "zeek"):
        if shutil.which(binary) is None:
            raise RuntimeError(f"{binary} is not on PATH")
    # IPv4 total length includes 20 byte IP and 8/20 byte UDP/TCP headers.
    max_payload = 65507 if transport == "udp" else 65495
    oversized = [p for p in packets if p.stat().st_size > max_payload]
    if oversized:
        raise ValueError(f"Packet exceeds {transport.upper()} IPv4 payload limit: {oversized[0]}")
    info = parse_spicy_file(grammar)
    if info.entry_unit_args:
        raise ValueError(f"Entry unit {info.module_name}::{info.entry_unit} has arguments; "
                         "this generic Zeek integration requires an argument-free entry unit")
    raw = grammar.read_text()
    adapted = adapt_parser_to_zeek_analyzer(raw)
    rewritten = replace_require_with_logging(replace_runtime_throw_with_logging(raw))
    output.mkdir(parents=True, exist_ok=True)
    variants = {"original": raw, "transformed": adapted}
    plugins = {name: compile_variant(source, info.module_name, info.entry_unit,
                                     transport, output / name)
               for name, source in variants.items()}
    results = {name: [] for name in variants}
    for batch_number, start in enumerate(range(0, len(packets), MAX_BATCH)):
        batch = packets[start:start + MAX_BATCH]
        capture = output / f"batch-{batch_number:04}.pcap"
        write_capture(capture, batch, transport)
        for name, plugin in plugins.items():
            run_dir = output / name / f"batch-{batch_number:04}"
            run_dir.mkdir()
            monitor = output / name / "monitor.zeek"
            if not monitor.exists():
                monitor.write_text(monitor_script(transport))
            run_command(["zeek", "-r", str(capture), str(plugin), str(monitor),
                         "LogAscii::use_json=T"], run_dir, "zeek")
            results[name].extend(read_run(run_dir, batch))
        print(f"Processed {min(start + len(batch), len(packets))}/{len(packets)} packets", flush=True)

    rows = []
    transitions = Counter()
    categories: dict[str, Counter] = {}
    for index, packet in enumerate(packets):
        original = results["original"][index]
        transformed = results["transformed"][index]
        transition = ("parsed" if original["parsed"] else "unparsed") + " -> " + (
            "parsed" if transformed["parsed"] else "unparsed")
        transitions[transition] += 1
        category = str(packet.parent.relative_to(packet_dir))
        categories.setdefault(category, Counter())[transition] += 1
        rows.append({"packet": str(packet), "category": category,
                     "bytes": packet.stat().st_size, "transition": transition,
                     "original": original, "transformed": transformed})
    with (output / "packets.jsonl").open("w") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")
    summary = {
        "grammar": str(grammar), "packet_dir": str(packet_dir), "transport": transport,
        "packet_count": len(packets), "source_sha256": hashlib.sha256(raw.encode()).hexdigest(),
        "transformed_sha256": hashlib.sha256(adapted.encode()).hexdigest(),
        "transformation_changed_source": raw != adapted,
        "checks_rewritten": raw != rewritten,
        "zeek_version": subprocess.check_output(["zeek", "--version"], text=True).strip(),
        "transitions": dict(transitions),
        "categories": {name: dict(counts) for name, counts in sorted(categories.items())},
        "variants": {
            name: {
                "parsed_packets": sum(bool(r["parsed"]) for r in results[name]),
                "parse_events": sum(r["parsed"] for r in results[name]),
                "packets_with_violations": sum(bool(r["violations"]) for r in results[name]),
                "analyzer_violations": sum(len(r["violations"]) for r in results[name]),
                "logged_weirds": sum(len(r["weirds"]) for r in results[name]),
                "violation_reasons": dict(Counter(short_reason(reason) for r in results[name]
                                                  for reason in r["violations"])),
                "weird_names": dict(Counter(weird for r in results[name]
                                            for weird in r["weirds"])),
            } for name in variants
        },
    }
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def preview(summary: dict, output: Path) -> None:
    count = summary["packet_count"]
    print(f"\n{count} packets | {summary['transport'].upper()} | "
          f"throw/require checks rewritten: {summary['checks_rewritten']}")
    print(f"{'Variant':<16}{'Parsed':>10}{'Packets w/ violations':>24}"
          f"{'Violation events':>19}{'Logged weirds':>17}")
    for name, stats in summary["variants"].items():
        print(f"{name:<16}{stats['parsed_packets']:>7}/{count:<3}"
              f"{stats['packets_with_violations']:>24}"
              f"{stats['analyzer_violations']:>19}{stats['logged_weirds']:>17}")
    print("Transitions: " + ", ".join(f"{name}: {value}"
                                     for name, value in sorted(summary["transitions"].items())))
    if len(summary["categories"]) > 1:
        print("By directory:")
        for category, transitions in summary["categories"].items():
            changes = sum(value for key, value in transitions.items()
                          if key in ("parsed -> unparsed", "unparsed -> parsed"))
            print(f"  {category}: {sum(transitions.values())} packets, {changes} changed verdicts")
    for name, stats in summary["variants"].items():
        reasons = Counter(stats["violation_reasons"]).most_common(3)
        weirds = Counter(stats["weird_names"]).most_common(3)
        if reasons:
            print(f"{name} violations: " + "; ".join(f"{reason} ({count})"
                                                    for reason, count in reasons))
        if weirds:
            print(f"{name} weirds: " + "; ".join(f"{weird} ({count})"
                                                for weird, count in weirds))
    if all(not stats["parse_events"] for stats in summary["variants"].values()):
        print("Warning: neither variant emitted a parse event; check traffic framing and parser activation")
    if not summary["checks_rewritten"]:
        print("Warning: no throw/require check matched the package builder's transformation")
    print(f"Details: {output / 'packets.jsonl'}")
    print(f"Summary: {output / 'summary.json'}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("grammar", type=Path, help="Spicy grammar (.spicy)")
    parser.add_argument("packet_dir", type=Path, help="Directory of raw .bin payloads (recursive)")
    parser.add_argument("--transport", choices=("udp", "tcp"), default="udp")
    parser.add_argument("--output-dir", type=Path, help="New/empty directory for captures and reports")
    args = parser.parse_args()
    grammar = args.grammar.resolve()
    packet_dir = args.packet_dir.resolve()
    output = (args.output_dir or ROOT / "tmp" / "throw-require-results" /
              f"{grammar.stem}-{datetime.now(timezone.utc):%Y%m%d-%H%M%S-%f}").resolve()
    try:
        summary = compare(grammar, packet_dir, output, args.transport)
        preview(summary, output)
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError,
            json.JSONDecodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
