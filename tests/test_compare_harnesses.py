from importlib import import_module


comparison = import_module(
    "analysis.harness-comparison.compare_harnesses"
)


def test_default_matrix_covers_both_harnesses_modes_and_six_protocols():
    args = comparison.parse_args(["--model", "gpt-test"])

    matrix = comparison.run_matrix(args)

    assert len(matrix) == 24
    assert {spec.harness for spec in matrix} == {"one-shot", "codex"}
    assert {spec.mode for spec in matrix} == {"full-context", "criteria-only"}
    assert {spec.protocol for spec in matrix} == set(comparison.DEFAULT_PROTOCOLS)
    assert {spec.model for spec in matrix} == {"gpt-test"}


def test_codex_command_receives_its_override_and_codex_binary(tmp_path):
    args = comparison.parse_args(
        [
            "--model",
            "shared-model",
            "--codex-model",
            "codex-model",
            "--codex-bin",
            "/opt/codex",
        ]
    )
    spec = next(spec for spec in comparison.run_matrix(args) if spec.harness == "codex")

    command = comparison.build_command(
        spec,
        args,
        grammar_file=tmp_path / "grammar.spicy",
        metrics_file=tmp_path / "metrics.json",
        debug_dir=tmp_path / "debug",
    )

    assert command[command.index("--model") + 1] == "codex-model"
    assert command[command.index("--codex-bin") + 1] == "/opt/codex"
    assert command[-1] == "http1.1"


def test_metrics_columns_report_packet_counts_and_rates():
    columns = comparison.metrics_columns(
        {
            "success": False,
            "positive_packets": 13,
            "negative_packets": 10,
            "time_elapsed_s": 12.34,
            "validation_feedback": "two packets failed",
            "iterations": [
                {
                    "compiled": True,
                    "positive_accepted": 12,
                    "negative_rejected": 9,
                }
            ],
        }
    )

    assert columns["compiled"] is True
    assert columns["positive_accepted"] == 12
    assert columns["positive_failed"] == 1
    assert columns["negative_rejected"] == 9
    assert columns["negative_failed"] == 1
    assert columns["correct_packets"] == 21
    assert columns["total_packets"] == 23
    assert columns["packet_accuracy"] == round(21 / 23, 6)
    assert columns["failure_summary"] == "two packets failed"


def test_usage_prefers_metrics_and_keeps_actual_usage_separate():
    columns = comparison.usage_columns(
        {
            "usage": {
                "total_usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 20,
                    "total_tokens": 120,
                    "total_cost": 0.5,
                },
                "actual_usage": {
                    "prompt_tokens": 80,
                    "completion_tokens": 20,
                    "total_tokens": 100,
                    "total_cost": 0.4,
                },
            }
        }
    )

    assert columns["total_tokens"] == 120
    assert columns["actual_total_tokens"] == 100
    assert columns["token_usage_source"] == "metrics"


def test_missing_usage_is_reported_as_unavailable():
    columns = comparison.usage_columns({})

    assert columns["total_tokens"] is None
    assert columns["actual_total_tokens"] is None
    assert columns["token_usage_source"] == "unavailable"


def test_artifact_stats_count_both_one_shot_prompt_files(tmp_path):
    grammar_file = tmp_path / "grammar.spicy"
    grammar_file.write_text("module Example;")
    debug_dir = tmp_path / "debug"
    debug_dir.mkdir()
    (debug_dir / "system-prompt.txt").write_text("abc")
    (debug_dir / "user-prompt.txt").write_text("defg")

    stats = comparison.artifact_stats(grammar_file, debug_dir)

    assert stats["prompt_chars"] == 7
    assert stats["grammar_chars"] == len("module Example;")
    assert len(stats["grammar_sha256"]) == 64
