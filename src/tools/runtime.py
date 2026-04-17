"""Semantic validation of Spicy grammars against known packets in runtime execution.

This module runs compiled Spicy grammars through ``spicy-driver`` to verify
that they correctly accept or reject a set of raw binary packets stored on
disk per protocol.
"""

import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
import os
import difflib

from src.logger import logger
from src.protocol import Protocol, ProtocolType
from src.utils import generate_diff

_RAW_BASE_DIR = Path(__file__).resolve().parents[2] / "data" / "raw"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ParseResult:
    """Result of running spicy-driver on a single packet."""
    packet_path: str
    success: bool
    error: str
    parse_tree: str | None = None


@dataclass
class PacketVerdict:
    """Comparison between the expected and actual parse outcome."""
    packet_path: str
    expected_accept: bool
    actual_accept: bool
    error: str
    parse_tree_summary: str | None = None

    @property
    def passed(self) -> bool:
        return self.expected_accept == self.actual_accept

@dataclass
class SemanticCheckResult:
    """Aggregated results of running a grammar against all packets for a protocol."""
    protocol: Protocol
    verdicts: list[PacketVerdict] = field(default_factory=list)

    @property
    def all_passed(self) -> bool:
        return all(v.passed for v in self.verdicts)

    @property
    def accept_passed(self) -> int:
        return sum(1 for v in self.verdicts if v.expected_accept and v.passed)

    @property
    def accept_total(self) -> int:
        return sum(1 for v in self.verdicts if v.expected_accept)

    @property
    def reject_passed(self) -> int:
        return sum(1 for v in self.verdicts if not v.expected_accept and v.passed)

    @property
    def reject_total(self) -> int:
        return sum(1 for v in self.verdicts if not v.expected_accept)

    def report_errors(self) -> str:
        lines = []
        if not self.all_passed:
            lines.append("")
            lines.append("Packets tested:")
            for v in self.verdicts:
                lines.append(f"- {v.packet_path}: {'PASS' if v.passed else 'FAIL'}")
                lines.append(f"Expected: {'ACCEPT' if v.expected_accept else 'REJECT'}")
                lines.append(f"Actual: {'ACCEPTED' if v.actual_accept else 'REJECTED'}")

                packet_repr = get_packet_representation(_RAW_BASE_DIR / v.packet_path, self.protocol.type)
                if packet_repr is not None:
                    lines.append(f"Packet representation:")
                    lines.append(packet_repr)

                if v.parse_tree_summary:
                    lines.append(f"Parse tree summary:")
                    lines.append(v.parse_tree_summary)

                if not v.passed:
                    if v.error:
                        lines.append(f"Error output from parsing:")
                        for err_line in v.error.strip().splitlines():
                            lines.append(f"  {err_line}")
                lines.append("")
        return "\n".join(lines)

    def summary(self) -> str:
        lines = [f"Runtime check for protocol '{self.protocol}':"]
        lines.append(f"  Accept set: {self.accept_passed}/{self.accept_total} passed")
        lines.append(f"  Reject set: {self.reject_passed}/{self.reject_total} passed")
        lines.append(f"  Overall: {'PASS' if self.all_passed else 'FAIL'}")
        lines.append(self.report_errors())
        return "\n".join(lines)


def get_parse_tree_summary(parse_result: ParseResult) -> str | None:
    if parse_result.parse_tree is None:
        return None
    
    packet_path, _ = os.path.splitext(parse_result.packet_path)
    expected_tree_path = Path(f"{packet_path}.json")
    if not expected_tree_path.exists():
        logger.warning(f"No expected parse tree found for packet '{parse_result.packet_path}' at {expected_tree_path}. Skipping parse tree diff.")
        return None
    
    with open(expected_tree_path, "r") as f:
        expected_tree = f.read()

    actual_tree = parse_result.parse_tree
    parse_tree_diff = generate_diff(actual_tree, expected_tree)
    if not parse_tree_diff:
        logger.debug(f"No differences found in parse trees for {parse_result.packet_path}.")
        return "No differences found in parse trees."

    summary_lines = []

    summary_lines.append(f"Actual parse tree:")
    summary_lines.append(f"```json\n{actual_tree}\n```")
    summary_lines.append(f"Expected parse tree:")
    summary_lines.append(f"```json\n{expected_tree}\n```")
    summary_lines.append("Parse tree diff (actual vs expected):")
    summary_lines.append(f"```diff\n{parse_tree_diff}\n```")

    logger.debug(f"Generated parse tree diff for {os.path.basename(parse_result.packet_path)}")    
    return "\n".join(summary_lines)

# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------

def get_packet_representation(packet_path: str | Path, packet_type: ProtocolType) -> str | None:
    """Return the raw text packet or a binary hexdump for a packet on disk."""
    try:
        with open(packet_path, "rb") as f:
            data = f.read()
    except OSError as exc:
        return None

    if packet_type == ProtocolType.TEXT:
        packet = data.decode("utf-8", errors="replace")
        if not packet:
            return "<empty>"
        return packet

    elif packet_type == ProtocolType.BINARY:
        lines = []
        for offset in range(0, len(data), 16):
            chunk = data[offset:offset + 16]
            hex_bytes = " ".join(f"{byte:02x}" for byte in chunk)
            ascii_bytes = "".join(chr(byte) if 32 <= byte <= 126 else "." for byte in chunk)
            lines.append(f"{offset:08x}  {hex_bytes:<47}  {ascii_bytes}")
        return "\n".join(lines)

def run_spicy_parser(grammar_file: str, packet_file: str) -> ParseResult:
    """Run ``spicy-dump <grammar_file> < <packet_file>`` and return the result."""
    packet_name = os.path.basename(packet_file)
    logger.info(f"Running spicy-dump: {grammar_file} < {packet_name}")

    with open(packet_file, "rb") as pf:
        spicy_proc = subprocess.Popen(
            ["spicy-dump", "-J", grammar_file],
            stdin=pf,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        fmt_proc = subprocess.Popen(["jq", "--monochrome-output", "--indent", "2"],
            stdin=spicy_proc.stdout,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        spicy_proc.stdout.close()
        formatted_stdout, _ = fmt_proc.communicate()
        spicy_stderr = spicy_proc.stderr.read()
        spicy_returncode = spicy_proc.wait()

    success = spicy_returncode == 0
    if success:
        logger.debug(f"spicy-dump accepted {packet_name}")
    else:
        logger.debug(f"spicy-dump rejected {packet_name}: {spicy_stderr.strip()}")

    return ParseResult(
        packet_path=packet_file,
        success=success,
        parse_tree=formatted_stdout if success else None,
        error=spicy_stderr.strip(),
    )


def run_parser_check(
    grammar_file: str,
    protocol: Protocol,
    show_parsing_tree: bool
) -> SemanticCheckResult:
    """Run a grammar against all packets for *protocol* found on disk.

    Parameters
    ----------
    grammar_file:
        Path to the ``.spicy`` grammar file.
    protocol:
        Protocol to look up under ``data/raw/<protocol>``.
    show_parsing_tree:
        Whether to include the parsing tree output from spicy-driver in the error report.

    Returns
    -------
    SemanticCheckResult
        Aggregated verdicts for every packet.
    """
    protocol_name = protocol.name
    protocol_dir = _RAW_BASE_DIR / protocol_name
    accept_dir = protocol_dir / "positive"
    reject_dir = protocol_dir / "negative"

    accept_packets = sorted(
        str(path.relative_to(_RAW_BASE_DIR))
        for path in accept_dir.rglob("*.bin")
        if path.is_file()
    ) if accept_dir.exists() else []
    reject_packets = sorted(
        str(path.relative_to(_RAW_BASE_DIR))
        for path in reject_dir.rglob("*.bin")
        if path.is_file()
    ) if reject_dir.exists() else []

    result = SemanticCheckResult(protocol=protocol)
    
    if not protocol_dir.exists():
        logger.warning(f"No raw packet directory found for protocol '{protocol_name}' at {protocol_dir}")
        return result

    logger.info(
        f"Semantic check for '{protocol_name}': "
        f"{len(accept_packets)} accept packet(s), {len(reject_packets)} reject packet(s)"
    )

    for rel_path in accept_packets:
        abs_path = str(_RAW_BASE_DIR / rel_path)
        parse_result = run_spicy_parser(grammar_file, abs_path)
        parse_tree_summary = get_parse_tree_summary(parse_result) if show_parsing_tree else None
        verdict = PacketVerdict(
            packet_path=rel_path,
            expected_accept=True,
            actual_accept=parse_result.success,
            error=parse_result.error,
            parse_tree_summary=parse_tree_summary,
        )
        result.verdicts.append(verdict)
        if not verdict.passed:
            logger.warning(f"FAIL (accept): {rel_path} was rejected. stderr: {parse_result.error.strip()}")

    for rel_path in reject_packets:
        abs_path = str(_RAW_BASE_DIR / rel_path)
        parse_result = run_spicy_parser(grammar_file, abs_path)
        verdict = PacketVerdict(
            packet_path=rel_path,
            expected_accept=False,
            actual_accept=parse_result.success,
            error=parse_result.error,
        )
        result.verdicts.append(verdict)
        if not verdict.passed:
            logger.warning(f"FAIL (reject): {rel_path} was accepted but should have been rejected.")

    logger.debug(f"Semantic check result: {result.summary()}")
    return result


def run_parser_check_from_code(
    grammar_code: str,
    protocol: Protocol,
    show_parsing_tree: bool = False
) -> SemanticCheckResult:
    """Convenience wrapper: writes *grammar_code* to a temp file, then runs
    :func:`run_parser_check` against it.

    This is useful when calling from the agent where we have the code string
    but no file on disk yet.
    """
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".spicy", delete=True
    ) as tmp:
        tmp.write(grammar_code)
        tmp.flush()
        return run_parser_check(tmp.name, protocol, show_parsing_tree)
