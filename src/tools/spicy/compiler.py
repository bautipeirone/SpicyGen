import subprocess
import re
from pathlib import Path
from src.utils import load_file
from src.tools.compiler import CompileResult
from .reserved_keywords import RESERVED_KEYWORDS, REPLACE_KEYWORDS_MAP
from typing import TypedDict

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
    errors = preprocess_code(code)
    if errors:
        error_message = "\n".join(errors)
        return CompileResult(success=False, errors=error_message)
  
    result = run_spicy_compiler(str(source_file))
    if result.returncode == 0:
        return CompileResult(success=True, errors=None)
    else:
        return CompileResult(success=False, errors=result.stderr.strip())

def search_reserved_keywords(code: str) -> list[tuple[int, str]]:
    # La primer heuristica es buscar literales de la forma "keyword ':'"
    reserved_keywords: list[tuple[int, str]] = []
    for line_num, line in enumerate(code.splitlines(), 1):
        for keyword in RESERVED_KEYWORDS:
            # if f"{keyword}:" in line:
            if re.search(rf"\b{keyword}\b\s*:", line): # Regex to match spaces around the keyword
                reserved_keywords.append((line_num, keyword))
    return reserved_keywords

def preprocess_code(code: str) -> list[str]:
    errors = []
    for line_num, keyword in search_reserved_keywords(code):
        error_message = f"[error]: found reserved keyword as field identifier '{keyword}' in line {line_num}."
        suggested_replacement = REPLACE_KEYWORDS_MAP.get(keyword, None)
        if suggested_replacement:
            error_message += f" Consider replacing it with '{suggested_replacement}'."
        errors.append(error_message)
    for bitfield_info in find_bitfields_without_bitorder_attribute(code):
        bitfield_name = f"'{bitfield_info['name']}' " if bitfield_info.get("name") else ""
        error_message = f"[error]: found bitfield declaration {bitfield_name}without a bit-order attribute at lines {bitfield_info['lines_range'][0]}-{bitfield_info['lines_range'][1]}. Add a &bit-order= attribute with the correct bitfield order to fix this issue."
        errors.append(error_message)
    return errors

class BitfieldInfo(TypedDict):
    name: str | None
    lines_range: tuple[int, int]
    content: str
    bitfield_order: str | None

def find_bitfields_without_bitorder_attribute(code: str) -> list[BitfieldInfo]:
    bitfields = find_bitfields_decls(code)
    return [ bitfield for bitfield in bitfields if not bitfield.get("bitfield_order") ]

def find_bitfields_decls(code: str) -> list[BitfieldInfo]:
    lines = list(enumerate([ line.strip() for line in code.splitlines() ], 1))
    # lines = [ line.strip() for line in code.splitlines() if not line.strip().startswith("#") ]
    # bitfield_decls = [ line for line in lines if "bitfield" in line ]
    current_bitfield_decl: BitfieldInfo = {}
    lines_buf = []
    result = []
    in_bitfield_decl = False
    for line_num, line in lines:
        if in_bitfield_decl:
            lines_buf.append(line)
            if match := re.match(r'^\s*\}\s*(?:(?:[^;]*?)&bit-order=(?P<order>[A-Za-z0-9:]+))?[^;]*;\s*$', line):
                in_bitfield_decl = False
                current_bitfield_decl["lines_range"] = start_line, line_num
                current_bitfield_decl["bitfield_order"] = match.group("order")
                current_bitfield_decl["content"] = "\n".join(lines_buf)
                result.append(current_bitfield_decl)
                current_bitfield_decl = {}
                lines_buf = []
        if match := re.match(r'^\s*(?P<identifier>[A-Za-z_]\w*)?\s*:\s*bitfield\(\s*(?P<n>\d+)\s*\)\s*\{\s*$', line):
            in_bitfield_decl = True
            start_line = line_num
            current_bitfield_decl["name"] = match.group("identifier")
            lines_buf.append(line)

    return result
