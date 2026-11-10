import os
from pathlib import Path
import json
from dataclasses import dataclass, field
from typing import Any, Mapping

MODEL_INDEX_PATH = Path(__file__).parents[1] / "model_index.json"

_AUTOGEN_TOP_LEVEL_PARAMS = (
    "temperature", "max_tokens", "top_p", "timeout", "seed", "cache_seed",
    "parallel_tool_calls", "check_every_ms", "allow_format_str_template",
    "response_format", "routing_method",
)
_SUPPORTED_PROVIDERS = frozenset({"ollama", "openai", "google", "anthropic", "openai-account"})

def read_model_index():
    try:
        with MODEL_INDEX_PATH.open("r") as f:
            return json.load(f)
    except OSError as exc:
        raise SystemExit(f"Unable to read model index from {MODEL_INDEX_PATH}: {exc}") from exc

@dataclass(frozen=True, slots=True)
class ModelConfig:
    """Inspectable model settings. Credentials are resolved only for AG2."""

    model: str
    model_name: str
    provider: str
    reasoning_effort: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    top_p: float | None = None
    timeout: int | None = None
    seed: int | None = None
    cache_seed: int | None = None
    parallel_tool_calls: bool | None = None
    check_every_ms: int | None = None
    allow_format_str_template: bool | None = None
    response_format: Any = field(default=None, repr=False)
    routing_method: str | None = None
    price: tuple[float, float] | None = None
    cached_price: float | None = None

    def __post_init__(self) -> None:
        if self.provider not in _SUPPORTED_PROVIDERS:
            raise ValueError(f"Unsupported provider for Autogen: {self.provider}")

    @property
    def uses_responses(self) -> bool:
        return self.provider == "openai" and self.reasoning_effort not in (None, "none")

    def register_clients(self, agent: Any) -> Any:
        """Attach custom AG2 model clients required by this config to *agent*.

        AG2 rebuilds an agent's client whenever a tool is registered on it, which
        drops custom clients. The hook re-attaches the client before each reply,
        sharing one replay store so encrypted reasoning survives the rebuild.
        """
        if not self.uses_responses:
            return agent
        from autogen.oai.client import PlaceHolderClient
        from src.responses_client import StatelessResponsesClient

        client_kwargs = {"price": self.price, "cached_price": self.cached_price, "turn_items": {}}

        def attach(agent: Any, *_: Any) -> None:
            if any(isinstance(c, PlaceHolderClient) for c in agent.client._clients):
                agent.register_model_client(StatelessResponsesClient, **client_kwargs)

        attach(agent)
        agent.register_hook("update_agent_state", attach)
        return agent

    def as_autogen_config(self) -> dict[str, Any]:
        """Build the AG2 config, resolving secrets at the point of use."""
        entry: dict[str, Any] = {"model": self.model_name}
        if self.provider == "ollama":
            entry.update({
                "api_key": os.getenv("OLLAMA_API_KEY", ""),
                "base_url": os.environ["OLLAMA_HOST_ENDPOINT"],
            })
        elif self.provider == "openai":
            entry["api_key"] = os.getenv("OPENAI_API_KEY", "")
            if self.uses_responses:
                # AG2's own Responses clients duplicate history on every turn;
                # see src/responses_client.py. Pair with register_clients().
                entry["model_client_cls"] = "StatelessResponsesClient"
        elif self.provider == "google":
            entry.update({"api_key": os.getenv("GEMINI_API_KEY", ""), "api_type": "google"})
        elif self.provider == "anthropic":
            entry.update({"api_key": os.getenv("ANTHROPIC_API_KEY", ""), "api_type": "anthropic"})
        elif self.provider == "openai-account":
            entry.update({
                "api_key": "dummy",
                "api_type": "openai",
                "base_url": "http://127.0.0.1:8000/v1",
            })
        else:
            raise ValueError(f"Unsupported provider for Autogen: {self.provider}")

        if self.price is not None and not self.uses_responses:
            entry["price"] = list(self.price)
        if self.reasoning_effort is not None:
            entry["reasoning_effort"] = self.reasoning_effort

        config: dict[str, Any] = {"config_list": [entry]}
        # Reasoning models reject temperature on the Responses API. Shared
        # configs can retain it for providers that accept it.
        for name in _AUTOGEN_TOP_LEVEL_PARAMS:
            value = getattr(self, name)
            if value is not None and not (name == "temperature" and self.uses_responses):
                config[name] = value
        return config


def get_model_config(model: str, llm_params: Mapping[str, Any] | None = None) -> ModelConfig:
    """Load model metadata and non-sensitive LLM settings for callers."""
    model_index = read_model_index()
    if model not in model_index:
        raise ValueError(f"Model '{model}' not found in model index.")

    params = dict(llm_params or {})
    unsupported = params.keys() - set(_AUTOGEN_TOP_LEVEL_PARAMS) - {"reasoning_effort"}
    if unsupported:
        raise ValueError(f"Unsupported LLM parameters: {', '.join(sorted(unsupported))}")

    entry = model_index[model]
    price = entry.get("price")
    return ModelConfig(
        model=model,
        model_name=entry["name"],
        provider=entry["provider"],
        reasoning_effort=params.get("reasoning_effort"),
        temperature=params.get("temperature"),
        max_tokens=params.get("max_tokens"),
        top_p=params.get("top_p"),
        timeout=params.get("timeout"),
        seed=params.get("seed"),
        cache_seed=params.get("cache_seed"),
        parallel_tool_calls=params.get("parallel_tool_calls"),
        check_every_ms=params.get("check_every_ms"),
        allow_format_str_template=params.get("allow_format_str_template"),
        response_format=params.get("response_format"),
        routing_method=params.get("routing_method"),
        price=(float(price[0]), float(price[1])) if price is not None else None,
        cached_price=float(entry["cached-price"]) if "cached-price" in entry else None,
    )
