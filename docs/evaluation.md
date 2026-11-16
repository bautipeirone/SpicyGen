# Evaluation Runner

`run_eval.py` runs `generate_grammar.py` over a matrix of protocols, models,
configs, target languages and repeated samples. It can resume an interrupted
session and summarizes results with pass@k. It replaces `benchmark.sh`.

```bash
python run_eval.py <eval.yaml> [--output-dir outputs] [--parallelism N] [--debug] [--dry-run]
python run_eval.py <eval.yaml> --resume       --eval-dir outputs/<session>
python run_eval.py <eval.yaml> --retry-failed --eval-dir outputs/<session>
```

## Configuration

An evaluation is a YAML file, usually in `evals/`. The `defaults` apply to
every protocol, and each protocol can override any of them.

```yaml
name: "binary-protocols-comparison"   # used in the output directory name
note: "Gemini vs Qwen on core binary protocols"

defaults:
  models: ["gemini-3.5-flash", "qwen3-coder"]   # required here or per protocol
  configs: ["configs/default.yaml"]             # one or more generation configs
  target_languages: ["spicy"]                   # default: ["spicy"]
  samples: 3                                    # repetitions per combination (default: 1)
  no_spec: false                                # generate without a specification
  # spec_file: "specs/custom.txt"               # replace the indexed specification
  # instructions_file: "prompts/spicy/NO_DOCS.md"  # replace the language manual

protocols:
  dns: {}                                       # defaults only
  dhcp:
    spec_file: "specs/dhcp-summary.txt"
  tftp:
    no_spec: true
  http1.1:
    models: ["gpt-5.4"]
```

The matrix is every combination of protocol × model × config × target
language × sample. `--dry-run` prints it without running anything.

Each run calls `generate_grammar.py` with `-m`, `-t`, `-o`, `--metrics-file`,
and `-c`, `-i`, `--spec-file` or `--no-spec` as configured. `--no-spec` takes
precedence over `spec_file`. `--debug` adds `--debug-dir` to every run. Runs
time out after one hour.

Configs specific to ablation studies are in `configs/evals/`. The evaluation
files in `evals/` are the ones used for the reported experiments, for example
`tool-ablation.yaml`, `preprocessing-ablation.yaml`,
`negative-packets-ablation.yaml` and `spicy-no-docs-ablation.yaml`.

## Output

```
outputs/<YYYYMMDD_HHMMSS>_<name>/
├── eval_config.yaml      # copy of the input YAML
├── eval_state.json       # per-run status, used by --resume and --retry-failed
├── report.jsonl          # every run's metrics.json, tagged with run_id
├── summary.md            # tables (see below)
├── README.md             # session description and run list
└── runs/
    └── <protocol>__<model>__<config>__<language>[__s<n>]/
        ├── output.<ext>
        ├── metrics.json
        ├── stdout.txt / stderr.txt
        └── debug/        # with --debug
```

The `__s<n>` suffix is added only when `samples > 1`. `<config>` is the config
file name without its extension.

## Run outcomes

Each run in `eval_state.json` moves through
`pending → running → completed | failed`. Combined with `success`, there are
three possible outcomes:

| `status` | `success` | Meaning |
|---|---|---|
| `completed` | `true` | The grammar passed validation. |
| `completed` | `false` | Generation finished but used up its attempts without a valid grammar. |
| `failed` | `false` | Generation did not finish: non-zero exit, timeout, exception in the agent loop, or a missing or unreadable `metrics.json`. |

Before each run, any existing `metrics.json` is deleted, so a retried run that
writes no metrics cannot pick up an older run's result.

- `--resume` skips `completed` runs and runs everything else.
- `--retry-failed` runs only `failed` runs.

State is saved after every run, so you can interrupt a session and resume it
safely. With `--parallelism N`, N runs execute at once in a thread pool.

## Summary

`summary.md` contains:

- **Overview**: total, completed, failed and successful runs.
- **Results**: one row per run with success, iterations, best iteration, first
  compiling iteration, time and cost.
- **Aggregated results** (when `samples > 1`): per configuration, the success
  count and the unbiased pass@k estimator
  `1 − C(n−c, k) / C(n, k)`, with averages over samples.
- **Per model** and **per protocol**: success rate, average time and
  iterations, and total cost per model.
- **Failed runs**: the error for each `failed` run.

Cost is read from `usage.total_usage.total_cost` in each run's metrics. It
depends on the `price` set in `model_index.json`.
