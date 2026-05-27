from src.utils import strip_code_fences

def clean_spicy_response(text: str) -> str:
    """Strip markdown fences and leading non-grammar lines from Spicy output."""
    cleaned = strip_code_fences(text)
    while cleaned and not cleaned.startswith("module"):
        cleaned = "\n".join(cleaned.splitlines()[1:]).strip()
    return cleaned
