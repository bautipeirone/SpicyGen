import os
from pathlib import Path
import json

MODEL_INDEX_PATH = Path(__file__).parents[1] / "model_index.json"

def read_model_index():
    try:
        with MODEL_INDEX_PATH.open("r") as f:
            return json.load(f)
    except OSError as exc:
        raise SystemExit(f"Unable to read model index from {MODEL_INDEX_PATH}: {exc}") from exc

def get_autogen_config(model: str, llm_params: dict | None = None) -> dict:
    model_index = read_model_index()
    if model not in model_index:
        raise ValueError(f"Model '{model}' not found in model index.")

    provider_name = model_index[model]["provider"]
    model_name = model_index[model]["name"]
    price = model_index[model].get("price")
    llm_params = llm_params or {}

    if provider_name == "ollama":
        config = {
            "config_list": [{
                "model": model_name,
                "api_key": os.getenv("OLLAMA_API_KEY", ""),
                "base_url": os.environ["OLLAMA_HOST_ENDPOINT"]
            }]
        }
    elif provider_name == "openai":
        config = {
            "config_list": [{
                "model": model_name,
                "api_key": os.getenv("OPENAI_API_KEY", "")
            }]
        }
        if llm_params.get("reasoning_effort") not in (None, "none"):
            # Use Responses for direct OpenAI reasoning calls with tools.
            config["config_list"][0]["api_type"] = "responses"
    elif provider_name == "google":
        config = {
            "config_list": [{
                "model": model_name,
                "api_key": os.getenv("GEMINI_API_KEY", ""),
                "api_type": "google"
            }]
        }
    elif provider_name == "anthropic":
        config = {
            "config_list": [{
                "model": model_name,
                "api_key": os.getenv("ANTHROPIC_API_KEY", ""),
                "api_type": "anthropic"
            }]
        }
    elif provider_name == "openai-account":
        config = {
            "config_list": [{
                "model": model_name,
                "api_key": "dummy",
                "api_type": "openai",
                "base_url": "http://127.0.0.1:8000/v1"
            }]
        }
    else:
        raise ValueError(f"Unsupported provider for Autogen: {provider_name}")

    using_responses = config["config_list"][0].get("api_type") == "responses"
    if price is not None and not using_responses:
        config["config_list"][0]["price"] = price

    # AG2's LLMConfig only accepts a fixed set of application-level options.
    # Provider-specific OpenAI options such as ``reasoning_effort`` must be
    # attached to the individual model entry instead.
    reasoning_effort = llm_params.get("reasoning_effort")
    if reasoning_effort is not None:
        config["config_list"][0]["reasoning_effort"] = reasoning_effort

    # Reasoning requests on Responses omit temperature. Shared configs can
    # retain it for providers that accept it.
    config.update({
        key: value for key, value in llm_params.items()
        if key != "reasoning_effort"
        and not (key == "temperature" and using_responses)
    })
    return config


def get_responses_price(llm_config: dict) -> tuple[float, float] | None:
    """Return local per-token prices for an AG2 Responses config, if known."""
    entry = llm_config.get("config_list", [{}])[0]
    if entry.get("api_type") != "responses":
        return None
    for model in read_model_index().values():
        if model.get("provider") == "openai" and model.get("name") == entry.get("model"):
            price = model.get("price")
            if price is not None:
                return float(price[0]), float(price[1])
    return None

# LEGACY CODE
# def get_llm_provider(provider_name: str) -> type[LLMInterface]:
#     match provider_name:
#         case "ollama":
#             provider = OllamaProvider
#         case "google":
#             provider = GoogleProvider
#         case "openai":
#             provider = OpenAIProvider
#         case "anthropic":
#             provider = AnthropicProvider
#         case _:
#             raise ValueError(f"Unsupported provider: {provider_name}")
#     return provider

# def get_llm_client(provider_name: str, model: str | None = None) -> LLMInterface:
#     provider = get_llm_provider(provider_name)
#     return provider(model=model or provider.get_default_model())
