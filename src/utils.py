import yaml
from pathlib import Path

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

def load_file(path: Path) -> str:
    try:
        with path.open("r") as f:
            return f.read()
    except OSError as exc:
        raise SystemExit(f"Unable to read {path}: {exc}") from exc
