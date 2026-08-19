import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Callable

from src.logger import logger
from src.tools._3d.compiler import run_everparse
from src.tools._3d.wrapper import write_error_handler, write_main
from src.tools.runtime import ParseResult, RuntimeAdapter, RuntimeArtifact


def _create_build_dir(build_dir: Path | None) -> tuple[Path, Callable[[], None] | None]:
    if build_dir is not None:
        build_dir.mkdir(parents=True, exist_ok=True)
        return build_dir, None

    temp_build_dir = Path(tempfile.mkdtemp(prefix="3d_build_"))
    return temp_build_dir, lambda: shutil.rmtree(temp_build_dir, ignore_errors=True)


def prepare_everparse_parser(grammar_file: Path, build_dir: Path | None = None) -> RuntimeArtifact:
    build_dir, cleanup = _create_build_dir(build_dir)
    output_exec = build_dir / "parser"

    try:
        logger.info(f"Generating EverParse sources in {build_dir}")
        result = run_everparse(str(grammar_file), build_dir=str(build_dir))
        if result.returncode != 0:
            errors = result.stderr.strip() if result.stderr else result.stdout.strip()
            raise RuntimeError(f"EverParse generation failed with error:\n{errors}")

        write_main(grammar_file, build_dir / "main.c")
        write_error_handler(grammar_file, build_dir / "error.c")

        c_files = sorted(build_dir.glob("*.c"))
        if not c_files:
            raise RuntimeError(f"EverParse did not generate any C files in '{build_dir}'.")
        logger.info(f"Compiling EverParse runtime with gcc: {output_exec}")
        compile_result = subprocess.run(
            ["gcc", "-o", output_exec, *[str(path) for path in c_files]],
            capture_output=True,
            text=True,
        )
        if compile_result.returncode != 0:
            errors = (
                compile_result.stderr.strip()
                if compile_result.stderr
                else compile_result.stdout.strip()
            )
            raise RuntimeError(f"gcc compilation failed with error:\n{errors}")
    except Exception:
        if cleanup is not None:
            cleanup()
        raise

    return RuntimeArtifact(path=output_exec, cleanup=cleanup)


def run_everparse_parser(
    parser_file: Path,
    packet_file: Path,
    parser_name: str | None = None,
) -> ParseResult:
    """Run an EverParse parser and return the result."""
    logger.info(f"Running EverParse parser: {parser_file} {packet_file.name}")

    result = subprocess.run(
        [str(parser_file), str(packet_file)],
        capture_output=True,
        text=True,
    )

    success = result.returncode == 0
    if success:
        logger.debug(f"EverParse parser accepted {packet_file.name}")
    else:
        logger.debug(f"EverParse parser rejected {packet_file.name}: {result.stderr.strip()}")

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


_3D_RUNTIME = RuntimeAdapter(
    parse_packet=run_everparse_parser,
    prepare_parser=prepare_everparse_parser
)
