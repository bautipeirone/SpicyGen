from dotenv import load_dotenv
load_dotenv()

from src.providers import get_autogen_config
from src.logger import logger, enable_runtime_log_file
from src.protocol import load_protocol, load_spec
from pathlib import Path
import argparse
import autogen
import json
import re
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

SUMMARIZE_SPEC_SYSTEM_PROMPT = """\
You are a communication protocol expert. You are given the full text of an \
RFC specification. Your task is to identify every interval of the document \
that contains important information about the message grammar/format or the \
protocol state machine.

An interval is relevant if it describes:
- Packet/message/frame structure or field layouts
- Encoding rules (bit fields, TLVs, length-value, etc.)
- Protocol state machines, transitions, or control flow
- Finite-state automata or sequence diagrams describing protocol behaviour
- ABNF or BNF grammar rules
- Message type definitions or command/response formats

An interval is NOT relevant if it only discusses:
- Administrative or organisational information
- References, acknowledgements, or IANA considerations boilerplate
- Implementation details or platform-specific information
- Programming interfaces or APIs (sockets, libraries, function calls, etc.)
- General background or motivation without protocol details
- Table of contents

You MUST use the `select_intervals` tool to mark the relevant intervals. \
Each interval is defined by a pair of strings: the start marker and the end \
marker. These markers must be exact substrings of the document text — copy \
them verbatim. For section headers, include the header AND the following line exactly as they appear. \
Do NOT rewrite or paraphrase the document text. Select intervals that do not overlap.

After you have marked all the relevant intervals, say "TERMINATE" to end the conversation.
"""


# ---------------------------------------------------------------------------
# Helper: build a one-shot autogen ConversableAgent call
# ---------------------------------------------------------------------------

def _query_llm_once(llm_config: dict, system_prompt: str, user_message: str) -> str:
    """Send a single user message to the LLM and return the reply text."""
    assistant = autogen.ConversableAgent(
        name="assistant",
        system_message=system_prompt,
        llm_config=llm_config,
        human_input_mode="NEVER",
        max_consecutive_auto_reply=1,
    )
    user_proxy = autogen.ConversableAgent(
        name="user",
        llm_config=False,
        human_input_mode="NEVER",
        max_consecutive_auto_reply=0,
    )
    user_proxy.initiate_chat(
        assistant,
        message=user_message,
        max_turns=1,
        silent=True,
    )
    return assistant.last_message()["content"]


def extract_protocol_info(llm_config: dict, protocol_name: str, spec_content: str | None) -> dict:
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
    raw = _query_llm_once(llm_config, EXTRACT_INFO_SYSTEM_PROMPT, user_message)

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

    return info

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


def extract_intervals(spec_content: str, intervals: list[list[str]]) -> str:
    """Given a spec text and a list of [start_marker, end_marker] pairs,
    find each marker in the text and return the concatenation of all matched
    intervals.

    Overlapping or adjacent intervals are merged automatically.
    Intervals that cannot be resolved are logged and skipped.
    """
    resolved: list[tuple[int, int]] = []

    for idx, pair in enumerate(intervals):
        if len(pair) != 2:
            logger.warning(f"Interval {idx}: expected a pair [start, end], got {len(pair)} elements — skipping.")
            continue

        start_hit = find_marker(spec_content, pair[0])
        if start_hit is None:
            logger.warning(f"Interval {idx}: start marker not found — skipping.\n  marker: {pair[0]!r}")
            continue

        end_hit = find_marker(spec_content, pair[1], start=start_hit[0])
        if end_hit is None:
            logger.warning(f"Interval {idx}: end marker not found after start — skipping.\n  marker: {pair[1]!r}")
            continue

        orig_start = start_hit[0]
        orig_end = end_hit[1]

        logger.debug(f"Interval {idx}: resolved to positions {orig_start}-{orig_end}.")
        resolved.append((orig_start, orig_end))

    if not resolved:
        return ""

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
    return "\n".join(parts)


def summarize_spec(llm_config: dict, spec_content: str) -> str:
    """Use an LLM agent with a select_intervals tool to identify and extract
    the parts of *spec_content* that describe message grammar, format, or
    protocol state machines.  Returns the concatenated relevant text.
    """
    # Mutable container so the nested tool function can store its result.
    result_holder: dict[str, str | None] = {"summary": None}

    def select_intervals(
        intervals: Annotated[list[list[str]],
            "Array of [start_marker, end_marker] pairs. "
            "Each marker must be an exact substring copied verbatim from the "
            "document (use full lines to reduce ambiguity)."],
    ) -> str:
        """Mark intervals of the specification that are relevant to protocol
        grammar, message format, or state machine description."""
        logger.info(f"select_intervals called with {len(intervals)} interval(s).")
        summary = extract_intervals(spec_content, intervals)
        result_holder["summary"] = summary
        if summary:
            return f"OK — extracted {len(intervals)} interval(s) ({len(summary)} chars total)."
        return "WARNING — no intervals could be matched in the document."

    assistant = autogen.AssistantAgent(
        name="SpecAnalyzer",
        system_message=SUMMARIZE_SPEC_SYSTEM_PROMPT,
        llm_config=llm_config,
    )

    user_proxy = autogen.UserProxyAgent(
        name="SpecProxy",
        human_input_mode="NEVER",
        max_consecutive_auto_reply=3,
        is_termination_msg=lambda msg: "TERMINATE" in msg.get("content", "") or result_holder.get("summary") is not None,
        code_execution_config=False,
        default_auto_reply="Please use the `select_intervals` tool to mark the relevant parts of the document, then say TERMINATE.",
    )

    autogen.agentchat.register_function(
        select_intervals,
        caller=assistant,
        executor=user_proxy,
        name="select_intervals",
        description=(
            "Mark intervals of the RFC specification that contain protocol grammar, "
            "message format, or state machine information. Each interval is a pair "
            "[start_marker, end_marker] of exact substrings from the document."
        ),
    )

    user_message = (
        "Here is the full specification document. Read it carefully and use the "
        "`select_intervals` tool to select every interval that is relevant to "
        "the protocol message grammar/format or state machine.\n\n"
        f"{spec_content}"
    )

    logger.info("Starting spec summarization agent...")
    user_proxy.initiate_chat(
        assistant,
        message=user_message,
    )

    if result_holder["summary"] is not None:
        logger.info(f"Spec summarization complete — {len(result_holder['summary'])} chars extracted.")
        return result_holder["summary"]

    logger.warning("LLM did not call select_intervals; returning empty summary.")
    return ""


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
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    logger.info(f"Preprocessing spec for '{args.protocol}' using model '{args.model}'")

    protocol = load_protocol(args.protocol)
    if protocol is None:
        logger.error(f"Protocol '{args.protocol}' not found in index. Exiting.")
        exit(1)

    spec_content = load_spec(protocol)
    if spec_content is None:
        logger.warning(f"No specification content found for '{protocol.name}'. Proceeding without spec text.")

    llm_config = get_autogen_config(args.model)

    # Extract protocol info
    if not args.no_info:
        info = extract_protocol_info(llm_config, protocol.name, spec_content)
        info_json = json.dumps(info, indent=2)
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
        else:
            summary = summarize_spec(llm_config, spec_content)
            if args.summary_output:
                try:
                    args.summary_output.write_text(f"{protocol.name} specification summary:\n{summary}")
                    logger.info(f"Specification summary written to {args.summary_output}")
                except OSError as exc:
                    raise SystemExit(f"Unable to write to {args.summary_output}: {exc}") from exc
            else:
                print(f"=== {protocol.name} specification summary ===")
                print(summary)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.error(f"Preprocessing failed: {e}")
        exit(1)
