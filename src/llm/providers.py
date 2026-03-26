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

