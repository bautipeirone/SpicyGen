"""AG2 Responses compatibility at the agent and usage boundaries."""

from src.agent import _message_text
from src.usage import extract_cost_summary


def test_responses_text_is_read_for_agent_messages():
    message = {
        "content": [
            {"type": "text", "role": "assistant", "text": "Done. "},
            {"type": "text", "role": "assistant", "text": "TERMINATE"},
        ]
    }
    assert _message_text(message).strip().endswith("TERMINATE")
    assert _message_text({"content": None}) == ""
    assert _message_text({"content": "TERMINATE"}) == "TERMINATE"


def test_responses_usage_is_priced_from_billed_tokens():
    usage = {
        "usage_including_cached_inference": {
            "total_cost": 0,
            "gpt-5.4": {"cost": 0, "prompt_tokens": 1000, "completion_tokens": 200, "total_tokens": 1200},
        },
        "usage_excluding_cached_inference": {
            "total_cost": 0,
            "gpt-5.4": {"cost": 0, "prompt_tokens": 1000, "completion_tokens": 200, "total_tokens": 1200},
        },
    }
    report = extract_cost_summary(usage, price=(0.0025, 0.015))
    assert report["actual_usage"] == {
        "total_cost": 0.0055,
        "prompt_tokens": 1000,
        "completion_tokens": 200,
        "total_tokens": 1200,
    }
