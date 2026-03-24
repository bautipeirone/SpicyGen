from src.llm.providers import get_llm_client
from src.agent import run_refinement_loop, clean_response
from typing import LiteralString
from pathlib import Path
import argparse
from dotenv import load_dotenv

prompt: LiteralString = """You are a communication protocol's specialist, and your job is to extract message structures from RFC specifications.
You will be given a manual instructing you the language in which you have to extract the grammar of the messages from the protocol.
Make sure to capture all the field value constraints and relationships.
Only output the grammar in this language for the RFC the user gives you as input.
"""

SPICY_CONTEXT_PATH = Path("./INSTRUCTIONS.md")

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract protocol grammar from RFCs using an LLM."
    )
    parser.add_argument(
        "--model",
        "-m",
        action="store",
        default=None,
        help="Model to query",
    )
    parser.add_argument(
        "--instructions-file",
        "-i",
        type=Path,
        default=SPICY_CONTEXT_PATH,
        help="Path to the instructions file that will be added to the prompt",
    )
    parser.add_argument(
        "rfc_file",
        type=Path,
        help="Path to the rfc file that will be added to the prompt",
    )
    parser.add_argument(
        "--provider",
        "-p",
        choices=["ollama", "google", "openai"],
        default="ollama",
        help="LLM provider to use (default: ollama)",
    )
    parser.add_argument(
        "--output-file",
        "-o",
        type=Path,
        help="Path to the output file where the extracted grammar will be saved (optional)",
    )
    parser.add_argument(
        "--refine",
        action="store_true",
        help="Enable the agent refinement loop: compile the output and feed errors back to the LLM until it compiles",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=5,
        help="Maximum number of refinement iterations (default: 5, only used with --refine)",
    )
    parser.add_argument(
        "--debug-dir",
        type=Path,
        default=None,
        help="Directory to save persistent refinement step files",
    )
    return parser.parse_args()


def load_file(path: Path) -> str:
    try:
        with path.open("r") as f:
            return f.read()
    except OSError as exc:
        raise SystemExit(f"Unable to read {path}: {exc}") from exc


def save_response(path: Path, response: str) -> None:
    try:
        with path.open("w") as f:
            f.write(response)
    except OSError as exc:
        raise SystemExit(f"Unable to write to {path}: {exc}") from exc


def main() -> None:
    args = parse_args()
    load_dotenv()

    spicy_context = load_file(args.instructions_file)
    rfc_content = load_file(args.rfc_file)

    client = get_llm_client(args.provider, args.model)

    system_instruction = prompt + spicy_context
    messages = [rfc_content]

    if args.refine:
        content, success = run_refinement_loop(
            client, system_instruction, messages, max_retries=args.max_retries, debug_dir=args.debug_dir
        )
        if not success:
            print("[agent] WARNING: grammar did not compile after all retries.")
    else:
        response = client.chat(system_instruction, messages)
        content = clean_response(response)

    if args.output_file:
        save_response(args.output_file, content)
    else:
        print(content)


if __name__ == "__main__":
    main()
