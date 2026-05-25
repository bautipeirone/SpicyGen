from dotenv import load_dotenv
load_dotenv()

from src.providers import get_autogen_config
from src.logger import logger, enable_runtime_log_file
from src.protocol import load_protocol, load_spec
from src.usage import extract_cost_summary, merge_usage_reports
from src.agent import query_llm_once, summarize_spec
from src.utils import write_json_file, compute_text_stats
from pathlib import Path
import argparse
import autogen
import json
import re
import time
from typing import Any
from typing import Annotated
from functools import cache


# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------

EXTRACT_INFO_SYSTEM_PROMPT = """\
You are a communication protocol expert. Your task is to extract metadata about \
a network protocol from the given RFC text and/or your training data.

You MUST respond with a single JSON object (no markdown, no extra text) with \
exactly the following fields:
{
  "type": "<text|binary>",
  "port": <integer or null>,
  "transport": "array of <tcp|udp|sctp|...> or [] if not applicable>",
  "layer": "<application|session|transport|network|link| or null>",
  "encoding": <ASN.1|CBOR|... or null>",
  "checksum": <Internet Checksum|CRC16|CRC32|HMAC-SHA1|... or null>",
}

Rules:
- "type" is "text" if the protocol payload is human-readable ASCII/UTF-8, \
  otherwise "binary".
- "port" is the well-known port number assigned by IANA, or null if not \
  applicable / not assigned.
- "transport" is the array of underlying transport protocols in lowercase \
  (e.g. ["tcp", "udp"]), or [] if not applicable.
- "layer" is the layer of the OSI model the protocol belongs to.
- "encoding" is the encoding of the protocol message (e.g. "ASN.1", "CBOR", ...).
- "checksum" is the checksum algorithm used in the protocol header, or null if not applicable.
"""

EXPECTED_INFO_FIELDS = ("type", "port", "transport", "layer", "encoding", "checksum")

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
        "valid_json_object": "raw_response" not in info,
    }
    return stats


def extract_protocol_info(
    llm_config: dict,
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
        info = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("LLM response was not valid JSON; attempting to extract JSON substring.")
        match = re.search(r"\{.*?\}", raw, re.DOTALL)
        if match:
            info = json.loads(match.group())
        else:
            logger.error("Could not parse protocol info from LLM response.")
            info = {"raw_response": raw}

    return info, usage_report


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
    logger.debug(f"Report file: {args.report_file}")
    logger.debug(f"Debug dir: {args.debug_dir}")

    protocol = load_protocol(args.protocol)
    if protocol is None:
        logger.error(f"Protocol '{args.protocol}' not found in index. Exiting.")
        exit(1)

    spec_content = load_spec(protocol)
    if spec_content is None:
        logger.warning(f"No specification content found for '{protocol.name}'. Proceeding without spec text.")

    llm_config = get_autogen_config(args.model)
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

    combined_usage = merge_usage_reports(
        report["steps"]["info"].get("usage") if isinstance(report["steps"]["info"], dict) else None,
        report["steps"]["summary"].get("usage") if isinstance(report["steps"]["summary"], dict) else None,
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
