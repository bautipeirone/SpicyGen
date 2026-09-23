"""Integration tests for config loading and provider configuration.

Covers config loading, inspectable model settings, and AG2 conversion.
"""

import json
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

import pytest

from src.utils import read_config
from src.providers import ModelConfig, get_model_config, read_model_index


class TestReadConfig:

    def test_defaults_when_no_file(self):
        llm_params, agent_config = read_config(None)
        assert llm_params["temperature"] == 0.0
        assert agent_config["max_retries"] == 15
        assert agent_config["enable_preprocessing"] is True
        assert agent_config["show_packet_representation"] is False

    def test_override_from_yaml(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            "llm:\n  temperature: 0.7\n  max_tokens: 2000\n"
            "agent:\n  max_retries: 5\n  show_packet_representation: true\n"
        )
        llm_params, agent_config = read_config(config_file)
        assert llm_params["temperature"] == 0.7
        assert agent_config["max_retries"] == 5

class TestModelConfig:

    def _make_index(self, provider, model_name="test-v1", price=None):
        index = {"test-key": {"name": model_name, "provider": provider, "context-window": "100000"}}
        if price is not None:
            index["test-key"]["price"] = price
        return index

    def test_google_provider(self):
        index = self._make_index("google", price=[0.001, 0.005])
        with patch("src.providers.read_model_index", return_value=index):
            config = get_model_config("test-key").as_autogen_config()
        entry = config["config_list"][0]
        assert entry["model"] == "test-v1"
        assert entry["api_type"] == "google"
        assert entry["price"] == [0.001, 0.005]

    def test_ollama_provider(self):
        with patch("src.providers.read_model_index", return_value=self._make_index("ollama")):
            config = get_model_config("test-key").as_autogen_config()
        assert "ollama" in config["config_list"][0]["base_url"]

    def test_unsupported_provider_raises(self):
        with patch("src.providers.read_model_index", return_value=self._make_index("bad")):
            with pytest.raises(ValueError, match="Unsupported provider"):
                get_model_config("test-key")

    def test_unknown_model_raises(self):
        with patch("src.providers.read_model_index", return_value={}):
            with pytest.raises(ValueError, match="not found"):
                get_model_config("nonexistent-model")

    def test_llm_params_merged(self):
        with patch("src.providers.read_model_index", return_value=self._make_index("google")):
            config = get_model_config("test-key", {"temperature": 0.7}).as_autogen_config()
        assert config["temperature"] == 0.7

    def test_model_config_exposes_settings_without_credentials(self, monkeypatch):
        index = self._make_index("openai", model_name="other-model", price=[0.0025, 0.015])
        monkeypatch.setenv("OPENAI_API_KEY", "private-test-key")
        with patch("src.providers.read_model_index", return_value=index):
            model_config = get_model_config(
                "test-key", {"temperature": 0.4, "max_tokens": 2000, "seed": 7}
            )
        monkeypatch.setenv("OPENAI_API_KEY", "replacement-private-key")

        assert isinstance(model_config, ModelConfig)
        assert model_config.model == "test-key"
        assert model_config.model_name == "other-model"
        assert model_config.provider == "openai"
        assert model_config.temperature == 0.4
        assert model_config.max_tokens == 2000
        assert model_config.seed == 7
        assert "private-test-key" not in repr(model_config)
        assert "replacement-private-key" not in repr(model_config)
        assert "api_key" not in asdict(model_config)
        autogen_config = model_config.as_autogen_config()
        assert autogen_config["config_list"][0]["api_key"] == "replacement-private-key"
        assert autogen_config["seed"] == 7

    def test_model_config_handles_reasoning_and_rejects_secret_options(self):
        index = self._make_index("openai", model_name="other-reasoning-model", price=[0.0025, 0.015])
        with patch("src.providers.read_model_index", return_value=index):
            model_config = get_model_config(
                "test-key", {"reasoning_effort": "medium", "temperature": 0.7}
            )
            with pytest.raises(ValueError, match="Unsupported LLM parameters: api_key"):
                get_model_config("test-key", {"api_key": "private-test-key"})

        assert model_config.uses_responses
        assert model_config.responses_price == (0.0025, 0.015)
        autogen_config = model_config.as_autogen_config()
        assert autogen_config["config_list"][0]["api_type"] == "responses"
        assert "temperature" not in autogen_config
        assert "price" not in autogen_config["config_list"][0]

    def test_reasoning_effort_is_added_to_model_entry(self):
        with patch("src.providers.read_model_index", return_value=self._make_index("openai")):
            config = get_model_config("test-key", {"reasoning_effort": "xhigh"}).as_autogen_config()
        assert config["config_list"][0]["reasoning_effort"] == "xhigh"
        assert config["config_list"][0]["api_type"] == "responses"
        assert "reasoning_effort" not in config

    def test_openai_reasoning_uses_responses_without_unsupported_options(self):
        index = self._make_index("openai", model_name="other-reasoning-model", price=[0.0025, 0.015])
        with patch("src.providers.read_model_index", return_value=index):
            model_config = get_model_config(
                "test-key", {"reasoning_effort": "medium", "temperature": 0.7}
            )
            config = model_config.as_autogen_config()
            price = model_config.responses_price
        assert config["config_list"][0]["api_type"] == "responses"
        assert config["config_list"][0]["reasoning_effort"] == "medium"
        assert "price" not in config["config_list"][0]
        assert "temperature" not in config
        assert price == (0.0025, 0.015)

    def test_openai_without_reasoning_stays_on_chat_completions(self):
        index = self._make_index("openai", model_name="other-model", price=[0.0025, 0.015])
        with patch("src.providers.read_model_index", return_value=index):
            config = get_model_config(
                "test-key", {"reasoning_effort": "none", "temperature": 0.0}
            ).as_autogen_config()
        assert "api_type" not in config["config_list"][0]
        assert config["config_list"][0]["price"] == [0.0025, 0.015]
        assert config["temperature"] == 0.0

    def test_openai_account_does_not_switch_to_responses(self):
        index = self._make_index("openai-account", model_name="other-model")
        with patch("src.providers.read_model_index", return_value=index):
            config = get_model_config(
                "test-key", {"reasoning_effort": "medium", "temperature": 0.7}
            ).as_autogen_config()
        assert config["config_list"][0]["api_type"] == "openai"
        assert config["temperature"] == 0.7


class TestRealModelIndex:

    def test_model_index_loads(self):
        index = read_model_index()
        assert isinstance(index, dict) and len(index) > 0

    def test_all_models_have_required_fields(self):
        for key, entry in read_model_index().items():
            assert "name" in entry, f"'{key}' missing 'name'"
            assert "provider" in entry, f"'{key}' missing 'provider'"
