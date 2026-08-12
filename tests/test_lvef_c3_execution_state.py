from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
import re
import sys
import tempfile
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import lvef_c3_execution_state as execution_state


EXPECTED_STATE = {
    "schema_name": "lvef_c3_execution_state_v1",
    "schema_version": 1,
    "branch": "codex/lvef-multitask-revalidation",
    "historical_base_commit": "23c74ccfd145ab9a423b6942a431a1894a34ab67",
    "starting_authority_commit": "6c9a00f8f25d785f85f506aea62b8d3f3e8e617c",
    "current_governing_commit": "GIT_HEAD",
    "execution_attempt_namespace": (
        "lvef_multitask_phase1ef_post_reallocation_lock_attempt"
    ),
    "logical_execution_attempt": 4,
    "logical_execution_governing_commit": (
        "6a814b3080f1159facf86ee60895117d187a41b7"
    ),
    "preparation_sequence_id": (
        "lvef_multitask_phase1ef_r2_attempt004_preparation_6a814b3_attempt_005"
    ),
    "preparation_environment_filename": "phase1ef_attempt004_authority.env",
    "preparation_environment_bytes": 9206,
    "next_unused_execution_attempt": 5,
    "attempt_004_execution_count": 1,
    "attempt_005_exists": False,
    "production_attempt_namespace": "lvef_c3_phase1ee_production_lock",
    "prior_production_attempt": 5,
    "next_unused_production_attempt": 6,
    "production_attempt_006_exists": False,
    "permitted_execution_scopes": (
        "preflight_only",
        "capture_current_environment",
    ),
}

OPERATIVE_CONSUMERS = (
    SCRIPTS / "finalize_lvef_phase1ef_d3.py",
    SCRIPTS / "lvef_c3_phase1ef_authority_manifest.py",
    SCRIPTS / "scc_capture_lvef_c3_post_reallocation_capacity.sh",
    SCRIPTS / "scc_execute_lvef_c3_phase1ef_attempt.sh",
    SCRIPTS / "scc_finalize_lvef_phase1ef_d3.sh",
    SCRIPTS / "scc_prepare_lvef_c3_phase1ef_environment.sh",
)
FULL_ID_ALLOWLIST = frozenset(
    {
        execution_state.DEFAULT_STATE_PATH,
        SCRIPTS / "scc_finalize_lvef_phase1ef_d3_canonical_2088832.sh",
    }
)


def _payload() -> dict[str, Any]:
    return json.loads(execution_state.DEFAULT_STATE_PATH.read_text(encoding="utf-8"))


def _assert_invalid(value: Any, expected_code: str) -> None:
    try:
        execution_state.validate_execution_state(value)
    except execution_state.ExecutionStateError as exc:
        assert exc.code == expected_code
        assert str(exc) == expected_code
    else:
        raise AssertionError(f"invalid execution state accepted: {expected_code}")


def test_checked_in_execution_state_has_exact_authoritative_invariants() -> None:
    state = execution_state.load_execution_state()

    assert asdict(state) == EXPECTED_STATE
    assert set(_payload()) == execution_state.STATE_KEYS
    assert state.logical_execution_attempt == 4
    assert state.next_unused_execution_attempt == 5
    assert state.attempt_004_execution_count == 1
    assert state.attempt_005_exists is False
    assert state.production_attempt_006_exists is False


def test_attempt_ids_and_scope_permissions_are_derived_exactly() -> None:
    state = execution_state.load_execution_state()

    assert state.logical_execution_attempt_id == (
        "lvef_multitask_phase1ef_post_reallocation_lock_attempt_004"
    )
    assert state.next_unused_execution_attempt_id == (
        "lvef_multitask_phase1ef_post_reallocation_lock_attempt_005"
    )
    assert state.next_unused_execution_attempt_tag == "005"
    assert state.prior_production_attempt_id == (
        "lvef_c3_phase1ee_production_lock_005"
    )
    assert state.next_unused_production_attempt_id == (
        "lvef_c3_phase1ee_production_lock_006"
    )
    assert state.permitted_execution_scopes == (
        "preflight_only",
        "capture_current_environment",
    )
    assert state.permits("preflight_only")
    assert state.permits("capture_current_environment")
    for forbidden_scope in (
        "cloud_object_body_request",
        "dicom_transfer",
        "scheduler_submission",
        "execute_exact_five_canary",
        "gpu_inference",
        "model_fitting",
        "prediction",
        "confirmatory_performance_access",
    ):
        assert not state.permits(forbidden_scope)


def test_preparation_sequence_is_opaque_and_never_selects_logical_attempt() -> None:
    for preparation_sequence_id in (
        "opaque_preparation_sequence",
        "opaque_preparation_sequence_attempt_004",
        "opaque_preparation_sequence_attempt_005",
        "opaque_preparation_sequence_attempt_999",
    ):
        value = _payload()
        value["preparation_sequence_id"] = preparation_sequence_id

        state = execution_state.validate_execution_state(value)

        assert state.preparation_sequence_id == preparation_sequence_id
        assert state.logical_execution_attempt == 4
        assert state.logical_execution_attempt_id.endswith("_004")
        assert state.next_unused_execution_attempt == 5
        assert state.next_unused_execution_attempt_id.endswith("_005")


def test_execution_state_schema_is_closed() -> None:
    missing = _payload()
    del missing["logical_execution_attempt"]
    _assert_invalid(missing, "EXECUTION_STATE_SCHEMA_NOT_CLOSED")

    unknown = _payload()
    unknown["logical_attempt_inferred_from_preparation"] = 5
    _assert_invalid(unknown, "EXECUTION_STATE_SCHEMA_NOT_CLOSED")


def test_execution_state_loader_rejects_duplicate_keys() -> None:
    duplicate = execution_state.DEFAULT_STATE_PATH.read_text(encoding="utf-8").replace(
        '"schema_version": 1,',
        '"schema_version": 1,\n  "schema_version": 1,',
        1,
    )
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "duplicate.yaml"
        path.write_text(duplicate, encoding="utf-8")
        try:
            execution_state.load_execution_state(path)
        except execution_state.ExecutionStateError as exc:
            assert exc.code == "EXECUTION_STATE_DUPLICATE_KEY"
        else:
            raise AssertionError("duplicate execution-state key accepted")


def test_execution_state_rejects_wrong_scalar_and_collection_types() -> None:
    mutations = (
        ("schema_name", None, "EXECUTION_STATE_SCHEMA_NAME_INVALID"),
        ("schema_version", True, "EXECUTION_STATE_SCHEMA_VERSION_INVALID"),
        ("branch", 4, "EXECUTION_STATE_BRANCH_INVALID"),
        ("historical_base_commit", 1, "EXECUTION_STATE_HISTORICAL_BASE_INVALID"),
        ("starting_authority_commit", None, "EXECUTION_STATE_STARTING_COMMIT_INVALID"),
        (
            "current_governing_commit",
            1,
            "EXECUTION_STATE_CURRENT_COMMIT_POLICY_INVALID",
        ),
        (
            "logical_execution_governing_commit",
            "not-a-commit",
            "EXECUTION_STATE_LOGICAL_COMMIT_INVALID",
        ),
        ("execution_attempt_namespace", 4, "EXECUTION_STATE_NAMESPACE_INVALID"),
        ("production_attempt_namespace", False, "EXECUTION_STATE_NAMESPACE_INVALID"),
        ("logical_execution_attempt", True, "EXECUTION_STATE_LOGICAL_ATTEMPT_INVALID"),
        ("next_unused_execution_attempt", "5", "EXECUTION_STATE_NEXT_ATTEMPT_INVALID"),
        (
            "attempt_004_execution_count",
            0,
            "EXECUTION_STATE_EXECUTION_COUNT_INVALID",
        ),
        (
            "prior_production_attempt",
            False,
            "EXECUTION_STATE_PRIOR_PRODUCTION_ATTEMPT_INVALID",
        ),
        (
            "next_unused_production_attempt",
            0,
            "EXECUTION_STATE_NEXT_PRODUCTION_ATTEMPT_INVALID",
        ),
        ("preparation_sequence_id", 5, "EXECUTION_STATE_PREPARATION_IDENTITY_INVALID"),
        (
            "preparation_environment_filename",
            ["phase1ef.env"],
            "EXECUTION_STATE_PREPARATION_IDENTITY_INVALID",
        ),
        ("preparation_environment_bytes", True, "EXECUTION_STATE_PREPARATION_SIZE_INVALID"),
        ("permitted_execution_scopes", tuple(), "EXECUTION_STATE_SCOPE_INVALID"),
        ("permitted_execution_scopes", ["preflight_only", 1], "EXECUTION_STATE_SCOPE_INVALID"),
    )
    for field, replacement, expected_code in mutations:
        value = _payload()
        value[field] = replacement
        _assert_invalid(value, expected_code)


def test_execution_state_rejects_identity_and_sequence_relation_failures() -> None:
    mutations = (
        ("logical_execution_attempt", 5),
        ("next_unused_execution_attempt", 6),
        ("attempt_004_execution_count", 2),
        ("attempt_005_exists", True),
        ("attempt_005_exists", 0),
        ("prior_production_attempt", 4),
        ("next_unused_production_attempt", 7),
        ("production_attempt_006_exists", True),
        ("production_attempt_006_exists", 0),
    )
    for field, replacement in mutations:
        value = _payload()
        value[field] = replacement
        _assert_invalid(value, "EXECUTION_STATE_IDENTITY_INVARIANT_INVALID")

    equal_namespaces = _payload()
    equal_namespaces["production_attempt_namespace"] = equal_namespaces[
        "execution_attempt_namespace"
    ]
    _assert_invalid(equal_namespaces, "EXECUTION_STATE_NAMESPACE_INVALID")


def test_execution_state_rejects_scope_expansion_reordering_or_omission() -> None:
    for scopes in (
        ["capture_current_environment", "preflight_only"],
        ["preflight_only"],
        ["preflight_only", "capture_current_environment", "dicom_transfer"],
    ):
        value = _payload()
        value["permitted_execution_scopes"] = scopes
        _assert_invalid(value, "EXECUTION_STATE_SCOPE_INVALID")


def test_operative_consumers_do_not_embed_independent_full_attempt_ids() -> None:
    # The canonical state file is the sole live source of these identities.  The
    # sealed historical D3 script is intentionally excluded from this operative
    # consumer list and remains immutable.
    full_attempt_id = re.compile(
        r"(?:lvef_multitask_phase1ef_post_reallocation_lock_attempt_00[456]"
        r"|lvef_c3_phase1ee_production_lock_00[456])"
    )
    state = execution_state.load_execution_state()
    violations: list[str] = []
    assert FULL_ID_ALLOWLIST == {
        ROOT / "configs/lvef_c3_execution_state_v1.yaml",
        SCRIPTS / "scc_finalize_lvef_phase1ef_d3_canonical_2088832.sh",
    }
    assert set(OPERATIVE_CONSUMERS).isdisjoint(FULL_ID_ALLOWLIST)
    for path in OPERATIVE_CONSUMERS:
        payload = path.read_text(encoding="utf-8")
        if full_attempt_id.search(payload) is not None:
            violations.append(str(path.relative_to(ROOT)))
        if state.preparation_sequence_id in payload:
            violations.append(str(path.relative_to(ROOT)))

    assert violations == []
