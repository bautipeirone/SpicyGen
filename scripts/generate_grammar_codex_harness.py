#!/usr/bin/env python3
"""Generate and validate a Spicy grammar through the Codex CLI harness."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env")

from generate_grammar import SpecSource, infer_spec_source
from scripts.generate_grammar_one_shot import build_metrics
from src.logger import enable_runtime_log_file, logger
from src.prompts import (
    GenerationContextMode,
    build_empty_spec_prompt,
    build_one_shot_system_message,
    build_one_shot_user_message,
    build_spicy_criteria_only_context,
    build_spicy_success_criterion,
)
from src.protocol import Protocol, load_protocol, load_spec
from src.target_language import get_target_language
from src.tools.runtime import get_protocol_packet_paths
from src.usage import CostSummary
from src.utils import load_file, read_config, write_json_file
from src.validation import validate_grammar


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Generate a Spicy grammar with one Codex CLI session, then validate "
            "the emitted grammar with the local harness."
        )
    )
    parser.add_argument(
        "protocol",
        help="Protocol name used to resolve the packet corpus and specification.",
    )
    parser.add_argument(
        "--mode",
        choices=[mode.value for mode in GenerationContextMode],
        default=GenerationContextMode.FULL_CONTEXT.value,
        help=(
            "full-context embeds the framework specification and Spicy manual; "
            "criteria-only supplies only the language and success criterion."
        ),
    )
    parser.add_argument(
        "--model",
        "-m",
        default=None,
        help="Optional model override passed directly to `codex exec`.",
    )
    parser.add_argument(
        "--instructions-file",
        "-i",
        type=Path,
        default=None,
        help="Spicy instructions used by full-context mode.",
    )
    parser.add_argument(
        "--spec-file",
        type=Path,
        default=None,
        help="Specification override used by full-context mode.",
    )
    parser.add_argument(
        "--no-spec",
        action="store_true",
        help="Use the framework's no-spec prompt in full-context mode.",
    )
    parser.add_argument(
        "--output-file",
        "-o",
        type=Path,
        default=None,
        help="Write the emitted grammar here instead of stdout.",
    )
    parser.add_argument(
        "--metrics-file",
        type=Path,
        default=None,
        help="Write generation and validation metrics as JSON.",
    )
    parser.add_argument(
        "--debug-dir",
        type=Path,
        default=None,
        help="Save the Codex prompt and validation artifacts here.",
    )
    parser.add_argument(
        "--config",
        "-c",
        type=Path,
        default=None,
        help="Validation configuration YAML.",
    )
    parser.add_argument(
        "--codex-bin",
        default="codex",
        help="Codex CLI executable name or path.",
    )
    return parser.parse_args(argv)


def codex_output_instructions(candidate_path: Path) -> str:
    return f"""Work autonomously until the grammar satisfies the success criterion. You may inspect the repository and use its shell tools to compile and test the grammar.

Write the final grammar source to this exact path:
{candidate_path}

Do not modify any other repository file. Use temporary files for intermediate work. Your final response must contain only the complete grammar source code, without Markdown fences or an explanation.
"""


def build_full_context_prompt(
    *,
    protocol: Protocol,
    spec_content: str,
    spicy_instructions: str,
    candidate_path: Path,
) -> str:
    target_language = get_target_language("spicy")
    framework_system_message = build_one_shot_system_message(
        model_name="Codex",
        target_language_context=spicy_instructions,
        target_language=target_language,
        protocol=protocol,
    )
    framework_user_message = build_one_shot_user_message(spec_content)
    return "\n\n".join(
        [
            framework_system_message,
            framework_user_message,
            build_spicy_success_criterion(protocol.name),
            codex_output_instructions(candidate_path),
        ]
    )


def build_criteria_only_prompt(
    *,
    protocol_name: str,
    candidate_path: Path,
) -> str:
    return "\n\n".join(
        [
            build_spicy_criteria_only_context(protocol_name),
            codex_output_instructions(candidate_path),
        ]
    )


def normalize_codex_usage(
    usage: Any,
) -> dict[str, CostSummary] | None:
    """Convert Codex CLI token counts to the framework's AutoGen usage shape."""
    if not isinstance(usage, dict):
        return None

    input_tokens = usage.get("input_tokens")
    output_tokens = usage.get("output_tokens")
    cached_input_tokens = usage.get("cached_input_tokens", 0)
    if (
        not isinstance(input_tokens, int)
        or isinstance(input_tokens, bool)
        or input_tokens < 0
        or not isinstance(output_tokens, int)
        or isinstance(output_tokens, bool)
        or output_tokens < 0
        or not isinstance(cached_input_tokens, int)
        or isinstance(cached_input_tokens, bool)
        or cached_input_tokens < 0
    ):
        return None

    actual_prompt_tokens = max(input_tokens - cached_input_tokens, 0)
    return {
        "total_usage": {
            "total_cost": None,
            "prompt_tokens": input_tokens,
            "completion_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        },
        "actual_usage": {
            "total_cost": None,
            "prompt_tokens": actual_prompt_tokens,
            "completion_tokens": output_tokens,
            "total_tokens": actual_prompt_tokens + output_tokens,
        },
    }


def extract_codex_usage(jsonl_output: str) -> dict[str, CostSummary] | None:
    """Read the latest ``turn.completed`` usage event from Codex JSONL output."""
    normalized_usage = None
    for line_number, line in enumerate(jsonl_output.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            logger.warning("Ignoring malformed Codex JSONL event on line %d.", line_number)
            continue
        if isinstance(event, dict) and event.get("type") == "turn.completed":
            candidate = normalize_codex_usage(event.get("usage"))
            if candidate is not None:
                normalized_usage = candidate

    if normalized_usage is None:
        logger.warning("Codex completed without reporting token usage.")
    return normalized_usage


def run_codex(
    *,
    prompt: str,
    candidate_path: Path,
    last_message_path: Path,
    codex_bin: str,
    model: str | None,
) -> tuple[str, str, dict[str, CostSummary] | None]:
    resolved_codex = shutil.which(codex_bin)
    if resolved_codex is None:
        raise RuntimeError(f"Codex CLI executable '{codex_bin}' was not found.")

    command = [
        resolved_codex,
        "exec",
        "--json",
        "--ephemeral",
        "--sandbox",
        "workspace-write",
        "--color",
        "never",
        "--cd",
        str(PROJECT_ROOT),
        "--add-dir",
        str(candidate_path.parent),
        "--output-last-message",
        str(last_message_path),
    ]
    if model:
        command.extend(["--model", model])
    command.append("-")

    completed = subprocess.run(
        command,
        input=prompt,
        text=True,
        cwd=PROJECT_ROOT,
        stdout=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"`codex exec` failed with exit code {completed.returncode}.")

    usage = extract_codex_usage(completed.stdout)
    if candidate_path.exists() and candidate_path.stat().st_size:
        return candidate_path.read_text(), "candidate-file", usage
    if last_message_path.exists() and last_message_path.stat().st_size:
        return last_message_path.read_text(), "last-message", usage
    raise RuntimeError("Codex completed without emitting a grammar.")


def resolve_full_context(
    args: argparse.Namespace,
    protocol: Protocol,
) -> tuple[str, str, str]:
    if args.spec_file and args.no_spec:
        logger.warning(
            "Both --spec-file and --no-spec were provided; using --spec-file."
        )

    if args.spec_file:
        spec_source = SpecSource.SPEC_FILE
        spec_content = load_file(args.spec_file)
    elif args.no_spec:
        spec_source = SpecSource.NO_SPEC
        spec_content = build_empty_spec_prompt(
            protocol.name,
            protocol_long_name=protocol.full_name,
        )
    else:
        spec_source = SpecSource.INDEX
        spec_content = load_spec(protocol)

    if spec_content is None:
        raise RuntimeError(
            f"No specification content found for protocol '{protocol.name}'."
        )

    target_language = get_target_language("spicy")
    instructions_file = (
        args.instructions_file
        or target_language.instructions_path_for(protocol.type)
    )
    return (
        spec_content,
        load_file(instructions_file),
        infer_spec_source(spec_source, args.spec_file),
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    mode = GenerationContextMode(args.mode)
    runtime_log_path = args.debug_dir / "runtime.log" if args.debug_dir else None
    enable_runtime_log_file(runtime_log_path)

    protocol = load_protocol(args.protocol)
    if protocol is None:
        raise RuntimeError(f"Protocol '{args.protocol}' not found in index.")
    target_language = get_target_language("spicy")
    _, agent_config = read_config(args.config)

    if mode == GenerationContextMode.CRITERIA_ONLY and (
        args.instructions_file or args.spec_file or args.no_spec
    ):
        logger.warning(
            "--instructions-file, --spec-file, and --no-spec are ignored in criteria-only mode."
        )

    if args.debug_dir:
        args.debug_dir.mkdir(parents=True, exist_ok=True)

    start_time = time.time()
    with tempfile.TemporaryDirectory(prefix="grammar-codex-") as temporary_dir:
        temporary_path = Path(temporary_dir)
        candidate_path = temporary_path / "candidate.spicy"
        last_message_path = temporary_path / "last-message.txt"

        if mode == GenerationContextMode.FULL_CONTEXT:
            spec_content, spicy_instructions, spec_source = resolve_full_context(
                args, protocol
            )
            prompt = build_full_context_prompt(
                protocol=protocol,
                spec_content=spec_content,
                spicy_instructions=spicy_instructions,
                candidate_path=candidate_path,
            )
        else:
            spec_source = "not provided to Codex"
            prompt = build_criteria_only_prompt(
                protocol_name=protocol.name,
                candidate_path=candidate_path,
            )

        if args.debug_dir:
            (args.debug_dir / "codex-prompt.txt").write_text(prompt)

        response, output_source, usage = run_codex(
            prompt=prompt,
            candidate_path=candidate_path,
            last_message_path=last_message_path,
            codex_bin=args.codex_bin,
            model=args.model,
        )

    validation = validate_grammar(
        response,
        protocol,
        target_language,
        preprocessing_enabled=agent_config.get("enable_preprocessing", True),
        negative_packets_enabled=True,
        show_parsing_tree=agent_config.get("show_parsing_tree", False),
        debug_dir=args.debug_dir,
    )
    elapsed_time = time.time() - start_time

    feedback = validation.feedback(
        show_packet_representation=agent_config.get(
            "show_packet_representation", False
        )
    )
    print(f"Validation feedback:\n{feedback}", file=sys.stderr)

    positive_packet_paths, negative_packet_paths = get_protocol_packet_paths(
        protocol.name
    )
    metrics = build_metrics(
        model=args.model or "codex-default",
        protocol_name=protocol.name,
        target_language_name=target_language.name,
        spec_source=spec_source,
        elapsed_time_s=elapsed_time,
        usage=usage,
        validation=validation,
        positive_packets=len(positive_packet_paths),
        negative_packets=len(negative_packet_paths),
        feedback=feedback,
    )
    metrics["mode"] = mode.value
    metrics["codex_output_source"] = output_source

    if args.metrics_file:
        write_json_file(args.metrics_file, metrics)

    if args.output_file:
        args.output_file.write_text(validation.code)
        logger.info("Saved grammar to %s", args.output_file)
    else:
        print(validation.code)

    if not validation.success:
        logger.warning("The Codex-generated grammar did not pass validation.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        logger.error("Codex harness grammar generation failed: %s", exc)
        raise SystemExit(1) from exc
