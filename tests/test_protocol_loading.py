"""Integration tests for the protocol loading pipeline.

Covers: protocol_index.json → load_protocol() → Protocol object → load_spec().
"""

import json
from pathlib import Path

import pytest

from src.protocol import (
    Protocol,
    ProtocolType,
    ProtocolSpecType,
    load_protocol,
    load_protocol_index,
    load_spec,
)


# ---------------------------------------------------------------------------
# Real index tests — exercise the actual protocol_index.json shipped with
# the project. These validate that the data file + loading logic agree.
# ---------------------------------------------------------------------------

class TestLoadProtocolIndex:
    """Integration tests using the real protocol_index.json."""

    def test_index_loads_successfully(self):
        index = load_protocol_index()
        assert isinstance(index, dict)
        assert len(index) > 0

    def test_index_contains_known_protocols(self):
        index = load_protocol_index()
        for name in ("dns", "dhcp", "tftp", "tcp", "udp"):
            assert name in index, f"Expected '{name}' in protocol index"

    def test_all_entries_have_required_fields(self):
        index = load_protocol_index()
        for name, entry in index.items():
            assert "type" in entry, f"'{name}' missing 'type'"
            assert entry["type"] in ("text", "binary"), f"'{name}' has invalid type: {entry['type']}"


class TestLoadProtocol:
    """Integration tests: index → Protocol dataclass."""

    def test_load_known_binary_protocol(self):
        protocol = load_protocol("dns")
        assert protocol is not None
        assert protocol.name == "dns"
        assert protocol.type == ProtocolType.BINARY
        assert protocol.spec_type == ProtocolSpecType.RFC
        assert protocol.spec_id is not None

    def test_load_known_text_protocol(self):
        protocol = load_protocol("ftp")
        assert protocol is not None
        assert protocol.type == ProtocolType.TEXT
        assert protocol.is_text_protocol

    def test_load_unknown_protocol_returns_none(self):
        protocol = load_protocol("nonexistent-protocol-xyz")
        assert protocol is None

    def test_protocol_with_local_spec_file(self):
        protocol = load_protocol("spp")
        assert protocol is not None
        assert protocol.local_spec_file is not None
        assert "spp" in str(protocol.local_spec_file)

    def test_protocol_type_properties(self):
        binary = load_protocol("tcp")
        assert binary.is_binary_protocol is True
        assert binary.is_text_protocol is False

        text = load_protocol("http1.1")
        assert text.is_text_protocol is True
        assert text.is_binary_protocol is False


class TestLoadProtocolWithCustomIndex:
    """Integration tests with a controlled (fixture-based) index."""

    def test_load_from_custom_index(self, protocol_index):
        protocol = load_protocol("test-binary", index_path=protocol_index)
        assert protocol is not None
        assert protocol.name == "test-binary"
        assert protocol.type == ProtocolType.BINARY
        assert protocol.spec_type == ProtocolSpecType.RFC

    def test_custom_index_text_protocol(self, protocol_index):
        protocol = load_protocol("test-text", index_path=protocol_index)
        assert protocol is not None
        assert protocol.type == ProtocolType.TEXT

    def test_custom_index_missing_protocol(self, protocol_index):
        result = load_protocol("does-not-exist", index_path=protocol_index)
        assert result is None


class TestLoadSpec:
    """Integration tests: Protocol → spec content loading."""

    def test_load_spec_from_local_file(self, protocol_index):
        protocol = load_protocol("test-local", index_path=protocol_index)
        assert protocol is not None
        spec = load_spec(protocol)
        assert spec is not None
        assert "Fake local spec content" in spec

    def test_load_spec_returns_none_for_missing_local_file(self, tmp_path):
        """When the local_spec_file path doesn't exist and there's no RFC fallback."""
        protocol = Protocol(
            name="ghost",
            full_name=None,
            type=ProtocolType.BINARY,
            spec_type=ProtocolSpecType.BLUEBOOK,
            spec_id=None,
            local_spec_file=tmp_path / "nonexistent.txt",
        )
        spec = load_spec(protocol)
        assert spec is None
