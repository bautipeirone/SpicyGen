from pathlib import Path
from types import SimpleNamespace

import pytest

import create_zeek_package as czp
from src.providers import ModelConfig


def test_derive_analyzer_name_strips_known_model_suffix():
    assert czp.derive_analyzer_name("DNS_gpt") == "DNS"
    assert czp.derive_analyzer_name("HTTP_gemini") == "HTTP"
    assert czp.derive_analyzer_name("VXLAN") == "VXLAN"


def test_protocol_metadata_selects_first_zeek_supported_transport(tmp_path):
    metadata_file = tmp_path / "metadata.json"
    metadata_file.write_text('{"transport":["sctp","udp","tcp"],"port":53}')

    metadata = czp.ProtocolMetadata.from_json(metadata_file)

    assert metadata.transports == ["udp", "tcp"]
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
        transports=["udp"],
        compile_errors="bad evt syntax",
        previous_files=previous_files,
    )

    assert "The parser module is: DNS_gpt" in prompt
    assert "The Zeek integration module MUST be: Zeek_DNS" in prompt
    assert "The .evt MUST import both DNS_gpt and Zeek_DNS" in prompt
    assert "bad evt syntax" in prompt
    assert "old evt" in prompt


def test_build_metrics_includes_package_generation_summary(tmp_path):
    spicy_file = tmp_path / "dns.spicy"
    spicy_file.write_text("module DNS_gpt;\npublic type Message = unit {};\n")
    spicy_info = czp.SpicyParserInfo("DNS_gpt", "Message", spicy_file)
    metadata = czp.ProtocolMetadata(
        port=53,
        transports=["udp", "tcp"],
    )

    metrics = czp.build_metrics(
        model="test-model",
        package_name="spicy-dns",
        package_dir=tmp_path / "spicy-dns",
        spicy_info=spicy_info,
        analyzer_name="DNS",
        metadata=metadata,
        elapsed_time_s=12.345,
        usage={"attempts": [{"attempt": 1, "usage": {"tokens": 42}}]},
    )

    assert metrics["model"] == "test-model"
    assert metrics["package_name"] == "spicy-dns"
    assert metrics["spicy_module"] == "DNS_gpt"
    assert metrics["transport_candidates"] == ["udp", "tcp"]
    assert metrics["time_elapsed_s"] == 12.35
    assert metrics["usage"]["attempts"][0]["usage"]["tokens"] == 42


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
        calls.append((cmd, kwargs, Path(cmd[3]).read_text()))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(czp.subprocess, "run", fake_run)

    spicy_source = spicy_file.read_text() + "public type Messages = unit { : Message[] &eod; };\n"
    assert czp.try_compile_zeek_files(
        spicy_info=spicy_info,
        spicy_source=spicy_source,
        analyzer_name="DNS",
        files=files,
    ) is None

    cmd, kwargs, compiled_source = calls[0]
    assert compiled_source == spicy_source
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
        spicy_source=spicy_file.read_text(),
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
    metadata = czp.ProtocolMetadata(port=53)
    instructions_file = tmp_path / "INTEGRATION.md"
    instructions_file.write_text("Use correct Zeek integration syntax.")
    responses = [
        '{"evt":"bad evt","zeek_spicy":"bad spicy","main_zeek":"bad zeek"}',
        '{"evt":"good evt","zeek_spicy":"good spicy","main_zeek":"good zeek"}',
    ]
    user_messages = []

    monkeypatch.setattr(czp, "read_config", lambda path: ({}, {}))
    monkeypatch.setattr(
        czp,
        "get_model_config",
        lambda model, params: ModelConfig(model=model, model_name=model, provider="openai-account"),
    )

    def fake_query_llm_once(llm_config, system_prompt, user_message):
        user_messages.append(user_message)
        return responses.pop(0), {"tokens": len(user_messages)}

    monkeypatch.setattr(czp, "query_llm_once", fake_query_llm_once)

    compile_results = ["compile failed", None]
    compiled_sources = []

    def fake_try_compile_zeek_files(**kwargs):
        compiled_sources.append(kwargs["spicy_source"])
        return compile_results.pop(0)

    monkeypatch.setattr(czp, "try_compile_zeek_files", fake_try_compile_zeek_files)

    files, usage = czp.generate_zeek_files_with_llm(
        spicy_info=spicy_info,
        spicy_source=spicy_file.read_text(),
        analyzer_name="DNS",
        metadata=metadata,
        model="test-model",
        config_path=None,
        max_retries=2,
        instructions_path=instructions_file,
    )

    assert files.evt == "good evt\n"
    assert compiled_sources == [spicy_file.read_text()] * 2
    assert "compile failed" in user_messages[1]
    assert "bad evt" in user_messages[1]
    assert usage["attempts"][0]["usage"] == {"tokens": 1}
    assert usage["attempts"][1]["usage"] == {"tokens": 2}


def test_generate_zeek_files_retries_after_stream_unit_violation(tmp_path, monkeypatch):
    spicy_file = tmp_path / "dns.spicy"
    spicy_file.write_text("module DNS_gpt;\npublic type Message = unit {};\n")
    spicy_info = czp.SpicyParserInfo("DNS_gpt", "Message", spicy_file)
    instructions_file = tmp_path / "INTEGRATION.md"
    instructions_file.write_text("Use correct Zeek integration syntax.")
    analyzer = "protocol analyzer DNS_TCP over TCP:\n    parse originator with DNS_gpt::{unit};\n"
    responses = [
        '{"evt":"%s","zeek_spicy":"spicy","main_zeek":"zeek"}' % analyzer.format(unit="Message").replace("\n", "\\n"),
        '{"evt":"%s","zeek_spicy":"spicy","main_zeek":"zeek"}' % analyzer.format(unit="Messages").replace("\n", "\\n"),
    ]
    user_messages = []

    monkeypatch.setattr(czp, "read_config", lambda path: ({}, {}))
    monkeypatch.setattr(
        czp,
        "get_model_config",
        lambda model, params: ModelConfig(model=model, model_name=model, provider="openai-account"),
    )

    def fake_query_llm_once(llm_config, system_prompt, user_message):
        user_messages.append(user_message)
        return responses.pop(0), None

    monkeypatch.setattr(czp, "query_llm_once", fake_query_llm_once)
    compile_calls = []
    monkeypatch.setattr(czp, "try_compile_zeek_files", lambda **kwargs: compile_calls.append(kwargs))

    files, _ = czp.generate_zeek_files_with_llm(
        spicy_info=spicy_info,
        spicy_source=spicy_file.read_text(),
        analyzer_name="DNS",
        metadata=czp.ProtocolMetadata(port=53, transports=["tcp"]),
        model="test-model",
        config_path=None,
        max_retries=2,
        instructions_path=instructions_file,
        stream_unit="Messages",
    )

    assert "DNS_gpt::Messages" in files.evt
    assert len(compile_calls) == 1
    assert "TCP analyzer MUST parse originator and responder with DNS_gpt::Messages" in user_messages[0]
    assert "violate the CONSTRAINTS" in user_messages[1]
    assert "Analyzer DNS_TCP over TCP must parse" in user_messages[1]


# ---------------------------------------------------------------------------
# TCP stream unit
# ---------------------------------------------------------------------------

def test_derive_stream_unit_name_pluralizes_entry_unit():
    assert czp.derive_stream_unit_name("Message", "") == "Messages"
    assert czp.derive_stream_unit_name("Address", "") == "Addresses"
    assert czp.derive_stream_unit_name("HTTPMessage", "") == "HTTPMessages"


def test_derive_stream_unit_name_avoids_existing_type():
    code = "public type Messages = unit {};"
    assert czp.derive_stream_unit_name("Message", code) == "MessageStream"


def test_build_stream_unit_with_synchronize_omits_eod_and_confirms():
    unit = czp.build_stream_unit("Message", "Messages", synchronize=True)

    assert "public type Messages = unit {" in unit
    assert ": (Message &synchronize)[];" in unit
    assert "on %synced { confirm; }" in unit
    assert "&eod" not in unit


def test_build_stream_unit_without_synchronize_uses_eod():
    unit = czp.build_stream_unit("Message", "Messages", synchronize=False)

    assert ": Message[] &eod;" in unit
    assert "&synchronize" not in unit


def test_add_tcp_stream_unit_prefers_synchronize(monkeypatch):
    monkeypatch.setattr(czp, "validate_spicy_code", lambda code: None)

    code, unit, synchronize = czp.add_tcp_stream_unit("module M;\npublic type Message = unit {};", "Message")

    assert (unit, synchronize) == ("Messages", True)
    assert code.startswith("module M;\npublic type Message = unit {};\n")
    assert "(Message &synchronize)[]" in code


def test_add_tcp_stream_unit_falls_back_to_eod_when_synchronize_is_rejected(monkeypatch):
    validated = []

    def fake_validate(code):
        validated.append(code)
        return "&synchronize cannot be used on field"

    monkeypatch.setattr(czp, "validate_spicy_code", fake_validate)

    code, unit, synchronize = czp.add_tcp_stream_unit("module M;\npublic type Message = unit {};", "Message")

    assert (unit, synchronize) == ("Messages", False)
    assert "&synchronize" in validated[0]
    assert "Message[] &eod;" in code
    assert "&synchronize" not in code


def test_validate_spicy_code_reports_missing_spicyc(monkeypatch):
    monkeypatch.setattr(czp.shutil, "which", lambda binary: None)

    assert "not found on PATH" in czp.validate_spicy_code("module M;")


EVT_OK = """import DNS_gpt;
protocol analyzer DNS_TCP over TCP:
    parse originator with DNS_gpt::Messages,
    parse responder with DNS_gpt::Messages;
protocol analyzer DNS_UDP over UDP:
    parse originator with DNS_gpt::Message,
    parse responder with DNS_gpt::Message;
on DNS_gpt::Message -> event DNS::message($conn, $is_orig, self.id);
"""


def test_check_evt_stream_unit_accepts_valid_evt():
    assert czp.check_evt_stream_unit(EVT_OK, "DNS_gpt", "Message", "Messages") is None


def test_check_evt_stream_unit_rejects_event_on_stream_unit():
    evt = EVT_OK.replace("on DNS_gpt::Message ->", "on DNS_gpt::Messages ->")

    errors = czp.check_evt_stream_unit(evt, "DNS_gpt", "Message", "Messages")

    assert "Events must not be mapped to DNS_gpt::Messages" in errors


def test_check_evt_stream_unit_rejects_tcp_analyzer_without_stream_unit():
    evt = EVT_OK.replace("parse responder with DNS_gpt::Messages", "parse responder with DNS_gpt::Message")

    errors = czp.check_evt_stream_unit(evt, "DNS_gpt", "Message", "Messages")

    assert "Analyzer DNS_TCP over TCP must parse" in errors


def test_check_evt_stream_unit_rejects_udp_analyzer_with_stream_unit():
    evt = EVT_OK.replace(
        "parse originator with DNS_gpt::Message,\n    parse responder with DNS_gpt::Message;",
        "parse with DNS_gpt::Messages;",
    )

    errors = czp.check_evt_stream_unit(evt, "DNS_gpt", "Message", "Messages")

    assert "Analyzer DNS_UDP over UDP must parse with DNS_gpt::Message" in errors


# ---------------------------------------------------------------------------
# replace_throw_with_logging
# ---------------------------------------------------------------------------

def test_replace_throw_with_logging_replaces_single_throw():
    code = 'module DNS;\nthrow "bad packet";'
    result = czp.replace_runtime_throw_with_logging(code)
    assert 'throw' not in result
    assert 'zeek::weird("bad packet")' in result


def test_replace_throw_with_logging_replaces_multiple_throws():
    code = 'module DNS;\nthrow "error one";\nthrow "error two";'
    result = czp.replace_runtime_throw_with_logging(code)
    assert 'throw' not in result
    assert 'zeek::weird("error one")' in result
    assert 'zeek::weird("error two")' in result


def test_add_zeek_import_has_import_statement():
    code = 'module DNS;\npublic type Message = unit {};'
    result = czp.add_zeek_import(code)
    assert 'import zeek;' in result


def test_replace_throw_with_logging_no_throw_still_adds_import():
    """Even when there are no throw statements the import must be added."""
    code = 'module DNS;\npublic type Message = unit {};' 
    result = czp.replace_runtime_throw_with_logging(czp.add_zeek_import(code))
    assert 'import zeek;' in result
    assert 'throw' not in result

# ---------------------------------------------------------------------------
# replace_require_with_logging
# ---------------------------------------------------------------------------

def test_replace_require_with_logging_replaces_field_constraint():
    code = 'version: uint8 &requires=($$ == 1);'

    result = czp.replace_require_with_logging(code)

    assert '&requires' not in result
    assert 'version: uint8' in result
    assert 'if (!($$ == 1))' in result
    assert 'zeek::weird("Constraint ($$ == 1) violated in field version")' in result


def test_replace_require_with_logging_handles_unnamed_field():
    code = ': void &requires=(self.length > 0);'

    result = czp.replace_require_with_logging(code)

    assert '&requires' not in result
    assert ': void' in result
    assert 'zeek::weird("Constraint (self.length > 0) violated")' in result
    assert 'in field' not in result


def test_replace_require_with_logging_replaces_all_constraints_and_escapes_quotes():
    code = (
        'marker: bytes &requires=($$ == b"\\x00");\n'
        'version: uint8 &requires=($$ == 1);'
    )

    result = czp.replace_require_with_logging(code)

    assert '&requires' not in result
    assert result.count('zeek::weird(') == 2
    assert "Constraint ($$ == b'\\x00') violated in field marker" in result
    assert 'Constraint ($$ == 1) violated in field version' in result
