import re
from dataclasses import dataclass, field
from pathlib import Path
from .reserved_keywords import RESERVED_KEYWORDS, REPLACE_KEYWORDS_MAP
from typing import TypedDict
from src.utils import strip_code_fences

@dataclass
class SpicyParserInfo:
    """Information extracted from a Spicy parser file."""
    module_name: str   # e.g. "DNS_gpt"
    entry_unit: str    # e.g. "Message"
    source_path: Path  # absolute path to the .spicy file
    entry_unit_args: list[tuple[str, str, str | None]] = field(default_factory=list)

def parse_spicy_file(path: Path) -> SpicyParserInfo:
    """Extract the module name and public entry-point unit from a .spicy file."""
    content = path.read_text()

    module_match = re.search(r"^module\s+(\w+)\s*;", content, re.MULTILINE)
    if not module_match:
        raise ValueError(f"No 'module' declaration found in {path}")

    unit_match = re.search(
        r"^public\s+type\s+(\w+)\s*=\s*unit\s*(?:\((.*?)\))?\s*\{",
        content,
        re.MULTILINE | re.DOTALL,
    )

    if not unit_match:
        raise ValueError(f"No 'public type ... = unit' found in {path}")

    entry_unit_args = []
    if unit_match.group(2):
        for args in unit_match.group(2).split(','):
            name, rest = args.split(':', maxsplit=1)
            _type, _, default = rest.partition('=')
            arg = (name.strip(), _type.strip(), default.strip() if default else None)
            entry_unit_args.append(arg)

    return SpicyParserInfo(
        module_name=module_match.group(1),
        entry_unit=unit_match.group(1),
        source_path=path.resolve(),
        entry_unit_args=entry_unit_args,
    )

def find_public_types(code: str) -> list[str]:
    """Return the names of all ``public type`` declarations, in source order.

    Aliases (``public type Packet = Message;``) count too: ``spicy-dump`` lists
    each one as a separate parser.
    """
    return re.findall(r"^\s*public\s+type\s+(\w+)\s*=", code, re.MULTILINE)


def check_single_entry_unit(code: str) -> str | None:
    """Return an error unless *code* declares exactly one ``public type``.

    Packets are parsed with ``spicy-dump`` without ``--parser``. When a grammar
    exposes several public units, ``spicy-dump`` silently picks one (observed:
    the alphabetically last), so validation would test a unit the model never
    meant as the entry point.
    """
    public_types = find_public_types(code)
    if len(public_types) == 1:
        return None
    if not public_types:
        return (
            "[error]: found no `public type` declaration. The grammar must declare "
            "exactly one `public type`: the entry-point unit that parses a whole packet."
        )
    names = ", ".join(public_types)
    return (
        f"[error]: found {len(public_types)} `public type` declarations ({names}). "
        "The grammar must declare exactly one `public type`: the entry-point unit "
        "that parses a whole packet. Declare every other unit with plain `type`."
    )


def clean_spicy_response(text: str) -> str:
    """Strip markdown fences and leading non-grammar lines from Spicy output."""
    cleaned = strip_code_fences(text)
    while cleaned and not cleaned.startswith("module"):
        cleaned = "\n".join(cleaned.splitlines()[1:]).strip()
    return cleaned

def search_reserved_keywords(code: str) -> list[tuple[int, str]]:
    # La primer heuristica es buscar literales de la forma "keyword ':'"
    reserved_keywords: list[tuple[int, str]] = []
    for line_num, line in enumerate(code.splitlines(), 1):
        for keyword in RESERVED_KEYWORDS:
            # if f"{keyword}:" in line:
            if re.search(rf"\b{keyword}\b\s*:", line): # Regex to match spaces around the keyword
                reserved_keywords.append((line_num, keyword))
    return reserved_keywords

def preprocess_spicy_code(code: str) -> list[str]:
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
    if entry_error := check_single_entry_unit(code):
        errors.append(entry_error)
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
