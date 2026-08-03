from pathlib import Path
from src.tools.runtime import RuntimeAdapter, ParseResult, RuntimeArtifact

def run_vest_parser(parser_file: Path, packet_file: Path, _parser_name: str | None = None) -> ParseResult:
    ...

def prepare_vest_parser(grammar_file: Path, build_dir: Path | None = None) -> RuntimeArtifact:
    ...

VEST_RUNTIME = RuntimeAdapter(
    parse_packet=run_vest_parser,
    prepare_parser=prepare_vest_parser,
)