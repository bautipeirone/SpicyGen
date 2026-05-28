import re
from .reserved_keywords import RESERVED_KEYWORDS, REPLACE_KEYWORDS_MAP
from src.tools.preprocess import trivial_response_cleaner, ResponseCleaner

clean_3d_response: ResponseCleaner = trivial_response_cleaner

def search_reserved_keywords(code: str) -> list[tuple[int, str]]:
    reserved_keywords: list[tuple[int, str]] = []
    for line_num, line in enumerate(code.splitlines(), 1):
        for keyword in RESERVED_KEYWORDS:
            if re.search(rf"\b{keyword}\b(\[.*\])?\s*;", line):
                reserved_keywords.append((line_num, keyword))
    return reserved_keywords

def preprocess_3d_code(code: str) -> list[str]:
    errors = []
    for line_num, keyword in search_reserved_keywords(code):
        error_message = f"[error]: found reserved keyword as field identifier '{keyword}' in line {line_num}."
        suggested_replacement = REPLACE_KEYWORDS_MAP.get(keyword, None)
        if suggested_replacement:
            error_message += f" Consider replacing it with '{suggested_replacement}'."
        errors.append(error_message)
    return errors
