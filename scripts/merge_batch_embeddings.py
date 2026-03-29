#!/usr/bin/env python3
"""Merge per-batch clip embedding NPZs and manifests into a single file.

Also handles merging with previously-computed embeddings (e.g., the 500-study
Stage D results) to produce a unified full-scale embedding store.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Merge batch embedding results.")
    parser.add_argument(
        "--batch-dirs", type=Path, nargs="+", required=True,
        help="Directories containing per-batch clip_embeddings_512.npz and clip_embedding_manifest.csv",
    )
    parser.add_argument(
        "--output-npz", type=Path, required=True,
        help="Output merged embedding NPZ.",
    )
    parser.add_argument(
        "--output-manifest", type=Path, required=True,
        help="Output merged manifest CSV.",
    )
    parser.add_argument(
        "--npz-filename", default="clip_embeddings_512.npz",
        help="Name of the NPZ file within each batch dir.",
    )
    parser.add_argument(
        "--manifest-filename", default="clip_embedding_manifest.csv",
        help="Name of the manifest CSV within each batch dir.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    all_embs: list[np.ndarray] = []
    all_dfs: list[pd.DataFrame] = []
    batch_summaries: list[dict] = []
    running_idx = 0

    for batch_dir in args.batch_dirs:
        npz_path = batch_dir / args.npz_filename
        csv_path = batch_dir / args.manifest_filename

        if not npz_path.exists():
            print(f"[warn] Skipping {batch_dir}: missing {args.npz_filename}")
            continue
        if not csv_path.exists():
            print(f"[warn] Skipping {batch_dir}: missing {args.manifest_filename}")
            continue

        with np.load(npz_path) as data:
            emb = data["embeddings"]
        df = pd.read_csv(csv_path)

        if "write_ok" in df.columns:
            ok_mask = df["write_ok"].fillna(False).astype(bool)
            df = df[ok_mask].reset_index(drop=True)
            emb = emb[:len(df)]

        df = df.copy()
        df["embedding_idx"] = np.arange(running_idx, running_idx + len(df), dtype=int)
        running_idx += len(df)

        all_embs.append(emb)
        all_dfs.append(df)

        batch_summaries.append({
            "batch_dir": str(batch_dir),
            "n_clips": int(len(df)),
            "n_studies": int(df["study_id"].nunique()),
            "embedding_dim": int(emb.shape[1]) if emb.ndim == 2 else 0,
        })
        print(f"[info] Loaded {len(df)} clips from {batch_dir.name}")

    if not all_embs:
        raise RuntimeError("No valid batch directories found.")

    merged_emb = np.concatenate(all_embs, axis=0).astype(np.float32)
    merged_df = pd.concat(all_dfs, ignore_index=True)

    args.output_npz.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(args.output_npz, embeddings=merged_emb)

    args.output_manifest.parent.mkdir(parents=True, exist_ok=True)
    merged_df.to_csv(args.output_manifest, index=False)

    summary = {
        "n_total_clips": int(len(merged_df)),
        "n_total_studies": int(merged_df["study_id"].nunique()),
        "n_total_subjects": int(merged_df["subject_id"].nunique()) if "subject_id" in merged_df.columns else None,
        "embedding_shape": list(merged_emb.shape),
        "n_batches_merged": len(batch_summaries),
        "batches": batch_summaries,
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
