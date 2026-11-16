#!/usr/bin/env python3
"""Apply the create_zeek_package.py grammar transformation with a print sink.

The Zeek package builder rewrites every ``throw "..."`` and ``&requires=(...)``
check into a ``zeek::weird`` call so that a malformed message is logged but
parsing continues. This script applies exactly that transformation
(``adapt_parser_to_zeek_analyzer``) and then swaps the ``zeek::weird`` sink for
a plain Spicy ``print`` statement and drops the ``import zeek;`` line, so the
result runs under standalone ``spicy-dump`` without a Zeek runtime.

Usage:
    python analysis/trace-eval/transform_to_prints.py INPUT.spicy OUTPUT.spicy
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from create_zeek_package import adapt_parser_to_zeek_analyzer  # noqa: E402

WEIRD_CALL = re.compile(r'zeek::weird\("(?P<message>(?:[^"\\]|\\.)*)"\);')


def transform_to_prints(code: str) -> str:
    adapted = adapt_parser_to_zeek_analyzer(code)
    rewritten, count = WEIRD_CALL.subn(lambda m: f'print "{m.group("message")}";', adapted)
    if "zeek::weird" in rewritten:
        raise RuntimeError("unconverted zeek::weird call left in grammar")
    lines = rewritten.splitlines()
    assert lines[2] == "import zeek;", lines[:3]
    del lines[1:3]
    return "\n".join(lines) + "\n", count


def main() -> int:
    source, destination = Path(sys.argv[1]), Path(sys.argv[2])
    code, count = transform_to_prints(source.read_text(encoding="utf-8"))
    destination.write_text(code, encoding="utf-8")
    print(f"{source} -> {destination}: {count} checks rewritten to print")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
