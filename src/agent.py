from pathlib import Path
from src.llm.interface import LLMInterface, Messages
from src.compiler import compile_spicy_grammar


FEEDBACK_TEMPLATE = """The grammar you produced failed to compile with the following errors:

{errors}

Please fix the grammar to resolve these compilation errors. Only output the corrected grammar."""


def clean_response(text: str) -> str:
    """Strip markdown fences and leading non-grammar lines."""
    text = text.strip().strip("```").strip()
    while text and not text.startswith("module"):
        text = "\n".join(text.splitlines()[1:]).strip()
    return text


def run_refinement_loop(
    client: LLMInterface,
    system_instruction: str,
    messages: Messages,
    max_retries: int = 5,
    debug_dir: Path | None = None,
) -> tuple[str, bool]:
    """Run the compile-feedback agent loop.

    Returns a tuple of (grammar_code, compiled_successfully).
    """
    if debug_dir:
        debug_dir.mkdir(parents=True, exist_ok=True)

    for attempt in range(1, max_retries + 1):
        print(f"[agent] Attempt {attempt}/{max_retries} ...")

        response = client.chat(system_instruction, messages)
        code = clean_response(response)
        step_path = debug_dir / f"{attempt}-step.spicy" if debug_dir else None
        result = compile_spicy_grammar(code, step_path)

        if result.success:
            print(f"[agent] Compilation succeeded on attempt {attempt}.")
            return code, True

        print(f"[agent] Compilation failed on attempt {attempt}.")
        print(f"[agent] Errors:\n{result.errors}\n")

        # Feed errors back as a new user message for the next iteration.
        feedback = FEEDBACK_TEMPLATE.format(errors=result.errors)
        messages.append(feedback)

    print(f"[agent] Max retries ({max_retries}) exhausted.")
    return code, False
