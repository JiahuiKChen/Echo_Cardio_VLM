import json
from pathlib import Path
import sys
import tempfile

import numpy as np
import pandas as pd


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from inspect_artifact_schemas import inspect_embedding_pair, inspect_path
from lvef_multitask_audit_utils import write_json


def test_schema_inspection_never_emits_row_or_array_values() -> None:
    secret_subject = "SYN_SECRET_SUBJECT_VALUE"
    secret_array_value = 987654.25
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        table_path = root / "table.csv"
        array_path = root / "array.npz"
        pd.DataFrame({"subject_id": [secret_subject], "value": [secret_array_value]}).to_csv(table_path, index=False)
        np.savez(array_path, embeddings=np.array([[secret_array_value]], dtype=np.float32))
        payload = {
            "table": inspect_path("table_alias", table_path),
            "array": inspect_path("array_alias", array_path),
        }
        serialized = json.dumps(payload, sort_keys=True)
        assert secret_subject not in serialized
        assert str(secret_array_value) not in serialized
        assert payload["table"]["schema"]["n_rows"] == 1
        assert payload["array"]["schema"]["arrays"][0]["shape"] == [1, 1]


def test_schema_inspection_never_emits_supplied_path() -> None:
    with tempfile.TemporaryDirectory() as directory:
        artifact = Path(directory) / "environment.txt"
        artifact.write_text("package==1.0\n")
        payload = inspect_path("environment_alias", artifact)
        assert str(artifact) not in json.dumps(payload, sort_keys=True)


def test_json_schema_inspection_never_emits_source_keys_or_scalar_values() -> None:
    secret_key = "SYN_SECRET_SUBJECT_IDENTIFIER"
    secret_value = "/restricted/secret/patient/path"
    with tempfile.TemporaryDirectory() as directory:
        artifact = Path(directory) / "metadata.json"
        artifact.write_text(json.dumps({secret_key: secret_value}))
        payload = inspect_path("metadata_alias", artifact)
        serialized = json.dumps(payload, sort_keys=True)
        assert secret_key not in serialized
        assert secret_value not in serialized
        assert payload["schema"]["n_top_level_keys"] == 1
        assert payload["schema"]["source_key_names_emitted"] is False


def test_schema_packet_with_identifier_column_names_passes_json_safety_gate() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        artifact = root / "manifest.csv"
        output = root / "schema_packet.json"
        pd.DataFrame(
            {
                "subject_id": ["SYN_SUBJECT"],
                "study_id": ["SYN_STUDY"],
                "dicom_filepath": ["SYN_PATH"],
            }
        ).to_csv(artifact, index=False)
        inspected = inspect_path("manifest_alias", artifact)
        packet = {"audit": "schema_test", "artifacts": [inspected]}
        write_json(packet, output)
        serialized = output.read_text()
        assert "subject_id" in serialized
        assert "SYN_SUBJECT" not in serialized


def test_embedding_pair_gate_detects_manifest_array_mismatch() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        npz_path = root / "embeddings.npz"
        manifest_path = root / "manifest.csv"
        np.savez(npz_path, embeddings=np.zeros((2, 512), dtype=np.float32))
        pd.DataFrame(
            {
                "study_idx": [0],
                "subject_id": ["SYN_SUBJECT"],
                "study_id": ["SYN_STUDY"],
            }
        ).to_csv(manifest_path, index=False)
        result = inspect_embedding_pair("study_store", npz_path, manifest_path, 512)
        assert result["status"] == "ERROR"
        assert result["checks"]["manifest_success_rows_match_array"] is False


def test_embedding_pair_gate_accepts_aligned_float32_512_store() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        npz_path = root / "embeddings.npz"
        manifest_path = root / "manifest.csv"
        np.savez(npz_path, embeddings=np.zeros((2, 512), dtype=np.float32))
        pd.DataFrame(
            {
                "study_idx": [0, 1],
                "subject_id": ["SYN_SUBJECT_A", "SYN_SUBJECT_B"],
                "study_id": ["SYN_STUDY_A", "SYN_STUDY_B"],
            }
        ).to_csv(manifest_path, index=False)
        result = inspect_embedding_pair("study_store", npz_path, manifest_path, 512)
        assert result["status"] == "OK"
        assert result["checks_passed"] is True


def test_embedding_pair_gate_rejects_fractional_indices() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        npz_path = root / "embeddings.npz"
        manifest_path = root / "manifest.csv"
        np.savez(npz_path, embeddings=np.zeros((2, 512), dtype=np.float32))
        pd.DataFrame(
            {
                "study_idx": [0.5, 1.5],
                "subject_id": ["SYN_SUBJECT_A", "SYN_SUBJECT_B"],
                "study_id": ["SYN_STUDY_A", "SYN_STUDY_B"],
            }
        ).to_csv(manifest_path, index=False)
        result = inspect_embedding_pair("study_store", npz_path, manifest_path, 512)
        assert result["status"] == "ERROR"
        assert result["checks"]["manifest_embedding_index_integer_nonnegative"] is False
        assert result["checks"]["manifest_embedding_index_covers_array"] is False


def test_embedding_pair_gate_rejects_missing_or_duplicate_study_identity() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        npz_path = root / "embeddings.npz"
        manifest_path = root / "manifest.csv"
        np.savez(npz_path, embeddings=np.zeros((2, 512), dtype=np.float32))
        pd.DataFrame(
            {
                "study_idx": [0, 1],
                "subject_id": ["SYN_SUBJECT_A", "  "],
                "study_id": ["SYN_STUDY", "SYN_STUDY"],
            }
        ).to_csv(manifest_path, index=False)
        result = inspect_embedding_pair("study_store", npz_path, manifest_path, 512)
        assert result["status"] == "ERROR"
        assert result["checks"]["manifest_ids_nonmissing"] is False
        assert result["checks"]["manifest_study_ids_unique_when_study_indexed"] is False


def test_embedding_pair_gate_rejects_one_study_mapped_to_multiple_subjects() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        npz_path = root / "embeddings.npz"
        manifest_path = root / "manifest.csv"
        np.savez(npz_path, embeddings=np.zeros((2, 512), dtype=np.float32))
        pd.DataFrame(
            {
                "embedding_idx": [0, 1],
                "subject_id": ["SYN_SUBJECT_A", "SYN_SUBJECT_B"],
                "study_id": ["SYN_STUDY", "SYN_STUDY"],
            }
        ).to_csv(manifest_path, index=False)
        result = inspect_embedding_pair("clip_store", npz_path, manifest_path, 512)
        assert result["status"] == "ERROR"
        assert result["checks"]["manifest_study_to_subject_consistent"] is False


def test_embedding_pair_gate_rejects_empty_or_unknown_success_store() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        npz_path = root / "embeddings.npz"
        manifest_path = root / "manifest.csv"
        np.savez(npz_path, embeddings=np.zeros((0, 512), dtype=np.float32))
        pd.DataFrame(
            columns=["embedding_idx", "subject_id", "study_id", "write_ok"]
        ).to_csv(manifest_path, index=False)
        result = inspect_embedding_pair("empty_store", npz_path, manifest_path, 512)
        assert result["status"] == "ERROR"
        assert result["checks"]["array_has_rows"] is False
        assert result["checks"]["manifest_has_success_rows"] is False

        np.savez(npz_path, embeddings=np.zeros((1, 512), dtype=np.float32))
        pd.DataFrame(
            {
                "embedding_idx": [0],
                "subject_id": ["SYN_SUBJECT"],
                "study_id": ["SYN_STUDY"],
                "write_ok": ["unknown"],
            }
        ).to_csv(manifest_path, index=False)
        result = inspect_embedding_pair("unknown_success_store", npz_path, manifest_path, 512)
        assert result["status"] == "ERROR"
        assert result["checks"]["manifest_success_flag_values_valid"] is False
