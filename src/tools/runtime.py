from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
import tempfile

from src.logger import logger
from src.protocol import Protocol, ProtocolType

_RAW_BASE_DIR = Path(__file__).resolve().parents[2] / "data" / "raw"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ParseResult:
    """Result of running a parser on a single packet."""
    packet_path: str
    success: bool
    error: str
    parse_tree: str | None = None


@dataclass(frozen=True)
class RuntimeArtifact:
    path: Path
    cleanup: Callable[[], None] | None = None


type ParseFunction = Callable[[Path, Path], ParseResult]
type PrepareParserFunction = Callable[[Path, Path | None], RuntimeArtifact]
type ParseResultSummarizer = Callable[[ParseResult], str | None]


@dataclass(frozen=True)
class RuntimeAdapter:
    parse_packet: ParseFunction
    prepare_parser: PrepareParserFunction | None = None
    summarize_parse_result: ParseResultSummarizer | None = None

    @property
    def requires_preparation(self) -> bool:
        return self.prepare_parser is not None

    @property
    def supports_parse_tree_summary(self) -> bool:
        return self.summarize_parse_result is not None

    def build_parser(self, source_path: Path, build_dir: Path | None = None) -> RuntimeArtifact:
        if self.prepare_parser is None:
            return RuntimeArtifact(path=source_path)
        return self.prepare_parser(source_path, build_dir)

    def summarize_result(self, parse_result: ParseResult) -> str | None:
        if self.summarize_parse_result is None:
            return None
        return self.summarize_parse_result(parse_result)


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
    setup_error: str | None = None

    @property
    def all_passed(self) -> bool:
        return self.setup_error is None and all(v.passed for v in self.verdicts)

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
    def score(self) -> int:
        if self.setup_error is not None:
            return 0
        return self.accept_passed + self.reject_passed

    @property
    def reject_total(self) -> int:
        return sum(1 for v in self.verdicts if not v.expected_accept)

    def report_errors(self, show_packet_representation: bool) -> str:
        lines: list[str] = []
        if self.setup_error:
            lines.append("")
            lines.append("Runtime setup failed:")
            for err_line in self.setup_error.strip().splitlines():
                lines.append(f"  {err_line}")
            lines.append("")
            return "\n".join(lines)

        if not self.all_passed:
            lines.append("")
            lines.append("Packets tested:")
            for v in self.verdicts:
                if v.passed:
                    continue

                lines.append(f"- {v.packet_path}: {'PASS' if v.passed else 'FAIL'}")
                lines.append(f"Expected: {'ACCEPT' if v.expected_accept else 'REJECT'}")
                lines.append(f"Actual: {'ACCEPTED' if v.actual_accept else 'REJECTED'}")

                packet_repr = get_packet_representation(_RAW_BASE_DIR / v.packet_path, self.protocol.type) if show_packet_representation else None
                if packet_repr is not None:
                    lines.append("Packet representation:")
                    lines.append(packet_repr)

                if v.parse_tree_summary:
                    lines.append("Parse tree summary:")
                    lines.append(v.parse_tree_summary)

                if not v.passed and v.error:
                    lines.append("Error output from parsing:")
                    for err_line in v.error.strip().splitlines():
                        lines.append(f"  {err_line}")
                lines.append("")
        return "\n".join(lines)

    def summary(self, show_packet_representation: bool = False) -> str:
        lines = [f"Runtime check for protocol '{self.protocol}':"]
        lines.append(f"  Accept set: {self.accept_passed}/{self.accept_total} passed")
        lines.append(f"  Reject set: {self.reject_passed}/{self.reject_total} passed")
        lines.append(f"  Overall: {'PASS' if self.all_passed else 'FAIL'}")
        lines.append(self.report_errors(show_packet_representation=show_packet_representation))
        return "\n".join(lines)


def get_packet_representation(packet_path: str | Path, packet_type: ProtocolType) -> str | None:
    """Return the raw text packet or a binary hexdump for a packet on disk."""
    try:
        with open(packet_path, "rb") as f:
            data = f.read()
    except OSError:
        return None

    if packet_type == ProtocolType.TEXT:
        packet = (
            data.decode("utf-8", errors="replace")
                .replace("\r", "\\r")
                .replace("\n", "\\n")
        )
        if not packet:
            return "<empty>"
        return packet

    if packet_type == ProtocolType.BINARY:
        lines = []
        for offset in range(0, len(data), 16):
            chunk = data[offset:offset + 16]
            hex_bytes = " ".join(f"{byte:02x}" for byte in chunk)
            ascii_bytes = "".join(chr(byte) if 32 <= byte <= 126 else "." for byte in chunk)
            lines.append(f"{offset:08x}  {hex_bytes:<47}  {ascii_bytes}")
        return "\n".join(lines)

    return None


def get_protocol_packet_paths(protocol_name: str) -> tuple[list[str], list[str]]:
    """Return relative accept/reject packet paths for a protocol."""
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

    return accept_packets, reject_packets


def run_parser_check(
    parser_source: str | Path,
    runtime: RuntimeAdapter,
    protocol: Protocol,
    include_parse_tree_summary: bool,
    debug_info: dict[str, Path | None] | None = None,
) -> SemanticCheckResult:
    """Run a parser against all packets for *protocol* found on disk."""
    protocol_name = protocol.name
    protocol_dir = _RAW_BASE_DIR / protocol_name

    build_dir = debug_info.get("build_dir") if debug_info else None
    errors_path = debug_info.get("errors_path") if debug_info else None

    accept_packets, reject_packets = get_protocol_packet_paths(protocol_name)

    result = SemanticCheckResult(protocol=protocol)

    if not protocol_dir.exists():
        logger.warning(f"No raw packet directory found for protocol '{protocol_name}' at {protocol_dir}")
        return result

    try:
        parser_artifact = runtime.build_parser(Path(parser_source), build_dir)
    except RuntimeError as exc:
        result.setup_error = str(exc)
        if errors_path:
            errors_path.write_text(result.setup_error)
        logger.error(f"Runtime setup failed for '{protocol_name}': {result.setup_error}")
        return result

    logger.info(
        f"Semantic check for '{protocol_name}': "
        f"{len(accept_packets)} accept packet(s), {len(reject_packets)} reject packet(s)"
    )

    errors_log: list[str] = []

    for rel_path in accept_packets:
        abs_path = _RAW_BASE_DIR / rel_path
        parse_result = runtime.parse_packet(parser_artifact.path, abs_path)
        parse_tree_summary = runtime.summarize_result(parse_result) if include_parse_tree_summary else None
        verdict = PacketVerdict(
            packet_path=rel_path,
            expected_accept=True,
            actual_accept=parse_result.success and parse_tree_summary is None,
            error=parse_result.error,
            parse_tree_summary=parse_tree_summary,
        )
        result.verdicts.append(verdict)
        if parse_result.error:
            errors_log.append(parse_result.error.strip())
        if not verdict.passed:
            logger.warning(f"FAIL (accept): {rel_path} was rejected. stderr: {parse_result.error.strip()}")

    for rel_path in reject_packets:
        abs_path = _RAW_BASE_DIR / rel_path
        parse_result = runtime.parse_packet(parser_artifact.path, abs_path)
        verdict = PacketVerdict(
            packet_path=rel_path,
            expected_accept=False,
            actual_accept=parse_result.success,
            error=parse_result.error,
        )
        result.verdicts.append(verdict)
        if parse_result.error:
            errors_log.append(parse_result.error.strip())
        if not verdict.passed:
            logger.warning(f"FAIL (reject): {rel_path} was accepted but should have been rejected.")

    if errors_log and errors_path:
        errors_path.write_text("\n".join(errors_log))

    if parser_artifact.cleanup is not None:
        parser_artifact.cleanup()

    logger.debug(f"Semantic check result: {result.summary()}")
    return result


def run_parser_check_from_code(
    grammar_code: str,
    runtime: RuntimeAdapter,
    protocol: Protocol,
    source_extension: str,
    include_parse_tree_summary: bool = False,
    debug_info: dict[str, Path | None] | None = None,
) -> SemanticCheckResult:
    """Write *grammar_code* to a temp file, then run :func:`run_parser_check`."""
    with tempfile.NamedTemporaryFile(
        mode="w", prefix="Grammar_", suffix=source_extension, delete=True
    ) as tmp:
        tmp.write(grammar_code)
        tmp.flush()
        return run_parser_check(
            tmp.name,
            runtime,
            protocol,
            include_parse_tree_summary,
            debug_info=debug_info,
        )
