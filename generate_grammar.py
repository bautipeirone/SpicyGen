from dotenv import load_dotenv
load_dotenv()

from src.providers import get_autogen_config
from src.agent import run_refinement_loop
from src.utils import load_file, read_config
from src.logger import enable_runtime_log_file, logger
from src.protocol import load_protocol, load_spec, ProtocolType
from pathlib import Path
import argparse
import time
import json

PROMPT_TEMPLATE = """You are a communication protocol's specialist called {name}, and your job is to extract message structures from RFC specifications.
You will be given a manual instructing you the language in which you have to extract the grammar of the messages from the protocol.
Make sure to capture all the fields relationships. Value constraints for fields (e.g. "MUST be equal to 1") must be ommitted, but the field name must be included in the grammar.
Use the tools provided to generate correct and complete grammars in this language for the RFC the user gives you as input.
Retrieve any additional information you need from other RFCs using the provided tool, so the grammar is as complete as possible.
"""

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract protocol grammar from RFCs using an LLM."
    )
    parser.add_argument(
        "--model",
        "-m",
        action="store",
        default=None,
        help="Model to query (must be a key from the model_index.json file)",
    )
    parser.add_argument(
        "--instructions-file",
        "-i",
        type=Path,
        default=None,
        help="Path to the instructions file that will be added to the prompt",
    )
    parser.add_argument(
        "protocol",
        type=str,
        help="Protocol name (e.g. 'dhcp', 'dns') used to look up packets for runtime check.",
    )
    parser.add_argument(
        "--output-file",
        "-o",
        type=Path,
        help="Path to the output file where the extracted grammar will be saved (optional)",
    )
    parser.add_argument(
        "--metrics-file",
        type=Path,
        default=None,
        help="Path to output the metrics JSON file",
    )
    parser.add_argument(
        "--debug-dir",
        type=Path,
        default=None,
        help="Directory to save debug information like refinement steps and compilation errors for each attempt (optional)",
    )
    parser.add_argument(
        "--spec-file",
        type=Path,
        default=None,
        help="Path to a custom specification file to use instead of the one resolved from protocol_index.json "
             "(e.g. a preprocessed summary from preprocess_spec.py).",
    )
    parser.add_argument(
        "--config",
        "-c",
        type=Path,
        default=None,
        help="Path to a config.yaml file containing generation parameters like temperature, max_tokens, etc.",
    )
    return parser.parse_args()


def save_response(path: Path, response: str) -> None:
    try:
        with path.open("w") as f:
            f.write(response)
    except OSError as exc:
        raise SystemExit(f"Unable to write to {path}: {exc}") from exc


def get_spicy_context_file(protocol_type: ProtocolType) -> Path:
    match protocol_type:
        case ProtocolType.TEXT:
            instructions_path = Path("./prompts/TEXT.md")
        case ProtocolType.BINARY:
            instructions_path = Path("./prompts/BINARY.md")
        case _:
            raise ValueError(f"Unsupported protocol type: {protocol_type}")
    return instructions_path


def main() -> None:
    args = parse_args()
    runtime_log_path = args.debug_dir / "runtime.log" if args.debug_dir else None
    enable_runtime_log_file(runtime_log_path)

    logger.info(f"Generating grammar for {args.protocol} using {args.model}")
    logger.debug(f"Instructions file: {args.instructions_file}")
    logger.debug(f"Output file: {args.output_file}")
    logger.debug(f"Debug dir: {args.debug_dir}")
    logger.debug(f"Config file: {args.config}")
    logger.debug(f"Spec file override: {args.spec_file}")

    protocol = load_protocol(args.protocol)
    if protocol is None:
        logger.error(f"Protocol '{args.protocol}' not found in index. Exiting.")
        exit(1)

    instructions_file = args.instructions_file
    if instructions_file is None:
        instructions_file = get_spicy_context_file(protocol.type)
        logger.debug(f"Using default spicy instructions file: {instructions_file}")

    spicy_context = load_file(instructions_file)

    if args.spec_file:
        logger.info(f"Using custom spec file: {args.spec_file}")
        spec_content = load_file(args.spec_file)
    else:
        spec_content = load_spec(protocol)

    if spec_content is None:
        logger.error(f"No specification content found for protocol '{protocol.name}'.")
        exit(1)

    llm_params, agent_config = read_config(args.config)
    llm_config = get_autogen_config(args.model, llm_params)

    model_name = args.model.split("-")[0]
    prompt = PROMPT_TEMPLATE.format(name=model_name)
    system_instruction = prompt + spicy_context
    messages = [spec_content]

    start_time = time.time()
    content, success, attempts, conversation_usage = run_refinement_loop(
        llm_config, system_instruction, messages,
        protocol=protocol,
        agent_config=agent_config,
        debug_dir=args.debug_dir,
    )
    elapsed_time = time.time() - start_time

    metrics = {
        "model": args.model,
        "protocol": protocol.name,
        "success": success,
        "attempts": attempts,
        "time_elapsed_s": round(elapsed_time, 2),
        "usage": conversation_usage,
    }

    if args.metrics_file:
        with args.metrics_file.open("w") as f:
            json.dump(metrics, f)
    else:
        print(json.dumps(metrics))

    if not success:
        logger.warning("Grammar did not pass tests.")

    if args.output_file:
        save_response(args.output_file, content)
        logger.info(f"Saved grammar to {args.output_file}")
    else:
        print(content)

    return 0 if success else 1

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.error(f"Grammar generation failed: {e}")
        exit(1)
