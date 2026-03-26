from anthropic import Anthropic
from .interface import LLMInterface, Messages
import os

class AnthropicProvider(LLMInterface):
    def __init__(self, model: str):
        self.client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        self.model = model

    @classmethod
    def get_default_model(cls) -> str:
        # max_tokens maximo para este modelo 128000
        return "claude-opus-4-6"

    def chat(self, system_instruction: str, messages: Messages) -> str:
        messages = [{ "role": "user", "content": message } for message in messages]

        with self.client.messages.stream(
            model=self.model,
            max_tokens=128000, # Anthropic requiere este argumento que depende del modelo. TODO Automatizar la lectura de este parametro
            system=system_instruction,
            messages=messages,
            temperature=0.0
        ) as stream:
            response = stream.get_final_message()

        return response.content[0].text
