from typing import Callable

type ResponseCleaner = Callable[[str], str]

def trivial_response_cleaner(code: str) -> str:
    return code
