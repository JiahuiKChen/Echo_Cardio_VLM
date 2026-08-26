"""Fail-closed primitives for deferred duplicate-input recovery.

The functions in this module support later scheduled verification. Nothing is
executed at import time, and callers must provide explicit recovered inputs,
historical vectors, and a verified frozen encoder callable.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import numpy as np
import pandas as pd

from .safety import (
    BLOCKED_UNSAFE_OUTPUT,
    Tier1BlockedError,
    assert_export_safe_frame,
    git_root,
    is_within,
    require_restricted_destination,
    sha256_file,
    write_json,
    write_safe_csv,
)


BLOCKED_SOURCE_LINKAGE = "BLOCKED_SOURCE_LINKAGE"
BLOCKED_PREPROCESSING_PROVENANCE = "BLOCKED_PREPROCESSING_PROVENANCE"
BLOCKED_FRAME_SELECTION_REPRODUCTION = "BLOCKED_FRAME_SELECTION_REPRODUCTION"
BLOCKED_CHECKPOINT_PROVENANCE = "BLOCKED_CHECKPOINT_PROVENANCE"
RECONSTRUCTED_VECTOR_MISMATCH = "RECONSTRUCTED_VECTOR_MISMATCH"
DUPLICATE_SEMANTICS_RESOLVED = "DUPLICATE_SEMANTICS_RESOLVED"
DUPLICATE_SEMANTICS_STILL_UNRESOLVED = "DUPLICATE_SEMANTICS_STILL_UNRESOLVED"

MODE_HISTORICAL_NPZ = "historical_npz"
MODE_DICOM_REHYDRATION = "dicom_rehydration"

HISTORICAL_MEAN = np.asarray([29.110628, 28.076836, 29.096405], dtype=np.float32)
HISTORICAL_STD = np.asarray([47.989223, 46.456997, 47.20083], dtype=np.float32)


def array_sha256(array: np.ndarray) -> str:
    contiguous = np.ascontiguousarray(array)
    digest = hashlib.sha256()
    digest.update(str(contiguous.dtype).encode("ascii"))
    digest.update(json.dumps(list(contiguous.shape), separators=(",", ":")).encode("ascii"))
    digest.update(contiguous.tobytes(order="C"))
    return digest.hexdigest()


@dataclass(frozen=True)
class WindowSpec:
    window_index: int | None = None
    frame_start: int | None = None
    frame_end: int | None = None


@dataclass(frozen=True)
class RecoveryPrerequisites:
    mode: str
    source_candidates: Sequence[Path] = ()
    recovered_npz: Path | None = None
    preprocessing_expected_sha256: str = ""
    preprocessing_observed_sha256: str = ""
    frame_selection_pinned: bool = False
    checkpoint_expected_sha256: str = ""
    checkpoint_observed_sha256: str = ""


@dataclass
class RecoveryEvidence:
    status: str
    detail: str
    processed_array_sha256: str = ""
    processed_array_shape: str = ""
    processed_array_dtype: str = ""
    processed_array_selector: str = ""
    encoder_input_sha256: str = ""
    encoder_input_shape: str = ""
    reconstructed_vector_sha256: str = ""
    historical_vector_count: int = 0
    exact_historical_vector_matches: int = 0
    max_absolute_difference: float | None = None


def prerequisite_status(prerequisites: RecoveryPrerequisites) -> tuple[str, str]:
    if prerequisites.mode not in {MODE_HISTORICAL_NPZ, MODE_DICOM_REHYDRATION}:
        return DUPLICATE_SEMANTICS_STILL_UNRESOLVED, "unknown recovery mode"
    if prerequisites.mode == MODE_HISTORICAL_NPZ:
        if prerequisites.recovered_npz is None or not prerequisites.recovered_npz.is_file():
            return DUPLICATE_SEMANTICS_STILL_UNRESOLVED, "historical processed NPZ is unavailable"
    else:
        available = [path for path in prerequisites.source_candidates if path.is_file()]
        if len(available) != 1:
            return (
                BLOCKED_SOURCE_LINKAGE,
                f"expected one available source DICOM, found {len(available)}",
            )

    if (
        not prerequisites.preprocessing_expected_sha256
        or not prerequisites.preprocessing_observed_sha256
        or prerequisites.preprocessing_expected_sha256
        != prerequisites.preprocessing_observed_sha256
    ):
        return BLOCKED_PREPROCESSING_PROVENANCE, "preprocessing code hash is missing or mismatched"
    if not prerequisites.frame_selection_pinned:
        return BLOCKED_FRAME_SELECTION_REPRODUCTION, "historical frame selection is not pinned"
    if (
        not prerequisites.checkpoint_expected_sha256
        or not prerequisites.checkpoint_observed_sha256
        or prerequisites.checkpoint_expected_sha256 != prerequisites.checkpoint_observed_sha256
    ):
        return BLOCKED_CHECKPOINT_PROVENANCE, "encoder checkpoint hash is missing or mismatched"
    return "READY", "all mode-specific prerequisites are pinned"


def select_processed_array(
    archive: Mapping[str, np.ndarray],
    window: WindowSpec = WindowSpec(),
) -> tuple[np.ndarray, str]:
    if window.window_index is not None:
        for key in (f"frames_{window.window_index}", f"clip_{window.window_index}"):
            if key in archive:
                frames = np.asarray(archive[key])
                if frames.ndim != 4:
                    raise Tier1BlockedError(
                        BLOCKED_FRAME_SELECTION_REPRODUCTION,
                        f"{key} must be four-dimensional",
                    )
                return frames, key

    if "frames" not in archive:
        raise Tier1BlockedError(
            BLOCKED_FRAME_SELECTION_REPRODUCTION,
            "processed archive does not contain a frames array",
        )
    frames = np.asarray(archive["frames"])
    if frames.ndim == 5:
        if window.window_index is None:
            if frames.shape[0] != 1:
                raise Tier1BlockedError(
                    BLOCKED_FRAME_SELECTION_REPRODUCTION,
                    "multi-window archive requires an explicit window index",
                )
            return frames[0], "frames[0]"
        if window.window_index < 0 or window.window_index >= frames.shape[0]:
            raise Tier1BlockedError(
                BLOCKED_FRAME_SELECTION_REPRODUCTION,
                "window index is outside the processed archive",
            )
        return frames[window.window_index], f"frames[{window.window_index}]"
    if frames.ndim != 4:
        raise Tier1BlockedError(
            BLOCKED_FRAME_SELECTION_REPRODUCTION,
            f"processed frames must be 4D or 5D, found {frames.ndim}D",
        )

    start, end = window.frame_start, window.frame_end
    if start is None and end is None:
        return frames, "frames"
    if start is None or end is None or start < 0 or end <= start or end > frames.shape[0]:
        raise Tier1BlockedError(
            BLOCKED_FRAME_SELECTION_REPRODUCTION,
            "temporal window is incomplete or outside the processed array",
        )
    return frames[start:end], f"frames[{start}:{end}]"


def prepare_encoder_input(
    frames: np.ndarray,
    *,
    mean: np.ndarray = HISTORICAL_MEAN,
    std: np.ndarray = HISTORICAL_STD,
    target_frames: int = 32,
    stride: int = 2,
    target_size: int = 224,
) -> np.ndarray:
    frames = np.asarray(frames)
    if frames.ndim != 4 or frames.shape[-1] != 3:
        raise Tier1BlockedError(
            BLOCKED_FRAME_SELECTION_REPRODUCTION,
            f"expected T,H,W,3 frames, found {frames.shape}",
        )
    if tuple(frames.shape[1:3]) != (target_size, target_size):
        raise Tier1BlockedError(
            BLOCKED_FRAME_SELECTION_REPRODUCTION,
            f"expected {target_size}x{target_size} processed frames",
        )
    if target_frames != 32 or stride != 2:
        raise Tier1BlockedError(
            BLOCKED_FRAME_SELECTION_REPRODUCTION,
            "historical encoder pathway requires 32 stored frames and stride 2",
        )

    x = frames.astype(np.float32, copy=False).transpose(3, 0, 1, 2)
    x = (x - mean.reshape(3, 1, 1, 1)) / std.reshape(3, 1, 1, 1)
    if x.shape[1] < target_frames:
        padding = np.zeros(
            (3, target_frames - x.shape[1], target_size, target_size), dtype=np.float32
        )
        x = np.concatenate([x, padding], axis=1)
    else:
        x = x[:, :target_frames]
    x = np.ascontiguousarray(x[:, :target_frames:stride], dtype=np.float32)
    if x.shape != (3, 16, target_size, target_size):
        raise Tier1BlockedError(
            BLOCKED_FRAME_SELECTION_REPRODUCTION,
            f"historical encoder input has unexpected shape {x.shape}",
        )
    return x


def compare_reconstructed_vector(
    reconstructed: np.ndarray,
    historical_vectors: Sequence[np.ndarray],
) -> tuple[str, int, float | None]:
    vector = np.asarray(reconstructed, dtype=np.float32).reshape(-1)
    if not historical_vectors:
        return DUPLICATE_SEMANTICS_STILL_UNRESOLVED, 0, None
    exact = 0
    max_difference = 0.0
    for historical in historical_vectors:
        reference = np.asarray(historical, dtype=np.float32).reshape(-1)
        if reference.shape != vector.shape:
            return RECONSTRUCTED_VECTOR_MISMATCH, exact, float("inf")
        difference = float(np.max(np.abs(reference - vector))) if len(vector) else 0.0
        max_difference = max(max_difference, difference)
        exact += int(np.array_equal(reference, vector))
    if exact == len(historical_vectors):
        return DUPLICATE_SEMANTICS_RESOLVED, exact, max_difference
    return RECONSTRUCTED_VECTOR_MISMATCH, exact, max_difference


def verify_processed_input(
    archive: Mapping[str, np.ndarray],
    historical_vectors: Sequence[np.ndarray],
    encoder: Callable[[np.ndarray], np.ndarray],
    *,
    window: WindowSpec = WindowSpec(),
) -> RecoveryEvidence:
    try:
        processed, selector = select_processed_array(archive, window)
        encoder_input = prepare_encoder_input(processed)
        reconstructed = np.asarray(encoder(encoder_input), dtype=np.float32).reshape(-1)
        status, exact_matches, max_difference = compare_reconstructed_vector(
            reconstructed, historical_vectors
        )
    except Tier1BlockedError as exc:
        return RecoveryEvidence(status=exc.status, detail=exc.detail)
    except Exception as exc:
        return RecoveryEvidence(
            status=DUPLICATE_SEMANTICS_STILL_UNRESOLVED,
            detail=f"{type(exc).__name__}: {exc}",
        )

    return RecoveryEvidence(
        status=status,
        detail=(
            "reconstructed vector exactly matches every historical row"
            if status == DUPLICATE_SEMANTICS_RESOLVED
            else "reconstructed vector does not exactly match every historical row"
        ),
        processed_array_sha256=array_sha256(processed),
        processed_array_shape=json.dumps(list(processed.shape), separators=(",", ":")),
        processed_array_dtype=str(processed.dtype),
        processed_array_selector=selector,
        encoder_input_sha256=array_sha256(encoder_input),
        encoder_input_shape=json.dumps(list(encoder_input.shape), separators=(",", ":")),
        reconstructed_vector_sha256=array_sha256(reconstructed),
        historical_vector_count=int(len(historical_vectors)),
        exact_historical_vector_matches=int(exact_matches),
        max_absolute_difference=max_difference,
    )


def write_recovery_outputs(
    restricted_rows: pd.DataFrame,
    output_root: Path,
    worktree: Path | None = None,
) -> dict[str, Any]:
    root = require_restricted_destination(output_root, worktree=worktree)
    repo = worktree.resolve() if worktree is not None else git_root()
    if repo is not None and is_within(root, repo):
        raise Tier1BlockedError(BLOCKED_UNSAFE_OUTPUT, "recovery output is inside Git")
    if root.exists():
        raise FileExistsError(f"refusing to overwrite recovery output root: {root}")
    if "status" not in restricted_rows.columns:
        raise ValueError("restricted recovery rows require a status column")

    restricted = root / "restricted"
    safe = root / "aggregate_safe"
    restricted.mkdir(parents=True)
    safe.mkdir(parents=True)
    rows_path = restricted / "duplicate_recovery_rows.csv"
    restricted_rows.to_csv(rows_path, index=False)

    counts = restricted_rows["status"].fillna("").astype(str).value_counts().to_dict()
    summary = {
        "n_groups": int(len(restricted_rows)),
        "status_counts": {str(key): int(value) for key, value in sorted(counts.items())},
        "n_resolved": int(counts.get(DUPLICATE_SEMANTICS_RESOLVED, 0)),
        "n_mismatched": int(counts.get(RECONSTRUCTED_VECTOR_MISMATCH, 0)),
        "n_still_unresolved": int(len(restricted_rows) - counts.get(DUPLICATE_SEMANTICS_RESOLVED, 0)),
        "restricted_evidence_packet_sha256": {
            "duplicate_recovery_rows.csv": sha256_file(rows_path)
        },
    }
    safe_frame = pd.DataFrame(
        [
            {"status": str(status), "n_groups": int(count)}
            for status, count in sorted(counts.items())
        ]
    )
    assert_export_safe_frame(safe_frame, "duplicate recovery summary")
    write_safe_csv(safe / "duplicate_recovery_status_counts.csv", safe_frame, "recovery status")
    write_json(safe / "duplicate_recovery_summary.json", summary)
    return summary

