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
    llm_params = llm_params or {}
    
    if provider_name == "ollama":
        config = {
            "config_list": [{
                "model": model_name,
                "api_key": os.getenv("OLLAMA_API_KEY", ""),
                "base_url": "https://ollama.com/v1"
            }]
        }
    elif provider_name == "openai":
        config = {
            "config_list": [{
                "model": model_name,
                "api_key": os.getenv("OPENAI_API_KEY", "")
            }]
        }
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
    else:
        raise ValueError(f"Unsupported provider for Autogen: {provider_name}")

    config.update(llm_params)
    return config

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
