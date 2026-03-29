#!/usr/bin/env python3
"""Aggregate clip-level EchoPrime embeddings to study-level vectors.

Supports mean pooling (default) and max pooling. Attention-based aggregation
is deferred to the trainable fusion model in Phase 5.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Aggregate clip embeddings to study-level.")
    parser.add_argument("--embedding-npz", type=Path, required=True, help="Clip-level embedding NPZ")
    parser.add_argument("--embedding-manifest", type=Path, required=True, help="Clip-level manifest CSV")
    parser.add_argument("--output-npz", type=Path, required=True, help="Study-level embedding NPZ")
    parser.add_argument("--output-manifest", type=Path, required=True, help="Study-level manifest CSV")
    parser.add_argument(
        "--method",
        choices=["mean", "max"],
        default="mean",
        help="Aggregation method (default: mean).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    with np.load(args.embedding_npz) as data:
        clip_embs = data["embeddings"]  # (N_clips, D)
    clip_df = pd.read_csv(args.embedding_manifest)

    ok_mask = clip_df["write_ok"].fillna(False).astype(bool)
    clip_df = clip_df[ok_mask].reset_index(drop=True)
    clip_embs = clip_embs[ok_mask.values[: len(clip_embs)]]

    if len(clip_df) != clip_embs.shape[0]:
        raise ValueError(
            f"Manifest rows ({len(clip_df)}) != embedding rows ({clip_embs.shape[0]})"
        )

    emb_dim = clip_embs.shape[1]
    study_ids = clip_df["study_id"].values
    unique_studies = np.unique(study_ids)

    study_rows: list[dict] = []
    study_embs: list[np.ndarray] = []

    for study_id in unique_studies:
        mask = study_ids == study_id
        group_embs = clip_embs[mask]
        group_df = clip_df[mask]

        if args.method == "mean":
            agg = group_embs.mean(axis=0)
        elif args.method == "max":
            agg = group_embs.max(axis=0)
        else:
            raise ValueError(f"Unknown method: {args.method}")

        study_embs.append(agg.astype(np.float32))
        study_rows.append({
            "study_idx": len(study_embs) - 1,
            "study_id": int(study_id),
            "subject_id": int(group_df["subject_id"].iloc[0]),
            "n_clips": int(mask.sum()),
            "embedding_l2_norm": float(np.linalg.norm(agg)),
        })

    study_arr = np.stack(study_embs, axis=0).astype(np.float32)  # (N_studies, D)
    study_manifest = pd.DataFrame(study_rows)

    args.output_npz.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(args.output_npz, embeddings=study_arr)

    args.output_manifest.parent.mkdir(parents=True, exist_ok=True)
    study_manifest.to_csv(args.output_manifest, index=False)

    summary = {
        "method": args.method,
        "n_input_clips": int(len(clip_df)),
        "n_output_studies": int(len(study_manifest)),
        "embedding_dim": int(emb_dim),
        "clips_per_study_min": int(study_manifest["n_clips"].min()),
        "clips_per_study_median": float(study_manifest["n_clips"].median()),
        "clips_per_study_max": int(study_manifest["n_clips"].max()),
        "clips_per_study_mean": round(float(study_manifest["n_clips"].mean()), 1),
    }
    summary_path = args.output_manifest.with_suffix(".summary.json")
    summary_path.write_text(json.dumps(summary, indent=2))

    print(json.dumps(summary, indent=2))
    print(f"[written] {args.output_npz.resolve()}")
    print(f"[written] {args.output_manifest.resolve()}")
    print(f"[written] {summary_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
