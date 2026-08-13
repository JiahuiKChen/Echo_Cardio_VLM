from __future__ import annotations

import base64
from contextlib import contextmanager, ExitStack, redirect_stderr, redirect_stdout
from dataclasses import replace
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any, Iterator
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import lvef_c3_canary_manifest as manifest_contract
import lvef_c3_minimal_canary as minimal
import lvef_c3_orchestration_core as core


_OBJECT_COUNTS = (1, 1, 1, 2, 2, 3, 1, 1, 3, 3)
_EXPECTED_SELECTED_STUDIES = (
    "9100001",
    "9100002",
    "9100004",
    "9100005",
    "9100006",
)


def _expect(code: str, operation: Any) -> None:
    try:
        operation()
    except minimal.MinimalCanaryError as exc:
        assert exc.code == code
    else:
        raise AssertionError(f"expected {code}")


def _expect_manifest(code: str, operation: Any) -> None:
    try:
        operation()
    except manifest_contract.CanaryManifestError as exc:
        assert exc.code == code
    else:
        raise AssertionError(f"expected {code}")


@contextmanager
def _small_shape() -> Iterator[None]:
    expected = {
        "EXPECTED_SELECTED_STUDIES": 10,
        "EXPECTED_SPLIT_COUNTS": {"train": 8, "val": 1, "test": 1},
        "EXPECTED_HISTORICAL_SELECTED_STUDIES": 9,
        "EXPECTED_HISTORICAL_NO_CINE_STUDIES": 1,
        "EXPECTED_TRAIN_NO_CINE_STUDIES": 1,
        "EXPECTED_PRIOR_SMOKE_STUDIES": 1,
        "EXPECTED_RAW_SOURCE_ROWS": 20,
        "EXPECTED_NORMALIZED_SOURCE_OBJECTS": 18,
        "EXPECTED_COLLAPSED_SOURCE_ROWS": 2,
    }
    with ExitStack() as stack:
        for name, value in expected.items():
            stack.enter_context(mock.patch.object(minimal, name, value))
        yield


def _synthetic_evidence() -> minimal.FixedSelectionEvidence:
    selected_rows: list[dict[str, Any]] = []
    selected_source_rows: list[dict[str, Any]] = []
    metadata_rows: list[dict[str, Any]] = []
    split_rows: list[dict[str, Any]] = []
    historical_rows: list[dict[str, Any]] = []
    smoke_rows: list[dict[str, Any]] = []
    for index, object_count in enumerate(_OBJECT_COUNTS, start=1):
        subject = str(8_100_000 + index)
        study = str(9_100_000 + index)
        split = "train" if index <= 8 else ("val" if index == 9 else "test")
        raw_count = object_count + (1 if index in {1, 9} else 0)
        selected_rows.append(
            {
                "subject_id": subject,
                "study_id": study,
                "n_dicoms": str(raw_count),
            }
        )
        split_rows.append({"subject_id": subject, "split": split})
        if index != 7:
            historical_rows.append({"subject_id": subject, "study_id": study})
        if index == 8:
            smoke_rows.append({"subject_id": subject, "study_id": study})
        for ordinal in range(1, object_count + 1):
            source_path = (
                f"files/p08/p{subject}/s{study}/synthetic_{ordinal:02d}.dcm"
            )
            object_key = hashlib.sha256(
                f"{manifest_contract.SOURCE_RELEASE}\0{source_path}".encode("utf-8")
            ).hexdigest()
            source = {
                "release_id": manifest_contract.SOURCE_RELEASE,
                "subject_id": subject,
                "study_id": study,
                "split": split,
                "source_relative_path": source_path,
                "source_object_key": object_key,
            }
            selected_source_rows.append(source)
            metadata_rows.append(
                {
                    **source,
                    "production_batch": "c3_batch_000",
                    "remote_size_bytes": index * 100 + ordinal,
                    "remote_generation": str(1000 + index * 10 + ordinal),
                    "remote_md5_base64": base64.b64encode(
                        bytes([index]) * 16
                    ).decode("ascii"),
                    "remote_crc32c_base64": base64.b64encode(
                        bytes([ordinal]) * 4
                    ).decode("ascii"),
                    "preflight_status": "PASS",
                    "discrepancy_reasons": [],
                }
            )
    return minimal.FixedSelectionEvidence(
        selected_rows=tuple(selected_rows),
        selected_source_rows=tuple(selected_source_rows),
        source_metadata_rows=tuple(metadata_rows),
        split_rows=tuple(split_rows),
        historical_rows=tuple(historical_rows),
        prior_smoke_rows=tuple(smoke_rows),
        hashes={
            "selected_studies": "1" * 64,
            "source_metadata": "2" * 64,
            "historical_study_manifest": "3" * 64,
            "prior_smoke_source_manifest": "4" * 64,
            "prior_smoke_source_summary": "5" * 64,
            "prior_smoke_source_safety": "6" * 64,
            "prior_smoke_preservation_manifest": "7" * 64,
        },
    )


def _reversed_evidence(evidence: minimal.FixedSelectionEvidence) -> minimal.FixedSelectionEvidence:
    return replace(
        evidence,
        selected_rows=tuple(reversed(evidence.selected_rows)),
        selected_source_rows=tuple(reversed(evidence.selected_source_rows)),
        source_metadata_rows=tuple(reversed(evidence.source_metadata_rows)),
        split_rows=tuple(reversed(evidence.split_rows)),
        historical_rows=tuple(reversed(evidence.historical_rows)),
        prior_smoke_rows=tuple(reversed(evidence.prior_smoke_rows)),
    )


def _selected_and_manifest(
    evidence: minimal.FixedSelectionEvidence,
) -> tuple[list[Any], dict[str, Any]]:
    candidates, source_objects = minimal._project_selection_candidates(evidence)
    selected = manifest_contract.select_exact_five(candidates)
    selected_pairs = {(row.subject_id, row.study_id) for row in selected}
    sealed = manifest_contract.build_sealed_manifest(
        selected_studies=selected,
        source_objects=[
            row
            for row in source_objects
            if (row["subject_id"], row["study_id"]) in selected_pairs
        ],
        source_authority_commit="a" * 40,
        source_manifest_sha256="b" * 64,
        source_configuration_hashes={"synthetic_projection": "c" * 64},
    )
    return selected, sealed


def _private_file(path: Path, payload: bytes) -> Path:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_bytes(payload)
    path.chmod(0o600)
    return path


def _csv_payload(rows: tuple[dict[str, Any], ...] | list[dict[str, Any]]) -> bytes:
    assert rows
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


def _authority(
    root: Path,
    *,
    selected_studies: Path | None = None,
    selected_source: Path | None = None,
    source_metadata: Path | None = None,
    split_map: Path | None = None,
    selected_studies_sha256: str = "1" * 64,
    selected_source_sha256: str = "9" * 64,
    split_map_sha256: str = "8" * 64,
) -> minimal.LiveAuthority:
    placeholder = root / "unused.restricted"
    return minimal.LiveAuthority(
        governing_commit="a" * 40,
        selection_authority_commit="a" * 40,
        legacy_session_sha256="7" * 64,
        legacy_session_size=1,
        legacy_session_repeated_assignment_count=0,
        legacy_session_repeated_name_count=0,
        legacy_session_conflict_count=0,
        checkpoint=placeholder,
        checkpoint_sha256="3" * 64,
        environment_receipt=placeholder,
        environment_receipt_sha256="4" * 64,
        gcloud=placeholder,
        gcloud_sha256="5" * 64,
        gcloud_receipt=placeholder,
        gcloud_receipt_sha256="6" * 64,
        cloudsdk_config=root,
        crc32c_python=placeholder,
        crc32c_worker=placeholder,
        crc32c_python_sha256="a" * 64,
        crc32c_worker_sha256="b" * 64,
        crc32c_distribution_sha256="c" * 64,
        billing_variable="LVEF_C3_GCP_BILLING_PROJECT",
        billing_project="synthetic-private-project",
        selected_studies=selected_studies or placeholder,
        selected_studies_sha256=selected_studies_sha256,
        selected_source=selected_source or placeholder,
        selected_source_sha256=selected_source_sha256,
        source_metadata=source_metadata or placeholder,
        split_map=split_map or placeholder,
        split_map_sha256=split_map_sha256,
    )


def test_pure_projection_is_deterministic_and_exclusions_survive_real_sealing() -> None:
    evidence = _synthetic_evidence()
    with _small_shape():
        candidates, objects = minimal._project_selection_candidates(evidence)
        reversed_candidates, reversed_objects = minimal._project_selection_candidates(
            _reversed_evidence(evidence)
        )
        selected, sealed = _selected_and_manifest(evidence)
        _, reversed_sealed = _selected_and_manifest(_reversed_evidence(evidence))

    assert candidates == reversed_candidates
    assert objects == reversed_objects
    assert manifest_contract.serialize_manifest(sealed) == (
        manifest_contract.serialize_manifest(reversed_sealed)
    )
    flags = {row["study_id"]: row for row in candidates}
    assert flags["9100007"]["known_no_cine"] is True
    assert flags["9100008"]["prior_reconstruction_smoke"] is True
    assert flags["9100009"]["split"] == "val"
    assert flags["9100010"]["split"] == "test"
    assert flags["9100001"]["expected_object_count"] == 1
    assert evidence.selected_rows[0]["n_dicoms"] == "2"
    assert tuple(row.study_id for row in selected) == _EXPECTED_SELECTED_STUDIES
    assert not ({"9100007", "9100008", "9100009", "9100010"} & set(_EXPECTED_SELECTED_STUDIES))
    body = sealed["manifest"]
    assert body["study_count"] == body["subject_count"] == 5
    assert body["expected_object_count"] == 9 <= manifest_contract.MAXIMUM_OBJECTS
    assert body["expected_byte_total"] == 3914 <= manifest_contract.MAXIMUM_EXPECTED_BYTES
    assert all(
        row["split"] == "train"
        and row["known_no_cine"] is False
        and row["prior_reconstruction_smoke"] is False
        for row in body["studies"]
    )


def test_selector_enforces_both_hard_ceilings_without_substitution() -> None:
    with _small_shape():
        candidates, _ = minimal._project_selection_candidates(_synthetic_evidence())
    object_overflow = [
        {**row, "expected_object_count": 151}
        if row["split"] == "train"
        and not row["known_no_cine"]
        and not row["prior_reconstruction_smoke"]
        else row
        for row in candidates
    ]
    byte_overflow = [
        {**row, "expected_byte_total": 1_000_000_001}
        if row["split"] == "train"
        and not row["known_no_cine"]
        and not row["prior_reconstruction_smoke"]
        else row
        for row in candidates
    ]
    _expect_manifest(
        "CANARY_OBJECT_CEILING_EXCEEDED",
        lambda: manifest_contract.select_exact_five(object_overflow),
    )
    _expect_manifest(
        "CANARY_BYTE_CEILING_EXCEEDED",
        lambda: manifest_contract.select_exact_five(byte_overflow),
    )


def _materialize_loader_authorities(
    root: Path, evidence: minimal.FixedSelectionEvidence, *, outcome_field: bool
) -> tuple[minimal.LiveAuthority, dict[str, Any]]:
    selected_rows = [dict(row) for row in evidence.selected_rows]
    if outcome_field:
        for row in selected_rows:
            row["endpoint_value"] = "forbidden"
    selected = _private_file(root / "selected.csv", _csv_payload(selected_rows))
    selected_source = _private_file(
        root / "selected_source.csv", _csv_payload(list(evidence.selected_source_rows))
    )
    source_metadata_payload = b"".join(
        json.dumps(dict(row), sort_keys=True, separators=(",", ":")).encode("utf-8")
        + b"\n"
        for row in evidence.source_metadata_rows
    )
    source_metadata = _private_file(root / "metadata.jsonl", source_metadata_payload)
    split_map = _private_file(root / "split.csv", _csv_payload(list(evidence.split_rows)))
    historical = _private_file(
        root / "historical.csv", _csv_payload(list(evidence.historical_rows))
    )
    smoke_payload = _csv_payload(list(evidence.prior_smoke_rows))
    smoke = _private_file(root / "smoke.restricted.csv", smoke_payload)
    historical_sha = core.sha256_file(historical)
    selected_source_sha = core.sha256_file(selected_source)
    smoke_sha = core.sha256_file(smoke)
    summary = {
        "status": "PASS",
        "schema_version": 2,
        "historical_study_manifest_sha256": historical_sha,
        "selected_source_manifest_sha256": selected_source_sha,
        "technical_smoke_source_manifest_sha256": smoke_sha,
        "n_selected_subjects": 4_530,
        "n_selected_studies": 4_530,
        "n_source_studies": 4_530,
        "n_source_objects": 335_984,
        "n_source_manifest_input_rows": 336_016,
        "n_source_locator_duplicate_groups": 32,
        "n_source_manifest_rows_collapsed": 32,
        "n_source_duplicate_rows_total": 64,
        "maximum_source_record_multiplicity": 2,
        "n_source_locator_conflict_groups": 0,
        "n_outside_selected_source_studies": 0,
        "n_missing_selected_source_studies": 0,
        "source_paths_safe_and_normalized": True,
        "source_ownership_exact": True,
        "source_objects_unique": True,
        "raw_source_row_counts_match_selected_n_dicoms_authority": True,
        "source_locator_conflict_gate_passed": True,
        "source_object_key_bijection_gate_passed": True,
        "smoke_n_roles": 4,
        "smoke_n_studies": 4,
        "smoke_all_train": True,
        "outcomes_read": False,
        "predictions_read": False,
        "embedding_arrays_read": False,
        "performance_computed": False,
        "source_values_emitted_to_stdout": False,
        "candidate_construction_mode": "phase1d_restricted_provenance",
        "locked_split_counts_match": True,
    }
    safety = {
        "status": "PASS",
        "aggregate_contains_identifiers": False,
        "aggregate_contains_object_locators": False,
        "restricted_outputs_outside_repository": True,
        "smoke_hard_caps_passed": True,
        "outcome_blind_selection_passed": True,
        "restricted_input_authority_hash_gate_passed": True,
        "locked_split_counts_gate_passed": True,
        "gcs_exact_object_metadata_gate_required": True,
        "source_locator_conflict_gate_passed": True,
        "source_locator_reconciliation_recorded": True,
        "raw_n_dicoms_reconciliation_gate_passed": True,
    }
    summary_path = _private_file(
        root / "summary.json", json.dumps(summary, sort_keys=True).encode("utf-8")
    )
    safety_path = _private_file(
        root / "safety.json", json.dumps(safety, sort_keys=True).encode("utf-8")
    )
    preservation_payload = (
        "relative_path\tsize_bytes\tsha256\n"
        f"{minimal.PRIOR_SMOKE_SOURCE_RELATIVE_PATH}\t{len(smoke_payload)}\t{smoke_sha}\n"
    ).encode("utf-8")
    preservation = _private_file(root / "preservation.tsv", preservation_payload)
    authority = _authority(
        root,
        selected_studies=selected,
        selected_source=selected_source,
        source_metadata=source_metadata,
        split_map=split_map,
        selected_studies_sha256=core.sha256_file(selected),
        selected_source_sha256=selected_source_sha,
        split_map_sha256=core.sha256_file(split_map),
    )
    patches = {
        "HISTORICAL_STUDY_MANIFEST_PATH": historical,
        "HISTORICAL_STUDY_MANIFEST_SHA256": historical_sha,
        "PRIOR_SMOKE_SOURCE_MANIFEST_PATH": smoke,
        "PRIOR_SMOKE_SOURCE_SUMMARY_PATH": summary_path,
        "PRIOR_SMOKE_SOURCE_SAFETY_PATH": safety_path,
        "PRIOR_SMOKE_PRESERVATION_MANIFEST_PATH": preservation,
        "PRIOR_SMOKE_PRESERVATION_MANIFEST_SHA256": core.sha256_file(preservation),
    }
    return authority, patches


def test_fixed_evidence_loader_rejects_outcome_bearing_fields() -> None:
    evidence = _synthetic_evidence()
    with tempfile.TemporaryDirectory(dir=str(minimal.SAFE_TEMPORARY_ROOT)) as directory:
        authority, values = _materialize_loader_authorities(
            Path(directory).resolve(), evidence, outcome_field=True
        )
        with ExitStack() as stack:
            for name, value in values.items():
                stack.enter_context(mock.patch.object(minimal, name, value))
            _expect_manifest(
                "PROHIBITED_FIELD_PRESENT",
                lambda: minimal._load_fixed_selection_evidence(authority),
            )


def test_fixed_evidence_loader_accepts_public_scientific_authority_modes() -> None:
    evidence = _synthetic_evidence()
    with tempfile.TemporaryDirectory(dir=str(minimal.SAFE_TEMPORARY_ROOT)) as directory:
        authority, values = _materialize_loader_authorities(
            Path(directory).resolve(), evidence, outcome_field=False
        )
        for path in (
            authority.selected_studies,
            authority.split_map,
            values["HISTORICAL_STUDY_MANIFEST_PATH"],
            values["PRIOR_SMOKE_SOURCE_SUMMARY_PATH"],
            values["PRIOR_SMOKE_SOURCE_SAFETY_PATH"],
            values["PRIOR_SMOKE_PRESERVATION_MANIFEST_PATH"],
        ):
            path.chmod(0o644)
        with ExitStack() as stack:
            for name, value in values.items():
                stack.enter_context(mock.patch.object(minimal, name, value))
            loaded = minimal._load_fixed_selection_evidence(authority)
        assert loaded.selected_rows == evidence.selected_rows
        assert loaded.selected_source_rows == evidence.selected_source_rows


def test_sealer_uses_fixed_path_is_no_clobber_and_has_no_external_effects() -> None:
    evidence = _synthetic_evidence()
    with tempfile.TemporaryDirectory(dir=str(minimal.SAFE_TEMPORARY_ROOT)) as directory:
        root = Path(directory).resolve()
        production_root = root / "production"
        owner_private = production_root / "owner_private"
        production_root.mkdir(mode=0o700)
        owner_private.mkdir(mode=0o700)
        output = owner_private / "exact_five_manifest.restricted.json"
        authority = _authority(root)
        with _small_shape():
            expected_candidates, expected_objects = minimal._project_selection_candidates(
                evidence
            )
            expected_selected = manifest_contract.select_exact_five(
                expected_candidates
            )
            expected_pairs = {
                (row.subject_id, row.study_id) for row in expected_selected
            }
            expected_manifest = manifest_contract.build_sealed_manifest(
                selected_studies=expected_selected,
                source_objects=[
                    row
                    for row in expected_objects
                    if (row["subject_id"], row["study_id"]) in expected_pairs
                ],
                source_authority_commit=authority.selection_authority_commit,
                source_manifest_sha256=authority.selected_source_sha256,
                source_configuration_hashes=minimal._manifest_configuration_hashes(
                    authority, evidence
                ),
            )
        expected_payload = manifest_contract.serialize_manifest(expected_manifest)
        forbidden = AssertionError("manifest sealing crossed an external effect boundary")
        plans: list[dict[str, Any]] = []

        def validate_plan(manifest: dict[str, Any], *, authority: Any) -> tuple[Any, ...]:
            plans.append(manifest)
            assert authority == _authority(root)
            return ({"status": "SYNTHETIC_PLAN_VALID"}, object(), {})

        with _small_shape(), ExitStack() as stack:
            patches = {
                "PRODUCTION_ROOT": production_root,
                "OWNER_PRIVATE_ROOT": owner_private,
                "EXACT_FIVE_MANIFEST_PATH": output,
                "_validate_live_no_row_gates": lambda supplied=None: supplied or authority,
                "_load_fixed_selection_evidence": lambda _authority: evidence,
                "discover_live_authority": lambda: authority,
                "_build_direct_manifest_plan": validate_plan,
                "_production_functions": mock.Mock(side_effect=forbidden),
            }
            for name, value in patches.items():
                stack.enter_context(mock.patch.object(minimal, name, value))
            stack.enter_context(
                mock.patch.object(
                    core,
                    "EXPECTED_SELECTED_SOURCE_MANIFEST_SHA256",
                    authority.selected_source_sha256,
                )
            )
            stack.enter_context(
                mock.patch.object(core, "EXPECTED_SPLIT_MAP_SHA256", authority.split_map_sha256)
            )
            stack.enter_context(
                mock.patch.object(core, "EXPECTED_CHECKPOINT_SHA256", authority.checkpoint_sha256)
            )
            stack.enter_context(
                mock.patch.object(
                    minimal, "CURRENT_ENVIRONMENT_SHA256", authority.environment_receipt_sha256
                )
            )
            run = stack.enter_context(
                mock.patch.object(minimal.subprocess, "run", side_effect=forbidden)
            )
            popen = stack.enter_context(
                mock.patch.object(minimal.subprocess, "Popen", side_effect=forbidden)
            )
            system = stack.enter_context(
                mock.patch.object(minimal.os, "system", side_effect=forbidden)
            )
            result = minimal.seal_exact_five_manifest(authority=authority)
            before = output.stat(follow_symlinks=False)
            payload = output.read_bytes()
            _expect(
                "MINIMAL_MANIFEST_OUTPUT_ALREADY_EXISTS",
                lambda: minimal.seal_exact_five_manifest(authority=authority),
            )

        assert result["status"] == "PASS_MINIMAL_EXACT_FIVE_MANIFEST_SEALED"
        assert result["study_count"] == result["subject_count"] == 5
        assert result["declared_object_count"] == 9
        assert result["declared_expected_bytes"] == 3914
        assert result["real_manifest_created"] is True
        assert result["ready_for_first_body_request"] == "YES"
        assert result["cloud_requests"] == result["qsub_submissions"] == 0
        assert result["dicom_bodies_processed"] == 0
        assert result["gpu_execution"] is False
        assert len(plans) == 2
        assert payload == expected_payload
        assert output.read_bytes() == payload
        after = output.stat(follow_symlinks=False)
        assert (before.st_dev, before.st_ino) == (after.st_dev, after.st_ino)
        assert (after.st_mode & 0o7777) == 0o600
        assert [path for path in owner_private.iterdir()] == [output]
        assert not (production_root / "minimal_canary_runs").exists()
        run.assert_not_called()
        popen.assert_not_called()
        system.assert_not_called()

        loaded = manifest_contract.load_and_validate_manifest(output)
        assert tuple(
            row["study_id"] for row in loaded["manifest"]["studies"]
        ) == _EXPECTED_SELECTED_STUDIES
        marker_output = io.StringIO()
        with redirect_stdout(marker_output):
            minimal._print_result(result)
        markers = set(marker_output.getvalue().splitlines())
        assert {
            "SEALED_EXACT_FIVE_MANIFEST=PASS",
            "REAL_CANARY_MANIFEST_CREATED=YES",
            "READY_FOR_FIRST_BODY_REQUEST=YES",
            "CLOUD_REQUESTS=0",
            "QSUB_SUBMISSIONS=0",
            "DICOM_BODIES_DOWNLOADED=NO",
            "GPU_EXECUTION=NO",
        }.issubset(markers)
        aggregate_output = marker_output.getvalue()
        assert all(
            forbidden not in aggregate_output
            for forbidden in (
                "8100001",
                "9100001",
                "files/p08/",
                str(owner_private),
                "synthetic-private-project",
            )
        )


def test_sealer_rejects_existing_run_or_authority_refresh_before_publication() -> None:
    evidence = _synthetic_evidence()
    with tempfile.TemporaryDirectory(dir=str(minimal.SAFE_TEMPORARY_ROOT)) as directory:
        root = Path(directory).resolve()
        production_root = root / "production"
        owner_private = production_root / "owner_private"
        production_root.mkdir(mode=0o700)
        owner_private.mkdir(mode=0o700)
        output = owner_private / "exact_five_manifest.restricted.json"
        authority = _authority(root)
        with _small_shape():
            selected, manifest = _selected_and_manifest(evidence)
            assert len(selected) == 5
            candidates, objects = minimal._project_selection_candidates(evidence)
            chosen = manifest_contract.select_exact_five(candidates)
            pairs = {(row.subject_id, row.study_id) for row in chosen}
            manifest = manifest_contract.build_sealed_manifest(
                selected_studies=chosen,
                source_objects=[
                    row
                    for row in objects
                    if (row["subject_id"], row["study_id"]) in pairs
                ],
                source_authority_commit=authority.selection_authority_commit,
                source_manifest_sha256=authority.selected_source_sha256,
                source_configuration_hashes=minimal._manifest_configuration_hashes(
                    authority, evidence
                ),
            )
        file_sha = hashlib.sha256(
            manifest_contract.serialize_manifest(manifest)
        ).hexdigest()
        run_parent = production_root / "minimal_canary_runs"
        run_parent.mkdir(mode=0o700)
        existing_run = run_parent / minimal._minimal_run_identity(
            file_sha, authority.governing_commit
        )
        existing_run.mkdir(mode=0o700)
        common = {
            "PRODUCTION_ROOT": production_root,
            "OWNER_PRIVATE_ROOT": owner_private,
            "EXACT_FIVE_MANIFEST_PATH": output,
            "_validate_live_no_row_gates": lambda supplied=None: supplied or authority,
            "_load_fixed_selection_evidence": lambda _authority: evidence,
            "_build_direct_manifest_plan": lambda *_args, **_kwargs: ({}, object(), {}),
        }
        with _small_shape(), ExitStack() as stack:
            for name, value in common.items():
                stack.enter_context(mock.patch.object(minimal, name, value))
            _expect(
                "MINIMAL_MANIFEST_RUN_ALREADY_EXISTS",
                lambda: minimal.seal_exact_five_manifest(authority=authority),
            )
        assert not output.exists()

        existing_run.rmdir()
        run_parent.rmdir()
        changed = replace(authority, billing_project="changed-private-project")
        with _small_shape(), ExitStack() as stack:
            for name, value in common.items():
                stack.enter_context(mock.patch.object(minimal, name, value))
            stack.enter_context(
                mock.patch.object(minimal, "discover_live_authority", return_value=changed)
            )
            _expect(
                "MINIMAL_LIVE_AUTHORITY_CHANGED",
                lambda: minimal.seal_exact_five_manifest(authority=authority),
            )
        assert not output.exists()
        assert not (production_root / "minimal_canary_runs").exists()


def test_seal_cli_has_no_caller_path_and_calls_only_the_fixed_sealer() -> None:
    runner = (ROOT / "scripts/scc_run_lvef_c3_minimal_canary.sh").read_text()
    assert "--seal-exact-five-manifest:1" in runner
    assert "--seal-exact-five-manifest:2" not in runner
    parsed = minimal.parse_args(["--seal-exact-five-manifest"])
    assert parsed.seal_exact_five_manifest is True
    with redirect_stderr(io.StringIO()):
        try:
            minimal.parse_args(["--seal-exact-five-manifest", "/caller/path"])
        except SystemExit as exc:
            assert exc.code == 2
        else:
            raise AssertionError("seal CLI accepted a caller-selected output path")

    result = {
        "status": "PASS_MINIMAL_EXACT_FIVE_MANIFEST_SEALED",
        "cloud_requests": 0,
        "qsub_submissions": 0,
        "dicom_bodies_processed": 0,
        "gpu_execution": False,
    }
    with mock.patch.object(minimal, "validate_installation") as installation, mock.patch.object(
        minimal, "seal_exact_five_manifest", return_value=result
    ) as seal, mock.patch.object(minimal, "_print_result") as printer:
        assert minimal.main(["--seal-exact-five-manifest"]) == 0
    installation.assert_called_once_with()
    seal.assert_called_once_with()
    printer.assert_called_once_with(result)
    assert minimal.EXACT_FIVE_MANIFEST_PATH == (
        minimal.OWNER_PRIVATE_ROOT / "exact_five_manifest.restricted.json"
    )
