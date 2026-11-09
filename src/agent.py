from dataclasses import dataclass, field
from pathlib import Path
from src.tools.runtime import get_protocol_packet_paths
from src.tools.rfc import retrieve_rfc_by_id, clean_rfc_text
from src.tools.extraction import extract_intervals
from src.protocol import Protocol
from src.target_language import TargetLanguage
from src.logger import logger
from src.prompts import SUMMARIZE_SPEC_SYSTEM_PROMPT
from src.usage import extract_cost_summary, CostSummary
from src.providers import ModelConfig
from src import validation
from typing import Annotated, Any
import autogen


@dataclass
class State:
    protocol: Protocol
    attempt: int = 0
    last_code: str = ""
    success: bool = False
    best_score: int = 0
    best_code: str | None = None
    best_iteration: int | None = None
    termination_reason: str | None = None
    iterations: list[validation.IterationStats] = field(default_factory=list)
    retrieved_rfcs: list[str] | None = None


def _message_text(message: dict[str, Any]) -> str:
    """Read plain text from either AG2 chat or Responses message content."""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and part.get("type") in {"text", "output_text"}
        )
    return ""


def _chat_cost(chat_result: Any, llm_config: ModelConfig) -> dict[str, CostSummary | None] | None:
    if chat_result is None:
        return None
    return extract_cost_summary(chat_result.cost, llm_config.responses_price)


def query_llm_once(llm_config: ModelConfig, system_prompt: str, user_message: str) -> tuple[str, dict[str, Any] | None]:
    """Send a single user message to the LLM and return the reply text plus usage."""
    assistant = autogen.ConversableAgent(
        name="assistant",
        system_message=system_prompt,
        llm_config=llm_config.as_autogen_config(),
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

    return _message_text(assistant.last_message()), _chat_cost(chat_result, llm_config)


def summarize_spec(llm_config: ModelConfig, spec_content: str) -> tuple[str, dict[str, Any], dict[str, Any] | None]:
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
        llm_config=llm_config.as_autogen_config(),
    )

    user_proxy = autogen.UserProxyAgent(
        name="SpecProxy",
        human_input_mode="NEVER",
        max_consecutive_auto_reply=3,
        is_termination_msg=lambda msg: _message_text(msg).strip().endswith("TERMINATE") or result_holder.get("summary") is not None,
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
        return result_holder["summary"], result_holder["stats"], _chat_cost(chat_result, llm_config)

    logger.warning("LLM did not call select_intervals; returning empty summary.")
    return "", result_holder["stats"], _chat_cost(chat_result, llm_config)


def run_refinement_loop(
    llm_config: ModelConfig,
    system_message: str,
    spec_content: str,
    protocol: Protocol,
    target_language: TargetLanguage,
    agent_config: dict,
    debug_dir: Path | None = None,
) -> tuple[str, bool, dict[str, Any], dict[str, CostSummary] | None]:
    """Run the compile-feedback agent loop using Autogen.

    Returns a tuple of (grammar_code, completed_successfully, refinement_stats, cost_summary).
    """
    max_retries = agent_config.get("max_retries")
    preprocessing_enabled = agent_config.get("enable_preprocessing", True)
    rfc_retrieval_tool_enabled = agent_config.get("enable_rfc_retrieval_tool")
    negative_packets_enabled = agent_config.get("enable_negative_packets")
    show_packet_representation = agent_config.get("show_packet_representation")
    show_parsing_tree = agent_config.get("show_parsing_tree")
    include_parse_tree_summary = show_parsing_tree and target_language.supports_parse_tree_summary
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

    positive_packets, negative_packets = get_protocol_packet_paths(protocol.name)
    state = State(protocol=protocol, retrieved_rfcs=[] if rfc_retrieval_tool_enabled else None)

    def retrieve_rfc_by_id_wrapper(rfc_id: Annotated[str, "The ID of the RFC to retrieve (e.g., '1035')"]) -> str:
        logger.debug("Retrieving RFC with ID: %s", rfc_id)
        state.retrieved_rfcs.append(rfc_id)
        rfc_text = retrieve_rfc_by_id(rfc_id)
        if rfc_text is None:
            return f"Could not retrieve RFC {rfc_id}."
        return clean_rfc_text(rfc_text)

    response_cleaner = target_language.create_response_cleaner()

    def validate_grammar_tool(code: Annotated[str, "The grammar code to compile and verify against packets"]) -> str:
        cleaned_code = response_cleaner(code) or code
        previous_code = state.last_code
        state.attempt += 1
        state.last_code = cleaned_code
        state.iterations.append(
            validation.IterationStats(
                changed_from_previous=(
                    state.attempt > 1 and cleaned_code != previous_code
                ),
            )
        )
        validation_result = validation.validate_grammar(
            code,
            state.protocol,
            target_language,
            preprocessing_enabled=preprocessing_enabled,
            negative_packets_enabled=negative_packets_enabled,
            show_parsing_tree=show_parsing_tree,
            debug_dir=debug_dir,
            attempt=state.attempt,
            max_attempts=max_retries,
        )
        iteration = validation_result.iteration_stats()
        iteration.changed_from_previous = state.iterations[-1].changed_from_previous
        state.iterations[-1] = iteration

        check_result = validation_result.packet_check
        if check_result and check_result.setup_error:
            logger.warning(f"Runtime setup FAILED for '{state.protocol.name}'.")
        elif check_result and not check_result.verdicts:
            logger.warning(f"No packets registered for protocol '{state.protocol.name}'.")
        elif check_result:
            score = check_result.score
            if score > state.best_score or state.best_iteration is None:
                state.best_score = score
                state.best_code = validation_result.code
                state.best_iteration = state.attempt
                logger.info(f"New best score: {score} on attempt {state.attempt}.")

            if check_result.all_passed:
                state.success = True
                state.termination_reason = "success"
                logger.info(f"Runtime check PASSED for '{state.protocol.name}'.")
            else:
                logger.warning(f"Runtime check FAILED for '{state.protocol.name}'.")

        if not validation_result.success and state.attempt >= max_retries:
            state.termination_reason = "max_retries"
            logger.warning(f"Reached max compilation attempts ({max_retries}). Stopping.")
            return "This was the last allowed attempt. Please say TERMINATE."

        return validation_result.feedback(
            show_packet_representation=show_packet_representation
        )

    if show_parsing_tree and not include_parse_tree_summary:
        logger.info(
            f"{target_language.display_name} does not provide parse tree summaries; runtime feedback will include accept/reject errors only."
        )

    assistant = autogen.AssistantAgent(
        name=target_language.assistant_name,
        system_message=system_message,
        llm_config=llm_config.as_autogen_config(),
    )

    # max_consecutive_auto_reply is set higher than max_retries because Autogen
    # counts ALL auto-replies (including default_auto_reply nudges when the LLM
    # responds without a tool call), not just actual compilation attempts.
    # The real retry limit is enforced inside validate_grammar_tool via state.attempt.
    user_proxy = autogen.UserProxyAgent(
        name="CompilerProxy",
        human_input_mode="NEVER",
        max_consecutive_auto_reply=max_retries * 3,
        is_termination_msg=lambda msg: _message_text(msg).strip().endswith("TERMINATE") or state.attempt > max_retries,
        code_execution_config=False,
        # Needed to avoid auto stop when LLM doesnt issue a tool call
        default_auto_reply=(
            "Please use the `validate_grammar` tool to check if the grammar compiles and test it against protocol packets. "
            "If all tests pass, say TERMINATE."
        )
    )
    
    autogen.agentchat.register_function(
        validate_grammar_tool,
        caller=assistant,
        executor=user_proxy,
        name="validate_grammar",
        description=(
            f"Validate a {target_language.display_name} grammar for the current protocol. This compiles the grammar and tests it"
            "against known accept/reject packets. Returns success when the grammar compiles and all"
            "packet checks pass; otherwise returns compiler or runtime feedback."
        ),
    )

    if rfc_retrieval_tool_enabled:
        autogen.agentchat.register_function(
            retrieve_rfc_by_id_wrapper,
            caller=assistant,
            executor=user_proxy,
            name="retrieve_rfc_by_id",
            description="Retrieve an RFC text by its ID (e.g., '1035').",
        )


    initial_prompt = "\n".join([spec_content, "Please output the grammar and verify it with the given tools."])

    logger.info("System message and tools initialized. Starting the agent loop...")
    logger.info("LLM Config: %s", llm_config)
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
        state.termination_reason = "runtime_exception"

    if state.termination_reason is None:
        state.termination_reason = "agent_terminated"

    code_to_return = state.best_code if state.best_code is not None else state.last_code
    cost_summary = _chat_cost(chat_result, llm_config)
    first_successful_compile_iteration = next(
        (
            index
            for index, iteration in enumerate(state.iterations, start=1)
            if iteration.compiled
        ),
        None,
    )
    refinement_stats = {
        "positive_packets": len(positive_packets),
        "negative_packets": len(negative_packets) if negative_packets_enabled else 0,
        "max_total_retries": max_retries,
        "best_iteration": state.best_iteration,
        "termination_reason": state.termination_reason,
        "first_successful_compile_iteration": first_successful_compile_iteration,
        "iterations": [iteration.as_dict() for iteration in state.iterations],
        "retrieved_rfcs": state.retrieved_rfcs
    }
    return code_to_return, state.success, refinement_stats, cost_summary
