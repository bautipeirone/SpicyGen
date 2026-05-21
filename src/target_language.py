from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from src.protocol import Protocol, ProtocolType
from src.tools.compiler import CompileResult, compile_spicy_grammar
from src.tools.runtime import SemanticCheckResult, run_parser_check_from_code

_PROJECT_ROOT = Path(__file__).resolve().parents[1]

CompileFunction = Callable[[str, dict[str, Path | None] | None], CompileResult]
RuntimeCheckFunction = Callable[
    [str, Protocol, bool, dict[str, Path | None] | None],
    SemanticCheckResult,
]
ResponseCleaner = Callable[[str], str]


def _strip_code_fences(text: str) -> str:
    if not text:
        return ""
    return text.strip().strip("```").strip()


def clean_spicy_response(text: str) -> str:
    """Strip markdown fences and leading non-grammar lines from Spicy output."""
    cleaned = _strip_code_fences(text)
    while cleaned and not cleaned.startswith("module"):
        cleaned = "\n".join(cleaned.splitlines()[1:]).strip()
    return cleaned


@dataclass(frozen=True)
class TargetLanguage:
    name: str
    display_name: str
    assistant_name: str
    source_extension: str
    build_extension: str | None
    instructions_by_protocol_type: dict[ProtocolType, Path]
    compile_fn: CompileFunction
    runtime_check_fn: RuntimeCheckFunction
    response_cleaner: ResponseCleaner

    def instructions_path_for(self, protocol_type: ProtocolType) -> Path:
        try:
            return self.instructions_by_protocol_type[protocol_type]
        except KeyError as exc:
            raise ValueError(
                f"Unsupported protocol type '{protocol_type.value}' for target language '{self.name}'."
            ) from exc


SUPPORTED_TARGET_LANGUAGES: dict[str, TargetLanguage] = {
    "spicy": TargetLanguage(
        name="spicy",
        display_name="Spicy",
        assistant_name="SpicyDeveloper",
        source_extension=".spicy",
        build_extension=".hlto",
        instructions_by_protocol_type={
            ProtocolType.TEXT: _PROJECT_ROOT / "prompts" / "TEXT.md",
            ProtocolType.BINARY: _PROJECT_ROOT / "prompts" / "BINARY.md",
        },
        compile_fn=compile_spicy_grammar,
        runtime_check_fn=run_parser_check_from_code,
        response_cleaner=clean_spicy_response,
    ),
}

DEFAULT_TARGET_LANGUAGE = "spicy"


def get_target_language(name: str) -> TargetLanguage:
    try:
        return SUPPORTED_TARGET_LANGUAGES[name]
    except KeyError as exc:
        supported = ", ".join(sorted(SUPPORTED_TARGET_LANGUAGES))
        raise ValueError(
            f"Unsupported target language '{name}'. Supported values: {supported}."
        ) from exc


def list_supported_target_languages() -> list[str]:
    return sorted(SUPPORTED_TARGET_LANGUAGES)
