#!/usr/bin/env python3
"""Aggregate EchoPrime clip embeddings into study embeddings by view policy.

This is a Phase 2 utility for imaging-only baselines. It can create all-clips
study embeddings or ECHOVIEW-filtered study embeddings for sensitivity analyses.
The output manifest is patient-level and must remain in approved restricted
storage.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


VIEW_POLICIES: dict[str, list[str]] = {
    "a4c_family": ["prob_a4c", "prob_a4c_lvocc_s", "prob_a4c_laocc"],
    "a4c_family_or_rvinf": ["prob_a4c", "prob_a4c_lvocc_s", "prob_a4c_laocc", "prob_rvinf"],
    "a5c": ["prob_a5c"],
    "other": ["prob_other"],
    "a5c_or_other": ["prob_a5c", "prob_other"],
    "all_clips": [],
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clip-embedding-npz", type=Path, required=True)
    parser.add_argument(
        "--joined-clip-manifest-csv",
        type=Path,
        required=True,
        help="Clip embedding manifest after ECHOVIEW join, or the raw clip manifest for all_clips.",
    )
    parser.add_argument("--output-npz", type=Path, required=True)
    parser.add_argument("--output-manifest", type=Path, required=True)
    parser.add_argument(
        "--view-policy",
        choices=sorted(VIEW_POLICIES),
        required=True,
        help="View policy used before study-level pooling.",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.70,
        help="ECHOVIEW probability threshold. Ignored for all_clips.",
    )
    parser.add_argument(
        "--pooling",
        choices=["mean", "max", "probability_weighted_mean", "topk_mean"],
        default="mean",
    )
    parser.add_argument("--top-k", type=int, default=8, help="Top clips per study for topk_mean pooling.")
    parser.add_argument(
        "--allow-repo-output-for-testing",
        action="store_true",
        help="Permit output paths inside the git worktree. Use only for synthetic tests.",
    )
    return parser.parse_args()


def load_embeddings(path: Path) -> np.ndarray:
    if not path.exists():
        raise FileNotFoundError(f"Missing clip embedding NPZ: {path}")
    with np.load(path) as data:
        if "embeddings" not in data:
            raise ValueError(f"{path} is missing an 'embeddings' array")
        embeddings = data["embeddings"].astype(np.float32)
    if embeddings.ndim != 2:
        raise ValueError(f"Expected a 2D embedding array, got shape {embeddings.shape}")
    return embeddings


def inside_current_worktree(path: Path) -> bool:
    resolved = path.resolve()
    cwd = Path.cwd().resolve()
    return resolved == cwd or cwd in resolved.parents


def guard_output_path(path: Path, allow_repo_output_for_testing: bool) -> None:
    if allow_repo_output_for_testing:
        return
    if inside_current_worktree(path):
        raise RuntimeError(
            f"Refusing to write patient-level embedding output inside the repo: {path}. "
            "Use an approved restricted output directory, or pass "
            "--allow-repo-output-for-testing only for synthetic tests."
        )


def resolve_embedding_index(manifest: pd.DataFrame, n_embeddings: int) -> tuple[pd.DataFrame, str]:
    out = manifest.copy()
    if "embedding_idx" in out.columns:
        idx_col = "embedding_idx"
    elif "clip_idx" in out.columns:
        idx_col = "clip_idx"
    else:
        if len(out) != n_embeddings:
            raise ValueError(
                "Manifest has no embedding index column and row count does not match embeddings: "
                f"{len(out)} rows vs {n_embeddings} embeddings"
            )
        out["_embedding_idx"] = np.arange(len(out), dtype=int)
        idx_col = "_embedding_idx"

    out[idx_col] = pd.to_numeric(out[idx_col], errors="coerce")
    valid = out[idx_col].notna() & (out[idx_col] >= 0) & (out[idx_col] < n_embeddings)
    if "write_ok" in out.columns:
        valid &= out["write_ok"].fillna(False).astype(bool)
    out = out[valid].copy()
    out[idx_col] = out[idx_col].astype(int)
    if out.empty:
        raise RuntimeError("No valid manifest rows after resolving embedding indices.")
    return out, idx_col


def coerce_id_columns(df: pd.DataFrame) -> pd.DataFrame:
    required = {"study_id", "subject_id"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Joined clip manifest missing required columns: {sorted(missing)}")
    out = df.copy()
    out["study_id"] = out["study_id"].astype(str)
    out["subject_id"] = out["subject_id"].astype(str)
    return out


def score_view_policy(df: pd.DataFrame, policy: str) -> pd.Series:
    cols = VIEW_POLICIES[policy]
    if not cols:
        return pd.Series(np.ones(len(df), dtype=np.float32), index=df.index)
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise ValueError(f"Manifest missing probability columns for {policy}: {missing}")
    scores = df[cols].apply(pd.to_numeric, errors="coerce").max(axis=1)
    return scores.astype(np.float32)


def filter_by_policy(df: pd.DataFrame, policy: str, threshold: float) -> tuple[pd.DataFrame, dict[str, Any]]:
    out = df.copy()
    out["view_policy_score"] = score_view_policy(out, policy)
    if policy == "all_clips":
        selected = out.copy()
    else:
        matched = out["echoview_matched"].fillna(False).astype(bool) if "echoview_matched" in out.columns else out[
            "view_policy_score"
        ].notna()
        selected = out[matched & out["view_policy_score"].notna() & (out["view_policy_score"] >= threshold)].copy()
    summary = {
        "view_policy": policy,
        "threshold": None if policy == "all_clips" else float(threshold),
        "n_valid_input_clips": int(len(df)),
        "n_selected_clips": int(len(selected)),
        "n_input_studies": int(df["study_id"].nunique()),
        "n_selected_studies": int(selected["study_id"].nunique()) if not selected.empty else 0,
        "n_input_subjects": int(df["subject_id"].nunique()),
        "n_selected_subjects": int(selected["subject_id"].nunique()) if not selected.empty else 0,
    }
    return selected, summary


def pool_embeddings(group: pd.DataFrame, embeddings: np.ndarray, idx_col: str, pooling: str, top_k: int) -> np.ndarray:
    idx = group[idx_col].to_numpy(dtype=int)
    group_embeddings = embeddings[idx]
    if pooling == "mean":
        pooled = group_embeddings.mean(axis=0)
    elif pooling == "max":
        pooled = group_embeddings.max(axis=0)
    elif pooling == "probability_weighted_mean":
        weights = pd.to_numeric(group["view_policy_score"], errors="coerce").fillna(0.0).to_numpy(dtype=np.float32)
        if np.allclose(weights.sum(), 0.0):
            pooled = group_embeddings.mean(axis=0)
        else:
            pooled = np.average(group_embeddings, axis=0, weights=weights)
    elif pooling == "topk_mean":
        if "view_policy_score" in group.columns and group["view_policy_score"].notna().any():
            order = np.argsort(-group["view_policy_score"].fillna(-np.inf).to_numpy(dtype=np.float32))
            keep = order[: max(1, min(top_k, len(order)))]
            pooled = group_embeddings[keep].mean(axis=0)
        else:
            pooled = group_embeddings.mean(axis=0)
    else:  # pragma: no cover - argparse prevents this
        raise ValueError(f"Unknown pooling: {pooling}")
    return pooled.astype(np.float32)


def summarize_selected(selected: pd.DataFrame) -> dict[str, Any]:
    if selected.empty:
        return {
            "selected_clips_per_study_min": None,
            "selected_clips_per_study_median": None,
            "selected_clips_per_study_max": None,
        }
    clips_per_study = selected.groupby("study_id").size()
    return {
        "selected_clips_per_study_min": int(clips_per_study.min()),
        "selected_clips_per_study_median": float(clips_per_study.median()),
        "selected_clips_per_study_max": int(clips_per_study.max()),
    }


def main() -> int:
    args = parse_args()
    try:
        guard_output_path(args.output_npz, args.allow_repo_output_for_testing)
        guard_output_path(args.output_manifest, args.allow_repo_output_for_testing)
    except RuntimeError as exc:
        print(json.dumps({"blocked_for_governance": True, "reason": str(exc)}, indent=2))
        return 2

    embeddings = load_embeddings(args.clip_embedding_npz)
    if not args.joined_clip_manifest_csv.exists():
        raise FileNotFoundError(f"Missing joined clip manifest: {args.joined_clip_manifest_csv}")
    manifest = pd.read_csv(args.joined_clip_manifest_csv)
    manifest = coerce_id_columns(manifest)
    manifest, idx_col = resolve_embedding_index(manifest, embeddings.shape[0])

    selected, selection_summary = filter_by_policy(manifest, args.view_policy, args.threshold)
    if selected.empty:
        raise RuntimeError(
            f"No clips selected for policy={args.view_policy} threshold={args.threshold}. "
            "Check the ECHOVIEW join and policy columns."
        )

    study_rows: list[dict[str, Any]] = []
    study_vectors: list[np.ndarray] = []
    for study_id, group in selected.groupby("study_id", sort=True):
        pooled = pool_embeddings(group, embeddings, idx_col, args.pooling, args.top_k)
        study_vectors.append(pooled)
        scores = pd.to_numeric(group["view_policy_score"], errors="coerce")
        study_rows.append(
            {
                "study_idx": len(study_vectors) - 1,
                "study_id": study_id,
                "subject_id": group["subject_id"].iloc[0],
                "n_selected_clips": int(len(group)),
                "view_policy": args.view_policy,
                "threshold": np.nan if args.view_policy == "all_clips" else float(args.threshold),
                "pooling": args.pooling,
                "view_policy_score_max": float(scores.max()) if scores.notna().any() else np.nan,
                "view_policy_score_mean": float(scores.mean()) if scores.notna().any() else np.nan,
                "embedding_l2_norm": float(np.linalg.norm(pooled)),
            }
        )

    output_arr = np.stack(study_vectors, axis=0).astype(np.float32)
    output_manifest = pd.DataFrame(study_rows)

    args.output_npz.parent.mkdir(parents=True, exist_ok=True)
    args.output_manifest.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(args.output_npz, embeddings=output_arr)
    output_manifest.to_csv(args.output_manifest, index=False)

    summary = {
        "clip_embedding_npz": str(args.clip_embedding_npz),
        "joined_clip_manifest_csv": str(args.joined_clip_manifest_csv),
        "output_npz": str(args.output_npz),
        "output_manifest": str(args.output_manifest),
        "embedding_dim": int(output_arr.shape[1]),
        "pooling": args.pooling,
        "top_k": int(args.top_k),
        "embedding_index_column": idx_col,
        "patient_level_outputs_written": True,
        "patient_level_output_governance": "Keep NPZ and manifest in approved restricted storage; do not commit.",
        **selection_summary,
        **summarize_selected(selected),
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
