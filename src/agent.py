from dataclasses import dataclass
from pathlib import Path
from src.tools.compiler import compile_spicy_grammar
from src.tools.rfc import retrieve_rfc_by_id, clean_rfc_text
from src.tools.runtime import run_parser_check_from_code
from src.tools.extraction import extract_intervals
from src.protocol import Protocol
from src.logger import logger
from src.usage import extract_cost_summary, CostSummary
from typing import Annotated, Any
import autogen

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


COMPILATION_FEEDBACK_TEMPLATE = """The grammar you produced failed to compile with the following errors:

{errors}

Please fix the grammar to resolve these compilation errors. Only output the corrected grammar."""

RUNTIME_FEEDBACK_TEMPLATE = """The grammar you produced failed to parse the following packets:

{errors}

Please fix the grammar to handle these packets correctly, then use compile_grammar again."""

AGENT_LOOP_INSTRUCTIONS = """
IMPORTANT WORKFLOW — follow these steps in order:

1. Write the grammar and use the `compile_grammar` tool to check if it compiles.
2. If ``compile_grammar`` returns SUCCESS, use the `parse_packets` tool with the same grammar code to verify it against known protocol packets.
   - If all packets pass, say TERMINATE.
   - If some packets fail, fix the grammar and go back to step 1. Use the summary provided for each packet to understand what is wrong with the grammar.
3. If ``compile_grammar`` returns errors, fix the grammar and try again.
4. If there are no packets registered for this protocol, you will be told so — in that case say TERMINATE after a successful compilation.

You can also use the retrieve_rfc_by_id tool to fetch additional RFC documents if needed.
"""


@dataclass
class State:
    protocol: Protocol
    attempt: int = 0
    last_code: str = ""
    success: bool = False
    best_score: int = 0
    best_code: str | None = None

def clean_response(text: str) -> str:
    """Strip markdown fences and leading non-grammar lines."""
    if not text:
        return ""
    text = text.strip().strip("```").strip()
    while text and not text.startswith("module"):
        text = "\n".join(text.splitlines()[1:]).strip()
    return text

def retrieve_rfc_by_id_wrapper(rfc_id: Annotated[str, "The ID of the RFC to retrieve (e.g., '1035')"]) -> str:
    logger.debug("Retrieving RFC with ID: %s", rfc_id)
    rfc_text = retrieve_rfc_by_id(rfc_id)
    if rfc_text is None:
        return f"Could not retrieve RFC {rfc_id}."
    return clean_rfc_text(rfc_text)


def query_llm_once(llm_config: dict, system_prompt: str, user_message: str) -> tuple[str, dict[str, Any] | None]:
    """Send a single user message to the LLM and return the reply text plus usage."""
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
    
    chat_result = None
    try:
        chat_result = user_proxy.initiate_chat(
            assistant,
            message=user_message,
            max_turns=1,
            silent=True,
        )
    except Exception as e:
        logger.error(f"Agent loop terminated with an exception: {e}")

    return assistant.last_message()["content"], extract_cost_summary(chat_result.cost) if chat_result is not None else None


def summarize_spec(llm_config: dict, spec_content: str) -> tuple[str, dict[str, Any], dict[str, Any] | None]:
    """Use an LLM agent with a select_intervals tool to identify and extract
    the parts of *spec_content* that describe message grammar, format, or
    protocol state machines.  Returns the concatenated relevant text.
    """
    # Mutable container so the nested tool function can store its result.
    result_holder: dict[str, Any] = {
        "summary": None,
        "stats": {
            "requested_intervals": 0,
            "resolved_intervals": 0,
            "merged_intervals": 0,
            "skipped_invalid_pairs": 0,
            "missing_start_markers": 0,
            "missing_end_markers": 0,
            "summary_chars": 0,
            "summary_lines": 0,
            "summary_nonempty_lines": 0,
            "source_chars": len(spec_content),
            "coverage_ratio": 0.0,
        },
    }

    def select_intervals(
        intervals: Annotated[list[list[str]],
            "Array of [start_marker, end_marker] pairs. "
            "Each marker must be an exact substring copied verbatim from the "
            "document (use full lines to reduce ambiguity)."],
    ) -> str:
        """Mark intervals of the specification that are relevant to protocol
        grammar, message format, or state machine description."""
        logger.info(f"select_intervals called with {len(intervals)} interval(s).")
        summary, stats = extract_intervals(spec_content, intervals)
        result_holder["summary"] = summary
        result_holder["stats"] = stats
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
    chat_result = user_proxy.initiate_chat(
        assistant,
        message=user_message,
    )

    if result_holder["summary"] is not None:
        logger.info(f"Spec summarization complete — {len(result_holder['summary'])} chars extracted.")
        return result_holder["summary"], result_holder["stats"], extract_cost_summary(chat_result.cost)

    logger.warning("LLM did not call select_intervals; returning empty summary.")
    return "", result_holder["stats"], extract_cost_summary(chat_result.cost)


def run_refinement_loop(
    llm_config: dict,
    system_message: str,
    messages: list[str],
    protocol: Protocol,
    agent_config: dict,
    debug_dir: Path | None = None,
) -> tuple[str, bool, int, dict[str, CostSummary]]:
    """Run the compile-feedback agent loop using Autogen.

    Returns a tuple of (grammar_code, compiled_successfully, attempts, cost_summary).
    """
    max_retries = agent_config.get("max_retries")
    show_packet_representation = agent_config.get("show_packet_representation")
    show_parsing_tree = agent_config.get("show_parsing_tree")
    refinement_dir = None
    errors_dir = None
    build_dir = None
    if debug_dir:
        refinement_dir = debug_dir / "refinement"
        errors_dir = debug_dir / "errors"
        build_dir = debug_dir / "build"
        debug_dir.mkdir(parents=True, exist_ok=True)
        refinement_dir.mkdir(parents=True, exist_ok=True)
        errors_dir.mkdir(parents=True, exist_ok=True)
        build_dir.mkdir(parents=True, exist_ok=True)

    state = State(protocol=protocol)

    def compile_grammar(code: Annotated[str, "The Spicy grammar code to compile"]) -> str:
        cleaned_code = clean_response(code)
        if not cleaned_code:
            cleaned_code = code # Fallback just in case
        
        state.attempt += 1
        state.last_code = cleaned_code
        step_path = refinement_dir / f"{state.attempt}-step.spicy" if refinement_dir else None
        errors_path = errors_dir / f"{state.attempt}-compilation.log" if errors_dir else None
        debug_info = { "step_path": step_path, "errors_path": errors_path }

        logger.info(f"Compiling attempt {state.attempt}/{max_retries} ...")

        result = compile_spicy_grammar(cleaned_code, debug_info=debug_info)

        if result.success:
            logger.info(f"Compilation succeeded on attempt {state.attempt}.")
            return "SUCCESS"

        logger.warning(f"Compilation failed on attempt {state.attempt}.")
        logger.error(f"Compilation Errors:\n{result.errors}\n")

        if state.attempt >= max_retries:
            logger.warning(f"Reached max compilation attempts ({max_retries}). Stopping.")
            return f"{COMPILATION_FEEDBACK_TEMPLATE.format(errors=result.errors)}\n\nThis was the last allowed attempt. Please say TERMINATE."

        return COMPILATION_FEEDBACK_TEMPLATE.format(errors=result.errors)

    def parse_packets(code: Annotated[str, "The Spicy grammar code to parse packets with"]) -> str:
        """Run the grammar against known protocol packets."""
        cleaned_code = clean_response(code)
        if not cleaned_code:
            cleaned_code = code

        build_path = build_dir / f"{state.attempt}.hlto" if build_dir else None
        errors_path = errors_dir / f"{state.attempt}-runtime.log" if errors_dir else None
        debug_info = { "build_path": build_path, "errors_path": errors_path }

        logger.info(f"Running Runtime check for protocol '{state.protocol.name}'...")
        check_result = run_parser_check_from_code(cleaned_code, state.protocol, show_parsing_tree=show_parsing_tree, debug_info=debug_info)

        if not check_result.verdicts:
            logger.warning(f"No packets registered for protocol '{state.protocol.name}'.")
            return f"No packets registered for protocol '{state.protocol.name}'. Say TERMINATE if the grammar compiled successfully."

        score = check_result.score
        if score > state.best_score:
            state.best_score = score
            state.best_code = cleaned_code
            logger.info(f"New best score: {score} on attempt {state.attempt}.")

        summary = check_result.summary(show_packet_representation=show_packet_representation)
        if check_result.all_passed:
            state.success = True
            logger.info(f"Runtime check PASSED for '{state.protocol.name}'.")
            return f"All packets parsed correctly. Say TERMINATE."
        else:
            logger.warning(f"Runtime check FAILED for '{state.protocol.name}'.")
            return RUNTIME_FEEDBACK_TEMPLATE.format(errors=summary)

    system_message = system_message + AGENT_LOOP_INSTRUCTIONS
    assistant = autogen.AssistantAgent(
        name="SpicyDeveloper",
        system_message=system_message,
        llm_config=llm_config,
    )

    # max_consecutive_auto_reply is set higher than max_retries because Autogen
    # counts ALL auto-replies (including default_auto_reply nudges when the LLM
    # responds without a tool call), not just actual compilation attempts.
    # The real retry limit is enforced inside compile_grammar via state.attempt.
    user_proxy = autogen.UserProxyAgent(
        name="CompilerProxy",
        human_input_mode="NEVER",
        max_consecutive_auto_reply=max_retries * 3,
        is_termination_msg=lambda x: (x.get("content", "") and "TERMINATE" in x.get("content", "")) or state.attempt > max_retries,
        code_execution_config=False,
        # Needed to avoid auto stop when LLM doesnt issue a tool call
        default_auto_reply="Please use the `compile_grammar` tool to check if the grammar compiles. If it does not compile, correct the errors. If you think the grammar is perfect or cannot be fixed, say TERMINATE.",
    )
    
    autogen.agentchat.register_function(
        compile_grammar,
        caller=assistant,
        executor=user_proxy,
        name="compile_grammar",
        description="Compile a Spicy grammar string. Returns TERMINATE if successful, otherwise returns compilation errors.",
    )

    autogen.agentchat.register_function(
        retrieve_rfc_by_id_wrapper,
        caller=assistant,
        executor=user_proxy,
        name="retrieve_rfc_by_id",
        description="Retrieve an RFC text by its ID (e.g., '1035').",
    )

    autogen.agentchat.register_function(
        parse_packets,
        caller=assistant,
        executor=user_proxy,
        name="parse_packets",
        description="Run the grammar against known accept/reject packets for the current protocol. Use this AFTER a successful compilation to verify semantic correctness.",
    )

    initial_prompt = "\n".join(messages)
    initial_prompt += "\n\nPlease output the grammar and use the compile_grammar tool to verify it."

    logger.info("System message and tools initialized. Starting the agent loop...")
    logger.info(f"LLM Config: {llm_config | { 'config_list' : '...' }}")
    logger.info(f"Agent config: {agent_config}")
    logger.info(f"Tools available: {[tool.name for tool in assistant.tools]}")
    logger.debug(f"Initial system message:\n{system_message}")
    logger.debug(f"Initial user message (start):\n{initial_prompt[:300]}\n...")
    logger.debug(f"Initial user message (end):\n{initial_prompt[-300:]}\n...")

    chat_result = None
    try:
        chat_result = user_proxy.initiate_chat(
            assistant,
            message=initial_prompt,
        )
    except Exception as e:
        logger.error(f"Agent loop terminated with an exception: {e}")

    code_to_return = state.best_code if state.best_code is not None else state.last_code
    cost_summary = extract_cost_summary(chat_result.cost) if chat_result is not None else None
    return code_to_return, state.success, state.attempt, cost_summary
