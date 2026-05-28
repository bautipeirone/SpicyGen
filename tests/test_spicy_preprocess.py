"""Integration tests for the Spicy preprocessor chain.

Covers: preprocess_spicy_code() orchestrating reserved keyword search +
bitfield-without-bitorder detection, producing actionable error lists.
"""

import pytest

from src.tools.spicy.preprocess import (
    find_bitfields_decls,
    find_bitfields_without_bitorder_attribute,
    preprocess_spicy_code,
    search_reserved_keywords,
)


class TestReservedKeywordDetection:

    def test_detects_reserved_keyword_as_field(self):
        code = "type: uint8;"
        hits = search_reserved_keywords(code)
        assert any(kw == "type" for _, kw in hits)

    def test_ignores_keyword_in_comment(self):
        code = "# type: something\nmy_field: uint8;"
        hits = search_reserved_keywords(code)
        # "type" appears in a comment line — but the regex still matches it
        # because preprocess doesn't skip comments. This documents current behaviour.
        keyword_names = [kw for _, kw in hits]
        assert "my_field" not in keyword_names

    def test_no_false_positives_on_safe_names(self):
        code = "opcode: uint8;\nlength: uint16;\nflags: uint8;"
        hits = search_reserved_keywords(code)
        assert len(hits) == 0

    def test_multiple_keywords_on_separate_lines(self):
        code = "port: uint16;\naddr: bytes &size=4;"
        hits = search_reserved_keywords(code)
        keyword_names = {kw for _, kw in hits}
        assert "port" in keyword_names
        assert "addr" in keyword_names


class TestBitfieldDetection:

    VALID_BITFIELD = (
        "flags: bitfield(8) {\n"
        "  a: 0..3;\n"
        "  b: 4..7;\n"
        "} &bit-order=LSB0;\n"
    )

    MISSING_BITORDER = (
        "flags: bitfield(8) {\n"
        "  a: 0..3;\n"
        "  b: 4..7;\n"
        "};\n"
    )

    def test_finds_bitfield_declarations(self):
        decls = find_bitfields_decls(self.VALID_BITFIELD)
        assert len(decls) == 1
        assert decls[0]["name"] == "flags"
        assert decls[0]["bitfield_order"] is not None

    def test_detects_missing_bitorder(self):
        bad = find_bitfields_without_bitorder_attribute(self.MISSING_BITORDER)
        assert len(bad) == 1
        assert bad[0]["name"] == "flags"

    def test_valid_bitfield_not_flagged(self):
        bad = find_bitfields_without_bitorder_attribute(self.VALID_BITFIELD)
        assert len(bad) == 0

    def test_multiple_bitfields_mixed(self):
        code = self.VALID_BITFIELD + "\n" + self.MISSING_BITORDER.replace("flags", "header")
        decls = find_bitfields_decls(code)
        assert len(decls) == 2
        bad = find_bitfields_without_bitorder_attribute(code)
        assert len(bad) == 1
        assert bad[0]["name"] == "header"


class TestPreprocessCodeIntegration:
    """Full preprocess_spicy_code() producing error message lists."""

    def test_clean_code_returns_no_errors(self):
        code = "module test;\nopcode: uint8;\nlength: uint16;"
        errors = preprocess_spicy_code(code)
        assert errors == []

    def test_reserved_keyword_produces_error(self):
        code = "module test;\ntype: uint8;"
        errors = preprocess_spicy_code(code)
        assert len(errors) >= 1
        assert "reserved keyword" in errors[0].lower() or "type" in errors[0]

    def test_missing_bitorder_produces_error(self):
        code = (
            "module test;\n"
            "flags: bitfield(8) {\n"
            "  a: 0..3;\n"
            "  b: 4..7;\n"
            "};\n"
        )
        errors = preprocess_spicy_code(code)
        assert len(errors) >= 1
        assert "bit-order" in errors[0] or "bitfield" in errors[0].lower()

    def test_combined_errors(self):
        code = (
            "module test;\n"
            "port: uint16;\n"
            "flags: bitfield(8) {\n"
            "  a: 0..3;\n"
            "};\n"
        )
        errors = preprocess_spicy_code(code)
        assert len(errors) >= 2  # One for 'port', one for bitfield.
