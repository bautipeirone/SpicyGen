from dotenv import load_dotenv
load_dotenv()

import argparse
import concurrent.futures
import json
import multiprocessing
import os
import queue
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Iterable
from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.target_language import get_target_language
from src.tools.runtime import RuntimeArtifact


RUNTIME_BY_EXTENSION = {
    ".spicy": "spicy",
    ".3d": "3d",
}


@dataclass(frozen=True)
class PacketTraceResult:
    packet_path: str
    success: bool
    error: str = ""
    execution_error: bool = False


@dataclass(frozen=True)
class ErrorSample:
    packet_path: str
    error: str


@dataclass(frozen=True)
class TraceSummary:
    parser: str
    runtime: str
    packet_dir: str
    total_packets: int
    accepted: int
    rejected: int
    accepted_percent: float
    rejected_percent: float
    elapsed_seconds: float
    packets_per_second: float
    workers: int
    errors: list[ErrorSample]


def detect_runtime(parser_path: Path, runtime_name: str) -> str:
    if runtime_name != "auto":
        get_target_language(runtime_name)
        return runtime_name

    detected = RUNTIME_BY_EXTENSION.get(parser_path.suffix.lower())
    if detected is None:
        supported = ", ".join(sorted(RUNTIME_BY_EXTENSION))
        raise ValueError(
            f"Cannot detect parser runtime from '{parser_path}'. "
            f"Use --runtime or a supported extension: {supported}."
        )
    return detected


def discover_packet_paths(packet_dir: Path, recursive: bool = True) -> list[Path]:
    pattern = "**/*.bin" if recursive else "*.bin"
    return sorted(path for path in packet_dir.glob(pattern) if path.is_file())


def read_packet_list(path: Path) -> list[Path]:
    paths = [Path(line.strip()) for line in path.read_text().splitlines() if line.strip()]
    if len(paths) != len(set(paths)):
        raise ValueError(f"Packet list contains duplicate paths: {path}")
    missing = [packet for packet in paths if not packet.is_file()]
    if missing:
        raise FileNotFoundError(f"Packet list references a missing file: {missing[0]}")
    return paths


def resolve_worker_count(requested_workers: int | None, packet_count: int) -> int:
    if packet_count < 1:
        return 0
    workers = requested_workers if requested_workers is not None else (os.cpu_count() or 1)
    if workers < 1:
        raise ValueError("--workers must be at least 1.")
    return max(1, min(workers, packet_count))


def _trace_worker(
    runtime_name: str,
    artifact_path: str,
    parser_name: str | None,
    task_queue,
    result_queue,
) -> int:
    runtime = get_target_language(runtime_name).create_runtime()
    artifact = RuntimeArtifact(path=Path(artifact_path), parser_name=parser_name)
    processed = 0

    while True:
        packet_path = task_queue.get()
        if packet_path is None:
            return processed

        packet_path = Path(packet_path)
        try:
            parse_result = runtime.run_parser(artifact, packet_path)
            result_queue.put(
                PacketTraceResult(
                    packet_path=str(packet_path),
                    success=parse_result.success,
                    error=parse_result.error,
                )
            )
        except Exception as exc:
            result_queue.put(
                PacketTraceResult(
                    packet_path=str(packet_path),
                    success=False,
                    error=f"{type(exc).__name__}: {exc}",
                    execution_error=True,
                )
            )
        processed += 1


def _submit_worker_tasks(
    executor,
    worker_count: int,
    runtime_name: str,
    artifact: RuntimeArtifact,
    task_queue,
    result_queue,
):
    return [
        executor.submit(
            _trace_worker,
            runtime_name,
            str(artifact.path),
            artifact.parser_name,
            task_queue,
            result_queue,
        )
        for _ in range(worker_count)
    ]


def _enqueue_packets(packet_paths: Iterable[Path], worker_count: int, task_queue) -> None:
    for packet_path in packet_paths:
        task_queue.put(str(packet_path))
    for _ in range(worker_count):
        task_queue.put(None)


def _raise_finished_worker_errors(futures) -> None:
    for future in futures:
        if future.done():
            exc = future.exception()
            if exc is not None:
                raise RuntimeError(f"Parser worker failed: {exc}") from exc


def parse_packets_parallel(
    packet_paths: list[Path],
    runtime_name: str,
    artifact: RuntimeArtifact,
    worker_count: int,
    on_result: Callable[[PacketTraceResult], None] | None = None,
) -> list[PacketTraceResult]:
    if not packet_paths:
        return []

    with multiprocessing.Manager() as manager:
        task_queue = manager.Queue()
        result_queue = manager.Queue()

        with concurrent.futures.ProcessPoolExecutor(max_workers=worker_count) as executor:
            futures = _submit_worker_tasks(
                executor,
                worker_count,
                runtime_name,
                artifact,
                task_queue,
                result_queue,
            )
            _enqueue_packets(packet_paths, worker_count, task_queue)

            results: list[PacketTraceResult] = []
            while len(results) < len(packet_paths):
                try:
                    result = result_queue.get(timeout=0.2)
                except queue.Empty:
                    _raise_finished_worker_errors(futures)
                    continue

                results.append(result)
                if on_result is not None:
                    on_result(result)

            for future in futures:
                future.result()

    return results


def build_summary(
    parser_path: Path,
    runtime_name: str,
    packet_dir: Path,
    results: list[PacketTraceResult],
    elapsed_seconds: float,
    workers: int,
    show_errors: int,
) -> TraceSummary:
    total_packets = len(results)
    accepted = sum(1 for result in results if result.success)
    rejected = total_packets - accepted
    accepted_percent = (accepted / total_packets * 100.0) if total_packets else 0.0
    rejected_percent = (rejected / total_packets * 100.0) if total_packets else 0.0
    packets_per_second = (total_packets / elapsed_seconds) if elapsed_seconds > 0 else 0.0
    errors = [
        ErrorSample(packet_path=result.packet_path, error=result.error)
        for result in results
        if not result.success
    ][:show_errors]

    return TraceSummary(
        parser=str(parser_path),
        runtime=runtime_name,
        packet_dir=str(packet_dir),
        total_packets=total_packets,
        accepted=accepted,
        rejected=rejected,
        accepted_percent=accepted_percent,
        rejected_percent=rejected_percent,
        elapsed_seconds=elapsed_seconds,
        packets_per_second=packets_per_second,
        workers=workers,
        errors=errors,
    )


def summary_to_json(summary: TraceSummary) -> dict:
    payload = asdict(summary)
    payload["elapsed_seconds"] = round(summary.elapsed_seconds, 6)
    payload["packets_per_second"] = round(summary.packets_per_second, 6)
    payload["accepted_percent"] = round(summary.accepted_percent, 4)
    payload["rejected_percent"] = round(summary.rejected_percent, 4)
    return payload


def write_json_summary(summary: TraceSummary, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(summary_to_json(summary), indent=2) + "\n")


def write_packet_results(results: list[PacketTraceResult], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as output:
        for result in sorted(results, key=lambda item: item.packet_path):
            output.write(json.dumps(asdict(result)) + "\n")


def _format_percent(value: float) -> str:
    return f"{value:.1f}%"


def print_summary(console, summary: TraceSummary) -> None:
    table = Table(title="Full Trace Parser Summary")
    table.add_column("Metric", style="bold")
    table.add_column("Value")

    table.add_row("Parser", summary.parser)
    table.add_row("Runtime", summary.runtime)
    table.add_row("Packet directory", summary.packet_dir)
    table.add_row("Total packets", str(summary.total_packets))
    table.add_row("Accepted", f"{summary.accepted} ({_format_percent(summary.accepted_percent)})")
    table.add_row("Rejected", f"{summary.rejected} ({_format_percent(summary.rejected_percent)})")
    table.add_row("Elapsed", f"{summary.elapsed_seconds:.2f}s")
    table.add_row("Rate", f"{summary.packets_per_second:.2f} packets/s")
    table.add_row("Workers", str(summary.workers))
    console.print(table)

    if summary.errors:
        error_lines: list[str] = []
        for sample in summary.errors:
            error_lines.append(f"[bold]{sample.packet_path}[/bold]")
            error_lines.append(sample.error.strip() or "<no parser error output>")
            error_lines.append("")
        console.print(Panel("\n".join(error_lines).rstrip(), title="Rejected Packet Samples"))


def run_trace(args) -> TraceSummary:
    parser_path = Path(args.parser)
    packet_dir = Path(args.packet_dir)

    if not parser_path.is_file():
        raise FileNotFoundError(f"Parser file not found: {parser_path}")
    if args.packet_list is None and not packet_dir.is_dir():
        raise NotADirectoryError(f"Packet directory not found: {packet_dir}")
    if args.show_errors < 0:
        raise ValueError("--show-errors must be at least 0.")

    runtime_name = detect_runtime(parser_path, args.runtime)
    target = get_target_language(runtime_name)
    packet_paths = (
        read_packet_list(Path(args.packet_list))
        if args.packet_list is not None
        else discover_packet_paths(packet_dir, recursive=not args.no_recursive)
    )
    if not packet_paths:
        raise RuntimeError(f"No .bin packet files found in '{packet_dir}'.")

    worker_count = resolve_worker_count(args.workers, len(packet_paths))
    console = Console()

    with tempfile.TemporaryDirectory(prefix="trace_parser_build_") as build_dir:
        console.print(f"Precompiling [bold]{runtime_name}[/bold] parser...")
        artifact = target.create_runtime().build_parser(parser_path, Path(build_dir))
        start = time.perf_counter()
        completed = 0

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            TextColumn("{task.fields[rate]} packets/s"),
            console=console,
        ) as progress:
            task_id = progress.add_task(
                "Parsing packets",
                total=len(packet_paths),
                rate="0.00",
            )

            def update_progress(_result: PacketTraceResult) -> None:
                nonlocal completed
                completed += 1
                elapsed = max(time.perf_counter() - start, 1e-9)
                progress.update(task_id, advance=1, rate=f"{completed / elapsed:.2f}")

            results = parse_packets_parallel(
                packet_paths,
                runtime_name,
                artifact,
                worker_count,
                on_result=update_progress,
            )

        elapsed_seconds = time.perf_counter() - start
        if artifact.cleanup is not None:
            artifact.cleanup()

    summary = build_summary(
        parser_path=parser_path,
        runtime_name=runtime_name,
        packet_dir=packet_dir,
        results=results,
        elapsed_seconds=elapsed_seconds,
        workers=worker_count,
        show_errors=args.show_errors,
    )

    print_summary(console, summary)

    if args.json_out is not None:
        json_out = Path(args.json_out)
        write_json_summary(summary, json_out)
        console.print(f"Wrote JSON summary to [bold]{json_out}[/bold]")
    if args.results_jsonl is not None:
        results_out = Path(args.results_jsonl)
        write_packet_results(results, results_out)
        console.print(f"Wrote packet results to [bold]{results_out}[/bold]")

    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a generated Spicy or 3D parser over a directory of raw .bin packets."
    )
    parser.add_argument("parser", help="Path to the generated .spicy or .3d parser source.")
    parser.add_argument("packet_dir", help="Directory containing raw .bin packet files.")
    parser.add_argument(
        "--runtime",
        choices=("auto", "spicy", "3d"),
        default="auto",
        help="Parser runtime to use (default: auto from file extension).",
    )
    parser.add_argument(
        "-j",
        "--workers",
        type=int,
        default=None,
        help="Number of long-lived parser workers (default: CPU count, capped by packet count).",
    )
    parser.add_argument(
        "--no-recursive",
        action="store_true",
        help="Only parse .bin files directly inside packet_dir.",
    )
    parser.add_argument(
        "--show-errors",
        type=int,
        default=10,
        help="Number of rejected packet errors to show (default: 10).",
    )
    parser.add_argument(
        "--packet-list",
        type=Path,
        help="Text file listing exactly which packet paths to parse, one per line.",
    )
    parser.add_argument(
        "--results-jsonl",
        type=Path,
        help="Write every packet's accept/reject result as JSONL.",
    )
    parser.add_argument(
        "--json-out",
        default=None,
        help="Optional path for a machine-readable JSON summary.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    try:
        run_trace(args)
    except Exception as exc:
        try:
            Console(stderr=True).print(f"[bold red]Error:[/bold red] {exc}")
        except RuntimeError:
            print(f"Error: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
