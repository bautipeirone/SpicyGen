import os
from src.llm.interface import LLMInterface
from src.llm.ollama import OllamaProvider
from src.llm.google import GoogleProvider
from src.llm.openai import OpenAIProvider
from src.llm.claude import AnthropicProvider

def get_llm_provider(provider_name: str) -> type[LLMInterface]:
    match provider_name:
        case "ollama":
            provider = OllamaProvider
        case "google":
            provider = GoogleProvider
        case "openai":
            provider = OpenAIProvider
        case "anthropic":
            provider = AnthropicProvider
        case _:
            raise ValueError(f"Unsupported provider: {provider_name}")
    return provider

def get_llm_client(provider_name: str, model: str | None = None) -> LLMInterface:
    provider = get_llm_provider(provider_name)
    return provider(model=model or provider.get_default_model())

def get_autogen_config(provider_name: str, model: str | None = None, extra_kwargs: dict | None = None) -> dict:
    provider_cls = get_llm_provider(provider_name)
    actual_model = model or provider_cls.get_default_model()
    extra_kwargs = extra_kwargs or {}
    
    if provider_name == "ollama":
        config = {
            "config_list": [{
                "model": actual_model,
                "api_key": os.getenv("OLLAMA_API_KEY", ""),
                "base_url": "https://ollama.com/v1"
            }]
        }
    elif provider_name == "openai":
        config = {
            "config_list": [{
                "model": actual_model,
                "api_key": os.getenv("OPENAI_API_KEY", "")
            }]
        }
    elif provider_name == "google":
        config = {
            "config_list": [{
                "model": actual_model,
                "api_key": os.getenv("GEMINI_API_KEY", ""),
                "api_type": "google"
            }]
        }
    elif provider_name == "anthropic":
        config = {
            "config_list": [{
                "model": actual_model,
                "api_key": os.getenv("ANTHROPIC_API_KEY", ""),
                "api_type": "anthropic"
            }]
        }
    else:
        raise ValueError(f"Unsupported provider for Autogen: {provider_name}")

    config.update(extra_kwargs)
    return config
