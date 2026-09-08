"""Synthetic, dependency-light checks for the fixed R7H evidence transition."""
from __future__ import annotations

import contextlib
import copy
import inspect
from pathlib import Path
import stat
import sys
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import lvef_c3_r8u_r7h_continuation as r7h


CURRENT = "a" * 40


def _expect(code, operation):
    try:
        operation()
    except r7h.R7HContinuationError as error:
        assert error.code == code, error.code
    else:
        raise AssertionError(f"expected {code}")


def _tail_snapshot():
    roles = {}
    for task in r7h.TASK_IDS:
        roles[f"task_{task}"] = {
            "task_id": task,
            "batch_id": f"c3_batch_{task - 1:03d}",
            **dict.fromkeys((
                "download_began", "extraction_began", "echoprime_began",
                "preservation_began", "scientific_artifact_exists",
                "paths_emitted", "identifiers_emitted",
            ), False),
            "preserved_scientific_artifact_files": 0,
            "preserved_scientific_artifact_bytes": 0,
            "scientific_body_reads": 0,
        }
    roles["finalizer"] = {
        "cohort_directory_exists": True,
        "cohort_directory_empty": True,
        "scientific_artifact_exists": False,
        "preserved_scientific_artifact_files": 0,
        "preserved_scientific_artifact_bytes": 0,
        "scientific_body_reads": 0,
        "paths_emitted": False,
        "identifiers_emitted": False,
    }
    return {
        "roles": roles,
        "preserved_scientific_artifact_files": 0,
        "preserved_scientific_artifact_bytes": 0,
    }


def _closed_log(spec, _receipt, **_kwargs):
    return {
        "job_kind": "array" if spec.task_id else "finalizer",
        "job_id": r7h.CONSUMED_ARRAY_JOB_ID if spec.task_id else r7h.CONSUMED_FINALIZER_JOB_ID,
        "task_id": spec.task_id,
        "expected_job_role": "SYNTHETIC_CONSUMED_ROLE",
        "failed": 0,
        "exit_status": 78,
        "terminal_classification": "FAIL",
        "terminal_marker": "SYNTHETIC_BLOCKED_PRE_SCIENTIFIC_GATE",
        "reported_failed_stage": "PREBODY_AUTHORITY",
        "scheduler_log_sha256": "c" * 64,
        "scheduler_log_bytes": 10,
        "scheduler_log_mode": "0600",
        "application_failure_supported": False,
        "pre_scientific_or_predecessor_gate_failure_supported": True,
    }


@contextlib.contextmanager
def _predecessor_evidence():
    specs = tuple(
        SimpleNamespace(task_id=task, receipt_path=Path(f"/synthetic/control_{index}"))
        for index, task in enumerate((*r7h.TASK_IDS, None))
    )
    snapshot = _tail_snapshot()
    with contextlib.ExitStack() as stack:
        stack.enter_context(mock.patch.object(r7h, "_current_r8u_r7h_implementation_commit", return_value=CURRENT))
        stack.enter_context(mock.patch.object(r7h, "_fixed_terminal_authority", return_value=({}, specs)))
        stack.enter_context(mock.patch.object(r7h, "_fixed_consumed_r7f_controls", return_value=r7h.CONSUMED_R7F_AUTHORITY_SHA256))
        stack.enter_context(mock.patch.object(r7h.accounting, "validate_fixed_scheduler_accounting_environment", return_value={}))
        stack.enter_context(mock.patch.object(
            r7h.accounting, "load_accounting_receipt",
            side_effect=lambda spec, **kwargs: ({}, r7h.CONSUMED_TASK17_ACCOUNTING_SHA256 if spec.task_id == 17 else "b" * 64),
        ))
        queries = stack.enter_context(mock.patch.object(r7h.accounting, "reuse_or_query_fixed_accounting"))
        stack.enter_context(mock.patch.object(r7h, "_fixed_consumed_log", side_effect=_closed_log))
        snapshot_mock = stack.enter_context(mock.patch.object(r7h, "_consumed_tail_artifact_snapshot", side_effect=lambda run: copy.deepcopy(snapshot)))
        stack.enter_context(mock.patch.object(r7h.os, "lstat", return_value=SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_size=10)))
        value, count = r7h._derive_consumed_r7f_evidence(
            SimpleNamespace(), capture_missing=False,
            evidence_producer_commit=r7h.R7H_TOPOLOGY_CORRECTION_BASE_COMMIT,
        )
        assert count == 0
        value["qacct_queries_to_close_consumed_attempt"] = 3
        source_hash = r7h._sha256(r7h._canonical(value))
        stack.enter_context(mock.patch.object(r7h, "PREDECESSOR_CONSUMED_EVIDENCE_SHA256", source_hash))
        yield SimpleNamespace(value=value, digest=source_hash, queries=queries, snapshot=snapshot, snapshot_mock=snapshot_mock)


def test_predecessor_evidence_replays_without_requery_or_relabeling():
    with _predecessor_evidence() as fixture:
        original = r7h._canonical(fixture.value)
        with mock.patch.object(r7h, "_write_private_json") as publication:
            validated = r7h._validate_consumed_evidence(fixture.value, run=SimpleNamespace())
        assert validated == fixture.value
        assert r7h._canonical(fixture.value) == original
        assert validated["r7h_runtime_commit"] == r7h.R7H_TOPOLOGY_CORRECTION_BASE_COMMIT
        assert validated["qacct_queries_to_close_consumed_attempt"] == 3
        fixture.queries.assert_not_called()
        publication.assert_not_called()


def test_predecessor_epoch_or_sealed_hash_cannot_be_substituted():
    with _predecessor_evidence() as fixture:
        for commit in (
            CURRENT, r7h.R7H_CORRECTION_BASE_COMMIT,
            r7h.R7H_LEGACY_METADATA_CORRECTION_BASE_COMMIT, "e" * 40,
        ):
            changed = {**fixture.value, "r7h_runtime_commit": commit}
            _expect("R7H_CONTROL_EPOCH_MISMATCH", lambda: r7h._validate_consumed_evidence(changed, run=SimpleNamespace()))
        changed = {**fixture.value, "qacct_queries_to_close_consumed_attempt": 0}
        _expect("R7H_CONTROL_EVIDENCE_HASH", lambda: r7h._validate_consumed_evidence(changed, run=SimpleNamespace()))
        fixture.queries.assert_not_called()


def test_historical_replay_cannot_create_new_historical_accounting():
    with _predecessor_evidence() as fixture:
        _expect("R7H_CONTROL_EPOCH_MISMATCH", lambda: r7h._derive_consumed_r7f_evidence(
            SimpleNamespace(), capture_missing=True,
            evidence_producer_commit=r7h.R7H_TOPOLOGY_CORRECTION_BASE_COMMIT,
        ))
        fixture.queries.assert_not_called()


def test_capacity_reuses_predecessor_before_topology_without_publication():
    class ReachedTopologyBoundary(Exception):
        pass

    with _predecessor_evidence() as fixture:
        with (
            mock.patch.object(r7h, "_load_fixed_original_run", return_value=SimpleNamespace()),
            mock.patch.object(r7h.historical, "_r8u_r7d_bounded_prefix", return_value=r7h.PREFIX_FINAL_RECEIPT_SHA256),
            mock.patch.object(r7h, "load_r8u_r7h_sealed_history", return_value={}),
            mock.patch.object(r7h.os.path, "lexists", side_effect=lambda path: path == r7h.CONSUMED_EVIDENCE_PATH),
            mock.patch.object(r7h, "_ensure_private_directory"),
            mock.patch.object(r7h, "_read_private_json", return_value=(fixture.value, r7h._canonical(fixture.value), fixture.digest)),
            mock.patch.object(r7h.scheduler, "build_qsub_environment", return_value=({}, "SYNTHETIC")),
            mock.patch.object(r7h, "_live_reference_projection", side_effect=ReachedTopologyBoundary) as next_boundary,
            mock.patch.object(r7h, "_write_private_json") as publication,
        ):
            try:
                r7h.capture_r8u_r7h_capacity()
            except ReachedTopologyBoundary:
                pass
            else:
                raise AssertionError("capacity must reach the next topology boundary")
        next_boundary.assert_called_once()
        fixture.queries.assert_not_called()
        publication.assert_not_called()


def test_missing_predecessor_cannot_start_new_accounting_or_execution():
    with (
        mock.patch.object(r7h, "_current_r8u_r7h_implementation_commit", return_value=CURRENT),
        mock.patch.object(r7h, "_load_fixed_original_run", return_value=SimpleNamespace()),
        mock.patch.object(r7h.historical, "_r8u_r7d_bounded_prefix", return_value=r7h.PREFIX_FINAL_RECEIPT_SHA256),
        mock.patch.object(r7h, "load_r8u_r7h_sealed_history", return_value={}),
        mock.patch.object(r7h.os.path, "lexists", return_value=False),
        mock.patch.object(r7h, "_ensure_private_directory") as namespace,
        mock.patch.object(r7h, "_derive_consumed_r7f_evidence") as derive,
        mock.patch.object(r7h, "_write_private_json") as publication,
        mock.patch.object(r7h, "_live_reference_projection") as references,
    ):
        _expect("R7H_CONTROL_EPOCH_MISMATCH", r7h.capture_r8u_r7h_capacity)
    derive.assert_not_called()
    publication.assert_not_called()
    references.assert_not_called()
    namespace.assert_not_called()


def test_fresh_topology_readback_binds_successor_to_original_evidence_hash():
    references = {
        "status": "PASS_R7H_ZERO_COMPETING_LIVE_REFERENCES",
        "active_job_references": 0,
        "active_process_references": 0,
        "qstat_projection": {
            "status": "PASS_R7H_RELEVANT_QSTAT_SNAPSHOT",
            "expected_job_count": 0,
            "target_rows_visible": 0,
            "competing_relevant_jobs": 0,
            "job_id_matches": True,
            "job_name_matches": True,
            "owner_matches": True,
            "qstat_snapshot_count": 1,
            "qstat_argv_sha256": "d" * 64,
            "qstat_stdout_sha256": "d" * 64,
            "truncated_display_name_used": False,
        },
        "process_projection": {
            "status": "PASS_ZERO_R7H_RELEVANT_PROCESSES",
            "matching_processes": 0,
            "process_snapshot_count": 1,
            "ps_argv_sha256": "d" * 64,
            "ps_stdout_sha256": "d" * 64,
        },
    }
    sealed = {"batch16_failed_partial_seal_sha256": "e" * 64}
    with _predecessor_evidence() as fixture:
        topology = r7h._topology_authority(
            implementation_commit=CURRENT,
            topology={"status": r7h.TOPOLOGY_PASS, "active_job_references": 0, "active_process_references": 0},
            sealed_history=sealed,
            consumed_evidence_sha256=fixture.digest,
            live_references=references,
        )
        with (
            mock.patch.object(r7h, "_load_fixed_original_run", return_value=SimpleNamespace()),
            mock.patch.object(r7h, "load_r8u_r7h_sealed_history", return_value=sealed),
            mock.patch.object(r7h, "_read_private_json", return_value=(fixture.value, r7h._canonical(fixture.value), fixture.digest)),
        ):
            assert r7h._validate_topology_authority(topology) == topology
        assert topology["r7h_runtime_commit"] == CURRENT
        assert topology["consumed_r7f_evidence_sha256"] == fixture.digest
        assert fixture.value["r7h_runtime_commit"] == r7h.R7H_TOPOLOGY_CORRECTION_BASE_COMMIT
        fixture.queries.assert_not_called()


def test_consumed_failure_stays_historical_as_tail_metadata_accumulates():
    with _predecessor_evidence() as fixture:
        for task in r7h.TASK_IDS:
            role = fixture.snapshot["roles"][f"task_{task}"]
            role["preservation_began"] = True
            role["scientific_artifact_exists"] = True
            role["preserved_scientific_artifact_files"] = 3
            role["preserved_scientific_artifact_bytes"] = 300
            fixture.snapshot["preserved_scientific_artifact_files"] += 3
            fixture.snapshot["preserved_scientific_artifact_bytes"] += 300
            validated = r7h._validate_consumed_evidence(
                fixture.value, run=SimpleNamespace(), replay_tail_artifacts=False,
            )
            assert validated["preserved_scientific_artifact_files"] == 0
            assert validated["records"][f"task_{task}"]["terminal_classification"] == "FAIL"
        _expect("R7H_CONSUMED_R7F_EVIDENCE_INVALID", lambda: r7h._validate_consumed_evidence(fixture.value, run=SimpleNamespace()))
        fixture.queries.assert_not_called()


def test_new_controls_reject_predecessor_execution_epoch():
    for commit in (
        r7h.R7H_TOPOLOGY_CORRECTION_BASE_COMMIT,
        r7h.R7H_CORRECTION_BASE_COMMIT,
        r7h.R7H_LEGACY_METADATA_CORRECTION_BASE_COMMIT,
    ):
        _expect("R7H_CONTROL_SCHEMA_INVALID", lambda: r7h._common(
            artifact_type="synthetic_new_control", status="PASS_SYNTHETIC",
            implementation_commit=commit,
        ))


def test_topology_semantics_allow_only_informational_metadata_growth():
    baseline = {
        "status": r7h.TOPOLOGY_PASS,
        "other_active_scientific_caches": 0,
        "unknown_or_unsealed_caches": 0,
        "historical_partial_adoptable": False,
        "retained_metadata_roots": 19,
        "retained_metadata_files": 100,
        "retained_metadata_bytes": 10000,
    }
    for completed in range(4):
        current = {
            **baseline,
            "retained_metadata_roots": 19 + completed,
            "retained_metadata_files": 100 + completed * 5,
            "retained_metadata_bytes": 10000 + completed * 500,
        }
        assert r7h._same_topology_execution_invariants(current, baseline)
        for field, invalid in (
            ("other_active_scientific_caches", 1),
            ("unknown_or_unsealed_caches", 1),
            ("historical_partial_adoptable", True),
            ("retained_metadata_files", True),
            ("retained_metadata_bytes", -1),
        ):
            assert not r7h._same_topology_execution_invariants({**current, field: invalid}, baseline)


if __name__ == "__main__":
    tests = [fn for name, fn in sorted(globals().items()) if name.startswith("test_") and inspect.isfunction(fn)]
    for test in tests:
        test()
    print(f"PASS R7H control epoch: {len(tests)} tests")
