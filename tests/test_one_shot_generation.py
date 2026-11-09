import json
from types import SimpleNamespace

from scripts import generate_grammar_codex_harness as codex_harness
from scripts import generate_grammar_one_shot as one_shot
from src import agent, validation
from src.prompts import (
    build_one_shot_system_message,
    build_one_shot_user_message,
)
from src.protocol import ParserArgument, Protocol, ProtocolSpecType, ProtocolType
from src.providers import ModelConfig
from src.target_language import get_target_language
from src.tools.compiler import CompileResult
from src.tools.runtime import PacketVerdict, SemanticCheckResult


def make_protocol(*, parser_arguments=()) -> Protocol:
    return Protocol(
        name="example",
        full_name="Example Protocol",
        type=ProtocolType.BINARY,
        spec_type=ProtocolSpecType.OTHER,
        spec_id=None,
        local_spec_file=None,
        parser_arguments=parser_arguments,
    )


def make_target_language():
    return SimpleNamespace(
        name="spicy",
        display_name="Spicy",
        source_extension=".spicy",
        create_compiler=lambda: object(),
        create_runtime=lambda: object(),
        create_response_cleaner=lambda: (
            lambda response: response.removeprefix("preamble\n")
        ),
        supports_parse_tree_summary=True,
    )


def test_one_shot_prompt_keeps_language_context_and_omits_harness_tools():
    language_context = "\n<LANGUAGE MANUAL SENTINEL>\n"
    protocol = make_protocol(
        parser_arguments=(
            ParserArgument("frame_length", "uint64", "Configured frame length."),
        )
    )

    prompt = build_one_shot_system_message(
        model_name="test",
        target_language_context=language_context,
        target_language=get_target_language("spicy"),
        protocol=protocol,
    )

    assert prompt.count(language_context) == 1
    assert "frame_length: uint64" in prompt
    assert "validate_grammar" not in prompt
    assert "retrieve_rfc_by_id" not in prompt
    assert "tools provided" not in prompt


def test_one_shot_user_message_preserves_spec_verbatim_at_the_front():
    spec = "first line\n\nlast line\n"

    message = build_one_shot_user_message(spec)

    assert message.startswith(spec)
    assert message == spec + "\nPlease output the grammar."
    assert "tool" not in message


def test_generation_scripts_share_the_same_evaluation_interface():
    one_shot_args = vars(one_shot.parse_args(["example", "-m", "test-model"]))
    codex_args = vars(codex_harness.parse_args(["example"]))

    assert set(one_shot_args) == set(codex_args) - {"codex_bin"}
    assert one_shot_args["mode"] == codex_args["mode"] == "full-context"


def test_validate_grammar_stops_after_compilation_failure(monkeypatch):
    parser_called = False

    def fake_parser(*args, **kwargs):
        nonlocal parser_called
        parser_called = True

    monkeypatch.setattr(
        validation,
        "run_grammar_compiler",
        lambda *args, **kwargs: CompileResult(False, "bad syntax"),
    )
    monkeypatch.setattr(validation, "run_parser_check_from_code", fake_parser)

    result = validation.validate_grammar(
        "preamble\nmodule Example;",
        make_protocol(),
        make_target_language(),
    )

    assert result.code == "module Example;"
    assert result.compiled is False
    assert result.success is False
    assert parser_called is False
    assert "bad syntax" in result.feedback()


def test_validate_grammar_runs_packets_once_after_compilation(monkeypatch):
    protocol = make_protocol()
    packet_check = SemanticCheckResult(
        protocol=protocol,
        verdicts=[
            PacketVerdict(
                packet_path="example/positive/valid.bin",
                expected_accept=True,
                actual_accept=True,
                error="",
            )
        ],
    )
    parser_calls = []

    monkeypatch.setattr(
        validation,
        "run_grammar_compiler",
        lambda *args, **kwargs: CompileResult(True, None),
    )

    def fake_parser(*args, **kwargs):
        parser_calls.append((args, kwargs))
        return packet_check

    monkeypatch.setattr(validation, "run_parser_check_from_code", fake_parser)

    result = validation.validate_grammar(
        "module Example;",
        protocol,
        make_target_language(),
        negative_packets_enabled=False,
        show_parsing_tree=True,
    )

    assert len(parser_calls) == 1
    parsing_config = parser_calls[0][1]["parsing_config"]
    assert parsing_config.negative_packets_enabled is False
    assert parsing_config.include_parse_tree_summary is True
    assert result.success is True
    assert result.iteration_stats().positive_accepted == 1
    assert result.feedback() == "All packets parsed correctly. Say TERMINATE."


def test_validate_grammar_forwards_preprocessing_toggle(monkeypatch):
    compiler_calls = []

    def fake_compiler(*args, **kwargs):
        compiler_calls.append((args, kwargs))
        return CompileResult(False, "compiler error")

    monkeypatch.setattr(validation, "run_grammar_compiler", fake_compiler)

    validation.validate_grammar(
        "module Example;",
        make_protocol(),
        make_target_language(),
        preprocessing_enabled=False,
    )

    assert compiler_calls[0][1]["preprocessing_enabled"] is False


def test_script_makes_one_query_then_validates_the_answer(monkeypatch, tmp_path):
    protocol = make_protocol()
    target_language = make_target_language()
    manual_path = tmp_path / "manual.md"
    manual_path.write_text("LANGUAGE MANUAL")
    output_path = tmp_path / "answer.spicy"
    metrics_path = tmp_path / "metrics.json"
    packet_check = SemanticCheckResult(protocol=protocol, verdicts=[])
    query_calls = []
    validation_calls = []

    monkeypatch.setattr(one_shot, "enable_runtime_log_file", lambda path: None)
    monkeypatch.setattr(one_shot, "get_target_language", lambda name: target_language)
    monkeypatch.setattr(one_shot, "load_protocol", lambda name: protocol)
    monkeypatch.setattr(one_shot, "load_spec", lambda selected: "SPECIFICATION")
    monkeypatch.setattr(one_shot, "read_config", lambda path: ({}, {}))
    monkeypatch.setattr(
        one_shot,
        "get_model_config",
        lambda model, params: ModelConfig(model=model, model_name=model, provider="openai-account"),
    )
    monkeypatch.setattr(one_shot, "get_protocol_packet_paths", lambda name: ([], []))

    def fake_query(llm_config, system_message, user_message):
        query_calls.append((llm_config, system_message, user_message))
        return "preamble\nmodule Example;", {"total_usage": {"total_tokens": 7}}

    def fake_validate(response, selected_protocol, selected_language, **kwargs):
        validation_calls.append((response, selected_protocol, selected_language, kwargs))
        return validation.GrammarValidationResult(
            code="module Example;",
            compiled=True,
            packet_check=packet_check,
        )

    monkeypatch.setattr(one_shot, "query_llm_once", fake_query)
    monkeypatch.setattr(one_shot, "validate_grammar", fake_validate)

    exit_code = one_shot.main([
        "--model",
        "test-model",
        "--instructions-file",
        str(manual_path),
        "--output-file",
        str(output_path),
        "--metrics-file",
        str(metrics_path),
        "example",
    ])

    assert exit_code == 0
    assert len(query_calls) == 1
    assert query_calls[0][2].startswith("SPECIFICATION")
    assert "data/raw/example/positive/" in query_calls[0][2]
    assert "data/raw/example/negative/" in query_calls[0][2]
    assert "LANGUAGE MANUAL" in query_calls[0][1]
    assert "validate_grammar" not in query_calls[0][1]
    assert len(validation_calls) == 1
    assert validation_calls[0][0] == "preamble\nmodule Example;"
    assert output_path.read_text() == "module Example;"
    metrics = json.loads(metrics_path.read_text())
    assert metrics["max_total_retries"] == 1
    assert metrics["mode"] == "full-context"
    assert validation_calls[0][3]["negative_packets_enabled"] is True
    assert validation_calls[0][3]["preprocessing_enabled"] is True


def test_criteria_only_mode_sends_no_spec_or_manual(monkeypatch, tmp_path):
    protocol = make_protocol()
    target_language = make_target_language()
    output_path = tmp_path / "answer.spicy"
    packet_check = SemanticCheckResult(protocol=protocol, verdicts=[])
    query_calls = []

    monkeypatch.setattr(one_shot, "enable_runtime_log_file", lambda path: None)
    monkeypatch.setattr(one_shot, "get_target_language", lambda name: target_language)
    monkeypatch.setattr(one_shot, "load_protocol", lambda name: protocol)
    monkeypatch.setattr(
        one_shot,
        "load_spec",
        lambda selected: (_ for _ in ()).throw(AssertionError("spec was loaded")),
    )
    monkeypatch.setattr(
        one_shot,
        "load_file",
        lambda path: (_ for _ in ()).throw(AssertionError("manual was loaded")),
    )
    monkeypatch.setattr(one_shot, "read_config", lambda path: ({}, {}))
    monkeypatch.setattr(
        one_shot,
        "get_model_config",
        lambda model, params: ModelConfig(model=model, model_name=model, provider="openai-account"),
    )
    monkeypatch.setattr(one_shot, "get_protocol_packet_paths", lambda name: ([], []))

    def fake_query(llm_config, system_message, user_message):
        query_calls.append((llm_config, system_message, user_message))
        return "module Example;", None

    monkeypatch.setattr(one_shot, "query_llm_once", fake_query)
    monkeypatch.setattr(
        one_shot,
        "validate_grammar",
        lambda *args, **kwargs: validation.GrammarValidationResult(
            code="module Example;",
            compiled=True,
            packet_check=packet_check,
        ),
    )

    exit_code = one_shot.main(
        [
            "example",
            "--mode",
            "criteria-only",
            "--model",
            "test-model",
            "--output-file",
            str(output_path),
        ]
    )

    assert exit_code == 0
    assert len(query_calls) == 1
    assert "Spicy v1.16.0" in query_calls[0][1]
    assert "data/raw/example/positive/" in query_calls[0][1]
    assert "data/raw/example/negative/" in query_calls[0][1]
    assert "SPECIFICATION" not in query_calls[0][1]
    assert "LANGUAGE MANUAL" not in query_calls[0][1]


def test_refinement_tool_delegates_to_global_validator(monkeypatch):
    protocol = make_protocol()
    target_language = make_target_language()
    target_language.assistant_name = "SpicyDeveloper"
    failed_packet_check = SemanticCheckResult(
        protocol=protocol,
        verdicts=[
            PacketVerdict(
                packet_path="example/positive/valid.bin",
                expected_accept=True,
                actual_accept=False,
                error="parse failed",
            )
        ],
    )
    successful_packet_check = SemanticCheckResult(
        protocol=protocol,
        verdicts=[
            PacketVerdict(
                packet_path="example/positive/valid.bin",
                expected_accept=True,
                actual_accept=True,
                error="",
            )
        ],
    )
    validation_calls = []
    registered_functions = {}

    class FakeAssistant:
        def __init__(self, **kwargs):
            self.tools = []

    class FakeUserProxy:
        def __init__(self, **kwargs):
            pass

        def initiate_chat(self, assistant, message):
            first_feedback = registered_functions["validate_grammar"](
                "preamble\nmodule First;"
            )
            assert "failed to parse" in first_feedback
            final_feedback = registered_functions["validate_grammar"](
                "preamble\nmodule Example;"
            )
            assert final_feedback == "All packets parsed correctly. Say TERMINATE."
            return SimpleNamespace(cost=None)

    def fake_register(function, *, caller, executor, name, description):
        registered_functions[name] = function
        caller.tools.append(SimpleNamespace(name=name))

    def fake_validate(code, selected_protocol, selected_language, **kwargs):
        validation_calls.append((code, selected_protocol, selected_language, kwargs))
        return validation.GrammarValidationResult(
            code=selected_language.create_response_cleaner()(code) or code,
            compiled=True,
            packet_check=(
                failed_packet_check
                if len(validation_calls) == 1
                else successful_packet_check
            ),
        )

    monkeypatch.setattr(agent.autogen, "AssistantAgent", FakeAssistant)
    monkeypatch.setattr(agent.autogen, "UserProxyAgent", FakeUserProxy)
    monkeypatch.setattr(agent.autogen.agentchat, "register_function", fake_register)
    monkeypatch.setattr(agent.validation, "validate_grammar", fake_validate)
    monkeypatch.setattr(
        agent,
        "get_protocol_packet_paths",
        lambda name: (["example/positive/valid.bin"], []),
    )

    code, success, stats, usage = agent.run_refinement_loop(
        llm_config=ModelConfig(model="test-model", model_name="test-model", provider="openai-account"),
        system_message="system",
        spec_content="specification",
        protocol=protocol,
        target_language=target_language,
        agent_config={
            "max_retries": 2,
            "enable_preprocessing": False,
            "enable_rfc_retrieval_tool": False,
            "enable_negative_packets": True,
            "show_packet_representation": False,
            "show_parsing_tree": False,
        },
    )

    assert len(validation_calls) == 2
    assert validation_calls[0][0] == "preamble\nmodule First;"
    assert validation_calls[1][0] == "preamble\nmodule Example;"
    assert validation_calls[0][3]["attempt"] == 1
    assert validation_calls[1][3]["attempt"] == 2
    assert validation_calls[0][3]["max_attempts"] == 2
    assert validation_calls[1][3]["max_attempts"] == 2
    assert validation_calls[0][3]["preprocessing_enabled"] is False
    assert validation_calls[1][3]["preprocessing_enabled"] is False
    assert code == "module Example;"
    assert success is True
    assert stats["best_iteration"] == 2
    assert stats["termination_reason"] == "success"
    assert stats["first_successful_compile_iteration"] == 1
    assert stats["iterations"] == [
        {
            "compiled": True,
            "positive_accepted": 0,
            "negative_rejected": 0,
            "changed_from_previous": False,
        },
        {
            "compiled": True,
            "positive_accepted": 1,
            "negative_rejected": 0,
            "changed_from_previous": True,
        },
    ]
    assert usage is None
