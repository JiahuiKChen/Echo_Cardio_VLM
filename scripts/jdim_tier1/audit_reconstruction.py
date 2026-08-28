"""Restricted technical reconstruction pilot for exact encoder input frames."""
from __future__ import annotations

import hashlib
import html
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import cv2
import numpy as np
import pandas as pd
import pydicom

from extract_mimic_echo_cines import (
    crop_and_scale,
    mask_outside_ultrasound,
    normalize_pixels,
    temporal_sample,
)

from .audit import canonical_clip_source_row_sha256
from .safety import (
    Tier1BlockedError,
    assert_export_safe_frame,
    require_columns,
    require_restricted_destination,
    sha256_file,
    write_json,
    write_safe_csv,
)


BLOCKED_AUDIT_RECONSTRUCTION = "BLOCKED_AUDIT_RECONSTRUCTION"
BLOCKED_AUDIT_SOURCE_RESTORATION = "BLOCKED_AUDIT_SOURCE_RESTORATION"
MODEL_SOURCE_FRAME_POSITIONS = tuple(range(0, 32, 2))
DICOM_PATH_COLUMNS = ("dicom_filepath", "source_dicom_path", "dicom_path", "dicom_abs_path")
PROCESSED_PATH_COLUMNS = ("output_path", "processed_npz_path", "npz_path")


@dataclass(frozen=True)
class ReconstructionPilotResult:
    restricted_rows: pd.DataFrame
    safe_summary: dict[str, Any]
    output_root: Path


@dataclass(frozen=True)
class SourceAvailabilityResult:
    restricted_rows: pd.DataFrame
    restoration_rows: pd.DataFrame
    ready_linkage: pd.DataFrame
    safe_summary: dict[str, Any]
    output_root: Path


def model_seen_frames(frames: np.ndarray) -> np.ndarray:
    if frames.ndim != 4 or frames.shape[-1] != 3:
        raise ValueError(f"processed frames must have shape (T,H,W,3), got {frames.shape}")
    if frames.shape[0] < 32:
        raise ValueError(f"processed input contains {frames.shape[0]} frames; expected at least 32")
    selected = frames[:32:2]
    if selected.shape[0] != 16:
        raise ValueError("encoder frame selection did not produce 16 frames")
    return selected


def _array_sha256(array: np.ndarray) -> str:
    contiguous = np.ascontiguousarray(array)
    digest = hashlib.sha256()
    digest.update(
        json.dumps(
            {"shape": list(contiguous.shape), "dtype": str(contiguous.dtype)},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    digest.update(contiguous.tobytes(order="C"))
    return digest.hexdigest()


def _text(value: Any) -> str:
    if pd.isna(value):
        return ""
    return str(value).strip()


def _stable_id(value: Any) -> str:
    text = _text(value)
    try:
        numeric = float(text)
    except ValueError:
        return text
    return str(int(numeric)) if np.isfinite(numeric) and numeric.is_integer() else text


def _first_path(row: pd.Series, columns: Sequence[str], label: str) -> str:
    for column in columns:
        if column in row.index and _text(row[column]):
            return _text(row[column])
    raise Tier1BlockedError(BLOCKED_AUDIT_RECONSTRUCTION, f"clip row lacks {label} path provenance")


def _resolve_path(raw: str, base: Path) -> Path:
    path = Path(raw).expanduser()
    return path.resolve() if path.is_absolute() else (base / path).resolve()


def _successful_manifest(frame: pd.DataFrame) -> pd.DataFrame:
    require_columns(frame, ["study_id", "subject_id"], "canonical clip manifest")
    work = frame.copy()
    if "write_ok" in work.columns:
        values = work["write_ok"]
        if pd.api.types.is_bool_dtype(values):
            keep = values.fillna(False).astype(bool)
        else:
            keep = values.astype(str).str.strip().str.lower().isin({"true", "1", "yes", "y"})
        work = work[keep].copy()
    work["study_id"] = work["study_id"].astype(str)
    work["subject_id"] = work["subject_id"].astype(str)
    work["_study"] = work["study_id"].map(_stable_id)
    work["_subject"] = work["subject_id"].map(_stable_id)
    if work[["_study", "_subject"]].eq("").any().any():
        raise Tier1BlockedError(BLOCKED_AUDIT_RECONSTRUCTION, "clip manifest has missing identifiers")
    conflicts = work.groupby("_study")["_subject"].nunique()
    if int((conflicts > 1).sum()):
        raise Tier1BlockedError(BLOCKED_AUDIT_RECONSTRUCTION, "study-to-subject mapping conflicts")
    order_columns = [
        column
        for column in ("canonical_clip_id", "embedding_idx", "source_embedding_idx")
        if column in work.columns
    ]
    if not order_columns:
        raise Tier1BlockedError(BLOCKED_AUDIT_RECONSTRUCTION, "clip manifest lacks a stable clip order")
    work["source_manifest_row"] = np.arange(len(work), dtype=int)
    return work.sort_values(["_study", *order_columns], kind="mergesort").reset_index(drop=True)


def _existing_source_path(raw: str, dicom_data_root: Path) -> Path:
    candidate = _resolve_path(raw, dicom_data_root)
    if candidate.is_file():
        return candidate
    rooted = (dicom_data_root / raw.lstrip("/")).resolve()
    return rooted if rooted.is_file() else candidate


def assess_audit_source_availability(
    audit_linkage_csv: Path,
    clip_roster_csv: Path,
    clip_manifest_csv: Path,
    dicom_data_root: Path,
    output_root: Path,
    safe_output_dir: Path,
    max_ready_pilot_studies: int = 6,
) -> SourceAvailabilityResult:
    """Assess every locked roster study without changing the sampling design."""

    for path in (
        audit_linkage_csv,
        clip_roster_csv,
        clip_manifest_csv,
        dicom_data_root,
        output_root,
        safe_output_dir,
    ):
        require_restricted_destination(path)
    if max_ready_pilot_studies < 1:
        raise ValueError("max_ready_pilot_studies must be positive")
    if output_root.exists() or safe_output_dir.exists():
        raise FileExistsError("refusing to overwrite source-availability outputs")

    linkage = pd.read_csv(audit_linkage_csv)
    require_columns(linkage, ["audit_id", "study_id", "subject_id", "review_order"], "audit linkage")
    if linkage["audit_id"].astype(str).duplicated().any():
        raise Tier1BlockedError(BLOCKED_AUDIT_RECONSTRUCTION, "audit linkage contains duplicate audit IDs")
    linkage["_study"] = linkage["study_id"].map(_stable_id)
    linkage["_subject"] = linkage["subject_id"].map(_stable_id)

    roster = pd.read_csv(clip_roster_csv)
    require_columns(
        roster,
        [
            "audit_id",
            "clip_audit_id",
            "study_id",
            "subject_id",
            "source_manifest_row",
            "source_manifest_row_sha256",
        ],
        "locked canonical clip roster",
    )
    roster["_study"] = roster["study_id"].map(_stable_id)
    roster["_subject"] = roster["subject_id"].map(_stable_id)
    roster["source_manifest_row"] = pd.to_numeric(roster["source_manifest_row"], errors="coerce")
    if roster["source_manifest_row"].isna().any() or not np.all(
        roster["source_manifest_row"] == np.floor(roster["source_manifest_row"])
    ):
        raise Tier1BlockedError(BLOCKED_AUDIT_RECONSTRUCTION, "clip roster has invalid source rows")
    roster["source_manifest_row"] = roster["source_manifest_row"].astype(int)

    manifest = _successful_manifest(pd.read_csv(clip_manifest_csv))
    manifest_base = clip_manifest_csv.parent
    clip_rows: list[dict[str, Any]] = []
    restoration_rows: list[dict[str, Any]] = []
    for _, roster_row in roster.iterrows():
        matches = manifest[manifest["source_manifest_row"].eq(int(roster_row["source_manifest_row"]))]
        unique_linkage = len(matches) == 1
        raw_dicom = ""
        raw_npz = ""
        source_exists = False
        processed_exists = False
        linkage_error = ""
        if unique_linkage:
            clip = matches.iloc[0]
            unique_linkage = (
                clip["_study"] == str(roster_row["_study"])
                and clip["_subject"] == str(roster_row["_subject"])
                and canonical_clip_source_row_sha256(clip)
                == str(roster_row["source_manifest_row_sha256"]).strip().lower()
            )
            if unique_linkage:
                try:
                    raw_dicom = _first_path(clip, DICOM_PATH_COLUMNS, "source DICOM")
                    raw_npz = _first_path(clip, PROCESSED_PATH_COLUMNS, "processed NPZ")
                    source_exists = _existing_source_path(raw_dicom, dicom_data_root).is_file()
                    processed_exists = _resolve_path(raw_npz, manifest_base).is_file()
                except Tier1BlockedError as exc:
                    linkage_error = exc.detail
                    unique_linkage = False
            else:
                linkage_error = "locked source-row identity or fingerprint mismatch"
        else:
            linkage_error = "locked source row did not map one-to-one"
        record = {
            "audit_id": str(roster_row["audit_id"]),
            "clip_audit_id": str(roster_row["clip_audit_id"]),
            "study_id": roster_row["study_id"],
            "subject_id": roster_row["subject_id"],
            "unique_linkage": bool(unique_linkage),
            "processed_input_available": bool(processed_exists),
            "source_dicom_available": bool(source_exists),
            "technically_ready": bool(unique_linkage and processed_exists and source_exists),
            "linkage_error": linkage_error,
        }
        clip_rows.append(record)
        if not record["technically_ready"]:
            restoration_rows.append(
                {
                    **record,
                    "declared_source_dicom": raw_dicom,
                    "declared_processed_input": raw_npz,
                    "restoration_need": ";".join(
                        item
                        for item, needed in (
                            ("unique_linkage", not unique_linkage),
                            ("processed_input", not processed_exists),
                            ("source_dicom", not source_exists),
                        )
                        if needed
                    ),
                }
            )

    clip_availability = pd.DataFrame(clip_rows)
    if set(clip_availability["audit_id"].astype(str)) != set(linkage["audit_id"].astype(str)):
        raise Tier1BlockedError(
            BLOCKED_AUDIT_RECONSTRUCTION,
            "locked roster and linkage do not contain the same physical studies",
        )
    study_availability = (
        clip_availability.groupby("audit_id", sort=False)
        .agg(
            study_id=("study_id", "first"),
            subject_id=("subject_id", "first"),
            canonical_clip_count=("clip_audit_id", "size"),
            unique_linkage=("unique_linkage", "all"),
            processed_input_available=("processed_input_available", "all"),
            source_dicom_available=("source_dicom_available", "all"),
            technically_ready=("technically_ready", "all"),
        )
        .reset_index()
    )
    ready_ids = set(
        study_availability.loc[study_availability["technically_ready"], "audit_id"].astype(str)
    )
    ready_linkage = (
        linkage.loc[linkage["audit_id"].astype(str).isin(ready_ids)]
        .sort_values(["review_order", "audit_id"], kind="mergesort")
        .head(max_ready_pilot_studies)
        .drop(columns=["_study", "_subject"])
    )
    restoration = pd.DataFrame(restoration_rows)
    locked_studies = int(len(study_availability))
    technically_ready = int(study_availability["technically_ready"].sum())
    summary = {
        "status": "ok" if technically_ready else BLOCKED_AUDIT_SOURCE_RESTORATION,
        "locked_roster_studies": locked_studies,
        "locked_roster_clips": int(len(clip_availability)),
        "studies_with_stored_processed_inputs_available": int(
            study_availability["processed_input_available"].sum()
        ),
        "studies_with_source_dicoms_available": int(
            study_availability["source_dicom_available"].sum()
        ),
        "studies_requiring_secure_restoration": int(
            (~study_availability["technically_ready"]).sum()
        ),
        "studies_lacking_unique_linkage": int((~study_availability["unique_linkage"]).sum()),
        "studies_technically_ready_for_reconstruction": technically_ready,
        "pilot_ready_subset_studies": int(len(ready_linkage)),
        "audit_linkage_sha256": sha256_file(audit_linkage_csv),
        "canonical_clip_roster_sha256": sha256_file(clip_roster_csv),
        "canonical_clip_manifest_sha256": sha256_file(clip_manifest_csv),
        "full_locked_roster_assessed": True,
        "roster_modified": False,
        "ocr_used": False,
        "clinical_content_annotations_recorded": False,
    }
    assert_export_safe_frame(pd.DataFrame([summary]), "source availability summary")

    output_root.mkdir(parents=True)
    clip_availability.to_csv(output_root / "source_availability_clip_rows.csv", index=False)
    study_availability.to_csv(output_root / "source_availability_study_rows.csv", index=False)
    restoration.to_csv(output_root / "source_restoration_manifest.csv", index=False)
    ready_linkage.to_csv(output_root / "technically_ready_pilot_linkage.csv", index=False)
    write_json(output_root / "source_availability_restricted_summary.json", summary)
    safe_output_dir.mkdir(parents=True)
    write_json(safe_output_dir / "source_availability_summary.json", summary)
    write_safe_csv(
        safe_output_dir / "source_availability_summary.csv",
        pd.DataFrame([summary]),
        "source availability summary",
    )
    return SourceAvailabilityResult(
        restricted_rows=study_availability,
        restoration_rows=restoration,
        ready_linkage=ready_linkage,
        safe_summary=summary,
        output_root=output_root,
    )


def _display_rgb(frame: np.ndarray, size: int = 224) -> np.ndarray:
    image = np.asarray(frame)
    if image.ndim == 2:
        image = np.repeat(image[..., None], 3, axis=2)
    if image.ndim != 3 or image.shape[-1] != 3:
        raise ValueError(f"display frame has unsupported shape {image.shape}")
    if image.dtype != np.uint8:
        finite = np.nan_to_num(image.astype(np.float32), nan=0.0, posinf=255.0, neginf=0.0)
        image = np.clip(finite, 0, 255).astype(np.uint8)
    return cv2.resize(image, (size, size), interpolation=cv2.INTER_AREA)


def build_contact_sheet(source_frames: np.ndarray, processed_frames: np.ndarray) -> np.ndarray:
    if len(source_frames) != 16 or len(processed_frames) != 16:
        raise ValueError("contact sheet requires exactly 16 source/processed frame pairs")
    tile_height = 254
    tile_width = 448
    canvas = np.full((tile_height * 4, tile_width * 4, 3), 255, dtype=np.uint8)
    for index, (source, processed) in enumerate(zip(source_frames, processed_frames)):
        row, column = divmod(index, 4)
        tile = np.zeros((tile_height, tile_width, 3), dtype=np.uint8)
        tile[:224, :224] = _display_rgb(source)
        tile[:224, 224:] = _display_rgb(processed)
        cv2.putText(tile, f"source {index + 1:02d}", (5, 246), cv2.FONT_HERSHEY_SIMPLEX, 0.48, (255, 255, 255), 1)
        cv2.putText(tile, f"processed {index + 1:02d}", (232, 246), cv2.FONT_HERSHEY_SIMPLEX, 0.48, (255, 255, 255), 1)
        y0, x0 = row * tile_height, column * tile_width
        canvas[y0 : y0 + tile_height, x0 : x0 + tile_width] = tile
    return canvas


def _replay_source(
    dicom_path: Path,
    processed_frames: np.ndarray,
    stored_sampled_indices: np.ndarray,
    stored_source_num_frames: np.ndarray,
) -> tuple[np.ndarray, bool, bool]:
    dataset = pydicom.dcmread(str(dicom_path), stop_before_pixels=False)
    raw_frames = normalize_pixels(dataset)
    if raw_frames.shape[0] <= 1:
        raise ValueError("source DICOM is not multiframe")
    sampled_from_npz = np.asarray(stored_sampled_indices).reshape(-1)
    if len(sampled_from_npz) != 32:
        raise ValueError("processed NPZ sampled_indices must contain exactly 32 entries")
    if not np.issubdtype(sampled_from_npz.dtype, np.integer):
        if not np.all(np.isfinite(sampled_from_npz)) or not np.all(
            sampled_from_npz == np.floor(sampled_from_npz)
        ):
            raise ValueError("processed NPZ sampled_indices are not integer-valued")
    sampled_from_npz = sampled_from_npz.astype(int)
    if (sampled_from_npz < 0).any() or (sampled_from_npz >= len(raw_frames)).any():
        raise ValueError("processed NPZ sampled_indices are outside the source frame range")
    source_count = np.asarray(stored_source_num_frames).reshape(-1)
    if len(source_count) != 1 or int(source_count[0]) != len(raw_frames):
        raise ValueError("processed NPZ source_num_frames does not match the source DICOM")
    masked = mask_outside_ultrasound(raw_frames)
    resized = np.stack([crop_and_scale(frame, 224) for frame in masked], axis=0).astype(np.uint8)
    replayed, replayed_indices = temporal_sample(resized, 32)
    source_selected = raw_frames[sampled_from_npz[list(MODEL_SOURCE_FRAME_POSITIONS)]]
    exact_replay = bool(np.array_equal(replayed, processed_frames[:32]))
    order_verified = bool(
        exact_replay
        and np.array_equal(sampled_from_npz, replayed_indices)
        and len(source_selected) == 16
    )
    return source_selected, exact_replay, order_verified


def build_reconstruction_pilot(
    audit_linkage_csv: Path,
    clip_roster_csv: Path,
    clip_manifest_csv: Path,
    dicom_data_root: Path,
    output_root: Path,
    safe_output_dir: Path,
    max_studies: int = 6,
    max_clips_per_study: int = 3,
    failure_rate_stop: float = 0.05,
) -> ReconstructionPilotResult:
    for path in (
        audit_linkage_csv,
        clip_roster_csv,
        clip_manifest_csv,
        dicom_data_root,
        output_root,
        safe_output_dir,
    ):
        require_restricted_destination(path)
    if max_studies < 1 or max_clips_per_study < 1:
        raise ValueError("max_studies and max_clips_per_study must be positive")
    if not 0 <= failure_rate_stop <= 1:
        raise ValueError("failure_rate_stop must be between 0 and 1")
    if output_root.exists():
        raise FileExistsError(f"refusing to overwrite reconstruction output root: {output_root}")
    safe_files = (
        safe_output_dir / "reconstruction_pilot_summary.csv",
        safe_output_dir / "reconstruction_pilot_summary.json",
    )
    if any(path.exists() for path in safe_files):
        raise FileExistsError("refusing to overwrite reconstruction aggregate-safe outputs")
    if not dicom_data_root.is_dir():
        raise FileNotFoundError(f"missing DICOM data root: {dicom_data_root}")

    linkage = pd.read_csv(audit_linkage_csv)
    require_columns(linkage, ["audit_id", "study_id", "subject_id", "review_order"], "audit linkage")
    if linkage["audit_id"].duplicated().any():
        raise Tier1BlockedError(BLOCKED_AUDIT_RECONSTRUCTION, "audit linkage contains duplicate audit IDs")
    linkage["_study"] = linkage["study_id"].map(_stable_id)
    linkage["_subject"] = linkage["subject_id"].map(_stable_id)
    selected_linkage = linkage.sort_values(["review_order", "audit_id"], kind="mergesort").head(max_studies)

    roster = pd.read_csv(clip_roster_csv)
    require_columns(
        roster,
        [
            "audit_id",
            "clip_audit_id",
            "study_id",
            "subject_id",
            "source_manifest_row",
            "source_manifest_row_sha256",
        ],
        "locked canonical clip roster",
    )
    if roster[["audit_id", "clip_audit_id"]].astype(str).duplicated().any():
        raise Tier1BlockedError(BLOCKED_AUDIT_RECONSTRUCTION, "clip roster contains duplicate opaque clip IDs")
    roster["_study"] = roster["study_id"].map(_stable_id)
    roster["_subject"] = roster["subject_id"].map(_stable_id)
    roster["source_manifest_row"] = pd.to_numeric(
        roster["source_manifest_row"], errors="coerce"
    )
    if roster["source_manifest_row"].isna().any() or not np.all(
        roster["source_manifest_row"] == np.floor(roster["source_manifest_row"])
    ):
        raise Tier1BlockedError(BLOCKED_AUDIT_RECONSTRUCTION, "clip roster has invalid source row ordinals")
    roster["source_manifest_row"] = roster["source_manifest_row"].astype(int)

    manifest = _successful_manifest(pd.read_csv(clip_manifest_csv))
    manifest_base = clip_manifest_csv.parent
    output_root.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output_root.name}.tmp-", dir=output_root.parent))
    records: list[dict[str, Any]] = []
    html_rows: list[str] = []
    total_canonical_clips = 0
    try:
        for _, linkage_row in selected_linkage.iterrows():
            audit_id = str(linkage_row["audit_id"])
            study = str(linkage_row["_study"])
            subject = str(linkage_row["_subject"])
            roster_rows = roster[roster["audit_id"].astype(str).eq(audit_id)].sort_values(
                ["source_manifest_row", "clip_audit_id"], kind="mergesort"
            )
            if roster_rows.empty:
                records.append(
                    {
                        "audit_id": audit_id,
                        "clip_audit_id": "",
                        "reconstruction_success": "no",
                        "source_processed_exact_match": "no",
                        "frame_order_verified": "no",
                        "error": "no canonical clips found for sampled study",
                        "contact_sheet": "",
                    }
                )
                continue
            if set(roster_rows["_study"]) != {study} or set(roster_rows["_subject"]) != {subject}:
                raise Tier1BlockedError(
                    BLOCKED_AUDIT_RECONSTRUCTION,
                    "sampled study identity does not match the locked clip roster",
                )
            total_canonical_clips += int(len(roster_rows))
            for _, roster_row in roster_rows.head(max_clips_per_study).iterrows():
                matches = manifest[
                    manifest["source_manifest_row"].eq(int(roster_row["source_manifest_row"]))
                ]
                if len(matches) != 1:
                    raise Tier1BlockedError(
                        BLOCKED_AUDIT_RECONSTRUCTION,
                        "locked clip roster does not map one-to-one to the canonical clip manifest",
                    )
                clip = matches.iloc[0]
                if clip["_study"] != study or clip["_subject"] != subject:
                    raise Tier1BlockedError(
                        BLOCKED_AUDIT_RECONSTRUCTION,
                        "locked clip roster source row has changed identity",
                    )
                declared_row_hash = str(roster_row["source_manifest_row_sha256"]).strip().lower()
                observed_row_hash = canonical_clip_source_row_sha256(clip)
                if declared_row_hash != observed_row_hash:
                    raise Tier1BlockedError(
                        BLOCKED_AUDIT_RECONSTRUCTION,
                        "locked clip roster source-row fingerprint does not match the canonical clip manifest",
                    )
                clip_id = str(roster_row["clip_audit_id"])
                relative_png = Path(audit_id) / f"{clip_id}.png"
                output_png = temporary / relative_png
                output_png.parent.mkdir(parents=True, exist_ok=True)
                record = {
                    "audit_id": audit_id,
                    "clip_audit_id": clip_id,
                    "reconstruction_success": "no",
                    "source_processed_exact_match": "no",
                    "frame_order_verified": "no",
                    "error": "",
                    "contact_sheet": str(relative_png),
                    "source_dicom_sha256": "",
                    "processed_npz_sha256": "",
                    "sampled_indices_sha256": "",
                    "model_seen_source_frames_sha256": "",
                    "model_seen_processed_frames_sha256": "",
                }
                try:
                    raw_dicom = _first_path(clip, DICOM_PATH_COLUMNS, "source DICOM")
                    raw_npz = _first_path(clip, PROCESSED_PATH_COLUMNS, "processed NPZ")
                    dicom_path = _resolve_path(raw_dicom, dicom_data_root)
                    if not dicom_path.is_file():
                        rooted_candidate = (dicom_data_root / raw_dicom.lstrip("/")).resolve()
                        if rooted_candidate.is_file():
                            dicom_path = rooted_candidate
                    npz_path = _resolve_path(raw_npz, manifest_base)
                    with np.load(npz_path, allow_pickle=False) as archive:
                        if "frames" not in archive:
                            raise KeyError("processed NPZ lacks frames")
                        if "sampled_indices" not in archive:
                            raise KeyError("processed NPZ lacks sampled_indices")
                        if "source_num_frames" not in archive:
                            raise KeyError("processed NPZ lacks source_num_frames")
                        processed = np.asarray(archive["frames"])
                        stored_sampled_indices = np.asarray(archive["sampled_indices"])
                        stored_source_num_frames = np.asarray(archive["source_num_frames"])
                    encoder_frames = model_seen_frames(processed)
                    source_frames, exact_replay, order_verified = _replay_source(
                        dicom_path,
                        processed,
                        stored_sampled_indices,
                        stored_source_num_frames,
                    )
                    sheet = build_contact_sheet(source_frames, encoder_frames)
                    if not cv2.imwrite(str(output_png), cv2.cvtColor(sheet, cv2.COLOR_RGB2BGR)):
                        raise IOError("contact-sheet write failed")
                    reconstruction_success = exact_replay and order_verified
                    record.update(
                        {
                            "reconstruction_success": "yes" if reconstruction_success else "no",
                            "source_processed_exact_match": "yes" if exact_replay else "no",
                            "frame_order_verified": "yes" if order_verified else "no",
                            "error": "" if reconstruction_success else "source replay or frame order did not match",
                            "source_dicom_sha256": sha256_file(dicom_path),
                            "processed_npz_sha256": sha256_file(npz_path),
                            "sampled_indices_sha256": _array_sha256(stored_sampled_indices),
                            "model_seen_source_frames_sha256": _array_sha256(source_frames),
                            "model_seen_processed_frames_sha256": _array_sha256(encoder_frames),
                        }
                    )
                    html_rows.append(
                        f"<h3>{html.escape(audit_id)} / {html.escape(clip_id)}</h3>"
                        f"<img src=\"{html.escape(str(relative_png))}\" alt=\"opaque reconstruction contact sheet\">"
                    )
                except Exception as exc:
                    record["error"] = f"{type(exc).__name__}: {exc}"
                records.append(record)

        restricted_rows = pd.DataFrame(records)
        attempted = int(len(restricted_rows))
        successful = int(restricted_rows["reconstruction_success"].eq("yes").sum()) if attempted else 0
        exact = int(restricted_rows["source_processed_exact_match"].eq("yes").sum()) if attempted else 0
        ordered = int(restricted_rows["frame_order_verified"].eq("yes").sum()) if attempted else 0
        failures = attempted - successful
        failure_rate = float(failures / attempted) if attempted else 1.0
        safe_summary = {
            "status": "ok" if attempted and failure_rate <= failure_rate_stop else BLOCKED_AUDIT_RECONSTRUCTION,
            "pilot_studies_requested": int(min(max_studies, len(linkage))),
            "pilot_studies_with_rows": int(restricted_rows.loc[restricted_rows["clip_audit_id"].ne(""), "audit_id"].nunique()),
            "canonical_clip_burden_across_pilot_studies": int(total_canonical_clips),
            "clips_attempted": attempted,
            "clips_reconstructed": successful,
            "clips_with_exact_source_replay": exact,
            "clips_with_verified_frame_order": ordered,
            "reconstruction_failures": failures,
            "reconstruction_failure_rate": failure_rate,
            "failure_rate_stop": float(failure_rate_stop),
            "model_frame_selection": "first 32 processed frames, stride 2, yielding 16 frames",
            "clinical_content_annotations_recorded": False,
            "ocr_used": False,
            "excluded_from_prevalence_estimates": True,
            "audit_linkage_sha256": sha256_file(audit_linkage_csv),
            "canonical_clip_roster_sha256": sha256_file(clip_roster_csv),
            "canonical_clip_manifest_sha256": sha256_file(clip_manifest_csv),
        }
        assert_export_safe_frame(pd.DataFrame([safe_summary]), "reconstruction pilot summary")
        restricted_rows.to_csv(temporary / "reconstruction_pilot_rows.csv", index=False)
        (temporary / "index.html").write_text(
            "<!doctype html><meta charset=\"utf-8\"><title>JDIM technical reconstruction pilot</title>"
            "<style>body{font:14px Arial;margin:24px}img{max-width:100%;border:1px solid #777}</style>"
            "<h1>Technical reconstruction pilot</h1><p>No OCR or clinical-content annotation.</p>"
            + "".join(html_rows),
            encoding="utf-8",
        )
        write_json(temporary / "reconstruction_pilot_restricted_summary.json", safe_summary)
        os.replace(temporary, output_root)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise

    safe_output_dir.mkdir(parents=True, exist_ok=True)
    safe_frame = pd.DataFrame([safe_summary])
    write_safe_csv(
        safe_output_dir / "reconstruction_pilot_summary.csv",
        safe_frame,
        "reconstruction pilot summary",
    )
    write_json(safe_output_dir / "reconstruction_pilot_summary.json", safe_summary)
    return ReconstructionPilotResult(restricted_rows, safe_summary, output_root)
