from src.logger import logger
from src.utils import compute_text_stats
from functools import cache
import re
from typing import Any

# ---------------------------------------------------------------------------
# Interval-based spec summarization
# ---------------------------------------------------------------------------

@cache
def normalize_str(s: str):
    norm_chars: list[str] = []
    pos_map: list[int] = []          # norm index -> original index
    in_ws = False
    for i, ch in enumerate(s):
        if ch in " \t\n\r\f\v":
            if not in_ws:
                norm_chars.append(" ")
                pos_map.append(i)
                in_ws = True
        else:
            norm_chars.append(ch)
            pos_map.append(i)
            in_ws = False
    norm_text = "".join(norm_chars)
    return norm_text, pos_map


def find_marker(spec_content: str, marker: str, start: int = 0) -> tuple[int, int] | None:
    """Find *marker* in *spec_content* starting from *start*.

    Matching is **whitespace-normalized**: all runs of whitespace in both
    the spec and the marker are collapsed to single spaces, and the marker
    is stripped before comparison.  The returned span ``(start, end)``
    refers to positions in the **original** (un-normalized) *spec_content*.

    Returns ``None`` if the marker cannot be found.
    """
    
    norm_text, pos_map = normalize_str(spec_content)

    # --- normalize marker ---
    norm_marker = re.sub(r'\s+', ' ', marker).strip()

    # --- map start to normalized space ---
    # Find the first norm index whose original position is >= start.
    norm_start = 0
    if start > 0:
        for j, orig in enumerate(pos_map):
            if orig >= start:
                norm_start = j
                break

    # --- search ---
    idx = norm_text.find(norm_marker, norm_start)
    if idx == -1:
        return None

    orig_start = pos_map[idx]
    norm_end_last = idx + len(norm_marker) - 1
    orig_end = pos_map[min(norm_end_last, len(pos_map) - 1)] + 1
    return (orig_start, orig_end)


def extract_intervals(spec_content: str, intervals: list[list[str]]) -> tuple[str, dict[str, Any]]:
    """Given a spec text and a list of [start_marker, end_marker] pairs,
    find each marker in the text and return the concatenation of all matched
    intervals.

    Overlapping or adjacent intervals are merged automatically.
    Intervals that cannot be resolved are logged and skipped.
    """

    logger.debug(f"Extracting intervals:\n{"\n".join(map(str,intervals))}")

    stats: dict[str, Any] = {
        "requested_intervals": len(intervals),
        "resolved_intervals": 0,
        "merged_intervals": 0,
        "skipped_invalid_pairs": 0,
        "missing_markers": 0,
    }
    resolved: list[tuple[int, int]] = []

    for idx, pair in enumerate(intervals):
        if len(pair) != 2:
            logger.warning(f"Interval {idx}: expected a pair [start, end], got {len(pair)} elements — skipping.")
            stats["skipped_invalid_pairs"] += 1
            continue

        start_hit = find_marker(spec_content, pair[0])
        if start_hit is None:
            logger.warning(f"Interval {idx}: start marker not found — skipping.\n  marker: {pair[0]!r}")
            stats["missing_markers"] += 1
            continue

        end_hit = find_marker(spec_content, pair[1], start=start_hit[0])
        if end_hit is None:
            logger.warning(f"Interval {idx}: end marker not found after start — skipping.\n  marker: {pair[1]!r}")
            stats["missing_markers"] += 1
            continue

        orig_start = start_hit[0]
        orig_end = end_hit[1]

        logger.debug(f"Interval {idx}: resolved to positions {orig_start}-{orig_end}.")
        resolved.append((orig_start, orig_end))
        stats["resolved_intervals"] += 1

    if not resolved:
        stats.update({
            "summary_chars": 0,
            "summary_lines": 0,
            "summary_nonempty_lines": 0,
            "source_chars": len(spec_content),
            "coverage_ratio": 0.0,
        })
        return "", stats

    # Sort by start position and merge overlapping/adjacent intervals.
    resolved.sort()
    merged: list[tuple[int, int]] = [resolved[0]]
    for start, end in resolved[1:]:
        prev_start, prev_end = merged[-1]
        if start <= prev_end:
            merged[-1] = (prev_start, max(prev_end, end))
        else:
            merged.append((start, end))

    logger.debug(f"Merged {len(resolved)} interval(s) into {len(merged)}.")

    parts = [spec_content[s:e] for s, e in merged]
    summary = "\n".join(parts)
    summary_stats = compute_text_stats(summary)
    stats.update({
        "merged_intervals": len(merged),
        "summary_chars": summary_stats["chars"],
        "summary_lines": summary_stats["lines"],
        "summary_nonempty_lines": summary_stats["nonempty_lines"],
        "source_chars": len(spec_content),
        "coverage_ratio": round(len(summary) / len(spec_content), 4) if spec_content else 0.0,
    })
    return summary, stats
