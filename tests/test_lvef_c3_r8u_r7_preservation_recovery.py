#!/usr/bin/env python3
"""Dependency-light contracts for the fixed R8U-R7 recovery controller.

The tests in this module inspect commands and Python control flow only.  They
never contact SCC, invoke Grid Engine, open a scientific artifact, or execute
an extraction, EchoPrime, publication, model, prediction, or analysis path.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path
import sys
import traceback
from typing import Any, Callable, Sequence
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import lvef_c3_r8r_recovery_continuation as controller


IMPLEMENTATION_COMMIT = "7" * 40
FORBIDDEN_FIXED_SCOPE_PARAMETERS = {
    "attempt",
    "attempt_id",
    "plan",
    "plan_path",
    "batch",
    "batch_id",
    "path",
    "output_root",
    "job_id",
    "retry_count",
    "continuation_range",
    "task_range",
    "first_task",
    "last_task",
}


def _api(name: str) -> Callable[..., Any]:
    value = getattr(controller, name, None)
    assert callable(value), f"missing R8U-R7 controller API: {name}"
    return value


def _source(function: Callable[..., Any]) -> str:
    return inspect.getsource(function)


def _dotted_name(node: ast.AST) -> str:
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    return ".".join(reversed(parts))


def _call_names(function: Callable[..., Any]) -> tuple[str, ...]:
    tree = ast.parse(_source(function))
    return tuple(
        _dotted_name(node.func)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
    )


def _leaf(name: str) -> str:
    return name.rsplit(".", 1)[-1]


def _reachable_r7_functions(
    root: Callable[..., Any],
) -> tuple[Callable[..., Any], ...]:
    """Return controller-local R7 helpers reachable from one public API."""

    pending = [root]
    found: list[Callable[..., Any]] = []
    visited: set[str] = set()
    while pending:
        function = pending.pop()
        name = function.__name__
        if name in visited:
            continue
        visited.add(name)
        found.append(function)
        for called in _call_names(function):
            candidate = getattr(controller, _leaf(called), None)
            if (
                inspect.isfunction(candidate)
                and candidate.__module__ == controller.__name__
                and "r8u_r7" in candidate.__name__
                and candidate.__name__ not in visited
            ):
                pending.append(candidate)
    return tuple(found)


def _called_builder(
    submitter: Callable[..., Any], expected_name: str
) -> Callable[..., Any]:
    assert expected_name in {_leaf(name) for name in _call_names(submitter)}, (
        submitter.__name__,
        expected_name,
    )
    return _api(expected_name)


def _resource_values(command: Sequence[str], prefix: str) -> tuple[str, ...]:
    values: list[str] = []
    for index, token in enumerate(command[:-1]):
        if token == "-l" and str(command[index + 1]).startswith(prefix):
            values.append(str(command[index + 1]))
    return tuple(values)


def _h_rt_seconds(command: Sequence[str]) -> int:
    values = _resource_values(command, "h_rt=")
    assert len(values) == 1, values
    pieces = values[0].split("=", 1)[1].split(":")
    assert len(pieces) == 3 and all(piece.isdigit() for piece in pieces)
    hours, minutes, seconds = (int(piece) for piece in pieces)
    return hours * 3600 + minutes * 60 + seconds


def _assert_retry_disabled(command: Sequence[str]) -> None:
    index = list(command).index("-r")
    assert command[index + 1] == "n"


def _assert_four_cpu_slots(command: Sequence[str]) -> None:
    index = list(command).index("-pe")
    assert command[index + 1 : index + 3] == ["omp", "4"]


def _first_position(source: str, alternatives: Sequence[str]) -> int:
    positions = [source.find(token) for token in alternatives]
    positions = [position for position in positions if position >= 0]
    assert positions, alternatives
    return min(positions)


def test_r7_public_surface_is_fixed_to_batch16_and_tasks17_19() -> None:
    required = (
        "_r8u_r7_recovery_qsub_command",
        "submit_r8u_r7_batch16_preservation_recovery",
        "run_r8u_r7_batch16_preservation_recovery",
        "adjudicate_r8u_r7_batch16_preservation_recovery",
        "submit_r8u_r7_continuation_17_19",
    )
    for name in required:
        _api(name)

    assert controller.R8U_FIXED_BATCH_ID == "c3_batch_015"
    assert controller.R8U_FIXED_RECOVERY_TASK_ID == 16
    assert controller.R8U_FIXED_CONTINUATION_TASK_IDS == (17, 18, 19)
    assert controller.R8U_FIXED_CONTINUATION_TASK_RANGE == "17-19"
    assert controller.R8U_FIXED_CONTINUATION_MAX_CONCURRENCY == 1
    assert controller.R8U_R7_ROOT.name == (
        "r8u_r7_batch16_preservation_recovery"
    )

    for name in required[1:]:
        parameters = set(inspect.signature(_api(name)).parameters)
        assert not parameters & FORBIDDEN_FIXED_SCOPE_PARAMETERS, (
            name,
            parameters & FORBIDDEN_FIXED_SCOPE_PARAMETERS,
        )

    parser = _api("_r8u_r7_parser")()
    options = {
        option
        for action in parser._actions
        for option in action.option_strings
    }
    assert {
        "--submit-r8u-r7-batch16-preservation-recovery",
        "--run-r8u-r7-batch16-preservation-recovery",
        "--adjudicate-r8u-r7-batch16-preservation-recovery",
        "--submit-r8u-r7-continuation-17-19",
        "--run-r8u-r7-continuation-17-19-array-task",
        "--run-r8u-r7-continuation-finalizer",
    } <= options
    forbidden_options = {
        "--attempt",
        "--attempt-id",
        "--plan",
        "--batch",
        "--batch-id",
        "--path",
        "--output-root",
        "--job-id",
        "--retry-count",
        "--task-range",
    }
    assert not options & forbidden_options


def test_r7_recovery_qsub_is_cpu_only_nonarray_and_at_most_two_hours() -> None:
    capture = mock.Mock(side_effect=AssertionError("qsub was reached"))
    with mock.patch.object(controller.scheduler, "_capture_qsub", capture):
        command = _api("_r8u_r7_recovery_qsub_command")(
            IMPLEMENTATION_COMMIT
        )
    capture.assert_not_called()
    assert isinstance(command, list) and command
    assert "-t" not in command
    assert not any("gpu" in str(token).lower() for token in command)
    assert command[command.index("-N") + 1] == (
        f"lvef_c3_r8u_r7_rec_{IMPLEMENTATION_COMMIT[:8]}"
    )
    assert command[-1] == str(controller.RUNNER_PATH)
    assert 0 < _h_rt_seconds(command) <= 2 * 60 * 60
    _assert_four_cpu_slots(command)
    _assert_retry_disabled(command)
    memory = _resource_values(command, "mem_per_core=")
    assert len(memory) == 1 and memory[0] != "mem_per_core=0"


def test_r7_worker_reaches_only_preservation_retirement_and_finalization() -> None:
    worker = _api("run_r8u_r7_batch16_preservation_recovery")
    functions = _reachable_r7_functions(worker)
    sources = {function.__name__: _source(function) for function in functions}
    combined = "\n".join(sources.values())
    calls = {
        name
        for function in functions
        for name in _call_names(function)
    }
    leaves = {_leaf(name) for name in calls}

    assert any(
        name in calls or name in leaves
        for name in ("dependency.preserve", "preserve", "preserve_batch")
    )
    assert any(
        name in calls or name in leaves
        for name in ("dependency.retire", "retire", "_execute_cache_retirement")
    )
    assert any(
        name in calls or name in leaves
        for name in (
            "dependency.finalize_batch",
            "finalize_batch",
            "_validate_batch_finalization",
        )
    )
    assert "PASS_BATCH_CACHE_RETIREMENT_ELIGIBLE" in combined
    assert "SEALED_SCHEDULER_RUNTIME_REPLAY" in combined
    assert "NO_SCIENTIFIC_BODY" in combined

    forbidden_calls = {
        "execute_exact_batch_download",
        "run_production_dicom_extraction",
        "run_production_echoprime",
        "advance_stage_ledger",
        "run_batch_task",
        "_r8u_r6_publish_candidate",
        "_r8u_r5_publish_candidate",
        "_r8u_r4_publish_candidate",
        "_r8u_r3_publish_candidate",
        "rename",
        "replace",
    }
    assert not leaves & forbidden_calls, leaves & forbidden_calls


def test_r7_retirement_is_lexically_gated_by_preservation_pass() -> None:
    worker = _api("run_r8u_r7_batch16_preservation_recovery")
    candidates = _reachable_r7_functions(worker)
    sequence_source = ""
    for function in candidates:
        source = _source(function)
        if (
            "PASS_BATCH_CACHE_RETIREMENT_ELIGIBLE" in source
            and any(token in source for token in (".preserve(", "preserve_batch("))
            and any(token in source for token in (".retire(", "_execute_cache_retirement("))
        ):
            sequence_source = source
            break
    assert sequence_source, "R7 worker has no closed preservation/retirement sequence"
    preserve = _first_position(
        sequence_source, (".preserve(", "preserve_batch(")
    )
    pass_gate = sequence_source.find(
        "PASS_BATCH_CACHE_RETIREMENT_ELIGIBLE", preserve
    )
    authorize = _first_position(
        sequence_source,
        ("_cache_retirement_authorization(", "retirement_authorization("),
    )
    retire = _first_position(
        sequence_source, (".retire(", "_execute_cache_retirement(")
    )
    finalize = _first_position(
        sequence_source, (".finalize_batch(", "_validate_batch_finalization(")
    )
    assert preserve < pass_gate < authorize < retire < finalize


def test_r7_adjudication_has_one_qacct_and_no_retry_submission_path() -> None:
    adjudicate = _api("adjudicate_r8u_r7_batch16_preservation_recovery")
    functions = _reachable_r7_functions(adjudicate)
    calls = [
        name
        for function in functions
        for name in _call_names(function)
    ]
    qacct_calls = [
        name
        for name in calls
        if _leaf(name) in {
            "_query_recovery_accounting",
            "_query_r8u_r7_recovery_accounting",
        }
    ]
    assert len(qacct_calls) == 1, qacct_calls
    assert sum(
        _leaf(name) == "submit_r8u_r7_continuation_17_19"
        for name in calls
    ) <= 1
    assert not any("retry" in name.lower() for name in calls)
    assert not any(
        _leaf(name) == "submit_r8u_r7_batch16_preservation_recovery"
        for name in calls
    )


def test_r7_continuation_is_exact_array17_19_and_one_held_finalizer() -> None:
    submitter = _api("submit_r8u_r7_continuation_17_19")
    array_builder = _called_builder(
        submitter, "_r8u_r7_continuation_array_command"
    )
    finalizer_builder = _called_builder(
        submitter, "_r8u_r7_continuation_finalizer_command"
    )
    array_job_id = "201"
    capture = mock.Mock(side_effect=AssertionError("qsub was reached"))
    with mock.patch.object(controller.scheduler, "_capture_qsub", capture):
        array = array_builder(IMPLEMENTATION_COMMIT)
        finalizer = finalizer_builder(IMPLEMENTATION_COMMIT, array_job_id)
    capture.assert_not_called()

    assert array[array.index("-t") + 1] == "17-19"
    assert array[array.index("-tc") + 1] == "1"
    assert array.count("gpus=1") == 1
    assert array[array.index("-N") + 1] == (
        f"lvef_c3_r8u_r7_seq_{IMPLEMENTATION_COMMIT[:8]}"
    )
    assert "-t" not in finalizer and "-tc" not in finalizer
    assert not any("gpu" in str(token).lower() for token in finalizer)
    assert finalizer[finalizer.index("-hold_jid") + 1] == array_job_id
    assert finalizer[finalizer.index("-N") + 1] == (
        f"lvef_c3_r8u_r7_fin_{IMPLEMENTATION_COMMIT[:8]}"
    )
    assert array[-1] == finalizer[-1] == str(controller.RUNNER_PATH)
    _assert_four_cpu_slots(array)
    _assert_four_cpu_slots(finalizer)
    _assert_retry_disabled(array)
    _assert_retry_disabled(finalizer)

    calls = _call_names(submitter)
    captures = [name for name in calls if _leaf(name) == "_capture_qsub"]
    assert len(captures) == 2
    assert not any(
        _leaf(name) in {
            "_query_recovery_accounting",
            "_query_r8u_r7_recovery_accounting",
        }
        for name in calls
    )


def test_r7_scientific_reuse_matches_the_frozen_embedding_summary_schema() -> None:
    summary = {
        "status": "PASS_ECHOPRIME_AND_POOLING",
        "n_clip_embeddings": 10_187,
        "n_pooled_studies": 250,
        "n_object_technical_dispositions": 0,
        "n_studies_affected_by_technical_disposition": 0,
        "n_no_cine_studies": 0,
        "n_new_no_cine_studies": 0,
        "object_substitution_count": 0,
        "unaccounted_multiframe_objects": 0,
        "all_extraction_rows_resolved": True,
        "all_successful_extractions_embedded": True,
        "all_technical_dispositions_retained": True,
        "all_no_cine_studies_prespecified": True,
        "all_finite": True,
        "encoder_only": True,
        "view_classifier_used": False,
        "pooling": "stable_clip_key_order_float64_mean_then_float32",
    }
    validator = _api("_validate_r8u_r7_embedding_summary")
    validator(summary)
    assert "n_blocking_failures" not in _source(validator)

    for key in (
        "object_substitution_count",
        "unaccounted_multiframe_objects",
        "all_successful_extractions_embedded",
        "encoder_only",
        "view_classifier_used",
        "pooling",
    ):
        altered = dict(summary)
        altered[key] = {
            "object_substitution_count": 1,
            "unaccounted_multiframe_objects": 1,
            "all_successful_extractions_embedded": False,
            "encoder_only": False,
            "view_classifier_used": True,
            "pooling": "unordered_mean",
        }[key]
        try:
            validator(altered)
        except controller.R8RControllerError as exc:
            assert exc.code == "R8U_R7_R6_SCIENTIFIC_OUTPUT_INVALID"
        else:
            raise AssertionError(f"summary mutation passed: {key}")


def test_r7_collision_checks_precede_every_qsub_boundary() -> None:
    recovery = _api("submit_r8u_r7_batch16_preservation_recovery")
    recovery_source = _source(recovery)
    recovery_qsub = _first_position(recovery_source, ("_capture_qsub(",))
    recovery_collision = _first_position(
        recovery_source,
        (
            "_r8u_r7_require_recovery_absent(",
            "_r8u_r7_require_recovery_namespace_absent(",
            "os.path.lexists(",
        ),
    )
    assert recovery_collision < recovery_qsub
    assert _call_names(recovery).count("scheduler._capture_qsub") == 1

    continuation = _api("submit_r8u_r7_continuation_17_19")
    continuation_source = _source(continuation)
    continuation_qsub = _first_position(
        continuation_source, ("_capture_qsub(",)
    )
    continuation_collision = _first_position(
        continuation_source,
        (
            "_r8u_r7_require_continuation_absent(",
            "_r8u_r7_require_continuation_namespace_absent(",
            "os.path.lexists(",
        ),
    )
    claim = _first_position(
        continuation_source,
        ("_r8u_r7_continuation_claim(", "continuation_claim("),
    )
    assert continuation_collision < claim < continuation_qsub
    assert _call_names(continuation).count("scheduler._capture_qsub") == 2


def _run_dependency_light() -> int:
    passed = 0
    failed = 0
    for name, function in sorted(globals().items()):
        if not name.startswith("test_") or not inspect.isfunction(function):
            continue
        try:
            function()
        except Exception:
            print(f"FAIL {name}")
            traceback.print_exc()
            failed += 1
        else:
            print(f"PASS {name}")
            passed += 1
    print(f"SUMMARY passed={passed} failed={failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_run_dependency_light())
