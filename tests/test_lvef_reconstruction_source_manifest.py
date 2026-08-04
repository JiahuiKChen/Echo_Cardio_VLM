from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile

import pandas as pd


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import build_lvef_reconstruction_source_manifest as builder
from build_lvef_reconstruction_source_manifest import (
    EXPECTED_COMPONENTS,
    _ranked_candidate,
    execute,
)
import download_lvef_reconstruction_smoke as downloader
from download_lvef_reconstruction_smoke import load_source_manifest
import lvef_reconstruction_smoke as smoke_pipeline
from lvef_reconstruction_smoke import _validate_source_manifest


def _fixture(
    root: Path,
    *,
    fallback: bool = False,
    multiple_later_candidates: bool = False,
    unsafe_path: bool = False,
):
    selected_rows = []
    split_rows = []
    record_paths = {}
    ownership = {}
    for index, component in enumerate(EXPECTED_COMPONENTS):
        subject = str(10000001 + index)
        study = str(20000001 + index)
        ownership[component] = (subject, study)
        selected_rows.append({"subject_id": subject, "study_id": study, "n_dicoms": 1})
        split = "val" if fallback and component == "batch_000" else "train"
        split_rows.append({"subject_id": subject, "split": split})
        path = root / f"{component}.csv"
        relative = f"files/p10/p{subject}/s{study}/{study}_cine.dcm"
        if unsafe_path and component == "batch_004":
            relative = f"files/p10/p{subject}/s{study}/../bad.dcm"
        pd.DataFrame(
            [{"subject_id": subject, "study_id": study, "dicom_filepath": relative}]
        ).to_csv(path, index=False)
        record_paths[component] = path

    if fallback:
        subject = "10000099"
        study = "20000099"
        selected_rows.append({"subject_id": subject, "study_id": study, "n_dicoms": 1})
        split_rows.append({"subject_id": subject, "split": "train"})
        batch_path = record_paths["batch_000"]
        frame = pd.read_csv(batch_path, dtype=str)
        frame = pd.concat(
            [
                frame,
                pd.DataFrame(
                    [
                        {
                            "subject_id": subject,
                            "study_id": study,
                            "dicom_filepath": f"files/p10/p{subject}/s{study}/{study}_cine.dcm",
                        }
                    ]
                ),
            ],
            ignore_index=True,
        )
        frame.to_csv(batch_path, index=False)
        ownership["batch_000_fallback"] = (subject, study)

    if multiple_later_candidates:
        subject = "10000098"
        study = "20000098"
        selected_rows.append({"subject_id": subject, "study_id": study, "n_dicoms": 1})
        split_rows.append({"subject_id": subject, "split": "train"})
        batch_path = record_paths["batch_001"]
        frame = pd.read_csv(batch_path, dtype=str)
        frame = pd.concat(
            [
                frame,
                pd.DataFrame(
                    [
                        {
                            "subject_id": subject,
                            "study_id": study,
                            "dicom_filepath": f"files/p10/p{subject}/s{study}/{study}_cine.dcm",
                        }
                    ]
                ),
            ],
            ignore_index=True,
        )
        frame.to_csv(batch_path, index=False)
        ownership["batch_001_extra"] = (subject, study)

    selected_path = root / "selected.csv"
    split_path = root / "splits.csv"
    pd.DataFrame(selected_rows).to_csv(selected_path, index=False)
    pd.DataFrame(split_rows).to_csv(split_path, index=False)

    def evidence(name: str, pair):
        path = root / f"evidence_{name}.csv"
        pd.DataFrame([{"subject_id": pair[0], "study_id": pair[1]}]).to_csv(path, index=False)
        return path

    batch_preferred = ownership["batch_000"]
    batch_fallback = ownership.get("batch_000_fallback", ownership["batch_000"])
    later_candidates = [ownership["batch_001"]]
    if multiple_later_candidates:
        later_candidates.append(ownership["batch_001_extra"])
    later_path = root / "evidence_batch_later.csv"
    pd.DataFrame(
        [{"subject_id": pair[0], "study_id": pair[1]} for pair in later_candidates]
    ).to_csv(later_path, index=False)
    candidate_paths = {
        "batch_000_duplicate_affected": evidence("preferred", batch_preferred),
        "batch_000_cine_positive_fallback": evidence("fallback", batch_fallback),
        "batch_001_008_cine_positive": later_path,
        "stage_d_cine_positive": evidence("stage_d", ownership["stage_d"]),
        "train_no_cine_negative": evidence("no_cine", ownership["batch_002"]),
    }
    return selected_path, split_path, record_paths, candidate_paths, len(selected_rows)


def _run(root: Path, **fixture_kwargs):
    inputs = _fixture(root, **fixture_kwargs)
    restricted = root / "restricted"
    aggregate = root / "aggregate"
    summary = execute(
        selected_path=inputs[0],
        split_path=inputs[1],
        record_components=inputs[2],
        candidate_evidence=inputs[3],
        restricted_output_dir=restricted,
        aggregate_output_dir=aggregate,
        expected_selected_sha256=None,
        expected_selected_studies=inputs[4],
    )
    return summary, restricted, aggregate


def test_selected_source_and_four_role_smoke_manifests_are_exact_and_safe() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        summary, restricted, aggregate = _run(root)
        source = pd.read_csv(restricted / "selected_source_manifest_restricted.csv", dtype=str)
        smoke = pd.read_csv(restricted / "technical_smoke_source_manifest_restricted.csv", dtype=str)
        assert source["study_id"].nunique() == len(EXPECTED_COMPONENTS)
        assert source["source_relative_path"].is_unique
        assert source["gcs_uri"].str.startswith(
            "gs://mimic-iv-echo-1.0.physionet.org/files/"
        ).all()
        assert smoke["study_id"].nunique() == 4
        assert smoke["smoke_role"].nunique() == 4
        assert set(smoke["split"]) == {"train"}
        assert summary["smoke_n_objects"] <= 1000
        aggregate_text = "\n".join(
            path.read_text() for path in sorted(aggregate.iterdir()) if path.is_file()
        )
        for value in source["subject_id"].tolist() + source["study_id"].tolist():
            assert value not in aggregate_text
        safety = json.loads(
            (aggregate / "reconstruction_source_manifest_safety_gate.json").read_text()
        )
        assert safety["status"] == "PASS"


def test_batch_000_fallback_is_explicit_and_train_only() -> None:
    with tempfile.TemporaryDirectory() as directory:
        summary, restricted, _ = _run(Path(directory), fallback=True)
        assert summary["batch_000_preferred_evidence_used"] is False
        assert summary["batch_000_selection_basis"] == "BATCH_000_CINE_POSITIVE_FALLBACK"
        smoke = pd.read_csv(
            restricted / "technical_smoke_source_manifest_restricted.csv", dtype=str
        )
        first = smoke[
            smoke["smoke_role"]
            == "batch_000_duplicate_affected_or_prespecified_fallback"
        ]
        assert set(first["selection_basis"]) == {"BATCH_000_CINE_POSITIVE_FALLBACK"}
        assert set(smoke["split"]) == {"train"}


def test_fixed_hash_ranking_is_order_independent_and_candidate_count_is_reported() -> None:
    with tempfile.TemporaryDirectory() as directory:
        summary, restricted, aggregate = _run(
            Path(directory), multiple_later_candidates=True
        )
        assert summary["smoke_n_studies"] == 4
        roles = pd.read_csv(aggregate / "reconstruction_smoke_roles.csv")
        later = roles[
            roles["smoke_role"] == "batch_001_008_historical_cine_positive"
        ].iloc[0]
        assert int(later["n_candidate_studies"]) == 2
        candidates = [("10000003", "20000003"), ("10000098", "20000098")]
        expected = _ranked_candidate(
            "batch_001_008_historical_cine_positive", candidates
        )
        assert expected == _ranked_candidate(
            "batch_001_008_historical_cine_positive",
            list(reversed(candidates)) + [candidates[0]],
        )
        smoke = pd.read_csv(
            restricted / "technical_smoke_source_manifest_restricted.csv", dtype=str
        )
        chosen = smoke[
            smoke["smoke_role"] == "batch_001_008_historical_cine_positive"
        ]
        assert set(chosen["subject_id"]) == {expected[0]}
        assert set(chosen["study_id"]) == {expected[1]}


def test_unsafe_source_path_fails_before_outputs() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        try:
            _run(root, unsafe_path=True)
        except ValueError:
            pass
        else:
            raise AssertionError("Unsafe source path was accepted")
        assert not (root / "restricted" / "selected_source_manifest_restricted.csv").exists()


def test_builder_smoke_output_is_accepted_end_to_end_by_both_consumers() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        _, restricted, _ = _run(root)
        manifest_path = restricted / "technical_smoke_source_manifest_restricted.csv"
        download_objects = load_source_manifest(manifest_path)
        assert len({item.study_id for item in download_objects}) == 4
        validated = _validate_source_manifest(
            pd.read_csv(manifest_path, low_memory=False)
        )
        assert validated["study_id"].nunique() == 4
        assert set(validated["split"]) == {"train"}
        assert tuple(downloader.EXPECTED_SMOKE_ROLES) == tuple(
            smoke_pipeline.EXPECTED_SMOKE_ROLES
        ) == tuple(builder.OUTPUT_ROLES)


def test_authoritative_provenance_mode_derives_all_smoke_strata() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        selected_path, split_path, records, _, n_selected = _fixture(root)
        selected = pd.read_csv(selected_path, dtype=str)
        source_pairs = {
            component: tuple(
                pd.read_csv(path, dtype=str)[["subject_id", "study_id"]].iloc[0]
            )
            for component, path in records.items()
        }
        no_cine_pair = source_pairs["batch_002"]
        embedded = selected[
            selected["study_id"].astype(str) != str(no_cine_pair[1])
        ][["subject_id", "study_id"]]
        historical = root / "study_embedding_manifest.csv"
        embedded.to_csv(historical, index=False)

        duplicate = root / "duplicate_resolution.csv"
        pd.DataFrame(
            [
                {
                    "subject_id": source_pairs["batch_000"][0],
                    "study_id": source_pairs["batch_000"][1],
                    "components": "batch_000",
                    "selected_scope": "selected",
                    "adjudication_category": "SOURCE_ARTIFACT_PURGED",
                    "quarantine_required": True,
                }
            ]
        ).to_csv(duplicate, index=False)

        canonical = root / "canonical_inventory.csv"
        pd.DataFrame(
            [
                {
                    "subject_id": source_pairs["stage_d"][0],
                    "study_id": source_pairs["stage_d"][1],
                    "components": "stage_d",
                    "source_availability": "SURVIVING_NPZ",
                    "quarantine_required": False,
                    "n_proposed_canonical_rows": 1,
                }
            ]
        ).to_csv(canonical, index=False)

        release = root / "SHA256SUMS.txt"
        release.write_text(
            "".join(
                f"{'a' * 64}  ./{row['dicom_filepath']}\n"
                for path in records.values()
                for row in pd.read_csv(path, dtype=str).to_dict(orient="records")
            ),
            encoding="utf-8",
        )

        names = (
            "EXPECTED_HISTORICAL_IMAGING_STUDIES",
            "EXPECTED_HISTORICAL_NO_CINE_STUDIES",
            "EXPECTED_TRAIN_NO_CINE_STUDIES",
            "EXPECTED_DUPLICATE_GROUPS",
            "EXPECTED_CANONICAL_PHYSICAL_SOURCE_GROUPS",
            "EXPECTED_STAGE_D_SURVIVING_NPZ_GROUPS",
        )
        original = {name: getattr(builder, name) for name in names}
        replacements = {
            "EXPECTED_HISTORICAL_IMAGING_STUDIES": n_selected - 1,
            "EXPECTED_HISTORICAL_NO_CINE_STUDIES": 1,
            "EXPECTED_TRAIN_NO_CINE_STUDIES": 1,
            "EXPECTED_DUPLICATE_GROUPS": 1,
            "EXPECTED_CANONICAL_PHYSICAL_SOURCE_GROUPS": 1,
            "EXPECTED_STAGE_D_SURVIVING_NPZ_GROUPS": 1,
        }
        for name, value in replacements.items():
            setattr(builder, name, value)
        authority_paths = builder._input_authority_paths(
            selected_path=selected_path,
            split_path=split_path,
            record_components=records,
            historical_study_manifest=historical,
            duplicate_resolution=duplicate,
            canonical_inventory=canonical,
        )
        expected_hashes = {
            name: builder.file_sha256(path) for name, path in authority_paths.items()
        }
        split_values = pd.read_csv(split_path)["split"]
        expected_splits = {
            name: int(split_values.eq(name).sum())
            for name in ("train", "val", "test")
        }
        try:
            summary = execute(
                selected_path=selected_path,
                split_path=split_path,
                record_components=records,
                candidate_evidence=None,
                historical_study_manifest=historical,
                duplicate_resolution=duplicate,
                canonical_inventory=canonical,
                release_checksums=release,
                restricted_output_dir=root / "restricted",
                aggregate_output_dir=root / "aggregate",
                expected_selected_sha256=None,
                expected_selected_studies=n_selected,
                expected_input_sha256=expected_hashes,
                expected_split_counts=expected_splits,
            )
        finally:
            for name, value in original.items():
                setattr(builder, name, value)
        assert summary["candidate_construction_mode"] == "phase1d_restricted_provenance"
        assert summary["all_selected_objects_have_release_sha256"] is True
        assert summary["smoke_n_studies"] == 4
        assert summary["restricted_input_authority_hash_set_exact"] is True
        assert summary["locked_split_counts_match"] is True
        assert builder.file_sha256(
            root / "restricted" / "technical_smoke_source_manifest_restricted.csv"
        ) == summary["technical_smoke_source_manifest_sha256"]


def test_locked_input_hash_and_split_count_gates_fail_before_outputs() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        selected, split, records, _, _ = _fixture(root)
        paths = builder._input_authority_paths(
            selected_path=selected,
            split_path=split,
            record_components=records,
            historical_study_manifest=None,
            duplicate_resolution=None,
            canonical_inventory=None,
        )
        expected = {name: builder.file_sha256(path) for name, path in paths.items()}
        verified = builder.verify_locked_input_authorities(paths, expected)
        assert verified == expected

        split_frame = pd.read_csv(split, dtype=str)
        split_frame.loc[0, "split"] = "test"
        split_frame.to_csv(split, index=False)
        try:
            builder.verify_locked_input_authorities(paths, expected)
        except ValueError:
            pass
        else:
            raise AssertionError("Mutated split authority passed its SHA-256 gate")
        assert not (root / "restricted").exists()


def test_outcome_bearing_candidate_evidence_is_rejected() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        inputs = _fixture(root)
        bad = pd.read_csv(inputs[3]["stage_d_cine_positive"], dtype=str)
        bad["lvef_label"] = "synthetic"
        bad.to_csv(inputs[3]["stage_d_cine_positive"], index=False)
        try:
            execute(
                selected_path=inputs[0],
                split_path=inputs[1],
                record_components=inputs[2],
                candidate_evidence=inputs[3],
                restricted_output_dir=root / "restricted",
                aggregate_output_dir=root / "aggregate",
                expected_selected_sha256=None,
                expected_selected_studies=inputs[4],
            )
        except ValueError:
            return
        raise AssertionError("Outcome-bearing candidate evidence was accepted")
