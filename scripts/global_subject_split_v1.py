#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a frozen, deterministic subject-level split map (v1) to prevent leakage."
    )
    parser.add_argument(
        "--input-csv",
        type=Path,
        required=True,
        help="CSV containing a subject_id column (e.g., selected_studies.csv).",
    )
    parser.add_argument("--output-csv", type=Path, required=True, help="Output split map CSV.")
    parser.add_argument(
        "--summary-json",
        type=Path,
        default=None,
        help="Optional summary path; defaults to <output-csv>.summary.json",
    )
    parser.add_argument("--subject-column", default="subject_id", help="Subject ID column in input CSV.")
    parser.add_argument("--split-version", default="subject_split_v1", help="Version tag for this split policy.")
    parser.add_argument("--seed-string", default="echo-ai-fixed-split-seed-v1", help="Stable seed string.")
    parser.add_argument("--train-frac", type=float, default=0.7, help="Train fraction.")
    parser.add_argument("--val-frac", type=float, default=0.15, help="Validation fraction.")
    parser.add_argument("--overwrite", action="store_true", help="Allow overwriting output CSV.")
    return parser.parse_args()


def deterministic_score(subject_id: int, seed_string: str) -> float:
    key = f"{seed_string}:{subject_id}".encode("utf-8")
    digest = hashlib.sha256(key).hexdigest()
    # Use 15 hex chars (~60 bits) for deterministic score in [0,1).
    numerator = int(digest[:15], 16)
    denominator = float(16**15)
    return numerator / denominator


def score_to_split(score: float, train_frac: float, val_frac: float) -> str:
    if score < train_frac:
        return "train"
    if score < train_frac + val_frac:
        return "val"
    return "test"


def validate_fracs(train_frac: float, val_frac: float) -> None:
    if not (0 < train_frac < 1):
        raise ValueError("train-frac must be in (0, 1).")
    if not (0 <= val_frac < 1):
        raise ValueError("val-frac must be in [0, 1).")
    if train_frac + val_frac >= 1:
        raise ValueError("train-frac + val-frac must be < 1.")


def main() -> int:
    args = parse_args()
    validate_fracs(args.train_frac, args.val_frac)

    if args.output_csv.exists() and not args.overwrite:
        raise FileExistsError(f"Output exists. Use --overwrite to replace: {args.output_csv}")

    df = pd.read_csv(args.input_csv)
    if args.subject_column not in df.columns:
        raise ValueError(f"Missing subject column '{args.subject_column}' in {args.input_csv}")

    subjects = (
        pd.to_numeric(df[args.subject_column], errors="coerce")
        .dropna()
        .astype(int)
        .drop_duplicates()
        .sort_values()
        .reset_index(drop=True)
    )
    if subjects.empty:
        raise RuntimeError(f"No valid subject IDs found in column '{args.subject_column}'.")

    out = pd.DataFrame({"subject_id": subjects})
    out["split_score"] = out["subject_id"].apply(lambda x: deterministic_score(int(x), args.seed_string))
    out["split"] = out["split_score"].apply(lambda s: score_to_split(float(s), args.train_frac, args.val_frac))
    out["split_version"] = args.split_version
    out["seed_string"] = args.seed_string
    out["train_frac"] = args.train_frac
    out["val_frac"] = args.val_frac

    out = out.sort_values(["split", "subject_id"]).reset_index(drop=True)
    args.output_csv.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(args.output_csv, index=False)

    summary = {
        "input_csv": str(args.input_csv.resolve()),
        "output_csv": str(args.output_csv.resolve()),
        "n_subjects": int(len(out)),
        "split_counts": out["split"].value_counts().to_dict(),
        "split_version": args.split_version,
        "seed_string": args.seed_string,
        "train_frac": args.train_frac,
        "val_frac": args.val_frac,
    }
    summary_path = args.summary_json or args.output_csv.with_suffix(".summary.json")
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2))

    print(json.dumps(summary, indent=2))
    print(f"[written] {args.output_csv.resolve()}")
    print(f"[written] {summary_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
