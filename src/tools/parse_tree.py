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
type ParseTree = dict[str, dict | list | str]

@dataclass(frozen=True)
class ParseTreeField:
    unit_name: str | None
    field_name: str
    field_value: Any
    field_type: type

    def show_field_value(self) -> str:
        field_value_repr = str(self.field_value)
        if self.field_type == str:
            return field_value_repr[:25] + "..." if len(field_value_repr) > 25 else field_value_repr
        return field_value_repr

    def __repr__(self) -> str:
        repr = f"{self.field_name} = {self.show_field_value()}"
        if self.unit_name is not None:
            repr += f" in {self.unit_name}"
        return repr


@dataclass(frozen=True)
class ParseTreeMismatch:
    field: ParseTreeField
    ref_field: ParseTreeField

    def __repr__(self) -> str:
        report = "Mismatch:\n"
        report += f"{self.field} != {self.ref_field}"
        return report


def match_by_field_name(pt: ParseTree, ref: ParseTree) -> Generator[ParseTreeMismatch, None, None]:
    pt = pt.copy()
    ref = ref.copy()

    pt_unit_name = pt.pop("_type", None)
    ref_unit_name = ref.pop("_type", None)
    
    pt_field_names = set(pt.keys())
    ref_field_names = set(ref.keys())
    common_field_names = pt_field_names & ref_field_names
    for field_name in common_field_names:
        pt_value = pt[field_name]
        ref_value = ref[field_name]
        pt_type = type(pt_value)
        ref_type = type(ref_value)
        pt_field = ParseTreeField(pt_unit_name, field_name, pt_value, pt_type)
        ref_field = ParseTreeField(ref_unit_name, field_name, ref_value, ref_type)
        if pt_type != ref_type:
            yield ParseTreeMismatch(pt_field, ref_field)
        elif pt_value != ref_value:
            yield ParseTreeMismatch(pt_field, ref_field)


def match_by_field_order(pt: ParseTree, ref: ParseTree) -> Generator[ParseTreeMismatch, None, None]:
    pt = pt.copy()
    ref = ref.copy()

    pt_unit_name = pt.pop("_type", None)
    ref_unit_name = ref.pop("_type", None)

    for (pt_key, pt_value), (ref_key, ref_value) in zip(pt.items(), ref.items()):
        pt_type = type(pt_value)
        ref_type = type(ref_value)
        pt_field = ParseTreeField(pt_unit_name, pt_key, pt_value, pt_type)
        ref_field = ParseTreeField(ref_unit_name, ref_key, ref_value, ref_type)
        if pt_type != ref_type:
            yield ParseTreeMismatch(pt_field, ref_field)
        elif pt_value != ref_value:
            yield ParseTreeMismatch(pt_field, ref_field)


def match_parse_trees(pt: ParseTree, ref: ParseTree) -> Generator[ParseTreeMismatch, None, None]:
    fields = set()
    for mismatch in match_by_field_name(pt, ref):
        field_name = mismatch.field.field_name
        if field_name in fields:
            continue
        fields.add(field_name)
        yield mismatch
    for mismatch in match_by_field_order(pt, ref):
        field_name = mismatch.field.field_name
        if field_name in fields:
            continue
        fields.add(field_name)
        yield mismatch


def parse_spicy_output(text: str) -> dict | list | str:
    """Parse a single ``spicy-dump`` output block into a Python structure.

    Returns a *dict* for objects (with an optional ``"_type"`` key),
    a *list* for arrays, or a *str* for scalar values.
    """
    parser = _Parser(text)
    result = parser.parse_top_level()
    logger.debug(f"Parsed spicy output: top-level type = {type(result).__name__}")
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

    def parse_top_level(self) -> dict | list | str:
        """Entry point: parse the top-level value."""
        self._skip_ws()
        if self._at_end():
            return {}
        return self._parse_value_in_context()

    def _parse_value_in_context(self) -> dict | list | str:
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
            return self._parse_object(type_name=None)
        if ch is None or ch in "}]":
            return ""

        # Try to read a qualified name followed by '{'
        saved = self.pos
        name = self._read_qualified_name()
        self._skip_spaces()

        if self._peek() == "{":
            return self._parse_object(type_name=name)

        # Not an object — fall back to reading the rest of the line as scalar
        self.pos = saved
        return self._read_line_rest()

    def _parse_object(self, type_name: str | None) -> dict:
        """Parse an object body.  Consumes the opening ``{``."""
        assert self._peek() == "{"
        self._advance()  # consume '{'

        result: dict = {}
        if type_name:
            result["_type"] = type_name

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
                # Anonymous nested object
                result[field_name] = self._parse_object(type_name=None)
            elif ch == "[":
                # Array
                result[field_name] = self._parse_array()
            elif ch == "\n" or ch is None:
                # Empty value  (e.g., ``payload: ``)
                result[field_name] = ""
                if ch == "\n":
                    self._advance()
            else:
                # Could be  ``TypeName { … }``  or a plain scalar
                saved = self.pos
                maybe_name = self._read_qualified_name()
                self._skip_spaces()

                if self._peek() == "{":
                    result[field_name] = self._parse_object(type_name=maybe_name)
                else:
                    # Plain scalar — rewind and grab the whole line
                    self.pos = saved
                    result[field_name] = self._read_line_rest()

        return result

    def _parse_array(self) -> list:
        """Parse an array body.  Consumes the opening ``[``."""
        assert self._peek() == "["
        self._advance()  # consume '['

        items: list = []

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
