#!/usr/bin/env python3
"""Replay full captures through one of the selected Spicy Zeek integrations.

Counts parsed protocol units from the module's event, analyzer violations from
Zeek's analyzer_violation_info event, and logged weirds whose source is the
module's analyzer. Each capture runs in its own directory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MODULES = ROOT.parent / "zeek-integration"
EVENTS = {
    "babel": [('BABEL::message', 'payload: string')],
    "bfd": [('BFD::control', 'msg: BFD::ControlInfo')],
    "dhcp": [('DHCP_gpt::message', 'msg: DHCP_gpt::MessageInfo')],
    "dns": [('DNS_gpt::message', 'msg: DNS_gpt::MessageInfo')],
    "http": [('HTTP_gpt::message', 'msg: HTTP_gpt::MessageInfo')],
    "rtsp": [('RTSP::message', 'payload: string')],
    "rtp": [('RTP::rtp_packet', 'msg: RTP::RtpLog'),
            ('RTP::rtcp_packet', 'msg: RTP::RtcpLog')],
}
ANALYZERS = {
    "babel": "BABEL", "bfd": "BFD", "dhcp": "DHCP_UDP",
    "dns": "DNS_UDP", "http": "HTTP_TCP", "rtsp": "RTSP", "rtp": "RTP",
}
TRANSPORTS = {"babel": "udp", "bfd": "udp", "dhcp": "udp", "dns": "udp",
              "http": "tcp", "rtsp": "tcp", "rtp": "udp"}
SUMMARY_RE = re.compile(r"^ZEEK_TRACE_EVAL parsed=(\d+) violations=(\d+)$")
REASON_RE = re.compile(r"^ZEEK_TRACE_EVAL_REASON (\d+) (.*)$")


def monitor_script(protocol: str, ports: list[int]) -> str:
    handlers = "\n".join(
        f"event {name}(c: connection, is_orig: bool, {argument}) "
        "{ ++parsed_messages; }"
        for name, argument in EVENTS[protocol]
    )
    analyzer = ANALYZERS[protocol]
    port_values = ", ".join(f"{port}/{TRANSPORTS[protocol]}" for port in ports)
    port_setup = (
        f"const extra_ports: set[port] = {{ {port_values} }};\n"
        f"event zeek_init() &priority=5\n    {{\n"
        f"    Analyzer::register_for_ports(Analyzer::ANALYZER_{analyzer}, extra_ports);\n"
        f"    }}"
    ) if ports else ""
    return f"""@load base/frameworks/analyzer
module TraceEval;

global parsed_messages: count = 0;
global violations: count = 0;
global violation_reasons: table[string] of count &default=0;

{port_setup}

{handlers}

event analyzer_violation_info(atype: AllAnalyzers::Tag, info: AnalyzerViolationInfo)
    {{
    if ( Analyzer::name(atype) == \"{analyzer}\" )
        {{
        ++violations;
        ++violation_reasons[info$reason];
        }}
    }}

event zeek_done()
    {{
    print fmt(\"ZEEK_TRACE_EVAL parsed=%d violations=%d\", parsed_messages, violations);
    for ( reason in violation_reasons )
        print fmt(\"ZEEK_TRACE_EVAL_REASON %d %s\", violation_reasons[reason], reason);
    }}
"""


def run(command: list[str], *, cwd: Path, stdout_path: Path, stderr_path: Path) -> float:
    start = time.perf_counter()
    with stdout_path.open("w", encoding="utf-8") as out, stderr_path.open("w", encoding="utf-8") as err:
        result = subprocess.run(command, cwd=cwd, stdout=out, stderr=err, check=False)
    elapsed = time.perf_counter() - start
    if result.returncode:
        error = stderr_path.read_text(encoding="utf-8", errors="replace")[-1500:]
        raise RuntimeError(f"Command exited {result.returncode}: {' '.join(command)}\n{error}")
    return elapsed


def monitor_counts(path: Path) -> tuple[int, int, dict[str, int]]:
    parsed = violations = None
    reasons = {}
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.rstrip("\n")
            if match := SUMMARY_RE.fullmatch(line):
                parsed, violations = int(match[1]), int(match[2])
            elif match := REASON_RE.fullmatch(line):
                reasons[match[2]] = int(match[1])
    if parsed is None or violations is None:
        raise RuntimeError(f"Missing monitor summary; see {path}")
    return parsed, violations, reasons


def weird_counts(path: Path, analyzer: str) -> tuple[int, int, dict[str, int]]:
    total = own = 0
    names: Counter[str] = Counter()
    if path.exists():
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                row = json.loads(line)
                total += 1
                if row.get("source") == analyzer:
                    own += 1
                    names[row.get("name", "")] += 1
    return total, own, dict(names)


def capture_paths(values: list[Path]) -> list[Path]:
    captures = []
    for value in values:
        if value.is_dir():
            captures.extend(sorted(p for p in value.iterdir() if p.suffix.lower() in {".pcap", ".pcapng", ".cap"}))
        elif value.is_file():
            captures.append(value)
        else:
            raise ValueError(f"Capture does not exist: {value}")
    if not captures:
        raise ValueError("No captures found")
    return list(dict.fromkeys(p.resolve() for p in captures))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("protocol", choices=sorted(EVENTS))
    parser.add_argument("captures", nargs="+", type=Path, help="pcap files or directories")
    parser.add_argument("--module-root", type=Path, default=DEFAULT_MODULES)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--no-checksums", action="store_true", help="Pass -C to Zeek for captures with invalid/offloaded checksums")
    parser.add_argument("--port", type=int, action="append", default=[],
                        help="Also activate the analyzer on this TCP/UDP port; repeatable")
    args = parser.parse_args()

    try:
        captures = capture_paths(args.captures)
        if any(port < 1 or port > 65535 for port in args.port):
            raise ValueError("Ports must be between 1 and 65535")
        package = args.module_root.resolve() / f"spicy-{args.protocol}"
        analyzer_dir = package / "analyzer"
        script = package / "scripts" / "__load__.zeek"
        sources = [analyzer_dir / f"{args.protocol}{suffix}" for suffix in (".spicy", ".evt")]
        sources.append(analyzer_dir / f"zeek_{args.protocol}.spicy")
        for path in [*sources, script]:
            if not path.is_file():
                raise ValueError(f"Missing integration file: {path}")

        output = args.output_dir.resolve()
        if output.exists() and any(output.iterdir()):
            raise ValueError(f"Output directory is not empty: {output}")
        output.mkdir(parents=True, exist_ok=True)
        compiled = output / f"{args.protocol}.hlto"
        compile_seconds = run(
            ["spicyz", "-o", str(compiled), *(str(p) for p in sources)],
            cwd=output, stdout_path=output / "compile.stdout", stderr_path=output / "compile.stderr",
        )
        monitor = output / "monitor.zeek"
        monitor.write_text(monitor_script(args.protocol, args.port), encoding="utf-8")

        results = []
        for capture in captures:
            label = f"{capture.stem}-{hashlib.sha256(str(capture).encode()).hexdigest()[:10]}"
            run_dir = output / label
            run_dir.mkdir(parents=True, exist_ok=True)
            command = ["zeek"]
            if args.no_checksums:
                command.append("-C")
            command.extend(["-r", str(capture), str(compiled), str(script), str(monitor), "LogAscii::use_json=T"])
            elapsed = run(command, cwd=run_dir,
                          stdout_path=run_dir / "zeek.stdout", stderr_path=run_dir / "zeek.stderr")
            parsed, violations, reasons = monitor_counts(run_dir / "zeek.stdout")
            all_weirds, own_weirds, weird_names = weird_counts(
                run_dir / "weird.log", ANALYZERS[args.protocol]
            )
            record = {
                "capture": str(capture), "capture_bytes": capture.stat().st_size,
                "replay_seconds": elapsed, "parsed_messages": parsed,
                "violations": violations, "logged_weirds": own_weirds,
                "violation_reasons": reasons,
                "logged_weirds_by_name": weird_names,
                "all_logged_weirds": all_weirds, "output_dir": str(run_dir),
            }
            results.append(record)
            print(f"{capture.name}: {record['parsed_messages']} parsed, "
                  f"{record['violations']} violations, {record['logged_weirds']} logged weirds")
            if not record["parsed_messages"]:
                print(f"Warning: no {args.protocol} message events; check analyzer activation and traffic ports",
                      file=sys.stderr)

        report = {
            "protocol": args.protocol, "analyzer": ANALYZERS[args.protocol],
            "package": str(package), "compile_seconds": compile_seconds,
            "no_checksums": args.no_checksums, "zeek_version": subprocess.check_output(["zeek", "--version"], text=True).strip(),
            "additional_ports": args.port,
            "spicyz_version": subprocess.check_output(["spicyz", "--version"], text=True).strip(),
            "captures": results,
            "totals": {
                "parsed_messages": sum(r["parsed_messages"] for r in results),
                "violations": sum(r["violations"] for r in results),
                "logged_weirds": sum(r["logged_weirds"] for r in results),
            },
        }
        (output / "summary.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(f"Summary: {output / 'summary.json'}")
        return 0
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
