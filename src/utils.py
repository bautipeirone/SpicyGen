import yaml
from pathlib import Path
import difflib

def read_config(path: Path | None) -> tuple[dict, dict]:
    # Defaults
    agent_config = {
        "max_retries": 15,
        "use_diff_tool": True
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
