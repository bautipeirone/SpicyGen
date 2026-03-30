from dataclasses import dataclass
from pathlib import Path
from src.tools.compiler import compile_spicy_grammar
from src.tools.rfc import retrieve_rfc_by_id, clean_rfc_text
from dataclasses import dataclass

from typing import Annotated
import autogen

FEEDBACK_TEMPLATE = """The grammar you produced failed to compile with the following errors:

{errors}

Please fix the grammar to resolve these compilation errors. Only output the corrected grammar."""

@dataclass
class State:
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
    rfc_text = retrieve_rfc_by_id(rfc_id)
    if rfc_text is None:
        return f"Could not retrieve RFC {rfc_id}."
    return clean_rfc_text(rfc_text)

def run_refinement_loop(
    llm_config: dict,
    system_instruction: str,
    messages: list[str],
    max_retries: int = 5,
    debug_dir: Path | None = None,
) -> tuple[str, bool]:
    """Run the compile-feedback agent loop using Autogen.

    Returns a tuple of (grammar_code, compiled_successfully).
    """
    if debug_dir:
        debug_dir.mkdir(parents=True, exist_ok=True)

    state = State()

    def compile_grammar(code: Annotated[str, "The Spicy grammar code to compile"]) -> str:
        cleaned_code = clean_response(code)
        if not cleaned_code:
            cleaned_code = code # Fallback just in case
        
        state.attempt += 1
        state.last_code = cleaned_code
        step_path = debug_dir / f"{state.attempt}-step.spicy" if debug_dir else None

        print(f"[agent] Compiling attempt {state.attempt}/{max_retries} ...")
        result = compile_spicy_grammar(cleaned_code, step_path)

        if result.success:
            state.success = True
            print(f"[agent] Compilation succeeded on attempt {state.attempt}.")
            return "TERMINATE"

        print(f"[agent] Compilation failed on attempt {state.attempt}.")
        print(f"[agent] Errors:\n{result.errors}\n")
        return FEEDBACK_TEMPLATE.format(errors=result.errors)

    assistant = autogen.AssistantAgent(
        name="SpicyDeveloper",
        system_message=system_instruction + "\n\nWhen you emit the grammar code, use the compile_grammar tool to check if it compiles. If it returns TERMINATE, you must say TERMINATE to end the chat. You can also use the retrieve_rfc_by_id tool to fetch additional RFC documents if needed.",
        llm_config=llm_config,
    )

    user_proxy = autogen.UserProxyAgent(
        name="CompilerProxy",
        human_input_mode="NEVER",
        max_consecutive_auto_reply=max_retries,
        is_termination_msg=lambda x: x.get("content", "") and "TERMINATE" in x.get("content", ""),
        code_execution_config=False,
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

    initial_prompt = "\n".join(messages)
    initial_prompt += "\n\nPlease output the grammar and use the compile_grammar tool to verify it."

    user_proxy.initiate_chat(
        assistant,
        message=initial_prompt,
    )

    return state.last_code, state.success
