"""Semantic validation of Spicy grammars against known packets in runtime execution.

This module runs compiled Spicy grammars through ``spicy-dump`` to verify
that they correctly accept or reject a set of raw binary packets stored on
disk per protocol.
"""

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from typing import Any, Mapping

from src.logger import logger
from src.protocol import ParserArgument, Protocol
from src.tools.runtime import ParseResult, RuntimeAdapter, RuntimeArtifact
from src.tools.spicy.preprocess import SpicyParserInfo, parse_spicy_file
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
        if cleanup is not None:
            cleanup()
        logger.error(f"Failed to pre-compile Spicy grammar '{grammar_file}'.")
        raise RuntimeError(f"Spicy pre-compilation failed with error:\n{result.errors}")

    logger.info(f"Pre-compilation succeeded: {output_path}")
    return RuntimeArtifact(path=output_path, cleanup=cleanup)


def cleanup_spicy_artifact_dir(build_dir: Path) -> None:
    logger.debug(f"Cleaning up temporary Spicy build directory: {build_dir}")
    shutil.rmtree(build_dir, ignore_errors=True)


def run_spicy_parser(
    parser_file: Path,
    packet_file: Path,
    parser_name: str | None = None,
) -> ParseResult:
    """Run ``spicy-dump <parser_file> < <packet_file>`` and return the result."""
    logger.info(f"Running spicy-dump: {parser_file} < {packet_file.name}")

    command = ["spicy-dump"]
    if parser_name:
        command.extend(["--parser", parser_name])
    command.append(str(parser_file))
    logger.debug(f"Running command {command}")

    with open(packet_file, "rb") as pf:
        result = subprocess.run(
            command,
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


def packet_argument_path(packet_file: Path) -> Path:
    """Return the metadata sidecar path for a packet fixture."""
    return Path(f"{packet_file}.args.json")


def load_packet_arguments(packet_file: Path) -> dict[str, Any]:
    sidecar = packet_argument_path(packet_file)
    if not sidecar.exists():
        raise RuntimeError(
            f"missing external parser argument sidecar '{sidecar}'. "
            "Add a JSON object mapping argument names to values."
        )

    try:
        payload = json.loads(sidecar.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot read external parser arguments from '{sidecar}': {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"external parser argument sidecar '{sidecar}' must contain a JSON object")
    return payload


def packet_argument_cache_key(packet_file: Path) -> str:
    """Group packets with identical external context under one compiled wrapper."""
    return json.dumps(load_packet_arguments(packet_file), sort_keys=True, separators=(",", ":"))


def _normalized_spicy_type(type_name: str) -> str:
    return re.sub(r"\s+", "", type_name)


def validate_parser_signature(
    parser_info: SpicyParserInfo,
    expected_arguments: tuple[ParserArgument, ...],
) -> None:
    expected = {argument.name: _normalized_spicy_type(argument.type) for argument in expected_arguments}
    actual = {name: _normalized_spicy_type(type_name) for name, type_name, _ in parser_info.entry_unit_args}

    if len(actual) != len(parser_info.entry_unit_args):
        raise RuntimeError("the generated public entry unit contains duplicate argument names")

    missing = sorted(expected.keys() - actual.keys())
    extra = sorted(actual.keys() - expected.keys())
    mismatched = sorted(
        name for name in expected.keys() & actual.keys() if expected[name] != actual[name]
    )
    if not (missing or extra or mismatched):
        return

    details = []
    if missing:
        details.append(f"missing arguments: {', '.join(missing)}")
    if extra:
        details.append(f"unexpected arguments: {', '.join(extra)}")
    if mismatched:
        details.append(
            "type mismatches: "
            + ", ".join(
                f"{name} is {actual[name]} (expected {expected[name]})" for name in mismatched
            )
        )
    raise RuntimeError("generated Spicy entry-unit signature violates protocol_index.json: " + "; ".join(details))


def render_spicy_argument(argument: ParserArgument, value: Any) -> str:
    """Render a JSON sidecar value as a type-checked Spicy expression."""
    type_name = _normalized_spicy_type(argument.type)
    if type_name == "bool":
        if not isinstance(value, bool):
            raise RuntimeError(f"argument '{argument.name}' must be a JSON boolean")
        return "True" if value else "False"

    integer_match = re.fullmatch(r"(u?)int(8|16|32|64)", type_name)
    if integer_match:
        if isinstance(value, bool) or not isinstance(value, int):
            raise RuntimeError(f"argument '{argument.name}' must be a JSON integer")
        unsigned = bool(integer_match.group(1))
        bits = int(integer_match.group(2))
        minimum = 0 if unsigned else -(2 ** (bits - 1))
        maximum = 2**bits - 1 if unsigned else 2 ** (bits - 1) - 1
        if not minimum <= value <= maximum:
            raise RuntimeError(
                f"argument '{argument.name}' value {value} is outside {type_name} range "
                f"[{minimum}, {maximum}]"
            )
        return str(value)

    if type_name == "string":
        if not isinstance(value, str):
            raise RuntimeError(f"argument '{argument.name}' must be a JSON string")
        return json.dumps(value, ensure_ascii=True)

    if type_name in {"real", "double"}:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise RuntimeError(f"argument '{argument.name}' must be a JSON number")
        return repr(value)

    raise RuntimeError(
        f"argument '{argument.name}' uses unsupported sidecar type '{argument.type}'; "
        "supported types are bool, int8/16/32/64, uint8/16/32/64, string, real, and double"
    )


def bind_packet_arguments(
    parser_info: SpicyParserInfo,
    expected_arguments: tuple[ParserArgument, ...],
    values: Mapping[str, Any],
) -> dict[str, str]:
    validate_parser_signature(parser_info, expected_arguments)
    expected_by_name = {argument.name: argument for argument in expected_arguments}
    missing = sorted(expected_by_name.keys() - values.keys())
    extra = sorted(values.keys() - expected_by_name.keys())
    if missing or extra:
        details = []
        if missing:
            details.append(f"missing values: {', '.join(missing)}")
        if extra:
            details.append(f"unknown values: {', '.join(extra)}")
        raise RuntimeError("invalid packet argument sidecar: " + "; ".join(details))
    return {
        name: render_spicy_argument(argument, values[name])
        for name, argument in expected_by_name.items()
    }


def generate_parser_wrapper(parser_info: SpicyParserInfo, main_args: Mapping[str, str]) -> str:
    ordered_arguments = [main_args[name] for name, _, _ in parser_info.entry_unit_args]
    return (
        f"module {parser_info.module_name}_Wrapper;\n\n"
        f"import {parser_info.module_name};\n\n"
        "public type Main = unit {\n"
        f"  packet: {parser_info.module_name}::{parser_info.entry_unit}({', '.join(ordered_arguments)});\n"
        "};\n"
    )


def precompile_spicy_packet_parser(
    grammar_file: Path,
    packet_file: Path,
    protocol: Protocol,
    build_dir: Path | None = None,
) -> RuntimeArtifact:
    """Generate and compile a wrapper binding one packet's external context."""
    expected_arguments = protocol.parser_arguments_for("spicy")
    if not expected_arguments:
        return precompile_spicy_parser(grammar_file, build_dir)

    cleanup = None
    if build_dir is None:
        build_dir = Path(tempfile.mkdtemp(prefix="spicy_packet_build_"))
        cleanup = lambda: cleanup_spicy_artifact_dir(build_dir)
    else:
        build_dir.mkdir(parents=True, exist_ok=True)

    try:
        parser_info = parse_spicy_file(grammar_file)
        values = load_packet_arguments(packet_file)
        bound_arguments = bind_packet_arguments(parser_info, expected_arguments, values)
        wrapper_path = build_dir / "parser-wrapper.spicy"
        wrapper_path.write_text(generate_parser_wrapper(parser_info, bound_arguments))
        output_path = build_dir / "parser.hlto"

        logger.info(f"Pre-compiling grammar and packet argument wrapper to {output_path}")
        result = compile_to_hlto(
            grammar_file=grammar_file,
            output_path=output_path,
            additional_sources=(wrapper_path,),
        )
        if not result.success:
            raise RuntimeError(f"Spicy wrapper pre-compilation failed with error:\n{result.errors}")
    except (OSError, ValueError) as exc:
        if cleanup is not None:
            cleanup()
        raise RuntimeError(f"cannot prepare Spicy packet wrapper: {exc}") from exc
    except RuntimeError:
        if cleanup is not None:
            cleanup()
        raise

    wrapper_parser = f"{parser_info.module_name}_Wrapper::Main"
    return RuntimeArtifact(path=output_path, parser_name=wrapper_parser, cleanup=cleanup)


SPICY_RUNTIME = RuntimeAdapter(
    parse_packet=run_spicy_parser,
    prepare_parser=precompile_spicy_parser,
    prepare_packet_parser=precompile_spicy_packet_parser,
    packet_parser_cache_key=packet_argument_cache_key,
    packet_argument_language="spicy",
    summarize_parse_result=get_spicy_parse_tree_summary,
)
