"""Phase 2I replay diagnosis, targeted restoration, and technical evidence tiers."""
from __future__ import annotations

import hashlib
import itertools
import json
import os
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping
from urllib.parse import quote

import numpy as np
import pandas as pd

from .safety import (
    Tier1BlockedError,
    assert_export_safe_frame,
    canonical_id_set_sha256,
    require_columns,
    require_restricted_destination,
    sha256_file,
    write_json,
    write_safe_csv,
)


EXACT_MODEL_INPUT = "EXACT_MODEL_INPUT"
VERIFIED_EQUIVALENT_REPLAY = "VERIFIED_EQUIVALENT_REPLAY"
SOURCE_ACQUISITION_ONLY = "SOURCE_ACQUISITION_ONLY"
NOT_ASSESSABLE = "NOT_ASSESSABLE"
EVIDENCE_TIERS = (
    EXACT_MODEL_INPUT,
    VERIFIED_EQUIVALENT_REPLAY,
    SOURCE_ACQUISITION_ONLY,
    NOT_ASSESSABLE,
)

REPLAY_PATH_VALIDATED = "REPLAY_PATH_VALIDATED"
REPLAY_USABLE_WITH_TIERED_REPORTING = "REPLAY_USABLE_WITH_TIERED_REPORTING"
BLOCKED_REPLAY_CONTENT_DIFFERENCE = "BLOCKED_REPLAY_CONTENT_DIFFERENCE"
BLOCKED_REPLAY_PROVENANCE = "BLOCKED_REPLAY_PROVENANCE"
BLOCKED_OFFICIAL_SOURCE_AUTHENTICATION = "BLOCKED_OFFICIAL_SOURCE_AUTHENTICATION"
AUDIT_INPUTS_TECHNICALLY_LOCKED = "AUDIT_INPUTS_TECHNICALLY_LOCKED"
BLOCKED_PHASE2I_SOURCE_MISMATCH = "BLOCKED_PHASE2I_SOURCE_MISMATCH"
BLOCKED_PHASE2J_SOURCE_MISMATCH = "BLOCKED_PHASE2J_SOURCE_MISMATCH"
RESTORATION_COMPLETE = "RESTORATION_COMPLETE"
LOCKED_ROSTER_SOURCE_RESTORED = "LOCKED_ROSTER_SOURCE_RESTORED"
RESTORATION_INCOMPLETE = "RESTORATION_INCOMPLETE"

OFFICIAL_SOURCE_BASE = "https://physionet.org/files/mimic-iv-echo/1.0/"
MODEL_SOURCE_FRAME_POSITIONS = tuple(range(0, 32, 2))
TECHNICAL_PREPROCESSING_POLICY = "PHASE2J_PINNED_ECHOPRIME_INPUT_REPLAY_V1"

PILOT_REQUIRED_COLUMNS = {
    "audit_id",
    "clip_audit_id",
    "source_dicom_sha256",
    "processed_npz_sha256",
}
ROSTER_REQUIRED_COLUMNS = {
    "audit_id",
    "clip_audit_id",
    "study_id",
    "subject_id",
    "dicom_filepath",
    "npz_path",
    "source_manifest_row_sha256",
}


@dataclass(frozen=True)
class ReplayDiagnosisResult:
    restricted_rows: pd.DataFrame
    safe_summary: dict[str, Any]
    output_root: Path


@dataclass(frozen=True)
class RestorationResult:
    restricted_rows: pd.DataFrame
    safe_summary: dict[str, Any]
    output_root: Path


@dataclass(frozen=True)
class TechnicalInventoryResult:
    restricted_rows: pd.DataFrame
    safe_tier_counts: pd.DataFrame
    safe_summary: dict[str, Any]
    output_root: Path


@dataclass(frozen=True)
class AuditMediaResult:
    restricted_rows: pd.DataFrame
    safe_summary: dict[str, Any]
    output_root: Path


def classify_evidence_tier(
    *,
    retained_historical_processed: bool,
    exact_replay: bool,
    equivalent_replay_verified: bool,
    unique_source_linkage: bool,
    source_viewable: bool,
    provenance_pinned: bool,
    frame_selection_established: bool,
    content_affecting_difference: bool,
) -> str:
    """Apply the prespecified A-D technical evidence hierarchy."""

    if retained_historical_processed or exact_replay:
        return EXACT_MODEL_INPUT
    if (
        equivalent_replay_verified
        and unique_source_linkage
        and source_viewable
        and provenance_pinned
        and frame_selection_established
        and not content_affecting_difference
    ):
        return VERIFIED_EQUIVALENT_REPLAY
    if unique_source_linkage and source_viewable:
        return SOURCE_ACQUISITION_ONLY
    return NOT_ASSESSABLE


def replay_readiness_status(rows: pd.DataFrame) -> str:
    """Return a bounded replay gate without collapsing source-only evidence into model input."""

    require_columns(
        rows,
        [
            "source_identity_verified",
            "frame_count_verified",
            "recorded_indices_valid",
            "frame_indices_match",
            "frame_order_verified",
            "geometry_verified",
            "content_difference_status",
            "retained_historical_processed",
            "exact_replay",
        ],
        "Phase 2I replay diagnosis",
    )
    if rows.empty:
        return BLOCKED_REPLAY_PROVENANCE
    provenance = (
        rows["source_identity_verified"].astype(bool)
        & rows["frame_count_verified"].astype(bool)
        & rows["recorded_indices_valid"].astype(bool)
        & rows["frame_indices_match"].astype(bool)
        & rows["frame_order_verified"].astype(bool)
        & rows["geometry_verified"].astype(bool)
    )
    if not bool(provenance.all()):
        return BLOCKED_REPLAY_PROVENANCE
    content = rows["content_difference_status"].astype(str)
    if content.isin({"content_affecting", "unresolved"}).any():
        return BLOCKED_REPLAY_CONTENT_DIFFERENCE
    if bool(rows["exact_replay"].astype(bool).all()):
        return REPLAY_PATH_VALIDATED
    if bool(rows["retained_historical_processed"].astype(bool).all()):
        return REPLAY_USABLE_WITH_TIERED_REPORTING
    return BLOCKED_REPLAY_PROVENANCE


def _normal_text(value: Any) -> str:
    if pd.isna(value):
        return ""
    return str(value).strip()


def _truth(value: Any) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


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


def _git_head() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def _require_new_output(root: Path, safe_root: Path) -> tuple[Path, Path]:
    restricted = require_restricted_destination(root)
    safe = require_restricted_destination(safe_root)
    if restricted.exists() or safe.exists():
        raise FileExistsError("refusing to overwrite Phase 2I output")
    return restricted, safe


def _validate_relative_dicom_path(value: Any) -> str:
    raw = _normal_text(value).replace("\\", "/")
    path = PurePosixPath(raw)
    if (
        not raw
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or path.suffix.lower() != ".dcm"
        or not raw.startswith("files/")
    ):
        raise Tier1BlockedError(
            BLOCKED_PHASE2I_SOURCE_MISMATCH,
            "locked DICOM path is not a safe MIMIC-IV-ECHO files/ path",
        )
    return path.as_posix()


def _source_path_from_locked_row(row: Mapping[str, Any]) -> Path:
    relative = _validate_relative_dicom_path(row["dicom_filepath"])
    npz_path = Path(_normal_text(row["npz_path"])).expanduser()
    marker = f"{os.sep}derived{os.sep}allclip_npz{os.sep}"
    text = str(npz_path)
    if marker not in text:
        raise Tier1BlockedError(
            BLOCKED_REPLAY_PROVENANCE,
            "retained processed path does not identify its bounded cohort source root",
        )
    source_root = Path(text.split(marker, 1)[0])
    candidate = (source_root / relative).resolve()
    if not candidate.is_file():
        raise FileNotFoundError(candidate)
    return candidate


def _load_replay_arrays(dicom_path: Path, npz_path: Path) -> dict[str, Any]:
    """Load and replay one retained clip using the pinned Phase 2H pathway."""

    import pydicom

    from extract_mimic_echo_cines import (
        crop_and_scale,
        mask_outside_ultrasound,
        normalize_pixels,
        temporal_sample,
    )

    dataset = pydicom.dcmread(str(dicom_path), stop_before_pixels=False)
    raw = normalize_pixels(dataset)
    with np.load(npz_path, allow_pickle=False) as archive:
        required = {"frames", "sampled_indices", "source_num_frames"}
        missing = sorted(required - set(archive.files))
        if missing:
            raise KeyError(f"retained processed archive lacks {missing}")
        stored = np.asarray(archive["frames"])
        recorded_indices = np.asarray(archive["sampled_indices"]).reshape(-1)
        recorded_source_count = np.asarray(archive["source_num_frames"]).reshape(-1)
        recorded_rows = (
            np.asarray(archive["source_rows"]).reshape(-1)
            if "source_rows" in archive.files
            else np.array([], dtype=int)
        )
        recorded_columns = (
            np.asarray(archive["source_columns"]).reshape(-1)
            if "source_columns" in archive.files
            else np.array([], dtype=int)
        )
    if stored.ndim != 4 or stored.shape[-1] != 3 or stored.shape[0] < 32:
        raise ValueError(f"retained processed frames have unsupported shape {stored.shape}")
    indices_valid = bool(
        len(recorded_indices) == 32
        and np.all(np.isfinite(recorded_indices))
        and np.all(recorded_indices == np.floor(recorded_indices))
        and np.all(recorded_indices >= 0)
        and np.all(recorded_indices < len(raw))
    )
    recorded_indices_int = recorded_indices.astype(int) if indices_valid else np.array([], dtype=int)
    masked = mask_outside_ultrasound(raw)
    resized = np.stack([crop_and_scale(frame, 224) for frame in masked], axis=0).astype(np.uint8)
    replayed, replay_indices = temporal_sample(resized, 32)
    return {
        "dataset": dataset,
        "raw": raw,
        "stored": stored[:32],
        "recorded_indices": recorded_indices_int,
        "recorded_source_count": recorded_source_count,
        "recorded_rows": recorded_rows,
        "recorded_columns": recorded_columns,
        "replayed": replayed,
        "replay_indices": replay_indices,
        "indices_valid": indices_valid,
    }


def _channel_permutation_exact(observed: np.ndarray, expected: np.ndarray) -> str:
    if observed.shape != expected.shape or observed.ndim != 4 or observed.shape[-1] != 3:
        return ""
    for permutation in itertools.permutations(range(3)):
        if permutation == (0, 1, 2):
            continue
        if np.array_equal(observed[..., permutation], expected):
            return "".join(str(index) for index in permutation)
    return ""


def _difference_metrics(replayed: np.ndarray, stored: np.ndarray) -> dict[str, Any]:
    if replayed.shape != stored.shape:
        return {
            "pixel_shape_match": False,
            "pixel_mae": None,
            "pixel_max_abs": None,
            "pixel_nonzero_fraction": None,
            "channel_permutation_exact": "",
            "content_difference_status": "content_affecting",
            "difference_explanation": "processed frame geometry differs",
        }
    left = replayed.astype(np.int16)
    right = stored.astype(np.int16)
    delta = np.abs(left - right)
    exact = bool(np.array_equal(replayed, stored))
    permutation = _channel_permutation_exact(replayed, stored)
    max_abs = int(delta.max()) if delta.size else 0
    mae = float(delta.mean()) if delta.size else 0.0
    fraction = float(np.count_nonzero(delta) / delta.size) if delta.size else 0.0
    if exact:
        status = "none"
        explanation = "bitwise identical"
    elif permutation:
        status = "non_content_affecting"
        explanation = "exact channel permutation with unchanged frames and geometry"
    elif max_abs <= 1:
        status = "non_content_affecting"
        explanation = "bounded one-level numerical difference with unchanged frames and geometry"
    else:
        status = "unresolved"
        explanation = "non-bitwise pixel transformation requires bounded technical review"
    return {
        "pixel_shape_match": True,
        "pixel_mae": mae,
        "pixel_max_abs": max_abs,
        "pixel_nonzero_fraction": fraction,
        "channel_permutation_exact": permutation,
        "content_difference_status": status,
        "difference_explanation": explanation,
    }


def _prefixed_metrics(prefix: str, metrics: Mapping[str, Any]) -> dict[str, Any]:
    return {f"{prefix}{key}": value for key, value in metrics.items()}


def diagnose_existing_pilot(
    *,
    pilot_rows_csv: Path,
    clip_roster_csv: Path,
    output_root: Path,
    safe_output_dir: Path,
    expected_attempts: int = 18,
) -> ReplayDiagnosisResult:
    """Diagnose exactly the prior pilot attempts without adding clinical annotations."""

    output_root, safe_output_dir = _require_new_output(output_root, safe_output_dir)
    pilot = pd.read_csv(pilot_rows_csv)
    roster = pd.read_csv(clip_roster_csv)
    require_columns(pilot, sorted(PILOT_REQUIRED_COLUMNS), "Phase 2H pilot rows")
    require_columns(roster, sorted(ROSTER_REQUIRED_COLUMNS), "locked canonical clip roster")
    if len(pilot) != expected_attempts:
        raise Tier1BlockedError(
            BLOCKED_PHASE2I_SOURCE_MISMATCH,
            f"expected exactly {expected_attempts} prior pilot attempts, found {len(pilot)}",
        )
    key_columns = ["audit_id", "clip_audit_id"]
    if pilot[key_columns].astype(str).duplicated().any():
        raise Tier1BlockedError(BLOCKED_PHASE2I_SOURCE_MISMATCH, "pilot contains duplicate clip keys")
    if roster[key_columns].astype(str).duplicated().any():
        raise Tier1BlockedError(BLOCKED_PHASE2I_SOURCE_MISMATCH, "roster contains duplicate clip keys")
    merged = pilot.merge(roster, on=key_columns, how="left", validate="one_to_one", suffixes=("_pilot", ""))
    if merged["study_id"].isna().any():
        raise Tier1BlockedError(BLOCKED_PHASE2I_SOURCE_MISMATCH, "pilot contains a clip outside the locked roster")

    records: list[dict[str, Any]] = []
    source_commit = _git_head()
    extraction_script = Path(__file__).resolve().parents[1] / "extract_mimic_echo_cines.py"
    extraction_script_sha256 = sha256_file(extraction_script)
    phase2i_script_sha256 = sha256_file(Path(__file__).resolve())
    for _, row in merged.iterrows():
        record: dict[str, Any] = {
            "audit_id": str(row["audit_id"]),
            "clip_audit_id": str(row["clip_audit_id"]),
            "study_id": row["study_id"],
            "subject_id": row["subject_id"],
            "retained_historical_processed": False,
            "source_identity_verified": False,
            "frame_count_verified": False,
            "recorded_indices_valid": False,
            "frame_indices_match": False,
            "frame_order_verified": False,
            "geometry_verified": False,
            "exact_replay": False,
            "full_32_frame_exact_replay": False,
            "equivalent_replay_verified": False,
            "source_viewable": False,
            "provenance_pinned": True,
            "content_difference_status": "unresolved",
            "difference_explanation": "diagnosis did not complete",
            "mismatch_stage": "load",
            "evidence_tier": NOT_ASSESSABLE,
            "technical_visual_review_required": True,
            "audit_category_impact": "unresolved",
            "prior_contact_sheet": _normal_text(row.get("contact_sheet", "")),
            "error": "",
            "source_commit": source_commit,
            "extraction_script_sha256": extraction_script_sha256,
            "phase2i_script_sha256": phase2i_script_sha256,
            "normalization_behavior": "pinned normalize_pixels implementation",
            "spatial_behavior": "mask_outside_ultrasound then crop_and_scale to 224 pixels",
            "frame_selection_behavior": "temporal_sample to 32 then positions 0:32:2",
        }
        try:
            npz_path = Path(_normal_text(row["npz_path"])).expanduser().resolve()
            source_path = _source_path_from_locked_row(row)
            if not npz_path.is_file():
                raise FileNotFoundError(npz_path)
            record["retained_historical_processed"] = True
            observed_source_hash = sha256_file(source_path)
            observed_npz_hash = sha256_file(npz_path)
            prior_source_hash = _normal_text(row.get("source_dicom_sha256_pilot", row.get("source_dicom_sha256", ""))).lower()
            prior_npz_hash = _normal_text(row.get("processed_npz_sha256_pilot", row.get("processed_npz_sha256", ""))).lower()
            source_identity = bool(prior_source_hash and observed_source_hash == prior_source_hash)
            processed_identity = bool(prior_npz_hash and observed_npz_hash == prior_npz_hash)
            record["source_identity_verified"] = source_identity and processed_identity
            arrays = _load_replay_arrays(source_path, npz_path)
            raw = arrays["raw"]
            stored = arrays["stored"]
            replayed = arrays["replayed"]
            stored_model_input = stored[list(MODEL_SOURCE_FRAME_POSITIONS)]
            replayed_model_input = replayed[list(MODEL_SOURCE_FRAME_POSITIONS)]
            recorded_indices = arrays["recorded_indices"]
            replay_indices = arrays["replay_indices"]
            record["source_viewable"] = True
            record["source_frame_count"] = int(len(raw))
            record["decoded_rows"] = int(raw.shape[1])
            record["decoded_columns"] = int(raw.shape[2])
            record["photometric_interpretation"] = _normal_text(
                getattr(arrays["dataset"], "PhotometricInterpretation", "")
            )
            source_count = arrays["recorded_source_count"]
            record["frame_count_verified"] = bool(
                len(source_count) == 1 and int(source_count[0]) == len(raw)
            )
            record["recorded_indices_valid"] = bool(arrays["indices_valid"])
            record["frame_indices_match"] = bool(
                arrays["indices_valid"] and np.array_equal(recorded_indices, replay_indices)
            )
            record["frame_order_verified"] = bool(
                arrays["indices_valid"]
                and len(recorded_indices) == 32
                and np.array_equal(
                    recorded_indices[list(MODEL_SOURCE_FRAME_POSITIONS)],
                    replay_indices[list(MODEL_SOURCE_FRAME_POSITIONS)],
                )
            )
            recorded_rows = arrays["recorded_rows"]
            recorded_columns = arrays["recorded_columns"]
            rows_match = len(recorded_rows) in {0, 1} and (
                len(recorded_rows) == 0 or int(recorded_rows[0]) == raw.shape[1]
            )
            columns_match = len(recorded_columns) in {0, 1} and (
                len(recorded_columns) == 0 or int(recorded_columns[0]) == raw.shape[2]
            )
            record["geometry_verified"] = bool(
                rows_match and columns_match and stored.shape == replayed.shape
            )
            full_difference = _difference_metrics(replayed, stored)
            model_input_difference = _difference_metrics(replayed_model_input, stored_model_input)
            record.update(_prefixed_metrics("full_32_frame_", full_difference))
            record.update(model_input_difference)
            record["full_32_frame_exact_replay"] = bool(np.array_equal(replayed, stored))
            record["exact_replay"] = bool(
                np.array_equal(replayed_model_input, stored_model_input)
            )
            record["model_input_source_frames_sha256"] = _array_sha256(replayed_model_input)
            record["retained_model_input_frames_sha256"] = _array_sha256(stored_model_input)
            if not record["source_identity_verified"]:
                record["mismatch_stage"] = "source_identity"
            elif not record["frame_count_verified"]:
                record["mismatch_stage"] = "source_frame_count"
            elif not record["recorded_indices_valid"] or not record["frame_indices_match"]:
                record["mismatch_stage"] = "frame_selection"
                record["content_difference_status"] = "content_affecting"
            elif not record["frame_order_verified"]:
                record["mismatch_stage"] = "frame_order"
                record["content_difference_status"] = "content_affecting"
            elif not record["geometry_verified"]:
                record["mismatch_stage"] = "geometry"
                record["content_difference_status"] = "content_affecting"
            elif record["exact_replay"] and record["full_32_frame_exact_replay"]:
                record["mismatch_stage"] = "none"
            elif record["exact_replay"]:
                record["mismatch_stage"] = "intermediate_32_frame_only"
                record["content_difference_status"] = "none"
                record["difference_explanation"] = (
                    "differences were confined to frames not selected for the 16-frame encoder input"
                )
            elif record.get("channel_permutation_exact"):
                record["mismatch_stage"] = "model_input_photometric_channel_order"
            elif record["content_difference_status"] == "non_content_affecting":
                record["mismatch_stage"] = "model_input_numerical_serialization"
            else:
                record["mismatch_stage"] = "model_input_pixel_transform_unresolved"
            record["evidence_tier"] = classify_evidence_tier(
                retained_historical_processed=record["retained_historical_processed"],
                exact_replay=record["exact_replay"],
                equivalent_replay_verified=False,
                unique_source_linkage=record["source_identity_verified"],
                source_viewable=record["source_viewable"],
                provenance_pinned=record["provenance_pinned"],
                frame_selection_established=record["frame_order_verified"],
                content_affecting_difference=record["content_difference_status"] == "content_affecting",
            )
            record["technical_visual_review_required"] = bool(not record["exact_replay"])
            record["audit_category_impact"] = (
                "none"
                if record["content_difference_status"] == "none"
                else "not_expected"
                if record["content_difference_status"] == "non_content_affecting"
                else "unresolved"
            )
            record["source_dicom_sha256"] = observed_source_hash
            record["processed_npz_sha256"] = observed_npz_hash
        except Exception as exc:
            record["error"] = f"{type(exc).__name__}: {exc}"
            record["mismatch_stage"] = "load_or_provenance"
            record["content_difference_status"] = "unresolved"
        records.append(record)

    restricted_rows = pd.DataFrame(records)
    status = replay_readiness_status(restricted_rows)
    mismatch_counts = restricted_rows["mismatch_stage"].value_counts(dropna=False).to_dict()
    tier_counts = restricted_rows["evidence_tier"].value_counts(dropna=False).to_dict()
    content_counts = restricted_rows["content_difference_status"].value_counts(dropna=False).to_dict()
    safe_summary = {
        "status": status,
        "pilot_attempts": int(len(restricted_rows)),
        "pilot_studies": int(restricted_rows["audit_id"].nunique()),
        "exact_replays": int(restricted_rows["exact_replay"].astype(bool).sum()),
        "nonexact_replays": int((~restricted_rows["exact_replay"].astype(bool)).sum()),
        "retained_historical_processed_inputs": int(
            restricted_rows["retained_historical_processed"].astype(bool).sum()
        ),
        "mismatch_stage_counts": {str(key): int(value) for key, value in mismatch_counts.items()},
        "evidence_tier_counts": {str(key): int(value) for key, value in tier_counts.items()},
        "content_difference_counts": {str(key): int(value) for key, value in content_counts.items()},
        "technical_visual_reviews_pending": int(
            restricted_rows["technical_visual_review_required"].astype(bool).sum()
        ),
        "audit_category_impact_unresolved": int(
            restricted_rows["audit_category_impact"].eq("unresolved").sum()
        ),
        "clinical_content_annotations_recorded": False,
        "ocr_used": False,
        "target_values_accessed": False,
        "pilot_rows_sha256": sha256_file(pilot_rows_csv),
        "clip_roster_sha256": sha256_file(clip_roster_csv),
    }
    assert_export_safe_frame(pd.DataFrame([safe_summary]), "Phase 2I replay summary")
    output_root.mkdir(parents=True)
    restricted_rows.to_csv(output_root / "pilot_replay_diagnostic_restricted.csv", index=False)
    write_json(output_root / "pilot_replay_diagnostic_restricted_summary.json", safe_summary)
    safe_output_dir.mkdir(parents=True)
    write_json(safe_output_dir / "pilot_replay_summary.json", safe_summary)
    write_safe_csv(
        safe_output_dir / "pilot_replay_summary.csv",
        pd.DataFrame([safe_summary]),
        "Phase 2I replay summary",
    )
    write_safe_csv(
        safe_output_dir / "pilot_replay_mismatch_counts.csv",
        pd.DataFrame(
            [
                {"mismatch_stage": key, "clip_count": int(value)}
                for key, value in sorted(mismatch_counts.items())
            ]
        ),
        "Phase 2I replay mismatch counts",
    )
    return ReplayDiagnosisResult(restricted_rows, safe_summary, output_root)


def _netrc_auth_available(path: Path, host: str = "physionet.org") -> bool:
    """Check only file presence and mode; credential parsing is delegated to wget."""

    del host
    return path.is_file() and (path.stat().st_mode & 0o777) == 0o600


def _validate_dicom_file(path: Path) -> tuple[bool, str]:
    try:
        import pydicom

        dataset = pydicom.dcmread(
            str(path),
            stop_before_pixels=False,
            defer_size="1 KB",
        )
    except Exception as exc:
        return False, f"{type(exc).__name__}"
    if "PixelData" not in dataset:
        return False, "missing_pixel_data"
    if not getattr(dataset, "Rows", None) or not getattr(dataset, "Columns", None):
        return False, "missing_pixel_geometry"
    return True, "dicom_validated"


def _download_one(url: str, destination: Path, netrc_path: Path) -> tuple[bool, str]:
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(destination.parent, 0o700)
    if destination.is_file() and destination.stat().st_size > 0:
        valid, detail = _validate_dicom_file(destination)
        return (True, "already_available_verified") if valid else (False, f"existing_{detail}")
    partial = destination.with_suffix(destination.suffix + ".part")
    wget = shutil.which("wget")
    if not wget:
        return False, "wget unavailable"
    command = [
        wget,
        "--quiet",
        "--continue",
        "--netrc",
        "--https-only",
        "--max-redirect=0",
        "--timeout=60",
        "--tries=3",
        f"--output-document={partial}",
        url,
    ]
    environment = os.environ.copy()
    environment["HOME"] = str(netrc_path.parent)
    result = subprocess.run(command, check=False, env=environment, capture_output=True, text=True)
    if result.returncode != 0 or not partial.is_file() or partial.stat().st_size == 0:
        return False, f"wget_exit_{result.returncode}"
    valid, detail = _validate_dicom_file(partial)
    if not valid:
        return False, f"downloaded_{detail}"
    os.replace(partial, destination)
    os.chmod(destination, 0o600)
    return True, "restored"


def _restoration_action_sheet(
    *,
    url_list: Path,
    destination_root: Path,
    job_a_command: str,
) -> str:
    return f"""# Phase 2I PhysioNet Authentication Action

The locked roster and file list are unchanged. No credential was found in a mode-0600 `$HOME/.netrc` entry for `physionet.org`.

On SCC, create or update your PhysioNet machine entry without placing a password on the command line:

```bash
umask 077
${{EDITOR:-vi}} "$HOME/.netrc"
chmod 600 "$HOME/.netrc"
wget --quiet --spider --netrc '{OFFICIAL_SOURCE_BASE}'
```

The file should contain a `machine physionet.org` entry with the authorized PhysioNet username and password. Enter those values only in the editor, never in a shell command, job script, log, Git file, or chat. Keep the file at mode 0600.

After the access check succeeds, the restricted locked URL list is:

`{url_list}`

The restricted destination is:

`{destination_root}`

Then submit:

```bash
{job_a_command}
```

Do not copy the URL list, restored files, or credentials outside restricted storage.
"""


def restore_locked_sources(
    *,
    restoration_manifest_csv: Path,
    output_root: Path,
    safe_output_dir: Path,
    source_destination_root: Path,
    netrc_path: Path,
    official_base_url: str = OFFICIAL_SOURCE_BASE,
    max_workers: int = 4,
    perform_downloads: bool = True,
    job_a_command: str = "qsub <validated Phase 2I Job A script>",
) -> RestorationResult:
    """Restore only roster-listed DICOMs from the pinned official v1.0 source."""

    output_root, safe_output_dir = _require_new_output(output_root, safe_output_dir)
    source_destination_root = require_restricted_destination(source_destination_root)
    if official_base_url != OFFICIAL_SOURCE_BASE:
        raise Tier1BlockedError(
            BLOCKED_PHASE2I_SOURCE_MISMATCH,
            "official source URL differs from pinned MIMIC-IV-ECHO v1.0",
        )
    if max_workers < 1 or max_workers > 8:
        raise ValueError("max_workers must be between 1 and 8")
    rows = pd.read_csv(restoration_manifest_csv)
    require_columns(
        rows,
        ["audit_id", "clip_audit_id", "declared_source_dicom", "unique_linkage"],
        "locked restoration manifest",
    )
    if not rows["unique_linkage"].map(_truth).all():
        raise Tier1BlockedError(
            BLOCKED_PHASE2I_SOURCE_MISMATCH,
            "restoration manifest contains ambiguous source linkage",
        )
    rows = rows.copy()
    rows["official_relative_path"] = rows["declared_source_dicom"].map(
        _validate_relative_dicom_path
    )
    if rows[["audit_id", "clip_audit_id"]].astype(str).duplicated().any():
        raise Tier1BlockedError(BLOCKED_PHASE2I_SOURCE_MISMATCH, "restoration manifest has duplicate clips")
    file_rows = rows.drop_duplicates("official_relative_path").copy()
    file_rows["official_url"] = file_rows["official_relative_path"].map(
        lambda value: official_base_url + quote(value, safe="/")
    )
    file_rows["restricted_destination"] = file_rows["official_relative_path"].map(
        lambda value: str((source_destination_root / value).resolve())
    )
    if any(
        source_destination_root.resolve() not in Path(value).resolve().parents
        for value in file_rows["restricted_destination"]
    ):
        raise Tier1BlockedError(BLOCKED_PHASE2I_SOURCE_MISMATCH, "restoration path escaped its root")

    output_root.mkdir(parents=True)
    url_list = output_root / "locked_official_source_urls.txt"
    url_list.write_text("\n".join(file_rows["official_url"].astype(str)) + "\n", encoding="utf-8")
    request_manifest = output_root / "locked_source_restoration_request.csv"
    file_rows.to_csv(request_manifest, index=False)

    auth_available = _netrc_auth_available(netrc_path)
    records: list[dict[str, Any]] = []
    if perform_downloads and auth_available:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {}
            for _, row in file_rows.iterrows():
                destination = Path(row["restricted_destination"])
                future = executor.submit(
                    _download_one,
                    str(row["official_url"]),
                    destination,
                    netrc_path,
                )
                futures[future] = row
            for future in as_completed(futures):
                row = futures[future]
                success, state = future.result()
                destination = Path(row["restricted_destination"])
                records.append(
                    {
                        "official_relative_path": row["official_relative_path"],
                        "restricted_destination": str(destination),
                        "success": bool(success),
                        "state": state,
                        "size_bytes": int(destination.stat().st_size) if success else 0,
                        "local_sha256": sha256_file(destination) if success else "",
                        "official_checksum_status": "not_provided_in_locked_manifest",
                    }
                )
    else:
        for _, row in file_rows.iterrows():
            destination = Path(row["restricted_destination"])
            existing = destination.is_file() and destination.stat().st_size > 0
            valid, validation_state = (
                _validate_dicom_file(destination) if existing else (False, "missing")
            )
            success = bool(existing and valid)
            records.append(
                {
                    "official_relative_path": row["official_relative_path"],
                    "restricted_destination": str(destination),
                    "success": success,
                    "state": (
                        "already_available_verified"
                        if success
                        else f"existing_{validation_state}"
                        if existing
                        else "authentication_required"
                    ),
                    "size_bytes": int(destination.stat().st_size) if success else 0,
                    "local_sha256": sha256_file(destination) if success else "",
                    "official_checksum_status": "not_provided_in_locked_manifest",
                }
            )
    restricted_rows = pd.DataFrame(records)
    restricted_rows.to_csv(output_root / "source_restoration_results_restricted.csv", index=False)
    restored_files = int(restricted_rows["success"].astype(bool).sum())
    requested_files = int(len(restricted_rows))
    restored_relative = set(
        restricted_rows.loc[restricted_rows["success"].astype(bool), "official_relative_path"].astype(str)
    )
    studies_with_any_file = set(
        rows.loc[rows["official_relative_path"].isin(restored_relative), "audit_id"].astype(str)
    )
    required_by_study = {
        str(audit_id): set(group["official_relative_path"].astype(str))
        for audit_id, group in rows.groupby("audit_id", sort=False)
    }
    completely_restored_study_ids = {
        audit_id
        for audit_id, required_paths in required_by_study.items()
        if required_paths and required_paths.issubset(restored_relative)
    }
    partially_restored_study_ids = studies_with_any_file - completely_restored_study_ids
    restored_studies = len(studies_with_any_file)
    requested_studies = int(rows["audit_id"].astype(str).nunique())
    expected_relative_paths = set(file_rows["official_relative_path"].astype(str))
    unexpected_files = 0
    if source_destination_root.exists():
        for candidate in source_destination_root.rglob("*"):
            if not candidate.is_file() or candidate.name.endswith(".part"):
                continue
            relative = candidate.relative_to(source_destination_root).as_posix()
            if relative not in expected_relative_paths:
                unexpected_files += 1
    status = (
        BLOCKED_PHASE2J_SOURCE_MISMATCH
        if unexpected_files
        else LOCKED_ROSTER_SOURCE_RESTORED
        if requested_files and restored_files == requested_files
        else BLOCKED_OFFICIAL_SOURCE_AUTHENTICATION
        if not auth_available
        else RESTORATION_INCOMPLETE
    )
    action_sheet = output_root / "official_source_authentication_action.md"
    if not auth_available:
        action_sheet.write_text(
            _restoration_action_sheet(
                url_list=url_list,
                destination_root=source_destination_root,
                job_a_command=job_a_command,
            ),
            encoding="utf-8",
        )
    safe_summary = {
        "status": status,
        "official_source": "PhysioNet MIMIC-IV-ECHO v1.0",
        "official_source_base_sha256": canonical_id_set_sha256([official_base_url]),
        "requested_studies": requested_studies,
        "requested_files": requested_files,
        "restored_studies": restored_studies,
        "completely_restored_studies": len(completely_restored_study_ids),
        "partially_restored_studies": len(partially_restored_study_ids),
        "restored_files": restored_files,
        "verified_files": restored_files,
        "newly_restored_files": int(restricted_rows["state"].eq("restored").sum()),
        "already_available_files": int(
            restricted_rows["state"].eq("already_available_verified").sum()
        ),
        "unavailable_studies": requested_studies - restored_studies,
        "failed_or_pending_files": requested_files - restored_files,
        "incomplete_files": int(restricted_rows["state"].str.contains("missing|partial", regex=True).sum()),
        "failed_files": int(
            restricted_rows["state"].str.startswith(("wget_exit_", "downloaded_", "existing_")).sum()
        ),
        "unexpected_files": unexpected_files,
        "total_restored_size_bytes": int(restricted_rows["size_bytes"].sum()),
        "newly_restored_size_bytes": int(
            restricted_rows.loc[restricted_rows["state"].eq("restored"), "size_bytes"].sum()
        ),
        "authentication_configured": bool(auth_available),
        "authentication_action_sheet_written": bool(not auth_available),
        "source_checksum_status": "not_provided_in_locked_manifest",
        "local_sha256_recorded_for_every_available_file": bool(
            restricted_rows.loc[restricted_rows["success"].astype(bool), "local_sha256"]
            .astype(str)
            .str.fullmatch(r"[0-9a-f]{64}")
            .all()
        ),
        "restoration_manifest_sha256": sha256_file(restoration_manifest_csv),
        "locked_url_list_sha256": sha256_file(url_list),
        "restoration_results_sha256": sha256_file(
            output_root / "source_restoration_results_restricted.csv"
        ),
        "locked_file_set_sha256": canonical_id_set_sha256(file_rows["official_relative_path"]),
        "official_host_verified": True,
        "credentials_logged": False,
        "source_files_outside_restricted_storage": False,
    }
    assert_export_safe_frame(pd.DataFrame([safe_summary]), "Phase 2I restoration summary")
    write_json(output_root / "source_restoration_restricted_summary.json", safe_summary)
    safe_output_dir.mkdir(parents=True)
    write_json(safe_output_dir / "source_restoration_summary.json", safe_summary)
    write_safe_csv(
        safe_output_dir / "source_restoration_summary.csv",
        pd.DataFrame([safe_summary]),
        "Phase 2I restoration summary",
    )
    return RestorationResult(restricted_rows, safe_summary, output_root)


def build_technical_inventory(
    *,
    clip_roster_csv: Path,
    audit_linkage_csv: Path,
    restored_source_results_csv: Path,
    output_root: Path,
    safe_output_dir: Path,
) -> TechnicalInventoryResult:
    """Assign conservative A/C/D tiers without performing content annotation."""

    output_root, safe_output_dir = _require_new_output(output_root, safe_output_dir)
    roster = pd.read_csv(clip_roster_csv)
    linkage = pd.read_csv(audit_linkage_csv)
    restored = pd.read_csv(restored_source_results_csv)
    require_columns(roster, sorted(ROSTER_REQUIRED_COLUMNS), "locked canonical clip roster")
    require_columns(
        linkage,
        ["audit_id", "target_membership", "target_strata"],
        "locked audit linkage",
    )
    require_columns(
        restored,
        ["official_relative_path", "restricted_destination", "success"],
        "restricted source restoration results",
    )
    restored_lookup = {
        str(row["official_relative_path"]): row
        for _, row in restored.iterrows()
        if _truth(row["success"])
    }
    records: list[dict[str, Any]] = []
    for _, row in roster.iterrows():
        relative = _validate_relative_dicom_path(row["dicom_filepath"])
        npz_path = Path(_normal_text(row["npz_path"])).expanduser()
        historical = npz_path.is_file()
        source_path: Path | None = None
        source_viewable = False
        source_frame_count = 0
        sampled_32_indices: list[int] = []
        source_indices: list[int] = []
        source_frame_shape: list[int] = []
        source_rows = 0
        source_columns = 0
        source_channels = 0
        photometric_interpretation = ""
        frame_order_verified = False
        retained_model_input_valid = False
        retained_model_input_shape: list[int] = []
        reconstruction_status = "not_assessable"
        reconstruction_error = ""
        try:
            source_path = _source_path_from_locked_row(row)
            source_viewable = source_path.is_file()
        except (FileNotFoundError, Tier1BlockedError):
            restored_row = restored_lookup.get(relative)
            if restored_row is not None:
                source_path = Path(str(restored_row["restricted_destination"]))
                source_viewable = source_path.is_file()
        if source_viewable and source_path is not None:
            try:
                import pydicom

                from extract_mimic_echo_cines import normalize_pixels, temporal_sample

                dataset = pydicom.dcmread(str(source_path), stop_before_pixels=False)
                raw = normalize_pixels(dataset)
                _, sampled_indices = temporal_sample(raw, 32)
                sampled_32_indices = [int(value) for value in sampled_indices]
                source_indices = [int(value) for value in sampled_indices[list(MODEL_SOURCE_FRAME_POSITIONS)]]
                source_frame_count = int(len(raw))
                source_frame_shape = [int(value) for value in raw.shape[1:]]
                source_rows = int(raw.shape[1])
                source_columns = int(raw.shape[2])
                source_channels = int(raw.shape[3]) if raw.ndim == 4 else 1
                photometric_interpretation = str(
                    getattr(dataset, "PhotometricInterpretation", "")
                )
                frame_order_verified = bool(
                    len(source_indices) == 16
                    and all(
                        current <= following
                        for current, following in zip(source_indices, source_indices[1:])
                    )
                )
                if not frame_order_verified:
                    raise ValueError("source frame selection did not preserve temporal order")
                reconstruction_status = "source_acquisition_frames_established"
            except Exception as exc:
                source_viewable = False
                reconstruction_error = f"{type(exc).__name__}: {exc}"
        if historical:
            try:
                with np.load(npz_path, allow_pickle=False) as archive:
                    if "frames" not in archive.files:
                        raise KeyError("frames")
                    retained = np.asarray(archive["frames"])
                    if retained.ndim != 4 or retained.shape[-1] != 3 or retained.shape[0] < 32:
                        raise ValueError(f"unsupported retained model-input shape {retained.shape}")
                    retained_model_input = retained[list(MODEL_SOURCE_FRAME_POSITIONS)]
                    retained_model_input_valid = retained_model_input.shape[0] == 16
                    retained_model_input_shape = [int(value) for value in retained_model_input.shape]
                if retained_model_input_valid:
                    reconstruction_status = "retained_historical_model_input_validated"
            except Exception as exc:
                historical = False
                reconstruction_error = f"{type(exc).__name__}: {exc}"
        tier = classify_evidence_tier(
            retained_historical_processed=historical,
            exact_replay=False,
            equivalent_replay_verified=False,
            unique_source_linkage=True,
            source_viewable=source_viewable,
            provenance_pinned=True,
            frame_selection_established=bool(frame_order_verified or retained_model_input_valid),
            content_affecting_difference=False,
        )
        records.append(
            {
                "audit_id": str(row["audit_id"]),
                "clip_audit_id": str(row["clip_audit_id"]),
                "study_id": row["study_id"],
                "subject_id": row["subject_id"],
                "evidence_tier": tier,
                "retained_historical_processed": bool(historical),
                "unique_source_linkage": True,
                "source_viewable": bool(source_viewable),
                "source_frame_count": source_frame_count,
                "sampled_32_source_frame_indices_json": json.dumps(sampled_32_indices),
                "encoder_sample_positions_json": json.dumps(list(MODEL_SOURCE_FRAME_POSITIONS)),
                "source_model_frame_indices_json": json.dumps(source_indices),
                "encoder_source_frame_indices_json": json.dumps(source_indices),
                "frame_order_verified": bool(frame_order_verified),
                "source_frame_shape_json": json.dumps(source_frame_shape),
                "source_rows": source_rows,
                "source_columns": source_columns,
                "source_channels": source_channels,
                "photometric_interpretation": photometric_interpretation,
                "photometric_handling": "extract_mimic_echo_cines.normalize_pixels",
                "spatial_transform_status": (
                    "retained_historical_model_input"
                    if retained_model_input_valid
                    else "not_verified_for_model_input"
                ),
                "preprocessing_policy": TECHNICAL_PREPROCESSING_POLICY,
                "retained_model_input_valid": bool(retained_model_input_valid),
                "retained_model_input_shape_json": json.dumps(retained_model_input_shape),
                "exact_replay": False,
                "verified_equivalent_replay": False,
                "reconstruction_status": reconstruction_status,
                "reconstruction_error": reconstruction_error,
                "content_affecting_mismatch_status": (
                    "not_applicable_retained_model_input"
                    if retained_model_input_valid
                    else "not_assessed_source_acquisition_only"
                ),
                "source_path": str(source_path) if source_path is not None else "",
                "processed_path": str(npz_path) if historical else "",
                "model_input_verified": tier in {EXACT_MODEL_INPUT, VERIFIED_EQUIVALENT_REPLAY},
                "source_only": tier == SOURCE_ACQUISITION_ONLY,
                "source_media_id": "",
                "model_input_media_id": "",
                "clinical_content_annotation": "",
            }
        )
    restricted_rows = pd.DataFrame(records)
    tier_rank = {
        EXACT_MODEL_INPUT: 0,
        VERIFIED_EQUIVALENT_REPLAY: 1,
        SOURCE_ACQUISITION_ONLY: 2,
        NOT_ASSESSABLE: 3,
    }
    ranked = restricted_rows.assign(tier_rank=restricted_rows["evidence_tier"].map(tier_rank))
    study_tier = (
        ranked.sort_values(["audit_id", "tier_rank"], ascending=[True, False], kind="mergesort")
        .drop_duplicates("audit_id")[["audit_id", "evidence_tier"]]
        .rename(columns={"evidence_tier": "study_evidence_tier"})
    )
    mixed_studies = int(
        (restricted_rows.groupby("audit_id")["evidence_tier"].nunique() > 1).sum()
    )
    membership_records: list[dict[str, str]] = []
    for _, row in linkage.iterrows():
        targets = [value.strip() for value in str(row["target_membership"]).split(";") if value.strip()]
        strata: dict[str, str] = {}
        for raw in str(row["target_strata"]).split(";"):
            target, separator, split = raw.strip().partition(":")
            if not separator or target in strata or not target or not split:
                raise Tier1BlockedError(
                    BLOCKED_PHASE2I_SOURCE_MISMATCH,
                    "locked target-strata encoding is malformed or duplicated",
                )
            strata[target] = split
        if set(targets) != set(strata):
            raise Tier1BlockedError(
                BLOCKED_PHASE2I_SOURCE_MISMATCH,
                "locked target membership and strata do not reconcile",
            )
        membership_records.extend(
            {"audit_id": str(row["audit_id"]), "target": target, "split": strata[target]}
            for target in targets
        )
    membership = pd.DataFrame(membership_records).drop_duplicates()
    if len(membership) != 120 or membership.groupby("target").size().to_dict() != {
        "lvot_vti": 60,
        "tapse": 60,
    }:
        raise Tier1BlockedError(
            BLOCKED_PHASE2I_SOURCE_MISMATCH,
            "locked target membership no longer contains 60 studies per target",
        )
    joined = membership.merge(study_tier, on="audit_id", how="left", validate="many_to_one")
    if joined["study_evidence_tier"].isna().any():
        raise Tier1BlockedError(BLOCKED_PHASE2I_SOURCE_MISMATCH, "sampling design left the technical roster")
    safe_tier_counts = (
        joined.groupby(["target", "split", "study_evidence_tier"], dropna=False)
        .size()
        .rename("study_count")
        .reset_index()
        .rename(columns={"study_evidence_tier": "evidence_tier"})
    )
    clip_counts = restricted_rows["evidence_tier"].value_counts().to_dict()
    physical_study_counts = study_tier["study_evidence_tier"].value_counts().to_dict()
    not_assessable = int((study_tier["study_evidence_tier"] == NOT_ASSESSABLE).sum())
    status = (
        AUDIT_INPUTS_TECHNICALLY_LOCKED
        if not_assessable == 0
        else "TECHNICAL_INPUTS_REQUIRE_REPRESENTATIVENESS_REVIEW"
    )
    safe_summary = {
        "status": status,
        "physical_studies": int(len(study_tier)),
        "clips": int(len(restricted_rows)),
        "studies_by_evidence_tier": {
            tier: int(physical_study_counts.get(tier, 0)) for tier in EVIDENCE_TIERS
        },
        "clips_by_evidence_tier": {tier: int(clip_counts.get(tier, 0)) for tier in EVIDENCE_TIERS},
        "technically_assessable_studies": int(len(study_tier) - not_assessable),
        "not_assessable_studies": not_assessable,
        "studies_with_mixed_clip_tiers": mixed_studies,
        "source_decode_failures": int((~restricted_rows["source_viewable"].astype(bool)).sum()),
        "frame_order_failures": int(
            (
                restricted_rows["source_viewable"].astype(bool)
                & ~restricted_rows["frame_order_verified"].astype(bool)
            ).sum()
        ),
        "retained_exact_model_input_clips": int(
            restricted_rows["retained_model_input_valid"].astype(bool).sum()
        ),
        "exact_replay_clips": int(restricted_rows["exact_replay"].astype(bool).sum()),
        "verified_equivalent_replay_clips": int(
            restricted_rows["verified_equivalent_replay"].astype(bool).sum()
        ),
        "source_acquisition_only_clips": int(
            restricted_rows["evidence_tier"].eq(SOURCE_ACQUISITION_ONLY).sum()
        ),
        "not_assessable_clips": int(
            restricted_rows["evidence_tier"].eq(NOT_ASSESSABLE).sum()
        ),
        "content_affecting_mismatches": 0,
        "non_content_affecting_mismatches": 0,
        "representativeness_review_required": bool(not_assessable),
        "clinical_content_annotations_recorded": False,
        "ocr_used": False,
        "clip_roster_sha256": sha256_file(clip_roster_csv),
        "audit_linkage_sha256": sha256_file(audit_linkage_csv),
        "restoration_results_sha256": sha256_file(restored_source_results_csv),
    }
    assert_export_safe_frame(pd.DataFrame([safe_summary]), "Phase 2I technical inventory summary")
    output_root.mkdir(parents=True)
    inventory_path = output_root / "technical_input_inventory_restricted.csv"
    study_tier_path = output_root / "technical_study_tiers_restricted.csv"
    restricted_rows.to_csv(inventory_path, index=False)
    study_tier.to_csv(study_tier_path, index=False)
    safe_summary["technical_inventory_sha256"] = sha256_file(inventory_path)
    safe_summary["technical_study_tiers_sha256"] = sha256_file(study_tier_path)
    write_json(output_root / "technical_input_inventory_restricted_summary.json", safe_summary)
    safe_output_dir.mkdir(parents=True)
    write_json(safe_output_dir / "technical_input_inventory_summary.json", safe_summary)
    write_safe_csv(
        safe_output_dir / "technical_input_tier_counts.csv",
        safe_tier_counts,
        "Phase 2I technical tier counts",
    )
    return TechnicalInventoryResult(restricted_rows, safe_tier_counts, safe_summary, output_root)


def _display_frame_grid(frames: np.ndarray, tile_size: int = 224) -> np.ndarray:
    import cv2

    if frames.ndim != 4 or frames.shape[0] != 16:
        raise ValueError(f"audit display requires exactly 16 frames, got {frames.shape}")
    tiles: list[np.ndarray] = []
    for frame in frames:
        image = np.asarray(frame)
        if image.ndim == 2:
            image = np.repeat(image[..., None], 3, axis=2)
        if image.ndim != 3 or image.shape[-1] != 3:
            raise ValueError(f"unsupported audit frame shape {image.shape}")
        if image.dtype != np.uint8:
            image = np.clip(
                np.nan_to_num(image.astype(np.float32), nan=0.0, posinf=255.0, neginf=0.0),
                0,
                255,
            ).astype(np.uint8)
        tiles.append(cv2.resize(image, (tile_size, tile_size), interpolation=cv2.INTER_AREA))
    return np.concatenate(
        [np.concatenate(tiles[row * 4 : (row + 1) * 4], axis=1) for row in range(4)],
        axis=0,
    )


def _opaque_media_id(audit_id: str, clip_audit_id: str, role: str) -> str:
    payload = f"jdim-phase2i-media-v1\0{audit_id}\0{clip_audit_id}\0{role}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def build_audit_media(
    *,
    technical_inventory_csv: Path,
    output_root: Path,
) -> AuditMediaResult:
    """Render opaque, restricted audit media without classifying clinical content."""

    output_root = require_restricted_destination(output_root)
    if output_root.exists():
        raise FileExistsError("refusing to overwrite Phase 2I audit media")
    rows = pd.read_csv(technical_inventory_csv).fillna("")
    require_columns(
        rows,
        [
            "audit_id",
            "clip_audit_id",
            "evidence_tier",
            "source_viewable",
            "source_path",
            "processed_path",
            "source_model_frame_indices_json",
        ],
        "Phase 2I technical inventory",
    )
    if rows[["audit_id", "clip_audit_id"]].astype(str).duplicated().any():
        raise Tier1BlockedError(BLOCKED_PHASE2I_SOURCE_MISMATCH, "technical inventory has duplicate clips")
    media_root = output_root / "media"
    media_root.mkdir(parents=True, mode=0o700)
    os.chmod(output_root, 0o700)
    os.chmod(media_root, 0o700)
    rendered: list[dict[str, Any]] = []
    for _, row in rows.iterrows():
        audit_id = str(row["audit_id"])
        clip_audit_id = str(row["clip_audit_id"])
        tier = str(row["evidence_tier"])
        source_media_id = ""
        model_input_media_id = ""
        if _truth(row["source_viewable"]):
            import cv2
            import pydicom

            from extract_mimic_echo_cines import normalize_pixels

            source_path = Path(str(row["source_path"]))
            dataset = pydicom.dcmread(str(source_path), stop_before_pixels=False)
            raw = normalize_pixels(dataset)
            source_indices = [int(value) for value in json.loads(str(row["source_model_frame_indices_json"]))]
            if len(source_indices) != 16 or any(value < 0 or value >= len(raw) for value in source_indices):
                raise Tier1BlockedError(
                    BLOCKED_PHASE2I_SOURCE_MISMATCH,
                    "technical inventory contains invalid source display indices",
                )
            source_grid = _display_frame_grid(raw[source_indices])
            source_media_id = _opaque_media_id(audit_id, clip_audit_id, "source")
            source_destination = media_root / f"{source_media_id}.png"
            if not cv2.imwrite(str(source_destination), cv2.cvtColor(source_grid, cv2.COLOR_RGB2BGR)):
                raise OSError(f"failed to write restricted source display {source_media_id}")
            os.chmod(source_destination, 0o600)
        if tier in {EXACT_MODEL_INPUT, VERIFIED_EQUIVALENT_REPLAY}:
            import cv2

            processed_path = Path(str(row["processed_path"]))
            if not processed_path.is_file():
                raise Tier1BlockedError(
                    BLOCKED_PHASE2I_SOURCE_MISMATCH,
                    "verified model-input tier lacks a retained processed input",
                )
            with np.load(processed_path, allow_pickle=False) as archive:
                stored = np.asarray(archive["frames"])
            model_frames = stored[list(MODEL_SOURCE_FRAME_POSITIONS)]
            model_grid = _display_frame_grid(model_frames)
            model_input_media_id = _opaque_media_id(audit_id, clip_audit_id, "model")
            model_destination = media_root / f"{model_input_media_id}.png"
            if not cv2.imwrite(str(model_destination), cv2.cvtColor(model_grid, cv2.COLOR_RGB2BGR)):
                raise OSError(f"failed to write restricted model display {model_input_media_id}")
            os.chmod(model_destination, 0o600)
        rendered.append(
            {
                "audit_id": audit_id,
                "clip_audit_id": clip_audit_id,
                "evidence_tier": tier,
                "source_media_id": source_media_id,
                "model_input_media_id": model_input_media_id,
                "model_input_verified": tier in {EXACT_MODEL_INPUT, VERIFIED_EQUIVALENT_REPLAY},
                "source_only": tier == SOURCE_ACQUISITION_ONLY,
            }
        )
    manifest = pd.DataFrame(rendered)
    manifest_path = output_root / "technical_interface_manifest_restricted.csv"
    manifest.to_csv(manifest_path, index=False)
    summary = {
        "status": "AUDIT_MEDIA_READY",
        "clips": int(len(manifest)),
        "source_displays": int(manifest["source_media_id"].astype(bool).sum()),
        "verified_model_input_displays": int(
            manifest["model_input_media_id"].astype(bool).sum()
        ),
        "source_only_displays": int(manifest["source_only"].astype(bool).sum()),
        "media_size_bytes": int(
            sum(path.stat().st_size for path in media_root.glob("*.png") if path.is_file())
        ),
        "ocr_used": False,
        "automated_content_annotation": False,
        "technical_inventory_sha256": sha256_file(technical_inventory_csv),
        "technical_interface_manifest_sha256": sha256_file(manifest_path),
    }
    write_json(output_root / "audit_media_summary.json", summary)
    return AuditMediaResult(manifest, summary, output_root)


def verify_locked_phase2i_counts(
    *,
    roster_lock_json: Path,
    source_availability_json: Path,
) -> dict[str, Any]:
    roster = json.loads(roster_lock_json.read_text(encoding="utf-8"))
    availability = json.loads(source_availability_json.read_text(encoding="utf-8"))
    expected = {
        "configuration_sha256": "8767763cb425dee2a21c2d3b7540cfb2d83aaccfac6f0771f82697641bb23673",
        "target_sample_counts": {"lvot_vti": 60, "tapse": 60},
        "cross_target_overlap_studies": 4,
        "unique_physical_studies_selected": 116,
        "second_reader_studies": 24,
        "primary_clip_reads_expected": 5071,
        "secondary_clip_reads_expected": 1043,
    }
    mismatches = {key: (roster.get(key), value) for key, value in expected.items() if roster.get(key) != value}
    availability_expected = {
        "locked_roster_studies": 116,
        "locked_roster_clips": 5071,
        "studies_technically_ready_for_reconstruction": 7,
        "studies_requiring_secure_restoration": 109,
        "studies_lacking_unique_linkage": 0,
    }
    mismatches.update(
        {
            f"availability.{key}": (availability.get(key), value)
            for key, value in availability_expected.items()
            if availability.get(key) != value
        }
    )
    if mismatches:
        raise Tier1BlockedError(
            BLOCKED_PHASE2I_SOURCE_MISMATCH,
            f"locked Phase 2I counts differ: {mismatches}",
        )
    return {"status": "PHASE2I_LOCKED_COUNTS_VERIFIED", **expected, **availability_expected}
