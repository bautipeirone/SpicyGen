import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

@dataclass
class CompileResult:
    success: bool
    errors: str | None


def compile_grammar(code: str, step_path: Path | None = None) -> CompileResult:
    """Compile a Spicy grammar string using spicyc.

    If step_path is provided, writes the code to it. Otherwise writes to a
    temporary .spicy file. Runs ``spicyc -j`` on it, and returns a
    :class:`CompileResult` indicating success or failure.
    """
    if step_path is not None:
        with step_path.open("w") as f:
            f.write(code)

        result = subprocess.run(
            ["spicyc", "-j", str(step_path)],
            capture_output=True,
            text=True,
        )
    else:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".spicy", delete=True
        ) as tmp:
            tmp.write(code)
            tmp.flush()

            result = subprocess.run(
                ["spicyc", "-j", tmp.name],
                capture_output=True,
                text=True,
            )

    if result.returncode == 0:
        return CompileResult(success=True, errors=None)
    else:
        return CompileResult(success=False, errors=result.stderr.strip())
