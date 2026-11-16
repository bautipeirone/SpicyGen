# Zeek Integration

`create_zeek_package.py` turns a generated Spicy parser into a Zeek package.
The package scaffold is created by `zkg`. The Zeek-specific files are written
by an LLM and checked with `spicyz`, using the same generate-and-validate
approach as grammar generation.

```bash
python create_zeek_package.py <parser.spicy> --metadata <info.json> \
    -m <model> --output-dir <dir> [options]
```

| Option | Effect |
|---|---|
| `--metadata` | Protocol metadata JSON (see below). Required. |
| `-m, --model` | Model key from `model_index.json` used to write the Zeek files. Required. |
| `--output-dir` | Parent directory for the package. Required. |
| `-c, --config` | YAML whose `llm:` section sets the model parameters. |
| `--zeek-max-retries` | Maximum LLM attempts (default: 5). |
| `--skip-zeek-compile-check` | Skip `spicyz` and use the first response that parses as JSON. |
| `--skip-tcp-stream-unit` | Don't wrap the entry unit for TCP; parse a single message per connection direction. |
| `--traces-dir` | Directory of pcaps to turn into btest trace tests. |
| `--skip-installation` | Generate and build the package, but don't run `zkg install`. |
| `--name` | Package name (default: `spicy-<analyzer>`). |
| `--metrics-file`, `--debug-dir` | Write metrics JSON and `runtime.log`. |

Inside the pipeline this is the `zeek-package` step. It reads
`grammar/output.spicy` and `preprocess/info.json` from the workspace; see
[pipeline.md](pipeline.md).

## Requirements

`zkg`, `spicyc`, `spicyz`, `zeek`, `cmake`, `make` and `btest` must be on
`PATH`. `spicyz` is not needed with `--skip-zeek-compile-check`.

## Metadata

The metadata is the `info.json` written by `preprocess_spec.py`. Only
`transport` and `port` are read:

```json
{"type": "binary", "port": 53, "transport": ["tcp", "udp"], "layer": "application"}
```

Only `tcp` and `udp` are kept from `transport`. If neither is present, the
script stops with an error. A `port` of 0 or a missing `port` means the
detection signatures match on transport only.

## Steps

1. **Parse the parser.** `parse_spicy_file()` finds the module and its single
   public entry unit. The analyzer name is the module name with any model
   suffix removed (`DNS_gpt` → `DNS`, `IRC_gemini` → `IRC`).

2. **Add a TCP stream unit.** TCP delivers a byte stream, so when `tcp` is a
   transport a public list unit is appended that parses entry units until the
   end of the stream. For an entry unit `Message`, the stream unit is
   `Messages`, or `MessageStream` if `Messages` already exists.
   - The script first tries `(Message &synchronize)[]` with
     `on %synced { confirm; }`, so parsing can resume after a malformed
     message.
   - If `spicyc -p` rejects that (the unit has no literal or
     `%synchronize-at`/`%synchronize-after` to synchronize on), it falls back
     to `Message[] &eod`.
   - `--skip-tcp-stream-unit` skips this step.

3. **Scaffold the package.** The script runs
   `zkg create --features spicy-protocol-analyzer` without prompts. `zkg`
   accepts a single transport, so the first one listed is used. The stream unit
   is the parse unit when that transport is TCP.

4. **Adapt the parser for Zeek.** The parser is copied to
   `analyzer/<analyzer>.spicy` with these changes:
   - `import zeek;` is added;
   - `throw "..."` becomes `zeek::weird("...")`;
   - `&requires=(cond)` becomes a field hook that calls `zeek::weird()` when
     `cond` fails.

   As a result, a constraint violation is logged in `weird.log` and no longer
   aborts the analyzer.

5. **Generate the Zeek files with the LLM.** The system prompt is
   `prompts/zeek/INTEGRATION.md`. The user message contains the parser source,
   module, analyzer, entry unit, transports and stream unit. The model returns
   JSON with three keys:

   | Key | File | Contents |
   |---|---|---|
   | `evt` | `analyzer/<analyzer>.evt` | `protocol analyzer <Analyzer>_<TRANSPORT>` declarations and unit → event mappings. |
   | `zeek_spicy` | `analyzer/zeek_<analyzer>.spicy` | The `Zeek_<Analyzer>` module with helper functions for `.evt`. |
   | `main_zeek` | `scripts/main.zeek` | Event declarations, an `Info` log record and handlers that write `<analyzer>.log`. |

   Each attempt is checked in three stages, and the error from the first stage
   that fails is sent back to the model:
   1. The response must contain a JSON object with a non-empty string for
      each of the three keys. Markdown fences are stripped first.
   2. When a TCP stream unit exists, the `.evt` must use it as the parse unit of
      the TCP analyzer and nowhere else. Events must be attached to the entry
      unit, not the stream unit.
   3. `spicyz` compiles the parser, `zeek_<analyzer>.spicy` and the `.evt`
      together.

   If the attempts run out, the last file set that parsed as JSON is used and a
   warning is logged. If the final response is not valid JSON, the script
   fails.

6. **Write the files and build.** The script writes the three files, then
   `scripts/dpd.sig` with one signature per transport (`ip-proto` and, if
   known, `dst-port`). It updates `README` and the `summary`/`description` in
   `zkg.meta`, and builds with `cmake .. && make` in `build/`.

7. **Set up tests.** The template's `standalone.spicy` and `trace.zeek` tests
   are removed. With `--traces-dir`, each pcap is copied to `testing/Traces/`
   and gets a `tests/<trace>.zeek` btest that runs Zeek on it and compares the
   output and `<analyzer>.log`. Baselines are then created with
   `btest -U`. These baselines record whatever the analyzer produced; they are
   not checked against a reference.

8. **Install.** Unless `--skip-installation` is passed, uncommitted changes in
   the package are committed and the package is installed with
   `zkg --user install . --force`.

## Generated package

```
spicy-dns/
├── CMakeLists.txt
├── zkg.meta
├── README
├── analyzer/
│   ├── CMakeLists.txt
│   ├── dns.spicy          # adapted parser (+ TCP stream unit)
│   ├── dns.evt            # LLM-generated
│   └── zeek_dns.spicy     # LLM-generated
├── scripts/
│   ├── __load__.zeek
│   ├── main.zeek          # LLM-generated
│   └── dpd.sig            # detection signatures
├── testing/               # btest suite, Traces/ and Baseline/
└── build/
```

File names use the lowercase analyzer name, which is what `zkg` and the
generated `CMakeLists.txt` expect.

## Metrics

```json
{
  "model": "gemini-3.1-flash",
  "package_name": "spicy-dns",
  "package_dir": "zeek-packages/spicy-dns",
  "spicy_module": "DNS_gpt",
  "entry_unit": "Message",
  "analyzer_name": "DNS",
  "transport_candidates": ["tcp", "udp"],
  "port": 53,
  "tcp_stream_unit": "Messages",
  "tcp_stream_unit_synchronize": true,
  "time_elapsed_s": 117.3,
  "usage": {"attempts": [{"attempt": 1, "usage": {...}}]}
}
```

## Limitations

- Test events are hard-coded to `<Analyzer>::message`, so trace tests assume
  the generated `main.zeek` defines that event.
- The return codes of `cmake`, `make`, `btest` and `zkg install` are not
  checked, so the script can finish even if the build fails. Check the logs.
- The package directory must not exist yet; the script will not overwrite an
  existing one.
