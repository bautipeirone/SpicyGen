import requests
import re

def clean_rfc_text(text: str):
    header_pattern = re.compile(r'RFC (\d+)\s+(.*?)\s+([A-Za-z]+ \d{4})')
    footer_pattern = re.compile(r'^.*\s+\[Page \d+\]$', re.MULTILINE)

    text = header_pattern.sub('', text)
    text = footer_pattern.sub('', text)

    text = re.sub(r'\n\s*\n', '\n', text)
    return text.strip()

def retrieve_rfc_by_id(rfc_id: str) -> str | None:
    """Retrieve an RFC by its ID."""
    RFC_EDITOR_URL = "https://www.rfc-editor.org/rfc/rfc{rfc_id}.txt"
    url = RFC_EDITOR_URL.format(rfc_id=rfc_id)
    response = requests.get(url, timeout=15)
    if response.status_code == 200:
        return response.text
    else:
        return None
