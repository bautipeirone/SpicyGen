from ollama import chat
from .interface import LLMInterface, Messages

class OllamaProvider(LLMInterface):
    def __init__(self, model: str):
        self.model = model

    @classmethod
    def get_default_model(cls) -> str:
        return "gpt-oss:120b-cloud"

    def chat(self, system_instruction: str, messages: Messages) -> str:
        messages = (
            [{ "role": "system", "content": system_instruction }] +
            [{ "role": "user", "content": message } for message in messages]
        )
        response = chat(model=self.model, messages=messages, options={'temperature': 0.0})
        return response.message.content