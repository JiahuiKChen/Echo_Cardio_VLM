#!/usr/bin/env python3
"""Join ECHOVIEW view probabilities to a clip/study manifest.

This script is intentionally path-driven and post-hoc. It does not assume SCC
layout and treats the joined manifest as potentially patient-level output.
Write outputs only to an approved destination for the data being joined.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd


PROB_PREFIX = "prob_"
A4C_FAMILY = ["prob_a4c", "prob_a4c_lvocc_s", "prob_a4c_laocc"]
RV_INFLOW = ["prob_rvinf"]
A5C = ["prob_a5c"]
OTHER = ["prob_other"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--echoview-csv", type=Path, required=True)
    parser.add_argument("--manifest-csv", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--join-key-strategy",
        choices=["basename", "explicit"],
        default="basename",
        help="Default derives basename from manifest DICOM path and ECHOVIEW image.",
    )
    parser.add_argument("--manifest-dicom-column", default=None)
    parser.add_argument("--manifest-join-column", default=None)
    parser.add_argument("--echoview-image-column", default=None)
    parser.add_argument("--study-column", default="study_id")
    parser.add_argument("--subject-column", default="subject_id")
    parser.add_argument(
        "--include-raw-unmatched-examples",
        action="store_true",
        help="Write raw unmatched keys. Default writes SHA256 hashes only.",
    )
    parser.add_argument("--max-examples", type=int, default=20)
    return parser.parse_args()


def read_csv_or_fail(path: Path, label: str) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Missing {label}: {path}")
    return pd.read_csv(path)


def first_present(columns: list[str], candidates: list[str]) -> str | None:
    lower_map = {c.lower(): c for c in columns}
    for cand in candidates:
        if cand.lower() in lower_map:
            return lower_map[cand.lower()]
    return None


def detect_manifest_dicom_column(df: pd.DataFrame) -> str | None:
    return first_present(
        list(df.columns),
        [
            "dicom_filepath",
            "dicom_path",
            "gcs_object_path",
            "image",
            "filepath",
            "path",
            "file",
        ],
    )


def detect_echoview_image_column(df: pd.DataFrame) -> str | None:
    return first_present(list(df.columns), ["image", "Image", "dicom", "filename", "file"])


def basename_series(values: pd.Series) -> pd.Series:
    return values.fillna("").astype(str).str.replace("\\\\", "/", regex=False).str.split("/").str[-1]


def parse_study_from_image_key(keys: pd.Series) -> pd.Series:
    return keys.astype(str).str.extract(r"^(\d+)_\d+\.dcm$", expand=False)


def hash_key(value: Any) -> str:
    text = "" if pd.isna(value) else str(value)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def example_keys(keys: pd.Series, max_examples: int, include_raw: bool) -> list[dict[str, str]]:
    clean = keys.dropna().astype(str).drop_duplicates().head(max_examples)
    rows: list[dict[str, str]] = []
    for key in clean:
        row = {"key_sha256_16": hash_key(key)}
        if include_raw:
            row["key"] = key
        rows.append(row)
    return rows


def count_unique(df: pd.DataFrame, column: str | None) -> int | None:
    if column and column in df.columns:
        return int(df[column].nunique(dropna=True))
    return None


def count_duplicates(df: pd.DataFrame, key_col: str) -> dict[str, int]:
    vc = df[key_col].value_counts(dropna=False)
    dup = vc[vc > 1]
    return {
        "n_duplicate_rows": int(df.duplicated(subset=[key_col], keep=False).sum()),
        "n_duplicate_keys": int(len(dup)),
        "max_rows_per_key": int(vc.max()) if len(vc) else 0,
    }


def available(cols: list[str], df: pd.DataFrame) -> list[str]:
    return [c for c in cols if c in df.columns]


def main() -> int:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    manifest = read_csv_or_fail(args.manifest_csv, "manifest CSV")
    echoview = read_csv_or_fail(args.echoview_csv, "ECHOVIEW CSV")
    warnings: list[str] = []

    image_col = args.echoview_image_column or detect_echoview_image_column(echoview)
    if image_col is None:
        raise ValueError("Could not detect ECHOVIEW image column; pass --echoview-image-column.")

    if args.join_key_strategy == "explicit":
        if not args.manifest_join_column:
            raise ValueError("--manifest-join-column is required for --join-key-strategy explicit.")
        if args.manifest_join_column not in manifest.columns:
            raise ValueError(f"Manifest missing explicit join column: {args.manifest_join_column}")
        manifest_key_source = args.manifest_join_column
        manifest["_echoview_join_key"] = manifest[manifest_key_source].fillna("").astype(str)
        echoview["_echoview_join_key"] = echoview[image_col].fillna("").astype(str)
    else:
        manifest_key_source = args.manifest_dicom_column or detect_manifest_dicom_column(manifest)
        if manifest_key_source is None:
            raise ValueError(
                "Could not detect manifest DICOM/path column; pass --manifest-dicom-column "
                "or use --join-key-strategy explicit."
            )
        manifest["_echoview_join_key"] = basename_series(manifest[manifest_key_source])
        echoview["_echoview_join_key"] = basename_series(echoview[image_col])

    echoview["_echoview_study_id_from_image"] = parse_study_from_image_key(echoview["_echoview_join_key"])

    prob_cols = [c for c in echoview.columns if c.startswith(PROB_PREFIX)]
    if not prob_cols:
        warnings.append("No ECHOVIEW probability columns detected.")

    manifest_null_keys = int((manifest["_echoview_join_key"].fillna("") == "").sum())
    echoview_null_keys = int((echoview["_echoview_join_key"].fillna("") == "").sum())
    if manifest_null_keys:
        warnings.append(f"Manifest has {manifest_null_keys} null or empty join keys.")
    if echoview_null_keys:
        warnings.append(f"ECHOVIEW has {echoview_null_keys} null or empty join keys.")

    manifest_dups = count_duplicates(manifest, "_echoview_join_key")
    echoview_dups = count_duplicates(echoview, "_echoview_join_key")
    if manifest_dups["n_duplicate_keys"]:
        warnings.append(f"Manifest has {manifest_dups['n_duplicate_keys']} duplicate join keys.")
    if echoview_dups["n_duplicate_keys"]:
        warnings.append(f"ECHOVIEW has {echoview_dups['n_duplicate_keys']} duplicate join keys.")
    if manifest_dups["n_duplicate_keys"] and echoview_dups["n_duplicate_keys"]:
        warnings.append("Potential many-to-many join: duplicate keys exist on both sides.")

    echoview_cols = ["_echoview_join_key", "_echoview_study_id_from_image", image_col] + prob_cols
    echoview_cols = list(dict.fromkeys([c for c in echoview_cols if c in echoview.columns]))

    joined = manifest.merge(
        echoview[echoview_cols],
        on="_echoview_join_key",
        how="left",
        indicator="_echoview_merge",
        suffixes=("", "_echoview"),
    )
    joined["echoview_matched"] = joined["_echoview_merge"].eq("both")

    manifest_keys = set(manifest["_echoview_join_key"].dropna().astype(str))
    echoview_keys = set(echoview["_echoview_join_key"].dropna().astype(str))
    unmatched_manifest_keys = pd.Series(sorted(manifest_keys - echoview_keys), dtype="object")
    unmatched_echoview_keys = pd.Series(sorted(echoview_keys - manifest_keys), dtype="object")

    study_col = args.study_column if args.study_column in joined.columns else None
    subject_col = args.subject_column if args.subject_column in joined.columns else None

    counts_rows = [
        {"metric": "manifest_rows", "value": int(len(manifest))},
        {"metric": "echoview_rows", "value": int(len(echoview))},
        {"metric": "joined_rows", "value": int(len(joined))},
        {"metric": "manifest_unique_join_keys", "value": int(manifest["_echoview_join_key"].nunique(dropna=True))},
        {"metric": "echoview_unique_join_keys", "value": int(echoview["_echoview_join_key"].nunique(dropna=True))},
        {"metric": "matched_manifest_rows", "value": int(joined["echoview_matched"].sum())},
        {"metric": "unmatched_manifest_rows", "value": int((~joined["echoview_matched"]).sum())},
        {"metric": "unmatched_echoview_keys", "value": int(len(unmatched_echoview_keys))},
        {"metric": "matched_manifest_studies", "value": count_unique(joined[joined["echoview_matched"]], study_col)},
        {"metric": "manifest_studies", "value": count_unique(manifest, args.study_column)},
        {"metric": "manifest_subjects", "value": count_unique(manifest, args.subject_column)},
        {"metric": "probability_columns_detected", "value": int(len(prob_cols))},
        {"metric": "manifest_duplicate_join_keys", "value": manifest_dups["n_duplicate_keys"]},
        {"metric": "echoview_duplicate_join_keys", "value": echoview_dups["n_duplicate_keys"]},
    ]
    counts_df = pd.DataFrame(counts_rows)

    view_columns = {
        "a4c_family": available(A4C_FAMILY, echoview),
        "rv_inflow": available(RV_INFLOW, echoview),
        "a5c": available(A5C, echoview),
        "other": available(OTHER, echoview),
    }

    if study_col is not None and "_echoview_study_id_from_image" in joined.columns:
        mismatch_mask = (
            joined["echoview_matched"]
            & joined["_echoview_study_id_from_image"].notna()
            & (joined[study_col].astype(str) != joined["_echoview_study_id_from_image"].astype(str))
        )
        mismatch_count = int(mismatch_mask.sum())
        counts_df = pd.concat(
            [counts_df, pd.DataFrame([{"metric": "matched_study_id_mismatches", "value": mismatch_count}])],
            ignore_index=True,
        )
        if mismatch_count:
            warnings.append(f"{mismatch_count} matched rows have study_id mismatch versus ECHOVIEW image key.")

    joined_path = args.output_dir / "echoview_joined_clip_manifest.csv"
    counts_path = args.output_dir / "echoview_join_counts.csv"
    summary_path = args.output_dir / "echoview_join_audit_summary.json"
    warnings_path = args.output_dir / "echoview_join_warnings.json"
    examples_path = args.output_dir / "echoview_unmatched_key_examples.json"

    joined.drop(columns=["_echoview_merge"], errors="ignore").to_csv(joined_path, index=False)
    counts_df.to_csv(counts_path, index=False)

    summary: dict[str, Any] = {
        "echoview_csv": str(args.echoview_csv),
        "manifest_csv": str(args.manifest_csv),
        "output_dir": str(args.output_dir),
        "join_key_strategy": args.join_key_strategy,
        "manifest_key_source_column": manifest_key_source,
        "echoview_image_column": image_col,
        "probability_columns": prob_cols,
        "view_policy_columns_detected": view_columns,
        "duplicate_checks": {
            "manifest": manifest_dups,
            "echoview": echoview_dups,
        },
        "counts": {row["metric"]: row["value"] for row in counts_rows},
        "joined_manifest_is_patient_level": True,
        "aggregate_audits_are_manuscript_safe": True,
    }
    summary_path.write_text(json.dumps(summary, indent=2))

    examples = {
        "raw_examples_included": bool(args.include_raw_unmatched_examples),
        "unmatched_manifest_keys": example_keys(
            unmatched_manifest_keys, args.max_examples, args.include_raw_unmatched_examples
        ),
        "unmatched_echoview_keys": example_keys(
            unmatched_echoview_keys, args.max_examples, args.include_raw_unmatched_examples
        ),
    }
    examples_path.write_text(json.dumps(examples, indent=2))

    warnings_payload = {
        "warnings": warnings,
        "fail_closed_notes": [
            "The joined manifest may contain patient-level columns inherited from the input manifest.",
            "Write joined outputs only to an approved destination.",
            "Unmatched key examples are hashed unless --include-raw-unmatched-examples is used.",
        ],
    }
    warnings_path.write_text(json.dumps(warnings_payload, indent=2))

    print(json.dumps({"summary": summary, "warnings": warnings}, indent=2))
    print(f"[written] {joined_path.resolve()}")
    print(f"[written] {counts_path.resolve()}")
    print(f"[written] {summary_path.resolve()}")
    print(f"[written] {warnings_path.resolve()}")
    print(f"[written] {examples_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
