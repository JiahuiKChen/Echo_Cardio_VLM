#!/usr/bin/env python3
"""Dependency-light proofs for the R7C retrospective/runtime boundary.

These tests inspect tracked Python and shell source plus synthetic accounting
records.  They never contact SCC, invoke Grid Engine, or open a scientific
artifact.
"""
from __future__ import annotations

import ast
import hashlib
import inspect
from pathlib import Path
import subprocess
import sys
import traceback
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import lvef_c3_r8u_r7c_accounting as accounting


RUNTIME_COMMIT = "1be99c6436293a7cad576e9855ba4cd58a71e156"
LEGACY_CONTROLLER = SCRIPTS / "lvef_c3_r8r_recovery_continuation.py"
LEGACY_RUNNER = SCRIPTS / "scc_run_lvef_c3_r8r_recovery_continuation.sh"
R7C_ENTRYPOINT = SCRIPTS / "lvef_c3_r8u_r7c_terminal_adjudicator.py"
R7C_DIAGNOSIS = (
    ROOT / "docs/lvef_multitask/phase1i_r8u_r7c_terminal_adjudication.md"
)

STARTING_CONTROLLER_SHA256 = (
    "e1aae71122249300088b7ed5d99487cb73282fc3fb0da59b884c886ba904cb4b"
)
STARTING_LIVE_FUNCTION_SHA256 = {
    "_r8u_r7_build_worker_context": (
        "f466d033770901b97722f29d0b12d3841e03cca1012b5360791bf069e2594be9"
    ),
    "_r8u_r7_qstat_projection": (
        "e83162b7ad018f8b88a87b5f6f9679200f29828c4dffd1fac119ee35ff20b26e"
    ),
    "_validate_r8u_r7_qstat_projection": (
        "27c4130d782f5fdab04dccc3482c8dd6d7b9b0cf52707d6ebede4da099874220"
    ),
    "validate_r8u_r7_continuation_worker_submission": (
        "f8e2819a078e47265f66eb6a7c9ff8db3fa1c6f7ca01da369696c048865e008a"
    ),
    "run_r8u_r7_continuation_array_task": (
        "36ba66cc599b8de913d82d4939de75cc9f4aa0f10623e4fe1b48c85b4af1a199"
    ),
    "run_r8u_r7_continuation_finalizer": (
        "a796ac96b74f2cab69459acc495681eddffddcb480bf9a4b6919240695ed400f"
    ),
}

LIVE_BOUNDARY_CALLS = {
    "build_worker_scheduler_context",
    "run_r8u_r7_continuation_array_task",
    "run_r8u_r7_continuation_finalizer",
    "validate_r8u_r7_continuation_worker_submission",
    "_r8u_r7_build_worker_context",
    "_r8u_r7_qstat_projection",
    "_r8u_r5_qstat_projection",
    "_validate_r8u_r7_qstat_projection",
}
SCIENTIFIC_EXECUTION_CALLS = {
    "execute_exact_batch_download",
    "run_production_dicom_extraction",
    "run_production_echoprime",
    "run_batch_task",
    "run_cross_batch_finalizer",
    "run_r8u_r7_batch16_preservation_recovery",
    "preserve_lvef_c3_production_batch",
    "retire_lvef_c3_extracted_cache",
    "fit",
    "fit_model",
    "predict",
    "predict_proba",
}
FORBIDDEN_DYNAMIC_CALLS = {"eval", "exec", "__import__"}
FORBIDDEN_DIRECT_IMPORTS = {
    "lvef_c3_full_sequential",
    "lvef_c3_full_scheduler",
    "lvef_c3_r8r_recovery_continuation",
    "run_production_dicom_extraction",
    "run_production_echoprime",
    "preserve_lvef_c3_production_batch",
    "retire_lvef_c3_extracted_cache",
    "finalize_lvef_c3_production",
}
WORKER_ENVIRONMENT_NAMES = {
    "JOB_ID",
    "JOB_NAME",
    "SGE_TASK_ID",
    "NSLOTS",
    "CUDA_VISIBLE_DEVICES",
}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _parse(path: Path) -> tuple[str, ast.Module]:
    source = _read(path)
    return source, ast.parse(source, filename=str(path))


def _functions(tree: ast.Module) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    return {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _dotted_name(node: ast.AST) -> str:
    pieces: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        pieces.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        pieces.append(current.id)
    return ".".join(reversed(pieces))


def _call_names(node: ast.AST) -> tuple[str, ...]:
    return tuple(
        _dotted_name(candidate.func)
        for candidate in ast.walk(node)
        if isinstance(candidate, ast.Call)
    )


def _call_leaves(node: ast.AST) -> set[str]:
    return {
        name.rsplit(".", 1)[-1]
        for name in _call_names(node)
        if name
    }


def _reachable_local_functions(
    tree: ast.Module, roots: Iterable[str]
) -> tuple[ast.FunctionDef | ast.AsyncFunctionDef, ...]:
    functions = _functions(tree)
    pending = list(roots)
    visited: set[str] = set()
    result: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
    while pending:
        name = pending.pop()
        if name in visited:
            continue
        assert name in functions, f"missing local function: {name}"
        visited.add(name)
        function = functions[name]
        result.append(function)
        for called in _call_leaves(function):
            if called in functions and called not in visited:
                pending.append(called)
    return tuple(result)


def _function_source(
    source: str, function: ast.FunctionDef | ast.AsyncFunctionDef
) -> str:
    value = ast.get_source_segment(source, function)
    assert isinstance(value, str) and value
    return value


def _main_guard_calls_guarded_main(tree: ast.Module) -> bool:
    for node in tree.body:
        if not isinstance(node, ast.If):
            continue
        comparison = node.test
        if not (
            isinstance(comparison, ast.Compare)
            and isinstance(comparison.left, ast.Name)
            and comparison.left.id == "__name__"
            and len(comparison.ops) == 1
            and isinstance(comparison.ops[0], ast.Eq)
            and len(comparison.comparators) == 1
            and isinstance(comparison.comparators[0], ast.Constant)
            and comparison.comparators[0].value == "__main__"
        ):
            continue
        return "guarded_main" in _call_leaves(node)
    return False


def _argument_names(function: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    arguments = function.args
    return [
        item.arg
        for item in (*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs)
    ]


def _valid_qacct_record(job_name: str) -> dict[str, str]:
    return {
        "jobnumber": accounting.ARRAY_JOB_ID,
        "taskid": "17",
        "jobname": job_name,
        "owner": accounting.OWNER,
        "qsub_time": "Sun Sep 06 12:31:30 2026",
        "start_time": "Sun Sep 06 12:32:00 2026",
        "end_time": "Sun Sep 06 12:33:00 2026",
        "failed": "0",
        "exit_status": "0",
        "ru_wallclock": "60",
    }


def test_documented_task17_live_failure_call_path_is_exact() -> None:
    legacy_source, legacy_tree = _parse(LEGACY_CONTROLLER)
    functions = _functions(legacy_tree)
    assert "run_r8u_r7_continuation_array_task" in _call_leaves(
        functions["guarded_main"]
    )
    assert "validate_r8u_r7_continuation_worker_submission" in _call_leaves(
        functions["run_r8u_r7_continuation_array_task"]
    )
    assert "_r8u_r7_qstat_projection" in _call_leaves(
        functions["validate_r8u_r7_continuation_worker_submission"]
    )
    assert "_r8u_r5_qstat_projection" in _call_leaves(
        functions["_r8u_r7_qstat_projection"]
    )

    qstat_source = _function_source(
        legacy_source, functions["_r8u_r5_qstat_projection"]
    )
    assert '"-xml"' in qstat_source
    assert '"JB_name"' in qstat_source
    assert "SCHEDULER_JOB_ROLE_MISMATCH" in qstat_source

    worker_context_source = _function_source(
        legacy_source, functions["_r8u_r7_build_worker_context"]
    )
    assert "expected_job_role=expected_role" in worker_context_source
    assert "observed_job_role=expected_role" in worker_context_source

    runner_source = _read(LEGACY_RUNNER)
    case_position = runner_source.index("lvef_c3_r8u_r7_seq_*)")
    role_position = runner_source.index(
        'ROLE="r8u_r7_array_task_${SGE_TASK_ID}"', case_position
    )
    mode_position = runner_source.index(
        "MODE=--run-r8u-r7-continuation-17-19-array-task", role_position
    )
    exec_position = runner_source.index('"$CONTROLLER" "$MODE"', mode_position)
    assert case_position < role_position < mode_position < exec_position

    diagnosis = _read(R7C_DIAGNOSIS)
    documented = (
        "scc_run_lvef_c3_r8r_recovery_continuation.sh",
        "run_r8u_r7_continuation_array_task",
        "validate_r8u_r7_continuation_worker_submission",
        "_r8u_r7_qstat_projection",
        "_r8u_r5_qstat_projection",
    )
    positions = [diagnosis.index(item) for item in documented]
    assert positions == sorted(positions)


def test_live_r7_worker_source_is_pinned_to_the_starting_commit() -> None:
    payload = LEGACY_CONTROLLER.read_bytes()
    source = payload.decode("utf-8", "strict")
    tree = ast.parse(source, filename=str(LEGACY_CONTROLLER))
    functions = _functions(tree)
    for name, expected_sha256 in STARTING_LIVE_FUNCTION_SHA256.items():
        function_payload = _function_source(source, functions[name]).encode("utf-8")
        assert hashlib.sha256(function_payload).hexdigest() == expected_sha256, name

    assert accounting.RUNTIME_IMPLEMENTATION_COMMIT == RUNTIME_COMMIT
    assert accounting.RUNTIME_CONTROLLER_SHA256 == STARTING_CONTROLLER_SHA256
    historical = subprocess.run(
        [
            "/usr/bin/git", "show",
            f"{RUNTIME_COMMIT}:scripts/lvef_c3_r8r_recovery_continuation.py",
        ],
        cwd=ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert historical.returncode == 0 and historical.stderr == b""
    assert hashlib.sha256(historical.stdout).hexdigest() == (
        STARTING_CONTROLLER_SHA256
    )


def test_r7c_public_entrypoint_and_main_runner_are_closed() -> None:
    _source, tree = _parse(R7C_ENTRYPOINT)
    functions = _functions(tree)
    assert {"adjudicate_fixed_existing_jobs", "guarded_main"} <= set(functions)

    adjudicate = functions["adjudicate_fixed_existing_jobs"]
    assert _argument_names(adjudicate) == []
    assert adjudicate.args.vararg is None and adjudicate.args.kwarg is None

    guarded = functions["guarded_main"]
    assert _argument_names(guarded) == ["arguments"]
    assert guarded.args.vararg is None and guarded.args.kwarg is None
    defaults = [*guarded.args.defaults, *guarded.args.kw_defaults]
    assert len(defaults) == 1
    assert isinstance(defaults[0], ast.Constant) and defaults[0].value is None
    assert "adjudicate_fixed_existing_jobs" in _call_leaves(guarded)
    assert _main_guard_calls_guarded_main(tree)


def test_r7c_entrypoint_cannot_reach_live_worker_or_qstat() -> None:
    source, tree = _parse(R7C_ENTRYPOINT)
    reachable = _reachable_local_functions(
        tree, ("adjudicate_fixed_existing_jobs", "guarded_main")
    )
    reachable_calls = set().union(*(_call_leaves(node) for node in reachable))
    assert not reachable_calls & LIVE_BOUNDARY_CALLS
    assert not reachable_calls & FORBIDDEN_DYNAMIC_CALLS

    for function in reachable:
        function_source = _function_source(source, function)
        function_tree = ast.parse(function_source)
        loaded_identifiers = {
            node.id.lower()
            for node in ast.walk(function_tree)
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
        }
        loaded_attributes = {
            node.attr.lower()
            for node in ast.walk(function_tree)
            if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load)
        }
        assert not any("qstat" in item for item in loaded_identifiers)
        assert not any("qstat" in item for item in loaded_attributes)
        command_literals = {
            node.value
            for node in ast.walk(function_tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
        assert "-xml" not in command_literals
        assert not any(
            value == "qstat" or value.endswith("/qstat")
            for value in command_literals
        )


def test_r7c_entrypoint_does_not_read_ambient_worker_environment() -> None:
    _source, tree = _parse(R7C_ENTRYPOINT)
    reachable = _reachable_local_functions(
        tree, ("adjudicate_fixed_existing_jobs", "guarded_main")
    )
    for function in reachable:
        for node in ast.walk(function):
            if isinstance(node, ast.Attribute) and node.attr == "environ":
                assert function.name == "_repository_authority"
            if not isinstance(node, ast.Call):
                continue
            called = _dotted_name(node.func)
            if called.rsplit(".", 1)[-1] not in {"getenv", "get"}:
                continue
            if node.args and isinstance(node.args[0], ast.Constant):
                name = node.args[0].value
                assert name not in WORKER_ENVIRONMENT_NAMES
                if called == "os.environ.get":
                    assert name in {
                        adjudicator.COORDINATOR_LOCAL_HEAD_ENV,
                        adjudicator.COORDINATOR_LOCAL_CLEAN_ENV,
                    }


def test_truncated_qstat_display_name_is_never_exact_identity() -> None:
    spec = accounting.TASK_ACCOUNTING_SPECS[17]
    accepted = accounting.project_fixed_qacct_record(
        spec, _valid_qacct_record(accounting.ARRAY_JOB_NAME)
    )
    assert accepted["observed_qacct_job_name"] == (
        "lvef_c3_r8u_r7_seq_1be99c64"
    )

    for truncated in (
        "lvef_c3_r8",
        "lvef_c3_r8...",
        "lvef_c3_r8u_r7_seq",
        "lvef_c3_r8u_r7_seq_1be99c6",
    ):
        try:
            accounting.project_fixed_qacct_record(
                spec, _valid_qacct_record(truncated)
            )
        except accounting.R7CAccountingError as exc:
            assert exc.code == "R8U_R7C_QACCT_IDENTITY_INVALID"
        else:
            raise AssertionError(f"truncated job name accepted: {truncated!r}")


def test_r7c_entrypoint_has_no_reachable_scientific_execution() -> None:
    _source, tree = _parse(R7C_ENTRYPOINT)
    reachable = _reachable_local_functions(
        tree, ("adjudicate_fixed_existing_jobs", "guarded_main")
    )
    calls = set().union(*(_call_leaves(node) for node in reachable))
    assert not calls & SCIENTIFIC_EXECUTION_CALLS
    assert not calls & LIVE_BOUNDARY_CALLS
    assert "_capture_qsub" not in calls
    assert "qsub" not in calls

    imported_modules: set[str] = set()
    star_imports: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported_modules.add(node.module or "")
            if any(alias.name == "*" for alias in node.names):
                star_imports.append(node.module or "")
    assert not star_imports
    assert not imported_modules & FORBIDDEN_DIRECT_IMPORTS


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
