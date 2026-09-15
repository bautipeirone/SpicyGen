from dotenv import load_dotenv
load_dotenv()

from pathlib import Path
import os
import subprocess
import tempfile
from src.utils import load_file
from src.tools.compiler import CompileResult
from src.tools._3d.preprocess import preprocess_3d_code

def run_everparse(grammar_file: str, build_dir: str):
    EVERPARSE_SCRIPT_PATH = os.getenv("EVERPARSE_SCRIPT_PATH")
    if EVERPARSE_SCRIPT_PATH is None:
        raise RuntimeError("everparse script path is not found. Add it to .env.")
    return subprocess.run(
        [
            EVERPARSE_SCRIPT_PATH,
            "--odir", build_dir,
            "--input_stream", "buffer",
            grammar_file
        ],
        capture_output=True,
        text=True
    )

def compile_3d_grammar(
    source_file: Path, *, preprocessing_enabled: bool = True
) -> CompileResult:
    if preprocessing_enabled:
        code = load_file(source_file)
        errors = preprocess_3d_code(code)
        if errors:
            error_message = "\n".join(errors)
            return CompileResult(success=False, errors=error_message)
    with tempfile.TemporaryDirectory(prefix="3d_build_", delete=True) as build_dir:
        result = run_everparse(str(source_file), build_dir=build_dir)
    if result.returncode == 0:
        return CompileResult(success=True, errors=None)
    else:
        errors = (
            result.stderr
            or result.stdout
            or f"Everparse/3D exited with status {result.returncode} without producing output"
        ).strip()
        return CompileResult(success=False, errors=errors)
