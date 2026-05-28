import subprocess
from pathlib import Path
from src.utils import load_file
from src.tools.compiler import CompileResult
from src.tools.spicy.preprocess import preprocess_spicy_code

def run_spicy_compiler(grammar_file: str, *args) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["spicyc", "-j", grammar_file, *args],
        capture_output=True,
        text=True,
    )

def compile_to_hlto(grammar_file: Path, output_path: Path) -> CompileResult:
    result = run_spicy_compiler(str(grammar_file), "-o", str(output_path))
    if result.returncode != 0:
        error = result.stderr.strip() or "Unknown pre-compilation error."
        return CompileResult(success=False, errors=error)
    return CompileResult(success=True, errors=None)

def compile_spicy_grammar(source_file: Path) -> CompileResult:
    code = load_file(source_file)
    errors = preprocess_spicy_code(code)
    if errors:
        error_message = "\n".join(errors)
        return CompileResult(success=False, errors=error_message)
  
    result = run_spicy_compiler(str(source_file))
    if result.returncode == 0:
        return CompileResult(success=True, errors=None)
    else:
        return CompileResult(success=False, errors=result.stderr.strip())
