from types import SimpleNamespace

import pytest

import create_zeek_package as czp


def test_derive_analyzer_name_strips_known_model_suffix():
    assert czp.derive_analyzer_name("DNS_gpt") == "DNS"
    assert czp.derive_analyzer_name("HTTP_gemini") == "HTTP"
    assert czp.derive_analyzer_name("VXLAN") == "VXLAN"


def test_protocol_metadata_selects_first_zeek_supported_transport(tmp_path):
    metadata_file = tmp_path / "metadata.json"
    metadata_file.write_text('{"transport":["sctp","udp","tcp"],"port":53}')

    metadata = czp.ProtocolMetadata.from_json(metadata_file)

    assert metadata.transport == "tcp"
    assert metadata.transports == ["sctp", "udp", "tcp"]
    assert metadata.port == 53


def test_protocol_metadata_rejects_unsupported_transport_lists(tmp_path):
    metadata_file = tmp_path / "metadata.json"
    metadata_file.write_text('{"transport":["sctp","dccp"],"port":53}')

    with pytest.raises(ValueError, match="No Zeek-supported transport"):
        czp.ProtocolMetadata.from_json(metadata_file)


def test_parse_llm_bundle_accepts_plain_json():
    bundle = czp.parse_llm_bundle(
        '{"evt":"evt content","zeek_spicy":"spicy content","main_zeek":"zeek content"}'
    )

    assert bundle == czp.ZeekIntegrationFiles(
        evt="evt content\n",
        zeek_spicy="spicy content\n",
        main_zeek="zeek content\n",
    )


def test_parse_llm_bundle_accepts_fenced_json():
    bundle = czp.parse_llm_bundle(
        """```json
        {"evt":"evt content","zeek_spicy":"spicy content","main_zeek":"zeek content"}
        ```"""
    )

    assert bundle.evt == "evt content\n"
    assert bundle.zeek_spicy == "spicy content\n"
    assert bundle.main_zeek == "zeek content\n"


def test_parse_llm_bundle_rejects_missing_keys():
    with pytest.raises(ValueError, match="missing required keys"):
        czp.parse_llm_bundle('{"evt":"evt content","main_zeek":"zeek content"}')


def test_build_zeek_generation_prompt_includes_constraints_and_feedback():
    previous_files = czp.ZeekIntegrationFiles(
        evt="old evt\n",
        zeek_spicy="old spicy\n",
        main_zeek="old zeek\n",
    )

    prompt = czp.build_zeek_generation_prompt(
        spicy_source="module DNS_gpt;\npublic type Message = unit {};",
        module_name="DNS_gpt",
        analyzer_name="DNS",
        entry_unit="Message",
        transport="udp",
        port=53,
        compile_errors="bad evt syntax",
        previous_files=previous_files,
    )

    assert "The parser module is: DNS_gpt" in prompt
    assert "The Zeek integration module MUST be: Zeek_DNS" in prompt
    assert "The .evt MUST import both DNS_gpt and Zeek_DNS" in prompt
    assert "bad evt syntax" in prompt
    assert "old evt" in prompt


def test_try_compile_zeek_files_invokes_spicyz(tmp_path, monkeypatch):
    spicy_file = tmp_path / "dns.spicy"
    spicy_file.write_text("module DNS_gpt;\npublic type Message = unit {};\n")
    spicy_info = czp.SpicyParserInfo("DNS_gpt", "Message", spicy_file)
    files = czp.ZeekIntegrationFiles(
        evt="import DNS_gpt;\n",
        zeek_spicy="module Zeek_DNS;\n",
        main_zeek="module DNS;\n",
    )
    calls = []

    monkeypatch.setattr(czp.shutil, "which", lambda binary: "/usr/bin/spicyz")

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(czp.subprocess, "run", fake_run)

    assert czp.try_compile_zeek_files(
        spicy_info=spicy_info,
        analyzer_name="DNS",
        files=files,
    ) is None

    cmd, kwargs = calls[0]
    assert cmd[0] == "/usr/bin/spicyz"
    assert cmd[2].endswith("dns.hlto")
    assert any(part.endswith("zeek_dns.spicy") for part in cmd)
    assert any(part.endswith("dns.evt") for part in cmd)
    assert kwargs["capture_output"] is True


def test_try_compile_zeek_files_returns_compiler_output(tmp_path, monkeypatch):
    spicy_file = tmp_path / "dns.spicy"
    spicy_file.write_text("module DNS_gpt;\npublic type Message = unit {};\n")
    spicy_info = czp.SpicyParserInfo("DNS_gpt", "Message", spicy_file)
    files = czp.ZeekIntegrationFiles("evt\n", "spicy\n", "zeek\n")

    monkeypatch.setattr(czp.shutil, "which", lambda binary: "/usr/bin/spicyz")
    monkeypatch.setattr(
        czp.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=1,
            stdout="compiler stdout",
            stderr="compiler stderr",
        ),
    )

    errors = czp.try_compile_zeek_files(
        spicy_info=spicy_info,
        analyzer_name="DNS",
        files=files,
    )

    assert "spicyz exited with 1" in errors
    assert "compiler stdout" in errors
    assert "compiler stderr" in errors


def test_generate_zeek_files_retries_after_compile_failure(tmp_path, monkeypatch):
    spicy_file = tmp_path / "dns.spicy"
    spicy_file.write_text("module DNS_gpt;\npublic type Message = unit {};\n")
    spicy_info = czp.SpicyParserInfo("DNS_gpt", "Message", spicy_file)
    metadata = czp.ProtocolMetadata(transport="udp", port=53)
    instructions_file = tmp_path / "INTEGRATION.md"
    instructions_file.write_text("Use correct Zeek integration syntax.")
    responses = [
        '{"evt":"bad evt","zeek_spicy":"bad spicy","main_zeek":"bad zeek"}',
        '{"evt":"good evt","zeek_spicy":"good spicy","main_zeek":"good zeek"}',
    ]
    user_messages = []

    monkeypatch.setattr(czp, "read_config", lambda path: ({}, {}))
    monkeypatch.setattr(czp, "get_autogen_config", lambda model, params: {"model": model})

    def fake_query_llm_once(llm_config, system_prompt, user_message):
        user_messages.append(user_message)
        return responses.pop(0), {"tokens": len(user_messages)}

    monkeypatch.setattr(czp, "query_llm_once", fake_query_llm_once)

    compile_results = ["compile failed", None]

    def fake_try_compile_zeek_files(**kwargs):
        return compile_results.pop(0)

    monkeypatch.setattr(czp, "try_compile_zeek_files", fake_try_compile_zeek_files)

    files, usage = czp.generate_zeek_files_with_llm(
        spicy_info=spicy_info,
        analyzer_name="DNS",
        metadata=metadata,
        model="test-model",
        config_path=None,
        max_retries=2,
        instructions_path=instructions_file,
    )

    assert files.evt == "good evt\n"
    assert "compile failed" in user_messages[1]
    assert "bad evt" in user_messages[1]
    assert usage["attempts"][0]["usage"] == {"tokens": 1}
    assert usage["attempts"][1]["usage"] == {"tokens": 2}
