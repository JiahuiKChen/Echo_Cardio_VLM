from dataclasses import replace
import json
from pathlib import Path
import sys
import tempfile

import numpy as np
import pandas as pd


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from audit_duplicate_clip_keys import (  # noqa: E402
    GroupEvidence,
    _named_paths,
    classify_duplicate_group,
    main as duplicate_audit_main,
)


def _base_evidence() -> GroupEvidence:
    return GroupEvidence(
        source_count=2,
        merged_count=2,
        source_full_rows_equal=False,
        source_nonindex_rows_equal=True,
        source_vectors_exact_equal=True,
        source_vectors_numerically_equal=True,
        merged_vectors_exact_equal=True,
        merged_vectors_numerically_equal=True,
        source_merged_manifest_payload_equal_excluding_index_and_norm=True,
        source_merged_vectors_exact_multiset_equal=True,
        source_merged_vectors_numerically_match=True,
        extracted_hashes_complete=True,
        n_unique_extracted_hashes=1,
        frame_hashes_complete=True,
        n_unique_frame_hashes=1,
        dicom_hashes_complete=False,
        n_unique_dicom_hashes=0,
    )


def test_duplicate_classification_exact_repeated_manifest_row() -> None:
    evidence = replace(_base_evidence(), source_full_rows_equal=True)
    assert classify_duplicate_group(evidence) == "EXACT_REPEATED_MANIFEST_ROW"


def test_duplicate_classification_same_clip_embedded_twice() -> None:
    assert classify_duplicate_group(_base_evidence()) == "SAME_CLIP_EMBEDDED_TWICE"


def test_duplicate_classification_different_physical_clips_has_precedence() -> None:
    evidence = replace(_base_evidence(), n_unique_frame_hashes=2)
    assert (
        classify_duplicate_group(evidence)
        == "DIFFERENT_CLIPS_SHARE_NONUNIQUE_KEY"
    )


def test_duplicate_classification_merge_defect_and_unresolved_fail_closed() -> None:
    merge = replace(_base_evidence(), merged_count=3)
    assert classify_duplicate_group(merge) == "MERGE_REINDEXING_DEFECT"
    unresolved = replace(
        _base_evidence(),
        extracted_hashes_complete=False,
        n_unique_extracted_hashes=0,
        frame_hashes_complete=False,
        n_unique_frame_hashes=0,
        source_vectors_numerically_equal=False,
    )
    assert classify_duplicate_group(unresolved) == "OTHER_UNRESOLVED"


def test_component_allowlist_rejects_unknown_or_duplicate_labels() -> None:
    try:
        _named_paths(["unsafe_component=/tmp/a"], "manifest")
    except ValueError:
        pass
    else:
        raise AssertionError("Unsafe component label was accepted")
    try:
        _named_paths(["batch_000=/tmp/a", "batch_000=/tmp/b"], "manifest")
    except ValueError:
        return
    raise AssertionError("Duplicate component label was accepted")


def test_duplicate_audit_cli_keeps_identifiers_and_paths_restricted() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        extracted = root / "restricted_clip.npz"
        np.savez_compressed(
            extracted,
            frames=np.arange(2 * 3 * 4, dtype=np.uint8).reshape(2, 3, 4),
        )
        vectors = np.arange(8, dtype=np.float32).reshape(2, 4)
        vectors[1] = vectors[0]
        component_npz = root / "component_embeddings.npz"
        merged_npz = root / "merged_embeddings.npz"
        np.savez_compressed(component_npz, embeddings=vectors)
        np.savez_compressed(merged_npz, embeddings=vectors)
        manifest = pd.DataFrame(
            {
                "embedding_idx": [0, 1],
                "subject_id": ["SYN_SUBJECT", "SYN_SUBJECT"],
                "study_id": ["SYN_STUDY", "SYN_STUDY"],
                "dicom_filepath": ["SYN_DICOM", "SYN_DICOM"],
                "npz_path": [str(extracted), str(extracted)],
                "view_id": [-1, -1],
                "view_name": [None, None],
                "embedding_l2_norm": [
                    float(np.linalg.norm(vectors[0])),
                    float(np.linalg.norm(vectors[1])),
                ],
                "write_ok": [True, True],
                "error": [None, None],
            }
        )
        component_manifest = root / "component.csv"
        merged_manifest = root / "merged.csv"
        selected = root / "selected.csv"
        manifest.to_csv(component_manifest, index=False)
        manifest.to_csv(merged_manifest, index=False)
        pd.DataFrame(
            {"subject_id": ["SYN_SUBJECT"], "study_id": ["SYN_STUDY"]}
        ).to_csv(selected, index=False)
        aggregate = root / "aggregate"
        restricted = root / "restricted"
        original_argv = sys.argv[:]
        sys.argv = [
            "audit_duplicate_clip_keys.py",
            "--component-manifest",
            f"batch_000={component_manifest}",
            "--component-embedding-npz",
            f"batch_000={component_npz}",
            "--merged-manifest",
            str(merged_manifest),
            "--merged-embedding-npz",
            str(merged_npz),
            "--selected-studies",
            str(selected),
            "--aggregate-output-dir",
            str(aggregate),
            "--restricted-output-dir",
            str(restricted),
            "--expected-component-count",
            "1",
            "--expected-duplicate-keys",
            "1",
            "--expected-embedding-dim",
            "4",
        ]
        try:
            status = duplicate_audit_main()
        finally:
            sys.argv = original_argv
        assert status == 1
        summary = json.loads(
            (aggregate / "duplicate_clip_key_adjudication.summary.json").read_text()
        )
        assert summary["n_duplicate_groups"] == 1
        assert summary["expected_duplicate_group_count_matches"] is True
        counts = pd.read_csv(aggregate / "duplicate_clip_key_reason_counts.csv")
        assert counts.iloc[0]["classification"] == "SAME_CLIP_EMBEDDED_TWICE"
        assert counts.iloc[0]["component"] == "batch_000"
        aggregate_text = "\n".join(
            path.read_text() for path in aggregate.iterdir() if path.is_file()
        )
        assert "SYN_SUBJECT" not in aggregate_text
        assert "SYN_STUDY" not in aggregate_text
        assert str(root) not in aggregate_text
        restricted_text = (
            restricted / "duplicate_clip_groups_restricted.csv"
        ).read_text()
        assert "SYN_STUDY" in restricted_text
