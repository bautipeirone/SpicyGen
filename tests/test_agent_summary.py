"""Offline checks for the specification summary agent's tool-call exchange."""

import json

import autogen

from src import agent
from src.providers import ModelConfig


def test_summary_executes_interval_tool_when_tool_call_has_null_content(monkeypatch):
    spec = "1. Header\nA message header.\n2. End\n"

    def assistant_factory(**kwargs):
        kwargs["llm_config"] = {"config_list": [{"model": "gpt-4", "api_key": "offline-test"}]}
        assistant = autogen.ConversableAgent(**kwargs, human_input_mode="NEVER")

        def select_interval_reply(_recipient, messages, sender, config):
            return True, {
                "content": None,
                "tool_calls": [{
                    "id": "call_select_intervals",
                    "type": "function",
                    "function": {
                        "name": "select_intervals",
                        "arguments": json.dumps({"intervals": [["1. Header", "2. End"]]}),
                    },
                }],
            }

        assistant.register_reply([autogen.Agent, None], select_interval_reply)
        return assistant

    monkeypatch.setattr(agent.autogen, "AssistantAgent", assistant_factory)

    config = ModelConfig(model="test-model", model_name="test-model", provider="openai-account")
    summary, stats, _usage = agent.summarize_spec(config, spec)

    assert summary == spec.rstrip("\n")
    assert stats["resolved_intervals"] == 1
