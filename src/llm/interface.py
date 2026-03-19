from abc import ABC, abstractmethod

type Messages = list[str]

class LLMInterface(ABC):
    @abstractmethod
    def chat(self, model: str, system_instruction: str, messages: Messages) -> str:
        raise NotImplementedError("LLMProvider subclasses must implement the chat method")

    @classmethod
    @abstractmethod
    def get_default_model(cls) -> str:
        raise NotImplementedError("LLMProvider subclasses must implement the get_default_model method")
