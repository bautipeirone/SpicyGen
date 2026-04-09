"""Semantic validation of Spicy grammars against known packets in runtime execution.

This module runs compiled Spicy grammars through ``spicy-driver`` to verify
that they correctly accept or reject a set of hardcoded binary packets.
"""

import json
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from src.logger import logger

# Default location of the packet index relative to the project root.
_DEFAULT_INDEX_PATH = Path(__file__).resolve().parents[2] / "data" / "binary" / "packet_index.json"
_BINARY_BASE_DIR = Path(__file__).resolve().parents[2] / "data" / "binary"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ParseResult:
    """Result of running spicy-driver on a single packet."""
    packet_path: str
    success: bool
    stdout: str
    stderr: str


@dataclass
class PacketVerdict:
    """Comparison between the expected and actual parse outcome."""
    packet_path: str
    expected_accept: bool
    actual_accept: bool
    stderr: str

    @property
    def passed(self) -> bool:
        return self.expected_accept == self.actual_accept


@dataclass
class SemanticCheckResult:
    """Aggregated results of running a grammar against all packets for a protocol."""
    protocol: str
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
            lines.append("Failures:")
            for v in self.verdicts:
                if not v.passed:
                    expected = "accept" if v.expected_accept else "reject"
                    actual = "accepted" if v.actual_accept else "rejected"
                    lines.append(f"  - {v.packet_path}: expected {expected}, but was {actual}")
                    if v.stderr:
                        for err_line in v.stderr.strip().splitlines():
                            lines.append(f"      {err_line}")
        return "\n".join(lines)

    def summary(self) -> str:
        lines = [f"Runtime check for protocol '{self.protocol}':"]
        lines.append(f"  Accept set: {self.accept_passed}/{self.accept_total} passed")
        lines.append(f"  Reject set: {self.reject_passed}/{self.reject_total} passed")
        lines.append(f"  Overall: {'PASS' if self.all_passed else 'FAIL'}")
        lines.append(self.report_errors())
        return "\n".join(lines)

# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------

def run_spicy_driver(grammar_file: str, packet_file: str) -> ParseResult:
    """Run ``spicy-driver <grammar_file> < <packet_file>`` and return the result."""
    packet_path = Path(packet_file)
    logger.info(f"Running spicy-driver: {grammar_file} < {packet_path.name}")

    with open(packet_file, "rb") as pf:
        result = subprocess.run(
            ["spicy-driver", grammar_file],
            stdin=pf,
            capture_output=True,
            text=True,
        )

    success = result.returncode == 0
    if success:
        logger.debug(f"  spicy-driver accepted {packet_path.name}")
    else:
        logger.debug(f"  spicy-driver rejected {packet_path.name}: {result.stderr.strip()}")

    return ParseResult(
        packet_path=packet_file,
        success=success,
        stdout=result.stdout,
        stderr=result.stderr,
    )


def load_packet_index(index_path: Path | None = None) -> dict:
    """Load the JSON packet index.

    Returns a dict mapping protocol names to ``{"accept": [...], "reject": [...]}``.
    """
    path = index_path or _DEFAULT_INDEX_PATH
    logger.debug(f"Loading packet index from {path}")
    with open(path, "r") as f:
        index = json.load(f)
    logger.info(f"Loaded packet index with {len(index)} protocol(s): {', '.join(index.keys())}")
    return index


def run_parser_check(
    grammar_file: str,
    protocol: str,
    index_path: Path | None = None,
) -> SemanticCheckResult:
    """Run a grammar against all packets for *protocol* defined in the index.

    Parameters
    ----------
    grammar_file:
        Path to the ``.spicy`` grammar file.
    protocol:
        Protocol name to look up in the packet index (e.g. ``"dhcp"``).
    index_path:
        Optional override for the packet index JSON path.

    Returns
    -------
    SemanticCheckResult
        Aggregated verdicts for every packet.
    """
    index = load_packet_index(index_path)

    if protocol not in index:
        logger.warning(f"Protocol '{protocol}' not found in packet index. Skipping semantic check.")
        return SemanticCheckResult(protocol=protocol)

    entry = index[protocol]
    accept_packets: list[str] = entry.get("accept", [])
    reject_packets: list[str] = entry.get("reject", [])

    logger.info(
        f"Semantic check for '{protocol}': "
        f"{len(accept_packets)} accept packet(s), {len(reject_packets)} reject packet(s)"
    )

    result = SemanticCheckResult(protocol=protocol)

    for rel_path in accept_packets:
        abs_path = str(_BINARY_BASE_DIR / rel_path)
        parse_result = run_spicy_driver(grammar_file, abs_path)
        verdict = PacketVerdict(
            packet_path=rel_path,
            expected_accept=True,
            actual_accept=parse_result.success,
            stderr=parse_result.stderr,
        )
        result.verdicts.append(verdict)
        if not verdict.passed:
            logger.warning(f"FAIL (accept): {rel_path} was rejected. stderr: {parse_result.stderr.strip()}")

    for rel_path in reject_packets:
        abs_path = str(_BINARY_BASE_DIR / rel_path)
        parse_result = run_spicy_driver(grammar_file, abs_path)
        verdict = PacketVerdict(
            packet_path=rel_path,
            expected_accept=False,
            actual_accept=parse_result.success,
            stderr=parse_result.stderr,
        )
        result.verdicts.append(verdict)
        if not verdict.passed:
            logger.warning(f"FAIL (reject): {rel_path} was accepted but should have been rejected.")

    logger.info(f"Semantic check result: {result.summary()}")
    return result


def run_parser_check_from_code(
    grammar_code: str,
    protocol: str,
    index_path: Path | None = None,
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
        return run_parser_check(tmp.name, protocol, index_path)
