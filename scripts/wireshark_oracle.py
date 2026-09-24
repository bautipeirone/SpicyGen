#!/usr/bin/env python3
"""
Use TShark/Wireshark dissectors as a packet-level validity oracle.

The script finds every frame containing the intended protocol dissector,
classifies each frame, and writes a CSV or JSON report.

Verdicts:
    STRICT_REJECT
        The frame matches the `_ws.malformed` pseudo-protocol.

    SOFT_REJECT
        The frame contains an Expert Information item with Error severity,
        or it matches a user-configured soft-reject display filter.

    ACCEPT
        The intended dissector handled the frame and neither rejection signal
        above was present.

    INCONCLUSIVE_NOT_RECOGNIZED
        No frame in the capture matched the intended protocol filter.

Important:
    Wireshark dissectors are not complete protocol validators. ACCEPT means
    "accepted without the configured Wireshark error signals", not necessarily
    "fully compliant with the protocol specification".
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


# ---------------------------------------------------------------------------
# Editable protocol profiles
# ---------------------------------------------------------------------------
#
# Add non-standard port associations here when they are used frequently.
#
# Example:
#
#     "my-http": {
#         "display_filter": "http",
#         "decode_as": [
#             "tcp.port==12345,http",
#         ],
#         "soft_reject_filters": [],
#         "report_fields": [
#             "http.request.method",
#             "http.request.uri",
#             "http.response.code",
#         ],
#     },
#
# The profile name is what you pass as the positional PROTOCOL argument:
#
#     python wireshark_oracle.py capture.pcap my-http -o report.csv
#
# Associations can also be supplied without editing this file:
#
#     --decode-as tcp.port==12345,http
#
PROTOCOL_PROFILES: dict[str, dict[str, Any]] = {
    "dns": {
        "display_filter": "dns",
        "decode_as": [],
        "soft_reject_filters": [],
        "report_fields": [],
    },
    "http": {
        "display_filter": "http",
        "decode_as": [],
        "soft_reject_filters": [],
        "report_fields": [],
    },
    "imap": {
        "display_filter": "imap",
        "decode_as": [],
        "soft_reject_filters": [],
        "report_fields": [],
    },
    "rtsp": {
        "display_filter": "rtsp",
        "decode_as": [],
        "soft_reject_filters": [],
        "report_fields": [],
    },
    "sip": {
        "display_filter": "sip",
        "decode_as": [],
        "soft_reject_filters": [],
        "report_fields": [],
    },
}


BASE_REPORT_FIELDS = [
    "frame.number",
    "frame.time_epoch",
    "frame.len",
    "frame.cap_len",
    "frame.protocols",
    "eth.src",
    "eth.dst",
    "ip.src",
    "ip.dst",
    "ipv6.src",
    "ipv6.dst",
    "tcp.srcport",
    "tcp.dstport",
    "tcp.stream",
    "udp.srcport",
    "udp.dstport",
    "udp.stream",
    "_ws.expert.severity",
    "_ws.expert.group",
    "_ws.expert.message",
]

# Wireshark's PI_ERROR value. Depending on output format/version, TShark may
# render the severity as this integer or as a textual value such as "Error".
PI_ERROR = 0x00800000


class OracleError(RuntimeError):
    """An expected command-line or TShark failure."""


@dataclass
class ProtocolProfile:
    name: str
    display_filter: str
    decode_as: list[str] = field(default_factory=list)
    soft_reject_filters: list[str] = field(default_factory=list)
    report_fields: list[str] = field(default_factory=list)

    @classmethod
    def from_mapping(
        cls,
        name: str,
        value: Mapping[str, Any],
    ) -> "ProtocolProfile":
        display_filter = str(value.get("display_filter", name)).strip()
        if not display_filter:
            raise OracleError(f"Profile {name!r} has an empty display_filter.")

        return cls(
            name=name,
            display_filter=display_filter,
            decode_as=_string_list(value.get("decode_as", []), "decode_as", name),
            soft_reject_filters=_string_list(
                value.get("soft_reject_filters", []),
                "soft_reject_filters",
                name,
            ),
            report_fields=_string_list(
                value.get("report_fields", []),
                "report_fields",
                name,
            ),
        )


@dataclass
class PacketResult:
    frame_number: int
    time_epoch: str
    frame_length: str
    captured_length: str
    protocols: str
    source: str
    destination: str
    transport: str
    source_port: str
    destination_port: str
    stream_id: str
    verdict: str
    rejection_reasons: list[str]
    expert_severities: list[str]
    expert_groups: list[str]
    expert_messages: list[str]
    extra_fields: dict[str, list[str]]

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "frame_number": self.frame_number,
            "time_epoch": self.time_epoch,
            "frame_length": self.frame_length,
            "captured_length": self.captured_length,
            "protocols": self.protocols,
            "source": self.source,
            "destination": self.destination,
            "transport": self.transport,
            "source_port": self.source_port,
            "destination_port": self.destination_port,
            "stream_id": self.stream_id,
            "verdict": self.verdict,
            "rejection_reasons": self.rejection_reasons,
            "expert_severities": self.expert_severities,
            "expert_groups": self.expert_groups,
            "expert_messages": self.expert_messages,
            "extra_fields": self.extra_fields,
        }


def _string_list(value: Any, field_name: str, profile_name: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise OracleError(
            f"Profile {profile_name!r}: {field_name} must be a list of strings."
        )
    return [item.strip() for item in value if item.strip()]


def load_external_profiles(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None:
        return {}

    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except OSError as exc:
        raise OracleError(f"Could not read profile config {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise OracleError(f"Invalid JSON in profile config {path}: {exc}") from exc

    if not isinstance(data, dict):
        raise OracleError("Profile config must contain a JSON object.")

    # Accept either {"profiles": {...}} or a direct {"name": {...}} mapping.
    profiles = data.get("profiles", data)
    if not isinstance(profiles, dict):
        raise OracleError('The "profiles" value must be a JSON object.')

    result: dict[str, dict[str, Any]] = {}
    for name, value in profiles.items():
        if not isinstance(name, str) or not isinstance(value, dict):
            raise OracleError("Every profile must map a string name to an object.")
        result[name.lower()] = value
    return result


def resolve_profile(
    requested_name: str,
    external_profiles: Mapping[str, Mapping[str, Any]],
) -> ProtocolProfile:
    profiles: dict[str, Mapping[str, Any]] = {
        name.lower(): value for name, value in PROTOCOL_PROFILES.items()
    }
    profiles.update({name.lower(): value for name, value in external_profiles.items()})

    key = requested_name.lower()
    if key in profiles:
        return ProtocolProfile.from_mapping(requested_name, profiles[key])

    # Generic fallback: assume the supplied protocol name is also its display
    # filter/protocol abbreviation.
    return ProtocolProfile(name=requested_name, display_filter=requested_name)


def find_tshark(executable: str) -> str:
    if "/" in executable or "\\" in executable:
        path = Path(executable)
        if not path.exists():
            raise OracleError(f"TShark executable does not exist: {path}")
        return str(path)

    resolved = shutil.which(executable)
    if resolved is None:
        raise OracleError(
            f"Could not find {executable!r} in PATH. Install Wireshark/TShark "
            "or pass --tshark /path/to/tshark."
        )
    return resolved


def tshark_version(tshark: str) -> str:
    completed = subprocess.run(
        [tshark, "--version"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    output = completed.stdout.strip() or completed.stderr.strip()
    return output.splitlines()[0] if output else "unknown"


def base_tshark_command(
    tshark: str,
    pcap: Path,
    decode_as: Sequence[str],
) -> list[str]:
    command = [
        tshark,
        "-2",  # two-pass analysis for reassembly-dependent dissection
        "-n",  # disable name resolution
        "-r",
        str(pcap),
    ]
    for association in decode_as:
        command.extend(["-d", association])
    return command


def run_tshark_json(
    tshark: str,
    pcap: Path,
    decode_as: Sequence[str],
    display_filter: str,
    fields: Sequence[str],
) -> list[dict[str, Any]]:
    command = base_tshark_command(tshark, pcap, decode_as)
    command.extend(["-Y", display_filter, "-T", "json"])
    for field_name in fields:
        command.extend(["-e", field_name])

    completed = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode != 0:
        raise OracleError(
            "TShark failed while dissecting the capture.\n"
            f"Command: {format_command(command)}\n"
            f"Error:\n{completed.stderr.strip() or completed.stdout.strip()}"
        )

    try:
        data = json.loads(completed.stdout or "[]")
    except json.JSONDecodeError as exc:
        raise OracleError(
            "TShark returned invalid JSON.\n"
            f"Command: {format_command(command)}\n"
            f"JSON error: {exc}"
        ) from exc

    if not isinstance(data, list):
        raise OracleError("Unexpected TShark JSON structure: expected a list.")
    return data


def run_frame_number_filter(
    tshark: str,
    pcap: Path,
    decode_as: Sequence[str],
    display_filter: str,
) -> set[int]:
    command = base_tshark_command(tshark, pcap, decode_as)
    command.extend(
        [
            "-Y",
            display_filter,
            "-T",
            "fields",
            "-e",
            "frame.number",
        ]
    )

    completed = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode != 0:
        raise OracleError(
            "TShark failed while evaluating a rejection filter.\n"
            f"Command: {format_command(command)}\n"
            f"Error:\n{completed.stderr.strip() or completed.stdout.strip()}"
        )

    frame_numbers: set[int] = set()
    for line in completed.stdout.splitlines():
        value = line.strip()
        if not value:
            continue
        try:
            frame_numbers.add(int(value))
        except ValueError as exc:
            raise OracleError(
                f"Unexpected frame number returned by TShark: {value!r}"
            ) from exc
    return frame_numbers


def format_command(command: Sequence[str]) -> str:
    """Produce a readable representation without invoking a shell."""
    return " ".join(json.dumps(part) if any(ch.isspace() for ch in part) else part
                    for part in command)


def flatten_values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        result: list[str] = []
        for item in value:
            result.extend(flatten_values(item))
        return result
    if isinstance(value, dict):
        result = []
        for item in value.values():
            result.extend(flatten_values(item))
        return result
    return [str(value)]


def layer_values(layers: Mapping[str, Any], field_name: str) -> list[str]:
    return flatten_values(layers.get(field_name))


def first_value(layers: Mapping[str, Any], *field_names: str) -> str:
    for field_name in field_names:
        values = layer_values(layers, field_name)
        if values:
            return values[0]
    return ""


def contains_error_severity(values: Iterable[str]) -> bool:
    for raw_value in values:
        value = raw_value.strip()
        lowered = value.lower()

        if lowered in {"error", "pi_error"} or lowered.endswith(" error"):
            return True

        # Accept decimal and 0x-prefixed representations.
        try:
            numeric = int(value, 0)
        except ValueError:
            continue
        if numeric == PI_ERROR:
            return True

    return False


def packet_result_from_tshark(
    item: Mapping[str, Any],
    malformed_frames: set[int],
    custom_soft_reject_frames: Mapping[int, list[str]],
    extra_report_fields: Sequence[str],
) -> PacketResult:
    source = item.get("_source", {})
    if not isinstance(source, dict):
        source = {}
    layers = source.get("layers", {})
    if not isinstance(layers, dict):
        layers = {}

    frame_number_text = first_value(layers, "frame.number")
    if not frame_number_text:
        raise OracleError("A TShark packet result did not contain frame.number.")
    try:
        frame_number = int(frame_number_text)
    except ValueError as exc:
        raise OracleError(
            f"Invalid frame.number returned by TShark: {frame_number_text!r}"
        ) from exc

    tcp_source_port = first_value(layers, "tcp.srcport")
    tcp_destination_port = first_value(layers, "tcp.dstport")
    udp_source_port = first_value(layers, "udp.srcport")
    udp_destination_port = first_value(layers, "udp.dstport")

    if tcp_source_port or tcp_destination_port:
        transport = "TCP"
        source_port = tcp_source_port
        destination_port = tcp_destination_port
        stream_id = first_value(layers, "tcp.stream")
    elif udp_source_port or udp_destination_port:
        transport = "UDP"
        source_port = udp_source_port
        destination_port = udp_destination_port
        stream_id = first_value(layers, "udp.stream")
    else:
        transport = ""
        source_port = ""
        destination_port = ""
        stream_id = ""

    expert_severities = layer_values(layers, "_ws.expert.severity")
    expert_groups = layer_values(layers, "_ws.expert.group")
    expert_messages = layer_values(layers, "_ws.expert.message")

    rejection_reasons: list[str] = []
    if frame_number in malformed_frames:
        verdict = "STRICT_REJECT"
        rejection_reasons.append("Matched _ws.malformed")
    else:
        if contains_error_severity(expert_severities):
            rejection_reasons.append("Expert Information severity Error")
        rejection_reasons.extend(custom_soft_reject_frames.get(frame_number, []))

        verdict = "SOFT_REJECT" if rejection_reasons else "ACCEPT"

    extra_fields = {
        field_name: layer_values(layers, field_name)
        for field_name in extra_report_fields
    }

    return PacketResult(
        frame_number=frame_number,
        time_epoch=first_value(layers, "frame.time_epoch"),
        frame_length=first_value(layers, "frame.len"),
        captured_length=first_value(layers, "frame.cap_len"),
        protocols=first_value(layers, "frame.protocols"),
        source=first_value(layers, "ip.src", "ipv6.src", "eth.src"),
        destination=first_value(layers, "ip.dst", "ipv6.dst", "eth.dst"),
        transport=transport,
        source_port=source_port,
        destination_port=destination_port,
        stream_id=stream_id,
        verdict=verdict,
        rejection_reasons=rejection_reasons,
        expert_severities=expert_severities,
        expert_groups=expert_groups,
        expert_messages=expert_messages,
        extra_fields=extra_fields,
    )


def unique_preserving_order(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def build_report(
    pcap: Path,
    profile: ProtocolProfile,
    tshark: str,
    decode_as: Sequence[str],
    extra_fields: Sequence[str],
    packets: Sequence[PacketResult],
) -> dict[str, Any]:
    counts = {
        "total_protocol_frames": len(packets),
        "strict_reject": sum(p.verdict == "STRICT_REJECT" for p in packets),
        "soft_reject": sum(p.verdict == "SOFT_REJECT" for p in packets),
        "accept": sum(p.verdict == "ACCEPT" for p in packets),
    }

    overall_verdict = (
        "INCONCLUSIVE_NOT_RECOGNIZED"
        if not packets
        else "PROTOCOL_FRAMES_ANALYZED"
    )

    return {
        "metadata": {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "pcap": str(pcap.resolve()),
            "profile": profile.name,
            "display_filter": profile.display_filter,
            "decode_as": list(decode_as),
            "soft_reject_filters": list(profile.soft_reject_filters),
            "extra_report_fields": list(extra_fields),
            "tshark_executable": tshark,
            "tshark_version": tshark_version(tshark),
            "overall_verdict": overall_verdict,
            "counts": counts,
            "interpretation": (
                "ACCEPT means the intended dissector handled the frame without "
                "a configured malformed/error signal. It does not prove full "
                "standards compliance."
            ),
        },
        "packets": [packet.to_json_dict() for packet in packets],
    }


def write_json_report(path: Path, report: Mapping[str, Any]) -> None:
    try:
        with path.open("w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
    except OSError as exc:
        raise OracleError(f"Could not write report {path}: {exc}") from exc


def csv_join(values: Iterable[str]) -> str:
    return " | ".join(value.replace("\r", "\\r").replace("\n", "\\n")
                      for value in values)


def write_csv_report(
    path: Path,
    report: Mapping[str, Any],
    packets: Sequence[PacketResult],
    extra_fields: Sequence[str],
) -> None:
    field_names = [
        "frame_number",
        "time_epoch",
        "frame_length",
        "captured_length",
        "protocols",
        "source",
        "destination",
        "transport",
        "source_port",
        "destination_port",
        "stream_id",
        "verdict",
        "rejection_reasons",
        "expert_severities",
        "expert_groups",
        "expert_messages",
        *extra_fields,
    ]

    try:
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=field_names)
            writer.writeheader()

            if not packets:
                row = {name: "" for name in field_names}
                row["verdict"] = "INCONCLUSIVE_NOT_RECOGNIZED"
                row["rejection_reasons"] = (
                    "No frame matched the intended protocol display filter"
                )
                writer.writerow(row)
                return

            for packet in packets:
                row: dict[str, Any] = {
                    "frame_number": packet.frame_number,
                    "time_epoch": packet.time_epoch,
                    "frame_length": packet.frame_length,
                    "captured_length": packet.captured_length,
                    "protocols": packet.protocols,
                    "source": packet.source,
                    "destination": packet.destination,
                    "transport": packet.transport,
                    "source_port": packet.source_port,
                    "destination_port": packet.destination_port,
                    "stream_id": packet.stream_id,
                    "verdict": packet.verdict,
                    "rejection_reasons": csv_join(packet.rejection_reasons),
                    "expert_severities": csv_join(packet.expert_severities),
                    "expert_groups": csv_join(packet.expert_groups),
                    "expert_messages": csv_join(packet.expert_messages),
                }
                for field_name in extra_fields:
                    row[field_name] = csv_join(packet.extra_fields[field_name])
                writer.writerow(row)
    except OSError as exc:
        raise OracleError(f"Could not write report {path}: {exc}") from exc


def parse_arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Classify every frame containing an intended Wireshark protocol "
            "dissector and write a CSV or JSON report."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s capture.pcap dns -o dns-report.csv

  %(prog)s capture.pcap http -o http-report.json \
      --decode-as tcp.port==12345,http

  %(prog)s capture.pcap myproto -o report.csv \
      --display-filter myproto \
      --decode-as udp.port==9999,myproto

  %(prog)s capture.pcap my-http -o report.json \
      --profile-config profiles.json

Profile config example:
  {
    "profiles": {
      "my-http": {
        "display_filter": "http",
        "decode_as": ["tcp.port==12345,http"],
        "soft_reject_filters": [],
        "report_fields": [
          "http.request.method",
          "http.request.uri",
          "http.response.code"
        ]
      }
    }
  }
""",
    )
    parser.add_argument("pcap", type=Path, help="Input PCAP/PCAPNG file.")
    parser.add_argument(
        "protocol",
        help=(
            "Profile name or Wireshark protocol/display-filter abbreviation, "
            "for example dns, http, sip, or myproto."
        ),
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        required=True,
        help="Output report. Extension must be .csv or .json.",
    )
    parser.add_argument(
        "--tshark",
        default="tshark",
        help="TShark executable name or path. Default: tshark",
    )
    parser.add_argument(
        "--profile-config",
        type=Path,
        help="Optional JSON file containing additional/overriding profiles.",
    )
    parser.add_argument(
        "--display-filter",
        help="Override the profile's protocol display filter.",
    )
    parser.add_argument(
        "--decode-as",
        action="append",
        default=[],
        metavar="ASSOCIATION",
        help=(
            "Add a TShark Decode-As association. Repeat as needed. "
            "Example: --decode-as tcp.port==12345,http"
        ),
    )
    parser.add_argument(
        "--soft-reject-filter",
        action="append",
        default=[],
        metavar="FILTER",
        help=(
            "Mark matching protocol frames as SOFT_REJECT. Repeat as needed. "
            "The filter is ANDed with the protocol filter."
        ),
    )
    parser.add_argument(
        "--field",
        action="append",
        default=[],
        metavar="FIELD",
        help=(
            "Add a Wireshark field to the report. Repeat as needed. "
            "Example: --field dns.qry.name"
        ),
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_arguments(argv)

    if not args.pcap.is_file():
        raise OracleError(f"Input capture does not exist or is not a file: {args.pcap}")

    output_suffix = args.output.suffix.lower()
    if output_suffix not in {".csv", ".json"}:
        raise OracleError("Output filename must end in .csv or .json.")

    external_profiles = load_external_profiles(args.profile_config)
    profile = resolve_profile(args.protocol, external_profiles)

    if args.display_filter:
        profile.display_filter = args.display_filter.strip()
    profile.decode_as.extend(args.decode_as)
    profile.soft_reject_filters.extend(args.soft_reject_filter)
    profile.report_fields.extend(args.field)

    profile.decode_as = unique_preserving_order(profile.decode_as)
    profile.soft_reject_filters = unique_preserving_order(
        profile.soft_reject_filters
    )
    extra_fields = unique_preserving_order(profile.report_fields)

    tshark = find_tshark(args.tshark)
    protocol_filter = f"({profile.display_filter})"

    all_fields = unique_preserving_order([*BASE_REPORT_FIELDS, *extra_fields])
    packet_json = run_tshark_json(
        tshark=tshark,
        pcap=args.pcap,
        decode_as=profile.decode_as,
        display_filter=protocol_filter,
        fields=all_fields,
    )

    malformed_frames = run_frame_number_filter(
        tshark=tshark,
        pcap=args.pcap,
        decode_as=profile.decode_as,
        display_filter=f"{protocol_filter} && _ws.malformed",
    )

    custom_soft_reject_frames: dict[int, list[str]] = {}
    for custom_filter in profile.soft_reject_filters:
        matching_frames = run_frame_number_filter(
            tshark=tshark,
            pcap=args.pcap,
            decode_as=profile.decode_as,
            display_filter=f"{protocol_filter} && ({custom_filter})",
        )
        for frame_number in matching_frames:
            custom_soft_reject_frames.setdefault(frame_number, []).append(
                f"Matched custom filter: {custom_filter}"
            )

    packets = [
        packet_result_from_tshark(
            item=item,
            malformed_frames=malformed_frames,
            custom_soft_reject_frames=custom_soft_reject_frames,
            extra_report_fields=extra_fields,
        )
        for item in packet_json
    ]
    packets.sort(key=lambda packet: packet.frame_number)

    report = build_report(
        pcap=args.pcap,
        profile=profile,
        tshark=tshark,
        decode_as=profile.decode_as,
        extra_fields=extra_fields,
        packets=packets,
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    if output_suffix == ".json":
        write_json_report(args.output, report)
    else:
        write_csv_report(args.output, report, packets, extra_fields)

    counts = report["metadata"]["counts"]
    print(f"Report written to: {args.output}")
    print(f"Protocol filter: {profile.display_filter}")
    if profile.decode_as:
        print(f"Decode-As: {', '.join(profile.decode_as)}")
    print(
        "Frames: "
        f"{counts['total_protocol_frames']} total, "
        f"{counts['strict_reject']} strict reject, "
        f"{counts['soft_reject']} soft reject, "
        f"{counts['accept']} accepted"
    )
    if not packets:
        print("Result: INCONCLUSIVE_NOT_RECOGNIZED")
        return 2
    if counts["strict_reject"] or counts["soft_reject"]:
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except OracleError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(3)
    except KeyboardInterrupt:
        print("error: interrupted", file=sys.stderr)
        sys.exit(130)
