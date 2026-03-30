from src.llm.providers import get_autogen_config
from src.agent import run_refinement_loop
from typing import LiteralString
from pathlib import Path
import argparse
from dotenv import load_dotenv
import re
import yaml

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
        choices=["ollama", "google", "openai", "anthropic"],
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
        "--debug-dir",
        type=Path,
        default=None,
        help="Directory to save persistent refinement step files",
    )
    parser.add_argument(
        "--config",
        "-c",
        type=Path,
        default=None,
        help="Path to a config.yaml file containing generation parameters like temperature, max_tokens, etc.",
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


def clean_rfc_text(text: str):
    header_pattern = re.compile(r'RFC (\d+)\s+(.*?)\s+([A-Za-z]+ \d{4})')
    footer_pattern = re.compile(r'^.*\s+\[Page \d+\]$', re.MULTILINE)

    text = header_pattern.sub('', text)
    text = footer_pattern.sub('', text)

    text = re.sub(r'\n\s*\n', '\n', text)
    return text.strip()

def read_config(path: Path | None) -> tuple[dict, dict]:
    # Defaults
    agent_config = {
        "max_retries": 5
    }
    llm_params = {
        "temperature": 0.0
    }

    if path and path.exists():
        with path.open("r") as f:
            parsed = yaml.safe_load(f)
            if isinstance(parsed, dict):
                if parsed.get("llm"):
                    llm_params = parsed.get("llm")
                if parsed.get("agent"):
                    agent_config = parsed.get("agent")

    return llm_params, agent_config

def main() -> None:
    args = parse_args()
    load_dotenv()

    spicy_context = load_file(args.instructions_file)
    rfc_content = load_file(args.rfc_file)
    rfc_content = clean_rfc_text(rfc_content)

    llm_params, agent_config = read_config(args.config)
    llm_config = get_autogen_config(args.provider, args.model, llm_params)

    system_instruction = prompt + spicy_context
    messages = [rfc_content]

    content, success = run_refinement_loop(
        llm_config, system_instruction, messages, max_retries=agent_config["max_retries"], debug_dir=args.debug_dir
    )

    if not success:
        print("[agent] WARNING: grammar did not compile after all retries.")

    if args.output_file:
        save_response(args.output_file, content)
    else:
        print(content)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("[ERROR]: Grammar generation failed:", e)
        exit(1)
