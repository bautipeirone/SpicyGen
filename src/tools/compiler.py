import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

@dataclass
class CompileResult:
    success: bool
    errors: str | None

def run_spicy_compiler(grammar_file: str):
    return subprocess.run(
            ["spicyc", "-j", grammar_file],
            capture_output=True,
            text=True,
        )

def compile_spicy_grammar(code: str, debug_info: dict[str, Path | None] | None = None) -> CompileResult:
    """Compile a Spicy grammar string using spicyc.

    If step_path is provided, writes the code to it. Otherwise writes to a
    temporary .spicy file. Runs ``spicyc -j`` on it, and returns a
    :class:`CompileResult` indicating success or failure.
    """
    step_path = debug_info.get("step_path") if debug_info else None
    errors_path = debug_info.get("errors_path") if debug_info else None

    if step_path is not None:
        with step_path.open("w") as f:
            f.write(code)
        result = run_spicy_compiler(str(step_path))
    else:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".spicy", delete=True
        ) as tmp:
            tmp.write(code)
            tmp.flush()
            result = run_spicy_compiler(tmp.name)


    if result.returncode == 0:
        return CompileResult(success=True, errors=None)
    else:
        if errors_path is not None:
            with errors_path.open("w") as f:
                f.write(result.stderr)
        return CompileResult(success=False, errors=result.stderr.strip())
