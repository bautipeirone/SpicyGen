import json
from types import SimpleNamespace

import run_eval


def _run_spec(model: str = "test-model") -> run_eval.RunSpec:
    return run_eval.RunSpec(
        protocol="dns",
        model=model,
        config=None,
        target_language="spicy",
        spec_file=None,
        no_spec=False,
    )


def _mock_completed_process(monkeypatch, *, grammar_success: bool, termination_reason: str, returncode: int = 0):
    def fake_run(cmd, **_kwargs):
        metrics_path = cmd[cmd.index("--metrics-file") + 1]
        with open(metrics_path, "w") as metrics_file:
            json.dump({"success": grammar_success, "termination_reason": termination_reason}, metrics_file)
        return SimpleNamespace(returncode=returncode, stdout="", stderr="")

    monkeypatch.setattr(run_eval.subprocess, "run", fake_run)


def test_execute_run_reads_successful_grammar_outcome(tmp_path, monkeypatch):
    _mock_completed_process(monkeypatch, grammar_success=True, termination_reason="success")

    success, error = run_eval.execute_run(_run_spec(), tmp_path, debug=False)

    assert success is True
    assert error is None


def test_execute_run_treats_incorrect_grammar_as_completed(tmp_path, monkeypatch):
    _mock_completed_process(monkeypatch, grammar_success=False, termination_reason="max_retries")

    success, error = run_eval.execute_run(_run_spec(), tmp_path, debug=False)

    assert success is False
    assert error is None


def test_execute_run_treats_nonzero_exit_as_runtime_failure(tmp_path, monkeypatch):
    _mock_completed_process(monkeypatch, grammar_success=False, termination_reason="runtime_exception", returncode=1)

    success, error = run_eval.execute_run(_run_spec(), tmp_path, debug=False)

    assert success is False
    assert error == "Exit code 1"


def test_execute_run_requires_metrics_from_completed_process(tmp_path, monkeypatch):
    (tmp_path / "metrics.json").write_text('{"success": true}')
    monkeypatch.setattr(
        run_eval.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )

    success, error = run_eval.execute_run(_run_spec(), tmp_path, debug=False)

    assert success is False
    assert error == "Grammar generation completed without writing metrics.json"


def test_eval_state_distinguishes_all_three_run_outcomes(tmp_path, monkeypatch):
    runs = [_run_spec("success"), _run_spec("incorrect"), _run_spec("runtime-error")]
    outcomes = {
        "success": (True, None),
        "incorrect": (False, None),
        "runtime-error": (False, "network error"),
    }

    monkeypatch.setattr(
        run_eval,
        "execute_run",
        lambda run, run_dir, debug: outcomes[run.model],
    )

    state = run_eval.EvalState()
    run_eval.run_evaluation(
        runs=runs,
        eval_dir=tmp_path,
        state=state,
        debug=False,
        parallelism=1,
        resume=False,
        retry_failed=False,
    )

    persisted = json.loads((tmp_path / run_eval.STATE_FILENAME).read_text())["runs"]

    assert persisted[runs[0].run_id]["status"] == "completed"
    assert persisted[runs[0].run_id]["success"] is True
    assert persisted[runs[1].run_id]["status"] == "completed"
    assert persisted[runs[1].run_id]["success"] is False
    assert persisted[runs[2].run_id]["status"] == "failed"
    assert persisted[runs[2].run_id]["success"] is False
