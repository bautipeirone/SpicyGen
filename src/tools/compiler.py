from dataclasses import dataclass
from pathlib import Path
import tempfile
from typing import Callable

type CompileFunction = Callable[[Path], CompileResult]

@dataclass
class CompileResult:
    success: bool
    errors: str | None

def run_grammar_compiler(code: str, compile_fn: CompileFunction, source_extension: str, debug_info: dict[str, Path | None] | None = None) -> CompileResult:
    """Compile a grammar string using the supplied target-language compiler."""
    step_path = debug_info.get("step_path") if debug_info else None
    errors_path = debug_info.get("errors_path") if debug_info else None

    if step_path is not None:
        with step_path.open("w") as f:
            f.write(code)
        result = compile_fn(step_path)
    else:
        with tempfile.NamedTemporaryFile(
            mode="w", prefix="Grammar_", suffix=source_extension, delete=True
        ) as tmp:
            tmp.write(code)
            tmp.flush()
            result = compile_fn(Path(tmp.name))

    if not result.success and errors_path is not None:
        with errors_path.open("w") as f:
            f.write(result.errors or "")
    return result
