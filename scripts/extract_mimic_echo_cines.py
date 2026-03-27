#!/usr/bin/env python3
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import json
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pandas as pd
import pydicom


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract normalized cine clips from audited MIMIC-IV-ECHO DICOMs.")
    parser.add_argument("--audit-csv", type=Path, required=True, help="Path to cine_candidates.csv or dicom_audit.csv")
    parser.add_argument("--data-root", type=Path, required=True, help="Root of the local MIMIC-IV-ECHO download")
    parser.add_argument("--output-root", type=Path, required=True, help="Directory for extracted clip files")
    parser.add_argument("--output-manifest", type=Path, required=True, help="Path to write extraction manifest CSV")
    parser.add_argument("--target-size", type=int, default=224, help="Output frame width/height")
    parser.add_argument("--target-frames", type=int, default=32, help="Number of frames to store per clip")
    parser.add_argument("--max-clips", type=int, default=0, help="Limit total extracted clips. 0 disables the cap.")
    parser.add_argument(
        "--max-clips-per-study",
        type=int,
        default=0,
        help="Limit extracted clips per study. 0 disables the cap.",
    )
    parser.add_argument("--num-workers", type=int, default=1, help="Parallel workers. Use >1 for faster extraction.")
    parser.add_argument(
        "--overwrite-existing",
        action="store_true",
        help="Re-extract clips even when output .npz already exists.",
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=200,
        help="Print progress every N clips. Set 0 to disable periodic progress logs.",
    )
    return parser.parse_args()


def crop_and_scale(img: np.ndarray, size: int, interpolation: int = cv2.INTER_CUBIC, zoom: float = 0.1) -> np.ndarray:
    res = (size, size)
    in_res = (img.shape[1], img.shape[0])
    r_in = in_res[0] / in_res[1]
    r_out = res[0] / res[1]

    if r_in > r_out:
        padding = int(round((in_res[0] - r_out * in_res[1]) / 2))
        img = img[:, padding:-padding]
    if r_in < r_out:
        padding = int(round((in_res[1] - in_res[0] / r_out) / 2))
        img = img[padding:-padding]
    if zoom != 0:
        pad_x = round(int(img.shape[1] * zoom))
        pad_y = round(int(img.shape[0] * zoom))
        img = img[pad_y:-pad_y, pad_x:-pad_x]

    return cv2.resize(img, res, interpolation=interpolation)


def normalize_pixels(ds: pydicom.Dataset) -> np.ndarray:
    pixels = ds.pixel_array

    if pixels.ndim == 4 and pixels.shape[-1] == 3:
        frames = pixels
    elif pixels.ndim == 3 and pixels.shape[-1] != 3:
        frames = np.repeat(pixels[..., None], 3, axis=3)
    elif pixels.ndim == 3 and pixels.shape[-1] == 3:
        frames = pixels[None, ...]
    elif pixels.ndim == 2:
        frames = np.repeat(pixels[None, ..., None], 3, axis=3)
    else:
        raise ValueError(f"Unsupported pixel array shape: {pixels.shape}")

    return frames.astype(np.uint8)


def mask_outside_ultrasound(original_pixels: np.ndarray) -> np.ndarray:
    if original_pixels.ndim != 4 or original_pixels.shape[-1] != 3:
        return original_pixels

    vid = np.copy(original_pixels)
    try:
        frame_sum = original_pixels[0].astype(np.float32)
        frame_sum = cv2.cvtColor(frame_sum, cv2.COLOR_YUV2RGB)
        frame_sum = cv2.cvtColor(frame_sum, cv2.COLOR_RGB2GRAY)
        frame_sum = np.where(frame_sum > 0, 1, 0)

        for i in range(original_pixels.shape[0]):
            frame = original_pixels[i].astype(np.uint8)
            frame = cv2.cvtColor(frame, cv2.COLOR_YUV2RGB)
            frame = cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY)
            frame = np.where(frame > 0, 1, 0)
            frame_sum = np.add(frame_sum, frame)

        kernel = np.ones((3, 3), np.uint8)
        frame_sum = cv2.erode(np.uint8(frame_sum), kernel, iterations=10)
        frame_sum = np.where(frame_sum > 0, 1, 0)

        frame0 = cv2.cvtColor(original_pixels[0].astype(np.uint8), cv2.COLOR_YUV2RGB)
        frame0 = cv2.cvtColor(frame0, cv2.COLOR_RGB2GRAY)
        frame_last = cv2.cvtColor(original_pixels[-1].astype(np.uint8), cv2.COLOR_YUV2RGB)
        frame_last = cv2.cvtColor(frame_last, cv2.COLOR_RGB2GRAY)
        frame_diff = abs(np.subtract(frame0, frame_last))
        frame_diff = np.where(frame_diff > 0, 1, 0)
        frame_diff[0:20, 0:20] = 0

        frame_overlap = np.add(frame_sum, frame_diff)
        frame_overlap = np.where(frame_overlap > 1, 1, 0)
        frame_overlap = cv2.dilate(np.uint8(frame_overlap), kernel, iterations=10).astype(np.uint8)
        cv2.floodFill(frame_overlap, None, (0, 0), 100)
        frame_overlap = np.where(frame_overlap != 100, 255, 0).astype(np.uint8)
        contours, _ = cv2.findContours(frame_overlap, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
        for contour in contours:
            hull = cv2.convexHull(contour)
            cv2.drawContours(frame_overlap, [hull], -1, (255, 0, 0), 3)
        frame_overlap = np.where(frame_overlap > 0, 1, 0).astype(np.uint8)
        cv2.floodFill(frame_overlap, None, (0, 0), 100)
        frame_overlap = np.array(np.where(frame_overlap != 100, 255, 0), dtype=bool)

        for i in range(len(vid)):
            frame = vid[i].astype(np.uint8)
            frame = cv2.cvtColor(frame, cv2.COLOR_YUV2BGR)
            frame = cv2.bitwise_and(frame, frame, mask=frame_overlap.astype(np.uint8))
            vid[i] = frame
        return vid
    except Exception:
        return vid


def temporal_sample(frames: np.ndarray, target_frames: int) -> tuple[np.ndarray, np.ndarray]:
    n_frames = frames.shape[0]
    if n_frames <= 0:
        raise ValueError("Cannot sample an empty frame sequence.")
    if n_frames >= target_frames:
        indices = np.linspace(0, n_frames - 1, num=target_frames, dtype=int)
    else:
        pad_count = target_frames - n_frames
        tail = np.full((pad_count,), n_frames - 1, dtype=int)
        indices = np.concatenate([np.arange(n_frames, dtype=int), tail])
    return frames[indices], indices


def pick_rows(df: pd.DataFrame, max_clips: int, max_clips_per_study: int) -> pd.DataFrame:
    out = df[df["read_ok"].fillna(False) & df["is_multiframe"].fillna(False)].copy()
    out = out.sort_values(["study_id", "dicom_filepath"]).reset_index(drop=True)
    if max_clips_per_study > 0:
        out = out.groupby("study_id", as_index=False, sort=False).head(max_clips_per_study)
    if max_clips > 0:
        out = out.head(max_clips)
    return out.reset_index(drop=True)


def extract_one(
    row: dict[str, Any],
    data_root: Path,
    output_root: Path,
    target_size: int,
    target_frames: int,
    overwrite_existing: bool,
) -> dict[str, Any]:
    relative_path = str(row["dicom_filepath"]).lstrip("/")
    dicom_path = data_root / relative_path
    output_path = output_root / Path(relative_path).with_suffix(".npz")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    result: dict[str, Any] = {
        "subject_id": int(row["subject_id"]),
        "study_id": int(row["study_id"]),
        "dicom_filepath": relative_path,
        "output_path": str(output_path.resolve()),
        "write_ok": False,
        "status": "failed",
        "error": None,
    }

    try:
        if output_path.exists() and not overwrite_existing:
            result.update(
                {
                    "write_ok": True,
                    "status": "skipped_existing",
                    "output_size_bytes": output_path.stat().st_size,
                }
            )
            return result

        ds = pydicom.dcmread(str(dicom_path), stop_before_pixels=False)
        raw_frames = normalize_pixels(ds)
        if raw_frames.shape[0] <= 1:
            raise ValueError("single-frame image")

        masked_frames = mask_outside_ultrasound(raw_frames)
        resized_frames = np.stack([crop_and_scale(frame, target_size) for frame in masked_frames], axis=0).astype(np.uint8)
        sampled_frames, sampled_indices = temporal_sample(resized_frames, target_frames)

        np.savez_compressed(
            output_path,
            frames=sampled_frames,
            sampled_indices=sampled_indices,
            source_num_frames=np.array([raw_frames.shape[0]], dtype=np.int32),
            source_rows=np.array([raw_frames.shape[1]], dtype=np.int32),
            source_columns=np.array([raw_frames.shape[2]], dtype=np.int32),
        )

        result.update(
            {
                "write_ok": True,
                "status": "written",
                "output_size_bytes": output_path.stat().st_size,
                "source_num_frames": int(raw_frames.shape[0]),
                "source_rows": int(raw_frames.shape[1]),
                "source_columns": int(raw_frames.shape[2]),
                "target_frames": int(target_frames),
                "target_size": int(target_size),
            }
        )
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def extract_one_star(
    row: dict[str, Any],
    data_root: str,
    output_root: str,
    target_size: int,
    target_frames: int,
    overwrite_existing: bool,
) -> dict[str, Any]:
    return extract_one(
        row=row,
        data_root=Path(data_root),
        output_root=Path(output_root),
        target_size=target_size,
        target_frames=target_frames,
        overwrite_existing=overwrite_existing,
    )


def main() -> int:
    args = parse_args()
    if args.num_workers < 1:
        raise ValueError("--num-workers must be >= 1")
    audit_df = pd.read_csv(args.audit_csv)
    rows = pick_rows(audit_df, max_clips=args.max_clips, max_clips_per_study=args.max_clips_per_study)
    output_root = args.output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)

    row_dicts = rows.to_dict(orient="records")
    data_root = str(args.data_root.resolve())
    output_root_str = str(output_root)
    total = len(row_dicts)
    print(
        json.dumps(
            {
                "event": "start_extraction",
                "requested_rows": total,
                "num_workers": int(args.num_workers),
                "overwrite_existing": bool(args.overwrite_existing),
                "output_root": output_root_str,
            }
        )
    )

    manifest_rows: list[dict[str, Any]] = []
    done = 0
    if args.num_workers == 1:
        for row in row_dicts:
            manifest_rows.append(
                extract_one_star(
                    row=row,
                    data_root=data_root,
                    output_root=output_root_str,
                    target_size=args.target_size,
                    target_frames=args.target_frames,
                    overwrite_existing=args.overwrite_existing,
                )
            )
            done += 1
            if args.progress_every > 0 and done % args.progress_every == 0:
                print(json.dumps({"event": "progress", "done": done, "total": total}))
    else:
        with ProcessPoolExecutor(max_workers=args.num_workers) as executor:
            futures = [
                executor.submit(
                    extract_one_star,
                    row,
                    data_root,
                    output_root_str,
                    args.target_size,
                    args.target_frames,
                    args.overwrite_existing,
                )
                for row in row_dicts
            ]
            for future in as_completed(futures):
                manifest_rows.append(future.result())
                done += 1
                if args.progress_every > 0 and done % args.progress_every == 0:
                    print(json.dumps({"event": "progress", "done": done, "total": total}))

    manifest_df = pd.DataFrame(manifest_rows)
    args.output_manifest.resolve().parent.mkdir(parents=True, exist_ok=True)
    manifest_df.to_csv(args.output_manifest.resolve(), index=False)

    success_df = manifest_df[manifest_df["write_ok"].fillna(False)]
    written_df = success_df[success_df["status"] == "written"] if "status" in success_df else success_df
    skipped_df = success_df[success_df["status"] == "skipped_existing"] if "status" in success_df else success_df.iloc[0:0]
    summary = {
        "requested_rows": int(len(rows)),
        "successful_writes": int(len(written_df)),
        "skipped_existing": int(len(skipped_df)),
        "successful_total": int(len(success_df)),
        "failed_writes": int(len(manifest_df) - len(success_df)),
        "n_studies": int(success_df["study_id"].nunique()) if not success_df.empty else 0,
        "total_output_size_bytes": int(success_df["output_size_bytes"].sum()) if "output_size_bytes" in success_df else 0,
        "output_root": str(output_root),
        "output_manifest": str(args.output_manifest.resolve()),
        "num_workers": int(args.num_workers),
        "overwrite_existing": bool(args.overwrite_existing),
    }
    summary_path = args.output_manifest.resolve().with_suffix(".summary.json")
    summary_path.write_text(json.dumps(summary, indent=2))

    print(json.dumps(summary, indent=2))
    print(f"[written] {args.output_manifest.resolve()}")
    print(f"[written] {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
