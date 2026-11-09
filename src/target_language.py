from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from src.protocol import ProtocolType
from src.tools.compiler import CompileFunction
from src.tools.preprocess import ResponseCleaner, trivial_response_cleaner
from src.tools.runtime import RuntimeAdapter

_PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts"


@dataclass(frozen=True)
class TargetLanguage(ABC):
    """Abstract factory for the components of a target language.

    Each concrete language creates its own compiler, runtime and response
    cleaner, and provides its instructions for each protocol type it
    supports. Language-specific modules are imported inside the factory methods
    so only the selected language's toolchain (and its environment) is loaded.
    """

    name: str
    display_name: str
    assistant_name: str
    source_extension: str

    @abstractmethod
    def create_compiler(self) -> CompileFunction: ...

    @abstractmethod
    def create_runtime(self) -> RuntimeAdapter: ...

    def create_response_cleaner(self) -> ResponseCleaner:
        return trivial_response_cleaner

    @property
    def supports_parse_tree_summary(self) -> bool:
        return self.create_runtime().supports_parse_tree_summary

    @abstractmethod
    def instructions_path_for(self, protocol_type: ProtocolType) -> Path: ...

    def _unsupported_protocol_type(self, protocol_type: ProtocolType) -> ValueError:
        return ValueError(
            f"Unsupported protocol type '{protocol_type.value}' for target language '{self.name}'."
        )


############## Spicy ###############
class SpicyLanguage(TargetLanguage):
    def create_compiler(self) -> CompileFunction:
        from src.tools.spicy.compiler import compile_spicy_grammar
        return compile_spicy_grammar

    def create_runtime(self) -> RuntimeAdapter:
        from src.tools.spicy.runtime import SPICY_RUNTIME
        return SPICY_RUNTIME

    def create_response_cleaner(self) -> ResponseCleaner:
        from src.tools.spicy.preprocess import clean_spicy_response
        return clean_spicy_response

    def instructions_path_for(self, protocol_type: ProtocolType) -> Path:
        match protocol_type:
            case ProtocolType.TEXT:
                return _PROMPTS_DIR / "spicy" / "TEXT.md"
            case ProtocolType.BINARY:
                return _PROMPTS_DIR / "spicy" / "BINARY.md"
        raise self._unsupported_protocol_type(protocol_type)


################ 3D ################
class ThreeDLanguage(TargetLanguage):
    def create_compiler(self) -> CompileFunction:
        from src.tools._3d.compiler import compile_3d_grammar
        return compile_3d_grammar

    def create_runtime(self) -> RuntimeAdapter:
        from src.tools._3d.runtime import _3D_RUNTIME
        return _3D_RUNTIME

    def create_response_cleaner(self) -> ResponseCleaner:
        from src.tools._3d.preprocess import clean_3d_response
        return clean_3d_response

    def instructions_path_for(self, protocol_type: ProtocolType) -> Path:
        match protocol_type:
            case ProtocolType.BINARY:
                return _PROMPTS_DIR / "3D" / "BINARY.md"
        raise self._unsupported_protocol_type(protocol_type)


############### Vest ###############
class VestLanguage(TargetLanguage):
    def create_compiler(self) -> CompileFunction:
        from src.tools.vest.compiler import compile_vest_grammar
        return compile_vest_grammar

    def create_runtime(self) -> RuntimeAdapter:
        from src.tools.vest.runtime import VEST_RUNTIME
        return VEST_RUNTIME

    def create_response_cleaner(self) -> ResponseCleaner:
        from src.tools.vest.preprocess import clean_vest_response
        return clean_vest_response

    def instructions_path_for(self, protocol_type: ProtocolType) -> Path:
        match protocol_type:
            case ProtocolType.BINARY:
                return _PROMPTS_DIR / "vest" / "BINARY.md"
        raise self._unsupported_protocol_type(protocol_type)


SUPPORTED_TARGET_LANGUAGES: dict[str, TargetLanguage] = {
    "spicy": SpicyLanguage(
        name="spicy",
        display_name="Spicy",
        assistant_name="SpicyDeveloper",
        source_extension=".spicy",
    ),
    "3d": ThreeDLanguage(
        name="3d",
        display_name="3D",
        assistant_name="3DDeveloper",
        source_extension=".3d",
    ),
    "vest": VestLanguage(
        name="vest",
        display_name="Vest",
        assistant_name="VestDeveloper",
        source_extension=".vest",
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
