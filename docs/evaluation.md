# Walkthrough: Evaluation Runner

## Files Created

### [run_eval.py](file:///home/bautistapeirone/Desktop/spec-extract/code/grammar-generator/run_eval.py)
Python evaluation runner (~450 lines) that replaces `benchmark.sh` for structured, resumable evaluation runs.

### [evals/example.yaml](file:///home/bautistapeirone/Desktop/spec-extract/code/grammar-generator/evals/example.yaml)
Example evaluation config demonstrating all available options.

---

## How It Works

### YAML-Driven Matrix
The runner reads an eval YAML and expands a cross-product matrix of `(protocol × model × config × target_language)`. Each protocol can override the global defaults:

```yaml
defaults:
  models: ["gemini-3.5-flash", "qwen3-coder"]
  configs: ["configs/default.yaml"]

protocols:
  dhcp:
    spec_file: "specs/dhcp-summary.txt"   # custom spec
  dns: {}                                  # use defaults
  http1.1:
    models: ["gpt-5.4"]                   # override models
    no_spec: true                          # no spec
```

### Output Structure
Each session gets an isolated timestamped directory:

```
outputs/20260605_150423_binary-protocols-comparison/
├── eval_config.yaml      # copy of input YAML
├── eval_state.json       # run status tracking
├── report.jsonl          # aggregated metrics
├── summary.md            # comparison tables
├── README.md
└── runs/
    ├── dhcp__gemini-3.5-flash__default__spicy/
    │   ├── output.spicy
    │   ├── metrics.json
    │   └── debug/        # if --debug
    └── ...
```

### Resume & Retry

`eval_state.json` tracks every run as `pending → running → completed|failed`.
For a completed run, `success` records the grammar validation result from the
run's `metrics.json`. This gives three terminal outcomes:

- `status: completed, success: true` — generation completed with a valid grammar
- `status: completed, success: false` — generation completed but exhausted its
  iterations without a valid grammar
- `status: failed, success: false` — the generation process failed (for example,
  due to a timeout, network error, or malformed/missing metrics)

The resume modes use `status` to select runs:

- `--resume --eval-dir <path>`: skips completed, runs pending + failed
- `--retry-failed --eval-dir <path>`: re-runs only failed runs

### Parallelism
`--parallelism N` runs N jobs concurrently via `ThreadPoolExecutor`. Default is 1 (sequential). State is saved after each run completes, so interrupting mid-evaluation is safe for resume.

### Summary
`summary.md` includes:
- **Overview table** — total/completed/failed/success counts
- **Results table** — per-run details (success, iterations, compile@1, time, cost)
- **Per-Model statistics** — aggregate success rate, avg time, avg iterations, total cost
- **Per-Protocol statistics** — aggregate success rate, avg time, avg iterations
- **Failed runs** — error details for quick triage

## Verified

- `--dry-run` correctly expands the matrix (7 runs from example.yaml)
- `--help` shows all options with examples
- No modifications to existing files
