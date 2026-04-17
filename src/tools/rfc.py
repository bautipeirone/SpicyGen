import requests
import re
from src.logger import logger

RFC_NAME_TO_ID = {
    "dns": "1035",
    "tftp": "1350",
    "irc": "1459",
    "dhcp": "2131",
    "http1.1": "2616",
    "ldap": "4511",
    "bpv7": "9171"
}

def clean_rfc_text(text: str):
    header_pattern = re.compile(r'RFC (\d+)\s+(.*?)\s+([A-Za-z]+ \d{4})')
    footer_pattern = re.compile(r'^.*\s+\[Page \d+\]$', re.MULTILINE)

    text = header_pattern.sub('', text)
    text = footer_pattern.sub('', text)

    text = re.sub(r'\n\s*\n', '\n', text)
    return text.strip()

def retrieve_rfc_by_id(rfc_id: str) -> str | None:
    """Retrieve an RFC by its ID."""
    if rfc_id in RFC_NAME_TO_ID: # If the ID provided is the name, convert it to the ID number
        rfc_id = RFC_NAME_TO_ID[rfc_id]
    RFC_EDITOR_URL = "https://www.rfc-editor.org/rfc/rfc{rfc_id}.txt"
    url = RFC_EDITOR_URL.format(rfc_id=rfc_id)
    response = requests.get(url, timeout=15)
    if response.status_code == 200:
        logger.debug(f"Successfully retrieved RFC {rfc_id} from {url}")
        return response.text
    else:
        logger.error(f"Failed to retrieve RFC {rfc_id} from {url}: HTTP {response.status_code}")
        return None
