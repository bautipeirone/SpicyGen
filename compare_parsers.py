#!/usr/bin/env python3
"""
compare_parsers.py

Compare multiple Spicy parsers against a packet capture file.

Usage:
    python compare_parsers.py <config.yaml> <packet_file> --port <port>

Example:
    python compare_parsers.py dhcp.yaml data/pcap/dhcp.pcap --port 67/udp%orig

Config file format (YAML):
    parsers:
      - name: "Hand-written DHCP"          # optional display name
        file: "examples/dhcp.spicy"        # path to .spicy grammar file
        entrypoint: "DHCP::Message"        # parser entrypoint (module::Unit)
      - name: "LLM-generated DHCP"
        file: "llm-results/dhcp-gemini.spicy"
        entrypoint: "DHCP::Message"
"""

import argparse
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml


# ── Data classes ────────────────────────────────────────────────────────────

@dataclass
class ParserConfig:
    """A single parser entry from the YAML config."""
    name: str
    file: str
    entrypoint: str


@dataclass
class RunResult:
    """Result of running a single parser against the input packet."""
    parser: ParserConfig
    accepted: bool
    duration_s: float
    return_code: int
    stdout: str
    stderr: str
    error: Optional[str] = None
    calls: Optional[int] = None


# ── Core logic ──────────────────────────────────────────────────────────────

def load_config(config_path: str) -> list[ParserConfig]:
    """Load and validate the YAML configuration file."""
    path = Path(config_path)
    if not path.exists():
        print(f"Error: config file not found: {config_path}", file=sys.stderr)
        sys.exit(1)

    with open(path) as f:
        raw = yaml.safe_load(f)

    if not raw or "parsers" not in raw:
        print(f"Error: config must contain a 'parsers' key", file=sys.stderr)
        sys.exit(1)

    parsers: list[ParserConfig] = []
    for i, entry in enumerate(raw["parsers"]):
        missing = [k for k in ("file", "entrypoint") if k not in entry]
        if missing:
            print(
                f"Error: parser entry {i} missing required field(s): {', '.join(missing)}",
                file=sys.stderr,
            )
            sys.exit(1)

        name = entry.get("name", Path(entry["file"]).stem)
        parsers.append(
            ParserConfig(
                name=name,
                file=entry["file"],
                entrypoint=entry["entrypoint"],
            )
        )

    if not parsers:
        print("Error: no parsers defined in config", file=sys.stderr)
        sys.exit(1)

    return parsers


def run_parser(parser: ParserConfig, packet_file: str, port: str) -> RunResult:
    """
    Invoke spicy-driver for a single parser and return the result.

    Command:
        spicy-driver -d -F <packet_file> -P <port>=<entrypoint> <grammar_file>
    """
    grammar_path = Path(parser.file)
    if not grammar_path.exists():
        return RunResult(
            parser=parser,
            accepted=False,
            duration_s=0.0,
            return_code=-1,
            stdout="",
            stderr="",
            error=f"Grammar file not found: {parser.file}",
        )

    port_spec = f"{port}={parser.entrypoint}"
    cmd = [
        "spicy-driver",
        "-d",
        "--enable-profiling",
        "-F", packet_file,
        "-P", port_spec,
        str(grammar_path),
    ]

    start = time.monotonic()
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
        )
        elapsed = time.monotonic() - start

        calls = _parse_profiling_calls(proc.stderr, parser.entrypoint)

        return RunResult(
            parser=parser,
            accepted=(proc.returncode == 0),
            duration_s=elapsed,
            return_code=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
            calls=calls,
        )
    except FileNotFoundError:
        elapsed = time.monotonic() - start
        return RunResult(
            parser=parser,
            accepted=False,
            duration_s=elapsed,
            return_code=-1,
            stdout="",
            stderr="",
            error="spicy-driver not found on PATH",
        )
    except subprocess.TimeoutExpired:
        elapsed = time.monotonic() - start
        return RunResult(
            parser=parser,
            accepted=False,
            duration_s=elapsed,
            return_code=-1,
            stdout="",
            stderr="",
            error="Timed out after 60s",
        )


def _parse_profiling_calls(stdout: str, entrypoint: str) -> Optional[int]:
    """
    Parse the profiling table from spicy-driver --enable-profiling stdout.

    Each line in the profiling table has columns separated by whitespace.
    The first column is the parser name and the second is the call count.
    We look for the line matching the entrypoint.
    """
    for line in stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split()
        if len(parts) >= 2 and entrypoint in parts[0]:
            try:
                return int(parts[1])
            except ValueError:
                continue
    return None


# ── Output formatting ──────────────────────────────────────────────────────

RESET = "\033[0m"
BOLD = "\033[1m"
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
DIM = "\033[2m"


def status_str(result: RunResult) -> str:
    if result.error:
        return f"{YELLOW}ERROR{RESET}"
    return f"{GREEN}ACCEPTED{RESET}" if result.accepted else f"{RED}REJECTED{RESET}"


def print_results(results: list[RunResult], packet_file: str, port: str) -> None:
    """Print a formatted summary table and statistics."""
    total = len(results)
    accepted = sum(1 for r in results if r.accepted)
    rejected = sum(1 for r in results if not r.accepted and not r.error)
    errors = sum(1 for r in results if r.error)

    # ── Header ──────────────────────────────────────────────────────────
    print()
    print(f"{BOLD}{'═' * 72}{RESET}")
    print(f"{BOLD}  Parser Comparison Results{RESET}")
    print(f"{BOLD}{'═' * 72}{RESET}")
    print(f"  Packet : {packet_file}")
    print(f"  Port   : {port}")
    print(f"  Parsers: {total}")
    print(f"{BOLD}{'─' * 72}{RESET}")

    # ── Per-parser results ──────────────────────────────────────────────
    name_width = max(len(r.parser.name) for r in results)
    name_width = max(name_width, 6)  # minimum width

    header = f"  {'Parser':<{name_width}}   {'Status':<10}   {'Time':>8}   {'RC':>4}   {'Calls':>6}"
    print(f"{DIM}{header}{RESET}")
    print(f"  {'─' * name_width}   {'─' * 10}   {'─' * 8}   {'─' * 4}   {'─' * 6}")

    for r in results:
        time_str = f"{r.duration_s:.3f}s" if r.duration_s > 0 else "  -"
        rc_str = str(r.return_code) if r.return_code != -1 else "-"
        calls_str = str(r.calls) if r.calls is not None else "-"
        print(
            f"  {r.parser.name:<{name_width}}   {status_str(r):<21}   {time_str:>8}   {rc_str:>4}   {calls_str:>6}"
        )
        if r.error:
            print(f"  {' ' * name_width}   {DIM}↳ {r.error}{RESET}")

    # ── Statistics ──────────────────────────────────────────────────────
    print(f"{BOLD}{'─' * 72}{RESET}")

    pct_accepted = (accepted / total * 100) if total else 0
    durations = [r.duration_s for r in results if r.duration_s > 0]

    print(f"  {GREEN}Accepted{RESET} : {accepted}/{total}  ({pct_accepted:.0f}%)")
    print(f"  {RED}Rejected{RESET} : {rejected}/{total}")
    if errors:
        print(f"  {YELLOW}Errors{RESET}   : {errors}/{total}")

    if durations:
        avg = sum(durations) / len(durations)
        fastest = min(durations)
        slowest = max(durations)
        print()
        print(f"  {CYAN}Avg time{RESET} : {avg:.3f}s")
        print(f"  {CYAN}Fastest{RESET}  : {fastest:.3f}s")
        print(f"  {CYAN}Slowest{RESET}  : {slowest:.3f}s")

    print(f"{BOLD}{'═' * 72}{RESET}")
    print()

    # ── Detailed stderr for failures ────────────────────────────────────
    failures = [r for r in results if not r.accepted]
    if failures:
        print(f"{BOLD}Failure Details{RESET}")
        print(f"{'─' * 72}")
        for r in failures:
            print(f"\n{BOLD}{r.parser.name}{RESET}  ({r.parser.file})")
            if r.error:
                print(f"  Error: {r.error}")
            if r.stderr.strip():
                for line in r.stderr.strip().splitlines()[:20]:
                    print(f"  {DIM}│{RESET} {line}")
                if len(r.stderr.strip().splitlines()) > 20:
                    print(f"  {DIM}│ ... (truncated){RESET}")
        print()


# ── CLI ─────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare multiple Spicy parsers against a packet capture.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  %(prog)s configs/dhcp.yaml data/pcap/dhcp.pcap --port 67/udp%%orig
  %(prog)s configs/dns.yaml  data/pcap/dns.cap   --port 53/udp%%orig
        """,
    )
    parser.add_argument(
        "config",
        help="Path to YAML config file listing parsers to compare",
    )
    parser.add_argument(
        "packet",
        help="Path to the packet capture file (pcap/pcapng)",
    )
    parser.add_argument(
        "--port",
        required=True,
        help="Port specification for spicy-driver, e.g. '67/udp%%orig'",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Print the full spicy-driver command before each run",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    # Validate packet file
    if not Path(args.packet).exists():
        print(f"Error: packet file not found: {args.packet}", file=sys.stderr)
        sys.exit(1)

    parsers = load_config(args.config)
    results: list[RunResult] = []

    print(f"\n{BOLD}Running {len(parsers)} parser(s) against {args.packet}...{RESET}\n")

    for i, parser in enumerate(parsers, 1):
        label = f"[{i}/{len(parsers)}] {parser.name}"
        print(f"  {CYAN}▶{RESET} {label} ...", end="", flush=True)

        if args.verbose:
            port_spec = f"{args.port}={parser.entrypoint}"
            print(f"\n    cmd: spicy-driver -d --enable-profiling -F {args.packet} -P {port_spec} {parser.file}")

        result = run_parser(parser, args.packet, args.port)
        results.append(result)

        # Inline status feedback
        print(f"\r  {status_str(result)} {label}")

    print_results(results, args.packet, args.port)

    # Exit with non-zero if any parser failed
    if any(not r.accepted for r in results):
        sys.exit(1)


if __name__ == "__main__":
    main()