"""End-to-end checks for the original/adapted Zeek comparison."""

import json
import shutil

import pytest

from analysis.zeek_transform.compare import compare, preview


@pytest.mark.parametrize("transport", ["udp", "tcp"])
def test_throw_and_requires_become_weirds_without_violations(tmp_path, transport, capsys):
    if not shutil.which("zeek") or not shutil.which("spicyz"):
        pytest.skip("Zeek and spicyz are required")

    grammar = tmp_path / "probe.spicy"
    grammar.write_text('''module Probe;

public type Packet = unit {
    value: uint8 &requires=($$ != 2);
    on %done {
        if (self.value == 3)
            throw "value was three";
    }
};
''')
    packets = tmp_path / "packets"
    packets.mkdir()
    for value in (1, 2, 3):
        (packets / f"{value}.bin").write_bytes(bytes([value]))

    output = tmp_path / "comparison"
    summary = compare(grammar, packets, output, transport)
    rows = [json.loads(line) for line in (output / "packets.jsonl").read_text().splitlines()]

    assert summary["variants"]["original"]["parsed_packets"] == 1
    assert summary["variants"]["original"]["analyzer_violations"] == 2
    assert summary["variants"]["transformed"]["parsed_packets"] == 3
    assert summary["variants"]["transformed"]["analyzer_violations"] == 0
    assert summary["variants"]["transformed"]["logged_weirds"] == 2
    assert summary["transitions"]["unparsed -> parsed"] == 2
    assert summary["checks_rewritten"] is True
    assert [row["transformed"]["weirds"] for row in rows] == [
        [], ["Constraint ($$ != 2) violated in field value"], ["value was three"]
    ]
    preview(summary, output)
    display = capsys.readouterr().out
    assert "unparsed -> parsed: 2" in display
    assert "throw/require checks rewritten: True" in display
