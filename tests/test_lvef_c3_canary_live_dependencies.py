from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
TABLE_PATH = ROOT / "configs/lvef_c3_canary_live_dependencies_v1.json"
sys.path.insert(0, str(ROOT / "scripts"))

import lvef_c3_canary_execution_authority as execution_authority


TOP_LEVEL_KEYS = {
    "schema_name",
    "schema_version",
    "audit_baseline_commit",
    "tracked_field_semantics",
    "post_repair_expectations",
    "canonical_entrypoint",
    "execute_mode",
    "dependency_count",
    "missing_producer_dependency_ids",
    "dependencies",
}
DEPENDENCY_KEYS = {
    "dependency_id",
    "phase",
    "prerequisite",
    "artifact_paths",
    "producer",
    "consumer",
    "producer_exists",
    "tracked",
    "exercised_by_tests",
    "test_evidence",
    "authority_packet_roles",
}
PHASE_ORDER = (
    "tracked_launch",
    "canonical_gates",
    "inherited_private_authority",
    "live_materialization",
    "dispatch_and_stages",
)
EXPECTED_DEPENDENCY_IDS = {
    "tracked_canary_entrypoint",
    "approved_python_launcher",
    "canonical_execution_state",
    "execute_scope_transition",
    "git_authority_and_clean_worktree",
    "manifest_schema_and_validator",
    "scheduler_template_and_validator",
    "execution_authority_schema_and_loader",
    "production_contract_and_callables",
    "immutable_attempt_capacity_and_preparation",
    "current_environment_receipt",
    "prior_production_authority_packet",
    "echoprime_checkpoint",
    "selected_study_authority",
    "selected_source_object_authority",
    "source_metadata_authority",
    "split_map_authority",
    "gcloud_and_cloudsdk_authority",
    "requester_pays_authority",
    "conflicting_process_and_scheduler_absence",
    "live_byte_and_file_quota_gate",
    "owner_private_directory_layout",
    "restricted_candidate_inventory",
    "sealed_exact_five_manifest",
    "exact_five_batch_plan",
    "bound_five_stage_scheduler_plan",
    "runtime_authority",
    "launch_authority",
    "body_transfer_and_download_grant",
    "dicom_extraction_grant",
    "echoprime_embedding_grant",
    "batch_preservation_grant",
    "canary_finalization_grant",
    "execution_authorization_packet",
    "qsub_executable_binding",
    "crc32c_runtime_bindings",
    "stage_worker_and_launcher_bindings",
    "unused_output_root",
    "durable_dispatch_claims",
    "predecessor_pass_receipts",
    "production_scientific_stage_implementations",
    "restricted_preservation_and_aggregate_finalization",
}
EXPECTED_BASELINE_MISSING_PRODUCERS = {
    "execute_scope_transition",
    "conflicting_process_and_scheduler_absence",
    "live_byte_and_file_quota_gate",
    "owner_private_directory_layout",
    "restricted_candidate_inventory",
    "execution_authorization_packet",
}
EXPECTED_BASELINE_MISSING_PRODUCER_ORDER = (
    "execute_scope_transition",
    "conflicting_process_and_scheduler_absence",
    "live_byte_and_file_quota_gate",
    "owner_private_directory_layout",
    "restricted_candidate_inventory",
    "execution_authorization_packet",
)
POST_REPAIR_EXPECTATION_KEYS = {
    "producer_must_become_available",
    "planned_tracked_modules",
    "realized_tracked_modules",
    "required_outcomes",
}
NON_FILE_REFERENCE_PREFIXES = {
    "git",
    "owner_private",
    "scc_runtime",
    "MISSING",
}


def _strict_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        assert key not in value, f"duplicate JSON key: {key}"
        value[key] = item
    return value


def _load() -> dict[str, Any]:
    return json.loads(
        TABLE_PATH.read_text(encoding="utf-8"), object_pairs_hook=_strict_pairs
    )


def _tracked(path: str) -> bool:
    result = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "--", path],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


def _tracked_or_pending_source(path: str) -> bool:
    if _tracked(path):
        return True
    result = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard", "--", path],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.splitlines() == [path]


def _assert_reference(reference: str, *, require_token: bool) -> None:
    prefix, separator, token = reference.partition("::")
    assert separator and prefix and token, reference
    if prefix in NON_FILE_REFERENCE_PREFIXES:
        return
    path = ROOT / prefix
    assert path.is_file() and not path.is_symlink(), reference
    assert _tracked(prefix), reference
    if require_token:
        assert token in path.read_text(encoding="utf-8"), reference


def _reference_is_tracked_file(reference: str) -> bool:
    prefix = reference.partition("::")[0]
    return prefix not in NON_FILE_REFERENCE_PREFIXES and _tracked(prefix)


def test_live_dependency_table_has_one_closed_deterministic_schema() -> None:
    table = _load()
    assert set(table) == TOP_LEVEL_KEYS
    assert table["schema_name"] == "lvef_c3_canary_live_dependencies_v1"
    assert table["schema_version"] == 1
    assert table["audit_baseline_commit"] == (
        "34c8ad795a7059f1d76b53a9a8be543132b095f7"
    )
    assert table["tracked_field_semantics"] == (
        "true means the current producer implementation is repository-tracked; "
        "owner-private output artifacts remain untracked by design"
    )
    assert table["canonical_entrypoint"] == "scripts/scc_run_lvef_c3_canary.sh"
    assert table["execute_mode"] == "--execute"

    dependencies = table["dependencies"]
    assert isinstance(dependencies, list)
    assert table["dependency_count"] == len(dependencies) == len(
        EXPECTED_DEPENDENCY_IDS
    )
    assert all(isinstance(item, dict) and set(item) == DEPENDENCY_KEYS for item in dependencies)
    ids = [item["dependency_id"] for item in dependencies]
    assert len(ids) == len(set(ids))
    assert set(ids) == EXPECTED_DEPENDENCY_IDS
    assert [PHASE_ORDER.index(item["phase"]) for item in dependencies] == sorted(
        PHASE_ORDER.index(item["phase"]) for item in dependencies
    )

    for item in dependencies:
        assert isinstance(item["dependency_id"], str) and item["dependency_id"]
        assert isinstance(item["prerequisite"], str) and item["prerequisite"]
        for field in (
            "artifact_paths",
            "producer",
            "consumer",
            "test_evidence",
            "authority_packet_roles",
        ):
            assert isinstance(item[field], list)
            assert all(isinstance(value, str) and value for value in item[field])
            assert len(item[field]) == len(set(item[field]))
        assert item["producer"] and item["consumer"]
        for field in ("producer_exists", "tracked", "exercised_by_tests"):
            assert type(item[field]) is bool
        assert item["exercised_by_tests"] is bool(item["test_evidence"])
        assert item["exercised_by_tests"] is item["producer_exists"]


def test_declared_producers_consumers_tracking_and_tests_are_real() -> None:
    table = _load()
    for item in table["dependencies"]:
        missing_markers = [
            reference.startswith("MISSING::") for reference in item["producer"]
        ]
        assert item["producer_exists"] is not any(missing_markers)
        for reference in item["producer"]:
            _assert_reference(reference, require_token=True)
        for reference in item["consumer"]:
            _assert_reference(reference, require_token=True)

        for path in item["artifact_paths"]:
            candidate = ROOT / path
            assert candidate.is_file() and not candidate.is_symlink(), path
            assert _tracked(path), path
        if item["tracked"]:
            assert item["producer_exists"] is True
            assert any(_tracked(path) for path in item["artifact_paths"]) or any(
                _reference_is_tracked_file(reference)
                for reference in item["producer"]
            ), item["dependency_id"]
        for path in item["test_evidence"]:
            assert path.startswith("tests/test_") and path.endswith(".py")
            candidate = ROOT / path
            assert candidate.is_file() and not candidate.is_symlink(), path
            assert _tracked(path), path


def test_table_covers_every_closed_execution_packet_role() -> None:
    table = _load()
    declared_roles = {
        role
        for item in table["dependencies"]
        for role in item["authority_packet_roles"]
    }
    assert declared_roles == set(execution_authority.PACKET_KEYS)

    role_holders: dict[str, set[str]] = {}
    for item in table["dependencies"]:
        for role in item["authority_packet_roles"]:
            role_holders.setdefault(role, set()).add(item["dependency_id"])
    assert role_holders["stage_authorizations"] == {
        "body_transfer_and_download_grant",
        "dicom_extraction_grant",
        "echoprime_embedding_grant",
        "batch_preservation_grant",
        "canary_finalization_grant",
    }
    assert role_holders["body_transfer_authorization"] == {
        "body_transfer_and_download_grant"
    }


def test_baseline_missing_producers_are_explicit_and_not_misreported_as_ready() -> None:
    table = _load()
    observed = {
        item["dependency_id"]
        for item in table["dependencies"]
        if item["producer_exists"] is False
    }
    assert observed == EXPECTED_BASELINE_MISSING_PRODUCERS
    assert tuple(table["missing_producer_dependency_ids"]) == (
        EXPECTED_BASELINE_MISSING_PRODUCER_ORDER
    )

    indexed = {item["dependency_id"]: item for item in table["dependencies"]}
    assert indexed["execute_scope_transition"]["tracked"] is False
    assert all(
        indexed[dependency_id]["exercised_by_tests"] is False
        and indexed[dependency_id]["test_evidence"] == []
        for dependency_id in EXPECTED_BASELINE_MISSING_PRODUCERS
    )
    assert indexed["execution_authorization_packet"]["authority_packet_roles"]


def test_post_repair_contract_closes_each_baseline_gap_without_rewriting_history() -> None:
    table = _load()
    expectations = table["post_repair_expectations"]
    assert isinstance(expectations, dict)
    assert set(expectations) == EXPECTED_BASELINE_MISSING_PRODUCERS
    for dependency_id, expectation in expectations.items():
        assert set(expectation) == POST_REPAIR_EXPECTATION_KEYS, dependency_id
        assert expectation["producer_must_become_available"] is True
        assert expectation["planned_tracked_modules"]
        assert expectation["realized_tracked_modules"] == expectation[
            "planned_tracked_modules"
        ]
        assert expectation["required_outcomes"]
        assert len(expectation["required_outcomes"]) == len(
            set(expectation["required_outcomes"])
        )
        for path in expectation["planned_tracked_modules"]:
            assert path.startswith("scripts/") and path.endswith(".py")
        for path in expectation["realized_tracked_modules"]:
            candidate = ROOT / path
            assert path in expectation["planned_tracked_modules"]
            assert candidate.is_file() and not candidate.is_symlink(), path
            assert _tracked_or_pending_source(path), path

    state = expectations["execute_scope_transition"]
    assert state["planned_tracked_modules"] == ["scripts/lvef_c3_canary_state.py"]
    materializer = "scripts/lvef_c3_canary_authority_materializer.py"
    assert all(
        expectations[dependency_id]["planned_tracked_modules"] == [materializer]
        for dependency_id in EXPECTED_BASELINE_MISSING_PRODUCERS
        - {"execute_scope_transition", "live_byte_and_file_quota_gate"}
    )
    assert expectations["live_byte_and_file_quota_gate"][
        "planned_tracked_modules"
    ] == [
        materializer,
        "scripts/capture_lvef_c3_post_reallocation_capacity.py",
    ]

    conflict_outcomes = set(
        expectations["conflicting_process_and_scheduler_absence"]["required_outcomes"]
    )
    assert {
        "process probe completed before materialization",
        "scheduler probe completed before materialization",
        "no conflicting canary process",
        "no conflicting scheduler job",
    }.issubset(conflict_outcomes)
    assert {
        "qsub and qstat identities sealed before scheduler probing",
        "sealed scheduler identities revalidated before dispatch",
    }.issubset(conflict_outcomes)
    quota_outcomes = set(
        expectations["live_byte_and_file_quota_gate"]["required_outcomes"]
    )
    assert {
        "sealed capacity authorities validate",
        "current no-body quota probe completed before materialization",
        "byte headroom is sufficient",
        "file-slot headroom is sufficient",
    }.issubset(quota_outcomes)
    packet_outcomes = set(
        expectations["execution_authorization_packet"]["required_outcomes"]
    )
    assert "creation timestamp is closed-schema and self-seal bound" in packet_outcomes
