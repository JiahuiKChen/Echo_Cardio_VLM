#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pandas as pd


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Select one key frame per extracted echo clip and write PNG files + manifest."
    )
    parser.add_argument("--extraction-manifest", type=Path, required=True, help="Path to extraction_manifest.csv")
    parser.add_argument("--output-root", type=Path, required=True, help="Directory for key-frame PNG outputs")
    parser.add_argument("--output-manifest", type=Path, required=True, help="Path for key-frame manifest CSV")
    parser.add_argument(
        "--summary-json",
        type=Path,
        default=None,
        help="Optional summary JSON path. Defaults to <output-manifest>.summary.json",
    )
    parser.add_argument(
        "--method",
        choices=["middle", "max_focus", "max_motion", "min_motion", "combo_sharp_still"],
        default="combo_sharp_still",
        help="Frame selection strategy.",
    )
    parser.add_argument("--max-clips", type=int, default=0, help="Cap number of clips processed. 0 disables.")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing PNG outputs.")
    parser.add_argument("--progress-every", type=int, default=25, help="Print progress every N processed rows.")
    return parser.parse_args()


def as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    text = str(value).strip().lower()
    return text in {"1", "true", "yes", "y", "t"}


def as_gray(frame: np.ndarray) -> np.ndarray:
    if frame.ndim == 2:
        return frame.astype(np.float32)
    if frame.ndim == 3 and frame.shape[-1] == 1:
        return frame[..., 0].astype(np.float32)
    if frame.ndim == 3 and frame.shape[-1] == 3:
        return cv2.cvtColor(frame.astype(np.uint8), cv2.COLOR_BGR2GRAY).astype(np.float32)
    raise ValueError(f"Unsupported frame shape: {frame.shape}")


def zscore(x: np.ndarray) -> np.ndarray:
    std = float(np.std(x))
    if std < 1e-8:
        return np.zeros_like(x, dtype=np.float32)
    return ((x - float(np.mean(x))) / std).astype(np.float32)


def compute_scores(frames: np.ndarray) -> dict[str, np.ndarray]:
    n = frames.shape[0]
    gray_frames = [as_gray(frames[i]) for i in range(n)]

    focus = np.zeros(n, dtype=np.float32)
    intensity = np.zeros(n, dtype=np.float32)
    contrast = np.zeros(n, dtype=np.float32)
    for i, gray in enumerate(gray_frames):
        focus[i] = float(cv2.Laplacian(gray, cv2.CV_32F).var())
        intensity[i] = float(gray.mean())
        contrast[i] = float(gray.std())

    motion = np.zeros(n, dtype=np.float32)
    if n > 1:
        for i in range(1, n):
            motion[i] = float(np.mean(np.abs(gray_frames[i] - gray_frames[i - 1])))
        motion[0] = motion[1]

    return {
        "focus": focus,
        "intensity": intensity,
        "contrast": contrast,
        "motion": motion,
    }


def choose_index(scores: dict[str, np.ndarray], method: str) -> tuple[int, float]:
    focus = scores["focus"]
    motion = scores["motion"]
    contrast = scores["contrast"]
    n = len(focus)
    if n == 0:
        raise ValueError("Empty frame sequence.")

    if method == "middle":
        idx = n // 2
        score = float(focus[idx])
    elif method == "max_focus":
        idx = int(np.argmax(focus))
        score = float(focus[idx])
    elif method == "max_motion":
        idx = int(np.argmax(motion))
        score = float(motion[idx])
    elif method == "min_motion":
        idx = int(np.argmin(motion))
        score = float(motion[idx])
    elif method == "combo_sharp_still":
        combined = zscore(focus) + 0.5 * zscore(contrast) - 0.5 * zscore(motion)
        idx = int(np.argmax(combined))
        score = float(combined[idx])
    else:  # pragma: no cover
        raise ValueError(f"Unknown method: {method}")

    return idx, score


def load_frames(npz_path: Path) -> np.ndarray:
    with np.load(npz_path) as data:
        if "frames" not in data:
            raise KeyError("Missing 'frames' array in NPZ.")
        frames = data["frames"]
    if frames.ndim != 4:
        raise ValueError(f"Expected 4D frames array, got shape: {frames.shape}")
    return frames


def output_path_for(row: pd.Series, output_root: Path, selected_index: int) -> Path:
    rel = Path(str(row["dicom_filepath"]).lstrip("/")).with_suffix("")
    return output_root / rel.parent / f"{rel.name}_f{selected_index:03d}.png"


def process_row(row: pd.Series, output_root: Path, method: str, overwrite: bool) -> dict[str, Any]:
    result: dict[str, Any] = {
        "subject_id": int(row["subject_id"]),
        "study_id": int(row["study_id"]),
        "dicom_filepath": str(row["dicom_filepath"]),
        "npz_path": str(row["output_path"]),
        "keyframe_path": None,
        "method": method,
        "n_frames": None,
        "selected_index": None,
        "selected_ratio": None,
        "selection_score": None,
        "focus_score": None,
        "motion_score": None,
        "intensity_mean": None,
        "contrast_std": None,
        "write_ok": False,
        "error": None,
    }

    npz_path = Path(str(row["output_path"]))
    if not npz_path.exists():
        result["error"] = f"missing_npz: {npz_path}"
        return result

    try:
        frames = load_frames(npz_path)
        scores = compute_scores(frames)
        idx, selection_score = choose_index(scores, method=method)

        out_path = output_path_for(row, output_root=output_root, selected_index=idx)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if overwrite or not out_path.exists():
            frame = frames[idx]
            if frame.ndim == 3 and frame.shape[-1] == 3:
                ok = cv2.imwrite(str(out_path), frame.astype(np.uint8))
            elif frame.ndim == 2:
                ok = cv2.imwrite(str(out_path), frame.astype(np.uint8))
            else:
                raise ValueError(f"Unsupported selected frame shape: {frame.shape}")
            if not ok:
                raise RuntimeError(f"cv2.imwrite failed for {out_path}")

        result.update(
            {
                "keyframe_path": str(out_path.resolve()),
                "n_frames": int(frames.shape[0]),
                "selected_index": int(idx),
                "selected_ratio": float(idx / max(frames.shape[0] - 1, 1)),
                "selection_score": float(selection_score),
                "focus_score": float(scores["focus"][idx]),
                "motion_score": float(scores["motion"][idx]),
                "intensity_mean": float(scores["intensity"][idx]),
                "contrast_std": float(scores["contrast"][idx]),
                "write_ok": True,
            }
        )
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def summarize(manifest_df: pd.DataFrame, method: str, output_root: Path, output_manifest: Path) -> dict[str, Any]:
    success_df = manifest_df[manifest_df["write_ok"].fillna(False)]
    summary: dict[str, Any] = {
        "n_rows": int(len(manifest_df)),
        "n_success": int(len(success_df)),
        "n_failed": int(len(manifest_df) - len(success_df)),
        "n_subjects": int(success_df["subject_id"].nunique()) if not success_df.empty else 0,
        "n_studies": int(success_df["study_id"].nunique()) if not success_df.empty else 0,
        "method": method,
        "output_root": str(output_root.resolve()),
        "output_manifest": str(output_manifest.resolve()),
    }
    if not success_df.empty:
        summary.update(
            {
                "selected_index_min": int(success_df["selected_index"].min()),
                "selected_index_median": float(success_df["selected_index"].median()),
                "selected_index_max": int(success_df["selected_index"].max()),
                "focus_score_median": float(success_df["focus_score"].median()),
                "motion_score_median": float(success_df["motion_score"].median()),
            }
        )
    return summary


def main() -> int:
    args = parse_args()
    extraction_df = pd.read_csv(args.extraction_manifest)
    if "write_ok" not in extraction_df.columns:
        raise ValueError("Extraction manifest must include a 'write_ok' column.")

    eligible_df = extraction_df[extraction_df["write_ok"].apply(as_bool)].copy()
    eligible_df = eligible_df.sort_values(["study_id", "dicom_filepath"]).reset_index(drop=True)
    if args.max_clips > 0:
        eligible_df = eligible_df.head(args.max_clips).reset_index(drop=True)

    args.output_root.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, Any]] = []
    total = len(eligible_df)
    for i, (_, row) in enumerate(eligible_df.iterrows(), start=1):
        rows.append(
            process_row(
                row=row,
                output_root=args.output_root.resolve(),
                method=args.method,
                overwrite=args.overwrite,
            )
        )
        if args.progress_every > 0 and i % args.progress_every == 0:
            print(f"[info] processed {i}/{total}")

    out_df = pd.DataFrame(rows)
    args.output_manifest.parent.mkdir(parents=True, exist_ok=True)
    out_df.to_csv(args.output_manifest, index=False)

    summary = summarize(
        manifest_df=out_df,
        method=args.method,
        output_root=args.output_root,
        output_manifest=args.output_manifest,
    )
    summary_path = args.summary_json or args.output_manifest.with_suffix(".summary.json")
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2))

    print(json.dumps(summary, indent=2))
    print(f"[written] {args.output_manifest.resolve()}")
    print(f"[written] {summary_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
