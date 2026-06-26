import yaml
from pathlib import Path
import difflib
import json
from typing import Any

def read_config(path: Path | None) -> tuple[dict, dict]:
    # Defaults
    agent_config = {
        "max_retries": 15,
        "enable_rfc_retrieval_tool" : False,
        "show_packet_representation": False,
        "show_parsing_tree": False
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

def load_file(path: Path) -> str:
    try:
        with path.open("r") as f:
            return f.read()
    except OSError as exc:
        raise SystemExit(f"Unable to read {path}: {exc}") from exc

def write_json_file(path: Path, payload: dict[str, Any]) -> None:
    try:
        path.write_text(json.dumps(payload, indent=2))
    except OSError as exc:
        raise SystemExit(f"Unable to write to {path}: {exc}") from exc

def generate_diff(str_a: str, str_b: str) -> str | None:    
    diff_generator = difflib.unified_diff(
        str_a.splitlines(keepends=True),
        str_b.splitlines(keepends=True),
        n=3
    )
    
    diff_lines = list(diff_generator)
    
    if not diff_lines:
        return ""

    # Skip '---' and '+++' headers
    clean_diff = "".join(diff_lines[2:])
    
    return clean_diff

def compute_text_stats(text: str | None) -> dict[str, Any]:
    if not text:
        return {
            "chars": 0,
            "lines": 0,
            "nonempty_lines": 0,
        }

    lines = text.splitlines()
    return {
        "chars": len(text),
        "lines": len(lines),
        "nonempty_lines": sum(1 for line in lines if line.strip()),
    }

def strip_code_fences(text: str) -> str:
    if not text:
        return ""
    return text.strip().strip("```").strip()

def strip_response_fences(raw: str) -> str:
    _, _, content = raw.strip().rpartition("</think>")
    return strip_code_fences(content)
