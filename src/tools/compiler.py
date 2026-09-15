from dataclasses import dataclass
from pathlib import Path
import tempfile
from typing import Protocol

@dataclass
class CompileResult:
    success: bool
    errors: str | None

class CompileFunction(Protocol):
    def __call__(
        self, source_file: Path, *, preprocessing_enabled: bool = True
    ) -> CompileResult: ...


def run_grammar_compiler(
    code: str,
    compile_fn: CompileFunction,
    source_extension: str,
    debug_info: dict[str, Path | None] | None = None,
    *,
    preprocessing_enabled: bool = True,
) -> CompileResult:
    """Compile a grammar string using the supplied target-language compiler."""
    step_path = debug_info.get("step_path") if debug_info else None
    errors_path = debug_info.get("errors_path") if debug_info else None

    if step_path is not None:
        with step_path.open("w") as f:
            f.write(code)
        result = compile_fn(
            step_path, preprocessing_enabled=preprocessing_enabled
        )
    else:
        with tempfile.NamedTemporaryFile(
            mode="w", prefix="Grammar_", suffix=source_extension, delete=True
        ) as tmp:
            tmp.write(code)
            tmp.flush()
            result = compile_fn(
                Path(tmp.name), preprocessing_enabled=preprocessing_enabled
            )

    if not result.success and errors_path is not None:
        with errors_path.open("w") as f:
            f.write(result.errors or "")
    return result
