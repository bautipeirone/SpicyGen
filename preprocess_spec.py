from dotenv import load_dotenv
load_dotenv()

from src.providers import ModelConfig, get_model_config
from src.logger import logger, enable_runtime_log_file
from src.protocol import Protocol, load_protocol, load_spec
from src.prompts import EXTRACT_INFO_SYSTEM_PROMPT, NEGATIVE_PACKETS_SYSTEM_PROMPT_TEMPLATE
from src.usage import merge_usage_reports
from src.agent import query_llm_once, summarize_spec
from src.utils import write_json_file, compute_text_stats, strip_response_fences
from pathlib import Path
import argparse
import json
import re
import time
from typing import Any

EXPECTED_INFO_FIELDS = (
    "type",
    "port",
    "transport",
    "layer",
    "encoding",
    "checksum",
    "parser_arguments",
)

NEGATIVE_PACKET_FIELDS = (
    "name",
    "protocol",
    "packet",
    "invalidity_category",
    "reason",
)

def compute_info_stats(info: dict[str, Any]) -> dict[str, Any]:
    present_fields = [field for field in EXPECTED_INFO_FIELDS if field in info]
    populated_fields = [
        field for field in present_fields
        if info[field] not in (None, [], "")
    ]
    null_fields = [
        field for field in present_fields
        if info[field] in (None, [], "")
    ]

    stats: dict[str, Any] = {
        "expected_fields": len(EXPECTED_INFO_FIELDS),
        "present_fields": len(present_fields),
        "populated_fields": len(populated_fields),
        "null_or_empty_fields": len(null_fields),
        "missing_fields": [field for field in EXPECTED_INFO_FIELDS if field not in info],
        "transport_count": len(info.get("transport", [])) if isinstance(info.get("transport"), list) else 0,
        "parser_argument_count": (
            len(info.get("parser_arguments", []))
            if isinstance(info.get("parser_arguments"), list)
            else 0
        ),
        "valid_json_object": "raw_response" not in info,
    }
    return stats


def extract_protocol_info(
    llm_config: ModelConfig,
    protocol_name: str,
    spec_content: str | None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Use the LLM to extract metadata about a protocol, like port number, transport protocol, etc.

    The spec text is included when available; otherwise the model relies on
    its training data.
    """
    if spec_content:
        user_message = (
            f"Protocol: {protocol_name}\n\n"
            f"Specification content:\n{spec_content}"
        )
    else:
        user_message = (
            f"Protocol: {protocol_name}\n\n"
            "No specification is available. Use your training data to fill in the fields."
        )

    logger.info(f"Extracting protocol info: {protocol_name}")
    raw, usage_report = query_llm_once(llm_config, EXTRACT_INFO_SYSTEM_PROMPT, user_message)

    try:
        response = strip_response_fences(raw)
        info = json.loads(response)
    except json.JSONDecodeError:
        logger.warning("LLM response was not valid JSON; attempting to extract JSON substring.")
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            info = json.loads(match.group())
        else:
            logger.error("Could not parse protocol info from LLM response.")
            info = {"raw_response": raw}

    return info, usage_report


def packet_field_description(protocol: Protocol) -> str:
    if protocol.is_binary_protocol:
        return (
            "a JSON array of decimal byte values in wire order. Each value MUST "
            "be an integer from 0 to 255. Do not use hex strings, base64, nested "
            "arrays, or comments."
        )
    return (
        "a JSON string containing the exact raw text message bytes. Preserve "
        "protocol line endings with JSON escapes such as \\r\\n. Do not use a "
        "byte array for text protocols."
    )


def compute_negative_packet_stats(packets: list[dict[str, Any]]) -> dict[str, Any]:
    categories = sorted(
        {
            packet.get("invalidity_category")
            for packet in packets
            if isinstance(packet.get("invalidity_category"), str)
        }
    )
    return {
        "packet_count": len(packets),
        "invalidity_category_count": len(categories),
        "invalidity_categories": categories,
    }


def parse_negative_packet_response(raw: str, protocol: Protocol) -> list[dict[str, Any]]:
    """Parse and validate JSONL negative packet output from the LLM."""
    text = strip_response_fences(raw)
    if not text:
        logger.error("negative packet response was empty")
        return []

    packets: list[dict[str, Any]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            parsed_line = json.loads(line)
            packets.append(parsed_line)
        except json.JSONDecodeError as exc:
            logger.error(f"negative packet JSONL line {line_number} is invalid JSON: {exc}")

    for index, packet in enumerate(packets, start=1):
        if not isinstance(packet, dict):
            logger.error(f"negative packet #{index} must be a JSON object")
        missing = [field for field in NEGATIVE_PACKET_FIELDS if field not in packet]
        if missing:
            logger.warning(f"negative packet #{index} is missing field(s): {', '.join(missing)}")
        for field in ("name", "protocol", "invalidity_category", "reason"):
            if not isinstance(packet[field], str) or not packet[field].strip():
                logger.warning(f"negative packet #{index} field {field!r} must be a non-empty string")
        value = packet["packet"]
        if protocol.is_binary_protocol:
            if (
                not isinstance(value, list)
                or not all(isinstance(byte, int) and 0 <= byte <= 255 for byte in value)
            ):
                logger.error(f"negative packet #{index} field 'packet' must be an array of byte integers")
        elif not isinstance(value, str):
            logger.error(f"negative packet #{index} field 'packet' must be a string for text protocols")

    return packets


def packets_to_jsonl(packets: list[dict[str, Any]]) -> str:
    return "".join(
        json.dumps(packet, ensure_ascii=False, separators=(",", ":")) + "\n"
        for packet in packets
    )


def generate_negative_packets(
    llm_config: ModelConfig,
    protocol: Protocol,
    spec_content: str | None,
    count: int,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Use the LLM to generate close-to-valid invalid packets for a protocol."""
    if count < 1:
        logger.error(f"negative packet count must be at least 1, recevied {count}")
        return ([], None)

    protocol_label = protocol.name
    if protocol.full_name:
        protocol_label = f"{protocol.name} ({protocol.full_name})"

    system_prompt = NEGATIVE_PACKETS_SYSTEM_PROMPT_TEMPLATE.format(
        protocol=protocol_label,
        count=count,
        packet_field_description=packet_field_description(protocol),
    )
    if spec_content:
        user_message = (
            f"Protocol: {protocol_label}\n\n"
            f"Specification content:\n{spec_content}"
        )
    else:
        user_message = (
            f"Protocol: {protocol_label}\n\n"
            "No specification is available. Use your training data to generate "
            "close-to-valid invalid packets."
        )

    logger.info(f"Generating {count} negative packet(s): {protocol.name}")
    raw, usage_report = query_llm_once(llm_config, system_prompt, user_message)
    packets = parse_negative_packet_response(raw, protocol)
    if len(packets) != count:
        logger.warning(
            "Expected %s negative packet(s), but LLM produced %s.",
            count,
            len(packets),
        )
    return packets, usage_report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Preprocess an RFC: extract protocol info and/or summarize relevant sections."
    )
    parser.add_argument(
        "--model",
        "-m",
        required=True,
        help="Model key from model_index.json to use for LLM queries.",
    )
    parser.add_argument(
        "protocol",
        type=str,
        help="Protocol name (e.g. 'dns', 'http') used to look up the spec in protocol_index.json.",
    )
    parser.add_argument(
        "--no-info",
        action="store_true",
        help="Skip the protocol info extraction step.",
    )
    parser.add_argument(
        "--no-summary",
        action="store_true",
        help="Skip the spec summarization step.",
    )
    parser.add_argument(
        "--no-negatives",
        action="store_true",
        help="Skip the negative packet generation step.",
    )
    parser.add_argument(
        "--info-output",
        type=Path,
        default=None,
        help="Path to write the protocol info JSON (default: stdout).",
    )
    parser.add_argument(
        "--summary-output",
        type=Path,
        default=None,
        help="Path to write the filtered spec summary (default: stdout).",
    )
    parser.add_argument(
        "--negative-output",
        type=Path,
        default=None,
        help="Path to write generated negative packets as JSONL (default: stdout).",
    )
    parser.add_argument(
        "--negative-count",
        type=int,
        default=10,
        help="Number of negative packets to request (default: 10).",
    )
    parser.add_argument(
        "--report-file",
        "--metrics-file",
        dest="report_file",
        type=Path,
        default=None,
        help="Path to write a JSON report with usage and extraction stats.",
    )
    parser.add_argument(
        "--debug-dir",
        type=Path,
        default=None,
        help="Directory to save runtime.log and other preprocessing debug artifacts (optional).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    runtime_log_path = args.debug_dir / "extraction.log" if args.debug_dir else None
    enable_runtime_log_file(runtime_log_path)

    logger.info(f"Preprocessing spec for '{args.protocol}' using model '{args.model}'")
    logger.debug(f"Info output: {args.info_output}")
    logger.debug(f"Summary output: {args.summary_output}")
    logger.debug(f"Negative output: {args.negative_output}")
    logger.debug(f"Negative count: {args.negative_count}")
    logger.debug(f"Report file: {args.report_file}")
    logger.debug(f"Debug dir: {args.debug_dir}")

    protocol = load_protocol(args.protocol)
    if protocol is None:
        logger.error(f"Protocol '{args.protocol}' not found in index. Exiting.")
        exit(1)

    spec_content = load_spec(protocol)
    if spec_content is None:
        logger.warning(f"No specification content found for '{protocol.name}'. Proceeding without spec text.")

    llm_config = get_model_config(args.model)
    spec_stats = compute_text_stats(spec_content)

    report: dict[str, Any] = {
        "model": args.model,
        "protocol": protocol.name,
        "spec_stats": spec_stats,
        "steps": {
            "info": {
                "enabled": not args.no_info,
                "completed": False,
            },
            "summary": {
                "enabled": not args.no_summary,
                "completed": False,
            },
            "negative_packets": {
                "enabled": not args.no_negatives,
                "completed": False,
            },
        },
    }

    # Extract protocol info
    if not args.no_info:
        info_started_at = time.time()
        info, info_usage = extract_protocol_info(llm_config, protocol.name, spec_content)
        info_json = json.dumps(info, indent=2)
        info_elapsed = round(time.time() - info_started_at, 2)
        report["steps"]["info"] = {
            "enabled": True,
            "completed": True,
            "time_elapsed_s": info_elapsed,
            "stats": compute_info_stats(info),
            "usage": info_usage,
        }
        if args.info_output:
            try:
                args.info_output.write_text(info_json)
                logger.info(f"Protocol info written to {args.info_output}")
            except OSError as exc:
                raise SystemExit(f"Unable to write to {args.info_output}: {exc}") from exc
        else:
            print(f"=== {protocol.name} protocol info ===")
            print(info_json)

    # Summarize spec
    if not args.no_summary:
        if spec_content is None:
            logger.error("Cannot summarize: no spec content available.")
            report["steps"]["summary"] = {
                "enabled": True,
                "completed": False,
                "error": "No specification content available.",
            }
        else:
            summary_started_at = time.time()
            summary, summary_stats, summary_usage = summarize_spec(llm_config, spec_content)
            summary_elapsed = round(time.time() - summary_started_at, 2)
            report["steps"]["summary"] = {
                "enabled": True,
                "completed": True,
                "time_elapsed_s": summary_elapsed,
                "stats": summary_stats,
                "usage": summary_usage,
            }
            if args.summary_output:
                try:
                    args.summary_output.write_text(f"{protocol.name} specification summary:\n{summary}")
                    logger.info(f"Specification summary written to {args.summary_output}")
                except OSError as exc:
                    raise SystemExit(f"Unable to write to {args.summary_output}: {exc}") from exc
            else:
                print(f"=== {protocol.name} specification summary ===")
                print(summary)

    # Generate negative packets
    if not args.no_negatives:
        negatives_started_at = time.time()
        packets, negatives_usage = generate_negative_packets(
            llm_config,
            protocol,
            spec_content,
            args.negative_count,
        )
        negatives_jsonl = packets_to_jsonl(packets)
        negatives_elapsed = round(time.time() - negatives_started_at, 2)
        report["steps"]["negative_packets"] = {
            "enabled": True,
            "completed": True,
            "time_elapsed_s": negatives_elapsed,
            "stats": compute_negative_packet_stats(packets),
            "usage": negatives_usage,
        }
        if args.negative_output:
            try:
                args.negative_output.write_text(negatives_jsonl)
                logger.info(f"Negative packets written to {args.negative_output}")
            except OSError as exc:
                raise SystemExit(f"Unable to write to {args.negative_output}: {exc}") from exc
        else:
            print(f"=== {protocol.name} generated negative packets ===")
            print(negatives_jsonl, end="")

    combined_usage = merge_usage_reports(
        report["steps"]["info"].get("usage") if isinstance(report["steps"]["info"], dict) else None,
        report["steps"]["summary"].get("usage") if isinstance(report["steps"]["summary"], dict) else None,
        report["steps"]["negative_packets"].get("usage") if isinstance(report["steps"]["negative_packets"], dict) else None,
    )
    report["usage"] = combined_usage

    if args.report_file:
        write_json_file(args.report_file, report)
        logger.info(f"Preprocessing report written to {args.report_file}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.error(f"Preprocessing failed: {e}")
        exit(1)
