import json
from pathlib import Path
from queue import Queue

import pytest

import run_parser_trace
from src.tools.runtime import ParseResult


def test_detect_runtime_from_extension():
    assert run_parser_trace.detect_runtime(Path("parser.spicy"), "auto") == "spicy"
    assert run_parser_trace.detect_runtime(Path("parser.3d"), "auto") == "3d"


def test_detect_runtime_rejects_unknown_extension():
    with pytest.raises(ValueError, match="Cannot detect parser runtime"):
        run_parser_trace.detect_runtime(Path("parser.txt"), "auto")


def test_discover_packet_paths_filters_bin_and_sorts(tmp_path):
    packet_dir = tmp_path / "packets"
    nested_dir = packet_dir / "nested"
    nested_dir.mkdir(parents=True)
    (packet_dir / "b.bin").write_bytes(b"b")
    (packet_dir / "a.txt").write_text("not a packet")
    (nested_dir / "a.bin").write_bytes(b"a")

    packet_paths = run_parser_trace.discover_packet_paths(packet_dir)

    assert packet_paths == [packet_dir / "b.bin", nested_dir / "a.bin"]


def test_discover_packet_paths_can_disable_recursion(tmp_path):
    packet_dir = tmp_path / "packets"
    nested_dir = packet_dir / "nested"
    nested_dir.mkdir(parents=True)
    (packet_dir / "root.bin").write_bytes(b"root")
    (nested_dir / "nested.bin").write_bytes(b"nested")

    packet_paths = run_parser_trace.discover_packet_paths(packet_dir, recursive=False)

    assert packet_paths == [packet_dir / "root.bin"]


def test_resolve_worker_count_caps_at_packet_count():
    assert run_parser_trace.resolve_worker_count(16, 3) == 3
    assert run_parser_trace.resolve_worker_count(1, 3) == 1
    assert run_parser_trace.resolve_worker_count(None, 0) == 0


def test_resolve_worker_count_rejects_non_positive_workers():
    with pytest.raises(ValueError, match="workers"):
        run_parser_trace.resolve_worker_count(0, 3)


def test_build_summary_counts_accepts_rejects_and_samples_errors(tmp_path):
    results = [
        run_parser_trace.PacketTraceResult("one.bin", True, ""),
        run_parser_trace.PacketTraceResult("two.bin", False, "bad packet"),
        run_parser_trace.PacketTraceResult("three.bin", False, "also bad"),
    ]

    summary = run_parser_trace.build_summary(
        parser_path=tmp_path / "parser.spicy",
        runtime_name="spicy",
        packet_dir=tmp_path / "packets",
        results=results,
        elapsed_seconds=2.0,
        workers=2,
        show_errors=1,
    )

    assert summary.total_packets == 3
    assert summary.accepted == 1
    assert summary.rejected == 2
    assert summary.accepted_percent == pytest.approx(100 / 3)
    assert summary.rejected_percent == pytest.approx(200 / 3)
    assert summary.packets_per_second == pytest.approx(1.5)
    assert summary.errors == [run_parser_trace.ErrorSample("two.bin", "bad packet")]


def test_write_json_summary(tmp_path):
    summary = run_parser_trace.TraceSummary(
        parser="parser.spicy",
        runtime="spicy",
        packet_dir="packets",
        total_packets=1,
        accepted=0,
        rejected=1,
        accepted_percent=0.0,
        rejected_percent=100.0,
        elapsed_seconds=0.5,
        packets_per_second=2.0,
        workers=1,
        errors=[run_parser_trace.ErrorSample("packet.bin", "parse error")],
    )
    output_path = tmp_path / "reports" / "summary.json"

    run_parser_trace.write_json_summary(summary, output_path)

    payload = json.loads(output_path.read_text())
    assert payload["total_packets"] == 1
    assert payload["errors"] == [{"packet_path": "packet.bin", "error": "parse error"}]


def test_submit_worker_tasks_uses_worker_count_not_packet_count(tmp_path):
    class FakeExecutor:
        def __init__(self):
            self.calls = []

        def submit(self, fn, *args):
            self.calls.append((fn, args))
            return object()

    executor = FakeExecutor()
    artifact = run_parser_trace.RuntimeArtifact(tmp_path / "parser.hlto", parser_name="Parser::Main")

    futures = run_parser_trace._submit_worker_tasks(
        executor,
        worker_count=3,
        runtime_name="spicy",
        artifact=artifact,
        task_queue=object(),
        result_queue=object(),
    )

    assert len(futures) == 3
    assert len(executor.calls) == 3
    assert all(call[0] is run_parser_trace._trace_worker for call in executor.calls)


def test_trace_worker_processes_task_queue_with_fake_runtime(tmp_path, monkeypatch):
    class FakeRuntime:
        def run_parser(self, artifact, packet_path):
            accepted = packet_path.name == "good.bin"
            return ParseResult(
                packet_path=str(packet_path),
                success=accepted,
                error="" if accepted else "rejected",
            )

    class FakeTarget:
        runtime = FakeRuntime()

    monkeypatch.setattr(run_parser_trace, "get_target_language", lambda _name: FakeTarget())
    good_packet = tmp_path / "good.bin"
    bad_packet = tmp_path / "bad.bin"
    good_packet.write_bytes(b"good")
    bad_packet.write_bytes(b"bad")
    task_queue = Queue()
    result_queue = Queue()
    task_queue.put(str(good_packet))
    task_queue.put(str(bad_packet))
    task_queue.put(None)

    processed = run_parser_trace._trace_worker(
        "spicy",
        str(tmp_path / "parser.hlto"),
        None,
        task_queue,
        result_queue,
    )

    results = [result_queue.get(), result_queue.get()]
    assert processed == 2
    assert [result.success for result in results] == [True, False]
    assert results[1].error == "rejected"
