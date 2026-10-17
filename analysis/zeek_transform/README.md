# Compare the Zeek grammar transformation

`compare.py` measures what changes when
`create_zeek_package.py` replaces Spicy `throw` and `&requires` checks with
`zeek::weird` calls. It uses the same transformation function as the package
builder, but generates a small, shared Zeek integration for both variants.
No model, metadata file, package installation, or existing Zeek module is needed.

Input is a directory of raw `.bin` payloads, such as
`../../data/packets/bgp` or output from `scripts/extract_layer.py`.
Subdirectories are scanned recursively.

```bash
.venv/bin/python analysis/zeek_transform/compare.py \
  ../../data/workspaces/bgp/gpt-5.4/20260724_105646/grammar/output.spicy \
  ../../data/packets/bgp --transport tcp \
  --output-dir tmp/zeek-transform-results/bgp
```

UDP is the default transport. The script wraps each payload in its own
synthetic flow, compiles the original and transformed grammar with `spicyz`,
and replays the same pcap through `zeek`. A TCP flow includes a handshake and
FIN so `&eod` fields can finish. `zeek` and `spicyz` must be on `PATH`.
Omit `--output-dir` to place the run under the repository's ignored `tmp/`
directory. An explicit output directory must be new or empty.

The terminal preview compares parsed packets, packets with analyzer violations,
violation events, logged weirds, and parse verdict changes. `summary.json`
contains totals, reason counts, and directory-level transitions. `packets.jsonl`
contains both outcomes for every payload. The output directory also retains
the exact grammar variants, compiled analyzers, captures, Zeek logs, and
compiler output.

Counts reflect this isolated replay. A parse event means the entry unit
completed; a violation is a raw `analyzer_violation_info` event, and a weird
is a `weird.log` row from this analyzer. A malformed packet can cause several
violation events. Synthetic flows do not preserve the original capture's
timing, fragmentation, retransmissions, or conversation context.
The generated integration uses the first public unit in the grammar, following
the package builder's entry-unit selection. That unit must have no arguments.

## Standalone Spicy comparison

The [trace comparison results](trace-comparison/RESULTS.md) run
`analysis/trace-eval/compare_traces.py` on the same original/transformed pairs
for Babel, BFD, BGP, RTSP, and RTP with `--skip-oracle`. This compares standalone
`spicy-dump` acceptance for each payload. The [experiment setup](trace-comparison/README.md)
explains the compatibility logger required by transformed grammars.
