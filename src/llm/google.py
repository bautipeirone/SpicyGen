import os
from google import genai
from google.genai import types
from .interface import LLMInterface, Messages

class GoogleGemini(LLMInterface):
    def __init__(self, model: str):
        self.client = genai.Client(
            api_key=os.environ.get("GEMINI_API_KEY"),
        )
        self.model = model

    @classmethod
    def get_default_model(cls) -> str:
        return "gemini-3-flash-preview"

    def chat(self, system_instruction: str, messages: Messages) -> str:
        generate_content_config = types.GenerateContentConfig(
            temperature=1.0,
            thinking_config=types.ThinkingConfig(
                thinking_level="HIGH",
            ),
            system_instruction=system_instruction
        )

        response = self.client.models.generate_content(
            model=self.model,
            contents=messages,
            config=generate_content_config,
        )
        return response.text
