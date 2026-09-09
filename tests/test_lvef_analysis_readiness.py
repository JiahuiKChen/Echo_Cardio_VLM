from __future__ import annotations

from contextlib import contextmanager
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import audit_lvef_analysis_readiness as readiness
from build_lvef_clinician_signoff_packet import (
    CLINICAL_ISSUE_IDS, build_packet, response_template,
)
from test_lvef_clinician_signoff import metadata_rows


def _write(path: Path, value: object) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = value if isinstance(value, bytes) else (json.dumps(value, sort_keys=True) + "\n").encode()
    path.write_bytes(data)
    path.chmod(0o600)
    return data


@contextmanager
def _clinical(complete: bool = False, blank_unit: bool = False):
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        rows = root / "review.csv"
        source_rows = metadata_rows()
        if blank_unit:
            source_rows.loc[0, "native_unit"] = ""
            source_rows.loc[0, "normalized_unit"] = ""
        source_rows.to_csv(rows, index=False)
        # Exercise the actual maintained producer's CSV loading semantics.
        from lvef_multitask_audit_utils import load_table
        packet = build_packet(load_table(rows), "a" * 40).encode()
        packet_path = root / "clinical_metadata_clinician_signoff_restricted.md"
        _write(packet_path, packet)
        digest = hashlib.sha256(packet).hexdigest()
        manifest = {
            "audit": "lvef_multitask_clinician_packet", "status": "READY_FOR_HUMAN_SIGNOFF",
            "packet_sha256": digest, "source_commit": "a" * 40,
            "n_questions": 8, "question_ids": list(CLINICAL_ISSUE_IDS),
        }
        _write(root / "clinical_metadata_clinician_packet_manifest_restricted.json", manifest)
        response = response_template(digest)
        if complete:
            response["reviewer"] = {
                "name_or_initials": "SYNTHETIC_PRIVATE_PERSON",
                "role_expertise": "Synthetic echo expert", "echo_measurement_expertise_attested": True,
                "signoff_date": "2026-09-09",
            }
            for item in response["responses"]:
                item["selected_option"] = "UNRESOLVED_EXCLUDE"
                item["rationale"] = "SYNTHETIC_PRIVATE_REASON: insufficient construct evidence."
        response_path = root / "clinical_metadata_clinician_response_restricted.json"
        _write(response_path, response)
        yield root, rows, response_path, response


def _expect(code: str, operation) -> None:
    try:
        operation()
    except readiness.ReadinessError as exc:
        assert str(exc) == code
    else:
        raise AssertionError("Expected closed readiness failure")


def test_ready_questionnaire_never_manufactures_clinician_decisions() -> None:
    with _clinical() as (root, rows, _, _):
        result = readiness.inspect_clinician_packet(root, rows)
        assert result["status"] == "PENDING_HUMAN_SIGNOFF"
        assert result["human_signoff_complete"] is False
        assert result["n_pending_questions"] == 8
        assert all(row["selected_option"] is None for row in result["questions"])


def test_real_clinician_validator_reuses_hash_bound_exclusions_without_private_text() -> None:
    with _clinical(True) as (root, rows, _, _):
        result = readiness.inspect_clinician_packet(root, rows)
        assert result["status"] == "PASS_CLINICIAN_SIGNOFF"
        assert result["n_pending_questions"] == 0
        assert all(row["selected_option"] == "UNRESOLVED_EXCLUDE" for row in result["questions"])
        rendered = json.dumps(result)
        for forbidden in ("SYNTHETIC_PRIVATE", "raw_name", "raw_description", "native_unit", "rationale"):
            assert forbidden not in rendered


def test_clinician_response_hash_and_metadata_both_bind_reuse() -> None:
    with _clinical(True) as (root, rows, response_path, response):
        response["packet_sha256"] = "f" * 64
        _write(response_path, response)
        assert readiness.inspect_clinician_packet(root, rows)["human_signoff_complete"] is False
        altered = metadata_rows()
        altered.loc[0, "raw_description"] = "Another synthetic construct"
        altered.to_csv(rows, index=False)
        _expect("READINESS_CLINICIAN_METADATA_MISMATCH",
                lambda: readiness.inspect_clinician_packet(root, rows))


def test_missing_metadata_replays_existing_producer_nan_spelling_without_rewrite() -> None:
    with _clinical(True, blank_unit=True) as (root, rows, _, _):
        path = root / "clinical_metadata_clinician_signoff_restricted.md"
        before = path.read_bytes()
        assert b"| nan | nan |" in before
        assert build_packet(pd.read_csv(io.BytesIO(rows.read_bytes()), keep_default_na=False), "a" * 40).encode() != before
        result = readiness.inspect_clinician_packet(root, rows)
        assert result["human_signoff_complete"] is True
        assert result["metadata_packet_regenerated_exactly"] is True
        assert path.read_bytes() == before


def test_clinician_duplicate_json_or_symlink_cannot_become_authority() -> None:
    with _clinical(True) as (root, rows, response_path, _):
        _write(response_path, b'{"responses": [], "responses": []}')
        _expect("READINESS_DUPLICATE_JSON_KEY", lambda: readiness.inspect_clinician_packet(root, rows))
        response_path.unlink()
        response_path.symlink_to(rows)
        _expect("READINESS_CONTROL_FILE_INVALID", lambda: readiness.inspect_clinician_packet(root, rows))


@contextmanager
def _technical():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        inputs = {key: "b" * 64 for key in (
            "clinical_review_rows", "raw_canonical_mapping", "structured_measurements",
            "selected_studies", "subject_split_map")}
        outputs = []
        for role, names in readiness.TECHNICAL_OUTPUTS.items():
            for name in sorted(names):
                value: object = b"synthetic\n"
                if name == "technical_metadata_issue_summary.csv":
                    value = ("issue_id,disposition\n" + "".join(
                        f"{issue},PENDING_RESTRICTED_TECHNICAL_REVIEW\n"
                        for issue in readiness.TECHNICAL_ISSUE_IDS)).encode()
                elif name == "technical_metadata_safety_gate.json":
                    value = {"status": "PASS", "safety_gate_passed": True,
                             "confirmatory_performance_accessed": False, "predictions_or_embeddings_read": False}
                elif name == "lvef_separate_label_authority.json":
                    value = {"status": "PASS", "raw_target_match": "EXACT_CASE_SENSITIVE_LVEF",
                             "aggregation": "NUMERIC_MEDIAN_BY_SUBJECT_AND_MEASUREMENT_ID",
                             "mapping_row_required": False, "synthetic_mapping_row_created": False,
                             "candidate_aliases_are_authority": False}
                data = _write(root / role / "technical_metadata" / name, value)
                outputs.append({"output_class": role, "relative_name": name,
                                "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
        manifest = {"audit": "phase1e_technical_metadata", "status": "COMPLETE_PENDING_SCIENTIFIC_ADJUDICATION",
                    "input_checksums": inputs, "output_checksums": outputs,
                    "model_outputs_read": False, "confirmatory_performance_accessed": False}
        manifest_path = root / "aggregate/technical_metadata/technical_metadata_manifest.json"
        _write(manifest_path, manifest)
        yield root, inputs, manifest_path, manifest


def test_successful_old_technical_packet_is_evidence_not_adjudication() -> None:
    with _technical() as (root, inputs, _, _):
        result = readiness.inspect_technical_packet(root, expected_input_checksums=inputs)
        assert result["status"] == "PASS_PREPARED_TECHNICAL_EVIDENCE"
        assert result["n_hash_verified_outputs"] == 10
        assert result["scientific_adjudication_complete"] is False
        assert result["separate_exact_name_lvef_authority"] is True


def test_technical_reuse_rejects_changed_restricted_evidence_and_foreign_inputs() -> None:
    with _technical() as (root, inputs, _, _):
        wrong = dict(inputs, selected_studies="c" * 64)
        _expect("READINESS_TECHNICAL_MANIFEST_MISMATCH",
                lambda: readiness.inspect_technical_packet(root, expected_input_checksums=wrong))
        _write(root / "restricted/technical_metadata/technical_metadata_train_distributions_restricted.csv",
               b"changed_synthetic\n")
        _expect("READINESS_TECHNICAL_OUTPUT_HASH_MISMATCH",
                lambda: readiness.inspect_technical_packet(root, expected_input_checksums=inputs))


def test_technical_output_manifest_cannot_alias_or_escape_fixed_artifact_roles() -> None:
    with _technical() as (root, inputs, path, manifest):
        manifest["output_checksums"][0]["relative_name"] = "../private.json"
        _write(path, manifest)
        _expect("READINESS_TECHNICAL_OUTPUT_SET_MISMATCH",
                lambda: readiness.inspect_technical_packet(root, expected_input_checksums=inputs))


def test_metadata_class_projection_redacts_raw_tokens_and_binds_existing_csvs() -> None:
    with _technical() as (root, _, path, manifest):
        frames = {
            "technical_metadata_evidence_restricted.csv": pd.DataFrame([{
                "raw_name": "PRIVATE_RAW_NAME", "raw_description": "PRIVATE_DESCRIPTION basal",
                "canonical_mapping": "arch_diam", "allowlisted_target": "arch_diam",
                "issue_id": "DIMENSION_CM_MM_UNITS", "native_unit": "PRIVATE_UNIT", "normalized_unit": "mm",
            }]),
            "technical_metadata_train_completeness_restricted.csv": pd.DataFrame([{
                "raw_name": "PRIVATE_RAW_NAME", "train_unit_tokens": "cm;PRIVATE_UNIT",
                "n_train_unit_tokens": 2, "n_train_numeric_rows": 500, "n_train_nonnumeric_rows": 0,
                "n_train_missing_value_rows": 2, "mapping_is_ambiguous": False, "n_mapping_canonicals": 1,
            }]),
        }
        for name, frame in frames.items():
            data = _write(root / "restricted/technical_metadata" / name, frame.to_csv(index=False).encode())
            record = next(row for row in manifest["output_checksums"] if row["relative_name"] == name)
            record.update(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
        _write(path, manifest)
        result = readiness.project_technical_metadata(root)
        assert result["status"] == "PASS_TECHNICAL_METADATA_CLASS_PROJECTION"
        assert result["fields"][0]["native_unit_classes"] == ["UNRECOGNIZED"]
        assert result["fields"][0]["train_unit_classes"] == ["LENGTH_CM", "UNRECOGNIZED"]
        assert result["lexical_tags_authorize_clinical_relationships"] is False
        assert "PRIVATE_" not in json.dumps(result)
        _write(root / "restricted/technical_metadata/technical_metadata_evidence_restricted.csv", b"changed\n")
        _expect("READINESS_TECHNICAL_OUTPUT_HASH_MISMATCH", lambda: readiness.project_technical_metadata(root))
