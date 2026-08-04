from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import audit_lvef_reconstruction_smoke_run as gate


def _payloads() -> dict[str, dict]:
    source = {
        "status": "PASS",
        "n_selected_subjects": 4530,
        "n_selected_studies": 4530,
        "n_source_studies": 4530,
        "n_outside_selected_source_studies": 0,
        "n_missing_selected_source_studies": 0,
        "source_paths_safe_and_normalized": True,
        "source_ownership_exact": True,
        "source_objects_unique": True,
        "source_object_counts_match_selected_authority": True,
        "all_selected_objects_have_release_sha256": True,
        "candidate_construction_mode": "phase1d_restricted_provenance",
        "selection_salt": "lvef-multitask-phase1e-a-smoke4-v1",
        "smoke_n_roles": 4,
        "smoke_n_studies": 4,
        "smoke_n_objects": 4,
        "smoke_all_train": True,
        "outcomes_read": False,
        "predictions_read": False,
        "embedding_arrays_read": False,
        "performance_computed": False,
        "restricted_input_authority_hash_set_exact": True,
        "locked_split_counts_match": True,
        "technical_smoke_source_manifest_sha256": "a" * 64,
    }
    source_safety = {
        "status": "PASS",
        "aggregate_contains_identifiers": False,
        "aggregate_contains_object_locators": False,
        "restricted_outputs_outside_repository": True,
        "smoke_hard_caps_passed": True,
        "outcome_blind_selection_passed": True,
        "restricted_input_authority_hash_gate_passed": True,
        "locked_split_counts_gate_passed": True,
    }
    download = {
        "status": "PASS",
        "n_studies": 4,
        "n_subjects": 4,
        "n_expected_objects": 4,
        "n_remote_objects": 4,
        "n_downloaded_objects": 4,
        "n_preexisting_verified_objects": 0,
        "n_checksum_verified_objects": 4,
        "exact_remote_set": True,
        "all_sizes_verified": True,
        "checksum_authority_status": "VERIFIED_ALL_OBJECTS",
        "no_symlinks": True,
        "no_extras": True,
        "error_code": "NONE",
        "source_manifest_sha256": "a" * 64,
        "source_manifest_sha256_verified": True,
        "max_studies": 4,
        "max_objects": 1000,
        "max_total_bytes": 5 * 1024**3,
        "min_free_bytes": 20 * 1024**3,
        "total_remote_bytes": 100,
        "free_bytes_before": 20 * 1024**3,
    }
    download_audit = {
        "status": "PASS",
        "n_smoke_roles": 4,
        "smoke_role_set_exact": True,
        "n_expected_objects": 4,
        "n_release_checksums_matched": 4,
        "n_download_objects_discovered": 4,
        "n_verified_objects": 4,
        "n_missing_objects": 0,
        "n_unexpected_objects": 0,
        "n_unsafe_symlink_objects": 0,
        "n_checksum_mismatches": 0,
    }
    dicom = {
        "status": "PASS",
        "n_studies": 4,
        "n_subjects": 4,
        "n_objects": 4,
        "n_read_ok": 4,
        "n_cine_candidates": 3,
        "n_smoke_roles": 4,
        "smoke_role_set_exact": True,
        "exactly_one_study_per_smoke_role": True,
        "positive_control_role_cine_gate_passed": True,
        "negative_control_zero_cine_gate_passed": True,
        "photometric_interpretation_counts": {"MONOCHROME2": 4},
        "transfer_syntax_uid_counts": {"1.2.840.10008.1.2.1": 4},
    }
    extraction = {
        "status": "PASS",
        "n_requested_cines": 3,
        "n_extracted_cines": 3,
        "n_failed_cines": 0,
        "n_unique_clip_keys": 3,
        "n_duplicate_clip_key_rows": 0,
        "all_shapes_32x224x224x3_uint8": True,
        "all_masks_explicitly_applied": True,
        "all_preprocessing_signal_gates_passed": True,
        "temporal_sampling_policy_locked_for_all_extracted_cines": True,
        "n_source_nonempty_sector_gate_passed": 3,
        "n_source_nonzero_retained_pixel_gate_passed": 3,
        "n_source_temporal_variation_gate_passed": 3,
        "n_sampled_nonzero_retained_pixel_gate_passed": 3,
        "n_sampled_temporal_variation_gate_passed": 3,
        "photometric_interpretation_counts": {"MONOCHROME2": 3},
        "transfer_syntax_uid_counts": {"1.2.840.10008.1.2.1": 3},
        "decoder_backend_counts": {"pydicom_pixels_raw:native": 3},
        "color_transform_counts": {"MONOCHROME2_REPLICATE_TO_RGB": 3},
    }
    embedding = {
        "status": "PASS",
        "n_embeddings": 3,
        "embedding_width": 512,
        "embedding_dtype": "float32",
        "all_finite": True,
        "all_l2_positive_and_reconciled": True,
        "embedding_idx_authoritative": True,
        "clip_keys_unique": True,
        "per_vector_content_hashes_reconciled": True,
        "positive_control_studies_exact": True,
        "checkpoint_identity_gate_passed": True,
        "encoder_only": True,
        "view_classifier_loaded": False,
        "device_type": "cuda",
    }
    pooling = {
        "status": "PASS",
        "n_input_clips": 3,
        "n_output_studies": 3,
        "embedding_width": 512,
        "embedding_dtype": "float32",
        "accumulation_dtype": "float64",
        "output_cast_dtype": "float32",
        "all_finite": True,
        "study_ids_unique": True,
        "study_ownership_consistent": True,
        "positive_control_studies_exact": True,
    }
    reproducibility = {
        "status": "PASS",
        "n_pairs": 5,
        "n_exact_equal": 5,
        "n_not_exact_equal": 0,
        "normalized_manifests_compared": 2,
        "internal_array_content_hashes_compared": 2,
        "extraction_runs_compared": 1,
        "extraction_internal_arrays_compared": 9,
        "npz_container_bytes_used_as_exactness_gate": False,
        "required_artifact_pair_set_exact": True,
        "distinct_run_files_and_extraction_roots": True,
    }
    return {
        "source_manifest_summary": source,
        "source_manifest_safety": source_safety,
        "download": download,
        "download_audit": download_audit,
        "dicom_audit": dicom,
        "run_a_extraction": extraction.copy(),
        "run_a_embedding": embedding.copy(),
        "run_a_pooling": pooling.copy(),
        "run_b_extraction": extraction.copy(),
        "run_b_embedding": embedding.copy(),
        "run_b_pooling": pooling.copy(),
        "reproducibility": reproducibility,
    }


def _write_payloads(root: Path, payloads: dict[str, dict]) -> dict[str, Path]:
    paths: dict[str, Path] = {}
    for name, payload in payloads.items():
        path = root / f"{name}.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        paths[name] = path
    return paths


def test_complete_cross_stage_aggregate_gate_passes() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        result = gate.execute(
            _write_payloads(root, _payloads()), root / "run_safety_gate.json"
        )
        assert result["status"] == "PASS"
        assert result["n_aggregate_artifacts"] == 12
        assert result["aggregate_safety_gate_passed"] is True


def test_cross_stage_denominator_mismatch_fails_closed() -> None:
    payloads = _payloads()
    payloads["run_b_extraction"]["n_requested_cines"] = 2
    try:
        gate.validate(payloads)
    except gate.SafetyGateError:
        return
    raise AssertionError("Cross-stage denominator mismatch passed")


def test_cross_stage_smoke_manifest_identity_mismatch_fails_closed() -> None:
    payloads = _payloads()
    payloads["download"]["source_manifest_sha256"] = "b" * 64
    try:
        gate.validate(payloads)
    except gate.SafetyGateError:
        return
    raise AssertionError("Cross-stage smoke manifest identity mismatch passed")


def test_identifier_alias_keys_are_rejected() -> None:
    for payload in ({"patient_ids": [10000001]}, {"ids": [10000001]}):
        try:
            gate.scan_aggregate(payload)
        except gate.SafetyGateError:
            continue
        raise AssertionError("Identifier alias key passed aggregate scan")
