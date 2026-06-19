"""Parser for Spicy driver parse-tree output.

Converts the textual representation emitted by ``spicy-driver`` into Python
dictionaries so that parse trees can be compared programmatically.

Grammar of the format (informal)::

    value        = object | array | scalar
    object       = [qualified_name] '{' field* '}'
    field        = identifier ':' value
    array        = '[' value* ']'
    qualified_name = identifier '::' identifier
    scalar       = <rest of line>   (may be empty)

Example input::

    tcp::TCP {
      src_port: 80
      dst_port: 3372
    }

Becomes::

    {"_type": "tcp::TCP", "src_port": "80", "dst_port": "3372"}
"""

from __future__ import annotations
from typing import Any, Generator
from dataclasses import dataclass
from src.logger import logger



# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
type ParseTree = FieldUnit
type ParseTreeField = FieldUnit | FieldString | FieldArray | FieldBitfield

@dataclass(frozen=True)
class FieldUnit:
    name: str
    type: str
    value: dict[str, ParseTreeField]

    def get_parse_tree_fields(self) -> list[str]:
        return sum([ child.get_parse_tree_fields() for child in self.value.values() ], [])

    def __repr__(self) -> str:
        field_names = list(self.value.keys())
        preview = ", ".join(field_names[:4])
        if len(field_names) > 4:
            preview += ", ..."
        field_count = len(field_names)
        location = self.name or "<root>"
        return f"{location}: {self.type} with {field_count} field(s){f' [{preview}]' if preview else ''}"

@dataclass(frozen=True)
class FieldString:
    name: str
    value: str

    def get_parse_tree_fields(self) -> list[str]:
        return [self.name]

    def __repr__(self) -> str:
        preview = self.value[:40] + ("..." if len(self.value) > 40 else "")
        location = self.name or "<value>"
        return f"{location}: {preview!r}"

@dataclass(frozen=True)
class FieldArray:
    name: str
    value: list[ParseTreeField]

    def get_parse_tree_fields(self) -> list[str]:
        # Concat list of fields from children
        return sum([ child.get_parse_tree_fields() for child in self.value ], [])

    def __repr__(self) -> str:
        items = [repr(item) for item in self.value[:2]]
        if len(self.value) > 2:
            items.append("...")
        preview = ", ".join(items)
        location = self.name or "<array>"
        return f"{location}: array[{len(self.value)}]{f' [{preview}]' if preview else ''}"

@dataclass(frozen=True)
class FieldBitfield:
    name: str
    value: dict[str, FieldString]

    def get_parse_tree_fields(self) -> list[str]:
        return sum([ child.get_parse_tree_fields() for child in self.value.values() ], [])

    def __repr__(self) -> str:
        entries = [f"{key}={field.value!r}" for key, field in list(self.value.items())[:8]]
        if len(self.value) > 8:
            entries.append("...")
        preview = ", ".join(entries)
        location = self.name or "<bitfield>"
        return f"{location}: {f' {{{preview}}}' if preview else ''}"

@dataclass(frozen=True)
class ParseTreeMismatch:
    field: ParseTreeField
    ref_field: ParseTreeField

    def __repr__(self) -> str:
        field_name = self.field.name or self.ref_field.name or "<anonymous>"

        if type(self.field) != type(self.ref_field):
            return (
                f"Mismatch at {field_name}: type differs "
                f"got {self.field}; expected {self.ref_field}"
            )

        if isinstance(self.field, FieldString) and isinstance(self.ref_field, FieldString):
            return (
                f"Mismatch at {field_name}: value differs; "
                f"got {self.field}; expected {self.ref_field}"
            )

        if isinstance(self.field, FieldArray) and isinstance(self.ref_field, FieldArray):
            return (
                f"Mismatch at {field_name}: array length differs "
                f"({len(self.field.value)} vs {len(self.ref_field.value)}); "
                f"got {self.field}; expected {self.ref_field}"
            )

        if isinstance(self.field, FieldUnit) and isinstance(self.ref_field, FieldUnit):
            extra_fields = sorted(set(self.field.value) - set(self.ref_field.value))
            missing_fields = sorted(set(self.ref_field.value) - set(self.field.value))
            details = []

            if self.field.type != self.ref_field.type:
                details.append(
                    f"unit type differs ({self.field.type} vs {self.ref_field.type})"
                )
            if extra_fields:
                details.append(f"only in got: {', '.join(extra_fields)}")
            if missing_fields:
                details.append(f"only in expected: {', '.join(missing_fields)}")

            detail_text = "; ".join(details) if details else "nested fields differ"
            return f"Mismatch at {field_name}: {detail_text}"

        if isinstance(self.field, FieldBitfield) and isinstance(self.ref_field, FieldBitfield):
            extra_flags = sorted(set(self.field.value) - set(self.ref_field.value))
            missing_flags = sorted(set(self.ref_field.value) - set(self.field.value))
            changed_flags = sorted(
                key
                for key in set(self.field.value) & set(self.ref_field.value)
                if self.field.value[key] != self.ref_field.value[key]
            )
            details = []

            if extra_flags:
                details.append(f"only in got: {', '.join(extra_flags)}")
            if missing_flags:
                details.append(f"only in expected: {', '.join(missing_flags)}")
            if changed_flags:
                details.append(f"different values for: {', '.join(changed_flags)}")

            detail_text = "; ".join(details) if details else "bitfield contents differ"
            return f"Mismatch at {field_name}: {detail_text}"

        return f"Mismatch at {field_name}: got {self.field}; expected {self.ref_field}"

def compare_parse_trees(pt: ParseTree, ref: ParseTree) -> list[ParseTreeMismatch]:
    """Compare two parse trees and return a list of mismatches."""
    return match_objects(pt.value, ref.value)

def compare_fields(field1: ParseTreeField, field2: ParseTreeField) -> list[ParseTreeMismatch]:
    mismatches: list[ParseTreeMismatch] = []
    match field1, field2:
        case FieldString(name1, value1), FieldString(name2, value2):
            if value1 != value2:
                mismatches.append(ParseTreeMismatch(field1, field2))
        case FieldArray(_, value1), FieldArray(_, value2):
            ...
        case FieldUnit(_, _, value1), FieldUnit(_, _, value2):
            mismatches.extend(match_objects(value1, value2))
        case FieldBitfield(_, value1), FieldBitfield(_, value2):
            mismatches.extend(match_objects(value1, value2))
        
    if type(field1) != type(field2):
        mismatches.append(ParseTreeMismatch(field1, field2))
    return mismatches

def match_arrays(array, ref_array) -> list[ParseTreeMismatch]:
    mismatches = []
    if len(array) != len(ref_array):
        mismatches.append(ParseTreeMismatch(FieldArray("", array), FieldArray("", ref_array)))
    for pt_item, ref_item in zip(array, ref_array):
        mismatches.extend(compare_fields(pt_item, ref_item))
    return mismatches

def match_objects(pt: dict[str, ParseTreeField], ref: dict[str, ParseTreeField]) -> list[ParseTreeMismatch]:
    fields = set()
    mismatches = []
    for mismatch in match_by_field_name(pt, ref):
        field_name = mismatch.field.name
        if field_name in fields:
            continue
        fields.add(field_name)
        mismatches.append(mismatch)
    for mismatch in match_by_field_order(pt, ref):
        field_name = mismatch.field.name
        if field_name in fields:
            continue
        fields.add(field_name)
        mismatches.append(mismatch)
    return mismatches

def match_by_field_name(pt: dict[str, ParseTreeField], ref: dict[str, ParseTreeField]) -> list[ParseTreeMismatch]:
    pt_field_names = set(pt.keys())
    ref_field_names = set(ref.keys())
    common_field_names = pt_field_names & ref_field_names
    mismatches = []
    for field_name in common_field_names:
        pt_value = pt[field_name]
        ref_value = ref[field_name]
        mismatches.extend(compare_fields(pt_value, ref_value))
    return mismatches

def match_by_field_order(pt: dict[str, ParseTreeField], ref: dict[str, ParseTreeField]) -> list[ParseTreeMismatch]:
    mismatches = []
    for (pt_key, pt_value), (ref_key, ref_value) in zip(pt.items(), ref.items()):
        mismatches.extend(compare_fields(pt_value, ref_value))
    return mismatches


def parse_spicy_output(text: str) -> ParseTree:
    """Parse a single ``spicy-dump`` output block into a Python structure.

    Returns a *dict* for objects (with an optional ``"_type"`` key),
    a *list* for arrays, or a *str* for scalar values.
    """
    parser = _Parser(text)
    result = parser.parse_top_level()
    return result


# ---------------------------------------------------------------------------
# Internal parser
# ---------------------------------------------------------------------------

class _Parser:
    """Recursive-descent parser for the spicy-driver output format."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.pos = 0
        self.length = len(text)

    # -- low-level helpers --------------------------------------------------

    def _at_end(self) -> bool:
        return self.pos >= self.length

    def _peek(self) -> str | None:
        if self._at_end():
            return None
        return self.text[self.pos]

    def _advance(self) -> str:
        ch = self.text[self.pos]
        self.pos += 1
        return ch

    def _skip_ws(self) -> None:
        """Skip spaces, tabs, and newlines."""
        while not self._at_end() and self.text[self.pos] in " \t\n\r":
            self.pos += 1

    def _skip_spaces(self) -> None:
        """Skip spaces and tabs only (not newlines)."""
        while not self._at_end() and self.text[self.pos] in " \t":
            self.pos += 1

    def _read_line_rest(self) -> str:
        """Read from the current position to the end of the line.

        The trailing newline is consumed but not included in the result.
        """
        start = self.pos
        while not self._at_end() and self.text[self.pos] != "\n":
            self.pos += 1
        result = self.text[start : self.pos].rstrip()
        # consume the newline
        if not self._at_end() and self.text[self.pos] == "\n":
            self.pos += 1
        return result

    def _read_identifier(self) -> str:
        """Read a contiguous identifier (letters, digits, underscores)."""
        start = self.pos
        while not self._at_end() and (
            self.text[self.pos].isalnum() or self.text[self.pos] == "_"
        ):
            self.pos += 1
        return self.text[start : self.pos]

    def _read_qualified_name(self) -> str:
        """Read a possibly-qualified name like ``tcp::TCP`` or ``TCP``.

        If no ``::`` follows the first identifier the simple name is returned.
        """
        name = self._read_identifier()
        if (
            not self._at_end()
            and self.pos + 1 < self.length
            and self.text[self.pos : self.pos + 2] == "::"
        ):
            self.pos += 2  # skip '::'
            name2 = self._read_identifier()
            name = f"{name}::{name2}"
        return name

    # -- recursive descent --------------------------------------------------

    def parse_top_level(self) -> ParseTree:
        """Entry point: parse the top-level value."""
        self._skip_ws()
        if self._at_end():
            return FieldUnit("", "Void", {})
        return self._parse_value_in_context()

    def _parse_value_in_context(self) -> ParseTreeField:
        """Parse a value that appears at the *start* of a context.

        This is used for the top level and for array elements where the value
        is **not** preceded by ``field_name:``.  In this position the value
        can be:

        * A typed object — ``Name::Type { … }``
        * An anonymous object — ``{ … }``
        * (rarely) a scalar line
        """
        self._skip_ws()
        ch = self._peek()

        if ch == "{":
            return FieldBitfield(name="", value=self._parse_object())
        if ch is None or ch in "}]":
            return FieldString(name="", value="")

        # Try to read a qualified name followed by '{'
        saved = self.pos
        name = self._read_qualified_name()
        self._skip_spaces()

        if self._peek() == "{":
            return FieldUnit(name="", type=name, value=self._parse_object())

        # Not an object — fall back to reading the rest of the line as scalar
        self.pos = saved
        return FieldString(name="", value=self._read_line_rest())

    def _parse_object(self) -> dict[str, ParseTreeField]:
        """Parse an object body.  Consumes the opening ``{``."""
        assert self._peek() == "{"
        self._advance()  # consume '{'

        fields: dict = {}

        while True:
            self._skip_ws()
            ch = self._peek()

            if ch is None:
                logger.warning(
                    "Unexpected end of input while parsing object (missing '}')"
                )
                break
            if ch == "}":
                self._advance()
                break

            # -- read field name --
            field_name = self._read_identifier()
            if not field_name:
                # Unexpected character — skip it to avoid infinite loop
                logger.warning(
                    "Unexpected character %r at position %d, skipping",
                    ch,
                    self.pos,
                )
                self._advance()
                continue

            self._skip_spaces()

            # Expect ':'
            if self._peek() == ":":
                self._advance()
            else:
                logger.warning(
                    "Expected ':' after field name %r at position %d",
                    field_name,
                    self.pos,
                )

            self._skip_spaces()

            # -- determine the field value --
            ch = self._peek()

            if ch == "{":
                # Bitfield
                fields[field_name] = FieldBitfield(field_name, self._parse_object())
            elif ch == "[":
                # Array
                fields[field_name] = FieldArray(field_name, self._parse_array())
            elif ch == "\n" or ch is None:
                # Empty value  (e.g., ``payload: ``)
                fields[field_name] = FieldString(field_name, "")
                if ch == "\n":
                    self._advance()
            else:
                # Could be  ``TypeName { … }``  or a plain scalar
                saved = self.pos
                maybe_name = self._read_qualified_name()
                self._skip_spaces()

                if self._peek() == "{":
                    fields[field_name] = FieldUnit(field_name, maybe_name, self._parse_object())
                else:
                    # Plain scalar — rewind and grab the whole line
                    self.pos = saved
                    fields[field_name] = FieldString(field_name, self._read_line_rest())

        return fields

    def _parse_array(self) -> list[ParseTreeField]:
        """Parse an array body.  Consumes the opening ``[``."""
        assert self._peek() == "["
        self._advance()  # consume '['

        items: list[ParseTreeField] = []

        while True:
            self._skip_ws()
            ch = self._peek()

            if ch is None:
                logger.warning(
                    "Unexpected end of input while parsing array (missing ']')"
                )
                break
            if ch == "]":
                self._advance()
                break

            item = self._parse_value_in_context()
            items.append(item)

        return items
