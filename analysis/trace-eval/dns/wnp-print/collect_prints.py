#!/usr/bin/env python3
"""Run spicy-dump -P on every packet and record the print messages that fired.

Writes prints.jsonl (one row per packet: exit code and ordered print lines) and
prints a summary of first-print and all-print counts.
"""
from __future__ import annotations

import json
import subprocess
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

hlto, packet_list, out_path = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
MESSAGES = {
    "invalid domain name component", "invalid compression pointer offset",
    "invalid opcode", "reserved Z bits must be zero", "invalid qtype",
    "invalid qclass", "invalid rr type", "invalid rr class",
}
packets = [Path(p) for p in packet_list.read_text().split()]


def run(packet: Path) -> dict:
    with packet.open("rb") as handle:
        result = subprocess.run(
            ["spicy-dump", "-P", "--parser", "DNS_gpt::Message", str(hlto)],
            stdin=handle, capture_output=True, text=True,
        )
    prints = [line for line in result.stdout.splitlines() if line in MESSAGES]
    return {"packet_path": str(packet), "exit_code": result.returncode,
            "prints": prints, "stderr": result.stderr.strip()}


with ThreadPoolExecutor(12) as pool:
    rows = list(pool.map(run, packets))
with out_path.open("w") as handle:
    for row in rows:
        handle.write(json.dumps(row) + "\n")

first, every, per_packet = Counter(), Counter(), Counter()
for row in rows:
    if row["prints"]:
        first[row["prints"][0]] += 1
        per_packet[len(row["prints"])] += 1
    every.update(row["prints"])
print("packets:", len(rows), "nonzero exit:", sum(r["exit_code"] != 0 for r in rows))
print("packets with >=1 print:", sum(1 for r in rows if r["prints"]))
print("first print:", dict(first))
print("all prints:", dict(every))
print("prints per packet:", dict(sorted(per_packet.items())))
