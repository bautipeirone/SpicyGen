from types import SimpleNamespace

import pytest

from scripts import generate_grammar_codex_harness as codex_harness
from src.prompts import build_one_shot_system_message, build_one_shot_user_message
from src.protocol import Protocol, ProtocolSpecType, ProtocolType
from src.target_language import get_target_language


def make_protocol() -> Protocol:
    return Protocol(
        name="example",
        full_name="Example Protocol",
        type=ProtocolType.BINARY,
        spec_type=ProtocolSpecType.OTHER,
        spec_id=None,
        local_spec_file=None,
    )


def test_full_context_mode_starts_with_the_one_shot_framework_prompt(tmp_path):
    protocol = make_protocol()
    spec = "SPECIFICATION SENTINEL"
    instructions = "SPICY MANUAL SENTINEL"
    candidate_path = tmp_path / "candidate.spicy"
    target_language = get_target_language("spicy")
    expected_prefix = "\n\n".join(
        [
            build_one_shot_system_message(
                model_name="Codex",
                target_language_context=instructions,
                target_language=target_language,
                protocol=protocol,
            ),
            build_one_shot_user_message(spec),
        ]
    )

    prompt = codex_harness.build_full_context_prompt(
        protocol=protocol,
        spec_content=spec,
        spicy_instructions=instructions,
        candidate_path=candidate_path,
    )

    assert prompt.startswith(expected_prefix)
    assert prompt.count(spec) == 1
    assert prompt.count(instructions) == 1
    assert "data/raw/example/positive/" in prompt
    assert "data/raw/example/negative/" in prompt
    assert str(candidate_path) in prompt


def test_criteria_only_mode_contains_no_embedded_spec_or_manual(tmp_path):
    prompt = codex_harness.build_criteria_only_prompt(
        protocol_name="example",
        candidate_path=tmp_path / "candidate.spicy",
    )

    assert "Spicy v1.16.0" in prompt
    assert "data/raw/example/positive/" in prompt
    assert "data/raw/example/negative/" in prompt
    assert "SPECIFICATION" not in prompt
    assert "SPICY MANUAL" not in prompt


def test_normalize_codex_usage_matches_autogen_metrics_shape():
    usage = codex_harness.normalize_codex_usage(
        {
            "input_tokens": 100,
            "cached_input_tokens": 60,
            "output_tokens": 25,
            "reasoning_output_tokens": 10,
        }
    )

    assert usage == {
        "total_usage": {
            "total_cost": None,
            "prompt_tokens": 100,
            "completion_tokens": 25,
            "total_tokens": 125,
        },
        "actual_usage": {
            "total_cost": None,
            "prompt_tokens": 40,
            "completion_tokens": 25,
            "total_tokens": 65,
        },
    }


def test_extract_codex_usage_uses_turn_completed_event():
    usage = codex_harness.extract_codex_usage(
        "\n".join(
            [
                '{"type":"thread.started","thread_id":"thread-1"}',
                '{"type":"turn.started"}',
                (
                    '{"type":"turn.completed","usage":'
                    '{"input_tokens":90,"cached_input_tokens":30,'
                    '"output_tokens":10,"reasoning_output_tokens":4}}'
                ),
            ]
        )
    )

    assert usage is not None
    assert usage["total_usage"]["total_tokens"] == 100
    assert usage["actual_usage"]["total_tokens"] == 70


def test_run_codex_uses_noninteractive_ephemeral_workspace_session(
    monkeypatch,
    tmp_path,
):
    candidate_path = tmp_path / "candidate.spicy"
    last_message_path = tmp_path / "last-message.txt"
    calls = []

    monkeypatch.setattr(
        codex_harness.shutil,
        "which",
        lambda executable: "/usr/bin/codex",
    )

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        candidate_path.write_text("module Example;")
        return SimpleNamespace(
            returncode=0,
            stderr="",
            stdout=(
                '{"type":"turn.completed","usage":'
                '{"input_tokens":100,"cached_input_tokens":20,'
                '"output_tokens":25,"reasoning_output_tokens":5}}\n'
            ),
        )

    monkeypatch.setattr(codex_harness.subprocess, "run", fake_run)

    response, source, usage = codex_harness.run_codex(
        prompt="PROMPT",
        candidate_path=candidate_path,
        last_message_path=last_message_path,
        codex_bin="codex",
        model="gpt-test",
    )

    command, kwargs = calls[0]
    assert command[:2] == ["/usr/bin/codex", "exec"]
    assert "--json" in command
    assert "--ephemeral" in command
    assert command[command.index("--sandbox") + 1] == "workspace-write"
    assert command[command.index("--output-last-message") + 1] == str(
        last_message_path
    )
    assert command[command.index("--model") + 1] == "gpt-test"
    assert command[-1] == "-"
    assert "--dangerously-bypass-approvals-and-sandbox" not in command
    assert kwargs["input"] == "PROMPT"
    assert kwargs["cwd"] == codex_harness.PROJECT_ROOT
    assert kwargs["stdout"] == codex_harness.subprocess.PIPE
    assert kwargs["stderr"] == codex_harness.subprocess.PIPE
    assert response == "module Example;"
    assert source == "candidate-file"
    assert usage is not None
    assert usage["total_usage"]["prompt_tokens"] == 100
    assert usage["total_usage"]["completion_tokens"] == 25
    assert usage["total_usage"]["total_tokens"] == 125
    assert usage["actual_usage"]["prompt_tokens"] == 80
    assert usage["actual_usage"]["total_tokens"] == 105


def test_run_codex_falls_back_to_last_message(monkeypatch, tmp_path):
    candidate_path = tmp_path / "candidate.spicy"
    last_message_path = tmp_path / "last-message.txt"

    monkeypatch.setattr(
        codex_harness.shutil,
        "which",
        lambda executable: "/usr/bin/codex",
    )

    def fake_run(command, **kwargs):
        last_message_path.write_text("module Fallback;")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(codex_harness.subprocess, "run", fake_run)

    response, source, usage = codex_harness.run_codex(
        prompt="PROMPT",
        candidate_path=candidate_path,
        last_message_path=last_message_path,
        codex_bin="codex",
        model=None,
    )

    assert response == "module Fallback;"
    assert source == "last-message"
    assert usage is None


def test_run_codex_reports_captured_cli_error(monkeypatch, tmp_path):
    candidate_path = tmp_path / "candidate.spicy"
    last_message_path = tmp_path / "last-message.txt"

    monkeypatch.setattr(
        codex_harness.shutil,
        "which",
        lambda executable: "/usr/bin/codex",
    )
    monkeypatch.setattr(
        codex_harness.subprocess,
        "run",
        lambda command, **kwargs: SimpleNamespace(
            returncode=1,
            stdout=(
                '{"type":"error","error":'
                '{"message":"state database is read-only"}}\n'
            ),
            stderr="startup failed",
        ),
    )

    with pytest.raises(RuntimeError, match="state database is read-only"):
        codex_harness.run_codex(
            prompt="PROMPT",
            candidate_path=candidate_path,
            last_message_path=last_message_path,
            codex_bin="codex",
            model="gpt-test",
        )
