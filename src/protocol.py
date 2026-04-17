from pathlib import Path
from dataclasses import dataclass
from src.utils import load_file
from src.tools.rfc import retrieve_rfc_by_id, clean_rfc_text
from enum import Enum
from src.logger import logger
import json
import os

# Default location of the packet index relative to the project root.
_DEFAULT_INDEX_PATH = Path(__file__).resolve().parents[1] / "protocol_index.json"


class ProtocolType(Enum):
    TEXT = "text"
    BINARY = "binary"


class ProtocolSpecType(Enum):
    RFC = "rfc"
    BLUEBOOK = "bluebook"
    IEEE_STD = "ieee_std"


@dataclass
class Protocol:
    name: str
    type: ProtocolType
    spec_type: ProtocolSpecType | None
    spec_id: str | None  # Can refer to RFC ID, IEEE standard number, etc.
    local_spec_file: Path | None

    @property
    def is_text_protocol(self) -> bool:
        return self.type == ProtocolType.TEXT
    
    @property
    def is_binary_protocol(self) -> bool:
        return self.type == ProtocolType.BINARY


def load_protocol_index(index_path: Path | None = None) -> dict:
    """Load the JSON packet index.

    Returns a dict mapping protocol names to ``{"accept": [...], "reject": [...]}``.
    """
    path = index_path or _DEFAULT_INDEX_PATH
    logger.debug(f"Loading packet index from {path}")
    with open(path, "r") as f:
        index = json.load(f)
    logger.info(f"Loaded packet index with {len(index)} protocol(s): {', '.join(index.keys())}")
    return index


def load_protocol(protocol_name: str, index_path: Path | None = None) -> Protocol | None:
    """Load protocol information from the index."""
    index = load_protocol_index(index_path)
    if protocol_name not in index:
        logger.warning(f"Protocol '{protocol_name}' not found in index.")
        return None
    entry = index[protocol_name]
    return Protocol(
        name=protocol_name,
        type=ProtocolType(entry.get("type", "text")),
        spec_type=ProtocolSpecType(entry.get("spec_type")) if entry.get("spec_type") else None,
        spec_id=entry.get("rfc_id"),
        local_spec_file=Path(entry["local_spec_file"]) if entry.get("local_spec_file") else None,
    )


def load_rfc(rfc_id: str) -> str:
    try:
        logger.debug(f"Retrieving RFC {rfc_id}...")
        rfc_content = retrieve_rfc_by_id(rfc_id)
        if rfc_content is None:
            return None
        rfc_content = clean_rfc_text(rfc_content)
        return rfc_content
    except Exception as exc:
        raise SystemExit(f"Unable to read RFC {rfc_id}: {exc}") from exc


def load_spec(protocol: Protocol) -> str | None:
    """Load the protocol specification text, either from a local file or by retrieving the RFC."""
    if protocol.local_spec_file and protocol.local_spec_file.exists():
        logger.debug(f"Loading protocol spec for {protocol.name} from {protocol.local_spec_file}")
        return load_file(protocol.local_spec_file)
    elif protocol.spec_type == ProtocolSpecType.RFC and protocol.spec_id:
        return load_rfc(protocol.spec_id)
    else:
        logger.warning(f"No specification found for protocol '{protocol.name}'")
        return None
