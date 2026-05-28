"""Integration tests for interval-based spec extraction.

Covers: find_marker() + extract_intervals() working together on
realistic spec-like documents, including whitespace normalization,
interval merging, and stats generation.
"""

import pytest

from src.tools.extraction import extract_intervals, find_marker


# ---------------------------------------------------------------------------
# find_marker — foundational search
# ---------------------------------------------------------------------------

class TestFindMarker:
    """Test the whitespace-normalized marker search."""

    def test_exact_match(self, sample_spec_text):
        result = find_marker(sample_spec_text, "2. Message Format")
        assert result is not None
        start, end = result
        assert sample_spec_text[start:end].strip() == "2. Message Format"

    def test_whitespace_normalized_match(self, sample_spec_text):
        # Extra spaces in the marker — should still match.
        result = find_marker(sample_spec_text, "2.  Message   Format")
        assert result is not None

    def test_marker_not_found(self, sample_spec_text):
        result = find_marker(sample_spec_text, "99. Nonexistent Section")
        assert result is None

    def test_search_with_offset(self, sample_spec_text):
        # Find "Flags" starting from different offsets.
        first = find_marker(sample_spec_text, "Flags")
        assert first is not None
        second = find_marker(sample_spec_text, "Flags", start=first[1])
        # There should be at least one more occurrence ("Flags" in the field description).
        assert second is not None
        assert second[0] > first[0]

    def test_empty_marker(self):
        result = find_marker("some text", "")
        # Empty marker matches at position 0.
        assert result is not None

    def test_marker_with_newlines(self, sample_spec_text):
        # The marker spans a line break in the source.
        result = find_marker(sample_spec_text, "Header\n\n   The header")
        assert result is not None


# ---------------------------------------------------------------------------
# extract_intervals — full pipeline
# ---------------------------------------------------------------------------

class TestExtractIntervals:
    """Test the full interval extraction and merging pipeline."""

    def test_single_interval(self, sample_spec_text):
        intervals = [["2.1. Header", "3. References"]]
        summary, stats = extract_intervals(sample_spec_text, intervals)

        assert len(summary) > 0
        assert "Header" in summary
        assert "OpCode" in summary
        assert stats["resolved_intervals"] == 1
        assert stats["merged_intervals"] == 1

    def test_multiple_non_overlapping_intervals(self, sample_spec_text):
        intervals = [
            ["2.1. Header", "2.2. Payload"],
            ["2.2. Payload", "3. References"],
        ]
        summary, stats = extract_intervals(sample_spec_text, intervals)

        assert "Header" in summary
        assert "Payload" in summary
        assert stats["resolved_intervals"] == 2

    def test_overlapping_intervals_are_merged(self, sample_spec_text):
        intervals = [
            ["2. Message Format", "2.2. Payload"],
            ["2.1. Header", "3. References"],
        ]
        summary, stats = extract_intervals(sample_spec_text, intervals)

        assert stats["resolved_intervals"] == 2
        assert stats["merged_intervals"] == 1  # They overlap, so merged into one.

    def test_unresolvable_interval_skipped(self, sample_spec_text):
        intervals = [
            ["DOES_NOT_EXIST_START", "DOES_NOT_EXIST_END"],
            ["2. Message Format", "3. References"],
        ]
        summary, stats = extract_intervals(sample_spec_text, intervals)

        assert stats["resolved_intervals"] == 1
        assert stats["missing_markers"] >= 1
        assert "Message Format" in summary

    def test_invalid_pair_length(self, sample_spec_text):
        intervals = [
            ["only_one_element"],  # Invalid pair
            ["2. Message Format", "3. References"],
        ]
        summary, stats = extract_intervals(sample_spec_text, intervals)

        assert stats["skipped_invalid_pairs"] == 1
        assert stats["resolved_intervals"] == 1

    def test_all_unresolvable_returns_empty(self, sample_spec_text):
        intervals = [["NOPE_START", "NOPE_END"]]
        summary, stats = extract_intervals(sample_spec_text, intervals)

        assert summary == ""
        assert stats["resolved_intervals"] == 0
        assert stats["coverage_ratio"] == 0.0

    def test_coverage_ratio_is_sensible(self, sample_spec_text):
        intervals = [["2. Message Format", "3. References"]]
        _, stats = extract_intervals(sample_spec_text, intervals)

        assert 0.0 < stats["coverage_ratio"] < 1.0
        assert stats["summary_chars"] > 0
        assert stats["source_chars"] == len(sample_spec_text)

    def test_stats_include_line_counts(self, sample_spec_text):
        intervals = [["2.1. Header", "2.2. Payload"]]
        _, stats = extract_intervals(sample_spec_text, intervals)

        assert stats["summary_lines"] > 0
        assert stats["summary_nonempty_lines"] > 0
        assert stats["summary_nonempty_lines"] <= stats["summary_lines"]
