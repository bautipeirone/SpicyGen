"""Summarise cross-protocol packet rejection results.

Rows where a parser is tested with its own protocol are used as a sanity check
and excluded from the cross-protocol rankings.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Iterable

import pandas as pd


DEFAULT_RESULTS = Path(__file__).with_name("cross-packets-results.csv")
REQUIRED_COLUMNS = {"parser", "protocol", "failed", "total"}


def parser_name(parser: str) -> str:
    """Return a short parser name while preserving dots in protocol names."""
    name = Path(parser).name
    return name.removesuffix(".hlto")


def infer_parser_protocol(parser: str, protocols: Iterable[str]) -> str | None:
    """Infer the parser protocol from names such as ``tm-sdlp-gpt-5.4``."""
    name = parser_name(parser)
    candidates = [
        protocol
        for protocol in protocols
        if name == protocol
        or name.startswith(f"{protocol}-")
        or name.startswith(f"{protocol}_")
    ]
    return max(candidates, key=len) if candidates else None


def load_results(path: Path) -> pd.DataFrame:
    """Load, validate, and annotate a result matrix."""
    df = pd.read_csv(path)
    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        raise ValueError(f"missing required columns: {', '.join(sorted(missing))}")
    if df.empty:
        raise ValueError("the results file is empty")

    df = df.loc[:, ["parser", "protocol", "failed", "total"]].copy()
    for column in ("parser", "protocol"):
        if df[column].isna().any():
            raise ValueError(f"column {column!r} contains empty values")
        df[column] = df[column].astype(str).str.strip()
        if df[column].eq("").any():
            raise ValueError(f"column {column!r} contains empty values")

    for column in ("failed", "total"):
        numeric = pd.to_numeric(df[column], errors="coerce")
        if numeric.isna().any() or (numeric % 1 != 0).any():
            raise ValueError(f"column {column!r} must contain integers")
        df[column] = numeric.astype("int64")

    invalid = (df["total"] <= 0) | (df["failed"] < 0) | (
        df["failed"] > df["total"]
    )
    if invalid.any():
        rows = ", ".join(str(index + 2) for index in df.index[invalid][:5])
        raise ValueError(f"invalid failed/total values on CSV row(s): {rows}")

    duplicates = df.duplicated(["parser", "protocol"], keep=False)
    if duplicates.any():
        pairs = df.loc[duplicates, ["parser", "protocol"]].drop_duplicates().head(5)
        rendered = ", ".join(f"{row.parser}/{row.protocol}" for row in pairs.itertuples())
        raise ValueError(f"duplicate parser/protocol rows: {rendered}")

    protocols = df["protocol"].unique().tolist()
    df["parser_name"] = df["parser"].map(parser_name)
    df["parser_protocol"] = df["parser"].map(
        lambda value: infer_parser_protocol(value, protocols)
    )
    df["rejection_rate"] = df["failed"] / df["total"]
    df["fully_rejected"] = df["failed"].eq(df["total"])
    df["fully_accepted"] = df["failed"].eq(0)
    df["partially_rejected"] = ~df["fully_rejected"] & ~df["fully_accepted"]
    df["is_self"] = df["protocol"].eq(df["parser_protocol"])
    return df


def protocol_summary(cross: pd.DataFrame) -> pd.DataFrame:
    """Aggregate results by the protocol supplying the packets."""
    summary = cross.groupby("protocol", as_index=False).agg(
        packet_count_min=("total", "min"),
        packet_count_max=("total", "max"),
        eligible_parsers=("parser", "nunique"),
        rejected_evaluations=("failed", "sum"),
        packet_evaluations=("total", "sum"),
        full_rejecting_parsers=("fully_rejected", "sum"),
        any_rejecting_parsers=("failed", lambda values: values.gt(0).sum()),
        fully_accepting_parsers=("fully_accepted", "sum"),
        partial_parsers=("partially_rejected", "sum"),
    )
    summary["rejection_rate"] = (
        summary["rejected_evaluations"] / summary["packet_evaluations"]
    )
    summary["full_rejection_rate"] = (
        summary["full_rejecting_parsers"] / summary["eligible_parsers"]
    )
    summary["any_rejection_rate"] = (
        summary["any_rejecting_parsers"] / summary["eligible_parsers"]
    )
    return summary


def parser_summary(cross: pd.DataFrame) -> pd.DataFrame:
    """Aggregate foreign-packet results by parser."""
    summary = cross.groupby("parser", as_index=False).agg(
        parser_name=("parser_name", "first"),
        parser_protocol=("parser_protocol", "first"),
        foreign_protocols=("protocol", "nunique"),
        rejected_packets=("failed", "sum"),
        tested_packets=("total", "sum"),
        balanced_rejection_rate=("rejection_rate", "mean"),
        fully_rejected_protocols=("fully_rejected", "sum"),
        any_rejected_protocols=("failed", lambda values: values.gt(0).sum()),
        fully_accepted_protocols=("fully_accepted", "sum"),
        partially_rejected_protocols=("partially_rejected", "sum"),
    )
    summary["packet_rejection_rate"] = (
        summary["rejected_packets"] / summary["tested_packets"]
    )
    return summary


def format_percent(value: float) -> str:
    return f"{value:.1%}"


def format_ratio(numerator: int, denominator: int, rate: float | None = None) -> str:
    if rate is None:
        rate = numerator / denominator if denominator else 0.0
    return f"{numerator}/{denominator} ({rate:.1%})"


def format_packet_count(row: pd.Series) -> str:
    minimum = int(row["packet_count_min"])
    maximum = int(row["packet_count_max"])
    return str(minimum) if minimum == maximum else f"{minimum}-{maximum}"


def text_table(headers: list[str], rows: list[list[object]]) -> str:
    """Render a dependency-free, terminal-friendly table."""
    if not rows:
        return "(none)"
    rendered = [[str(cell) for cell in row] for row in rows]
    widths = [
        max(len(headers[index]), *(len(row[index]) for row in rendered))
        for index in range(len(headers))
    ]
    header = "  ".join(value.ljust(widths[index]) for index, value in enumerate(headers))
    rule = "  ".join("-" * width for width in widths)
    body = [
        "  ".join(value.ljust(widths[index]) for index, value in enumerate(row))
        for row in rendered
    ]
    return "\n".join([header, rule, *body])


def data_quality_notes(df: pd.DataFrame) -> list[str]:
    notes: list[str] = []
    unresolved = sorted(df.loc[df["parser_protocol"].isna(), "parser_name"].unique())
    if unresolved:
        notes.append(
            "Could not infer the own protocol for: " + ", ".join(unresolved)
            + ". Their rows are all treated as cross-protocol."
        )

    expected = {
        (parser, protocol)
        for parser in df["parser"].unique()
        for protocol in df["protocol"].unique()
    }
    actual = set(zip(df["parser"], df["protocol"]))
    missing = expected - actual
    if missing:
        notes.append(f"The parser/protocol matrix is missing {len(missing)} pair(s).")

    inconsistent = df.groupby("protocol")["total"].nunique()
    inconsistent = inconsistent[inconsistent > 1].index.tolist()
    if inconsistent:
        notes.append(
            "Packet-suite size differs between parsers for: " + ", ".join(inconsistent)
            + ". Raw totals are still shown, but comparisons need care."
        )
    return notes


def reciprocal_asymmetries(
    cross: pd.DataFrame, excluded_protocols: set[str]
) -> pd.DataFrame:
    """Find A-on-B versus B-on-A rejection-rate differences."""
    usable = cross[
        cross["parser_protocol"].notna()
        & ~cross["parser_protocol"].isin(excluded_protocols)
        & ~cross["protocol"].isin(excluded_protocols)
    ]
    lookup = {
        (row.parser_protocol, row.protocol): row
        for row in usable.itertuples()
    }
    records: list[dict[str, object]] = []
    parser_protocols = sorted(usable["parser_protocol"].unique())
    for protocol_a_index, protocol_a in enumerate(parser_protocols):
        for protocol_b in parser_protocols[protocol_a_index + 1 :]:
            forward = lookup.get((protocol_a, protocol_b))
            reverse = lookup.get((protocol_b, protocol_a))
            if forward is None or reverse is None:
                continue
            gap = abs(forward.rejection_rate - reverse.rejection_rate)
            if gap < 0.5:
                continue
            records.append(
                {
                    "protocol_a": protocol_a,
                    "protocol_b": protocol_b,
                    "a_on_b_failed": forward.failed,
                    "a_on_b_total": forward.total,
                    "a_on_b_rate": forward.rejection_rate,
                    "b_on_a_failed": reverse.failed,
                    "b_on_a_total": reverse.total,
                    "b_on_a_rate": reverse.rejection_rate,
                    "gap": gap,
                    "evidence": forward.total + reverse.total,
                }
            )
    if not records:
        return pd.DataFrame()
    return pd.DataFrame(records).sort_values(
        ["gap", "evidence"], ascending=[False, False]
    )


def build_report(df: pd.DataFrame, input_path: Path, top: int) -> str:
    self_rows = df[df["is_self"]]
    cross = df[~df["is_self"]]
    if cross.empty:
        raise ValueError("there are no cross-protocol rows to analyse")

    protocols = protocol_summary(cross)
    parsers = parser_summary(cross)
    overall_failed = int(cross["failed"].sum())
    overall_total = int(cross["total"].sum())
    full_pair_count = int(cross["fully_rejected"].sum())
    partial_pair_count = int(cross["partially_rejected"].sum())
    accepted_pair_count = int(cross["fully_accepted"].sum())

    by_breadth = protocols.sort_values(
        ["full_rejecting_parsers", "any_rejecting_parsers", "rejection_rate"],
        ascending=False,
    )
    by_raw = protocols.sort_values(
        ["rejected_evaluations", "rejection_rate"], ascending=False
    )
    by_rate = protocols.sort_values(
        ["rejection_rate", "full_rejecting_parsers"], ascending=False
    )
    breadth_best = by_breadth.iloc[0]
    raw_best = by_raw.iloc[0]
    rate_best = by_rate.iloc[0]

    strict = parsers.sort_values(
        ["fully_rejected_protocols", "balanced_rejection_rate", "packet_rejection_rate"],
        ascending=False,
    )
    accepting = parsers.sort_values(
        ["fully_accepted_protocols", "balanced_rejection_rate"],
        ascending=[False, True],
    )

    self_passing_parser_ids = set(self_rows.loc[self_rows["failed"].eq(0), "parser"])
    strict_self_valid = strict[strict["parser"].isin(self_passing_parser_ids)]
    if strict_self_valid.empty:
        validated_parser_headline = (
            "- No parser has a passing own-protocol row, so strictness cannot be "
            "distinguished from an always-reject failure mode."
        )
    else:
        best_validated_count = int(strict_self_valid.iloc[0]["fully_rejected_protocols"])
        best_validated = strict_self_valid[
            strict_self_valid["fully_rejected_protocols"].eq(best_validated_count)
        ]
        validated_parser_headline = (
            "- Strictest parsers that also pass their own-protocol sanity check: "
            + ", ".join(best_validated["parser_name"])
            + f"; each fully rejected {best_validated_count}/"
            f"{int(best_validated.iloc[0]['foreign_protocols'])} foreign protocols."
        )

    lines = [
        "CROSS-PROTOCOL PARSER ANALYSIS",
        f"Input: {input_path}",
        (
            f"Scope: {df['parser'].nunique()} parsers, "
            f"{df['protocol'].nunique()} packet protocols, {len(cross)} cross rows "
            f"({len(self_rows)} own-protocol rows excluded)."
        ),
        (
            "Overall: "
            + format_ratio(overall_failed, overall_total)
            + " packet/parser evaluations were rejected."
        ),
        "",
        "HEADLINES",
        (
            f"- Viability: {full_pair_count}/{len(cross)} ({full_pair_count / len(cross):.1%}) "
            f"foreign parser/protocol pairs fully rejected the source suite; "
            f"{partial_pair_count} were mixed and {accepted_pair_count} fully accepted it. "
            "Cross-protocol packets are useful as a broad supplement, not a complete negative-test strategy."
        ),
        (
            f"- Best rejection breadth: {breadth_best['protocol']} packets; "
            f"{int(breadth_best['full_rejecting_parsers'])}/"
            f"{int(breadth_best['eligible_parsers'])} parsers rejected every packet, "
            f"and {int(breadth_best['any_rejecting_parsers'])}/"
            f"{int(breadth_best['eligible_parsers'])} rejected at least one."
        ),
        (
            f"- Most rejected evaluations: {raw_best['protocol']} with "
            f"{int(raw_best['rejected_evaluations'])}/"
            f"{int(raw_best['packet_evaluations'])} ({raw_best['rejection_rate']:.1%}). "
            "This metric favours larger packet suites."
        ),
        (
            f"- Highest rejection rate: {rate_best['protocol']} at "
            f"{rate_best['rejection_rate']:.1%} across "
            f"{int(rate_best['packet_evaluations'])} evaluations."
        ),
        validated_parser_headline,
        (
            f"- Most accepting parser: {accepting.iloc[0]['parser_name']}; "
            f"{int(accepting.iloc[0]['fully_accepted_protocols'])}/"
            f"{int(accepting.iloc[0]['foreign_protocols'])} foreign protocols fully accepted."
        ),
        "",
        f"SOURCE PACKET PROTOCOLS — TOP {min(top, len(protocols))} BY REJECTION BREADTH",
    ]

    breadth_rows: list[list[object]] = []
    for _, row in by_breadth.head(top).iterrows():
        breadth_rows.append(
            [
                row["protocol"],
                format_packet_count(row),
                format_ratio(
                    int(row["full_rejecting_parsers"]),
                    int(row["eligible_parsers"]),
                    row["full_rejection_rate"],
                ),
                format_ratio(
                    int(row["any_rejecting_parsers"]),
                    int(row["eligible_parsers"]),
                    row["any_rejection_rate"],
                ),
                format_ratio(
                    int(row["rejected_evaluations"]),
                    int(row["packet_evaluations"]),
                    row["rejection_rate"],
                ),
            ]
        )
    lines.append(
        text_table(
            ["source", "packets", "fully rejecting", "rejecting any", "rejected evals"],
            breadth_rows,
        )
    )

    lines.extend(["", f"SOURCE PACKET PROTOCOLS — TOP {min(top, len(protocols))} BY RAW REJECTIONS"])
    raw_rows: list[list[object]] = []
    for _, row in by_raw.head(top).iterrows():
        raw_rows.append(
            [
                row["protocol"],
                format_packet_count(row),
                format_ratio(
                    int(row["rejected_evaluations"]),
                    int(row["packet_evaluations"]),
                    row["rejection_rate"],
                ),
                format_ratio(
                    int(row["full_rejecting_parsers"]),
                    int(row["eligible_parsers"]),
                    row["full_rejection_rate"],
                ),
            ]
        )
    lines.append(
        text_table(
            ["source", "packets", "rejected evals", "fully rejecting"], raw_rows
        )
    )

    lines.extend(["", "PARSERS — MOST REJECTING TO MOST ACCEPTING"])
    parser_rows: list[list[object]] = []
    for _, row in strict.iterrows():
        parser_rows.append(
            [
                row["parser_name"],
                row["parser_protocol"] or "?",
                format_ratio(
                    int(row["rejected_packets"]),
                    int(row["tested_packets"]),
                    row["packet_rejection_rate"],
                ),
                format_percent(row["balanced_rejection_rate"]),
                f"{int(row['fully_rejected_protocols'])}/{int(row['foreign_protocols'])}",
                str(int(row["partially_rejected_protocols"])),
                str(int(row["fully_accepted_protocols"])),
            ]
        )
    lines.append(
        text_table(
            [
                "parser",
                "own",
                "rejected packets",
                "balanced",
                "full reject",
                "partial",
                "full accept",
            ],
            parser_rows,
        )
    )

    lines.extend(["", "EDGE CASES TO REVIEW MANUALLY"])
    self_failures = self_rows[self_rows["failed"] > 0].sort_values(
        "rejection_rate", ascending=False
    )
    if self_failures.empty:
        lines.append("- Own-protocol sanity check: every inferred parser accepted all own packets.")
    else:
        details = "; ".join(
            f"{row.parser_name} rejected {row.failed}/{row.total} {row.protocol} packets"
            for row in self_failures.itertuples()
        )
        lines.append(
            "- OWN-PROTOCOL FAILURES: " + details
            + ". Foreign rejection scores for these parsers may reflect an always-reject "
            "parser or a harness/configuration problem."
        )
        self_failing_ids = set(self_failures["parser"])
        robust_cross = cross[~cross["parser"].isin(self_failing_ids)]
        if not robust_cross.empty:
            robust_protocols = protocol_summary(robust_cross).sort_values(
                ["full_rejecting_parsers", "any_rejecting_parsers", "rejection_rate"],
                ascending=False,
            )
            robust_best = robust_protocols.iloc[0]
            lines.append(
                f"- ROBUSTNESS CHECK: after excluding those self-failing parsers, "
                f"{robust_best['protocol']} remains the best source by breadth: "
                f"{int(robust_best['full_rejecting_parsers'])}/"
                f"{int(robust_best['eligible_parsers'])} remaining parsers fully reject it."
            )

    universal_accept = parsers[parsers["any_rejected_protocols"] == 0]
    if not universal_accept.empty:
        names = ", ".join(universal_accept["parser_name"])
        lines.append(
            f"- ACCEPTS EVERY FOREIGN PROTOCOL: {names}. Check for permissive fallback "
            "rules, prefix-only parsing, ignored trailing bytes, or a broken failure signal."
        )

    universal_reject = parsers[
        parsers["fully_rejected_protocols"].eq(parsers["foreign_protocols"])
    ]
    if not universal_reject.empty:
        names = ", ".join(universal_reject["parser_name"])
        lines.append(
            f"- REJECTS EVERY FOREIGN PROTOCOL: {names}. This is promising only when "
            "the corresponding own-protocol sanity check passes."
        )

    low_sample = protocols[protocols["packet_count_max"] < 5].sort_values(
        ["packet_count_max", "protocol"]
    )
    if not low_sample.empty:
        samples = ", ".join(
            f"{row.protocol} ({int(row.packet_count_max)})" for row in low_sample.itertuples()
        )
        lines.append(
            "- VERY SMALL PACKET SUITES (<5 packets): " + samples
            + ". A 100% result here is weak evidence; add packet variants before relying on it."
        )

    universal_accept_ids = set(universal_accept["parser"])
    accepted_pairs = cross[
        cross["fully_accepted"] & ~cross["parser"].isin(universal_accept_ids)
    ].sort_values(["total", "parser_name"], ascending=[False, True])
    lines.extend(
        [
            "",
            (
                f"Largest fully accepted foreign pairs (showing {min(top, len(accepted_pairs))} "
                f"of {len(accepted_pairs)}, excluding universally accepting parsers):"
            ),
        ]
    )
    accepted_rows = [
        [row.parser_name, row.protocol, f"0/{row.total} (0.0%)"]
        for row in accepted_pairs.head(top).itertuples()
    ]
    lines.append(text_table(["parser", "foreign packets", "rejected"], accepted_rows))

    partial_pairs = cross[cross["partially_rejected"]].sort_values(
        ["total", "rejection_rate"], ascending=[False, True]
    )
    lines.extend(
        [
            "",
            (
                f"Largest mixed-outcome pairs (showing {min(top, len(partial_pairs))} "
                f"of {len(partial_pairs)}):"
            ),
        ]
    )
    partial_rows = [
        [
            row.parser_name,
            row.protocol,
            format_ratio(row.failed, row.total, row.rejection_rate),
        ]
        for row in partial_pairs.head(top).itertuples()
    ]
    lines.append(text_table(["parser", "foreign packets", "rejected"], partial_rows))

    extreme_protocols = set(
        pd.concat([universal_accept["parser_protocol"], universal_reject["parser_protocol"]])
        .dropna()
        .astype(str)
    )
    asymmetries = reciprocal_asymmetries(cross, extreme_protocols)
    lines.extend(
        [
            "",
            (
                "Strong reciprocal asymmetries (A parser on B packets versus B parser on "
                "A packets; globally always-accept/reject parsers excluded):"
            ),
        ]
    )
    asymmetry_rows: list[list[object]] = []
    for row in asymmetries.head(top).itertuples():
        asymmetry_rows.append(
            [
                f"{row.protocol_a} <- {row.protocol_b}",
                format_ratio(row.a_on_b_failed, row.a_on_b_total, row.a_on_b_rate),
                f"{row.protocol_b} <- {row.protocol_a}",
                format_ratio(row.b_on_a_failed, row.b_on_a_total, row.b_on_a_rate),
                format_percent(row.gap),
            ]
        )
    lines.append(
        text_table(["direction", "rejected", "reverse", "rejected", "gap"], asymmetry_rows)
    )

    quality = data_quality_notes(df)
    lines.extend(["", "DATA QUALITY"])
    if quality:
        lines.extend(f"- {note}" for note in quality)
    else:
        lines.append("- Matrix is complete, parser protocols were inferred, and suite sizes are consistent.")

    lines.extend(
        [
            "",
            "INTERPRETATION NOTES",
            "- 'failed' is interpreted as parser rejection.",
            "- Own-protocol rows are sanity checks and are excluded from every cross ranking.",
            "- 'Balanced' gives each source protocol equal weight; packet rate gives every packet equal weight.",
            "- A fully rejecting parser rejected every packet from that source protocol; 'rejecting any' is weaker.",
            "- Cross-protocol packets are useful negative tests, but they do not replace malformed near-valid packets at grammar boundaries.",
        ]
    )
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Analyse parser rejection of packets from other protocols."
    )
    parser.add_argument(
        "csv",
        nargs="?",
        type=Path,
        default=DEFAULT_RESULTS,
        help=f"results CSV (default: {DEFAULT_RESULTS.name})",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=10,
        help="number of source protocols and edge-case pairs to show (default: 10)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.top < 1:
        raise SystemExit("--top must be at least 1")
    try:
        results = load_results(args.csv)
        report = build_report(results, args.csv, args.top)
    except (FileNotFoundError, pd.errors.ParserError, ValueError) as error:
        raise SystemExit(f"error: {error}") from error
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
