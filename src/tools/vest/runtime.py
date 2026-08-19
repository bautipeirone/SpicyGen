from pathlib import Path
from typing import Callable
import tempfile
import shutil
import subprocess
import os
from src.logger import logger
from src.tools.runtime import RuntimeAdapter, ParseResult, RuntimeArtifact
from src.tools.vest.compiler import run_vest
from src.tools.vest.wrapper import write_main

VERUS_EXEC = Path(os.environ["VERUS_EXEC"])
VEST_LIB = Path(os.environ["VEST_LIB"])


def _create_build_dir(build_dir: Path | None) -> tuple[Path, Callable[[], None] | None]:
    if build_dir is not None:
        build_dir.mkdir(parents=True, exist_ok=True)
        return build_dir, None

    temp_build_dir = Path(tempfile.mkdtemp(prefix="vest_build_"))
    return temp_build_dir, lambda: shutil.rmtree(temp_build_dir, ignore_errors=True)


def prepare_vest_parser(grammar_file: Path, build_dir: Path | None = None) -> RuntimeArtifact:
    build_dir, cleanup = _create_build_dir(build_dir)

    try:
        main_path = build_dir / "main.rs"
        write_main(grammar_file, main_path)

        vest_result = run_vest(grammar_file, build_dir)
        if vest_result.returncode != 0:
            errors = vest_result.stderr.strip() if vest_result.stderr else vest_result.stdout.strip()
            raise RuntimeError(f"EverParse generation failed with error:\n{errors}")

        output_exec = build_dir / "parser"
        verus_result = subprocess.run(
            [
                str(VERUS_EXEC),
                "--import", f"vest_lib={VEST_LIB / "vest_lib.verusdata"}",
                "--extern", f"vest_lib={VEST_LIB / "libvest_lib.rlib"}",
                "--compile",
                str(main_path),
                "-o", output_exec
            ],
            capture_output=True,
            text=True
        )

        if verus_result.returncode != 0:
            errors = (
                verus_result.stderr.strip()
                if verus_result.stderr
                else verus_result.stdout.strip()
            )
            raise RuntimeError(f"verus verification and compilation failed with error:\n{errors}")
    except Exception:
        if cleanup is not None:
            cleanup()
        raise

    return RuntimeArtifact(path=output_exec, cleanup=cleanup)


def run_vest_parser(parser_file: Path, packet_file: Path, _parser_name: str | None = None) -> ParseResult:
    """Run a Vest parser and return the result."""
    logger.info(f"Running Vest parser: {parser_file} {packet_file.name}")
    result = subprocess.run(
        [str(parser_file), str(packet_file)],
        capture_output=True,
        text=True,
    )
    
    success = result.returncode == 0
    if success:
        logger.debug(f"Vest parser accepted {packet_file.name}")
    else:
        logger.debug(f"Vest parser rejected {packet_file.name}: {result.stderr.strip()}")

    error_output = ""
    if not success:
        error_output = "\n".join(
            chunk for chunk in [result.stderr.strip(), result.stdout.strip()] if chunk
        )

    return ParseResult(
        packet_path=str(packet_file),
        success=success,
        error=error_output,
    )


VEST_RUNTIME = RuntimeAdapter(
    parse_packet=run_vest_parser,
    prepare_parser=prepare_vest_parser,
)