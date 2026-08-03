import json
from pathlib import Path
import sys
import tempfile

import pandas as pd


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from audit_canonical_selected_clip_inventory import (  # noqa: E402
    AGGREGATE_FILENAMES,
    main as inventory_main,
)
from lvef_multitask_clip_provenance import EXPECTED_COMPONENTS  # noqa: E402


CLIP_COLUMNS = [
    "embedding_idx",
    "subject_id",
    "study_id",
    "dicom_filepath",
    "npz_path",
    "view_id",
    "view_name",
    "embedding_l2_norm",
    "write_ok",
    "error",
]
EXTRACTION_COLUMNS = [
    "subject_id",
    "study_id",
    "dicom_filepath",
    "output_path",
    "write_ok",
    "source_num_frames",
    "source_rows",
    "source_columns",
    "target_frames",
    "target_size",
]
DICOM_AUDIT_COLUMNS = [
    "subject_id",
    "study_id",
    "dicom_filepath",
    "dicom_abs_path",
    "read_ok",
]


def test_canonical_inventory_counts_components_dedup_and_source_reextraction() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        duplicate_dicom = root / "duplicate.dcm"
        duplicate_npz = root / "duplicate.npz"
        retained_dicom = root / "retained.dcm"
        outside_dicom = root / "outside.dcm"
        outside_npz = root / "outside.npz"
        duplicate_dicom.write_bytes(b"duplicate-dicom")
        duplicate_npz.write_bytes(b"synthetic-extracted-clip")
        retained_dicom.write_bytes(b"retained-dicom")
        outside_dicom.write_bytes(b"outside-dicom")
        outside_npz.write_bytes(b"outside-extracted")
        missing_npz = root / "purged-extracted.npz"

        batch_duplicate = pd.DataFrame(
            [
                [
                    0,
                    "SYN_SUBJECT_1",
                    "SYN_STUDY_1",
                    str(duplicate_dicom),
                    str(duplicate_npz),
                    -1,
                    None,
                    1.0,
                    True,
                    None,
                ],
                [
                    1,
                    "SYN_SUBJECT_1",
                    "SYN_STUDY_1",
                    str(duplicate_dicom),
                    str(duplicate_npz),
                    -1,
                    None,
                    1.0,
                    True,
                    None,
                ],
            ],
            columns=CLIP_COLUMNS,
        )
        stage_rows = pd.DataFrame(
            [
                [
                    0,
                    "SYN_SUBJECT_2",
                    "SYN_STUDY_2",
                    str(retained_dicom),
                    str(missing_npz),
                    -1,
                    None,
                    1.0,
                    True,
                    None,
                ],
                [
                    1,
                    "SYN_OUTSIDE_SUBJECT",
                    "SYN_OUTSIDE_STUDY",
                    str(outside_dicom),
                    str(outside_npz),
                    -1,
                    None,
                    1.0,
                    True,
                    None,
                ],
            ],
            columns=CLIP_COLUMNS,
        )
        batch_extraction = pd.DataFrame(
            [
                [
                    "SYN_SUBJECT_1",
                    "SYN_STUDY_1",
                    str(duplicate_dicom),
                    str(duplicate_npz),
                    True,
                    2,
                    3,
                    4,
                    2,
                    4,
                ]
            ],
            columns=EXTRACTION_COLUMNS,
        )
        stage_extraction = pd.DataFrame(
            [
                [
                    "SYN_SUBJECT_2",
                    "SYN_STUDY_2",
                    str(retained_dicom),
                    str(missing_npz),
                    True,
                    2,
                    3,
                    4,
                    2,
                    4,
                ],
                [
                    "SYN_OUTSIDE_SUBJECT",
                    "SYN_OUTSIDE_STUDY",
                    str(outside_dicom),
                    str(outside_npz),
                    True,
                    2,
                    3,
                    4,
                    2,
                    4,
                ],
            ],
            columns=EXTRACTION_COLUMNS,
        )

        manifest_args: list[str] = []
        extraction_args: list[str] = []
        dicom_audit_args: list[str] = []
        for component in EXPECTED_COMPONENTS:
            manifest = root / f"{component}_clip.csv"
            extraction = root / f"{component}_extraction.csv"
            dicom_audit = root / f"{component}_dicom_audit.csv"
            if component == "batch_000":
                batch_duplicate.to_csv(manifest, index=False)
                batch_extraction.to_csv(extraction, index=False)
                pd.DataFrame(
                    [
                        [
                            "SYN_SUBJECT_1",
                            "SYN_STUDY_1",
                            str(duplicate_dicom),
                            str(duplicate_dicom),
                            True,
                        ]
                    ],
                    columns=DICOM_AUDIT_COLUMNS,
                ).to_csv(dicom_audit, index=False)
            elif component == "stage_d":
                stage_rows.to_csv(manifest, index=False)
                stage_extraction.to_csv(extraction, index=False)
                pd.DataFrame(
                    [
                        [
                            "SYN_SUBJECT_2",
                            "SYN_STUDY_2",
                            str(retained_dicom),
                            str(retained_dicom),
                            True,
                        ],
                        [
                            "SYN_OUTSIDE_SUBJECT",
                            "SYN_OUTSIDE_STUDY",
                            str(outside_dicom),
                            str(outside_dicom),
                            True,
                        ],
                    ],
                    columns=DICOM_AUDIT_COLUMNS,
                ).to_csv(dicom_audit, index=False)
            else:
                pd.DataFrame(columns=CLIP_COLUMNS).to_csv(manifest, index=False)
                pd.DataFrame(columns=EXTRACTION_COLUMNS).to_csv(extraction, index=False)
                pd.DataFrame(columns=DICOM_AUDIT_COLUMNS).to_csv(dicom_audit, index=False)
            manifest_args.extend(["--component-manifest", f"{component}={manifest}"])
            extraction_args.extend(
                ["--component-extraction-manifest", f"{component}={extraction}"]
            )
            dicom_audit_args.extend(
                ["--component-dicom-audit", f"{component}={dicom_audit}"]
            )

        selected = root / "selected.csv"
        pd.DataFrame(
            {
                "subject_id": ["SYN_SUBJECT_1", "SYN_SUBJECT_2"],
                "study_id": ["SYN_STUDY_1", "SYN_STUDY_2"],
            }
        ).to_csv(selected, index=False)
        aggregate = root / "aggregate"
        restricted = root / "restricted"
        original = sys.argv[:]
        sys.argv = [
            "audit_canonical_selected_clip_inventory.py",
            *manifest_args,
            *extraction_args,
            *dicom_audit_args,
            "--selected-studies",
            str(selected),
            "--hash-mode",
            "duplicates",
            "--expected-selected-imaging-studies",
            "2",
            "--aggregate-output-dir",
            str(aggregate),
            "--restricted-output-dir",
            str(restricted),
        ]
        try:
            status = inventory_main()
        finally:
            sys.argv = original
        assert status == 0
        assert {path.name for path in aggregate.iterdir()} == set(AGGREGATE_FILENAMES)
        summary = json.loads(
            (aggregate / "canonical_selected_clip_inventory.summary.json").read_text()
        )
        assert summary["n_selected_studies_with_at_least_one_proposed_canonical_clip"] == 2
        assert summary["n_exact_deduplication_rows_removed"] == 1
        assert summary["n_physical_sources_with_source_dicom_no_npz"] == 1
        assert summary["n_outside_selected_rows_excluded"] == 1
        assert summary["proposed_availability_path"] == (
            "C2_SELECTED_ONLY_REEXTRACTION_FROM_RETAINED_DICOM"
        )
        assert summary["vector_arrays_read"] is False
        by_component = pd.read_csv(
            aggregate / "canonical_selected_clip_inventory_by_component.csv"
        )
        assert list(by_component["component"]) == list(EXPECTED_COMPONENTS)
        batch = by_component.loc[by_component["component"] == "batch_000"].iloc[0]
        assert batch["n_exact_deduplication_rows_removed"] == 1
        stage = by_component.loc[by_component["component"] == "stage_d"].iloc[0]
        assert stage["n_sources_requiring_npz_reextraction"] == 1
        aggregate_text = "\n".join(
            path.read_text() for path in aggregate.iterdir() if path.is_file()
        )
        assert "SYN_STUDY" not in aggregate_text
        assert str(root) not in aggregate_text
        restricted_text = (
            restricted / "canonical_selected_clip_inventory_restricted.csv"
        ).read_text()
        assert "SYN_STUDY_1" in restricted_text
