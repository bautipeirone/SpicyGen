import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
import re
from .reserved_keywords import RESERVED_KEYWORDS, REPLACE_KEYWORDS_MAP

@dataclass
class CompileResult:
    success: bool
    errors: str | None

@dataclass
class ProcessContext:
    code: str
    source_file: Path | None = None
    errors_path: Path | None = None

def run_spicy_compiler(grammar_file: str):
    return subprocess.run(
            ["spicyc", "-j", grammar_file],
            capture_output=True,
            text=True,
        )

def run_spicy_pipeline(context: ProcessContext) -> CompileResult:
    errors = search_reserved_keywords(context.code)
    if errors:
        error_message = "\n".join(errors)
        if context.errors_path is not None:
            with context.errors_path.open("w") as f:
                f.write(error_message)
        return CompileResult(success=False, errors=error_message)
  
    result = run_spicy_compiler(str(context.source_file))
    if result.returncode == 0:
        return CompileResult(success=True, errors=None)
    else:
        if context.errors_path is not None:
            with context.errors_path.open("w") as f:
                f.write(result.stderr)
        return CompileResult(success=False, errors=result.stderr.strip())

def search_reserved_keywords(code: str) -> list[str]:
    # La primer heuristica es buscar literales de la forma "keyword ':'"
    error_list: list[str] = []
    for line in code.splitlines():
        for keyword in RESERVED_KEYWORDS:
            # if f"{keyword}:" in line:
            if re.search(rf"\b{keyword}\b\s*:", line): # Regex to match spaces around the keyword
                reserved_keyword_error = f"[error]: found reserved keyword as field identifier '{keyword}'."
                suggested_replacement = REPLACE_KEYWORDS_MAP.get(keyword, None)
                if suggested_replacement:
                    reserved_keyword_error += f" Consider replacing it with '{suggested_replacement}'."
                error_list.append(reserved_keyword_error)
    return error_list

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
        context = ProcessContext(code=code, source_file=step_path, errors_path=errors_path)
        result = run_spicy_pipeline(context)
    else:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".spicy", delete=True
        ) as tmp:
            tmp.write(code)
            tmp.flush()
            context = ProcessContext(code=code, source_file=Path(tmp.name), errors_path=errors_path)
            result = run_spicy_pipeline(context)
    return result
