#!/usr/bin/env python3
"""Dependency-light R5E raw-retirement and capacity-binding regressions."""
from __future__ import annotations

import ast
import csv
import copy
from dataclasses import replace
import errno
import hashlib
import inspect
import io
import json
import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
import sys

sys.path.insert(0, str(SCRIPTS))

import capture_lvef_c3_post_reallocation_capacity as capacity
import lvef_c3_full_sequential as sequential
import retire_lvef_c3_older_raw_duplicates as raw_retirement


def _expect(code: str, function) -> None:
    try:
        function()
    except Exception as exc:
        assert getattr(exc, "code", str(exc)) == code
    else:  # pragma: no cover
        raise AssertionError(f"expected {code}")


def _private_file(path: Path, payload: bytes) -> None:
    path.write_bytes(payload)
    os.chmod(path, 0o600)


def _private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, 0o700)


def _synthetic_raw_leaf(
    root: Path, *, nested: bool = False
) -> tuple[Path, dict[str, int]]:
    leaf = root / "raw" / "c3_batch_000" / "objects"
    _private_directory(leaf)
    expected = {"a" * 64: 3, "b" * 64: 4}
    for ordinal, (key, size) in enumerate(expected.items()):
        parent = leaf
        if nested:
            parent = leaf / f"layout_{ordinal}"
            _private_directory(parent)
        _private_file(parent / f"{key}.dcm", bytes([ordinal + 1]) * size)
    return leaf, expected


def _synthetic_raw_authority(
    older: Path,
    leaf: Path,
    expected: dict[str, int],
):
    with mock.patch.object(
        raw_retirement, "_mountinfo_mountpoints", return_value=frozenset()
    ):
        return raw_retirement._raw_object_leaf_authority(
            older,
            leaf,
            batch_id="c3_batch_000",
            expected_sizes=expected,
        )


def _transition_receipt_fixture(root: Path) -> SimpleNamespace:
    attempt_id = raw_retirement.OLDER_ATTEMPT_ID
    batch_id = "c3_batch_000"
    attempt_root = root / attempt_id
    receipts_root = attempt_root / "raw" / batch_id / "receipts"
    _private_directory(receipts_root)
    source_key = "a" * 64
    required_name = f"{source_key}.verification.json"
    _private_file(receipts_root / required_name, b"required-receipt\n")
    runtime = {"batch_plan_sha256": "1" * 64}
    launch = {"sealed_launch": "2" * 64}
    verification_map = {source_key: "3" * 64}
    manifest_sha = "4" * 64
    documents = {
        raw_retirement.TRANSITION_RECEIPT_BASENAMES[0]: {
            "schema_version": 2,
            "receipt_type": "lvef_c3_state_transition_v2",
            "attempt_id": attempt_id,
            "batch_id": batch_id,
            "from_state": "PLANNED",
            "to_state": "DOWNLOAD_IN_PROGRESS",
            "status": "PASS",
            "authority": runtime,
            "input_receipt_sha256": [runtime["batch_plan_sha256"]],
            "output_manifest_sha256": raw_retirement.core.canonical_json_sha256(
                launch
            ),
        }
    }
    start_payload = raw_retirement.core.canonical_json_bytes(
        documents[raw_retirement.TRANSITION_RECEIPT_BASENAMES[0]]
    )
    start_sha = hashlib.sha256(start_payload).hexdigest()
    documents[raw_retirement.TRANSITION_RECEIPT_BASENAMES[1]] = {
        "schema_version": 2,
        "receipt_type": "lvef_c3_state_transition_v2",
        "attempt_id": attempt_id,
        "batch_id": batch_id,
        "from_state": "DOWNLOAD_IN_PROGRESS",
        "to_state": "DOWNLOAD_VERIFIED",
        "status": "PASS",
        "authority": runtime,
        "input_receipt_sha256": [start_sha, *sorted(verification_map.values())],
        "output_manifest_sha256": manifest_sha,
    }
    events = []
    for document in documents.values():
        payload = raw_retirement.core.canonical_json_bytes(document)
        basename = (
            raw_retirement.TRANSITION_RECEIPT_BASENAMES[0]
            if document["from_state"] == "PLANNED"
            else raw_retirement.TRANSITION_RECEIPT_BASENAMES[1]
        )
        _private_file(receipts_root / basename, payload)
        events.append(
            {
                "from_state": document["from_state"],
                "to_state": document["to_state"],
                "receipt_sha256": hashlib.sha256(payload).hexdigest(),
            }
        )
    return SimpleNamespace(
        attempt_root=attempt_root,
        receipts_root=receipts_root,
        batch_id=batch_id,
        attempt_id=attempt_id,
        required_name=required_name,
        runtime=runtime,
        launch=launch,
        manifest_sha=manifest_sha,
        documents=documents,
        ledger_batch={
            "events": events,
            "download_verification_receipts": verification_map,
            "download_recovery_receipts": {},
        },
    )


def _validate_transition_fixture(fixture: SimpleNamespace):
    with mock.patch.object(
        raw_retirement.core,
        "validate_runtime_authority",
        side_effect=lambda authority: dict(authority),
    ):
        return raw_retirement._retained_receipt_tree_authority(
            fixture.attempt_root,
            fixture.receipts_root,
            batch_id=fixture.batch_id,
            attempt_id=fixture.attempt_id,
            expected_receipt_names={fixture.required_name},
            ledger_batch=fixture.ledger_batch,
            runtime_authority=fixture.runtime,
            launch_authority=fixture.launch,
            download_manifest_sha256=fixture.manifest_sha,
        )


def _download_rows() -> tuple[list[dict[str, str]], dict[str, dict[str, object]]]:
    rows = [
        {
            "subject_id": "11",
            "study_id": "21",
            "source_relative_path": "files/p11/s21/a.dcm",
            "download_ok": "true",
            "observed_sha256": "c" * 64,
            "physical_source_key": "a" * 64,
        },
        {
            "subject_id": "12",
            "study_id": "22",
            "source_relative_path": "files/p12/s22/b.dcm",
            "download_ok": "true",
            "observed_sha256": "d" * 64,
            "physical_source_key": "b" * 64,
        },
    ]
    expected = {
        row["physical_source_key"]: {
            "source_object_key": row["physical_source_key"],
            "subject_id": row["subject_id"],
            "study_id": row["study_id"],
            "source_relative_path": row["source_relative_path"],
        }
        for row in rows
    }
    return rows, expected


def _download_payload(
    rows: list[dict[str, str]],
    *,
    header: tuple[str, ...] | None = None,
) -> bytes:
    columns = header or raw_retirement.VERIFIED_DOWNLOAD_MANIFEST_HEADER_V1
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(columns)
    for row in rows:
        writer.writerow([row.get(key, "") for key in columns])
    return output.getvalue().encode("utf-8")


def _parse_download_fixture(
    payload: bytes,
    expected: dict[str, dict[str, object]],
    *,
    role: str = "OLDER_BATCH_1",
    expected_attempt_id: str | None = None,
    expected_batch_id: str | None = None,
):
    base = raw_retirement.VERIFIED_DOWNLOAD_MANIFEST_ROLES[role]
    schema = replace(
        base,
        expected_row_count=len(expected),
        manifest_bytes=len(payload),
        manifest_sha256=hashlib.sha256(payload).hexdigest(),
    )
    with mock.patch.dict(
        raw_retirement.VERIFIED_DOWNLOAD_MANIFEST_ROLES,
        {role: schema},
    ):
        return raw_retirement._validate_verified_download_manifest_payload(
            payload,
            schema=schema,
            expected_attempt_id=expected_attempt_id or schema.attempt_id,
            expected_batch_id=expected_batch_id or schema.batch_id,
            expected=expected,
        )


def test_r5e_retirement_scope_and_constants_are_exact() -> None:
    assert raw_retirement.TARGET_BATCHES == (
        "c3_batch_000", "c3_batch_001"
    )
    assert raw_retirement.EXPECTED_DELETE_FILES == 37_068
    assert raw_retirement.EXPECTED_DELETE_BYTES == 133_822_359_346
    assert raw_retirement.EXPECTED_RETAINED_FILES == 121_220
    assert raw_retirement.EXPECTED_RETAINED_BYTES == 18_131_756_871
    assert raw_retirement.EXPECTED_R4_METADATA_SHA256 == (
        "c820806c26ba9644061e7d1c92e79de48d69b7b91fa3d0d09763d028284fc4c6"
    )
    leaves = raw_retirement._target_leaves(Path("/fixed/older"))
    assert leaves == (
        Path("/fixed/older/raw/c3_batch_000/objects"),
        Path("/fixed/older/raw/c3_batch_001/objects"),
    )


def test_r5e_r1_four_manifest_role_registry_is_exact_and_closed() -> None:
    roles = raw_retirement.VERIFIED_DOWNLOAD_MANIFEST_ROLES
    assert tuple(roles) == (
        "OLDER_BATCH_1", "OLDER_BATCH_2", "R4_BATCH_1", "R4_BATCH_2"
    )
    assert [roles[key].expected_row_count for key in roles] == [
        18_872, 18_196, 18_872, 18_196
    ]
    assert [roles[key].manifest_bytes for key in roles] == [
        3_793_361, 3_657_485, 3_793_361, 3_657_485
    ]
    assert [roles[key].manifest_sha256 for key in roles] == [
        "80aa064d5da8c28b9193410497a721923f47605a0a86124e6c89988bea1bfb2e",
        "01162bbe350af5c53e8c8e644127eea06bdcd47c6f874b3f765d006129218ffb",
        "80aa064d5da8c28b9193410497a721923f47605a0a86124e6c89988bea1bfb2e",
        "01162bbe350af5c53e8c8e644127eea06bdcd47c6f874b3f765d006129218ffb",
    ]
    assert all(
        item.ordered_header
        == raw_retirement.VERIFIED_DOWNLOAD_MANIFEST_HEADER_V1
        and item.allowed_download_ok_token == "true"
        and item.source_relative_path_convention
        == "PLANNED_SOURCE_AUTHORITY_LOCATOR_V1"
        for item in roles.values()
    )
    assert sum(
        roles[key].expected_row_count
        for key in ("OLDER_BATCH_1", "OLDER_BATCH_2")
    ) == raw_retirement.EXPECTED_DELETE_FILES
    assert sum(raw_retirement.EXPECTED_BATCH_BYTES.values()) == (
        raw_retirement.EXPECTED_DELETE_BYTES
    )

    rows, expected = _download_rows()
    payload = _download_payload(rows)
    for role, authority in roles.items():
        manifest, digest = _parse_download_fixture(
            payload,
            expected,
            role=role,
            expected_attempt_id=authority.attempt_id,
            expected_batch_id=authority.batch_id,
        )
        assert set(manifest) == set(expected)
        assert digest == hashlib.sha256(payload).hexdigest()


def test_r5e_r1_exact_historical_source_locator_projection_passes() -> None:
    rows, expected = _download_rows()
    payload = _download_payload(rows)
    manifest, digest = _parse_download_fixture(payload, expected)
    assert set(manifest) == set(expected)
    assert digest == hashlib.sha256(payload).hexdigest()

    physical_locator = copy.deepcopy(rows)
    physical_locator[0]["source_relative_path"] = (
        physical_locator[0]["physical_source_key"] + ".dcm"
    )
    changed = _download_payload(physical_locator)
    _expect(
        "OLDER_RAW_DOWNLOAD_MANIFEST_SOURCE_PATH_INVALID",
        lambda: _parse_download_fixture(changed, expected),
    )


def test_r5e_r1_manifest_role_cannot_be_applied_cross_artifact() -> None:
    rows, expected = _download_rows()
    payload = _download_payload(rows)
    _expect(
        "OLDER_RAW_DOWNLOAD_MANIFEST_ROLE_SCHEMA_INVALID",
        lambda: _parse_download_fixture(
            payload,
            expected,
            role="R4_BATCH_1",
            expected_attempt_id=raw_retirement.OLDER_ATTEMPT_ID,
            expected_batch_id="c3_batch_000",
        ),
    )
    _expect(
        "OLDER_RAW_DOWNLOAD_MANIFEST_ROLE_SCHEMA_INVALID",
        lambda: raw_retirement._verified_download_manifest_role(
            attempt_id=raw_retirement.OLDER_ATTEMPT_ID,
            batch_id="c3_batch_002",
        ),
    )


def test_r5e_r1_manifest_header_width_and_row_count_fail_closed() -> None:
    rows, expected = _download_rows()
    header = raw_retirement.VERIFIED_DOWNLOAD_MANIFEST_HEADER_V1
    for changed_header in (
        header[:-1],
        (*header, "extra"),
        (header[1], header[0], *header[2:]),
        (*header[:-1], header[-2]),
    ):
        payload = _download_payload(rows, header=changed_header)
        _expect(
            "OLDER_RAW_DOWNLOAD_MANIFEST_HEADER_INVALID",
            lambda payload=payload: _parse_download_fixture(payload, expected),
        )
    full = _download_payload(rows).decode("utf-8").splitlines()
    extra = ("\n".join([full[0], full[1] + ",unexpected", full[2]]) + "\n").encode()
    missing = ("\n".join([full[0], full[1].rsplit(",", 1)[0], full[2]]) + "\n").encode()
    blank = ("\n".join([full[0], full[1], "", full[2]]) + "\n").encode()
    for payload in (extra, missing, blank):
        _expect(
            "OLDER_RAW_DOWNLOAD_MANIFEST_ROW_WIDTH_INVALID",
            lambda payload=payload: _parse_download_fixture(payload, expected),
        )
    short = _download_payload(rows[:1])
    _expect(
        "OLDER_RAW_DOWNLOAD_MANIFEST_ROW_COUNT_INVALID",
        lambda: _parse_download_fixture(short, expected),
    )


def test_r5e_r1_manifest_row_authorities_fail_closed() -> None:
    rows, expected = _download_rows()
    cases = []
    duplicate = copy.deepcopy(rows)
    duplicate[1]["physical_source_key"] = duplicate[0]["physical_source_key"]
    cases.append((duplicate, "OLDER_RAW_DOWNLOAD_MANIFEST_DUPLICATE_KEY"))
    for token in ("True", "true ", " true"):
        changed = copy.deepcopy(rows)
        changed[0]["download_ok"] = token
        cases.append((changed, "OLDER_RAW_DOWNLOAD_MANIFEST_STATUS_INVALID"))
    substituted = copy.deepcopy(rows)
    substituted[0]["physical_source_key"] = "e" * 64
    cases.append(
        (substituted, "OLDER_RAW_DOWNLOAD_MANIFEST_PLAN_MEMBERSHIP_INVALID")
    )
    for field in ("subject_id", "study_id"):
        changed = copy.deepcopy(rows)
        changed[0][field] = "999"
        cases.append((changed, "OLDER_RAW_DOWNLOAD_MANIFEST_OWNERSHIP_INVALID"))
    changed_path = copy.deepcopy(rows)
    changed_path[0]["source_relative_path"] += " "
    cases.append(
        (changed_path, "OLDER_RAW_DOWNLOAD_MANIFEST_SOURCE_PATH_INVALID")
    )
    changed_sha = copy.deepcopy(rows)
    changed_sha[0]["observed_sha256"] = "x" * 64
    cases.append((changed_sha, "OLDER_RAW_DOWNLOAD_MANIFEST_SHA_INVALID"))
    for changed, code in cases:
        payload = _download_payload(changed)
        _expect(
            code,
            lambda payload=payload: _parse_download_fixture(payload, expected),
        )


def test_r5e_destructive_reachability_has_no_caller_target() -> None:
    source = (SCRIPTS / "retire_lvef_c3_older_raw_duplicates.py").read_text()
    tree = ast.parse(source)
    rmtree_calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "rmtree"
    ]
    assert len(rmtree_calls) == 1
    assert isinstance(rmtree_calls[0].args[0], ast.Name)
    parser_source = source[source.index("def main("):]
    assert "--target" not in parser_source
    assert "--root" not in parser_source
    assert "--glob" not in parser_source
    assert "--schema" not in parser_source
    assert "Path(args" not in parser_source
    assert tuple(
        inspect.signature(raw_retirement.execute_exact_retirement).parameters
    ) == ("governing_commit",)
    executor = source[
        source.index("def execute_exact_retirement("):
        source.index("def validate_retirement_receipt_authority(")
    ]
    validation_gate = executor.index(
        "if tuple(batch for batch, _leaf in eligible) != TARGET_BATCHES:"
    )
    first_delete = executor.index("shutil.rmtree(leaf)")
    assert validation_gate < first_delete
    before_delete = executor[:first_delete]
    assert before_delete.count("_quiescent()") == 3
    assert before_delete.rindex("_quiescent()") > before_delete.rindex(
        "_validate_leaf_from_manifest(older_root, leaf, by_batch[batch])"
    )
    assert before_delete.rindex("_quiescent()") < before_delete.rindex(
        'getattr(shutil.rmtree, "avoids_symlink_attacks", False)'
    )
    assert before_delete.rindex("_retained_evidence_authority(") < (
        before_delete.rindex("_quiescent()")
    )
    assert "except OlderRawRetirementError" not in executor[
        executor.index("eligible: list"):first_delete
    ]


def test_r5e_r1_deletion_is_unreachable_before_manifest_seal() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        evidence = Path(temporary).resolve()
        with (
            mock.patch.object(raw_retirement, "MANIFEST_PATH", evidence / "manifest"),
            mock.patch.object(raw_retirement, "RECEIPT_PATH", evidence / "receipt"),
            mock.patch.object(raw_retirement, "SUMMARY_PATH", evidence / "summary"),
            mock.patch.object(raw_retirement, "_current_commit", return_value="a" * 40),
            mock.patch.object(raw_retirement.shutil, "rmtree") as destructive,
        ):
            _expect(
                "OLDER_RAW_CONTROL_READ_INVALID",
                lambda: raw_retirement.execute_exact_retirement(
                    governing_commit="a" * 40
                ),
            )
        destructive.assert_not_called()


def test_r5e_r3_final_mutation_quiescence_and_primitive_gates_delete_zero() -> None:
    governing_commit = "a" * 40
    role_inventory = [{"role": "SEALED", "file_count": 1, "total_bytes": 1}]
    retained = {
        "file_count": raw_retirement.EXPECTED_RETAINED_FILES,
        "total_bytes": raw_retirement.EXPECTED_RETAINED_BYTES,
        "file_metadata_sha256": "b" * 64,
        "directory_topology_sha256": "c" * 64,
        "role_inventory": role_inventory,
        "role_inventory_sha256": "d" * 64,
        "control_content_sha256": "e" * 64,
    }
    manifest = {
        "governing_commit": governing_commit,
        "retained_file_metadata_sha256": retained["file_metadata_sha256"],
        "retained_directory_topology_sha256": retained[
            "directory_topology_sha256"
        ],
        "retained_role_inventory": role_inventory,
        "retained_role_inventory_sha256": retained["role_inventory_sha256"],
        "retained_control_content_sha256": retained["control_content_sha256"],
        "targets": [
            {
                "batch_id": batch,
                "relative_path": f"raw/{batch}/objects/{index + 1:064x}.dcm",
                "size_bytes": 1,
            }
            for index, batch in enumerate(raw_retirement.TARGET_BATCHES)
        ],
    }
    payload = raw_retirement._canonical(manifest)

    def run_to_gate(*, retained_effect, quiescence_effect, safe: bool, code: str):
        destructive = mock.Mock()
        destructive.avoids_symlink_attacks = safe
        quiescent = mock.Mock()
        if isinstance(quiescence_effect, tuple):
            quiescent.side_effect = quiescence_effect
        else:
            quiescent.return_value = quiescence_effect
        with (
            mock.patch.object(raw_retirement, "_current_commit", return_value=governing_commit),
            mock.patch.object(raw_retirement.os.path, "lexists", return_value=False),
            mock.patch.object(raw_retirement, "_read_json", return_value=(manifest, payload)),
            mock.patch.object(raw_retirement, "_validate_manifest"),
            mock.patch.object(raw_retirement, "_validate_safe_export"),
            mock.patch.object(raw_retirement, "_derive_manifest", return_value=manifest),
            mock.patch.object(raw_retirement, "_validate_r4", return_value={}),
            mock.patch.object(raw_retirement, "_validate_leaf_from_manifest"),
            mock.patch.object(
                raw_retirement,
                "_retained_evidence_authority",
                return_value=retained_effect,
            ),
            mock.patch.object(raw_retirement, "_quiescent", quiescent),
            mock.patch.object(raw_retirement.shutil, "rmtree", destructive),
        ):
            _expect(
                code,
                lambda: raw_retirement.execute_exact_retirement(
                    governing_commit=governing_commit
                ),
            )
        destructive.assert_not_called()

    run_to_gate(
        retained_effect={**retained, "control_content_sha256": "0" * 64},
        quiescence_effect={},
        safe=True,
        code="OLDER_RAW_RETAINED_AUTHORITY_INVALID",
    )
    run_to_gate(
        retained_effect=retained,
        quiescence_effect=(
            {},
            {},
            raw_retirement.OlderRawRetirementError(
                "OLDER_RAW_ACTIVE_PROCESS_EXISTS"
            ),
        ),
        safe=True,
        code="OLDER_RAW_ACTIVE_PROCESS_EXISTS",
    )
    run_to_gate(
        retained_effect=retained,
        quiescence_effect={},
        safe=False,
        code="OLDER_RAW_DELETION_PRIMITIVE_UNSAFE",
    )


def test_r5e_r3_final_leaf_mutation_before_delete_calls_rmtree_zero() -> None:
    governing_commit = "a" * 40
    role_inventory = [{"role": "SEALED", "file_count": 1, "total_bytes": 1}]
    retained = {
        "file_count": raw_retirement.EXPECTED_RETAINED_FILES,
        "total_bytes": raw_retirement.EXPECTED_RETAINED_BYTES,
        "file_metadata_sha256": "b" * 64,
        "directory_topology_sha256": "c" * 64,
        "role_inventory": role_inventory,
        "role_inventory_sha256": "d" * 64,
        "control_content_sha256": "e" * 64,
    }
    manifest = {
        "governing_commit": governing_commit,
        "retained_file_metadata_sha256": retained["file_metadata_sha256"],
        "retained_directory_topology_sha256": retained[
            "directory_topology_sha256"
        ],
        "retained_role_inventory": role_inventory,
        "retained_role_inventory_sha256": retained["role_inventory_sha256"],
        "retained_control_content_sha256": retained["control_content_sha256"],
        "targets": [
            {
                "batch_id": batch,
                "relative_path": f"raw/{batch}/objects/{index + 1:064x}.dcm",
                "size_bytes": 1,
            }
            for index, batch in enumerate(raw_retirement.TARGET_BATCHES)
        ],
    }
    manifest_payload = raw_retirement._canonical(manifest)
    final_leaf_mutation = raw_retirement.OlderRawRetirementError(
        "OLDER_RAW_OBJECT_LEAF_FILE_SET_INVALID"
    )
    destructive = mock.Mock()
    destructive.avoids_symlink_attacks = True
    with (
        mock.patch.object(
            raw_retirement, "_current_commit", return_value=governing_commit
        ),
        mock.patch.object(raw_retirement.os.path, "lexists", return_value=False),
        mock.patch.object(
            raw_retirement,
            "_read_json",
            return_value=(manifest, manifest_payload),
        ),
        mock.patch.object(raw_retirement, "_validate_manifest"),
        mock.patch.object(raw_retirement, "_validate_safe_export"),
        mock.patch.object(
            raw_retirement, "_derive_manifest", return_value=manifest
        ),
        mock.patch.object(raw_retirement, "_validate_r4", return_value={}),
        mock.patch.object(
            raw_retirement,
            "_validate_leaf_from_manifest",
            side_effect=(None, None, final_leaf_mutation),
        ) as leaf_validator,
        mock.patch.object(
            raw_retirement,
            "_retained_evidence_authority",
            return_value=retained,
        ),
        mock.patch.object(raw_retirement, "_quiescent", return_value={}),
        mock.patch.object(raw_retirement.shutil, "rmtree", destructive),
    ):
        _expect(
            "OLDER_RAW_OBJECT_LEAF_FILE_SET_INVALID",
            lambda: raw_retirement.execute_exact_retirement(
                governing_commit=governing_commit
            ),
        )
    assert leaf_validator.call_count == 3
    destructive.assert_not_called()


def test_r5e_r3_execution_deletes_exact_two_fixed_leaves_then_uses_sealed_postcheck() -> None:
    governing_commit = "a" * 40
    role_inventory = [{"role": "SEALED", "file_count": 1, "total_bytes": 1}]
    retained = {
        "file_count": raw_retirement.EXPECTED_RETAINED_FILES,
        "total_bytes": raw_retirement.EXPECTED_RETAINED_BYTES,
        "file_metadata_sha256": "b" * 64,
        "directory_topology_sha256": "c" * 64,
        "role_inventory": role_inventory,
        "role_inventory_sha256": "d" * 64,
        "control_content_sha256": "e" * 64,
    }
    r4_authority = {"metadata_stat_sha256": "f" * 64}
    manifest = {
        "governing_commit": governing_commit,
        "retained_file_metadata_sha256": retained["file_metadata_sha256"],
        "retained_directory_topology_sha256": retained[
            "directory_topology_sha256"
        ],
        "retained_role_inventory": role_inventory,
        "retained_role_inventory_sha256": retained["role_inventory_sha256"],
        "retained_control_content_sha256": retained["control_content_sha256"],
        "diagnostic_evidence_authority": {
            "source_object_key": "1" * 64,
            "source_local_sha256": "2" * 64,
        },
        "r4_authority": r4_authority,
        "targets": [
            {
                "batch_id": batch,
                "relative_path": f"raw/{batch}/objects/{index + 1:064x}.dcm",
                "size_bytes": 1,
            }
            for index, batch in enumerate(raw_retirement.TARGET_BATCHES)
        ],
    }
    payload = raw_retirement._canonical(manifest)
    destructive = mock.Mock()
    destructive.avoids_symlink_attacks = True
    diagnostic_failure = raw_retirement.OlderRawRetirementError(
        "OLDER_RAW_DIAGNOSTIC_AUTHORITY_UNRESOLVED"
    )
    with (
        mock.patch.object(raw_retirement, "_current_commit", return_value=governing_commit),
        mock.patch.object(raw_retirement.os.path, "lexists", return_value=False),
        mock.patch.object(raw_retirement, "_read_json", return_value=(manifest, payload)),
        mock.patch.object(raw_retirement, "_validate_manifest"),
        mock.patch.object(raw_retirement, "_validate_safe_export"),
        mock.patch.object(raw_retirement, "_derive_manifest", return_value=manifest),
        mock.patch.object(
            raw_retirement, "_validate_r4", return_value=r4_authority
        ) as validate_r4,
        mock.patch.object(raw_retirement, "_validate_leaf_from_manifest"),
        mock.patch.object(
            raw_retirement, "_retained_evidence_authority", return_value=retained
        ),
        mock.patch.object(raw_retirement, "_quiescent", return_value={}),
        mock.patch.object(raw_retirement, "_diagnostic_root", return_value=Path("/sealed")),
        mock.patch.object(
            raw_retirement,
            "_diagnostic_authority_from_sealed_manifest",
            side_effect=diagnostic_failure,
        ) as diagnostic,
        mock.patch.object(raw_retirement.shutil, "rmtree", destructive),
    ):
        _expect(
            "OLDER_RAW_DIAGNOSTIC_AUTHORITY_UNRESOLVED",
            lambda: raw_retirement.execute_exact_retirement(
                governing_commit=governing_commit
            ),
        )
    older_root = (
        raw_retirement.PRODUCTION_ROOT
        / "attempts"
        / raw_retirement.OLDER_ATTEMPT_ID
    )
    assert destructive.call_args_list == [
        mock.call(path) for path in raw_retirement._target_leaves(older_root)
    ]
    assert validate_r4.call_count == 2
    diagnostic.assert_called_once_with(Path("/sealed"), manifest)


def test_r5e_r3_sealed_diagnostic_postcheck_never_requires_retired_source() -> None:
    sealed = {
        "source_object_key": "a" * 64,
        "source_local_sha256": "b" * 64,
        "status": "PASS_R3E_DIAGNOSTIC_EVIDENCE_RETAINED",
    }
    root = Path("/sealed/diagnostic")
    with mock.patch.object(
        raw_retirement, "_diagnostic_evidence_authority", return_value=sealed
    ) as validator:
        assert raw_retirement._diagnostic_authority_from_sealed_manifest(
            root, {"diagnostic_evidence_authority": sealed}
        ) == sealed
    validator.assert_called_once_with(
        root,
        source_object_key="a" * 64,
        source_local_sha256="b" * 64,
    )
    with mock.patch.object(
        raw_retirement,
        "_diagnostic_evidence_authority",
        return_value={**sealed, "source_local_sha256": "0" * 64},
    ):
        _expect(
            "OLDER_RAW_DIAGNOSTIC_AUTHORITY_UNRESOLVED",
            lambda: raw_retirement._diagnostic_authority_from_sealed_manifest(
                root, {"diagnostic_evidence_authority": sealed}
            ),
        )
    assert "r3e.run_preflight" not in inspect.getsource(
        raw_retirement.validate_retired_state
    )


def test_r5e_r3_recursive_raw_object_closure_and_nested_layout_pass() -> None:
    for nested in (False, True):
        with tempfile.TemporaryDirectory() as temporary:
            older = Path(temporary).resolve() / "older"
            leaf, expected = _synthetic_raw_leaf(older, nested=nested)
            authority = _synthetic_raw_authority(older, leaf, expected)
            assert authority["file_count"] == 2
            assert authority["total_bytes"] == 7
            assert authority["directory_count"] == (3 if nested else 1)
            entries = tuple(
                {
                    "batch_id": "c3_batch_000",
                    "source_object_key": key,
                    "relative_path": authority["entries"][key]["relative_path"],
                    "size_bytes": size,
                }
                for key, size in expected.items()
            )
            with mock.patch.object(
                raw_retirement,
                "_mountinfo_mountpoints",
                return_value=frozenset(),
            ):
                raw_retirement._validate_leaf_from_manifest(older, leaf, entries)


def test_r5e_r3_raw_object_file_set_and_byte_errors_are_role_specific() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        older = Path(temporary).resolve() / "older"
        leaf, expected = _synthetic_raw_leaf(older)
        missing = leaf / ("a" * 64 + ".dcm")
        missing.unlink()
        _expect(
            "OLDER_RAW_OBJECT_LEAF_FILE_SET_INVALID",
            lambda: _synthetic_raw_authority(older, leaf, expected),
        )

    with tempfile.TemporaryDirectory() as temporary:
        older = Path(temporary).resolve() / "older"
        leaf, expected = _synthetic_raw_leaf(older)
        _private_file(leaf / ("c" * 64 + ".dcm"), b"x")
        _expect(
            "OLDER_RAW_OBJECT_LEAF_FILE_SET_INVALID",
            lambda: _synthetic_raw_authority(older, leaf, expected),
        )

    with tempfile.TemporaryDirectory() as temporary:
        older = Path(temporary).resolve() / "older"
        leaf, expected = _synthetic_raw_leaf(older)
        (leaf / ("a" * 64 + ".dcm")).unlink()
        _private_file(leaf / ("c" * 64 + ".dcm"), b"abc")
        _expect(
            "OLDER_RAW_OBJECT_LEAF_FILE_SET_INVALID",
            lambda: _synthetic_raw_authority(older, leaf, expected),
        )

    with tempfile.TemporaryDirectory() as temporary:
        older = Path(temporary).resolve() / "older"
        leaf, expected = _synthetic_raw_leaf(older)
        wrong_bytes = {**expected, "a" * 64: 99}
        _expect(
            "OLDER_RAW_OBJECT_LEAF_BYTES_INVALID",
            lambda: _synthetic_raw_authority(older, leaf, wrong_bytes),
        )


def test_r5e_r3_raw_object_unsafe_entries_fail_closed() -> None:
    unsafe_cases = (
        (".nfs0001", lambda path, _leaf: _private_file(path, b"x")),
        ("c" * 64 + ".dcm.partial", lambda path, _leaf: _private_file(path, b"x")),
        (
            "c" * 64 + ".dcm",
            lambda path, leaf: path.symlink_to(leaf / ("a" * 64 + ".dcm")),
        ),
        ("c" * 64 + ".dcm", lambda path, _leaf: os.mkfifo(path, 0o600)),
        (
            "c" * 64 + ".dcm",
            lambda path, leaf: os.link(leaf / ("a" * 64 + ".dcm"), path),
        ),
    )
    for name, create in unsafe_cases:
        with tempfile.TemporaryDirectory() as temporary:
            older = Path(temporary).resolve() / "older"
            leaf, expected = _synthetic_raw_leaf(older)
            create(leaf / name, leaf)
            _expect(
                "OLDER_RAW_OBJECT_LEAF_UNSAFE_ENTRY",
                lambda: _synthetic_raw_authority(older, leaf, expected),
            )


def test_r5e_r3_mountinfo_parser_and_platform_authority_are_closed() -> None:
    payload = (
        b"36 25 0:32 / /restricted\\040project rw,relatime - ext4 /dev/x rw\n"
        b"37 25 0:33 / /restricted\\134backslash rw - ext4 /dev/y rw\n"
    )
    with (
        mock.patch.object(raw_retirement.sys, "platform", "linux"),
        mock.patch.object(raw_retirement.os, "open", return_value=41),
        mock.patch.object(raw_retirement.os, "read", side_effect=(payload, b"")),
        mock.patch.object(raw_retirement.os, "close") as close,
    ):
        observed = raw_retirement._mountinfo_mountpoints()
    assert observed == frozenset(
        {Path("/restricted project"), Path("/restricted\\backslash")}
    )
    close.assert_called_once_with(41)

    with (
        mock.patch.object(raw_retirement.sys, "platform", "darwin"),
        mock.patch.object(raw_retirement.os, "open") as opener,
    ):
        assert raw_retirement._mountinfo_mountpoints() == frozenset()
    opener.assert_not_called()

    with mock.patch.object(raw_retirement.sys, "platform", "freebsd14"):
        _expect(
            "OLDER_RAW_OBJECT_LEAF_TOPOLOGY_INVALID",
            raw_retirement._mountinfo_mountpoints,
        )


def test_r5e_r3_mounts_on_attempt_chain_or_nested_subtree_fail_closed() -> None:
    for mount_selector in (
        lambda older, _leaf: older / "raw",
        lambda _older, leaf: leaf / "empty_nested_bind",
    ):
        with tempfile.TemporaryDirectory() as temporary:
            older = Path(temporary).resolve() / "older"
            leaf, expected = _synthetic_raw_leaf(older)
            _private_directory(leaf / "empty_nested_bind")
            mountpoint = mount_selector(older, leaf)
            with mock.patch.object(
                raw_retirement,
                "_mountinfo_mountpoints",
                return_value=frozenset({mountpoint}),
            ):
                _expect(
                    "OLDER_RAW_OBJECT_LEAF_TOPOLOGY_INVALID",
                    lambda: raw_retirement._raw_object_leaf_authority(
                        older,
                        leaf,
                        batch_id="c3_batch_000",
                        expected_sizes=expected,
                    ),
                )


def test_r5e_r3_partial_tree_is_separate_safe_metadata_only_authority() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        attempt_root = Path(temporary).resolve() / raw_retirement.OLDER_ATTEMPT_ID
        partials = attempt_root / "raw" / "c3_batch_000" / "partials"
        _private_directory(partials)
        source_key = "a" * 64
        partial = partials / (
            f"{source_key}.{raw_retirement.OLDER_ATTEMPT_ID}.partial"
        )
        _private_file(partial, b"preserved-partial")
        with (
            mock.patch.object(
                raw_retirement, "_mountinfo_mountpoints", return_value=frozenset()
            ),
            mock.patch("builtins.open", side_effect=AssertionError("body read")),
        ):
            authority = raw_retirement._partial_tree_authority(
                attempt_root,
                partials,
                batch_id="c3_batch_000",
                attempt_id=raw_retirement.OLDER_ATTEMPT_ID,
                expected_source_keys={source_key},
            )
        assert authority == {
            "file_count": 1,
            "total_bytes": len(b"preserved-partial"),
            "directory_count": 1,
        }

        for unsafe_name in (".nfs0001", "nested.partial"):
            unsafe = partials / unsafe_name
            _private_directory(unsafe)
            with mock.patch.object(
                raw_retirement, "_mountinfo_mountpoints", return_value=frozenset()
            ):
                _expect(
                    "OLDER_RAW_PARTIAL_TREE_AUTHORITY_INVALID",
                    lambda: raw_retirement._partial_tree_authority(
                        attempt_root,
                        partials,
                        batch_id="c3_batch_000",
                        attempt_id=raw_retirement.OLDER_ATTEMPT_ID,
                        expected_source_keys={source_key},
                    ),
                )
            unsafe.rmdir()

        unknown = partials / (
            f"{'b' * 64}.{raw_retirement.OLDER_ATTEMPT_ID}.partial"
        )
        _private_file(unknown, b"unknown")
        with mock.patch.object(
            raw_retirement, "_mountinfo_mountpoints", return_value=frozenset()
        ):
            _expect(
                "OLDER_RAW_PARTIAL_TREE_AUTHORITY_INVALID",
                lambda: raw_retirement._partial_tree_authority(
                    attempt_root,
                    partials,
                    batch_id="c3_batch_000",
                    attempt_id=raw_retirement.OLDER_ATTEMPT_ID,
                    expected_source_keys={source_key},
                ),
            )

    with tempfile.TemporaryDirectory() as temporary:
        attempt_root = Path(temporary).resolve() / raw_retirement.OLDER_ATTEMPT_ID
        partials = attempt_root / "raw" / "c3_batch_000" / "partials"
        with mock.patch.object(
            raw_retirement,
            "_mountinfo_mountpoints",
            side_effect=raw_retirement.OlderRawRetirementError(
                "OLDER_RAW_OBJECT_LEAF_TOPOLOGY_INVALID"
            ),
        ):
            _private_directory(partials)
            _expect(
                "OLDER_RAW_PARTIAL_TREE_AUTHORITY_INVALID",
                lambda: raw_retirement._partial_tree_authority(
                    attempt_root,
                    partials,
                    batch_id="c3_batch_000",
                    attempt_id=raw_retirement.OLDER_ATTEMPT_ID,
                    expected_source_keys=set(),
                ),
            )


def test_r5e_r3_retained_receipt_tree_allows_exact_two_transitions_only() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        fixture = _transition_receipt_fixture(Path(temporary).resolve())
        authority = _validate_transition_fixture(fixture)
        assert authority["required_receipt_files"] == 1
        assert authority["auxiliary_receipt_files"] == 2
        assert authority["auxiliary_receipt_bytes"] > 0

        _private_file(fixture.receipts_root / "unknown.extra.json", b"{}")
        _expect(
            "OLDER_RAW_AUXILIARY_RECEIPT_UNCLASSIFIED",
            lambda: _validate_transition_fixture(fixture),
        )

    with tempfile.TemporaryDirectory() as temporary:
        fixture = _transition_receipt_fixture(Path(temporary).resolve())
        (fixture.receipts_root / fixture.required_name).unlink()
        _expect(
            "OLDER_RAW_REQUIRED_RECEIPT_MISSING",
            lambda: _validate_transition_fixture(fixture),
        )

    with tempfile.TemporaryDirectory() as temporary:
        fixture = _transition_receipt_fixture(Path(temporary).resolve())
        os.chmod(fixture.receipts_root / fixture.required_name, 0o640)
        _expect(
            "OLDER_RAW_REQUIRED_RECEIPT_INVALID",
            lambda: _validate_transition_fixture(fixture),
        )

    with tempfile.TemporaryDirectory() as temporary:
        fixture = _transition_receipt_fixture(Path(temporary).resolve())
        fixture.ledger_batch["download_recovery_receipts"] = {"a" * 64: "5" * 64}
        _expect(
            "OLDER_RAW_AUXILIARY_RECEIPT_UNCLASSIFIED",
            lambda: _validate_transition_fixture(fixture),
        )


def test_r5e_r3_transition_receipts_bind_exact_inputs_outputs_and_bytes() -> None:
    cases = (
        (0, "output_manifest_sha256", "9" * 64),
        (0, "input_receipt_sha256", ["9" * 64]),
        (1, "input_receipt_sha256", ["8" * 64, "3" * 64]),
    )
    for transition_index, field, value in cases:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = _transition_receipt_fixture(Path(temporary).resolve())
            basename = raw_retirement.TRANSITION_RECEIPT_BASENAMES[
                transition_index
            ]
            fixture.documents[basename][field] = value
            payload = raw_retirement.core.canonical_json_bytes(
                fixture.documents[basename]
            )
            _private_file(fixture.receipts_root / basename, payload)
            fixture.ledger_batch["events"][transition_index][
                "receipt_sha256"
            ] = hashlib.sha256(payload).hexdigest()
            _expect(
                "OLDER_RAW_AUXILIARY_RECEIPT_UNCLASSIFIED",
                lambda: _validate_transition_fixture(fixture),
            )

    with tempfile.TemporaryDirectory() as temporary:
        fixture = _transition_receipt_fixture(Path(temporary).resolve())
        basename = raw_retirement.TRANSITION_RECEIPT_BASENAMES[0]
        payload = json.dumps(fixture.documents[basename], indent=2).encode()
        _private_file(fixture.receipts_root / basename, payload)
        fixture.ledger_batch["events"][0]["receipt_sha256"] = hashlib.sha256(
            payload
        ).hexdigest()
        _expect(
            "OLDER_RAW_AUXILIARY_RECEIPT_UNCLASSIFIED",
            lambda: _validate_transition_fixture(fixture),
        )

    with tempfile.TemporaryDirectory() as temporary:
        older = Path(temporary).resolve() / "older"
        leaf, expected = _synthetic_raw_leaf(older)
        _private_file(leaf / "unexpected.txt", b"x")
        _expect(
            "OLDER_RAW_OBJECT_LEAF_FILE_SET_INVALID",
            lambda: _synthetic_raw_authority(older, leaf, expected),
        )

    for name in (".nfs0001", "layout.partial"):
        with tempfile.TemporaryDirectory() as temporary:
            older = Path(temporary).resolve() / "older"
            leaf, expected = _synthetic_raw_leaf(older)
            _private_directory(leaf / name)
            _expect(
                "OLDER_RAW_OBJECT_LEAF_UNSAFE_ENTRY",
                lambda: _synthetic_raw_authority(older, leaf, expected),
            )


def test_r5e_quiescence_fails_closed_when_process_inventory_fails() -> None:
    completed_qstat = SimpleNamespace(
        returncode=0, stderr=b"", stdout=b"<job_info></job_info>"
    )
    failed_ps = SimpleNamespace(returncode=1, stderr=b"failed", stdout=b"")
    with (
        mock.patch.object(
            raw_retirement.subprocess,
            "run",
            side_effect=(completed_qstat, failed_ps),
        ),
        mock.patch.dict(os.environ, {"USER": "synthetic"}),
    ):
        _expect(
            "OLDER_RAW_QUIESCENCE_AUTHORITY_INVALID",
            raw_retirement._quiescent,
        )


def test_r5e_r2_quiescence_classifies_target_processes_and_unrelated_scope() -> None:
    completed_qstat = SimpleNamespace(
        returncode=0, stderr=b"", stdout=b"<job_info></job_info>"
    )
    process_prefix = b"4242 S Mon Jan 1 00:00:00 2026 "
    stable = ({"batch_id": "c3_batch_000"}, {"batch_id": "c3_batch_001"})
    cases = (
        (
            process_prefix + b"python retire_lvef_c3_older_raw_duplicates.py\n",
            "MATCHING_TARGET_REFERENCE_BLOCKING",
            "OLDER_RAW_ACTIVE_PROCESS_EXISTS",
        ),
        (
            process_prefix + b"python retire_lvef_c3_older_raw_duplicates.py\n",
            "CANDIDATE_PROC_AUTHORITY_INACCESSIBLE_BLOCKING",
            "OLDER_RAW_CANDIDATE_PROC_AUTHORITY_INACCESSIBLE",
        ),
        (
            b"4242 S Mon Jan 1 00:00:00 2026\n",
            "CANDIDATE_PROC_AUTHORITY_INACCESSIBLE_BLOCKING",
            "OLDER_RAW_UNKNOWN_PROCESS_SCOPE",
        ),
    )
    for ps_stdout, disposition, expected in cases:
        completed_ps = SimpleNamespace(
            returncode=0, stderr=b"", stdout=ps_stdout
        )
        with (
            mock.patch.object(
                raw_retirement.subprocess,
                "run",
                side_effect=(completed_qstat, completed_ps),
            ),
            mock.patch.object(
                raw_retirement,
                "_inspect_proc_references",
                return_value=disposition,
            ),
            mock.patch.object(
                raw_retirement,
                "_stable_target_leaf_authority",
                return_value=stable,
            ),
            mock.patch.dict(os.environ, {"USER": "synthetic"}),
        ):
            _expect(expected, raw_retirement._quiescent)

    unrelated_ps = SimpleNamespace(
        returncode=0,
        stderr=b"",
        stdout=process_prefix + b"/bin/zsh -l\n",
    )
    with (
        mock.patch.object(
            raw_retirement.subprocess,
            "run",
            side_effect=(completed_qstat, unrelated_ps),
        ),
        mock.patch.object(
            raw_retirement,
            "_inspect_proc_references",
            return_value="UNRELATED_PROCESS_INSPECTED_NONBLOCKING",
        ) as inspect_proc,
        mock.patch.object(
            raw_retirement,
            "_stable_target_leaf_authority",
            return_value=stable,
        ),
        mock.patch.dict(os.environ, {"USER": "synthetic"}),
    ):
        result = raw_retirement._quiescent()
    inspect_proc.assert_called_once()
    assert inspect_proc.call_args.kwargs["required"] is False
    assert result == {
        "same_user_processes_observed": 1,
        "candidate_processes": 0,
        "vanished_processes": 0,
        "unrelated_inaccessible_processes": 0,
        "candidate_inaccessible_processes": 0,
        "confirmed_target_references": 0,
        "matching_scheduler_jobs": 0,
        "stable_target_leaves": 2,
    }

    completed_ps = SimpleNamespace(
        returncode=0,
        stderr=b"",
        stdout=process_prefix + b"/bin/zsh -l\n",
    )
    with (
        mock.patch.object(
            raw_retirement.subprocess,
            "run",
            side_effect=(completed_qstat, completed_ps),
        ),
        mock.patch.object(
            raw_retirement,
            "_inspect_proc_references",
            return_value="MATCHING_TARGET_REFERENCE_BLOCKING",
        ) as inspect_proc,
        mock.patch.object(
            raw_retirement,
            "_stable_target_leaf_authority",
            return_value=stable,
        ),
        mock.patch.dict(os.environ, {"USER": "synthetic"}),
    ):
        _expect("OLDER_RAW_ACTIVE_PROCESS_EXISTS", raw_retirement._quiescent)
    assert inspect_proc.call_args.kwargs["required"] is False

    vanished_ps = SimpleNamespace(
        returncode=0,
        stderr=b"",
        stdout=(
            process_prefix
            + b"python retire_lvef_c3_older_raw_duplicates.py\n"
            + b"4243 Z Mon Jan 1 00:00:00 2026 /bin/zombie\n"
        ),
    )
    with (
        mock.patch.object(
            raw_retirement.subprocess,
            "run",
            side_effect=(completed_qstat, vanished_ps),
        ),
        mock.patch.object(
            raw_retirement,
            "_inspect_proc_references",
            return_value="PROCESS_EXITED_DURING_SCAN_NONBLOCKING",
        ),
        mock.patch.object(
            raw_retirement,
            "_stable_target_leaf_authority",
            return_value=stable,
        ),
        mock.patch.dict(os.environ, {"USER": "synthetic"}),
    ):
        result = raw_retirement._quiescent()
    assert result["same_user_processes_observed"] == 2
    assert result["candidate_processes"] == 1
    assert result["vanished_processes"] == 1
    assert result["confirmed_target_references"] == 0


def test_r5e_r2_quiescence_closed_proc_dispositions_and_scheduler_block() -> None:
    target = Path("/restricted/projectnb/mimicecho/target")
    exited = FileNotFoundError(errno.ENOENT, "gone")
    inaccessible = PermissionError(errno.EACCES, "private")
    for error, required, expected in (
        (exited, True, "PROCESS_EXITED_DURING_SCAN_NONBLOCKING"),
        (
            inaccessible,
            True,
            "CANDIDATE_PROC_AUTHORITY_INACCESSIBLE_BLOCKING",
        ),
        (
            inaccessible,
            False,
            "UNRELATED_PROCESS_PROC_INACCESSIBLE_NONBLOCKING",
        ),
    ):
        with mock.patch.object(raw_retirement.os, "readlink", side_effect=error):
            assert raw_retirement._inspect_proc_references(
                42, target_leaves=(target,), required=required
            ) == expected

    qstat = SimpleNamespace(
        returncode=0,
        stderr=b"",
        stdout=(
            b"<job_info><job_list><JB_job_number>7183952</JB_job_number>"
            b"<JB_name>unrelated</JB_name></job_list></job_info>"
        ),
    )
    with (
        mock.patch.object(raw_retirement.subprocess, "run", return_value=qstat),
        mock.patch.dict(os.environ, {"USER": "synthetic"}),
    ):
        _expect("OLDER_RAW_ACTIVE_JOB_EXISTS", raw_retirement._quiescent)


def test_r5e_r2_target_leaf_double_snapshot_is_exact_and_metadata_only() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        production = Path(temporary).resolve()
        older = (
            production
            / "attempts"
            / raw_retirement.OLDER_ATTEMPT_ID
        )
        leaves = raw_retirement._target_leaves(older)
        for index, leaf in enumerate(leaves):
            leaf.mkdir(parents=True)
            leaf.chmod(0o700)
            _private_file(leaf / (f"{index + 1:064x}.dcm"), b"abc")
        counts = {batch: 1 for batch in raw_retirement.TARGET_BATCHES}
        sizes = {batch: 3 for batch in raw_retirement.TARGET_BATCHES}
        with (
            mock.patch.object(raw_retirement, "PRODUCTION_ROOT", production),
            mock.patch.object(raw_retirement, "EXPECTED_BATCH_COUNTS", counts),
            mock.patch.object(raw_retirement, "EXPECTED_BATCH_BYTES", sizes),
        ):
            opened = mock.Mock(side_effect=AssertionError("body open forbidden"))
            with mock.patch("builtins.open", opened):
                authority = raw_retirement._stable_target_leaf_authority(
                    sleeper=lambda _seconds: None
                )
            opened.assert_not_called()
            assert len(authority) == 2
            assert all(row["file_count"] == 1 for row in authority)

            first = raw_retirement._target_leaf_snapshot(
                older, leaves[0], batch_id=raw_retirement.TARGET_BATCHES[0]
            )
            changed = {**first, "metadata_projection_sha256": "0" * 64}
            with mock.patch.object(
                raw_retirement,
                "_target_leaf_snapshot",
                side_effect=(first, authority[1], changed, authority[1]),
            ):
                _expect(
                    "OLDER_RAW_TARGET_LEAF_UNSTABLE",
                    lambda: raw_retirement._stable_target_leaf_authority(
                        sleeper=lambda _seconds: None
                    ),
                )

            anomaly = leaves[0] / ".nfs0001"
            _private_file(anomaly, b"x")
            _expect(
                "OLDER_RAW_OBJECT_LEAF_UNSAFE_ENTRY",
                lambda: raw_retirement._target_leaf_snapshot(
                    older,
                    leaves[0],
                    batch_id=raw_retirement.TARGET_BATCHES[0],
                ),
            )
            anomaly.unlink()

            partial = leaves[0] / ("2" * 64 + ".dcm.partial")
            _private_file(partial, b"x")
            _expect(
                "OLDER_RAW_OBJECT_LEAF_UNSAFE_ENTRY",
                lambda: raw_retirement._target_leaf_snapshot(
                    older, leaves[0], batch_id=raw_retirement.TARGET_BATCHES[0]
                ),
            )
            partial.unlink()

            symlink = leaves[0] / ("3" * 64 + ".dcm")
            symlink.symlink_to("1".zfill(64) + ".dcm")
            _expect(
                "OLDER_RAW_OBJECT_LEAF_UNSAFE_ENTRY",
                lambda: raw_retirement._target_leaf_snapshot(
                    older, leaves[0], batch_id=raw_retirement.TARGET_BATCHES[0]
                ),
            )
            symlink.unlink()

            source = leaves[0] / (f"{1:064x}.dcm")
            hardlink = leaves[0] / ("4" * 64 + ".dcm")
            os.link(source, hardlink)
            _expect(
                "OLDER_RAW_OBJECT_LEAF_UNSAFE_ENTRY",
                lambda: raw_retirement._target_leaf_snapshot(
                    older, leaves[0], batch_id=raw_retirement.TARGET_BATCHES[0]
                ),
            )
            hardlink.unlink()

            fifo = leaves[0] / ("5" * 64 + ".dcm")
            os.mkfifo(fifo, 0o600)
            _expect(
                "OLDER_RAW_OBJECT_LEAF_UNSAFE_ENTRY",
                lambda: raw_retirement._target_leaf_snapshot(
                    older, leaves[0], batch_id=raw_retirement.TARGET_BATCHES[0]
                ),
            )
            fifo.unlink()

            with mock.patch.object(
                raw_retirement.os.path, "ismount", return_value=True
            ):
                _expect(
                    "OLDER_RAW_OBJECT_LEAF_TOPOLOGY_INVALID",
                    lambda: raw_retirement._target_leaf_snapshot(
                        older,
                        leaves[0],
                        batch_id=raw_retirement.TARGET_BATCHES[0],
                    ),
                )

def test_r5e_retained_role_classifier_is_closed_and_body_roles_are_exact() -> None:
    cases = {
        "raw/c3_batch_000/objects/a.dcm": "RAW_DICOM_PAYLOAD",
        (
            "raw/c3_batch_000/partials/"
            + "a" * 64
            + f".{raw_retirement.OLDER_ATTEMPT_ID}.partial"
        ): "PRESERVED_DOWNLOAD_PARTIALS",
        "extracted_cache/c3_batch_001/dicom_extraction.partial/clips/aa/a.npz": (
            "EXTRACTED_NPZ_CACHE"
        ),
        "batches/c3_batch_000/echoprime/clip_embeddings.restricted.npz": (
            "CLIP_EMBEDDINGS"
        ),
        "batches/c3_batch_000/echoprime/study_embeddings.restricted.npz": (
            "STUDY_EMBEDDINGS"
        ),
        "raw/c3_batch_000/receipts/a.verification.json": (
            "DOWNLOAD_VERIFICATION_RECEIPTS"
        ),
        "raw/c3_batch_000/verified_download_manifest.restricted.csv": (
            "SOURCE_AND_BATCH_MANIFESTS"
        ),
        "extracted_cache/c3_batch_001/dicom_extraction.partial/dicom_audit.restricted.csv": (
            "DICOM_AUDIT_AND_EXTRACTION_MANIFESTS"
        ),
        "batches/c3_batch_000/download_resume_ledger.restricted.json": (
            "POOLING_PRESERVATION_RETIREMENT_FINALIZATION_RECEIPTS"
        ),
        "full_submission_claim.restricted.json": (
            "CLAIM_PLAN_SUBMISSION_AND_ENVIRONMENT_AUTHORITIES"
        ),
        "scheduler/job.o7183952.1": "SCHEDULER_LOGS",
        "batches/c3_batch_001/failure.summary.json": "OTHER_CONTROL_EVIDENCE",
        "unknown.bin": "UNCLASSIFIED",
    }
    assert {
        path: raw_retirement._retained_role(path) for path in cases
    } == cases
    assert len(raw_retirement.RETAINED_ROLE_NAMES) == 14
    assert raw_retirement._retained_role(
        "raw/c3_batch_000/partials/"
        + "a" * 64
        + ".wrong_attempt.partial"
    ) == "UNCLASSIFIED"


def test_r5e_receipt_and_aggregate_summary_are_closed_and_bound() -> None:
    retained = {
        "file_count": raw_retirement.EXPECTED_RETAINED_FILES,
        "total_bytes": raw_retirement.EXPECTED_RETAINED_BYTES,
        "file_metadata_sha256": "a" * 64,
        "directory_topology_sha256": "b" * 64,
        "role_inventory_sha256": "d" * 64,
        "control_content_sha256": "e" * 64,
    }
    diagnostic_sha = "f" * 64
    manifest = {
        "governing_commit": "c" * 40,
        "retained_role_inventory_sha256": retained[
            "role_inventory_sha256"
        ],
        "retained_control_content_sha256": retained[
            "control_content_sha256"
        ],
        "diagnostic_evidence_authority_sha256": diagnostic_sha,
    }
    manifest_payload = raw_retirement._canonical(manifest)
    receipt = raw_retirement._post_receipt(
        manifest_payload=manifest_payload,
        manifest=manifest,
        deleted_files=raw_retirement.EXPECTED_DELETE_FILES,
        deleted_bytes=raw_retirement.EXPECTED_DELETE_BYTES,
        retained=retained,
        r4_authority={
            "metadata_stat_sha256": (
                raw_retirement.EXPECTED_R4_METADATA_SHA256
            )
        },
        status="PASS_OLDER_RAW_DUPLICATES_RETIRED",
    )
    raw_retirement._validate_receipt(
        receipt, manifest_payload=manifest_payload
    )
    receipt_payload = raw_retirement._canonical(receipt)
    summary = raw_retirement._summary_from_receipt(
        receipt, receipt_payload=receipt_payload
    )
    assert set(summary) == raw_retirement.SUMMARY_KEYS
    assert summary["receipt_sha256"] == hashlib.sha256(
        receipt_payload
    ).hexdigest()
    raw_retirement._validate_safe_export(raw_retirement._canonical(summary))
    for key in ("actual_deleted_files", "actual_deleted_bytes", "receipt_sha256"):
        changed = copy.deepcopy(summary)
        changed[key] = -1 if key != "receipt_sha256" else "0" * 64
        assert changed != raw_retirement._summary_from_receipt(
            receipt, receipt_payload=receipt_payload
        )


def test_r5e_capacity_gain_classification_is_exact() -> None:
    historical = capacity.EXPECTED_RESEARCH_QUOTA_KIB * 1024
    base = {
        "status": capacity.DYNAMIC_SUCCESSOR_STATUS_PASS,
        "live_research_quota_bytes": historical,
        "live_research_usage_bytes": 1,
        "live_research_filesystem_available_bytes": (
            sequential.SUCCESSOR_INCREMENT_BYTES
            + sequential.SUCCESSOR_REQUIRED_RESERVE_BYTES
        ),
        "remaining_file_slots": sequential.SUCCESSOR_REQUIRED_FILE_SLOTS,
    }
    assert sequential._capacity_gain_source(
        base, evidence_role="R5E_R2_PRE_ACTION"
    ) == "EXISTING_HEADROOM"
    allocation = {**base, "live_research_quota_bytes": historical + 10**12}
    assert sequential._capacity_gain_source(
        allocation, evidence_role="R5E_R2_PRE_ACTION"
    ) == "ALLOCATION"
    blocked_pre = {
        **base,
        "status": capacity.DYNAMIC_SUCCESSOR_STATUS_ALLOCATION_PENDING,
        "live_research_usage_bytes": 527_008_808_960,
    }
    cleanup = {
        **base,
        "live_research_usage_bytes": 393_186_449_614,
    }
    assert sequential._capacity_gain_source(
        cleanup,
        evidence_role="R5E_POST_CLEANUP",
        pre_cleanup_observation=blocked_pre,
    ) == "CLEANUP"
    assert sequential._capacity_gain_source(
        blocked_pre, evidence_role="R5E_R2_PRE_ACTION"
    ) == "NONE"


def test_r5e_claim_v3_binds_capacity_role_and_retirement_receipt() -> None:
    assert sequential.FULL_SUBMISSION_CLAIM_KEYS.isdisjoint(
        {
            "dynamic_capacity_receipt_sha256",
            "raw_retirement_receipt_sha256",
            "capacity_gain_source",
        }
    )
    assert sequential.FRESH_FULL_SUBMISSION_CLAIM_V3_KEYS == frozenset({
        *sequential.FRESH_FULL_SUBMISSION_CLAIM_V2_KEYS,
        "capacity_evidence_role", "capacity_gain_source",
        "raw_retirement_status", "raw_retirement_receipt_sha256",
    })
    run = SimpleNamespace(
        authority=SimpleNamespace(governing_commit="a" * 40),
        attempt_id="lvef_c3_full_" + "b" * 16 + "_" + "a" * 8,
        plan_sha256="c" * 64,
        plan={"authority": {"git_commit": "a" * 40}},
        runtime_authority={"git_commit": "a" * 40},
        launch_authority_sha256="d" * 64,
    )
    claim = sequential._expected_submission_claim(
        run,
        capacity_receipt_sha256="e" * 64,
        dynamic_capacity_receipt_sha256="f" * 64,
        capacity_evidence_role="R5E_POST_CLEANUP",
        capacity_gain_source="CLEANUP",
        raw_retirement_status="PASS_OLDER_RAW_DUPLICATES_RETIRED",
        raw_retirement_receipt_sha256="1" * 64,
        qsub_environment_sha256="2" * 64,
    )
    assert claim["schema_version"] == 3
    assert claim["raw_retirement_receipt_sha256"] == "1" * 64
    assert claim["capacity_gain_source"] == "CLEANUP"
    pre_action_claim = sequential._expected_submission_claim(
        run,
        capacity_receipt_sha256="e" * 64,
        dynamic_capacity_receipt_sha256="f" * 64,
        capacity_evidence_role="R5E_R2_PRE_ACTION",
        capacity_gain_source="ALLOCATION",
        raw_retirement_status="NOT_APPLICABLE_CAPACITY_ALREADY_PASSING",
        raw_retirement_receipt_sha256=(
            "NOT_APPLICABLE_CAPACITY_ALREADY_PASSING"
        ),
        qsub_environment_sha256="2" * 64,
    )
    assert pre_action_claim["capacity_evidence_role"] == "R5E_R2_PRE_ACTION"
    assert "post_cleanup_capacity_evidence_role" not in pre_action_claim
    _expect(
        "FULL_SEQUENTIAL_PREPARED_CAPACITY_INVALID",
        lambda: sequential._expected_submission_claim(
            run,
            capacity_receipt_sha256="e" * 64,
            dynamic_capacity_receipt_sha256="f" * 64,
            capacity_evidence_role="R5E_PRE_CLEANUP",
            capacity_gain_source="ALLOCATION",
            raw_retirement_status="NOT_APPLICABLE_CLEANUP_SKIPPED",
            raw_retirement_receipt_sha256="NOT_APPLICABLE_CLEANUP_SKIPPED",
            qsub_environment_sha256="2" * 64,
        ),
    )
    _expect(
        "FULL_SEQUENTIAL_PREPARED_CAPACITY_INVALID",
        lambda: sequential._expected_submission_claim(
            run,
            capacity_receipt_sha256="e" * 64,
            dynamic_capacity_receipt_sha256="f" * 64,
            capacity_evidence_role="R5E_POST_CLEANUP",
            capacity_gain_source="CLEANUP",
            raw_retirement_status="PASS_OLDER_RAW_DUPLICATES_RETIRED",
            raw_retirement_receipt_sha256=(
                "NOT_APPLICABLE_CLEANUP_SKIPPED"
            ),
            qsub_environment_sha256="2" * 64,
        ),
    )


def test_r5e_capacity_publisher_keeps_historical_pairs_and_adds_r2_pair() -> None:
    allowed = capacity.DYNAMIC_SUCCESSOR_ALLOWED_EVIDENCE_BASENAME_PAIRS
    assert (
        capacity.R5E_PRE_CLEANUP_RESTRICTED_RECEIPT_BASENAME,
        capacity.R5E_PRE_CLEANUP_AGGREGATE_SUMMARY_BASENAME,
    ) in allowed
    assert (
        capacity.R5E_R2_PRE_ACTION_RESTRICTED_RECEIPT_BASENAME,
        capacity.R5E_R2_PRE_ACTION_AGGREGATE_SUMMARY_BASENAME,
    ) in allowed
    assert (
        capacity.R5E_POST_CLEANUP_RESTRICTED_RECEIPT_BASENAME,
        capacity.R5E_POST_CLEANUP_AGGREGATE_SUMMARY_BASENAME,
    ) in allowed
    assert len(allowed) == 4
    parser = sequential._parser()
    parsed = parser.parse_args(["--seal-r5e-r2-pre-action-capacity"])
    assert parsed.seal_r5e_r2_pre_action_capacity is True
    assert "restricted_receipt_path" not in inspect.signature(
        sequential.run_dynamic_successor_capacity_seal
    ).parameters


def test_r5e_cleanup_requires_the_exact_failed_pre_cleanup_capacity_pair() -> None:
    sealed_receipt = {
        "governing_commit": raw_retirement.R5E_R2_PRE_ACTION_GOVERNING_COMMIT,
        "captured_at_utc": "2026-08-01T12:34:56Z",
    }
    blocked = capacity.DynamicSuccessorCapacityCapture(
        receipt={},
        receipt_payload=b"sealed-pre-capacity\n",
        observation={
            "status": capacity.DYNAMIC_SUCCESSOR_STATUS_ALLOCATION_PENDING
        },
    )
    with (
        mock.patch.object(
            capacity,
            "load_dynamic_successor_capacity_capture",
            return_value=blocked,
        ) as loader,
        mock.patch.object(
            capacity,
            "validate_production_dynamic_successor_capacity_capture",
            return_value=blocked,
        ) as validator,
        mock.patch.object(
            raw_retirement,
            "_read_json",
            return_value=(sealed_receipt, raw_retirement._canonical(sealed_receipt)),
        ),
    ):
        authority = raw_retirement._pre_cleanup_capacity_authority()
    assert authority["status"] == capacity.DYNAMIC_SUCCESSOR_STATUS_ALLOCATION_PENDING
    assert authority["receipt_sha256"] == hashlib.sha256(
        blocked.receipt_payload
    ).hexdigest()
    assert loader.call_args.kwargs["restricted_receipt_path"].name == (
        capacity.R5E_R2_PRE_ACTION_RESTRICTED_RECEIPT_BASENAME
    )
    assert loader.call_args.kwargs["expected_governing_commit"] == (
        raw_retirement.R5E_R2_PRE_ACTION_GOVERNING_COMMIT
    )
    assert loader.call_args.kwargs["now_utc"].isoformat() == (
        "2026-08-01T12:34:56+00:00"
    )
    assert validator.call_args.kwargs["now_utc"] == (
        loader.call_args.kwargs["now_utc"]
    )

    wrong_commit = {**sealed_receipt, "governing_commit": "0" * 40}
    with (
        mock.patch.object(
            raw_retirement, "_read_json", return_value=(wrong_commit, b"wrong\n")
        ),
        mock.patch.object(
            capacity, "load_dynamic_successor_capacity_capture"
        ) as loader,
    ):
        _expect(
            "OLDER_RAW_PRE_CLEANUP_CAPACITY_AUTHORITY_INVALID",
            raw_retirement._pre_cleanup_capacity_authority,
        )
    loader.assert_not_called()

    with (
        mock.patch.object(
            raw_retirement,
            "_read_json",
            return_value=(sealed_receipt, raw_retirement._canonical(sealed_receipt)),
        ),
        mock.patch.object(
            capacity,
            "load_dynamic_successor_capacity_capture",
            side_effect=ValueError("sealed pair hash mismatch"),
        ),
    ):
        _expect(
            "OLDER_RAW_PRE_CLEANUP_CAPACITY_AUTHORITY_INVALID",
            raw_retirement._pre_cleanup_capacity_authority,
        )

    passed = copy.deepcopy(blocked.observation)
    passed["status"] = capacity.DYNAMIC_SUCCESSOR_STATUS_PASS
    pass_capture = capacity.DynamicSuccessorCapacityCapture(
        receipt={}, receipt_payload=b"pass\n", observation=passed
    )
    with (
        mock.patch.object(
            capacity,
            "load_dynamic_successor_capacity_capture",
            return_value=pass_capture,
        ),
        mock.patch.object(
            capacity,
            "validate_production_dynamic_successor_capacity_capture",
            return_value=pass_capture,
        ),
        mock.patch.object(
            raw_retirement,
            "_read_json",
            return_value=(sealed_receipt, raw_retirement._canonical(sealed_receipt)),
        ),
    ):
        _expect(
            "OLDER_RAW_UNAUTHORIZED_CLEANUP_AFTER_CAPACITY_PASS",
            raw_retirement._pre_cleanup_capacity_authority,
        )


def test_r5e_post_capacity_admission_requires_pre_failure_and_cleanup_receipt() -> None:
    assert "require_pass=False" in inspect.getsource(
        sequential.run_dynamic_successor_capacity_seal
    )
    historical = capacity.EXPECTED_RESEARCH_QUOTA_KIB * 1024
    blocked = {
        "status": capacity.DYNAMIC_SUCCESSOR_STATUS_ALLOCATION_PENDING,
        "live_research_quota_bytes": historical,
        "live_research_usage_bytes": 527_008_808_960,
        "live_research_filesystem_available_bytes": 1_674_160_635_904,
        "remaining_file_slots": 33_052_951,
    }
    passed = {
        **blocked,
        "status": capacity.DYNAMIC_SUCCESSOR_STATUS_PASS,
        "live_research_usage_bytes": 393_186_449_614,
    }
    pre = capacity.DynamicSuccessorCapacityCapture({}, b"pre", blocked)
    post = capacity.DynamicSuccessorCapacityCapture({}, b"post", passed)
    pre_authority = {
        "status": blocked["status"],
        "receipt_basename": (
            capacity.R5E_R2_PRE_ACTION_RESTRICTED_RECEIPT_BASENAME
        ),
        "receipt_bytes": len(pre.receipt_payload),
        "receipt_sha256": hashlib.sha256(pre.receipt_payload).hexdigest(),
        "summary_basename": (
            capacity.R5E_R2_PRE_ACTION_AGGREGATE_SUMMARY_BASENAME
        ),
        "summary_bytes": len(capacity._canonical(pre.observation)),
        "summary_sha256": hashlib.sha256(
            capacity._canonical(pre.observation)
        ).hexdigest(),
    }
    with tempfile.TemporaryDirectory() as temporary:
        production = Path(temporary).resolve()
        owner = production / "owner_private"
        owner.mkdir(mode=0o700)
        for basename in (
            capacity.R5E_R2_PRE_ACTION_RESTRICTED_RECEIPT_BASENAME,
            capacity.R5E_R2_PRE_ACTION_AGGREGATE_SUMMARY_BASENAME,
            capacity.R5E_POST_CLEANUP_RESTRICTED_RECEIPT_BASENAME,
            capacity.R5E_POST_CLEANUP_AGGREGATE_SUMMARY_BASENAME,
        ):
            _private_file(owner / basename, b"sealed")
        run = SimpleNamespace(
            production_root=production,
            authority=SimpleNamespace(governing_commit="c" * 40),
        )

        pass_requirements: list[tuple[str, bool]] = []

        def load(
            _run, *, restricted_basename, summary_basename, require_pass=True
        ):
            del _run, summary_basename
            pass_requirements.append((restricted_basename, require_pass))
            return post if "post_cleanup" in restricted_basename else pre

        with (
            mock.patch.object(sequential, "_load_capacity_pair", side_effect=load),
            mock.patch.object(
                raw_retirement,
                "validate_retired_state",
                return_value={
                    "status": "PASS_OLDER_RAW_DUPLICATES_RETIRED",
                    "receipt_sha256": "d" * 64,
                    "pre_cleanup_capacity_authority": pre_authority,
                },
            ),
        ):
            admission = sequential._load_fixed_capacity_admission(run)
        assert admission.capture is post
        assert admission.evidence_role == "R5E_POST_CLEANUP"
        assert admission.capacity_gain_source == "CLEANUP"
        assert admission.raw_retirement_receipt_sha256 == "d" * 64
        assert pass_requirements == [
            (
                capacity.R5E_R2_PRE_ACTION_RESTRICTED_RECEIPT_BASENAME,
                False,
            ),
            (
                capacity.R5E_POST_CLEANUP_RESTRICTED_RECEIPT_BASENAME,
                True,
            ),
        ]

        pre_pass = capacity.DynamicSuccessorCapacityCapture({}, b"pre", passed)
        with (
            mock.patch.object(
                sequential,
                "_load_capacity_pair",
                side_effect=lambda _run, *, restricted_basename,
                summary_basename, require_pass=True: (
                    post if "post_cleanup" in restricted_basename else pre_pass
                ),
            ),
            mock.patch.object(
                raw_retirement,
                "validate_retired_state",
                return_value={
                    "status": "PASS_OLDER_RAW_DUPLICATES_RETIRED",
                    "receipt_sha256": "d" * 64,
                    "pre_cleanup_capacity_authority": pre_authority,
                },
            ),
        ):
            _expect(
                "FULL_SEQUENTIAL_UNAUTHORIZED_CLEANUP_AFTER_CAPACITY_PASS",
                lambda: sequential._load_fixed_capacity_admission(run),
            )

        wrong_pre = {**pre_authority, "receipt_sha256": "0" * 64}
        with (
            mock.patch.object(sequential, "_load_capacity_pair", side_effect=load),
            mock.patch.object(
                raw_retirement,
                "validate_retired_state",
                return_value={
                    "status": "PASS_OLDER_RAW_DUPLICATES_RETIRED",
                    "receipt_sha256": "d" * 64,
                    "pre_cleanup_capacity_authority": wrong_pre,
                },
            ),
        ):
            _expect(
                "FULL_SEQUENTIAL_OLDER_RAW_RETIREMENT_INVALID",
                lambda: sequential._load_fixed_capacity_admission(run),
            )


def test_r5e_r2_pre_action_is_the_only_no_cleanup_current_role() -> None:
    observation = {
        "status": capacity.DYNAMIC_SUCCESSOR_STATUS_PASS,
        "live_research_quota_bytes": capacity.EXPECTED_RESEARCH_QUOTA_KIB
        * 1024
        + 10**12,
        "live_research_usage_bytes": 1,
        "live_research_filesystem_available_bytes": (
            sequential.SUCCESSOR_INCREMENT_BYTES
            + sequential.SUCCESSOR_REQUIRED_RESERVE_BYTES
        ),
        "remaining_file_slots": sequential.SUCCESSOR_REQUIRED_FILE_SLOTS,
    }
    captured = capacity.DynamicSuccessorCapacityCapture({}, b"r2", observation)
    with tempfile.TemporaryDirectory() as temporary:
        production = Path(temporary).resolve()
        owner = production / "owner_private"
        owner.mkdir(mode=0o700)
        for basename in (
            capacity.R5E_R2_PRE_ACTION_RESTRICTED_RECEIPT_BASENAME,
            capacity.R5E_R2_PRE_ACTION_AGGREGATE_SUMMARY_BASENAME,
        ):
            _private_file(owner / basename, b"sealed")
        run = SimpleNamespace(
            production_root=production,
            authority=SimpleNamespace(governing_commit="c" * 40),
        )
        with mock.patch.object(
            sequential, "_load_capacity_pair", return_value=captured
        ) as loader:
            admission = sequential._load_fixed_capacity_admission(run)
    assert admission.evidence_role == "R5E_R2_PRE_ACTION"
    assert admission.capacity_gain_source == "ALLOCATION"
    assert admission.raw_retirement_status == (
        "NOT_APPLICABLE_CAPACITY_ALREADY_PASSING"
    )
    assert loader.call_args.kwargs == {
        "restricted_basename": (
            capacity.R5E_R2_PRE_ACTION_RESTRICTED_RECEIPT_BASENAME
        ),
        "summary_basename": (
            capacity.R5E_R2_PRE_ACTION_AGGREGATE_SUMMARY_BASENAME
        ),
    }


def test_r5e_retirement_module_never_opens_dicom_or_npz_payloads() -> None:
    source = (SCRIPTS / "retire_lvef_c3_older_raw_duplicates.py").read_text()
    tree = ast.parse(source)
    forbidden_names = {
        "sha256_file", "validate_extracted_npz", "np.load",
        "materialize_verified_download_manifest",
    }
    observed = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                observed.add(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                observed.add(node.func.attr)
    assert forbidden_names.isdisjoint(observed)
