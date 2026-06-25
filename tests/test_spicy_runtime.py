import json
from pathlib import Path

import pytest

from src.protocol import ParserArgument, Protocol, ProtocolSpecType, ProtocolType
from src.tools.compiler import CompileResult
from src.tools.runtime import ParseResult, RuntimeAdapter, RuntimeArtifact, run_parser_check
from src.tools.spicy.preprocess import parse_spicy_file
from src.tools.spicy.runtime import (
    bind_packet_arguments,
    generate_parser_wrapper,
    load_packet_arguments,
    precompile_spicy_packet_parser,
    render_spicy_argument,
)


ARGUMENTS = (
    ParserArgument("frame_length", "uint64"),
    ParserArgument("has_fecf", "bool"),
)


def write_parser(path: Path, arguments: str = "frame_length: uint64, has_fecf: bool") -> Path:
    path.write_text(
        "module Test;\n\n"
        f"public type Packet = unit(\n    {arguments}\n) {{\n"
        "    data: bytes &eod;\n"
        "};\n"
    )
    return path


def test_parse_spicy_file_extracts_multiline_entry_arguments(tmp_path):
    info = parse_spicy_file(write_parser(tmp_path / "parser.spicy"))

    assert info.entry_unit_args == [
        ("frame_length", "uint64", None),
        ("has_fecf", "bool", None),
    ]


def test_wrapper_binds_values_by_name_in_generated_signature_order(tmp_path):
    parser = write_parser(
        tmp_path / "parser.spicy",
        "has_fecf: bool, frame_length: uint64",
    )
    info = parse_spicy_file(parser)
    bound = bind_packet_arguments(
        info,
        ARGUMENTS,
        {"frame_length": 16, "has_fecf": True},
    )

    wrapper = generate_parser_wrapper(info, bound)

    assert "Test::Packet(True, 16)" in wrapper


def test_signature_validation_reports_type_mismatch(tmp_path):
    info = parse_spicy_file(
        write_parser(tmp_path / "parser.spicy", "frame_length: uint32, has_fecf: bool")
    )

    with pytest.raises(RuntimeError, match=r"frame_length is uint32 \(expected uint64\)"):
        bind_packet_arguments(info, ARGUMENTS, {"frame_length": 16, "has_fecf": True})


def test_sidecar_requires_exact_named_values(tmp_path):
    packet = tmp_path / "frame.bin"
    packet.write_bytes(b"packet")
    Path(f"{packet}.args.json").write_text(json.dumps({"frame_length": 16, "extra": 1}))
    info = parse_spicy_file(write_parser(tmp_path / "parser.spicy"))

    with pytest.raises(RuntimeError, match="missing values: has_fecf; unknown values: extra"):
        bind_packet_arguments(info, ARGUMENTS, load_packet_arguments(packet))


@pytest.mark.parametrize(
    ("argument", "value", "rendered"),
    [
        (ParserArgument("enabled", "bool"), True, "True"),
        (ParserArgument("offset", "int8"), -4, "-4"),
        (ParserArgument("length", "uint16"), 512, "512"),
        (ParserArgument("label", "string"), "a\\b", '"a\\\\b"'),
    ],
)
def test_render_spicy_argument(argument, value, rendered):
    assert render_spicy_argument(argument, value) == rendered


def test_render_spicy_argument_checks_integer_range():
    with pytest.raises(RuntimeError, match="outside uint8 range"):
        render_spicy_argument(ParserArgument("length", "uint8"), 256)


def test_packet_precompile_writes_wrapper_and_selects_it(tmp_path, monkeypatch):
    parser = write_parser(tmp_path / "parser.spicy")
    packet = tmp_path / "frame.bin"
    packet.write_bytes(b"packet")
    Path(f"{packet}.args.json").write_text(
        json.dumps({"frame_length": 16, "has_fecf": True})
    )
    protocol = Protocol(
        name="test",
        full_name=None,
        type=ProtocolType.BINARY,
        spec_type=ProtocolSpecType.BLUEBOOK,
        spec_id=None,
        local_spec_file=None,
        parser_arguments=ARGUMENTS,
    )
    captured = {}

    def fake_compile_to_hlto(grammar_file, output_path, additional_sources=()):
        captured["grammar_file"] = grammar_file
        captured["output_path"] = output_path
        captured["additional_sources"] = additional_sources
        return CompileResult(success=True, errors=None)

    monkeypatch.setattr("src.tools.spicy.runtime.compile_to_hlto", fake_compile_to_hlto)

    artifact = precompile_spicy_packet_parser(parser, packet, protocol, tmp_path / "build")

    assert artifact.parser_name == "Test_Wrapper::Main"
    assert captured["grammar_file"] == parser
    wrapper_path = captured["additional_sources"][0]
    assert "Test::Packet(16, True)" in wrapper_path.read_text()


def test_runtime_reuses_wrapper_for_identical_packet_arguments(tmp_path, monkeypatch):
    raw_dir = tmp_path / "raw"
    positive_dir = raw_dir / "test" / "positive"
    positive_dir.mkdir(parents=True)
    for name in ("one.bin", "two.bin"):
        packet = positive_dir / name
        packet.write_bytes(b"packet")
        Path(f"{packet}.args.json").write_text(
            json.dumps({"frame_length": 16, "has_fecf": True})
        )

    monkeypatch.setattr("src.tools.runtime._RAW_BASE_DIR", raw_dir)
    builds = []
    cleanups = []

    def prepare_packet_parser(source, packet, protocol, build_dir):
        builds.append(packet)
        return RuntimeArtifact(source, cleanup=lambda: cleanups.append(packet))

    runtime = RuntimeAdapter(
        parse_packet=lambda parser, packet, parser_name: ParseResult(
            packet_path=str(packet), success=True, error=""
        ),
        prepare_packet_parser=prepare_packet_parser,
        packet_parser_cache_key=lambda packet: Path(f"{packet}.args.json").read_text(),
        packet_argument_language="spicy",
    )
    protocol = Protocol(
        name="test",
        full_name=None,
        type=ProtocolType.BINARY,
        spec_type=ProtocolSpecType.BLUEBOOK,
        spec_id=None,
        local_spec_file=None,
        parser_arguments=ARGUMENTS,
    )

    result = run_parser_check(tmp_path / "parser.spicy", runtime, protocol, False)

    assert result.all_passed
    assert len(result.verdicts) == 2
    assert len(builds) == 1
    assert len(cleanups) == 1
