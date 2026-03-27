#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a still-image manifest with LVEF labels from key-frame outputs."
    )
    parser.add_argument("--selected-studies-csv", type=Path, required=True, help="Path to selected_studies.csv")
    parser.add_argument(
        "--structured-measurements-csv",
        type=Path,
        required=True,
        help="Path to structured_measurements.csv exported for the cohort.",
    )
    parser.add_argument("--keyframe-manifest-csv", type=Path, required=True, help="Path to keyframe_manifest.csv")
    parser.add_argument("--output-csv", type=Path, required=True, help="Path to output model-ready CSV manifest")
    parser.add_argument(
        "--summary-json",
        type=Path,
        default=None,
        help="Optional summary JSON path. Defaults to <output-csv>.summary.json",
    )
    parser.add_argument(
        "--subject-split-map-csv",
        type=Path,
        default=None,
        help="Optional frozen subject split map CSV with columns: subject_id,split.",
    )
    parser.add_argument("--seed", type=int, default=1337, help="Random seed for subject-level split assignment")
    parser.add_argument("--train-frac", type=float, default=0.7, help="Train fraction at subject level")
    parser.add_argument("--val-frac", type=float, default=0.15, help="Validation fraction at subject level")
    parser.add_argument("--lvef-binary-threshold", type=float, default=40.0, help="Reduced EF threshold in percent")
    return parser.parse_args()


def split_group(ids: list[int], rng: np.random.Generator, train_frac: float, val_frac: float) -> dict[int, str]:
    if not ids:
        return {}
    perm = np.array(ids, dtype=int)
    rng.shuffle(perm)
    n = len(perm)

    if n == 1:
        return {int(perm[0]): "train"}
    if n == 2:
        return {int(perm[0]): "train", int(perm[1]): "test"}

    n_train = max(1, int(np.floor(n * train_frac)))
    n_val = max(1, int(np.floor(n * val_frac)))
    n_test = n - n_train - n_val

    while n_test < 1:
        if n_val > 1:
            n_val -= 1
        else:
            n_train -= 1
        n_test = n - n_train - n_val

    out: dict[int, str] = {}
    for s in perm[:n_train]:
        out[int(s)] = "train"
    for s in perm[n_train : n_train + n_val]:
        out[int(s)] = "val"
    for s in perm[n_train + n_val :]:
        out[int(s)] = "test"
    return out


def assign_subject_splits(
    subject_to_label: pd.DataFrame, seed: int, train_frac: float, val_frac: float
) -> dict[int, str]:
    if train_frac <= 0 or train_frac >= 1:
        raise ValueError("train-frac must be in (0, 1).")
    if val_frac < 0 or train_frac + val_frac >= 1:
        raise ValueError("val-frac must be >= 0 and train-frac + val-frac must be < 1.")

    rng = np.random.default_rng(seed)
    split_map: dict[int, str] = {}
    for label in sorted(subject_to_label["label"].unique()):
        ids = (
            subject_to_label[subject_to_label["label"] == label]["subject_id"]
            .astype(int)
            .drop_duplicates()
            .tolist()
        )
        split_map.update(split_group(ids=ids, rng=rng, train_frac=train_frac, val_frac=val_frac))

    # Ensure each split is represented in tiny cohorts by moving one subject if needed.
    present = set(split_map.values())
    if "val" not in present:
        for s, split in list(split_map.items()):
            if split == "train":
                split_map[s] = "val"
                break
    present = set(split_map.values())
    if "test" not in present:
        for s, split in list(split_map.items()):
            if split == "train":
                split_map[s] = "test"
                break
    return split_map


def lvef_class_4(lvef: float) -> str:
    if lvef < 30.0:
        return "severely_reduced"
    if lvef < 40.0:
        return "moderately_reduced"
    if lvef < 50.0:
        return "mildly_reduced"
    return "preserved"


def build_summary(manifest: pd.DataFrame, args: argparse.Namespace) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "n_rows": int(len(manifest)),
        "n_subjects": int(manifest["subject_id"].nunique()) if not manifest.empty else 0,
        "n_studies": int(manifest["study_id"].nunique()) if not manifest.empty else 0,
        "n_images": int(manifest["keyframe_path"].nunique()) if "keyframe_path" in manifest else 0,
        "lvef_binary_threshold": float(args.lvef_binary_threshold),
        "output_csv": str(args.output_csv.resolve()),
        "subject_split_map_csv": str(args.subject_split_map_csv.resolve()) if args.subject_split_map_csv else None,
    }
    if not manifest.empty:
        summary["split_counts"] = manifest["split"].value_counts().to_dict()
        summary["split_subject_counts"] = manifest.groupby("split")["subject_id"].nunique().to_dict()
        summary["lvef_binary_counts"] = manifest["lvef_binary_reduced"].value_counts().to_dict()
        summary["lvef_class4_counts"] = manifest["lvef_class4"].value_counts().to_dict()
        summary["lvef_min"] = float(manifest["lvef"].min())
        summary["lvef_median"] = float(manifest["lvef"].median())
        summary["lvef_max"] = float(manifest["lvef"].max())
    return summary


def apply_frozen_split_map(manifest: pd.DataFrame, split_map_csv: Path) -> pd.DataFrame:
    split_df = pd.read_csv(split_map_csv)
    required = {"subject_id", "split"}
    missing = required - set(split_df.columns)
    if missing:
        raise ValueError(f"Split map missing required columns: {sorted(missing)}")

    split_df = split_df[["subject_id", "split"]].copy()
    split_df["subject_id"] = pd.to_numeric(split_df["subject_id"], errors="coerce").astype("Int64")
    split_df = split_df.dropna(subset=["subject_id"]).copy()
    split_df["subject_id"] = split_df["subject_id"].astype(int)
    split_df["split"] = split_df["split"].astype(str).str.lower().str.strip()

    allowed = {"train", "val", "test"}
    bad_split = sorted(set(split_df["split"].unique()) - allowed)
    if bad_split:
        raise ValueError(f"Split map contains invalid split values: {bad_split}")

    dup_subjects = split_df["subject_id"][split_df["subject_id"].duplicated()].unique().tolist()
    if dup_subjects:
        raise ValueError(f"Split map has duplicate subject_id rows (showing first 10): {dup_subjects[:10]}")

    out = manifest.merge(split_df, how="left", on="subject_id", validate="many_to_one")
    missing_subjects = out[out["split"].isna()]["subject_id"].drop_duplicates().sort_values().tolist()
    if missing_subjects:
        preview = ",".join(str(x) for x in missing_subjects[:10])
        raise RuntimeError(
            f"Frozen split map is missing {len(missing_subjects)} subject(s). "
            f"First subject_ids: {preview}"
        )
    return out


def main() -> int:
    args = parse_args()

    selected_df = pd.read_csv(args.selected_studies_csv)
    measures_df = pd.read_csv(args.structured_measurements_csv)
    keyframes_df = pd.read_csv(args.keyframe_manifest_csv)

    keyframes_df = keyframes_df[keyframes_df["write_ok"].fillna(False)].copy()
    keyframes_df["subject_id"] = keyframes_df["subject_id"].astype(int)
    keyframes_df["study_id"] = keyframes_df["study_id"].astype(int)

    lvef_df = measures_df[measures_df["measurement"] == "lvef"].copy()
    lvef_df["lvef"] = pd.to_numeric(lvef_df["result"], errors="coerce")
    lvef_df = lvef_df.dropna(subset=["lvef"]).copy()
    lvef_df = (
        lvef_df.groupby(["subject_id", "measurement_id"], as_index=False)["lvef"]
        .median()
        .rename(columns={"lvef": "lvef"})
    )
    lvef_df["subject_id"] = lvef_df["subject_id"].astype(int)
    lvef_df["measurement_id"] = lvef_df["measurement_id"].astype(int)

    studies_df = selected_df[["subject_id", "study_id", "measurement_id", "n_dicoms"]].copy()
    studies_df["subject_id"] = studies_df["subject_id"].astype(int)
    studies_df["study_id"] = studies_df["study_id"].astype(int)
    studies_df["measurement_id"] = studies_df["measurement_id"].astype(int)

    labeled_studies_df = studies_df.merge(
        lvef_df,
        how="inner",
        on=["subject_id", "measurement_id"],
        validate="one_to_one",
    )

    manifest = keyframes_df.merge(
        labeled_studies_df,
        how="inner",
        on=["subject_id", "study_id"],
        validate="many_to_one",
    )

    manifest["lvef_binary_reduced"] = (manifest["lvef"] < args.lvef_binary_threshold).astype(int)
    manifest["lvef_class4"] = manifest["lvef"].apply(lvef_class_4)
    if manifest.empty:
        raise RuntimeError("No labeled rows produced after joining keyframes with LVEF labels.")

    if args.subject_split_map_csv:
        manifest = apply_frozen_split_map(manifest=manifest, split_map_csv=args.subject_split_map_csv)
    else:
        print(
            "[warn] No --subject-split-map-csv provided; using stratified random subject split "
            "(safe for pilot only).",
            file=sys.stderr,
        )
        subject_label_df = manifest[["subject_id", "lvef_binary_reduced"]].drop_duplicates().rename(
            columns={"lvef_binary_reduced": "label"}
        )
        split_map = assign_subject_splits(
            subject_to_label=subject_label_df,
            seed=args.seed,
            train_frac=args.train_frac,
            val_frac=args.val_frac,
        )
        manifest["split"] = manifest["subject_id"].map(split_map)

    cols = [
        "subject_id",
        "study_id",
        "measurement_id",
        "split",
        "keyframe_path",
        "dicom_filepath",
        "npz_path",
        "selected_index",
        "n_frames",
        "method",
        "lvef",
        "lvef_binary_reduced",
        "lvef_class4",
        "n_dicoms",
        "focus_score",
        "motion_score",
        "intensity_mean",
        "contrast_std",
    ]
    manifest = manifest[cols].sort_values(["split", "study_id", "dicom_filepath"]).reset_index(drop=True)

    args.output_csv.parent.mkdir(parents=True, exist_ok=True)
    manifest.to_csv(args.output_csv, index=False)

    summary = build_summary(manifest, args)
    summary_path = args.summary_json or args.output_csv.with_suffix(".summary.json")
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2))

    print(json.dumps(summary, indent=2))
    print(f"[written] {args.output_csv.resolve()}")
    print(f"[written] {summary_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
