from src.target_language import TargetLanguage

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

BASE_GENERATION_PROMPT = """You are a communication protocol's specialist called {name}, and your job is to extract message structures from RFC specifications.
You will be given a manual instructing you the language in which you have to extract the grammar of the messages from the protocol.
Make sure to capture all the fields relationships. Value constraints for fields (e.g. "MUST be equal to 1") must be ommitted, but the field name must be included in the grammar.
Use the tools provided to generate correct and complete grammars in this language for the RFC the user gives you as input.
Retrieve any additional information you need from other RFCs so the grammar is as complete as possible.
"""

AGENT_LOOP_INSTRUCTIONS_TEMPLATE = """
IMPORTANT WORKFLOW — follow these steps in order:

1. Write the grammar and use the `compile_grammar` tool to check if it compiles.
2. If ``compile_grammar`` returns SUCCESS, use the `parse_packets` tool with the same grammar code to verify it against known protocol packets.
   - If all packets pass, say TERMINATE.
   - If some packets fail, fix the grammar and go back to step 1.{runtime_hint}
3. If ``compile_grammar`` returns errors, fix the grammar and try again.
4. If there are no packets registered for this protocol, you will be told so — in that case say TERMINATE after a successful compilation.
{rfc_retrieval_tool_hint}
"""


def build_agent_loop_instructions(
    target_language: TargetLanguage,
    rfc_retrieval_tool_enabled: bool,
) -> str:
    runtime_hint = ""
    rfc_retrieval_tool_hint = ""
    if target_language.supports_parse_tree_summary:
        runtime_hint = (
            " Use the packet summary provided in the runtime feedback to understand "
            "what is wrong with the grammar."
        )
    if rfc_retrieval_tool_enabled:
        rfc_retrieval_tool_hint = (
            "You can also use the retrieve_rfc_by_id tool to fetch additional RFC "
            "documents if needed."
        )

    return AGENT_LOOP_INSTRUCTIONS_TEMPLATE.format(
        runtime_hint=runtime_hint,
        rfc_retrieval_tool_hint=rfc_retrieval_tool_hint,
    )


def build_refinement_system_message(
    model_name: str,
    target_language_context: str,
    target_language: TargetLanguage,
    rfc_retrieval_tool_enabled: bool,
) -> str:
    base_prompt = BASE_GENERATION_PROMPT.format(name=model_name)
    loop_instructions = build_agent_loop_instructions(
        target_language,
        rfc_retrieval_tool_enabled=rfc_retrieval_tool_enabled,
    )
    return f"{base_prompt}{target_language_context}{loop_instructions}"
