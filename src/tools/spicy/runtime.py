"""Semantic validation of Spicy grammars against known packets in runtime execution.

This module runs compiled Spicy grammars through ``spicy-dump`` to verify
that they correctly accept or reject a set of raw binary packets stored on
disk per protocol.
"""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from src.logger import logger
from src.tools.runtime import ParseResult, RuntimeAdapter, RuntimeArtifact
from src.tools.compiler import CompileResult
from src.tools.spicy.compiler import compile_to_hlto
from src.tools.spicy.parse_tree import compare_parse_trees, parse_spicy_output


def get_spicy_parse_tree_summary(parse_result: ParseResult) -> str | None:
    if parse_result.parse_tree is None:
        return None

    packet_path, _ = os.path.splitext(parse_result.packet_path)
    expected_tree_path = Path(f"{packet_path}.tree")
    if not expected_tree_path.exists():
        logger.warning(
            f"No expected parse tree found for packet '{parse_result.packet_path}' at {expected_tree_path}. Skipping parse tree diff."
        )
        return None

    with open(expected_tree_path, "r") as f:
        expected_tree = f.read()

    actual_tree = parse_spicy_output(parse_result.parse_tree)
    expected_tree = parse_spicy_output(expected_tree)

    actual_fields = actual_tree.get_parse_tree_fields()
    expected_fields = expected_tree.get_parse_tree_fields()
    logger.debug(f"Number of fields in parse tree: {len(actual_fields)}, expected: {len(expected_fields)}")
    logger.debug(f"Fields in parse tree: {actual_fields}")
    logger.debug(f"Expected fields: {expected_fields}")
    
    # This is probably a signal that the parser is trivial, i.e. unit { data: bytes &eod }
    if len(actual_fields) == 1:
        return (
            f"Parse tree contains only one leaf field ({actual_fields[0]!r}). "
            f"This likely means the parser is not extracting the message structure in enough detail. "
            f"Expected fields: {expected_fields}."
        )

    parse_tree_diffs = [str(mismatch) for mismatch in compare_parse_trees(actual_tree, expected_tree)]
    if not parse_tree_diffs:
        return None

    summary = "The parse tree of the actual packet is different from the expected one.\n"
    summary += "The following mismatches were found between the actual parse tree and the expected parse tree:\n"
    summary += "\n".join(parse_tree_diffs)

    logger.debug(f"Generated parse tree diff for {os.path.basename(parse_result.packet_path)}")
    return summary


def precompile_spicy_parser(grammar_file: Path, build_dir: Path | None = None) -> RuntimeArtifact:
    """Pre-compile a ``.spicy`` grammar to a ``.hlto`` binary."""
    cleanup = None
    if build_dir is None:
        build_dir = Path(tempfile.mkdtemp(prefix="spicy_build_"))
        cleanup = lambda: cleanup_spicy_artifact_dir(build_dir)
    else:
        build_dir.mkdir(parents=True, exist_ok=True)

    output_path = build_dir / "parser.hlto"

    logger.info(f"Pre-compiling grammar to {output_path}")
    result = compile_to_hlto(grammar_file=grammar_file, output_path=output_path)
    if not result.success:
        logger.error(f"Failed to pre-compile Spicy grammar '{grammar_file}'.")
        raise RuntimeError(f"Spicy pre-compilation failed with error:\n{result.errors}")

    logger.info(f"Pre-compilation succeeded: {output_path}")
    return RuntimeArtifact(path=output_path, cleanup=cleanup)


def cleanup_spicy_artifact_dir(build_dir: Path) -> None:
    logger.debug(f"Cleaning up temporary Spicy build directory: {build_dir}")
    shutil.rmtree(build_dir, ignore_errors=True)


def run_spicy_parser(parser_file: Path, packet_file: Path) -> ParseResult:
    """Run ``spicy-dump <parser_file> < <packet_file>`` and return the result."""
    logger.info(f"Running spicy-dump: {parser_file} < {packet_file.name}")

    with open(packet_file, "rb") as pf:
        result = subprocess.run(
            ["spicy-dump", str(parser_file)],
            stdin=pf,
            capture_output=True,
            text=True,
        )

    success = result.returncode == 0
    if success:
        logger.debug(f"spicy-dump accepted {packet_file.name}")
    else:
        logger.debug(f"spicy-dump rejected {packet_file.name}: {result.stderr.strip()}")

    return ParseResult(
        packet_path=str(packet_file),
        success=success,
        parse_tree=result.stdout if success else None,
        error=result.stderr.strip(),
    )


SPICY_RUNTIME = RuntimeAdapter(
    parse_packet=run_spicy_parser,
    prepare_parser=precompile_spicy_parser,
    summarize_parse_result=get_spicy_parse_tree_summary,
)
