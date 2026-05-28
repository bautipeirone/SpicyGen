"""Integration tests for config loading and provider configuration.

Covers: read_config() → get_autogen_config() pipeline.
"""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from src.utils import read_config
from src.providers import get_autogen_config, read_model_index


class TestReadConfig:

    def test_defaults_when_no_file(self):
        llm_params, agent_config = read_config(None)
        assert llm_params["temperature"] == 0.0
        assert agent_config["max_retries"] == 15
        assert agent_config["show_packet_representation"] is False

    def test_defaults_when_file_does_not_exist(self, tmp_path):
        llm_params, _ = read_config(tmp_path / "nonexistent.yaml")
        assert llm_params["temperature"] == 0.0

    def test_override_from_yaml(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            "llm:\n  temperature: 0.7\n  max_tokens: 2000\n"
            "agent:\n  max_retries: 5\n  show_packet_representation: true\n"
        )
        llm_params, agent_config = read_config(config_file)
        assert llm_params["temperature"] == 0.7
        assert agent_config["max_retries"] == 5

    def test_partial_override_llm_only(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text("llm:\n  temperature: 0.5\n")
        llm_params, agent_config = read_config(config_file)
        assert llm_params["temperature"] == 0.5
        assert agent_config["max_retries"] == 15

    def test_empty_yaml_returns_defaults(self, tmp_path):
        config_file = tmp_path / "empty.yaml"
        config_file.write_text("")
        llm_params, agent_config = read_config(config_file)
        assert llm_params["temperature"] == 0.0


class TestGetAutogenConfig:

    def _make_index(self, provider, model_name="test-v1", price=None):
        index = {"test-key": {"name": model_name, "provider": provider, "context-window": "100000"}}
        if price is not None:
            index["test-key"]["price"] = price
        return index

    def test_google_provider(self):
        index = self._make_index("google", price=[0.001, 0.005])
        with patch("src.providers.read_model_index", return_value=index):
            config = get_autogen_config("test-key")
        entry = config["config_list"][0]
        assert entry["model"] == "test-v1"
        assert entry["api_type"] == "google"
        assert entry["price"] == [0.001, 0.005]

    def test_ollama_provider(self):
        with patch("src.providers.read_model_index", return_value=self._make_index("ollama")):
            config = get_autogen_config("test-key")
        assert "ollama" in config["config_list"][0]["base_url"]

    def test_unsupported_provider_raises(self):
        with patch("src.providers.read_model_index", return_value=self._make_index("bad")):
            with pytest.raises(ValueError, match="Unsupported provider"):
                get_autogen_config("test-key")

    def test_unknown_model_raises(self):
        with patch("src.providers.read_model_index", return_value={}):
            with pytest.raises(ValueError, match="not found"):
                get_autogen_config("nonexistent-model")

    def test_llm_params_merged(self):
        with patch("src.providers.read_model_index", return_value=self._make_index("google")):
            config = get_autogen_config("test-key", llm_params={"temperature": 0.7})
        assert config["temperature"] == 0.7


class TestRealModelIndex:

    def test_model_index_loads(self):
        index = read_model_index()
        assert isinstance(index, dict) and len(index) > 0

    def test_all_models_have_required_fields(self):
        for key, entry in read_model_index().items():
            assert "name" in entry, f"'{key}' missing 'name'"
            assert "provider" in entry, f"'{key}' missing 'provider'"
