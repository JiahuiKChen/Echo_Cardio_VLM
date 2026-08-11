from __future__ import annotations

# SYNTHETIC_TEST_DATA_ONLY: these tests never execute the SCC capture or network.
import copy
from contextlib import redirect_stdout
from decimal import Decimal, ROUND_HALF_UP
import io
import json
from pathlib import Path
import sys
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import capture_lvef_c3_post_reallocation_capacity as capacity
import lvef_multitask_analysis_modes as analysis_modes


def _native() -> bytes:
    return (
        "rproject_mimicecho root FILESET 10690224 52428800 0 0 none | "
        "47379 1638400 0 0 none\n"
        "rprojectnb_mimicecho root FILESET 147117696 2044723200 0 0 none | "
        "106407 33554432 0 0 none\n"
    ).encode()


def _aggregate() -> dict[str, object]:
    rq = capacity.EXPECTED_RESEARCH_QUOTA_KIB * 1024
    bq = capacity.EXPECTED_BACKED_QUOTA_KIB * 1024
    value: dict[str, object] = {key: False for key in capacity.AGGREGATE_KEYS}
    value.update(
        {
            "schema_version": capacity.SCHEMA_VERSION,
            "artifact_type": capacity.AGGREGATE_TYPE,
            "status": capacity.AGGREGATE_STATUS,
            "attempt_id": "lvef_multitask_phase1ef_post_reallocation_lock_attempt_003",
            "governing_commit": "a" * 40,
            "created_at_utc": "2026-08-11T12:00:00+00:00",
            "units": "BYTES_FROM_NATIVE_KIB_EXACT_INTEGER",
            "quota_display_unit_ruling": "BINARY_GIB_ROUNDED_SECONDARY_ONLY",
            "restricted_receipt_size_bytes": 10,
            "restricted_receipt_sha256": "b" * 64,
            "pquota_executable_sha256": capacity.EXPECTED_PQUOTA_SHA256,
            "pquota_executable_authority_status": "PASS_TRUSTED_ROOT_CONTROLLED",
            "pquota_display_crosscheck": capacity.DISPLAY_CROSSCHECK_PASS,
            "pquota_display_crosscheck_reason": "MATCHED_NATIVE_AUTHORITY",
            "pquota_display_backed_project_row_matches": 1,
            "pquota_display_research_project_row_matches": 1,
            "pquota_display_rounding_rule":
                "DECIMAL_HALF_UP_AT_OBSERVED_PRECISION_0_TO_6",
            **{
                capacity.AGGREGATE_GATE_STATUS_FIELDS[key]:
                    capacity.GATE_EVALUATION_PASS
                for key in capacity.CAPACITY_GATE_KEYS
            },
            "research_quota_bytes": rq,
            "backed_quota_bytes": bq,
            "research_quota_margin_above_minimum_bytes": rq - capacity.MINIMUM_EFFECTIVE_QUOTA_BYTES,
            "research_quota_slack_after_projected_peak_bytes": rq - capacity.PROJECTED_PEAK_BYTES,
            "research_margin_beyond_200gb_reserve_bytes": rq - capacity.PROJECTED_PEAK_BYTES - capacity.REQUIRED_FREE_HEADROOM_BYTES,
            "pretransfer_research_write_bound_bytes": capacity.PRETRANSFER_RESEARCH_WRITE_BOUND_BYTES,
            "research_remaining_write_bytes": capacity.PROJECTED_PEAK_BYTES,
            "research_filesystem_available_bytes": capacity.PROJECTED_PEAK_BYTES
            + capacity.REQUIRED_FREE_HEADROOM_BYTES
            + capacity.PRETRANSFER_RESEARCH_WRITE_BOUND_BYTES,
            "research_physical_required_available_bytes": capacity.PROJECTED_PEAK_BYTES
            + capacity.REQUIRED_FREE_HEADROOM_BYTES
            + capacity.PRETRANSFER_RESEARCH_WRITE_BOUND_BYTES,
            "research_physical_slack_bytes": 0,
        }
    )
    for key in (
        "prior_authorities_hash_verified", "prior_authorities_closed_schema_verified",
        "pquota_to_restricted_mount_reconciliation_verified",
        "pquota_display_fileset_mapping_verified",
        "pquota_current_not_snapshot_mode_verified",
        "research_mount_fsroot_is_root", "backed_mount_fsroot_is_root",
        "mounted_filesystems_distinct", "mount_targets_distinct",
        "filesystem_devices_distinct", "research_quota_gate_passed",
        "physical_filesystem_capacity_gate_passed",
        "projected_200gb_reserve_gate_passed", "research_file_quota_gate_passed",
        "backed_control_tier_byte_gate_passed",
        "backed_control_tier_file_gate_passed", "backed_control_tier_gate_passed",
        "purchased_saas_allocation_remains_on_research",
        "snapshot_capacity_double_counting_avoided",
    ):
        value[key] = True
    for key in (
        "cloud_requests", "scheduler_jobs_submitted", "dicom_bodies_downloaded",
        "files_moved", "files_deleted",
    ):
        value[key] = 0
    value["control_write_binding_evaluated_by_capacity_receipt"] = False
    value["additional_project_quota_row_for_same_principal_observed"] = False
    value["snapshot_presence_independently_enumerated"] = False
    value["snapshot_accounting_ruling"] = (
        "NO_SEPARATE_SNAPSHOT_ADDITION_EFFECTIVE_QUOTA_AND_DF_GOVERN"
    )
    return value


def _receipt_with_unavailable_display() -> dict[str, object]:
    empty_sha = capacity._sha(b"")

    def command(role: str) -> dict[str, object]:
        return {
            "role": role,
            "argv": [f"/{role}"],
            "argv_sha256": "a" * 64,
            "executable_sha256": "b" * 64,
            "executable_size_bytes": 1,
            "exit_status": 0,
            "stdout_bytes": 0,
            "stdout_sha256": empty_sha,
            "stdout_text": "",
            "stderr_bytes": 0,
            "stderr_sha256": empty_sha,
        }

    commands = {
        role: command(role)
        for role in (
            "research_findmnt", "backed_findmnt", "research_df", "backed_df"
        )
    }
    commands["pquota"] = {
        "role": "pquota",
        "argv": ["pquota", "-u", "PROJECT_PLACEHOLDER"],
        "argv_sha256": "c" * 64,
        "executable_sha256": "UNAVAILABLE",
        "executable_size_bytes": 0,
        "exit_status": -1,
        "stdout_bytes": 0,
        "stdout_sha256": empty_sha,
        "stdout_text": "",
        "stderr_bytes": 0,
        "stderr_sha256": empty_sha,
        "availability_status": "UNAVAILABLE_NONBLOCKING",
        "availability_reason": "EXECUTABLE_NOT_FOUND",
    }
    identities = {
        role: {
            "path_sha256": "d" * 64,
            "resolved_path_sha256": "e" * 64,
            "device": index,
            "inode": index,
            "is_symlink": False,
        }
        for index, role in enumerate(("backed", "research"), start=1)
    }
    mounts = {
        role: {
            "source_sha256": "f" * 64,
            "target_sha256": "1" * 64,
            "fsroot_sha256": "2" * 64,
            "identity_sha256": "3" * 64,
            "source": f"synthetic:{role}",
            "target": f"/synthetic/{role}",
            "fsroot": "/",
            "fstype": "syntheticfs",
            "bind": False,
        }
        for role in ("backed", "research")
    }
    df_values = {
        role: {"total": 3, "used": 1, "available": 2}
        for role in ("backed", "research")
    }
    return {
        "schema_version": capacity.SCHEMA_VERSION,
        "artifact_type": capacity.RECEIPT_TYPE,
        "status": capacity.RECEIPT_STATUS,
        "attempt_id":
            "lvef_multitask_phase1ef_post_reallocation_lock_attempt_003",
        "governing_commit": "a" * 40,
        "captured_at_utc": "2026-08-11T12:00:00+00:00",
        "capture_identity": {},
        "native_quota_authority": {
            "record_unit": "KIB",
            "bytes_per_kib": 1024,
            "rows": capacity._parse_native_quota(_native()),
        },
        "commands": commands,
        "paths": {"identities": identities, "mounts": mounts, "df": df_values},
        "prior_authorities": {},
        "frozen_plan": {
            "selected_source_bytes": capacity.SELECTED_SOURCE_BYTES,
            "projected_peak_bytes": capacity.PROJECTED_PEAK_BYTES,
            "required_free_headroom_bytes": capacity.REQUIRED_FREE_HEADROOM_BYTES,
            "minimum_effective_quota_bytes": capacity.MINIMUM_EFFECTIVE_QUOTA_BYTES,
            "preferred_research_quota_bytes": capacity.PREFERRED_RESEARCH_QUOTA_BYTES,
            "prespecified_control_burden_bytes":
                capacity.PRESPECIFIED_CONTROL_BURDEN_BYTES,
            "pretransfer_research_write_bound_bytes":
                capacity.PRETRANSFER_RESEARCH_WRITE_BOUND_BYTES,
        },
        "pquota_display_crosscheck": capacity._display_result(
            capacity.DISPLAY_CROSSCHECK_UNAVAILABLE,
            "COMMAND_UNAVAILABLE",
            {},
        ),
        "gate_evaluation_status": {
            key: capacity.GATE_EVALUATION_PASS
            for key in capacity.CAPACITY_GATE_KEYS
        },
        "no_mutation_attestations": {
            "cloud_requests": 0,
            "object_listing_repeated": False,
            "storage_inventory_repeated": False,
            "scheduler_jobs_submitted": 0,
            "dicom_bodies_downloaded": 0,
            "real_dicom_extraction": False,
            "echoprime_inference": False,
            "model_fitting": False,
            "confirmatory_performance_accessed": False,
            "quota_changed": False,
            "files_moved": 0,
            "files_deleted": 0,
            "full_c3_authorized": False,
        },
    }


def _observed_display() -> str:
    return (
        ROOT / "tests" / "fixtures" / "phase1ef"
        / "pquota_display_observed_sanitized.txt"
    ).read_text().replace("PROJECT_PLACEHOLDER", "mimicecho")


def _display_with_precision(places: int) -> str:
    text = _observed_display()
    for role in ("backed", "research"):
        native = capacity._parse_native_quota(_native())[role]
        value = Decimal(int(native["usage_kib"])) / Decimal(1024 ** 2)
        shown = format(
            value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP),
            f".{places}f",
        )
        old = "10.19" if role == "backed" else "140.30"
        text = text.replace(old, shown)
    return text


def _expect(code: str, function) -> None:
    try:
        function()
    except capacity.PostReallocationCapacityError as exc:
        assert exc.code == code
    else:
        raise AssertionError(f"Expected {code}")


def test_native_kib_rows_produce_exact_binary_scaled_bytes_and_file_counts() -> None:
    rows = capacity._parse_native_quota(_native())
    assert rows["research"]["quota_kib"] * 1024 == 2_093_796_556_800
    assert rows["backed"]["quota_kib"] * 1024 == 53_687_091_200
    assert rows["research"]["files_used"] == 106_407
    assert rows["backed"]["file_quota"] == 1_638_400


def test_native_quota_rejects_duplicate_missing_or_changed_allocation() -> None:
    _expect("NATIVE_QUOTA_ROW_NOT_UNIQUE", lambda: capacity._parse_native_quota(_native() + _native().splitlines()[0] + b"\n"))
    _expect("NATIVE_QUOTA_ROWS_MISSING", lambda: capacity._parse_native_quota(_native().splitlines()[0] + b"\n"))
    _expect("NATIVE_QUOTA_ALLOCATION_UNEXPECTED", lambda: capacity._parse_native_quota(_native().replace(b"2044723200", b"2044723199")))
    _expect(
        "NATIVE_QUOTA_FILESET_SCOPE_INVALID",
        lambda: capacity._parse_native_quota(
            _native().replace(b"rproject_mimicecho root", b"rproject_mimicecho mimicecho")
        ),
    )
    extra = b"snapshot_mimicecho root FILESET 0 1 0 0 none | 0 1 0 0 none\n"
    _expect("NATIVE_QUOTA_ADDITIONAL_PRINCIPAL_ROW", lambda: capacity._parse_native_quota(_native() + extra))


def test_observed_pquota_display_schema_passes_as_secondary_crosscheck() -> None:
    rows = capacity._parse_native_quota(_native())
    result = capacity._parse_pquota(_observed_display(), rows)
    assert result["status"] == capacity.DISPLAY_CROSSCHECK_PASS
    assert result["project_row_match_counts"] == {"backed": 1, "research": 1}
    assert result["usage_decimal_places"] == {"backed": 2, "research": 2}
    owner_context_aliases = _observed_display().replace(
        "/rproject/", "/project/"
    ).replace("/rprojectnb/", "/projectnb/")
    assert capacity._parse_pquota(owner_context_aliases, rows)["status"] == (
        capacity.DISPLAY_CROSSCHECK_PASS
    )


def test_pquota_display_tolerates_headers_whitespace_children_and_precision() -> None:
    rows = capacity._parse_native_quota(_native())
    observed = _observed_display()
    variants = [
        observed,
        observed.replace(" ", "\t"),
        "\n".join("   " + line if line.startswith("/rproject") else line for line in observed.splitlines()),
        observed.replace("\n", "\r\n"),
        _display_with_precision(0),
        _display_with_precision(1),
        _display_with_precision(3),
        _display_with_precision(6),
    ]
    for text in variants:
        assert capacity._parse_pquota(text, rows)["status"] == capacity.DISPLAY_CROSSCHECK_PASS


def test_pquota_display_missing_or_unparseable_is_nonblocking_unavailable() -> None:
    rows = capacity._parse_native_quota(_native())
    observed = _observed_display()
    variants = [
        "",
        observed.replace("/rproject/mimicecho", "/rproject/mimicecho-near"),
        observed.replace("/rprojectnb/mimicecho", "/rprojectnb/mimicecho-near"),
        observed.replace(" 50 ", " malformed ", 1),
    ]
    for text in variants:
        result = capacity._parse_pquota(text, rows)
        assert result["status"] == capacity.DISPLAY_CROSSCHECK_UNAVAILABLE
        assert result["reason"] == "EXPECTED_ROWS_MISSING_OR_UNPARSEABLE"
    unavailable = capacity._parse_pquota(observed, rows, command_available=False)
    assert unavailable["status"] == capacity.DISPLAY_CROSSCHECK_UNAVAILABLE
    assert unavailable["reason"] == "COMMAND_UNAVAILABLE"


def test_missing_display_command_remains_receipt_eligible() -> None:
    with mock.patch.object(capacity.shutil, "which", return_value=None):
        command = capacity._capture_optional_pquota("PROJECT_PLACEHOLDER")
    assert command["availability_status"] == "UNAVAILABLE_NONBLOCKING"
    assert command["availability_reason"] == "EXECUTABLE_NOT_FOUND"
    assert command["stdout_bytes"] == 0
    receipt = _receipt_with_unavailable_display()
    capacity.validate_receipt_output(receipt)
    contradictory = copy.deepcopy(receipt)
    contradictory["commands"]["pquota"]["availability_reason"] = "AVAILABLE"
    _expect(
        "CAPACITY_RECEIPT_PQUOTA_COMMAND_INVALID",
        lambda: capacity.validate_receipt_output(contradictory),
    )


def test_pquota_display_ignores_unrelated_project_and_subordinate_rows() -> None:
    rows = capacity._parse_native_quota(_native())
    observed = _observed_display()
    extra = (
        observed
        + "\n/rproject/SYNTHETIC_OTHER 1 2 0.00 3\n"
        + "    SYNTHETIC_CHILD 0.00 0\n"
    )
    assert capacity._parse_pquota(extra, rows)["status"] == capacity.DISPLAY_CROSSCHECK_PASS


def test_pquota_display_duplicate_and_native_contradictions_block() -> None:
    rows = capacity._parse_native_quota(_native())
    observed = _observed_display()
    backed = next(line for line in observed.splitlines() if line.startswith("/rproject/"))
    duplicate = capacity._parse_pquota(observed + "\n" + backed + "\n", rows)
    assert duplicate["status"] == capacity.DISPLAY_CROSSCHECK_FAIL
    assert duplicate["reason"] == "DUPLICATE_EXPECTED_PROJECT_ROW"
    assert duplicate["project_row_match_counts"] == {"backed": 2, "research": 1}
    alias_duplicate = capacity._parse_pquota(
        observed + "\n" + backed.replace("/rproject/", "/project/") + "\n",
        rows,
    )
    assert alias_duplicate["status"] == capacity.DISPLAY_CROSSCHECK_FAIL
    assert alias_duplicate["reason"] == "DUPLICATE_EXPECTED_PROJECT_ROW"
    changes = {
        " 1950 ": (" 1951 ", "NOMINAL_QUOTA_CONTRADICTION"),
        " 33554432 ": (" 33554431 ", "FILE_QUOTA_CONTRADICTION"),
        " 140.30 ": (" 140.29 ", "ROUNDED_USAGE_CONTRADICTION"),
        " 106407": (" 106408", "FILE_USAGE_CONTRADICTION"),
    }
    for old, (new, reason) in changes.items():
        result = capacity._parse_pquota(observed.replace(old, new, 1), rows)
        assert result["status"] == capacity.DISPLAY_CROSSCHECK_FAIL
        assert result["reason"] == reason

    # A malformed row cannot mask a material contradiction in the other role.
    mixed = observed.replace(" 50 ", " malformed ", 1).replace(
        " 1950 ", " 1951 ", 1
    )
    result = capacity._parse_pquota(mixed, rows)
    assert result["status"] == capacity.DISPLAY_CROSSCHECK_FAIL
    assert result["reason"] == "NOMINAL_QUOTA_CONTRADICTION"


def test_display_status_reason_pairs_are_closed() -> None:
    _expect(
        "PQUOTA_DISPLAY_RESULT_INTERNAL_INVALID",
        lambda: capacity._display_result(
            capacity.DISPLAY_CROSSCHECK_PASS,
            "NOMINAL_QUOTA_CONTRADICTION",
            {"backed": 1, "research": 1},
        ),
    )
    _expect(
        "PQUOTA_DISPLAY_RESULT_INTERNAL_INVALID",
        lambda: capacity._display_result(
            capacity.DISPLAY_CROSSCHECK_UNAVAILABLE,
            "MATCHED_NATIVE_AUTHORITY",
            {"backed": 1, "research": 1},
        ),
    )


def test_gate_reporting_distinguishes_fail_from_not_evaluated() -> None:
    values = {key: True for key in capacity.CAPACITY_GATE_KEYS}
    values["physical_filesystem_capacity_gate"] = False
    status = capacity._gate_evaluation_status(values)
    assert status["research_quota_gate"] == capacity.GATE_EVALUATION_PASS
    assert status["physical_filesystem_capacity_gate"] == capacity.GATE_EVALUATION_FAIL
    assert set(capacity._not_evaluated_gate_status().values()) == {
        capacity.GATE_EVALUATION_NOT_EVALUATED
    }

    class SyntheticParser:
        @staticmethod
        def parse_args(_argv):
            return object()

    output = io.StringIO()
    with mock.patch.object(capacity, "build_parser", return_value=SyntheticParser()), mock.patch.object(
        capacity, "capture", side_effect=capacity.PostReallocationCapacityError("SYNTHETIC_UPSTREAM_FAILURE")
    ), redirect_stdout(output):
        assert capacity.main([]) == 2
    payload = json.loads(output.getvalue())
    assert set(payload["gate_evaluation_status"].values()) == {
        capacity.GATE_EVALUATION_NOT_EVALUATED
    }
    assert payload["pquota_display_crosscheck"] == capacity.GATE_EVALUATION_NOT_EVALUATED

    output = io.StringIO()
    with mock.patch.object(
        capacity, "build_parser", return_value=SyntheticParser()
    ), mock.patch.object(capacity, "capture", return_value=_aggregate()), redirect_stdout(output):
        assert capacity.main([]) == 0
    payload = json.loads(output.getvalue())
    assert set(payload["gate_evaluation_status"]) == set(capacity.CAPACITY_GATE_KEYS)
    assert set(payload["gate_evaluation_status"].values()) == {
        capacity.GATE_EVALUATION_PASS
    }


def test_findmnt_and_df_reject_aliasing_bind_and_byte_mismatch() -> None:
    text = json.dumps({"filesystems": [{"source": "/dev/synthetic-a", "target": "/restricted/projectnb", "fstype": "gpfs", "options": "rw", "fsroot": "/"}]})
    mount = capacity._parse_findmnt(text, Path("/restricted/projectnb/mimicecho"))
    assert mount["bind"] is False
    assert capacity._parse_df(
        "Filesystem 1B-blocks Used Avail Mounted on\n/dev/synthetic-a 2200000000000 100000000000 2100000000000 /restricted/projectnb\n",
        mount,
    )["available"] == 2_100_000_000_000
    bind = capacity._parse_findmnt(text.replace('"rw"', '"rw,bind"'), Path("/restricted/projectnb/mimicecho"))
    assert bind["bind"] is True
    subtree = capacity._parse_findmnt(text.replace('"fsroot": "/"', '"fsroot": "/subtree"'), Path("/restricted/projectnb/mimicecho"))
    assert subtree["bind"] is True
    _expect("DF_MOUNT_IDENTITY_MISMATCH", lambda: capacity._parse_df("Filesystem 1B-blocks Used Avail Mounted on\n/dev/other 2 1 1 /restricted/projectnb\n", mount))
    _expect(
        "FINDMNT_JSON_INVALID",
        lambda: capacity._parse_findmnt("", Path("/restricted/projectnb/mimicecho")),
    )
    _expect("DF_ROW_COUNT_INVALID", lambda: capacity._parse_df("", mount))


def test_pquota_restricted_mount_reconciliation_is_not_a_hardcoded_boolean() -> None:
    native = capacity._parse_native_quota(_native())
    paths = {
        role: {
            "resolved_path_sha256": capacity._sha(str(path).encode()),
            "is_symlink": False,
        }
        for role, path in capacity.EXPECTED_RESTRICTED_PATHS.items()
    }
    mounts = {
        "backed": {"source": "host:/gpfs4/rproject", "target": "/restricted/project", "fsroot": "/", "bind": False},
        "research": {"source": "host:/gpfs4/rprojectnb", "target": "/restricted/projectnb", "fsroot": "/", "bind": False},
    }
    capacity._validate_pquota_restricted_mount_reconciliation(
        native=native, paths=paths, mounts=mounts
    )
    changed = copy.deepcopy(mounts)
    changed["research"]["fsroot"] = "/subtree"
    _expect(
        "PQUOTA_RESTRICTED_MOUNT_RECONCILIATION_FAILED",
        lambda: capacity._validate_pquota_restricted_mount_reconciliation(
            native=native, paths=paths, mounts=changed
        ),
    )


def test_capacity_aggregate_is_closed_and_arithmetic_fail_closed() -> None:
    value = _aggregate()
    capacity.validate_aggregate_output(value)
    policy, _ = analysis_modes.load_policy(
        ROOT / "configs/lvef_multitask_safe_export_policy.yaml"
    )
    result = analysis_modes.validate_candidate_bytes(
        (json.dumps(value, sort_keys=True) + "\n").encode(),
        filename="lvef_c3_post_reallocation_capacity.summary.json",
        profile_name="phase1ef_post_reallocation_capacity_json",
        policy=policy,
    )
    assert result["status"] == "PASS"
    changed = copy.deepcopy(value)
    changed["unexpected"] = False
    _expect("CAPACITY_AGGREGATE_SCHEMA_NOT_CLOSED", lambda: capacity.validate_aggregate_output(changed))
    changed = copy.deepcopy(value)
    changed["research_margin_beyond_200gb_reserve_bytes"] += 1
    _expect("CAPACITY_AGGREGATE_ARITHMETIC_INVALID", lambda: capacity.validate_aggregate_output(changed))

    unavailable = copy.deepcopy(value)
    unavailable["pquota_display_crosscheck"] = capacity.DISPLAY_CROSSCHECK_UNAVAILABLE
    unavailable["pquota_display_crosscheck_reason"] = "EXPECTED_ROWS_MISSING_OR_UNPARSEABLE"
    unavailable["pquota_display_backed_project_row_matches"] = 0
    unavailable["pquota_display_research_project_row_matches"] = 0
    unavailable["pquota_display_fileset_mapping_verified"] = False
    capacity.validate_aggregate_output(unavailable)

    inconsistent = copy.deepcopy(value)
    inconsistent[
        capacity.AGGREGATE_GATE_STATUS_FIELDS["research_quota_gate"]
    ] = (
        capacity.GATE_EVALUATION_FAIL
    )
    _expect(
        "CAPACITY_AGGREGATE_GATE_STATUS_MISMATCH",
        lambda: capacity.validate_aggregate_output(inconsistent),
    )

    extra_nested = copy.deepcopy(value)
    extra_nested["gate_evaluation_status"] = {
        "research_quota_gate": capacity.GATE_EVALUATION_PASS,
        "unexpected_safe_key": capacity.GATE_EVALUATION_PASS,
    }
    _expect(
        "CAPACITY_AGGREGATE_SCHEMA_NOT_CLOSED",
        lambda: capacity.validate_aggregate_output(extra_nested),
    )
    try:
        analysis_modes.validate_candidate_bytes(
            (json.dumps(extra_nested, sort_keys=True) + "\n").encode(),
            filename="lvef_c3_post_reallocation_capacity.summary.json",
            profile_name="phase1ef_post_reallocation_capacity_json",
            policy=policy,
        )
    except analysis_modes.SafetyPolicyError:
        pass
    else:
        raise AssertionError("Safe export accepted an unapproved nested gate object")


def test_capture_contract_has_no_storage_inventory_cloud_or_scheduler_path() -> None:
    source = (ROOT / "scripts" / "capture_lvef_c3_post_reallocation_capacity.py").read_text()
    wrapper = (ROOT / "scripts" / "scc_capture_lvef_c3_post_reallocation_capacity.sh").read_text()
    assert "objects.list" not in source
    assert "alt=media" not in source
    assert "qsub" not in source
    assert " du " not in wrapper
    assert "find " not in wrapper
    assert "pquota" in source and "findmnt" in source and "df" in source
    assert (
        "--attempt-id lvef_multitask_phase1ef_post_reallocation_lock_attempt_003"
        in wrapper
    )
    assert "--attempt-id lvef_multitask_phase1ef_post_reallocation_lock_attempt_001" not in wrapper
    assert "--attempt-id lvef_multitask_phase1ef_post_reallocation_lock_attempt_002" not in wrapper
