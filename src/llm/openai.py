from openai import OpenAI
from .interface import LLMInterface, Messages
import os

class OpenAIProvider(LLMInterface):
    def __init__(self, model: str):
        self.client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        self.model = model

    @classmethod
    def get_default_model(cls) -> str:
        return "gpt-4o"

    def chat(self, system_instruction: str, messages: Messages) -> str:
        messages = (
            [{ "role": "system", "content": system_instruction }] +
            [{ "role": "user", "content": message } for message in messages]
        )

        response = self.client.chat.completions.create(model=self.model, messages=messages, temperature=0.0)
        return response.choices[0].message.content
