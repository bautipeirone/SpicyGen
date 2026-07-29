"""Targeted unit tests for functions prone to modification.

These are pure-function tests for utility and preprocessing logic that
is likely to be edited as the project evolves.
"""

import json
from pathlib import Path

import pytest

from src.utils import (
    compute_text_stats,
    generate_diff,
    strip_code_fences,
)
from src.tools.spicy.preprocess import clean_spicy_response
from src.tools.rfc import clean_rfc_text
from src.tools._3d.wrapper import (
    get_C_entrypoint_from_3d,
    get_error_callback_name,
    get_parser_entrypoint,
    normalize_identifier,
)
from src.usage import merge_usage_reports
from src.prompts import EXTRACT_INFO_SYSTEM_PROMPT, NEGATIVE_PACKETS_SYSTEM_PROMPT_TEMPLATE
from src.protocol import Protocol, ProtocolSpecType, ProtocolType
from preprocess_spec import (
    compute_info_stats,
    compute_negative_packet_stats,
    packet_field_description,
    packets_to_jsonl,
    parse_negative_packet_response,
)
from generate_grammar import build_metrics, infer_spec_source, SpecSource


# ---------------------------------------------------------------------------
# strip_code_fences / clean_spicy_response
# ---------------------------------------------------------------------------

class TestStripCodeFences:

    def test_strips_triple_backticks(self):
        assert strip_code_fences("```\nhello\n```") == "hello"

    def test_no_fences(self):
        assert strip_code_fences("hello world") == "hello world"

    def test_empty_string(self):
        assert strip_code_fences("") == ""

    def test_none_input(self):
        assert strip_code_fences(None) == ""

    def test_fences_with_language_tag(self):
        result = strip_code_fences("```spicy\nmodule foo;\n```")
        # strip_code_fences only strips the ``` — the language tag remains.
        assert "module" in result

    def test_preserves_internal_backticks(self):
        result = strip_code_fences("```\nsome `inline` code\n```")
        assert "`inline`" in result


class TestCleanSpicyResponse:

    def test_strips_fences_and_preamble(self):
        text = "```spicy\nSome preamble text\nmodule foo;\ntype Bar = unit {};\n```"
        result = clean_spicy_response(text)
        assert result.startswith("module foo;")

    def test_already_clean(self):
        text = "module foo;\ntype Bar = unit {};"
        result = clean_spicy_response(text)
        assert result == text

    def test_empty_input(self):
        assert clean_spicy_response("") == ""

    def test_no_module_keyword(self):
        text = "```\nthis has no module keyword\n```"
        result = clean_spicy_response(text)
        # All lines get stripped because none start with "module".
        assert result == ""


# ---------------------------------------------------------------------------
# compute_text_stats
# ---------------------------------------------------------------------------

class TestComputeTextStats:

    def test_normal_text(self):
        text = "line one\n\nline three\n"
        stats = compute_text_stats(text)
        assert stats["chars"] == len(text)
        assert stats["lines"] == 3
        assert stats["nonempty_lines"] == 2

    def test_empty_string(self):
        stats = compute_text_stats("")
        assert stats["chars"] == 0
        assert stats["lines"] == 0

    def test_none_input(self):
        stats = compute_text_stats(None)
        assert stats["chars"] == 0

    def test_single_line_no_newline(self):
        stats = compute_text_stats("hello")
        assert stats["lines"] == 1
        assert stats["nonempty_lines"] == 1


# ---------------------------------------------------------------------------
# compute_info_stats
# ---------------------------------------------------------------------------

class TestComputeInfoStats:

    def test_fully_populated(self):
        info = {
            "type": "binary",
            "port": 53,
            "transport": ["udp", "tcp"],
            "layer": "application",
            "encoding": None,
            "checksum": None,
            "parser_arguments": [
                {
                    "name": "frame_length",
                    "type": "uint",
                    "description": "Configured frame length.",
                }
            ],
        }
        stats = compute_info_stats(info)
        assert stats["expected_fields"] == 7
        assert stats["present_fields"] == 7
        assert stats["populated_fields"] == 5
        assert stats["null_or_empty_fields"] == 2
        assert stats["missing_fields"] == []
        assert stats["transport_count"] == 2
        assert stats["parser_argument_count"] == 1
        assert stats["valid_json_object"] is True

    def test_raw_response_fallback(self):
        info = {"raw_response": "some unparseable text"}
        stats = compute_info_stats(info)
        assert stats["valid_json_object"] is False
        assert stats["present_fields"] == 0

    def test_missing_fields_tracked(self):
        info = {"type": "text", "port": 80}
        stats = compute_info_stats(info)
        assert "transport" in stats["missing_fields"]
        assert "parser_arguments" in stats["missing_fields"]
        assert stats["parser_argument_count"] == 0

    def test_empty_parser_arguments_is_counted_as_empty_metadata(self):
        info = {
            "type": "binary",
            "port": None,
            "transport": [],
            "layer": None,
            "encoding": None,
            "checksum": None,
            "parser_arguments": [],
        }
        stats = compute_info_stats(info)
        assert stats["parser_argument_count"] == 0
        assert "parser_arguments" not in stats["missing_fields"]
        assert stats["null_or_empty_fields"] == 6


# ---------------------------------------------------------------------------
# negative packet generation helpers
# ---------------------------------------------------------------------------

class TestNegativePacketHelpers:

    def test_binary_packet_field_description_requests_byte_array(self):
        protocol = Protocol("dhcp", None, ProtocolType.BINARY, ProtocolSpecType.RFC, "2131", None)
        description = packet_field_description(protocol)

        assert "array of decimal byte values" in description
        assert "0 to 255" in description

    def test_text_packet_field_description_requests_raw_string(self):
        protocol = Protocol("http1.1", None, ProtocolType.TEXT, ProtocolSpecType.RFC, "2616", None)
        description = packet_field_description(protocol)

        assert "JSON string" in description
        assert "\\r\\n" in description

    def test_parse_jsonl_binary_packets(self):
        protocol = Protocol("dhcp", None, ProtocolType.BINARY, ProtocolSpecType.RFC, "2131", None)
        raw = (
            '{"name":"bad_cookie","protocol":"DHCP","packet":[1,2,3],'
            '"invalidity_category":"invalid_magic_cookie","reason":"bad cookie"}\n'
        )

        packets = parse_negative_packet_response(raw, protocol)

        assert packets[0]["packet"] == [1, 2, 3]

    def test_parse_json_array_text_packets(self):
        protocol = Protocol("http1.1", None, ProtocolType.TEXT, ProtocolSpecType.RFC, "2616", None)
        raw = json.dumps(
            {
                "name": "http_invalid_method",
                "protocol": "HTTP1.1",
                "packet": "TEST / HTTP/1.1\r\n",
                "invalidity_category": "invalid_request_method",
                "reason": "Wrong method used in HTTP request.",
            }
        )

        packets = parse_negative_packet_response(raw, protocol)

        assert packets[0]["packet"] == "TEST / HTTP/1.1\r\n"

    def test_packets_to_jsonl_escapes_text_packet(self):
        packet = {
            "name": "http_invalid_method",
            "protocol": "HTTP1.1",
            "packet": "TEST / HTTP/1.1\r\n",
            "invalidity_category": "invalid_request_method",
            "reason": "Wrong method used in HTTP request.",
        }

        jsonl = packets_to_jsonl([packet])

        assert jsonl.count("\n") == 1
        assert json.loads(jsonl)["packet"] == "TEST / HTTP/1.1\r\n"

    def test_compute_negative_packet_stats_counts_categories(self):
        stats = compute_negative_packet_stats([
            {"invalidity_category": "invalid_length"},
            {"invalidity_category": "invalid_length"},
            {"invalidity_category": "missing_terminator"},
        ])

        assert stats["packet_count"] == 3
        assert stats["invalidity_category_count"] == 2

# ---------------------------------------------------------------------------
# build_metrics
# ---------------------------------------------------------------------------

class TestBuildMetrics:

    def test_includes_iteration_log_and_summary_fields(self):
        metrics = build_metrics(
            model="test-model",
            protocol_name="dns",
            target_language_name="spicy",
            success=True,
            spec_source="preprocessed_summary",
            refinement_stats={
                "positive_packets": 12,
                "negative_packets": 3,
                "max_total_retries": 5,
                "best_iteration": 2,
                "termination_reason": "success",
                "first_successful_compile_iteration": 2,
                "iterations": [
                    {
                        "compiled": False,
                        "positive_accepted": 0,
                        "negative_rejected": 0,
                        "changed_from_previous": False,
                    },
                    {
                        "compiled": True,
                        "positive_accepted": 11,
                        "negative_rejected": 2,
                        "changed_from_previous": True,
                    },
                ],
                "retrieved_rfcs": None
            },
            elapsed_time_s=1.234,
            usage={"total": {"tokens": 42}},
        )

        assert metrics["model"] == "test-model"
        assert metrics["protocol"] == "dns"
        assert metrics["target_language"] == "spicy"
        assert metrics["success"] is True
        assert metrics["spec_source"] == "preprocessed_summary"
        assert metrics["positive_packets"] == 12
        assert metrics["negative_packets"] == 3
        assert metrics["max_total_retries"] == 5
        assert metrics["best_iteration"] == 2
        assert metrics["termination_reason"] == "success"
        assert metrics["first_successful_compile_iteration"] == 2
        assert metrics["iterations"][0]["compiled"] is False
        assert metrics["iterations"][1]["positive_accepted"] == 11
        assert metrics["iterations"][1]["negative_rejected"] == 2
        assert metrics["iterations"][1]["changed_from_previous"] is True
        assert "attempts" not in metrics
        assert metrics["time_elapsed_s"] == 1.23


class TestInferSpecSource:

    def test_defaults_to_protocol_spec(self):
        assert infer_spec_source(SpecSource.INDEX, None) == "index"

    def test_detects_custom_spec_file(self):
        assert infer_spec_source(SpecSource.SPEC_FILE, Path("/tmp/custom.txt")) == "/tmp/custom.txt"

    def test_detects_no_spec_file(self):
        assert infer_spec_source(SpecSource.NO_SPEC, None) == "no specification"


# ---------------------------------------------------------------------------
# generate_diff
# ---------------------------------------------------------------------------

class TestGenerateDiff:

    def test_identical_strings(self):
        assert generate_diff("hello\n", "hello\n") == ""

    def test_single_line_change(self):
        diff = generate_diff("line1\nline2\n", "line1\nline2_modified\n")
        assert "-line2" in diff
        assert "+line2_modified" in diff

    def test_addition(self):
        diff = generate_diff("a\n", "a\nb\n")
        assert "+b" in diff


# ---------------------------------------------------------------------------
# clean_rfc_text
# ---------------------------------------------------------------------------

class TestCleanRfcText:

    def test_removes_page_footers(self):
        text = "Some content\nAuthor                   [Page 1]\nMore content"
        result = clean_rfc_text(text)
        assert "[Page 1]" not in result
        assert "More content" in result

    def test_removes_rfc_headers(self):
        text = "RFC 1035                   Domain Names                November 1987\nContent here"
        result = clean_rfc_text(text)
        assert "RFC 1035" not in result
        assert "Content here" in result

    def test_collapses_blank_lines(self):
        text = "a\n\n\n\nb"
        result = clean_rfc_text(text)
        assert "\n\n" not in result


# ---------------------------------------------------------------------------
# 3D wrapper helpers
# ---------------------------------------------------------------------------

class TestNormalizeIdentifier:

    def test_single_word(self):
        assert normalize_identifier("hello") == "Hello"

    def test_underscored(self):
        assert normalize_identifier("my_type") == "MyType"

    def test_already_capitalized(self):
        assert normalize_identifier("Hello") == "Hello"


class TestGetParserEntrypoint:

    def test_finds_entrypoint(self):
        code = 'entrypoint typedef struct MyPacket {\n  UINT16 field;\n} MyPacket;'
        result = get_parser_entrypoint(code)
        assert result == "MyPacket"

    def test_no_entrypoint(self):
        code = "typedef struct Foo { UINT8 x; } Foo;"
        assert get_parser_entrypoint(code) is None


class TestGetCEntrypoint:

    def test_standard_case(self):
        result = get_C_entrypoint_from_3d("IPV4", "Header")
        assert result == "Ipv4CheckHeader"

    def test_underscored_filename(self):
        result = get_C_entrypoint_from_3d("my_file", "some_type")
        assert result == "MyFileCheckSomeType"


class TestGetErrorCallbackName:

    def test_generates_callback(self):
        assert get_error_callback_name("IPV4") == "IPV4EverParseError"


# ---------------------------------------------------------------------------
# merge_usage_reports
# ---------------------------------------------------------------------------

class TestMergeUsageReports:

    def test_merge_two_reports(self):
        r1 = {
            "total_usage": {"total_cost": 0.01, "prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
            "actual_usage": {"total_cost": 0.01, "prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
        }
        r2 = {
            "total_usage": {"total_cost": 0.02, "prompt_tokens": 200, "completion_tokens": 100, "total_tokens": 300},
            "actual_usage": {"total_cost": 0.02, "prompt_tokens": 200, "completion_tokens": 100, "total_tokens": 300},
        }
        merged = merge_usage_reports(r1, r2)
        assert merged is not None
        assert merged["total_usage"]["total_cost"] == pytest.approx(0.03)
        assert merged["total_usage"]["prompt_tokens"] == 300
        assert merged["total_usage"]["total_tokens"] == 450

    def test_merge_with_none(self):
        r1 = {
            "total_usage": {"total_cost": 0.01, "prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
            "actual_usage": None,
        }
        merged = merge_usage_reports(r1, None)
        assert merged is not None
        assert merged["total_usage"]["total_cost"] == pytest.approx(0.01)

    def test_all_none_returns_none(self):
        assert merge_usage_reports(None, None) is None

    def test_single_report(self):
        r1 = {
            "total_usage": {"total_cost": 0.05, "prompt_tokens": 500, "completion_tokens": 250, "total_tokens": 750},
            "actual_usage": {"total_cost": 0.04, "prompt_tokens": 400, "completion_tokens": 200, "total_tokens": 600},
        }
        merged = merge_usage_reports(r1)
        assert merged["total_usage"]["total_tokens"] == 750
