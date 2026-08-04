#!/usr/bin/env python3
"""Validate the complete aggregate-only Phase 1E-A smoke result set.

The script reads only named aggregate JSON artifacts. It never opens row-level
manifests, DICOMs, extracted clips, embeddings, labels, or predictions.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence


EXPECTED_ARTIFACTS = {
    "source_manifest_summary",
    "source_manifest_safety",
    "download",
    "download_audit",
    "dicom_audit",
    "run_a_extraction",
    "run_a_embedding",
    "run_a_pooling",
    "run_b_extraction",
    "run_b_embedding",
    "run_b_pooling",
    "reproducibility",
}
SAFE_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
FORBIDDEN_KEYS = {
    "subject_id",
    "study_id",
    "patient_id",
    "dicom_filepath",
    "source_relative_path",
    "gcs_uri",
    "clip_key",
    "embedding",
    "embedding_sha256",
    "y_true",
    "y_pred",
    "label",
    "prediction",
}
FORBIDDEN_VALUE_FRAGMENTS = (
    "/restricted/",
    "gs://mimic-iv-echo",
    "files/p",
    "clip_key",
    "subject_id",
    "study_id",
)


class SafetyGateError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def parse_named_path(value: str) -> tuple[str, Path]:
    if "=" not in value:
        raise SafetyGateError("Aggregate arguments must use NAME=PATH")
    name, raw_path = value.split("=", 1)
    if not SAFE_NAME_RE.fullmatch(name):
        raise SafetyGateError("Aggregate argument name is not canonical")
    return name, Path(raw_path).expanduser()


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def require_outside_repository(path: Path) -> None:
    repository = Path(__file__).resolve().parents[1]
    resolved = path.resolve(strict=False)
    if resolved == repository or _inside(resolved, repository):
        raise SafetyGateError("Aggregate smoke artifacts must remain outside Git")


def scan_aggregate(value: Any) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).lower()
            identifier_alias = normalized in {"id", "ids", "identifier", "identifiers"} or (
                normalized.endswith(("_id", "_ids"))
                and any(
                    token in normalized
                    for token in ("subject", "study", "patient", "dicom", "clip", "hadm", "stay", "mrn")
                )
            )
            if normalized in FORBIDDEN_KEYS or identifier_alias:
                raise SafetyGateError("Aggregate JSON contains a restricted key")
            scan_aggregate(child)
    elif isinstance(value, list):
        for child in value:
            scan_aggregate(child)
    elif isinstance(value, str):
        lowered = value.lower()
        if any(fragment.lower() in lowered for fragment in FORBIDDEN_VALUE_FRAGMENTS):
            raise SafetyGateError("Aggregate JSON contains a restricted value fragment")


def load_artifacts(named_paths: Mapping[str, Path]) -> dict[str, dict[str, Any]]:
    if set(named_paths) != EXPECTED_ARTIFACTS:
        raise SafetyGateError("Aggregate artifact set is not exact")
    payloads: dict[str, dict[str, Any]] = {}
    for name in sorted(named_paths):
        path = named_paths[name]
        require_outside_repository(path)
        if path.is_symlink() or not path.is_file():
            raise SafetyGateError("Aggregate input is not a regular file")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise SafetyGateError("Aggregate input is not valid JSON") from exc
        if not isinstance(payload, dict):
            raise SafetyGateError("Aggregate input must be a JSON object")
        scan_aggregate(payload)
        payloads[name] = payload
    return payloads


def _require(payload: Mapping[str, Any], **expected: Any) -> None:
    for key, value in expected.items():
        if payload.get(key) != value:
            raise SafetyGateError(f"Aggregate gate mismatch for {key}")


def validate(payloads: Mapping[str, Mapping[str, Any]]) -> None:
    _require(
        payloads["source_manifest_summary"],
        status="PASS",
        n_selected_subjects=4530,
        n_selected_studies=4530,
        n_source_studies=4530,
        n_outside_selected_source_studies=0,
        n_missing_selected_source_studies=0,
        source_paths_safe_and_normalized=True,
        source_ownership_exact=True,
        source_objects_unique=True,
        source_object_counts_match_selected_authority=True,
        all_selected_objects_have_release_sha256=False,
        release_sha256_authority_available=False,
        historical_object_sha256_imported=False,
        object_integrity_authority="GCS_EXACT_OBJECT_STAT",
        gcs_exact_object_metadata_required_for_smoke=True,
        candidate_construction_mode="phase1d_restricted_provenance",
        selection_salt="lvef-multitask-phase1e-a-smoke4-v1",
        smoke_n_roles=4,
        smoke_n_studies=4,
        smoke_all_train=True,
        outcomes_read=False,
        predictions_read=False,
        embedding_arrays_read=False,
        performance_computed=False,
        restricted_input_authority_hash_set_exact=True,
        locked_split_counts_match=True,
    )
    smoke_manifest_sha256 = payloads["source_manifest_summary"].get(
        "technical_smoke_source_manifest_sha256"
    )
    if not isinstance(smoke_manifest_sha256, str) or not re.fullmatch(
        r"[0-9a-f]{64}", smoke_manifest_sha256
    ):
        raise SafetyGateError("Technical smoke manifest identity is missing")
    _require(
        payloads["source_manifest_safety"],
        status="PASS",
        aggregate_contains_identifiers=False,
        aggregate_contains_object_locators=False,
        restricted_outputs_outside_repository=True,
        smoke_hard_caps_passed=True,
        outcome_blind_selection_passed=True,
        restricted_input_authority_hash_gate_passed=True,
        locked_split_counts_gate_passed=True,
        gcs_exact_object_metadata_gate_required=True,
    )
    download = payloads["download"]
    _require(
        download,
        status="PASS",
        n_studies=4,
        n_subjects=4,
        exact_remote_set=True,
        exact_stat_set=True,
        listing_stat_sizes_match=True,
        all_sizes_verified=True,
        all_remote_md5_present=True,
        all_local_md5_match=True,
        all_local_sha256_computed=True,
        remote_metadata_authority="GCS_EXACT_OBJECT_STAT",
        object_transport_integrity_status="VERIFIED_ALL_OBJECTS",
        no_symlinks=True,
        no_extras=True,
        error_code="NONE",
        source_manifest_sha256_verified=True,
    )
    if download.get("source_manifest_sha256") != smoke_manifest_sha256:
        raise SafetyGateError("Queued download used a different smoke manifest")
    restricted_report_sha256 = download.get("restricted_report_sha256")
    if not isinstance(restricted_report_sha256, str) or not re.fullmatch(
        r"[0-9a-f]{64}", restricted_report_sha256
    ):
        raise SafetyGateError("Restricted downloader authority is not hash-bound")
    source_objects = payloads["source_manifest_summary"].get("smoke_n_objects")
    if not isinstance(source_objects, int) or not (4 <= source_objects <= 1000):
        raise SafetyGateError("Source smoke object count is outside the locked envelope")
    if any(
        download.get(key) != source_objects
        for key in (
            "n_expected_objects",
            "n_requested_objects",
            "n_remote_objects",
            "n_remote_stat_objects",
            "n_remote_metadata_complete",
            "n_remote_md5_present",
            "n_remote_crc32c_present",
            "n_remote_generation_present",
            "n_remote_md5_verified_objects",
            "n_local_sha256_computed",
        )
    ):
        raise SafetyGateError("Source/download object counts are not identical")
    if download.get("n_remote_metadata_mismatches") != 0:
        raise SafetyGateError("Remote object metadata reconciliation is not exact")
    if (
        download.get("n_downloaded_objects", 0)
        + download.get("n_preexisting_verified_objects", 0)
        != source_objects
    ):
        raise SafetyGateError("Local verified-object count is incomplete")
    if (
        download.get("max_studies") != 4
        or download.get("max_objects") != 1000
        or download.get("max_total_bytes") != 5 * 1024**3
        or download.get("min_free_bytes") != 20 * 1024**3
        or not isinstance(download.get("total_remote_bytes"), int)
        or download["total_remote_bytes"] > 5 * 1024**3
        or download.get("total_downloaded_bytes") != download["total_remote_bytes"]
        or not isinstance(download.get("free_bytes_before"), int)
        or download["free_bytes_before"] < 20 * 1024**3
    ):
        raise SafetyGateError("Download resource envelope is not the locked envelope")

    _require(
        payloads["download_audit"],
        status="PASS",
        n_smoke_roles=4,
        smoke_role_set_exact=True,
        downloader_report_authority="GCS_EXACT_OBJECT_STAT",
        download_integrity_status="PASS_GCS_METADATA_AND_LOCAL_HASH",
        n_missing_downloads=0,
        n_unexpected_downloads=0,
        n_unsafe_symlink_objects=0,
        n_remote_metadata_mismatches=0,
        n_gcs_md5_mismatches=0,
        n_local_sha256_report_mismatches=0,
    )
    download_audit = payloads["download_audit"]
    expected_objects = download_audit.get("n_expected_objects")
    if not isinstance(expected_objects, int) or expected_objects < 4:
        raise SafetyGateError("Downloaded-object audit has an invalid object count")
    for key in (
        "n_downloaded_objects",
        "n_remote_metadata_complete",
        "n_remote_md5_verified_objects",
        "n_local_sha256_matched",
        "n_verified_objects",
    ):
        if download_audit.get(key) != expected_objects:
            raise SafetyGateError("Downloaded-object audit is incomplete")
    if expected_objects != source_objects:
        raise SafetyGateError("Downloaded-object audit denominator differs from source")
    if download_audit.get("source_manifest_sha256") != smoke_manifest_sha256:
        raise SafetyGateError("Downloaded-object audit used a different smoke manifest")
    _require(
        payloads["dicom_audit"],
        status="PASS",
        n_studies=4,
        n_subjects=4,
        n_smoke_roles=4,
        smoke_role_set_exact=True,
        exactly_one_study_per_smoke_role=True,
        positive_control_role_cine_gate_passed=True,
        negative_control_zero_cine_gate_passed=True,
    )
    dicom = payloads["dicom_audit"]
    if dicom.get("n_objects") != source_objects or dicom.get("n_read_ok") != source_objects:
        raise SafetyGateError("DICOM audit denominator differs from verified source")
    cine_count = dicom.get("n_cine_candidates")
    if not isinstance(cine_count, int) or cine_count < 3:
        raise SafetyGateError("DICOM audit has an invalid cine count")
    for key in ("photometric_interpretation_counts", "transfer_syntax_uid_counts"):
        counts = dicom.get(key)
        if (
            not isinstance(counts, dict)
            or not counts
            or any(not isinstance(value, int) or value < 0 for value in counts.values())
            or sum(counts.values()) != source_objects
        ):
            raise SafetyGateError("DICOM technical-format counts are incomplete")
    run_clip_counts: list[int] = []
    for run in ("run_a", "run_b"):
        extraction = payloads[f"{run}_extraction"]
        _require(
            extraction,
            status="PASS",
            n_failed_cines=0,
            n_duplicate_clip_key_rows=0,
            all_shapes_32x224x224x3_uint8=True,
            all_masks_explicitly_applied=True,
            all_preprocessing_signal_gates_passed=True,
            temporal_sampling_policy_locked_for_all_extracted_cines=True,
        )
        if (
            extraction.get("n_requested_cines") != cine_count
            or extraction.get("n_extracted_cines") != cine_count
            or extraction.get("n_unique_clip_keys") != cine_count
        ):
            raise SafetyGateError("Extraction denominator differs from DICOM cine count")
        for key in (
            "n_source_nonempty_sector_gate_passed",
            "n_source_nonzero_retained_pixel_gate_passed",
            "n_source_temporal_variation_gate_passed",
            "n_sampled_nonzero_retained_pixel_gate_passed",
            "n_sampled_temporal_variation_gate_passed",
        ):
            if extraction.get(key) != cine_count:
                raise SafetyGateError("Extraction signal-quality gate count is incomplete")
        for key in (
            "photometric_interpretation_counts",
            "transfer_syntax_uid_counts",
            "decoder_backend_counts",
            "color_transform_counts",
        ):
            counts = extraction.get(key)
            if (
                not isinstance(counts, dict)
                or not counts
                or any(not isinstance(value, int) or value < 0 for value in counts.values())
                or sum(counts.values()) != cine_count
            ):
                raise SafetyGateError("Extraction format/decoder counts are incomplete")
        embedding = payloads[f"{run}_embedding"]
        _require(
            embedding,
            status="PASS",
            embedding_width=512,
            embedding_dtype="float32",
            all_finite=True,
            all_l2_positive_and_reconciled=True,
            embedding_idx_authoritative=True,
            clip_keys_unique=True,
            per_vector_content_hashes_reconciled=True,
            positive_control_studies_exact=True,
            checkpoint_identity_gate_passed=True,
            encoder_only=True,
            view_classifier_loaded=False,
            device_type="cuda",
        )
        if embedding.get("n_embeddings") != cine_count:
            raise SafetyGateError("Embedding denominator differs from extracted cines")
        run_clip_counts.append(int(embedding["n_embeddings"]))
        pooling = payloads[f"{run}_pooling"]
        _require(
            pooling,
            status="PASS",
            n_output_studies=3,
            embedding_width=512,
            embedding_dtype="float32",
            accumulation_dtype="float64",
            output_cast_dtype="float32",
            all_finite=True,
            study_ids_unique=True,
            study_ownership_consistent=True,
            positive_control_studies_exact=True,
        )
        if pooling.get("n_input_clips") != cine_count:
            raise SafetyGateError("Pooling denominator differs from clip embeddings")
    if len(set(run_clip_counts)) != 1:
        raise SafetyGateError("Clean runs have different clip denominators")
    _require(
        payloads["reproducibility"],
        status="PASS",
        n_pairs=5,
        n_exact_equal=5,
        n_not_exact_equal=0,
        normalized_manifests_compared=2,
        internal_array_content_hashes_compared=2,
        extraction_runs_compared=1,
        npz_container_bytes_used_as_exactness_gate=False,
        required_artifact_pair_set_exact=True,
        distinct_run_files_and_extraction_roots=True,
    )
    restricted_details_sha256 = payloads["reproducibility"].get(
        "restricted_details_sha256"
    )
    if not isinstance(restricted_details_sha256, str) or not re.fullmatch(
        r"[0-9a-f]{64}", restricted_details_sha256
    ):
        raise SafetyGateError("Restricted reproducibility details are not hash-bound")
    if payloads["reproducibility"].get("extraction_internal_arrays_compared") != 3 * cine_count:
        raise SafetyGateError("Extraction reproducibility array count is incomplete")


def execute(named_paths: Mapping[str, Path], output: Path) -> dict[str, Any]:
    require_outside_repository(output)
    if output.exists() or output.is_symlink():
        raise SafetyGateError("Safety-gate output already exists")
    payloads = load_artifacts(named_paths)
    validate(payloads)
    artifact_sha256 = {
        name: sha256_file(named_paths[name]) for name in sorted(named_paths)
    }
    result = {
        "schema_version": 1,
        "status": "PASS",
        "aggregate_safety_gate_passed": True,
        "expected_artifact_set_exact": True,
        "n_aggregate_artifacts": len(payloads),
        "all_component_status_gates_passed": True,
        "restricted_keys_absent": True,
        "restricted_value_fragments_absent": True,
        "confirmatory_performance_accessed": False,
        "models_fitted": False,
        "predictions_generated": False,
        "artifact_sha256": artifact_sha256,
        "technical_smoke_source_manifest_sha256": payloads[
            "source_manifest_summary"
        ]["technical_smoke_source_manifest_sha256"],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2, sort_keys=True)
        handle.write("\n")
    return result


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--aggregate", action="append", required=True, metavar="NAME=PATH")
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    parsed = [parse_named_path(value) for value in args.aggregate]
    names = [name for name, _ in parsed]
    if len(names) != len(set(names)):
        raise SafetyGateError("Aggregate arguments contain duplicate names")
    try:
        result = execute(dict(parsed), args.output)
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "BLOCKED_SMOKE_SAFETY_GATE",
                    "error_type": type(exc).__name__,
                    "exception_message_emitted": False,
                    "paths_emitted": False,
                    "row_values_emitted": False,
                },
                sort_keys=True,
            )
        )
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
