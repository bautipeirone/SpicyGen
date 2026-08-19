from pathlib import Path
import subprocess
import tempfile
import os
from src.utils import load_file
from src.tools.compiler import CompileResult
from src.tools.vest.preprocess import preprocess_vest_code

VEST_EXEC = Path(os.environ["VEST_EXEC"])

def run_vest(source_file: str, build_dir: str):
    output_name = Path(source_file).stem + '.rs'
    output_path = os.path.join(build_dir, output_name)
    return subprocess.run(
        [str(VEST_EXEC), source_file, "--output", output_path],
        capture_output=True,
        text=True
    )


def compile_vest_grammar(source_file: Path) -> CompileResult:
    code = load_file(source_file)
    errors = preprocess_vest_code(code)

    if errors:
        error_message = "\n".join(errors)
        return CompileResult(success=False, errors=error_message)

    with tempfile.TemporaryDirectory(prefix="vest_build_", delete=True) as build_dir:
        result = run_vest(str(source_file), build_dir=build_dir)
        if result.returncode == 0:
            return CompileResult(success=True, errors=None)
        else:
            errors = (
                result.stderr
                or result.stdout
                or f"Vest exited with status {result.returncode} without producing output"
            ).strip()
            return CompileResult(success=False, errors=errors)
