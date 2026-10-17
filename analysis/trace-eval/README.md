# Trace comparison

`compare_traces.py` compares one or more standalone packet parsers against a
Wireshark oracle. It works with any protocol supported by `extract_layer.py`
and `wireshark_oracle.py`. Run commands from the `code/grammar-generator`
project directory using its Python environment.

First extract payloads. The extractor writes `manifest.jsonl`, which maps every
`.bin` file to its capture, one-based frame number, and zero-based unit index.
Keep this manifest with the packet files; the comparison checks each payload's
SHA-256 hash and runs only the files listed in the manifest. RTSP interleaved
`$` frames are written one unit per file, including when several units share a
TCP frame. For RTP extraction, complete RTP payloads inside RTSP interleaved
units are also written separately, without the four-byte RTSP framing header.

```bash
.venv/bin/python scripts/extract_layer.py CAPTURE_OR_DIRECTORY \
  --protocol PROTOCOL --out PACKET_DIRECTORY

.venv/bin/python analysis/trace-eval/compare_traces.py \
  CAPTURE_OR_DIRECTORY PACKET_DIRECTORY PROTOCOL \
  --target reference=path/to/reference.spicy \
  --target candidate=path/to/candidate.3d \
  --output-dir REPORT_DIRECTORY
```

The oracle runs once for each capture by default. `PROTOCOL` is an oracle
profile name or Wireshark protocol abbreviation. Its selection can be changed
with `--oracle-filter 'dns || mdns'`, repeated `--oracle-decode-as` and
`--oracle-soft-reject-filter` flags, or `--oracle-profile-config profiles.json`
using the format documented in `scripts/wireshark_oracle.py`. These options are
protocol-neutral; the DNS filter is only an example. Use
`--oracle-report-dir EXISTING_DIRECTORY` to reuse `CAPTURE_FILENAME.json`
reports, or `--skip-oracle` for target-only runs. `--workers N` controls the
number of workers used for each target.

The output directory contains:

- `comparison.csv`: one row per extracted unit or oracle-only frame,
  with capture, frame, unit index, oracle verdict, each target's result, and errors.
- `summary.json`: coverage, verdict totals, per-capture counts, per-target
  cross counts, settings, and target run summaries.
- `oracle/`: individual Wireshark reports when the oracle is run.
- `targets/`: full packet-level target results and each runner's summary.
- `packet-list.txt`: exact files passed to every target.

`ACCEPT`, `STRICT_REJECT`, and `SOFT_REJECT` remain separate. A soft rejection
can come from an enclosing network layer, while these target parsers receive
only the extracted payload. An oracle-only frame and a payload without an
oracle frame are coverage gaps, not parser rejections. Oracle verdicts remain
frame-level: when a frame contains several extracted units, the verdict is
associated with each unit, while coverage counts the frame once. The extractor
separates complete RTSP interleaved units within a TCP frame but does not
reassemble units split across TCP frames. RTP detection uses packet structure;
it can select valid RTP from flows that Wireshark leaves labeled as generic
RTSP data when the signaling context is absent from the capture.

## Full Zeek module replay

`evaluate_zeek_modules.py` replays complete captures through the modules in
`../zeek-integration/spicy-{babel,bfd,dhcp,dns,http,rtsp,rtp}`. It compiles
the selected module with the installed `spicyz`, then starts a fresh Zeek
process and log directory for each capture. Give it a new, empty output
directory for each run.

```bash
.venv/bin/python analysis/trace-eval/evaluate_zeek_modules.py dns \
  /path/to/capture.pcap --output-dir /path/to/dns-evaluation
```

Use `--port PORT` when the trace uses a nonstandard server port. Use
`--no-checksums` only when the trace has invalid or offloaded checksums and
you want Zeek's `-C` behavior. For example, the bundled RTP H.263 capture
needs both `--port 32976` and `--no-checksums` to activate the analyzer and
deliver its packets to it.

The report in `summary.json` provides these counts per capture and in total:

- `parsed_messages`: events emitted at the selected top-level unit. For RTP,
  it is the sum of `rtp_packet` and `rtcp_packet`; for RTSP, it counts
  `RTSP::message`, not the session-level `rtsp.log` rows.
- `violations`: raw `analyzer_violation_info` events from the selected
  analyzer, with `violation_reasons`. One parse failure can generate both a
  module rejection and a Spicy parser violation, so this is not a count of
  distinct malformed packets.
- `logged_weirds`: rows in `weird.log` whose `source` equals the selected
  analyzer, with `logged_weirds_by_name`. This is a count of log records,
  not distinct pcap frames. `all_logged_weirds` includes unrelated sources.

The report keeps Zeek's stdout, stderr, JSON logs, monitor script, and the
freshly compiled `.hlto` for inspection. A zero parsed count prompts an
activation warning; check the ports and checksum setting before interpreting
it as rejection. This module-level replay complements the extracted-payload
comparison above; it does not perform frame-level oracle matching.
