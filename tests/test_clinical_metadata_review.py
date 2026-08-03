from pathlib import Path
import json
import subprocess
import sys
import tempfile

import pandas as pd


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from lvef_multitask_clinical_metadata import (
    ALLOWED_TARGETS,
    build_canonical_summary,
    build_review_rows,
    exact_target,
    requested_targets,
)


def synthetic_mapping() -> pd.DataFrame:
    rows = []
    for target in ALLOWED_TARGETS:
        rows.append(
            {
                "measurement": f"RAW_{target}",
                "measurement_description": f"Synthetic description for {target}",
                "canonical_measurement": target,
                "canonical_source": "synthetic_manual",
                "unit": "unknown",
                "unit_norm": "unknown",
                "unit_category": "unknown",
            }
        )
    rows.extend(
        [
            {
                "measurement": "RAW_EF_VISUAL",
                "measurement_description": "Visual ejection fraction",
                "canonical_measurement": "source_specific_visual_ef",
                "canonical_source": "synthetic_source",
                "unit": "%",
                "unit_norm": "%",
                "unit_category": "fraction",
            },
            {
                "measurement": "RAW_LVOT_VTI_CM",
                "measurement_description": "PW Doppler apical five chamber",
                "canonical_measurement": "lvot_vti",
                "canonical_source": "synthetic_manual",
                "unit": "cm",
                "unit_norm": "cm",
                "unit_category": "length",
            },
            {
                "measurement": "RAW_LVOT_VTI_MM",
                "measurement_description": "PW Doppler apical five chamber beat average",
                "canonical_measurement": "lvot_vti",
                "canonical_source": "synthetic_manual",
                "unit": "mm",
                "unit_norm": "mm",
                "unit_category": "length",
            },
            {
                "measurement": "RAW_LAVI",
                "measurement_description": "Left atrial volume indexed to BSA",
                "canonical_measurement": "left_atrial_volume_index",
                "canonical_source": "synthetic_auto",
                "unit": "mL/m2",
                "unit_norm": "ml/m2",
                "unit_category": "indexed_volume",
            },
        ]
    )
    return pd.DataFrame(rows)


def test_exact_identifier_allowlist_rejects_mangled_and_unknown_names() -> None:
    assert exact_target("lvef") == "lvef"
    assert requested_targets([]) == ALLOWED_TARGETS
    for invalid in ("left*ventricular*end*diastolic*diameter", "LVEF", "new_target"):
        try:
            exact_target(invalid)
        except ValueError:
            continue
        raise AssertionError(f"Corrupted or unknown target was accepted: {invalid}")


def test_requested_target_allowlist_rejects_duplicates() -> None:
    try:
        requested_targets(["lvef", "lvef"])
    except ValueError:
        return
    raise AssertionError("Duplicate requested targets were accepted")


def test_packet_includes_exact_targets_and_flags_candidates_without_promoting_them() -> None:
    rows = build_review_rows(synthetic_mapping(), ALLOWED_TARGETS)
    assert set(ALLOWED_TARGETS).issubset(set(rows["allowlisted_target"]))
    candidate = rows[rows["canonical_mapping"] == "source_specific_visual_ef"]
    assert len(candidate) == 1
    assert candidate.iloc[0]["allowlisted_target"] == ""
    assert candidate.iloc[0]["canonical_mapping_status"] == "SOURCE_CANDIDATE_NOT_ALLOWLISTED"
    assert "LVEF_ALIAS_OR_METHOD" in candidate.iloc[0]["selection_reason"]


def test_packet_flags_multiple_units_and_method_view_timing_metadata() -> None:
    rows = build_review_rows(synthetic_mapping(), ALLOWED_TARGETS)
    vti = rows[rows["canonical_mapping"] == "lvot_vti"]
    assert len(vti) == 3
    assert vti["multiple_nonunknown_normalized_units"].all()
    described = vti[vti["raw_name"].isin({"RAW_LVOT_VTI_CM", "RAW_LVOT_VTI_MM"})]
    assert described["method_encoded"].all()
    assert described["view_encoded"].all()
    assert described["timing_or_respiratory_context_encoded"].any()
    summary = build_canonical_summary(rows, ALLOWED_TARGETS)
    vti_summary = summary[summary["canonical_mapping"] == "lvot_vti"].iloc[0]
    assert bool(vti_summary["multiple_nonunknown_normalized_units"])
    assert int(vti_summary["n_normalized_units"]) == 2


def test_packet_flags_indexed_candidate_but_does_not_allowlist_it() -> None:
    rows = build_review_rows(synthetic_mapping(), ALLOWED_TARGETS)
    indexed = rows[rows["canonical_mapping"] == "left_atrial_volume_index"]
    assert len(indexed) == 1
    assert bool(indexed.iloc[0]["appears_indexed"])
    assert indexed.iloc[0]["allowlisted_target"] == ""


def test_metadata_review_rejects_patient_values_and_identifiers() -> None:
    for forbidden_column in ("subject_id", "study_id", "result", "embedding_idx", "dicom_path"):
        frame = synthetic_mapping()
        frame[forbidden_column] = "SYNTHETIC"
        try:
            build_review_rows(frame, ALLOWED_TARGETS)
        except ValueError:
            continue
        raise AssertionError(f"Patient/value-level column was accepted: {forbidden_column}")


def test_metadata_review_rejects_blank_raw_or_canonical_names() -> None:
    for column in ("measurement", "canonical_measurement"):
        frame = synthetic_mapping()
        frame.loc[0, column] = " "
        try:
            build_review_rows(frame, ALLOWED_TARGETS)
        except ValueError:
            continue
        raise AssertionError(f"Blank required metadata was accepted: {column}")


def test_cli_writes_restricted_metadata_packet_with_aggregate_only_stdout() -> None:
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory() as directory:
        temporary = Path(directory)
        mapping = temporary / "mapping.csv"
        output = temporary / "restricted_packet"
        synthetic_mapping().to_csv(mapping, index=False)
        completed = subprocess.run(
            [
                sys.executable,
                str(root / "scripts" / "lvef_multitask_clinical_metadata.py"),
                "--mapping-csv",
                str(mapping),
                "--output-dir",
                str(output),
            ],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 0, completed.stdout
        stdout = json.loads(completed.stdout)
        assert stdout["status"] == "COMPLETE"
        assert stdout["contains_patient_values"] is False
        assert stdout["contains_patient_or_study_identifiers"] is False
        assert "RAW_" not in completed.stdout
        assert str(temporary) not in completed.stdout
        packet = pd.read_csv(output / "clinical_metadata_review_rows.csv")
        forbidden = {"subject_id", "study_id", "result", "value", "embedding", "dicom_path"}
        assert not (forbidden & set(packet.columns))
        manifest = json.loads((output / "clinical_metadata_review_packet_manifest.json").read_text())
        assert manifest["n_allowlisted_targets_present"] == len(ALLOWED_TARGETS)


def test_cli_refuses_to_reuse_nonempty_output_directory() -> None:
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory() as directory:
        temporary = Path(directory)
        mapping = temporary / "mapping.csv"
        output = temporary / "restricted_packet"
        output.mkdir()
        (output / "existing.txt").write_text("synthetic")
        synthetic_mapping().to_csv(mapping, index=False)
        completed = subprocess.run(
            [
                sys.executable,
                str(root / "scripts" / "lvef_multitask_clinical_metadata.py"),
                "--mapping-csv",
                str(mapping),
                "--output-dir",
                str(output),
            ],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 2
        payload = json.loads(completed.stdout)
        assert payload["status"] == "BLOCKED_AUDIT_EXCEPTION"
        assert (output / "existing.txt").read_text() == "synthetic"
