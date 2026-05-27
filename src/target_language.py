from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from src.protocol import ProtocolType
from src.tools.compiler import CompileFunction
from src.tools.preprocess import ResponseCleaner
from src.tools.runtime import RuntimeAdapter

############## Spicy ###############
from src.tools.spicy.compiler import compile_spicy_grammar
from src.tools.spicy.preprocess import clean_spicy_response
from src.tools.spicy.runtime import SPICY_RUNTIME

################ 3D ################
from src.tools._3d.compiler import compile_3d_grammar
from src.tools._3d.preprocess import clean_3d_response
from src.tools._3d.runtime import _3D_RUNTIME

_PROJECT_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class TargetLanguage:
    name: str
    display_name: str
    assistant_name: str
    source_extension: str
    instructions_by_protocol_type: dict[ProtocolType, Path]
    compile_fn: CompileFunction
    runtime: RuntimeAdapter
    response_cleaner: ResponseCleaner

    @property
    def supports_parse_tree_summary(self) -> bool:
        return self.runtime.supports_parse_tree_summary

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
        instructions_by_protocol_type={
            ProtocolType.TEXT: _PROJECT_ROOT / "prompts" / "TEXT.md",
            ProtocolType.BINARY: _PROJECT_ROOT / "prompts" / "BINARY.md",
        },
        compile_fn=compile_spicy_grammar,
        runtime=SPICY_RUNTIME,
        response_cleaner=clean_spicy_response,
    ),
    "3d": TargetLanguage(
        name="3d",
        display_name="3D",
        assistant_name="3DDeveloper",
        source_extension=".3d",
        instructions_by_protocol_type={
            ProtocolType.BINARY: _PROJECT_ROOT / "prompts" / "3D_BINARY.md"
        },
        compile_fn=compile_3d_grammar,
        runtime=_3D_RUNTIME,
        response_cleaner=clean_3d_response,
    )
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
