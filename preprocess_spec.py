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
  "transport": "<tcp|udp|sctp|... or null>"
  "layer": "<application|session|transport|network|link| or null>",
  "encoding": <ASN.1|CBOR|... or null>",
  "checksum": <Internet Checksum|CRC16|CRC32|HMAC-SHA1|... or null>",
}

Rules:
- "type" is "text" if the protocol payload is human-readable ASCII/UTF-8, \
  otherwise "binary".
- "port" is the well-known port number assigned by IANA, or null if not \
  applicable / not assigned.
- "transport" is the underlying transport protocol in lowercase \
  (e.g. "tcp", "udp", "sctp"), or null if not applicable.
- "layer" is the layer of the OSI model the protocol belongs to.
- "encoding" is the encoding of the protocol message (e.g. "ASN.1", "CBOR", ...).
- "checksum" is the checksum algorithm used in the protocol header, or null if not applicable.
"""

SUMMARIZE_SECTION_SYSTEM_PROMPT = """\
You are a communication protocol expert reviewing RFC sections. \
Your task is to decide whether a given RFC section contains important \
information about the message grammar/format or the protocol state machine.

Respond with a single JSON object (no markdown, no extra text):
{"relevant": true}   — if the section IS relevant
{"relevant": false}  — if the section is NOT relevant

A section is relevant if it describes:
- Packet/message/frame structure or field layouts
- Encoding rules (bit fields, TLVs, length-value, etc.)
- Protocol state machines, transitions, or control flow
- Finite-state automata or sequence diagrams describing protocol behaviour

It is NOT relevant if it only discusses:
- Administrative or organisational information
- References, acknowledgements, or IANA considerations boilerplate
- General background or motivation
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

def normalize_title(title: str) -> str:
    title = title.strip()
    title = re.sub(r'\s+', ' ', title)
    title = title.rstrip('.')
    return title


def extract_toc_entries(text: str):
    TOC_ENTRY_RE = re.compile(
        r'''(?mx)
        ^
        (?![ \t]*\d+(?:\.\d+)+\b)   # reject 1.1, 2.10, 4.3.2, etc.
        [ \t]*
        (?P<heading>.*?\S)
        [ \t]*
        \.{2,}
        [ \t]*
        (?P<page>\d+|[ivxlcdm]+)
        [ \t]*$
        '''
)

    toc_heading = re.search(r'(?im)^[ \t]*Table of Contents[ \t]*$', text)
    if not toc_heading:
        return {}, None

    # Limit the search to a reasonable region after the ToC heading.
    # This avoids accidentally picking up unrelated dot-leader lines later.
    toc_start = toc_heading.end()
    toc_region = text[toc_start:toc_start + 50_000]

    entries = []
    last_match_end = None

    for m in TOC_ENTRY_RE.finditer(toc_region):
        entries.append(normalize_title(m.group("heading")))
        last_match_end = toc_start + m.end()

    return entries, last_match_end

def find_sections(spec_content: str) -> list[dict]:
    BODY_HEADER_RE = re.compile(
        r'''(?mx)
        ^
        (?![ \t]*\d+(?:\.\d+)+\b)   # reject 1.1, 2.10, 4.3.2, etc.
        [ \t]*
        (?P<heading>.*?\S)
        [ \t]*$
        '''
    )

    SECTION_HEADING_RE = re.compile(
        r'''(?mx)
        ^[ \t]*
        (?!.*\.{2,}[ \t]*\d+[ \t]*$)
        (?:
            (?P<number>\d+)\.[ \t]+(?P<heading>\S.*?)
        |
            (?P<annex>(?:Appendix|Annex)[ \t]+[A-Z])[\.:]?[ \t]+(?P<annex_heading>\S.*?)
        )
        [ \t]*$
        '''
    )

    toc_entries, body_start = extract_toc_entries(spec_content)

    # RFCs that dont have a table of contents are harder to split
    # We choose to fallback to another a more straightforward method that works for some cases
    regex_to_use = BODY_HEADER_RE
    if not toc_entries:
        regex_to_use = SECTION_HEADING_RE

    # Start after the ToC, otherwise we may inspect front matter unnecessarily.
    search_start = body_start or 0

    matches = []
    for m in regex_to_use.finditer(spec_content, search_start):
        heading = normalize_title(m.group("heading") or m.group("annex_heading"))

        if toc_entries and heading not in toc_entries:
            continue

        matches.append({
            "heading": heading,
            "start": m.start(),
            "end": m.end(),
            "match": m,
        })

    if matches == []:
        logger.error(f"Could not find any sections in the spec content.")
        return [{ "heading": "Full Specification", "content": spec_content, "start": 0, "end": len(spec_content), "match": None }]

    sections = []
    for i, match_info in enumerate(matches):
        start_idx = match_info["start"]
        end_idx = matches[i + 1]["start"] if i + 1 < len(matches) else len(spec_content)
        
        match_info["content"] = spec_content[start_idx:end_idx]
        sections.append(match_info)

    return sections

def split_spec_into_sections(spec_content: str) -> list[str]:
    """Split *spec_content* into (header, body) pairs using a section regex."""
    # This regex matches only the first level sections (e.g. "1. Introduction", "2. Protocol Overview")
    
    SECTION_HEADING_RE = re.compile(
        r'''(?mx)
        ^[ \t]*
        (?!.*\.{2,}[ \t]*\d+[ \t]*$)
        (?:
            (?P<number>\d+)\.[ \t]+(?P<title>\S.*?)
        |
            (?P<annex>(?:Appendix|Annex)[ \t]+[A-Z])[\.:]?[ \t]+(?P<annex_title>\S.*?)
        )
        [ \t]*$
        '''
    )

    parts = SECTION_HEADING_RE.split(spec_content)
    headings = SECTION_HEADING_RE.findall(spec_content)

    # This case handles the extreme case where no splitting could be done
    if len(parts) == 1:
        return [{"heading": "Full Specification", "content": spec_content}]

    return [{"heading": heading, "content": part} for heading, part in zip(headings, parts[1:]) if part.strip()]


def summarize_spec(llm_config: dict, spec_content: str) -> str:
    """Return the concatenation of RFC sections that contain grammar/state-machine info."""
    sections = find_sections(spec_content)
    logger.info(f"Split spec into {len(sections)} section(s).")

    relevant_parts: list[str] = []

    for idx, section in enumerate(sections):
        header = section["heading"]
        content = section["content"]

        logger.debug(f"Evaluating section {idx + 1}/{len(sections)}: {header!r}")
        user_message = f"Section:\n{content}"

        raw = _query_llm_once(llm_config, SUMMARIZE_SECTION_SYSTEM_PROMPT, user_message)

        try:
            result = json.loads(raw)
            is_relevant = bool(result.get("relevant", False))
        except json.JSONDecodeError:
            logger.warning(f"Section {idx + 1}: LLM returned non-JSON; defaulting to not relevant.")
            is_relevant = False

        if is_relevant:
            logger.info(f"Section {idx + 1} marked relevant.")
            relevant_parts.append(content)
        else:
            logger.debug(f"Section {idx + 1} marked not relevant.")

    return "".join(relevant_parts)


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
