from dataclasses import dataclass
from pathlib import Path

from src.logger import logger
from src.protocol import Protocol
from src.target_language import TargetLanguage
from src.tools.compiler import run_grammar_compiler
from src.tools.runtime import (
    ParsingConfig,
    SemanticCheckResult,
    run_parser_check_from_code,
)


COMPILATION_FEEDBACK_TEMPLATE = """The grammar you produced failed to compile with the following errors:

{errors}

Please fix the grammar to resolve these compilation errors. Only output the corrected grammar."""

RUNTIME_FEEDBACK_TEMPLATE = """The grammar you produced failed to parse the following packets:

{errors}

Please fix the grammar to handle these packets correctly, then use validate_grammar again."""


@dataclass
class IterationStats:
    compiled: bool = False
    positive_accepted: int = 0
    negative_rejected: int = 0
    changed_from_previous: bool = False

    def as_dict(self) -> dict[str, bool | int]:
        return {
            "compiled": self.compiled,
            "positive_accepted": self.positive_accepted,
            "negative_rejected": self.negative_rejected,
            "changed_from_previous": self.changed_from_previous,
        }


@dataclass(frozen=True)
class GrammarValidationResult:
    """Result of one compile-and-packet-check validation attempt."""

    code: str
    compiled: bool
    compilation_errors: str | None = None
    packet_check: SemanticCheckResult | None = None

    @property
    def success(self) -> bool:
        return self.compiled and self.packet_check is not None and self.packet_check.all_passed

    def feedback(self, show_packet_representation: bool = False) -> str:
        """Return the same feedback that the refinement tool gives the LLM."""
        if not self.compiled:
            return COMPILATION_FEEDBACK_TEMPLATE.format(
                errors=self.compilation_errors
            )

        assert self.packet_check is not None
        if self.packet_check.setup_error:
            summary = self.packet_check.summary(
                show_packet_representation=show_packet_representation
            )
            return RUNTIME_FEEDBACK_TEMPLATE.format(errors=summary)
        if not self.packet_check.verdicts:
            return (
                f"No packets registered for protocol '{self.packet_check.protocol.name}'. "
                "Say TERMINATE if the grammar compiled successfully."
            )
        if self.packet_check.all_passed:
            return "All packets parsed correctly. Say TERMINATE."

        summary = self.packet_check.summary(
            show_packet_representation=show_packet_representation
        )
        return RUNTIME_FEEDBACK_TEMPLATE.format(errors=summary)

    def iteration_stats(self) -> IterationStats:
        packet_check = self.packet_check
        return IterationStats(
            compiled=self.compiled,
            positive_accepted=packet_check.accept_passed if packet_check else 0,
            negative_rejected=packet_check.reject_passed if packet_check else 0,
            changed_from_previous=False,
        )


def validate_grammar(
    code: str,
    protocol: Protocol,
    target_language: TargetLanguage,
    *,
    preprocessing_enabled: bool = True,
    negative_packets_enabled: bool = True,
    show_parsing_tree: bool = False,
    debug_dir: Path | None = None,
    attempt: int = 1,
    max_attempts: int | None = None,
) -> GrammarValidationResult:
    """Compile and packet-test one grammar without starting an agent loop."""
    cleaned_code = target_language.response_cleaner(code) or code

    step_path = None
    compilation_errors_path = None
    runtime_errors_path = None
    build_dir = None
    if debug_dir:
        refinement_dir = debug_dir / "refinement"
        errors_dir = debug_dir / "errors"
        build_root = debug_dir / "build"
        build_dir = build_root / f"step_{attempt}"
        for directory in (refinement_dir, errors_dir, build_root):
            directory.mkdir(parents=True, exist_ok=True)
        step_path = refinement_dir / f"Grammar_{attempt}{target_language.source_extension}"
        compilation_errors_path = errors_dir / f"{attempt}-compilation.log"
        runtime_errors_path = errors_dir / f"{attempt}-runtime.log"

    attempt_label = f"{attempt}/{max_attempts}" if max_attempts else str(attempt)
    logger.info(
        f"Compiling {target_language.display_name} attempt {attempt_label} ..."
    )

    compilation = run_grammar_compiler(
        cleaned_code,
        target_language.compile_fn,
        target_language.source_extension,
        debug_info={
            "step_path": step_path,
            "errors_path": compilation_errors_path,
        },
        preprocessing_enabled=preprocessing_enabled,
    )
    if not compilation.success:
        logger.warning(f"Compilation failed on attempt {attempt}.")
        logger.error(f"Compilation Errors:\n{compilation.errors}\n")
        return GrammarValidationResult(
            code=cleaned_code,
            compiled=False,
            compilation_errors=compilation.errors,
        )

    logger.info(f"Compilation succeeded on attempt {attempt}.")
    logger.info(
        f"Running runtime check for protocol '{protocol.name}' using {target_language.display_name}..."
    )
    parsing_config = ParsingConfig(
        negative_packets_enabled=negative_packets_enabled,
        include_parse_tree_summary=(
            show_parsing_tree and target_language.supports_parse_tree_summary
        ),
    )
    packet_check = run_parser_check_from_code(
        cleaned_code,
        target_language.runtime,
        protocol,
        target_language.source_extension,
        parsing_config=parsing_config,
        debug_info={
            "build_dir": build_dir,
            "errors_path": runtime_errors_path,
        },
    )
    return GrammarValidationResult(
        code=cleaned_code,
        compiled=True,
        packet_check=packet_check,
    )
