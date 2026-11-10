"""OpenAI Responses API support through AG2 with a stubbed endpoint."""

import json
from unittest.mock import patch

import autogen
import pytest
from openai.resources.responses import Responses
from openai.types.responses import Response

from src.agent import _chat_cost, _message_text
from src.providers import ModelConfig
from src.responses_client import StatelessResponsesClient


def _response(index: int, output: list[dict], input_tokens: int, cached: int, output_tokens: int = 100) -> Response:
    return Response.model_validate({
        "id": f"resp_{index}", "object": "response", "created_at": 0, "model": "gpt-5.4",
        "output": output, "parallel_tool_calls": True, "tool_choice": "auto", "tools": [],
        "status": "completed",
        "usage": {
            "input_tokens": input_tokens,
            "input_tokens_details": {"cached_tokens": cached, "cache_write_tokens": 0},
            "output_tokens": output_tokens,
            "output_tokens_details": {"reasoning_tokens": 60},
            "total_tokens": input_tokens + output_tokens,
        },
    })


def _tool_turn(n: int, code: str) -> list[dict]:
    return [
        {"type": "reasoning", "id": f"rs_{n}", "summary": [], "encrypted_content": f"enc_{n}"},
        {"type": "function_call", "id": f"fc_{n}", "call_id": f"call_{n}", "name": "validate_grammar",
         "arguments": json.dumps({"code": code}), "status": "completed"},
    ]


SCRIPT = [
    _tool_turn(1, "v1"),
    _tool_turn(2, "v2"),
    [{"type": "message", "id": "msg_3", "role": "assistant", "status": "completed",
      "content": [{"type": "output_text", "text": "All good. TERMINATE", "annotations": []}]}],
]


@pytest.fixture
def model_config():
    return ModelConfig(
        model="gpt-5.4", model_name="gpt-5.4", provider="openai", reasoning_effort="high",
        temperature=0.0, max_tokens=4000, price=(0.0025, 0.015), cached_price=0.00025,
    )


@pytest.fixture
def run_tool_chat(model_config, monkeypatch):
    """Run a refinement-style tool loop and return (requests, chat_result)."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    requests: list[dict] = []

    def fake_create(self, **kwargs):
        requests.append(json.loads(json.dumps(kwargs)))
        n = len(requests)
        return _response(n, SCRIPT[n - 1], input_tokens=1000 * n, cached=800 * (n - 1))

    def run():
        assistant = model_config.register_clients(autogen.AssistantAgent(
            "Writer", system_message="SYS", llm_config=model_config.as_autogen_config()
        ))
        proxy = autogen.UserProxyAgent(
            "Proxy", human_input_mode="NEVER", code_execution_config=False,
            max_consecutive_auto_reply=10,
            is_termination_msg=lambda m: _message_text(m).strip().endswith("TERMINATE"),
        )

        def validate_grammar(code: str) -> str:
            return f"feedback for {code}"

        autogen.agentchat.register_function(
            validate_grammar, caller=assistant, executor=proxy,
            name="validate_grammar", description="Validate the grammar.",
        )
        with patch.object(Responses, "create", fake_create):
            result = proxy.initiate_chat(assistant, message="SPEC", silent=True)
        return requests, result, assistant

    return run


def test_requests_are_stateless_and_send_each_item_once(run_tool_chat):
    requests, _, _ = run_tool_chat()
    assert len(requests) == 3
    assert len({request["prompt_cache_key"] for request in requests}) == 1
    for request in requests:
        assert request["store"] is False
        assert "previous_response_id" not in request
        assert request["include"] == ["reasoning.encrypted_content"]
        assert request["reasoning"] == {"effort": "high"}
        assert request["max_output_tokens"] == 4000
        assert "temperature" not in request and "max_tokens" not in request
        assert request["tools"][0]["name"] == "validate_grammar"

    last_input = requests[-1]["input"]
    assert [item.get("role") or item["type"] for item in last_input] == [
        "system", "user",
        "reasoning", "function_call", "function_call_output",
        "reasoning", "function_call", "function_call_output",
    ]
    # Each turn's raw output is replayed verbatim, including encrypted reasoning.
    assert last_input[2]["encrypted_content"] == "enc_1"
    assert last_input[3]["call_id"] == "call_1"
    assert last_input[4] == {"type": "function_call_output", "call_id": "call_1", "output": "feedback for v1"}
    # Earlier requests are exact prefixes of later ones, which keeps prompt caching effective.
    assert requests[1]["input"] == last_input[:5]


def test_chat_cost_bills_cached_input_at_cached_rate(run_tool_chat):
    _, result, _ = run_tool_chat()
    # input: 1000+2000+3000, cached: 0+800+1600, output: 3 * 100
    expected = ((6000 - 2400) * 0.0025 + 2400 * 0.00025 + 300 * 0.015) / 1000
    summary = _chat_cost(result)
    assert summary["actual_usage"]["prompt_tokens"] == 6000
    assert summary["actual_usage"]["completion_tokens"] == 300
    assert summary["actual_usage"]["total_cost"] == pytest.approx(expected)


def test_final_text_is_returned_as_plain_content(run_tool_chat):
    _, _, assistant = run_tool_chat()
    assert assistant.last_message()["content"] == "All good. TERMINATE"


def test_build_request_rejects_unknown_parameters():
    client = StatelessResponsesClient({"api_key": "test-key"})
    with pytest.raises(ValueError, match="frequency_penalty"):
        client.build_request({"messages": [], "frequency_penalty": 0.5})


def test_history_without_replay_is_reconstructed():
    client = StatelessResponsesClient({"api_key": "test-key"})
    request = client.build_request({"messages": [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "call_9", "type": "function", "function": {"name": "f", "arguments": "{}"}},
        ]},
        {"role": "tool", "tool_call_id": "call_9", "content": "ok"},
    ]})
    assert request["input"] == [
        {"role": "user", "content": "hi"},
        {"type": "function_call", "call_id": "call_9", "name": "f", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "call_9", "output": "ok"},
    ]


def test_message_text_handles_string_and_list_content():
    message = {"content": [{"type": "text", "text": "Done. "}, {"type": "text", "text": "TERMINATE"}]}
    assert _message_text(message).strip().endswith("TERMINATE")
    assert _message_text({"content": None}) == ""
    assert _message_text({"content": "TERMINATE"}) == "TERMINATE"
