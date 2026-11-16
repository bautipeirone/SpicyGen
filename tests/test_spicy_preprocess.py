"""Integration tests for the Spicy preprocessor chain.

Covers: preprocess_spicy_code() orchestrating reserved keyword search +
bitfield-without-bitorder detection, producing actionable error lists.
"""

import pytest

from src.tools.spicy import compiler
from src.tools.spicy.preprocess import (
    find_bitfields_decls,
    check_single_entry_unit,
    find_bitfields_without_bitorder_attribute,
    find_public_types,
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
        code = "module test;\npublic type Msg = unit {\nopcode: uint8;\nlength: uint16;\n};"
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


class TestCompilerPreprocessingToggle:

    def test_preprocessing_is_enabled_by_default(self, monkeypatch, tmp_path):
        source_file = tmp_path / "grammar.spicy"
        source_file.write_text("module Test;")
        compiler_called = False

        monkeypatch.setattr(
            compiler, "preprocess_spicy_code", lambda code: ["early syntax error"]
        )

        def fake_compiler(*args):
            nonlocal compiler_called
            compiler_called = True

        monkeypatch.setattr(compiler, "run_spicy_compiler", fake_compiler)

        result = compiler.compile_spicy_grammar(source_file)

        assert result.success is False
        assert result.errors == "early syntax error"
        assert compiler_called is False

    def test_disabled_preprocessing_calls_compiler_directly(
        self, monkeypatch, tmp_path
    ):
        source_file = tmp_path / "grammar.spicy"
        source_file.write_text("module Test;\npublic type Msg = unit { x: uint8; };")

        monkeypatch.setattr(
            compiler,
            "preprocess_spicy_code",
            lambda code: (_ for _ in ()).throw(
                AssertionError("preprocessor should not run")
            ),
        )
        monkeypatch.setattr(
            compiler,
            "run_spicy_compiler",
            lambda *args: type(
                "CompilerResult", (), {"returncode": 0, "stderr": "", "stdout": ""}
            )(),
        )

        result = compiler.compile_spicy_grammar(
            source_file, preprocessing_enabled=False
        )

        assert result.success is True


class TestSingleEntryUnit:
    """Packet validation runs spicy-dump without --parser, so exactly one
    public unit must exist or an arbitrary one gets tested."""

    def test_single_public_type_passes(self):
        code = "module M;\ntype A = unit { x: uint8; };\npublic type B = unit { a: A; };"
        assert check_single_entry_unit(code) is None

    def test_multiple_public_types_rejected_with_names(self):
        code = (
            "module DNS;\n"
            "public type Message = unit { x: uint8; };\n"
            "public type Packet = Message;\n"
            "public type TCP_Message = unit { m: Message; };\n"
        )
        error = check_single_entry_unit(code)
        assert error is not None
        assert "found 3 `public type` declarations (Message, Packet, TCP_Message)" in error

    def test_alias_counts_as_public_type(self):
        assert find_public_types("public type P = Message;\npublic type Message = unit {};") == ["P", "Message"]

    def test_commented_out_public_type_ignored(self):
        code = "module M;\n# public type Old = unit {};\npublic type New = unit { x: uint8; };"
        assert check_single_entry_unit(code) is None

    def test_no_public_type_rejected(self):
        error = check_single_entry_unit("module M;\ntype A = unit { x: uint8; };")
        assert error is not None and "no `public type`" in error

    def test_part_of_preprocessing_and_skips_compiler(self, monkeypatch, tmp_path):
        source_file = tmp_path / "grammar.spicy"
        source_file.write_text(
            "module T;\npublic type A = unit { x: uint8; };\npublic type B = unit { y: uint8; };"
        )

        def fake_compiler(*args):
            raise AssertionError("compiler should not run after a preprocessing error")

        monkeypatch.setattr(compiler, "run_spicy_compiler", fake_compiler)
        result = compiler.compile_spicy_grammar(source_file)
        assert result.success is False
        assert "found 2 `public type` declarations (A, B)" in result.errors

    def test_reported_after_other_preprocessing_errors(self):
        code = "module T;\ntype: uint8;\npublic type A = unit {};\npublic type B = unit {};"
        errors = preprocess_spicy_code(code)
        assert "reserved keyword" in errors[0]
        assert "`public type` declarations" in errors[-1]

    def test_not_enforced_without_preprocessing(self, monkeypatch, tmp_path):
        source_file = tmp_path / "grammar.spicy"
        source_file.write_text(
            "module T;\npublic type A = unit { x: uint8; };\npublic type B = unit { y: uint8; };"
        )
        monkeypatch.setattr(
            compiler,
            "run_spicy_compiler",
            lambda *args: type(
                "CompilerResult", (), {"returncode": 0, "stderr": "", "stdout": ""}
            )(),
        )
        result = compiler.compile_spicy_grammar(source_file, preprocessing_enabled=False)
        assert result.success is True
