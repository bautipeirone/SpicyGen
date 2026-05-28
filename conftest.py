"""Root conftest for grammar-generator tests.

Provides shared fixtures used across test modules.
"""

import json
import os
import sys
from pathlib import Path

import pytest

# The project root is the parent directory of grammar-generator-testing.
# Tests are expected to be run from the testing worktree, but `src` lives
# in the original grammar-generator directory.
PROJECT_ROOT = Path(__file__).resolve().parent

# Ensure imports like ``from src.…`` work regardless of CWD.
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_workspace(tmp_path: Path):
    """Return a temporary directory suitable for workspace tests."""
    return tmp_path


@pytest.fixture()
def protocol_index(tmp_path: Path) -> Path:
    """Create a minimal protocol_index.json for testing."""
    index = {
        "test-binary": {
            "type": "binary",
            "spec_type": "rfc",
            "rfc_id": "9999",
        },
        "test-text": {
            "type": "text",
            "spec_type": "rfc",
            "rfc_id": "0000",
        },
        "test-local": {
            "type": "binary",
            "spec_type": "bluebook",
            "local_spec_file": str(tmp_path / "local_spec.txt"),
        },
    }
    index_path = tmp_path / "protocol_index.json"
    index_path.write_text(json.dumps(index, indent=2))

    # Create the local spec file so load_spec can find it.
    (tmp_path / "local_spec.txt").write_text("Fake local spec content for testing.")
    return index_path


@pytest.fixture()
def sample_spec_text() -> str:
    """A realistic multi-section spec document used for extraction tests."""
    return """\
Table of Contents

   1. Introduction
   2. Message Format
   3. References

1. Introduction

   This document describes a test protocol used purely for testing
   purposes. It has no real-world applications.

2. Message Format

   2.1. Header

   The header is 4 bytes long.

       0                   1                   2                   3
       0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
      +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
      |      OpCode       |         Length            |    Flags      |
      +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+

   OpCode (8 bits): The operation code identifying the message type.
   Length (16 bits): Total length of the message in bytes.
   Flags (8 bits): Bit flags for message options.

   2.2. Payload

   The payload follows the header and its length is determined by the
   Length field minus the header size.

3. References

   [1] RFC 9999 — Test Protocol Specification
   [2] RFC 0000 — Another Test Document
"""


@pytest.fixture()
def model_index(tmp_path: Path) -> Path:
    """Create a minimal model_index.json for testing."""
    index = {
        "test-model": {
            "name": "test-model-v1",
            "provider": "google",
            "context-window": "100000",
            "cutoff-date": "01-25",
            "price": [0.001, 0.005],
        },
        "test-ollama-model": {
            "name": "test-ollama-v1",
            "provider": "ollama",
            "context-window": "100000",
        },
    }
    index_path = tmp_path / "model_index.json"
    index_path.write_text(json.dumps(index, indent=2))
    return index_path
