from dataclasses import dataclass
from pathlib import Path
from src.tools.compiler import compile_spicy_grammar
from src.tools.rfc import retrieve_rfc_by_id, clean_rfc_text
from src.tools.runtime import run_parser_check_from_code
from src.protocol import Protocol
from src.logger import logger
from typing import Annotated
import autogen

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
   - If some packets fail, fix the grammar and go back to step 1.
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

def run_refinement_loop(
    llm_config: dict,
    system_message: str,
    messages: list[str],
    protocol: Protocol,
    agent_config: dict,
    debug_dir: Path | None = None,
) -> tuple[str, bool, int]:
    """Run the compile-feedback agent loop using Autogen.

    Returns a tuple of (grammar_code, compiled_successfully, attempts).
    """
    max_retries = agent_config.get("max_retries")
    use_parsing_tree_tool = agent_config.get("use_parsing_tree_tool")
    refinement_dir = None
    errors_dir = debug_dir / "errors" if debug_dir else None
    if debug_dir:
        refinement_dir = debug_dir / "refinement"
        errors_dir = debug_dir / "errors"
        debug_dir.mkdir(parents=True, exist_ok=True)
        refinement_dir.mkdir(parents=True, exist_ok=True)
        errors_dir.mkdir(parents=True, exist_ok=True)


    state = State(protocol=protocol)

    def compile_grammar(code: Annotated[str, "The Spicy grammar code to compile"]) -> str:
        cleaned_code = clean_response(code)
        if not cleaned_code:
            cleaned_code = code # Fallback just in case
        
        state.attempt += 1
        state.last_code = cleaned_code
        step_path = refinement_dir / f"{state.attempt}-step.spicy" if refinement_dir else None
        errors_path = errors_dir / f"{state.attempt}-errors.log" if errors_dir else None

        logger.info(f"Compiling attempt {state.attempt}/{max_retries} ...")

        result = compile_spicy_grammar(cleaned_code, { "step_path": step_path, "errors_path": errors_path })

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

        logger.info(f"Running Runtime check for protocol '{state.protocol.name}'...")
        check_result = run_parser_check_from_code(cleaned_code, state.protocol, show_parsing_tree=use_parsing_tree_tool, debug_dir=debug_dir)

        if not check_result.verdicts:
            logger.warning(f"No packets registered for protocol '{state.protocol.name}'.")
            return f"No packets registered for protocol '{state.protocol.name}'. Say TERMINATE if the grammar compiled successfully."

        summary = check_result.summary()
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

    user_proxy.initiate_chat(
        assistant,
        message=initial_prompt,
    )

    return state.last_code, state.success, state.attempt
