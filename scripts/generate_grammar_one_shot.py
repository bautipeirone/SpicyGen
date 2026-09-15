#!/usr/bin/env python3
"""Generate and validate a grammar with one model request and no agent tools."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env")

from generate_grammar import SpecSource, infer_spec_source
from src.agent import query_llm_once
from src.logger import enable_runtime_log_file, logger
from src.prompts import (
    GenerationContextMode,
    ONE_SHOT_INSTRUCTIONS,
    build_empty_spec_prompt,
    build_one_shot_system_message,
    build_one_shot_user_message,
    build_spicy_criteria_only_context,
    build_spicy_success_criterion,
)
from src.protocol import Protocol, load_protocol, load_spec
from src.providers import get_autogen_config
from src.target_language import get_target_language
from src.tools.runtime import get_protocol_packet_paths
from src.utils import load_file, read_config, write_json_file
from src.validation import GrammarValidationResult, validate_grammar


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Generate a Spicy grammar with one LLM request, then validate the "
            "answer without sending feedback back to the model."
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
        required=True,
        help="Model to query (must be a key from model_index.json).",
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
        help="Save the model prompts and validation artifacts here.",
    )
    parser.add_argument(
        "--config",
        "-c",
        type=Path,
        default=None,
        help="Generation and validation configuration YAML.",
    )
    return parser.parse_args(argv)


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


def build_metrics(
    *,
    model: str,
    protocol_name: str,
    target_language_name: str,
    spec_source: str,
    elapsed_time_s: float,
    usage: dict[str, Any] | None,
    validation: GrammarValidationResult,
    positive_packets: int,
    negative_packets: int,
    feedback: str,
) -> dict[str, Any]:
    iteration = validation.iteration_stats().as_dict()
    return {
        "model": model,
        "protocol": protocol_name,
        "target_language": target_language_name,
        "success": validation.success,
        "spec_source": spec_source,
        "positive_packets": positive_packets,
        "negative_packets": negative_packets,
        "max_total_retries": 1,
        "best_iteration": 1 if validation.packet_check is not None else None,
        "termination_reason": "success" if validation.success else "one_shot_complete",
        "first_successful_compile_iteration": 1 if validation.compiled else None,
        "iterations": [iteration],
        "retrieved_rfcs": None,
        "validation_feedback": feedback,
        "time_elapsed_s": round(elapsed_time_s, 2),
        "usage": usage,
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    mode = GenerationContextMode(args.mode)
    runtime_log_path = args.debug_dir / "runtime.log" if args.debug_dir else None
    enable_runtime_log_file(runtime_log_path)

    protocol = load_protocol(args.protocol)
    if protocol is None:
        raise RuntimeError(f"Protocol '{args.protocol}' not found in index.")
    target_language = get_target_language("spicy")

    if mode == GenerationContextMode.CRITERIA_ONLY and (
        args.instructions_file or args.spec_file or args.no_spec
    ):
        logger.warning(
            "--instructions-file, --spec-file, and --no-spec are ignored in criteria-only mode."
        )

    llm_params, agent_config = read_config(args.config)
    llm_config = get_autogen_config(args.model, llm_params)

    if mode == GenerationContextMode.FULL_CONTEXT:
        spec_content, target_language_context, spec_source = resolve_full_context(
            args, protocol
        )
        system_message = build_one_shot_system_message(
            model_name=args.model.split("-")[0],
            target_language_context=target_language_context,
            target_language=target_language,
            protocol=protocol,
        )
        user_message = "\n\n".join(
            [
                build_one_shot_user_message(spec_content),
                build_spicy_success_criterion(protocol.name),
            ]
        )
    else:
        spec_source = "not provided to model"
        system_message = (
            build_spicy_criteria_only_context(protocol.name)
            + ONE_SHOT_INSTRUCTIONS
        )
        user_message = "Please output the grammar."

    if args.debug_dir:
        args.debug_dir.mkdir(parents=True, exist_ok=True)
        (args.debug_dir / "system-prompt.txt").write_text(system_message)
        (args.debug_dir / "user-prompt.txt").write_text(user_message)

    logger.info(
        "Generating one-shot grammar for %s using %s targeting %s",
        protocol.name,
        args.model,
        target_language.display_name,
    )
    logger.debug("Initial system message:\n%s", system_message)
    logger.debug("Initial user message (start):\n%s\n...", user_message[:300])
    logger.debug("Initial user message (end):\n%s\n...", user_message[-300:])

    start_time = time.time()
    response, usage = query_llm_once(llm_config, system_message, user_message)
    validation = validate_grammar(
        response,
        protocol,
        target_language,
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
        model=args.model,
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

    if args.metrics_file:
        write_json_file(args.metrics_file, metrics)

    if args.output_file:
        args.output_file.write_text(validation.code)
        logger.info("Saved grammar to %s", args.output_file)
    else:
        print(validation.code)

    if not validation.success:
        logger.warning("The one-shot grammar did not pass validation.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        logger.error("One-shot grammar generation failed: %s", exc)
        raise SystemExit(1) from exc
