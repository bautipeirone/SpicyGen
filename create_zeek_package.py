#!/usr/bin/env python3
"""
create_zeek_package.py — Create a Zeek integration package from a Spicy parser.

Takes a generated .spicy parser file and a JSON metadata file, and produces
a complete Zeek package scaffold using `zkg create` with skeleton .evt and
.zeek files ready for customization.

Usage:
    python create_zeek_package.py <spicy_file> --metadata <json> --output-dir <dir>

Example:
    python create_zeek_package.py outputs/stable/dns-gpt-5.4.spicy \
        --metadata metadata/dns.json \
        --output-dir ./zeek-packages
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from src.agent import query_llm_once

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class ProtocolMetadata:
    """Protocol metadata needed for Zeek package creation.

    Loaded from a JSON file.  The structure is intentionally minimal;
    additional fields can be added as the pipeline evolves.
    """
    transport: str  # "tcp" or "udp"
    port: int       # well-known port number (0 means placeholder / unknown)

    @classmethod
    def from_json(cls, path: Path) -> ProtocolMetadata:
        with open(path) as f:
            data = json.load(f)
        transport = data.get("transport", "").lower()
        if transport not in ("tcp", "udp"):
            raise ValueError(
                f"Invalid transport '{data.get('transport')}' in {path}. "
                "Must be 'tcp' or 'udp'."
            )
        return cls(transport=transport, port=int(data.get("port", 0)))


@dataclass
class SpicyParserInfo:
    """Information extracted from a Spicy parser file."""
    module_name: str   # e.g. "DNS_gpt"
    entry_unit: str    # e.g. "Message"
    source_path: Path  # absolute path to the .spicy file


# ---------------------------------------------------------------------------
# Spicy file parsing
# ---------------------------------------------------------------------------

# Known LLM model name tokens that may appear as a suffix in module names.
_MODEL_TOKENS = {"gpt", "gemini", "qwen", "claude"}


def parse_spicy_file(path: Path) -> SpicyParserInfo:
    """Extract the module name and public entry-point unit from a .spicy file."""
    content = path.read_text()

    module_match = re.search(r"^module\s+(\w+)\s*;", content, re.MULTILINE)
    if not module_match:
        raise ValueError(f"No 'module' declaration found in {path}")

    unit_match = re.search(
        r"^public\s+type\s+(\w+)\s*=\s*unit", content, re.MULTILINE
    )
    if not unit_match:
        raise ValueError(f"No 'public type ... = unit' found in {path}")

    return SpicyParserInfo(
        module_name=module_match.group(1),
        entry_unit=unit_match.group(1),
        source_path=path.resolve(),
    )


def derive_analyzer_name(module_name: str) -> str:
    """Derive a clean analyzer name by stripping the LLM model suffix.

    Examples
    --------
    >>> derive_analyzer_name("DNS_gpt")
    'DNS'
    >>> derive_analyzer_name("IRC_gemini")
    'IRC'
    >>> derive_analyzer_name("NTPv4_gpt")
    'NTPv4'
    >>> derive_analyzer_name("VXLAN")
    'VXLAN'
    """
    parts = module_name.split("_")
    if len(parts) > 1 and parts[-1].lower() in _MODEL_TOKENS:
        return "_".join(parts[:-1])
    return module_name


# ---------------------------------------------------------------------------
# zkg invocation
# ---------------------------------------------------------------------------

def run_zkg_create(
    package_dir: Path,
    package_name: str,
    analyzer_name: str,
    transport: str,
    entry_unit: str,
) -> None:
    """Run ``zkg create`` non-interactively to scaffold the package."""
    if shutil.which("zkg") is None:
        raise RuntimeError(
            "'zkg' not found on PATH. "
            "Install the Zeek Package Manager (https://docs.zeek.org/projects/package-manager/)."
        )

    cmd = [
        "zkg", "--user", "create",
        "--packagedir", str(package_dir),
        "--features", "spicy-protocol-analyzer",
        "--force",
        "--user-var", f"name={package_name}",
        "--user-var", f"analyzer={analyzer_name}",
        "--user-var", f"protocol={transport.upper()}",
        "--user-var", f"unit_orig={entry_unit}",
        "--user-var", f"unit_resp={entry_unit}",
    ]

    print(f"  Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"zkg create failed (exit {result.returncode}):\n"
            f"stdout: {result.stdout}\n"
            f"stderr: {result.stderr}"
        )


# ---------------------------------------------------------------------------
# File generation  (skeleton / TODO templates)
# ---------------------------------------------------------------------------

def generate_evt(
    module_name: str,
    analyzer_name: str,
    entry_unit: str,
    transport: str,
    port: int,
) -> str:
    """Generate skeleton ``.evt`` file content."""
    t_lower = transport.lower()
    a_lower = analyzer_name.lower()
    # The Zeek_* module is generated in zeek_<analyzer>.spicy and provides
    # protocol confirmation/rejection hooks.
    zeek_module = f"Zeek_{analyzer_name}"

    return (
        f"import {module_name};\n"
        f"import {zeek_module};\n"
        f"\n"
        f"# TODO: Adjust below how/when the analyzer will be activated.\n"
        f"# For more information, see:\n"
        f"#   https://docs.zeek.org/projects/spicy/en/latest/zeek.html#interface-definitions-evt-files\n"
        f"#\n"
        f"protocol analyzer {analyzer_name} over {transport.upper()}:\n"
        f"    parse originator with {module_name}::{entry_unit},\n"
        f"    parse responder with {module_name}::{entry_unit},\n"
        f"    port {port}/{t_lower};\n"
        f"\n"
        f"# TODO: Define events for specific parsed units/fields.\n"
        f"# Example:\n"
        f"#   on {module_name}::{entry_unit} -> event {a_lower}::request($conn, $is_orig);\n"
        f"on {module_name}::{entry_unit} -> event {a_lower}::message($conn, $is_orig);\n"
    )


def generate_zeek_spicy(analyzer_name: str, module_name: str, entry_unit: str) -> str:
    """Generate ``zeek_<analyzer>.spicy`` with protocol confirmation/rejection."""
    zeek_module = f"Zeek_{analyzer_name}"

    return (
        f"# Set up protocol confirmation/rejection for analyzers, as well as any further\n"
        f"# Zeek-specific analysis.\n"
        f"\n"
        f"module {zeek_module};\n"
        f"\n"
        f"import {module_name};\n"
        f"import zeek;\n"
        f"\n"
        f"# TODO: Protocol analyzers should confirm once they are reasonably sure that\n"
        f"# they are indeed parsing the right protocol. Pick a unit that's a little bit\n"
        f"# into the parsing process here.\n"
        f"#\n"
        f"# on {module_name}::SUITABLE_UNIT::%done {{\n"
        f"#     zeek::confirm_protocol();\n"
        f"# }}\n"
        f"\n"
        f"# Any error bubbling up to the top unit will trigger a protocol rejection.\n"
        f"on {module_name}::{entry_unit}::%error {{\n"
        f"    zeek::reject_protocol(\"error while parsing {analyzer_name}\");\n"
        f"}}\n"
    )


def generate_main_zeek(analyzer_name: str) -> str:
    """Generate skeleton ``main.zeek`` script content."""
    a_lower = analyzer_name.lower()

    return (
        f"module {analyzer_name};\n"
        f"\n"
        f"export {{\n"
        f"    ## Log stream identifier.\n"
        f"    redef enum Log::ID += {{ LOG }};\n"
        f"\n"
        f"    ## Record type for the log.\n"
        f"    type Info: record {{\n"
        f"        ## Timestamp\n"
        f"        ts:   time    &log;\n"
        f"        ## Connection UID\n"
        f"        uid:  string  &log;\n"
        f"        ## Connection ID (4-tuple)\n"
        f"        id:   conn_id &log;\n"
        f"        # TODO: Add protocol-specific fields here.\n"
        f"    }};\n"
        f"}}\n"
        f"\n"
        f"event zeek_init() &priority=5 {{\n"
        f"    Log::create_stream({analyzer_name}::LOG,\n"
        f"        [$columns=Info, $path=\"{a_lower}\"]);\n"
        f"}}\n"
        f"\n"
        f"# TODO: Implement event handler to populate log record.\n"
        f"# event {a_lower}::message(c: connection, is_orig: bool) {{\n"
        f"#     local rec = Info($ts=network_time(), $uid=c$uid, $id=c$id);\n"
        f"#     Log::write({analyzer_name}::LOG, rec);\n"
        f"# }}\n"
    )


def generate_readme(package_name: str, analyzer_name: str) -> str:
    """Generate ``README`` content."""
    return (
        f"# {package_name}\n"
        f"\n"
        f"Auto-generated Zeek package for the **{analyzer_name}** protocol "
        f"using a Spicy parser.\n"
    )


def update_zkg_meta(meta_path: Path, analyzer_name: str) -> None:
    """Patch the ``description`` field in ``zkg.meta``."""
    content = meta_path.read_text()
    content = re.sub(
        r"^summary\s*=.*$",
        f"summary = {analyzer_name} protocol plugin (auto-generated)",
        content,
        flags=re.MULTILINE)
    content = re.sub(
        r"^description\s*=.*$",
        f"description = Spicy-based Zeek analyzer for the {analyzer_name} protocol (auto-generated)",
        content,
        flags=re.MULTILINE,
    )
    meta_path.write_text(content)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a Zeek integration package from a Spicy parser.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Example:\n"
            "  python create_zeek_package.py outputs/stable/dns-gpt-5.4.spicy \\\n"
            "      --metadata metadata/dns.json --output-dir ./zeek-packages\n"
        ),
    )
    parser.add_argument(
        "spicy_file",
        type=Path,
        help="Path to the generated .spicy parser file",
    )
    parser.add_argument(
        "--metadata",
        type=Path,
        required=True,
        help="Path to JSON file with protocol metadata (transport, port)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Directory where the package will be created",
    )
    parser.add_argument(
        "--name",
        type=str,
        default=None,
        help="Package name override (default: spicy-<analyzer_lower>)",
    )
    return parser.parse_args()


def create_zeek_package(
    spicy_file: Path,
    metadata_file: Path,
    output_dir: Path,
    name_override: str | None = None,
) -> Path:
    """Create a Zeek package from a Spicy parser and metadata.

    Parameters
    ----------
    spicy_file : Path
        Path to the ``.spicy`` parser file.
    metadata_file : Path
        Path to the JSON metadata file.
    output_dir : Path
        Parent directory where the package folder will be created.
    name_override : str, optional
        Override for the package name.

    Returns
    -------
    Path
        The path to the created package directory.
    """
    # ── Phase 1: Parse the Spicy file ──────────────────────────────────
    spicy_info = parse_spicy_file(spicy_file)
    analyzer_name = derive_analyzer_name(spicy_info.module_name)
    metadata = ProtocolMetadata.from_json(metadata_file)

    package_name = name_override or f"spicy-{analyzer_name.lower()}"
    package_dir = output_dir / package_name

    if package_dir.exists():
        raise FileExistsError(f"Package directory already exists: {package_dir}")

    print(f"Creating Zeek package '{package_name}'")
    print(f"  Module:    {spicy_info.module_name}")
    print(f"  Unit:      {spicy_info.entry_unit}")
    print(f"  Analyzer:  {analyzer_name}")
    print(f"  Transport: {metadata.transport.upper()}")
    print(f"  Port:      {metadata.port}")

    # ── Phase 2: Scaffold with zkg ─────────────────────────────────────
    output_dir.mkdir(parents=True, exist_ok=True)
    run_zkg_create(
        package_dir, package_name, analyzer_name,
        metadata.transport, spicy_info.entry_unit,
    )

    # zkg creates files using lowercase analyzer name (e.g. dns.spicy, dns.evt,
    # zeek_dns.spicy) and CMakeLists.txt references those lowercase names.
    a_lower = analyzer_name.lower()

    # ── Phase 3: Copy the user's Spicy parser ─────────────────────────
    # Overwrite the template placeholder with the real parser.
    analyzer_spicy = package_dir / "analyzer" / f"{a_lower}.spicy"
    shutil.copy2(spicy_info.source_path, analyzer_spicy)
    print(f"  Copied parser => {analyzer_spicy.relative_to(output_dir)}")

    # ── Phase 4: Generate skeleton integration files ───────────────────
    evt_path = package_dir / "analyzer" / f"{a_lower}.evt"
    evt_path.write_text(generate_evt(
        spicy_info.module_name, analyzer_name, spicy_info.entry_unit,
        metadata.transport, metadata.port,
    ))

    zeek_spicy_path = package_dir / "analyzer" / f"zeek_{a_lower}.spicy"
    zeek_spicy_path.write_text(generate_zeek_spicy(
        analyzer_name, spicy_info.module_name, spicy_info.entry_unit,
    ))

    main_zeek_path = package_dir / "scripts" / "main.zeek"
    main_zeek_path.write_text(generate_main_zeek(analyzer_name))

    # ── Phase 5: Update metadata ───────────────────────────────────────
    readme_path = package_dir / "README"
    readme_path.write_text(generate_readme(package_name, analyzer_name))

    zkg_meta_path = package_dir / "zkg.meta"
    if zkg_meta_path.exists():
        update_zkg_meta(zkg_meta_path, analyzer_name)

    print(f"\n✓ Package created at: {package_dir}")

    return package_dir


def main() -> None:
    args = parse_args()

    if not args.spicy_file.exists():
        print(f"Error: Spicy file not found: {args.spicy_file}", file=sys.stderr)
        sys.exit(1)
    if not args.metadata.exists():
        print(f"Error: Metadata file not found: {args.metadata}", file=sys.stderr)
        sys.exit(1)

    try:
        create_zeek_package(
            spicy_file=args.spicy_file,
            metadata_file=args.metadata,
            output_dir=args.output_dir,
            name_override=args.name,
        )
    except (ValueError, RuntimeError, FileExistsError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
