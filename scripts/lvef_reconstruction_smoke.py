#!/usr/bin/env python3
"""Prospective, model-independent-to-embedding reconstruction smoke pipeline.

This module deliberately has no label, prediction, or model-fitting interface.  Row-
level manifests are restricted outputs; aggregate outputs contain counts and gate
states only.  Heavy optional dependencies (pydicom, OpenCV, torch, torchvision) are
imported only inside the subcommand that needs them so dependency-light tests can
exercise provenance and ordering logic.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import random
import re
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd


CHECKPOINT_FILENAME = "echo_prime_encoder.pt"
CHECKPOINT_BYTES = 138_642_379
CHECKPOINT_SHA256 = "7ca32e8bfde248bd6d8c7e46fdb7440385169af4dc2f416b5de840bdc2e64f3b"
CLIP_KEY_NAMESPACE = "mimic-iv-echo-1.0:prospective-cine-v1"
EXTRACTION_SHAPE = (32, 224, 224, 3)
EMBEDDING_WIDTH = 512
TEMPORAL_SAMPLING_POLICY = "historical_compatible_linspace_or_tail_repeat_v1"
DECODER_PLUGIN_PRIORITY = ("pylibjpeg", "gdcm", "pillow", "pyjpegls")
NATIVE_PIXEL_TRANSFER_SYNTAX_UIDS = {
    "1.2.840.10008.1.2",       # Implicit VR Little Endian
    "1.2.840.10008.1.2.1",     # Explicit VR Little Endian
    "1.2.840.10008.1.2.1.99",  # Deflated Explicit VR Little Endian
    "1.2.840.10008.1.2.2",     # Explicit VR Big Endian
}
SUPPORTED_PHOTOMETRICS = {
    "MONOCHROME1",
    "MONOCHROME2",
    "RGB",
    "YBR_FULL",
    "YBR_FULL_422",
}
EXPECTED_SMOKE_ROLES = (
    "batch_000_duplicate_affected_or_prespecified_fallback",
    "batch_001_008_historical_cine_positive",
    "stage_d_historical_cine_positive_with_surviving_npz",
    "historical_no_cine_negative_control",
)
NEGATIVE_CONTROL_ROLE = "historical_no_cine_negative_control"
POSITIVE_CONTROL_ROLES = tuple(role for role in EXPECTED_SMOKE_ROLES if role != NEGATIVE_CONTROL_ROLE)
EXPECTED_MANIFEST_PAIR_ALIASES = {"clip_manifest", "study_manifest"}
EXPECTED_ARRAY_PAIR_ALIASES = {"clip_embeddings", "study_embeddings"}
EXPECTED_EXTRACTION_PAIR_ALIASES = {"extraction"}
MEAN = np.asarray([29.110628, 28.076836, 29.096405], dtype=np.float32)
STD = np.asarray([47.989223, 46.456997, 47.20083], dtype=np.float32)
HEX64 = re.compile(r"^[0-9a-f]{64}$")
TRUE_VALUES = {"true", "1", "yes", "y"}
FALSE_VALUES = {"false", "0", "no", "n"}


def _technical_count_key(value: Any, *, kind: str) -> str:
    """Return a Git-safe technical enumeration key, never a row locator."""

    if value is None or pd.isna(value):
        return "MISSING"
    text = str(value).strip()
    if kind == "photometric":
        normalized = text.upper().replace(" ", "_")
        return normalized if re.fullmatch(r"[A-Z0-9_]{1,40}", normalized) else "INVALID"
    if kind == "transfer_syntax":
        return text if re.fullmatch(r"[0-9]+(?:\.[0-9]+)+", text) else "INVALID"
    if kind in {"decoder", "color_transform"}:
        return text if re.fullmatch(r"[A-Za-z0-9_.:+-]{1,120}", text) else "INVALID"
    raise ValueError("Unsupported technical-count kind.")


def _technical_counts(frame: pd.DataFrame, column: str, *, kind: str) -> dict[str, int]:
    if column not in frame.columns:
        return {}
    keys = frame[column].map(lambda value: _technical_count_key(value, kind=kind))
    counts = keys.value_counts(dropna=False).to_dict()
    return {str(key): int(counts[key]) for key in sorted(counts)}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def array_content_sha256(array: np.ndarray) -> str:
    """Hash logical array content independently of NPZ container metadata."""

    value = np.asarray(array)
    if value.dtype.hasobject:
        raise ValueError("Object arrays are prohibited.")
    canonical_dtype = value.dtype.newbyteorder("<")
    canonical = np.ascontiguousarray(value.astype(canonical_dtype, copy=False))
    header = json.dumps(
        {"dtype": canonical.dtype.str, "shape": list(canonical.shape)},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return hashlib.sha256(header + b"\n" + canonical.tobytes(order="C")).hexdigest()


def safe_relative_path(value: Any) -> str:
    text = str(value)
    if not text or text != text.strip() or "\\" in text or any(ord(c) < 32 for c in text):
        raise ValueError("Source locator is not a canonical safe relative path.")
    path = PurePosixPath(text)
    if path.is_absolute() or text.startswith("/"):
        raise ValueError("Absolute source paths are prohibited.")
    if any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("Traversal or noncanonical path components are prohibited.")
    normalized = path.as_posix()
    if normalized != text:
        raise ValueError("Source locator is not POSIX-normalized.")
    return normalized


def resolve_under(root: Path, relative: str, *, must_exist: bool = False) -> Path:
    relative = safe_relative_path(relative)
    root_resolved = root.resolve(strict=True)
    candidate = root_resolved.joinpath(*PurePosixPath(relative).parts)
    resolved = candidate.resolve(strict=must_exist)
    try:
        resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError("Resolved path escapes the declared root.") from exc
    if candidate.is_symlink() or (must_exist and resolved.is_symlink()):
        raise ValueError("Symlinked source objects are prohibited.")
    return resolved


def require_outside_repository_output(path: Path) -> None:
    """Fail closed if a row-level or array output could land in this Git tree."""

    if not path.is_absolute():
        raise ValueError("Restricted outputs must use absolute paths.")
    repository = Path(__file__).resolve().parents[1]
    resolved = path.resolve(strict=False)
    try:
        resolved.relative_to(repository)
    except ValueError:
        return
    raise ValueError("Restricted row or array output may not be written inside the repository.")


def parse_bool(value: Any) -> bool:
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if value is None or pd.isna(value):
        raise ValueError("Missing boolean value.")
    normalized = str(value).strip().lower()
    if normalized in TRUE_VALUES:
        return True
    if normalized in FALSE_VALUES:
        return False
    raise ValueError(f"Invalid boolean token: {normalized!r}")


def stable_clip_key(source_relative_path: str) -> str:
    relative = safe_relative_path(source_relative_path)
    return hashlib.sha256(f"{CLIP_KEY_NAMESPACE}\0{relative}".encode("utf-8")).hexdigest()


def write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError("Refusing to overwrite an existing output.")
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    with temporary.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def write_csv_atomic(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError("Refusing to overwrite an existing output.")
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    frame.to_csv(temporary, index=False, lineterminator="\n")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def write_npz_atomic(path: Path, **arrays: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError("Refusing to overwrite an existing output.")
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}.npz")
    np.savez_compressed(temporary, **arrays)
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def read_release_checksums(path: Path) -> dict[str, str]:
    checksums: dict[str, str] = {}
    with path.open(encoding="utf-8", errors="strict") as handle:
        for line_number, raw in enumerate(handle, start=1):
            line = raw.rstrip("\n")
            if not line.strip():
                continue
            match = re.fullmatch(r"([0-9A-Fa-f]{64})[ \t]+\*?(.+)", line)
            if not match:
                raise ValueError(f"Malformed checksum line {line_number}.")
            digest = match.group(1).lower()
            relative_raw = match.group(2).strip()
            while relative_raw.startswith("./"):
                relative_raw = relative_raw[2:]
            relative = safe_relative_path(relative_raw)
            if relative in checksums:
                raise ValueError("Duplicate release-checksum path.")
            checksums[relative] = digest
    if not checksums:
        raise ValueError("Release checksum file contains no entries.")
    return checksums


def _validate_source_manifest(frame: pd.DataFrame) -> pd.DataFrame:
    required = {"subject_id", "study_id", "split", "source_relative_path", "smoke_role"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"Source manifest missing required columns: {missing}")
    out = frame.copy()
    if out.empty:
        raise ValueError("Source manifest is empty.")
    if out[list(required)].isna().any().any():
        raise ValueError("Source manifest contains missing authority fields.")
    out["source_relative_path"] = out["source_relative_path"].map(safe_relative_path)
    out["smoke_role"] = out["smoke_role"].astype(str).str.strip()
    out["split"] = out["split"].astype(str).str.strip().str.lower()
    if set(out["split"]) != {"train"}:
        raise ValueError("Reconstruction smoke manifest must be train-only.")
    if not out["source_relative_path"].str.lower().str.endswith(".dcm").all():
        raise ValueError("Prospective source manifest may contain DICOM objects only.")
    if out["source_relative_path"].duplicated().any():
        raise ValueError("Source manifest has duplicate release-relative objects.")
    ownership = out.groupby("study_id", dropna=False)["subject_id"].nunique(dropna=False)
    if not ownership.le(1).all():
        raise ValueError("A study is assigned to multiple subjects.")
    study_roles = out.groupby("study_id", dropna=False)["smoke_role"].nunique(dropna=False)
    if not study_roles.eq(1).all():
        raise ValueError("A smoke study is assigned to multiple technical roles.")
    role_studies = out.groupby("smoke_role", dropna=False)["study_id"].nunique(dropna=False)
    if set(role_studies.index) != set(EXPECTED_SMOKE_ROLES) or not role_studies.eq(1).all():
        raise ValueError("Smoke manifest must contain exactly one study for each expected role.")
    if out["study_id"].nunique() != 4 or out["subject_id"].nunique() != 4:
        raise ValueError("Smoke manifest must contain exactly four distinct studies and subjects.")
    return out.sort_values("source_relative_path", kind="mergesort").reset_index(drop=True)


def audit_downloaded_objects(
    source_manifest: pd.DataFrame,
    release_checksums: Mapping[str, str],
    download_root: Path,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    source = _validate_source_manifest(source_manifest)
    release = {safe_relative_path(key): str(value).lower() for key, value in release_checksums.items()}
    if any(not HEX64.fullmatch(value) for value in release.values()):
        raise ValueError("Release checksum map contains an invalid SHA-256 value.")

    rows: list[dict[str, Any]] = []
    for record in source.to_dict(orient="records"):
        relative = record["source_relative_path"]
        expected = release.get(relative)
        status = "PASS"
        actual: str | None = None
        size: int | None = None
        error_code: str | None = None
        try:
            source_path = resolve_under(download_root, relative, must_exist=True)
            if not source_path.is_file():
                raise FileNotFoundError
            size = int(source_path.stat().st_size)
            actual = sha256_file(source_path)
            if expected is None:
                status, error_code = "FAIL", "MISSING_RELEASE_CHECKSUM"
            elif actual != expected:
                status, error_code = "FAIL", "SHA256_MISMATCH"
        except FileNotFoundError:
            status, error_code = "FAIL", "MISSING_DOWNLOADED_OBJECT"
        except ValueError:
            status, error_code = "FAIL", "UNSAFE_OR_SYMLINKED_OBJECT"
        rows.append(
            {
                "subject_id": record["subject_id"],
                "study_id": record["study_id"],
                "smoke_role": record["smoke_role"],
                "source_relative_path": relative,
                "expected_sha256": expected,
                "observed_sha256": actual,
                "file_size_bytes": size,
                "download_ok": status == "PASS",
                "error_code": error_code,
            }
        )

    expected_set = set(source["source_relative_path"])
    discovered: set[str] = set()
    root_resolved = download_root.resolve(strict=True)
    n_unsafe_discovered = 0
    for candidate in root_resolved.rglob("*"):
        if candidate.suffix.lower() != ".dcm":
            continue
        if candidate.is_symlink():
            n_unsafe_discovered += 1
            continue
        if candidate.is_file():
            discovered.add(candidate.relative_to(root_resolved).as_posix())
    unexpected = discovered - expected_set
    missing = expected_set - discovered
    audit = pd.DataFrame(rows).sort_values("source_relative_path", kind="mergesort").reset_index(drop=True)
    all_rows_pass = bool(audit["download_ok"].map(parse_bool).all())
    summary = {
        "audit": "prospective_downloaded_object_audit",
        "status": (
            "PASS"
            if all_rows_pass and not unexpected and not missing and n_unsafe_discovered == 0
            else "FAIL"
        ),
        "n_expected_objects": int(len(expected_set)),
        "n_release_checksums_matched": int(audit["expected_sha256"].notna().sum()),
        "n_download_objects_discovered": int(len(discovered)),
        "n_verified_objects": int(audit["download_ok"].map(parse_bool).sum()),
        "n_missing_objects": int(len(missing)),
        "n_unexpected_objects": int(len(unexpected)),
        "n_unsafe_symlink_objects": int(n_unsafe_discovered),
        "n_checksum_mismatches": int((audit["error_code"] == "SHA256_MISMATCH").sum()),
        "n_smoke_roles": int(audit["smoke_role"].nunique()),
        "smoke_role_set_exact": set(audit["smoke_role"]) == set(EXPECTED_SMOKE_ROLES),
        "objects_by_smoke_role": {
            role: int((audit["smoke_role"] == role).sum()) for role in EXPECTED_SMOKE_ROLES
        },
        "source_manifest_sha256": _normalized_manifest_hash(source),
        "row_values_emitted": False,
        "paths_emitted": False,
    }
    return audit, summary


def _dicom_header_row(record: Mapping[str, Any], download_root: str) -> dict[str, Any]:
    import pydicom  # optional dependency; intentionally local

    relative = safe_relative_path(record["source_relative_path"])
    base = {
        "subject_id": record["subject_id"],
        "study_id": record["study_id"],
        "smoke_role": record["smoke_role"],
        "source_relative_path": relative,
        "download_sha256": record.get("observed_sha256"),
        "read_ok": False,
        "is_multiframe": False,
        "number_of_frames": None,
        "rows": None,
        "columns": None,
        "samples_per_pixel": None,
        "bits_allocated": None,
        "bits_stored": None,
        "photometric_interpretation": None,
        "transfer_syntax_uid": None,
        "error_code": None,
    }
    try:
        path = resolve_under(Path(download_root), relative, must_exist=True)
        ds = pydicom.dcmread(str(path), stop_before_pixels=True, force=False)
        frames = int(getattr(ds, "NumberOfFrames", 1) or 1)
        base.update(
            {
                "read_ok": True,
                "is_multiframe": frames > 1,
                "number_of_frames": frames,
                "rows": _optional_int(getattr(ds, "Rows", None)),
                "columns": _optional_int(getattr(ds, "Columns", None)),
                "samples_per_pixel": _optional_int(getattr(ds, "SamplesPerPixel", None)),
                "bits_allocated": _optional_int(getattr(ds, "BitsAllocated", None)),
                "bits_stored": _optional_int(getattr(ds, "BitsStored", None)),
                "photometric_interpretation": str(getattr(ds, "PhotometricInterpretation", "")),
                "transfer_syntax_uid": str(getattr(ds.file_meta, "TransferSyntaxUID", "")),
            }
        )
    except Exception as exc:  # row-level details remain restricted
        base["error_code"] = type(exc).__name__
    return base


def _optional_int(value: Any) -> int | None:
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None


def summarize_dicom_audit(frame: pd.DataFrame) -> dict[str, Any]:
    required = {
        "read_ok", "is_multiframe", "study_id", "subject_id", "source_relative_path", "smoke_role"
    }
    if not required.issubset(frame.columns):
        raise ValueError("DICOM audit frame is missing required columns.")
    read_ok = frame["read_ok"].map(parse_bool)
    cine = frame["is_multiframe"].map(parse_bool) & read_ok
    observed_roles = set(frame["smoke_role"].astype(str))
    role_set_exact = observed_roles == set(EXPECTED_SMOKE_ROLES)
    cine_by_role = {
        role: int((cine & frame["smoke_role"].eq(role)).sum()) for role in EXPECTED_SMOKE_ROLES
    }
    positive_roles_pass = all(cine_by_role[role] >= 1 for role in POSITIVE_CONTROL_ROLES)
    negative_role_pass = cine_by_role[NEGATIVE_CONTROL_ROLE] == 0
    role_study_counts = {
        role: int(frame.loc[frame["smoke_role"].eq(role), "study_id"].nunique())
        for role in EXPECTED_SMOKE_ROLES
    }
    exactly_one_study_per_role = all(value == 1 for value in role_study_counts.values())
    passed = bool(
        read_ok.all()
        and role_set_exact
        and exactly_one_study_per_role
        and positive_roles_pass
        and negative_role_pass
    )
    return {
        "audit": "prospective_dicom_header_audit",
        "status": "PASS" if passed else "FAIL",
        "n_objects": int(len(frame)),
        "n_read_ok": int(read_ok.sum()),
        "n_read_failed": int((~read_ok).sum()),
        "n_cine_candidates": int(cine.sum()),
        "n_single_frame": int((read_ok & ~cine).sum()),
        "n_studies": int(frame["study_id"].nunique()),
        "n_subjects": int(frame["subject_id"].nunique()),
        "n_studies_with_cine": int(frame.loc[cine, "study_id"].nunique()),
        "n_smoke_roles": int(frame["smoke_role"].nunique()),
        "smoke_role_set_exact": role_set_exact,
        "exactly_one_study_per_smoke_role": exactly_one_study_per_role,
        "cine_candidates_by_smoke_role": cine_by_role,
        "positive_control_role_cine_gate_passed": positive_roles_pass,
        "negative_control_zero_cine_gate_passed": negative_role_pass,
        "photometric_interpretation_counts": _technical_counts(
            frame.loc[read_ok], "photometric_interpretation", kind="photometric"
        ),
        "transfer_syntax_uid_counts": _technical_counts(
            frame.loc[read_ok], "transfer_syntax_uid", kind="transfer_syntax"
        ),
        "row_values_emitted": False,
        "paths_emitted": False,
    }


def audit_dicom_headers(
    download_audit: pd.DataFrame, download_root: Path, workers: int = 1
) -> tuple[pd.DataFrame, dict[str, Any]]:
    if workers < 1:
        raise ValueError("workers must be at least one.")
    required = {"subject_id", "study_id", "smoke_role", "source_relative_path", "download_ok"}
    if not required.issubset(download_audit.columns):
        raise ValueError("Download audit is missing required columns.")
    records = download_audit[download_audit["download_ok"].map(parse_bool)].to_dict(orient="records")
    if not records:
        raise ValueError("No checksum-verified DICOM objects are available.")
    if workers == 1:
        rows = [_dicom_header_row(record, str(download_root.resolve())) for record in records]
    else:
        rows = []
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(_dicom_header_row, record, str(download_root.resolve())) for record in records]
            for future in as_completed(futures):
                rows.append(future.result())
    frame = pd.DataFrame(rows).sort_values("source_relative_path", kind="mergesort").reset_index(drop=True)
    return frame, summarize_dicom_audit(frame)


def temporal_sample(frames: np.ndarray, target_frames: int = 32) -> tuple[np.ndarray, np.ndarray]:
    """Apply the frozen historical sampling rule without outcome-dependent choices.

    Long cines use endpoint-inclusive integer ``linspace`` indices.  Short cines
    retain every source frame in order and repeat only the final frame.  This is
    intentionally historical-compatible rather than a newly optimized sampler;
    the exact policy identifier is emitted in aggregate extraction metadata.
    """

    if frames.ndim != 4 or frames.shape[-1] != 3 or frames.shape[0] < 1:
        raise ValueError("Expected nonempty T,H,W,3 frames.")
    if target_frames < 1:
        raise ValueError("target_frames must be positive.")
    if frames.shape[0] >= target_frames:
        indices = np.linspace(0, frames.shape[0] - 1, target_frames, dtype=np.int64)
    else:
        tail = np.full(target_frames - frames.shape[0], frames.shape[0] - 1, dtype=np.int64)
        indices = np.concatenate([np.arange(frames.shape[0], dtype=np.int64), tail])
    return np.ascontiguousarray(frames[indices]), indices


def _preferred_decoder_plugin(pydicom_module: Any, transfer_syntax_uid: str) -> str | None:
    """Choose a decoder deterministically when the pydicom 3 raw API exposes plugins."""

    pixels_api = getattr(pydicom_module, "pixels", None)
    get_decoder = getattr(pixels_api, "get_decoder", None)
    if not callable(get_decoder):
        return None
    try:
        decoder = get_decoder(transfer_syntax_uid)
        available = tuple(str(item) for item in getattr(decoder, "available_plugins", ()))
    except Exception:
        return None
    for candidate in DECODER_PLUGIN_PRIORITY:
        if candidate in available:
            return candidate
    return sorted(available)[0] if available else None


def _normalize_dicom_pixels(ds: Any, pydicom_module: Any) -> tuple[np.ndarray, dict[str, str]]:
    """Decode a cine into canonical RGB with explicit decoder/color provenance.

    Pixels are requested as stored-color (`raw=True`) through the pydicom 3 API and
    YBR is converted exactly once here.  Legacy Dataset.pixel_array decoding is
    prohibited because its implicit YBR conversion and handler selection cannot be
    represented as prospective stored-color authority.  Monochrome data are
    replicated directly into RGB and never passed through a YUV transform.
    """

    photometric = str(getattr(ds, "PhotometricInterpretation", "")).strip().upper()
    if photometric not in SUPPORTED_PHOTOMETRICS:
        raise ValueError("Unsupported or missing PhotometricInterpretation.")
    transfer_syntax_uid = str(getattr(getattr(ds, "file_meta", None), "TransferSyntaxUID", ""))
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)+", transfer_syntax_uid):
        raise ValueError("Missing or invalid TransferSyntaxUID.")

    pixels_api = getattr(pydicom_module, "pixels", None)
    raw_pixel_array = getattr(pixels_api, "pixel_array", None)
    if not callable(raw_pixel_array):
        raise ValueError("Prospective extraction requires the pydicom 3 raw pixel API.")
    is_native = transfer_syntax_uid in NATIVE_PIXEL_TRANSFER_SYNTAX_UIDS
    plugin = None if is_native else _preferred_decoder_plugin(pydicom_module, transfer_syntax_uid)
    if not is_native and plugin is None:
        raise ValueError("Compressed transfer syntax has no deterministic available decoder plugin.")
    decode_kwargs: dict[str, Any] = {"raw": True}
    if plugin is not None:
        decode_kwargs["decoding_plugin"] = plugin
    pixels = np.asarray(raw_pixel_array(ds, **decode_kwargs))
    decoder_backend = f"pydicom_pixels_raw:{plugin or 'native'}"

    frames = int(getattr(ds, "NumberOfFrames", 1) or 1)
    samples = int(getattr(ds, "SamplesPerPixel", 1) or 1)
    bits = int(getattr(ds, "BitsAllocated", pixels.dtype.itemsize * 8) or pixels.dtype.itemsize * 8)
    bits_stored = int(getattr(ds, "BitsStored", bits) or bits)
    if frames <= 1:
        raise ValueError("Not a multiframe cine.")
    if bits != 8 or bits_stored != 8 or pixels.dtype != np.uint8:
        raise ValueError("Only decoded uint8 cines with BitsAllocated=BitsStored=8 are permitted.")
    if samples == 1 and pixels.ndim == 3 and pixels.shape[0] == frames:
        if photometric not in {"MONOCHROME1", "MONOCHROME2"}:
            raise ValueError("Single-sample cine has a non-monochrome photometric interpretation.")
        if photometric == "MONOCHROME1":
            pixels = np.uint8(255) - pixels
            color_transform = "MONOCHROME1_INVERT_REPLICATE_TO_RGB"
        else:
            color_transform = "MONOCHROME2_REPLICATE_TO_RGB"
        pixels = np.repeat(pixels[..., None], 3, axis=3)
    elif samples == 3 and pixels.ndim == 4 and pixels.shape[0] == frames and pixels.shape[-1] == 3:
        if photometric == "RGB":
            color_transform = "NONE_RGB"
        elif photometric in {"YBR_FULL", "YBR_FULL_422"}:
            convert_color_space = getattr(pixels_api, "convert_color_space", None)
            if not callable(convert_color_space):
                raise ValueError("Raw YBR decode is available but explicit color conversion is not.")
            pixels = np.asarray(convert_color_space(pixels, photometric, "RGB"))
            color_transform = f"EXPLICIT_{photometric}_TO_RGB"
        else:
            raise ValueError("Three-sample cine has a non-color photometric interpretation.")
    else:
        raise ValueError("Unsupported decoded multiframe pixel layout.")
    if pixels.dtype != np.uint8:
        raise ValueError("Color conversion did not preserve uint8 output.")
    metadata = {
        "photometric_interpretation": photometric,
        "transfer_syntax_uid": transfer_syntax_uid,
        "decoder_backend": decoder_backend,
        "decoder_color_behavior": "STORED_COLOR_RAW",
        "color_transform": color_transform,
        "canonical_color_space": "RGB",
    }
    return np.ascontiguousarray(pixels, dtype=np.uint8), metadata


def _rgb_luma_uint8(frames: np.ndarray) -> np.ndarray:
    """Convert canonical RGB to deterministic BT.601-like integer luma."""

    value = np.asarray(frames)
    if value.ndim < 3 or value.shape[-1] != 3 or value.dtype != np.uint8:
        raise ValueError("Luma conversion requires uint8 RGB input.")
    widened = value.astype(np.uint16)
    # Coefficients sum to 256; rounding is explicit and platform-independent.
    luma = (
        77 * widened[..., 0] + 150 * widened[..., 1] + 29 * widened[..., 2] + 128
    ) >> 8
    return np.asarray(luma, dtype=np.uint8)


def _mask_ultrasound_strict(original: np.ndarray, cv2: Any) -> tuple[np.ndarray, np.ndarray]:
    if original.ndim != 4 or original.shape[-1] != 3:
        raise ValueError("Masking requires T,H,W,3 frames.")
    source = np.copy(original)
    gray_source = _rgb_luma_uint8(source)
    frame_sum = np.where(gray_source[0] > 0, 1, 0)
    for gray in gray_source:
        frame_sum = np.add(frame_sum, np.where(gray > 0, 1, 0))
    kernel = np.ones((3, 3), np.uint8)
    # The historical algorithm uses only zero/nonzero occupancy downstream.  Make
    # that binarization explicit before uint8 conversion so long cines cannot wrap
    # an accumulated count at 256 and spuriously erase occupied pixels.
    frame_sum = cv2.erode(np.where(frame_sum > 0, 1, 0).astype(np.uint8), kernel, iterations=10)
    frame_sum = np.where(frame_sum > 0, 1, 0)
    first = gray_source[0].astype(np.int16)
    last = gray_source[-1].astype(np.int16)
    difference = np.where(np.abs(first - last) > 0, 1, 0)
    difference[0:20, 0:20] = 0
    overlap = np.where(np.add(frame_sum, difference) > 1, 1, 0)
    overlap = cv2.dilate(np.uint8(overlap), kernel, iterations=10).astype(np.uint8)
    cv2.floodFill(overlap, None, (0, 0), 100)
    overlap = np.where(overlap != 100, 255, 0).astype(np.uint8)
    contours, _ = cv2.findContours(overlap, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
    for contour in contours:
        hull = cv2.convexHull(contour)
        cv2.drawContours(overlap, [hull], -1, (255, 0, 0), 3)
    overlap = np.where(overlap > 0, 1, 0).astype(np.uint8)
    cv2.floodFill(overlap, None, (0, 0), 100)
    mask = np.asarray(np.where(overlap != 100, 1, 0), dtype=bool)
    output = np.where(mask[None, ..., None], source, np.uint8(0))
    return np.ascontiguousarray(output, dtype=np.uint8), np.ascontiguousarray(mask)


def _signal_quality_metrics(frames: np.ndarray, sector_mask: np.ndarray | None = None) -> dict[str, Any]:
    """Compute strict, threshold-free degenerate-cine gates before extraction authority."""

    value = np.asarray(frames)
    if value.ndim != 4 or value.shape[-1] != 3 or value.dtype != np.uint8:
        raise ValueError("Signal gates require uint8 T,H,W,3 frames.")
    if value.shape[0] < 2:
        raise ValueError("Temporal-variation gate requires at least two source frames.")
    sector_pixels: int | None = None
    sector_nonempty = True
    if sector_mask is not None:
        mask = np.asarray(sector_mask)
        if mask.shape != value.shape[1:3] or mask.dtype != np.bool_:
            raise ValueError("Sector mask must be boolean H,W and match the frames.")
        sector_pixels = int(np.count_nonzero(mask))
        sector_nonempty = sector_pixels > 0
        retained = value[:, mask, :]
    else:
        retained = value
    nonzero_retained = int(np.count_nonzero(np.any(retained != 0, axis=-1)))
    temporal_delta = value[1:].astype(np.int16) - value[:-1].astype(np.int16)
    temporal_variation = int(np.count_nonzero(np.any(temporal_delta != 0, axis=-1)))
    return {
        "sector_pixel_count": sector_pixels,
        "sector_nonempty_gate_passed": bool(sector_nonempty),
        "nonzero_retained_pixel_count": nonzero_retained,
        "nonzero_retained_pixel_gate_passed": nonzero_retained > 0,
        "temporal_variation_pixel_count": temporal_variation,
        "temporal_variation_gate_passed": temporal_variation > 0,
    }


def _require_signal_quality(metrics: Mapping[str, Any]) -> None:
    gates = (
        "sector_nonempty_gate_passed",
        "nonzero_retained_pixel_gate_passed",
        "temporal_variation_gate_passed",
    )
    if not all(bool(metrics[key]) for key in gates):
        raise ValueError("Nonempty-sector, retained-signal, or temporal-variation gate failed.")


def _crop_resize(frame: np.ndarray, cv2: Any, size: int = 224, zoom: float = 0.1) -> np.ndarray:
    height, width = frame.shape[:2]
    ratio_in = width / height
    if ratio_in > 1.0:
        padding = int(round((width - height) / 2))
        if padding > 0:
            frame = frame[:, padding:-padding]
    elif ratio_in < 1.0:
        padding = int(round((height - width) / 2))
        if padding > 0:
            frame = frame[padding:-padding]
    pad_x = round(int(frame.shape[1] * zoom))
    pad_y = round(int(frame.shape[0] * zoom))
    if pad_x <= 0 or pad_y <= 0 or 2 * pad_x >= frame.shape[1] or 2 * pad_y >= frame.shape[0]:
        raise ValueError("Invalid zoom crop for decoded frame dimensions.")
    frame = frame[pad_y:-pad_y, pad_x:-pad_x]
    return cv2.resize(frame, (size, size), interpolation=cv2.INTER_CUBIC)


def _extract_one(record: Mapping[str, Any], download_root: str, output_root: str) -> dict[str, Any]:
    import cv2  # optional dependencies; intentionally local
    import pydicom

    cv2.setNumThreads(1)
    relative = safe_relative_path(record["source_relative_path"])
    clip_key = stable_clip_key(relative)
    output_relative = f"clips/{clip_key[:2]}/{clip_key}.npz"
    result = {
        "subject_id": record["subject_id"],
        "study_id": record["study_id"],
        "smoke_role": record["smoke_role"],
        "source_relative_path": relative,
        "source_sha256": record.get("download_sha256"),
        "clip_key": clip_key,
        "output_relative_path": output_relative,
        "write_ok": False,
        "mask_status": "NOT_REACHED",
        "photometric_interpretation": None,
        "transfer_syntax_uid": None,
        "decoder_backend": None,
        "decoder_color_behavior": None,
        "color_transform": None,
        "canonical_color_space": None,
        "source_sector_pixel_count": None,
        "source_sector_nonempty_gate_passed": False,
        "source_nonzero_retained_pixel_count": None,
        "source_nonzero_retained_pixel_gate_passed": False,
        "source_temporal_variation_pixel_count": None,
        "source_temporal_variation_gate_passed": False,
        "sampled_nonzero_retained_pixel_count": None,
        "sampled_nonzero_retained_pixel_gate_passed": False,
        "sampled_temporal_variation_pixel_count": None,
        "sampled_temporal_variation_gate_passed": False,
        "temporal_sampling_policy": TEMPORAL_SAMPLING_POLICY,
        "frames_shape": None,
        "frames_dtype": None,
        "frames_sha256": None,
        "sampled_indices_sha256": None,
        "source_num_frames_sha256": None,
        "npz_sha256": None,
        "source_num_frames": None,
        "error_code": None,
    }
    try:
        source_path = resolve_under(Path(download_root), relative, must_exist=True)
        output_path = resolve_under(Path(output_root), output_relative, must_exist=False)
        if output_path.exists():
            raise FileExistsError("Fresh output root required.")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        ds = pydicom.dcmread(str(source_path), stop_before_pixels=False, force=False)
        frames, decoder_metadata = _normalize_dicom_pixels(ds, pydicom)
        result.update(decoder_metadata)
        result["source_num_frames"] = int(frames.shape[0])
        try:
            frames, sector_mask = _mask_ultrasound_strict(frames, cv2)
            result["mask_status"] = "GENERATED_PENDING_SIGNAL_GATES"
            source_quality = _signal_quality_metrics(frames, sector_mask)
            result.update(
                {
                    "source_sector_pixel_count": source_quality["sector_pixel_count"],
                    "source_sector_nonempty_gate_passed": source_quality[
                        "sector_nonempty_gate_passed"
                    ],
                    "source_nonzero_retained_pixel_count": source_quality[
                        "nonzero_retained_pixel_count"
                    ],
                    "source_nonzero_retained_pixel_gate_passed": source_quality[
                        "nonzero_retained_pixel_gate_passed"
                    ],
                    "source_temporal_variation_pixel_count": source_quality[
                        "temporal_variation_pixel_count"
                    ],
                    "source_temporal_variation_gate_passed": source_quality[
                        "temporal_variation_gate_passed"
                    ],
                }
            )
            _require_signal_quality(source_quality)
        except Exception:
            result["mask_status"] = "FAILED"
            raise
        resized = np.stack([_crop_resize(frame, cv2) for frame in frames], axis=0).astype(np.uint8)
        sampled, indices = temporal_sample(resized, 32)
        if sampled.shape != EXTRACTION_SHAPE or sampled.dtype != np.uint8:
            raise ValueError("Extraction shape/dtype gate failed.")
        sampled_quality = _signal_quality_metrics(sampled)
        result.update(
            {
                "sampled_nonzero_retained_pixel_count": sampled_quality[
                    "nonzero_retained_pixel_count"
                ],
                "sampled_nonzero_retained_pixel_gate_passed": sampled_quality[
                    "nonzero_retained_pixel_gate_passed"
                ],
                "sampled_temporal_variation_pixel_count": sampled_quality[
                    "temporal_variation_pixel_count"
                ],
                "sampled_temporal_variation_gate_passed": sampled_quality[
                    "temporal_variation_gate_passed"
                ],
            }
        )
        try:
            _require_signal_quality(sampled_quality)
        except Exception:
            result["mask_status"] = "FAILED"
            raise
        result["mask_status"] = "APPLIED"
        source_num_frames_array = np.asarray([frames.shape[0]], dtype=np.int32)
        write_npz_atomic(
            output_path,
            frames=sampled,
            sampled_indices=indices.astype(np.int64),
            source_num_frames=source_num_frames_array,
        )
        result.update(
            {
                "write_ok": True,
                "frames_shape": "32x224x224x3",
                "frames_dtype": "uint8",
                "frames_sha256": array_content_sha256(sampled),
                "sampled_indices_sha256": array_content_sha256(indices.astype(np.int64)),
                "source_num_frames_sha256": array_content_sha256(source_num_frames_array),
                "npz_sha256": sha256_file(output_path),
            }
        )
    except Exception as exc:
        if result["mask_status"] == "GENERATED_PENDING_SIGNAL_GATES":
            result["mask_status"] = "FAILED"
        result["error_code"] = type(exc).__name__
    return result


def summarize_extraction(frame: pd.DataFrame) -> dict[str, Any]:
    required = {
        "write_ok",
        "clip_key",
        "study_id",
        "frames_shape",
        "frames_dtype",
        "mask_status",
        "source_sector_nonempty_gate_passed",
        "source_nonzero_retained_pixel_gate_passed",
        "source_temporal_variation_gate_passed",
        "sampled_nonzero_retained_pixel_gate_passed",
        "sampled_temporal_variation_gate_passed",
        "temporal_sampling_policy",
        "photometric_interpretation",
        "transfer_syntax_uid",
        "decoder_backend",
        "decoder_color_behavior",
        "color_transform",
        "canonical_color_space",
    }
    if not required.issubset(frame.columns):
        raise ValueError("Extraction frame is missing strict preprocessing authority fields.")
    ok = frame["write_ok"].map(parse_bool)
    successful = frame[ok]
    duplicate_keys = int(successful["clip_key"].duplicated(keep=False).sum())
    shape_dtype_ok = bool(
        successful["frames_shape"].eq("32x224x224x3").all()
        and successful["frames_dtype"].eq("uint8").all()
    )
    mask_ok = bool(successful["mask_status"].eq("APPLIED").all())
    gate_columns = (
        "source_sector_nonempty_gate_passed",
        "source_nonzero_retained_pixel_gate_passed",
        "source_temporal_variation_gate_passed",
        "sampled_nonzero_retained_pixel_gate_passed",
        "sampled_temporal_variation_gate_passed",
    )
    gate_values = {
        column: successful[column].map(parse_bool) for column in gate_columns
    }
    all_signal_gates = bool(all(values.all() for values in gate_values.values()))
    sampling_policy_locked = bool(
        successful["temporal_sampling_policy"].eq(TEMPORAL_SAMPLING_POLICY).all()
    )
    decoder_authority_ok = bool(
        successful["decoder_color_behavior"].eq("STORED_COLOR_RAW").all()
        and successful["canonical_color_space"].eq("RGB").all()
        and successful["decoder_backend"].notna().all()
    )
    passed = bool(
        ok.all()
        and len(successful) > 0
        and duplicate_keys == 0
        and shape_dtype_ok
        and mask_ok
        and all_signal_gates
        and sampling_policy_locked
        and decoder_authority_ok
    )
    return {
        "audit": "prospective_cine_extraction",
        "status": "PASS" if passed else "FAIL",
        "n_requested_cines": int(len(frame)),
        "n_extracted_cines": int(ok.sum()),
        "n_failed_cines": int((~ok).sum()),
        "n_unique_clip_keys": int(successful["clip_key"].nunique()),
        "n_duplicate_clip_key_rows": duplicate_keys,
        "n_studies_with_extracted_cine": int(successful["study_id"].nunique()),
        "all_shapes_32x224x224x3_uint8": shape_dtype_ok,
        "all_masks_explicitly_applied": mask_ok,
        "n_source_nonempty_sector_gate_passed": int(
            gate_values["source_sector_nonempty_gate_passed"].sum()
        ),
        "n_source_nonzero_retained_pixel_gate_passed": int(
            gate_values["source_nonzero_retained_pixel_gate_passed"].sum()
        ),
        "n_source_temporal_variation_gate_passed": int(
            gate_values["source_temporal_variation_gate_passed"].sum()
        ),
        "n_sampled_nonzero_retained_pixel_gate_passed": int(
            gate_values["sampled_nonzero_retained_pixel_gate_passed"].sum()
        ),
        "n_sampled_temporal_variation_gate_passed": int(
            gate_values["sampled_temporal_variation_gate_passed"].sum()
        ),
        "all_preprocessing_signal_gates_passed": all_signal_gates,
        "temporal_sampling_policy": TEMPORAL_SAMPLING_POLICY,
        "temporal_sampling_policy_locked_for_all_extracted_cines": sampling_policy_locked,
        "temporal_sampling_long_cine_rule": "endpoint_inclusive_integer_linspace",
        "temporal_sampling_short_cine_rule": "ordered_source_frames_then_repeat_final_frame",
        "all_decoders_stored_color_raw_to_canonical_rgb": decoder_authority_ok,
        "photometric_interpretation_counts": _technical_counts(
            successful, "photometric_interpretation", kind="photometric"
        ),
        "transfer_syntax_uid_counts": _technical_counts(
            successful, "transfer_syntax_uid", kind="transfer_syntax"
        ),
        "decoder_backend_counts": _technical_counts(
            successful, "decoder_backend", kind="decoder"
        ),
        "decoder_color_behavior_counts": _technical_counts(
            successful, "decoder_color_behavior", kind="decoder"
        ),
        "color_transform_counts": _technical_counts(
            successful, "color_transform", kind="color_transform"
        ),
        "row_values_emitted": False,
        "paths_emitted": False,
    }


def extract_all_cines(
    dicom_audit: pd.DataFrame, download_root: Path, output_root: Path, workers: int = 1
) -> tuple[pd.DataFrame, dict[str, Any]]:
    if workers < 1:
        raise ValueError("workers must be at least one.")
    required = {
        "subject_id", "study_id", "smoke_role", "source_relative_path", "read_ok", "is_multiframe"
    }
    if not required.issubset(dicom_audit.columns):
        raise ValueError("DICOM audit is missing required columns.")
    mask = dicom_audit["read_ok"].map(parse_bool) & dicom_audit["is_multiframe"].map(parse_bool)
    records = dicom_audit[mask].sort_values("source_relative_path", kind="mergesort").to_dict(orient="records")
    if not records:
        raise ValueError("No cine candidates are available for extraction.")
    output_root.mkdir(parents=True, exist_ok=True)
    if workers == 1:
        rows = [_extract_one(record, str(download_root.resolve()), str(output_root.resolve())) for record in records]
    else:
        rows = []
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = [
                pool.submit(_extract_one, record, str(download_root.resolve()), str(output_root.resolve()))
                for record in records
            ]
            for future in as_completed(futures):
                rows.append(future.result())
    frame = pd.DataFrame(rows).sort_values("source_relative_path", kind="mergesort").reset_index(drop=True)
    return frame, summarize_extraction(frame)


def validate_checkpoint(path: Path, expected_sha256: str, expected_bytes: int) -> dict[str, Any]:
    if path.name != CHECKPOINT_FILENAME:
        raise ValueError("Unexpected checkpoint filename.")
    if path.is_symlink() or not path.is_file():
        raise ValueError("Checkpoint must be a regular non-symlink file.")
    expected_sha256 = expected_sha256.lower()
    if not HEX64.fullmatch(expected_sha256):
        raise ValueError("Invalid expected checkpoint SHA-256.")
    observed_bytes = int(path.stat().st_size)
    observed_sha256 = sha256_file(path)
    passed = observed_bytes == int(expected_bytes) and observed_sha256 == expected_sha256
    if not passed:
        raise ValueError("Checkpoint identity gate failed.")
    return {
        "filename": path.name,
        "bytes": observed_bytes,
        "sha256": observed_sha256,
        "identity_gate_passed": True,
    }


def configure_torch_determinism(torch: Any, seed: int) -> dict[str, Any]:
    os.environ["CUBLAS_WORKSPACE_CONFIG"] = ":4096:8"
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    torch.use_deterministic_algorithms(True)
    if hasattr(torch.backends, "cudnn"):
        torch.backends.cudnn.benchmark = False
        torch.backends.cudnn.deterministic = True
        torch.backends.cudnn.allow_tf32 = False
    if hasattr(torch.backends, "cuda") and hasattr(torch.backends.cuda, "matmul"):
        torch.backends.cuda.matmul.allow_tf32 = False
    if hasattr(torch, "set_float32_matmul_precision"):
        torch.set_float32_matmul_precision("highest")
    return {
        "seed": int(seed),
        "deterministic_algorithms": True,
        "cublas_workspace_config": os.environ["CUBLAS_WORKSPACE_CONFIG"],
        "cudnn_benchmark": False,
        "cudnn_deterministic": True,
        "tf32_allowed": False,
        "autocast_used": False,
    }


def _load_extracted_frames(row: Mapping[str, Any], extraction_root: Path) -> np.ndarray:
    relative = safe_relative_path(row["output_relative_path"])
    path = resolve_under(extraction_root, relative, must_exist=True)
    if sha256_file(path) != str(row["npz_sha256"]):
        raise ValueError("Extracted NPZ byte checksum mismatch.")
    with np.load(path, allow_pickle=False) as archive:
        if set(archive.files) != {"frames", "sampled_indices", "source_num_frames"}:
            raise ValueError("Unexpected extracted NPZ schema.")
        frames = archive["frames"]
        sampled_indices = archive["sampled_indices"]
        source_num_frames = archive["source_num_frames"]
    if frames.shape != EXTRACTION_SHAPE or frames.dtype != np.uint8:
        raise ValueError("Extracted frame shape/dtype gate failed.")
    if array_content_sha256(frames) != str(row["frames_sha256"]):
        raise ValueError("Extracted frame content checksum mismatch.")
    if array_content_sha256(sampled_indices) != str(row["sampled_indices_sha256"]):
        raise ValueError("Extracted sampled-index checksum mismatch.")
    if array_content_sha256(source_num_frames) != str(row["source_num_frames_sha256"]):
        raise ValueError("Extracted source-frame metadata checksum mismatch.")
    return np.ascontiguousarray(frames)


def _prepare_encoder_input(frames: np.ndarray, torch: Any) -> Any:
    tensor = torch.as_tensor(frames, dtype=torch.float32).permute(3, 0, 1, 2)
    mean = torch.as_tensor(MEAN, dtype=torch.float32).reshape(3, 1, 1, 1)
    std = torch.as_tensor(STD, dtype=torch.float32).reshape(3, 1, 1, 1)
    tensor = tensor.sub(mean).div(std)
    tensor = tensor[:, 0:32:2, :, :]
    if tuple(tensor.shape) != (3, 16, 224, 224):
        raise ValueError("Encoder input shape gate failed.")
    return tensor


def validate_embedding_authority(
    embeddings: np.ndarray, manifest: pd.DataFrame
) -> dict[str, Any]:
    required = {
        "embedding_idx",
        "subject_id",
        "study_id",
        "smoke_role",
        "clip_key",
        "write_ok",
        "embedding_l2_norm",
        "embedding_sha256",
    }
    if not required.issubset(manifest.columns):
        raise ValueError("Embedding manifest is missing required columns.")
    if embeddings.ndim != 2 or embeddings.shape[1] != EMBEDDING_WIDTH or embeddings.dtype != np.float32:
        raise ValueError("Embedding array shape/dtype gate failed.")
    if len(manifest) != embeddings.shape[0] or not manifest["write_ok"].map(parse_bool).all():
        raise ValueError("Embedding array/manifest row alignment failed.")
    indices = pd.to_numeric(manifest["embedding_idx"], errors="raise").astype(int).to_numpy()
    if not np.array_equal(indices, np.arange(len(manifest), dtype=int)):
        raise ValueError("embedding_idx is not authoritative and contiguous.")
    if manifest["clip_key"].duplicated().any():
        raise ValueError("Duplicate clip keys are prohibited.")
    if not np.isfinite(embeddings).all():
        raise ValueError("Embedding array contains nonfinite values.")
    norms = np.linalg.norm(embeddings.astype(np.float64), axis=1)
    stored = pd.to_numeric(manifest["embedding_l2_norm"], errors="raise").to_numpy(dtype=np.float64)
    if not np.isfinite(norms).all() or not (norms > 0).all() or not np.allclose(norms, stored, rtol=1e-6, atol=1e-6):
        raise ValueError("Embedding L2 norm gate failed.")
    vector_hashes = [array_content_sha256(vector) for vector in embeddings]
    if vector_hashes != manifest["embedding_sha256"].astype(str).tolist():
        raise ValueError("Embedding vector content hashes do not align with manifest rows.")
    role_studies = manifest.groupby("smoke_role", dropna=False)["study_id"].nunique(
        dropna=False
    )
    if (
        set(role_studies.index) != set(POSITIVE_CONTROL_ROLES)
        or not role_studies.eq(1).all()
        or manifest["study_id"].nunique() != len(POSITIVE_CONTROL_ROLES)
    ):
        raise ValueError("Embedding authority requires all three positive-control studies.")
    return {
        "n_embeddings": int(embeddings.shape[0]),
        "embedding_width": int(embeddings.shape[1]),
        "embedding_dtype": str(embeddings.dtype),
        "all_finite": True,
        "all_l2_positive_and_reconciled": True,
        "embedding_idx_authoritative": True,
        "clip_keys_unique": True,
        "per_vector_content_hashes_reconciled": True,
        "positive_control_studies_exact": True,
        "array_content_sha256": array_content_sha256(embeddings),
    }


def embed_clips(
    extraction_manifest: pd.DataFrame,
    extraction_root: Path,
    checkpoint: Path,
    output_npz: Path,
    output_manifest: Path,
    *,
    expected_checkpoint_sha256: str = CHECKPOINT_SHA256,
    expected_checkpoint_bytes: int = CHECKPOINT_BYTES,
    device_request: str = "cuda",
    batch_size: int = 8,
    seed: int = 20260803,
) -> dict[str, Any]:
    if batch_size < 1:
        raise ValueError("batch_size must be positive.")
    checkpoint_identity = validate_checkpoint(checkpoint, expected_checkpoint_sha256, expected_checkpoint_bytes)
    os.environ["CUBLAS_WORKSPACE_CONFIG"] = ":4096:8"
    import torch  # optional dependencies; intentionally local and after CUBLAS setting
    import torchvision

    determinism = configure_torch_determinism(torch, seed)
    if device_request == "cuda":
        if not torch.cuda.is_available():
            raise RuntimeError("CUDA requested but unavailable.")
        device = torch.device("cuda")
    elif device_request == "cpu":
        device = torch.device("cpu")
    elif device_request == "auto":
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    else:
        raise ValueError("device must be cpu, cuda, or auto.")
    if device.type != "cuda":
        raise RuntimeError("Authoritative reconstruction smoke requires CUDA.")

    required = {
        "subject_id", "study_id", "smoke_role", "source_relative_path", "clip_key", "output_relative_path",
        "write_ok", "frames_sha256", "sampled_indices_sha256", "source_num_frames_sha256",
        "npz_sha256",
    }
    if not required.issubset(extraction_manifest.columns):
        raise ValueError("Extraction manifest is missing required columns.")
    extraction_success = extraction_manifest["write_ok"].map(parse_bool)
    if not extraction_success.all():
        raise ValueError("Authoritative embedding refuses a partially failed extraction manifest.")
    work = extraction_manifest[extraction_success].copy()
    work = work.sort_values(["study_id", "clip_key"], kind="mergesort").reset_index(drop=True)
    if work.empty or work["clip_key"].duplicated().any():
        raise ValueError("Embedding input must contain unique successful clips.")
    frames = [_load_extracted_frames(row, extraction_root) for row in work.to_dict(orient="records")]

    model = torchvision.models.video.mvit_v2_s(weights=None)
    model.head[-1] = torch.nn.Linear(model.head[-1].in_features, EMBEDDING_WIDTH)
    try:
        state = torch.load(str(checkpoint), map_location="cpu", weights_only=True)
    except TypeError as exc:
        raise RuntimeError("Pinned torch must support weights_only checkpoint loading.") from exc
    model.load_state_dict(state, strict=True)
    model.eval().to(device)
    for parameter in model.parameters():
        parameter.requires_grad = False

    vectors: list[np.ndarray] = []
    with torch.inference_mode():
        for start in range(0, len(frames), batch_size):
            tensors = [_prepare_encoder_input(value, torch) for value in frames[start : start + batch_size]]
            batch = torch.stack(tensors, dim=0).to(device)
            output = model(batch).detach().cpu().numpy().astype(np.float32, copy=False)
            if output.shape != (len(tensors), EMBEDDING_WIDTH) or not np.isfinite(output).all():
                raise RuntimeError("Encoder output gate failed.")
            vectors.extend(output)
    array = np.stack(vectors, axis=0).astype(np.float32, copy=False)
    rows: list[dict[str, Any]] = []
    for index, (record, vector) in enumerate(zip(work.to_dict(orient="records"), array)):
        rows.append(
            {
                "embedding_idx": index,
                "subject_id": record["subject_id"],
                "study_id": record["study_id"],
                "smoke_role": record["smoke_role"],
                "source_relative_path": record["source_relative_path"],
                "clip_key": record["clip_key"],
                "embedding_l2_norm": float(np.linalg.norm(vector.astype(np.float64))),
                "embedding_sha256": array_content_sha256(vector),
                "write_ok": True,
            }
        )
    manifest = pd.DataFrame(rows)
    validation = validate_embedding_authority(array, manifest)
    write_npz_atomic(output_npz, embeddings=array)
    write_csv_atomic(output_manifest, manifest)
    return {
        "audit": "prospective_echoprime_encoder_smoke",
        "status": "PASS",
        **{key: value for key, value in validation.items() if key != "array_content_sha256"},
        "checkpoint_identity_gate_passed": checkpoint_identity["identity_gate_passed"],
        "encoder_only": True,
        "view_classifier_loaded": False,
        "device_type": device.type,
        "batch_size": int(batch_size),
        "determinism": determinism,
        "torch_version": str(torch.__version__),
        "torchvision_version": str(torchvision.__version__),
        "row_values_emitted": False,
        "paths_emitted": False,
    }


def mean_pool_studies(
    embeddings: np.ndarray, clip_manifest: pd.DataFrame
) -> tuple[np.ndarray, pd.DataFrame, dict[str, Any]]:
    validation = validate_embedding_authority(embeddings, clip_manifest)
    ordered = clip_manifest.sort_values(["study_id", "clip_key"], kind="mergesort").reset_index(drop=True)
    ownership = ordered.groupby("study_id", dropna=False)["subject_id"].nunique(dropna=False)
    if not ownership.eq(1).all():
        raise ValueError("Study-to-subject ownership is inconsistent.")
    if "smoke_role" not in ordered.columns:
        raise ValueError("Clip embedding manifest is missing smoke_role.")
    role_ownership = ordered.groupby("study_id", dropna=False)["smoke_role"].nunique(dropna=False)
    if not role_ownership.eq(1).all():
        raise ValueError("Study-to-smoke-role ownership is inconsistent.")
    if (
        set(ordered["smoke_role"]) != set(POSITIVE_CONTROL_ROLES)
        or ordered["study_id"].nunique() != len(POSITIVE_CONTROL_ROLES)
    ):
        raise ValueError("Study pooling requires all three positive-control studies.")
    study_vectors: list[np.ndarray] = []
    rows: list[dict[str, Any]] = []
    for study_id, group in ordered.groupby("study_id", sort=True, dropna=False):
        indices = group["embedding_idx"].astype(int).to_numpy()
        aggregate = embeddings[indices].mean(axis=0, dtype=np.float64).astype(np.float32)
        study_index = len(study_vectors)
        study_vectors.append(aggregate)
        rows.append(
            {
                "study_idx": study_index,
                "subject_id": group["subject_id"].iloc[0],
                "study_id": study_id,
                "smoke_role": group["smoke_role"].iloc[0],
                "n_clips": int(len(group)),
                "embedding_l2_norm": float(np.linalg.norm(aggregate.astype(np.float64))),
                "embedding_sha256": array_content_sha256(aggregate),
            }
        )
    array = np.stack(study_vectors, axis=0).astype(np.float32, copy=False)
    manifest = pd.DataFrame(rows)
    if not np.isfinite(array).all() or manifest["study_id"].duplicated().any():
        raise ValueError("Study aggregation authority gate failed.")
    if len(manifest) != len(POSITIVE_CONTROL_ROLES):
        raise ValueError("Study aggregation output count is incomplete.")
    summary = {
        "audit": "prospective_study_mean_pooling",
        "status": "PASS",
        "n_input_clips": int(validation["n_embeddings"]),
        "n_output_studies": int(len(manifest)),
        "embedding_width": EMBEDDING_WIDTH,
        "embedding_dtype": "float32",
        "accumulation_dtype": "float64",
        "output_cast_dtype": "float32",
        "all_finite": True,
        "study_ids_unique": True,
        "study_ownership_consistent": True,
        "positive_control_studies_exact": True,
        "row_values_emitted": False,
        "paths_emitted": False,
    }
    return array, manifest, summary


def load_embedding_pair(npz_path: Path, manifest_path: Path) -> tuple[np.ndarray, pd.DataFrame]:
    with np.load(npz_path, allow_pickle=False) as archive:
        if set(archive.files) != {"embeddings"}:
            raise ValueError("Embedding NPZ must contain only the embeddings array.")
        array = archive["embeddings"]
    return array, pd.read_csv(manifest_path, low_memory=False)


def _normalized_manifest_bytes(frame: pd.DataFrame) -> bytes:
    normalized = frame.copy()
    normalized.columns = [str(value) for value in normalized.columns]
    normalized = normalized.reindex(sorted(normalized.columns), axis=1)
    sort_keys = [
        key for key in ("study_id", "clip_key", "source_relative_path", "study_idx", "embedding_idx")
        if key in normalized.columns
    ]
    if sort_keys:
        normalized = normalized.sort_values(sort_keys, kind="mergesort", na_position="last")
    else:
        normalized = normalized.sort_values(list(normalized.columns), kind="mergesort", na_position="last")
    return normalized.to_csv(
        index=False, lineterminator="\n", na_rep="<NA>", float_format="%.17g"
    ).encode("utf-8")


def _normalized_manifest_hash(frame: pd.DataFrame) -> str:
    return hashlib.sha256(_normalized_manifest_bytes(frame)).hexdigest()


def parse_pair(value: str) -> tuple[str, Path, Path]:
    if "=" not in value or "," not in value:
        raise ValueError("Expected ALIAS=PATH_A,PATH_B.")
    alias, paths = value.split("=", 1)
    first, second = paths.split(",", 1)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", alias):
        raise ValueError("Unsafe comparison alias.")
    return alias, Path(first), Path(second)


def parse_extraction_pair(value: str) -> tuple[str, Path, Path, Path, Path]:
    """Parse ALIAS=MANIFEST_A,ROOT_A,MANIFEST_B,ROOT_B."""

    if "=" not in value:
        raise ValueError("Expected ALIAS=MANIFEST_A,ROOT_A,MANIFEST_B,ROOT_B.")
    alias, raw_paths = value.split("=", 1)
    paths = raw_paths.split(",")
    if len(paths) != 4:
        raise ValueError("Expected ALIAS=MANIFEST_A,ROOT_A,MANIFEST_B,ROOT_B.")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", alias):
        raise ValueError("Unsafe comparison alias.")
    return alias, *(Path(path) for path in paths)


def _load_extraction_array_hashes(row: Mapping[str, Any], root: Path) -> dict[str, str]:
    output_relative = safe_relative_path(row["output_relative_path"])
    path = resolve_under(root, output_relative, must_exist=True)
    with np.load(path, allow_pickle=False) as archive:
        expected_arrays = {"frames", "sampled_indices", "source_num_frames"}
        if set(archive.files) != expected_arrays:
            raise ValueError("Unexpected extracted NPZ schema in reproducibility audit.")
        hashes = {name: array_content_sha256(archive[name]) for name in sorted(expected_arrays)}
    declared_columns = {
        "frames": "frames_sha256",
        "sampled_indices": "sampled_indices_sha256",
        "source_num_frames": "source_num_frames_sha256",
    }
    for array_name, column in declared_columns.items():
        declared = row.get(column)
        if declared is None or pd.isna(declared) or str(declared) != hashes[array_name]:
            raise ValueError("Extraction manifest internal-array hash gate failed.")
    return hashes


def _prepare_extraction_compare_manifest(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, low_memory=False)
    required = {
        "clip_key", "output_relative_path", "write_ok", "frames_sha256",
        "sampled_indices_sha256", "source_num_frames_sha256",
    }
    if not required.issubset(frame.columns):
        raise ValueError("Extraction comparison manifest is missing required columns.")
    if frame.empty or not frame["write_ok"].map(parse_bool).all():
        raise ValueError("Extraction comparison requires a complete successful run.")
    if frame["clip_key"].isna().any() or frame["clip_key"].duplicated().any():
        raise ValueError("Extraction comparison requires unique nonmissing clip keys.")
    frame = frame.copy()
    frame["output_relative_path"] = frame["output_relative_path"].map(safe_relative_path)
    return frame.sort_values("clip_key", kind="mergesort").reset_index(drop=True)


def _compare_extraction_pair(
    alias: str,
    first_manifest_path: Path,
    first_root: Path,
    second_manifest_path: Path,
    second_root: Path,
) -> dict[str, Any]:
    first = _prepare_extraction_compare_manifest(first_manifest_path)
    second = _prepare_extraction_compare_manifest(second_manifest_path)
    first_by_key = {str(row["clip_key"]): row for row in first.to_dict(orient="records")}
    second_by_key = {str(row["clip_key"]): row for row in second.to_dict(orient="records")}
    first_keys = set(first_by_key)
    second_keys = set(second_by_key)
    mismatched_keys = set(first_keys ^ second_keys)
    n_arrays_compared = 0
    first_run_digest = hashlib.sha256()
    second_run_digest = hashlib.sha256()
    for clip_key in sorted(first_keys & second_keys):
        first_row = first_by_key[clip_key]
        second_row = second_by_key[clip_key]
        if first_row["output_relative_path"] != second_row["output_relative_path"]:
            mismatched_keys.add(clip_key)
            continue
        first_hashes = _load_extraction_array_hashes(first_row, first_root)
        second_hashes = _load_extraction_array_hashes(second_row, second_root)
        for name in sorted(first_hashes):
            n_arrays_compared += 1
            first_run_digest.update(f"{clip_key}\0{name}\0{first_hashes[name]}\n".encode("ascii"))
            second_run_digest.update(f"{clip_key}\0{name}\0{second_hashes[name]}\n".encode("ascii"))
            if first_hashes[name] != second_hashes[name]:
                mismatched_keys.add(clip_key)

    # Container-byte SHA-256 is intentionally excluded from normalized manifest authority.
    first_normalized = first.drop(columns=["npz_sha256"], errors="ignore")
    second_normalized = second.drop(columns=["npz_sha256"], errors="ignore")
    first_manifest_hash = _normalized_manifest_hash(first_normalized)
    second_manifest_hash = _normalized_manifest_hash(second_normalized)
    arrays_equal = first_run_digest.digest() == second_run_digest.digest()
    exact_equal = bool(
        first_keys == second_keys
        and not mismatched_keys
        and arrays_equal
        and first_manifest_hash == second_manifest_hash
    )
    return {
        "alias": alias,
        "artifact_type": "extraction_run",
        "first_content_sha256": first_run_digest.hexdigest(),
        "second_content_sha256": second_run_digest.hexdigest(),
        "first_normalized_manifest_sha256": first_manifest_hash,
        "second_normalized_manifest_sha256": second_manifest_hash,
        "n_first_clips": len(first_keys),
        "n_second_clips": len(second_keys),
        "n_internal_arrays_compared": n_arrays_compared,
        "mismatched_clip_keys": sorted(mismatched_keys),
        "npz_container_bytes_compared": False,
        "exact_equal": exact_equal,
    }


def compare_reproducibility(
    manifest_pairs: Sequence[tuple[str, Path, Path]],
    array_pairs: Sequence[tuple[str, Path, Path]],
    extraction_pairs: Sequence[tuple[str, Path, Path, Path, Path]] = (),
) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest_aliases = [alias for alias, _, _ in manifest_pairs]
    array_aliases = [alias for alias, _, _ in array_pairs]
    extraction_aliases = [alias for alias, *_ in extraction_pairs]
    if (
        set(manifest_aliases) != EXPECTED_MANIFEST_PAIR_ALIASES
        or len(manifest_aliases) != len(EXPECTED_MANIFEST_PAIR_ALIASES)
        or set(array_aliases) != EXPECTED_ARRAY_PAIR_ALIASES
        or len(array_aliases) != len(EXPECTED_ARRAY_PAIR_ALIASES)
        or set(extraction_aliases) != EXPECTED_EXTRACTION_PAIR_ALIASES
        or len(extraction_aliases) != len(EXPECTED_EXTRACTION_PAIR_ALIASES)
    ):
        raise ValueError("Reproducibility audit requires the exact five-artifact pair set.")
    for _, first, second in [*manifest_pairs, *array_pairs]:
        if first.resolve(strict=True) == second.resolve(strict=True):
            raise ValueError("Reproducibility inputs must come from distinct files.")
    for _, first_manifest, first_root, second_manifest, second_root in extraction_pairs:
        if first_manifest.resolve(strict=True) == second_manifest.resolve(strict=True):
            raise ValueError("Extraction manifests must be distinct files.")
        if first_root.resolve(strict=True) == second_root.resolve(strict=True):
            raise ValueError("Extraction runs must use distinct clean roots.")

    restricted_rows: list[dict[str, Any]] = []
    for alias, first, second in manifest_pairs:
        first_frame = pd.read_csv(first, low_memory=False)
        second_frame = pd.read_csv(second, low_memory=False)
        first_hash = _normalized_manifest_hash(first_frame)
        second_hash = _normalized_manifest_hash(second_frame)
        restricted_rows.append(
            {
                "alias": alias,
                "artifact_type": "manifest",
                "first_content_sha256": first_hash,
                "second_content_sha256": second_hash,
                "exact_equal": first_hash == second_hash,
            }
        )
    for alias, first_manifest, first_root, second_manifest, second_root in extraction_pairs:
        restricted_rows.append(
            _compare_extraction_pair(
                alias, first_manifest, first_root, second_manifest, second_root
            )
        )
    for alias, first, second in array_pairs:
        with np.load(first, allow_pickle=False) as first_archive, np.load(second, allow_pickle=False) as second_archive:
            if set(first_archive.files) != {"embeddings"} or set(second_archive.files) != {"embeddings"}:
                raise ValueError("Compared NPZ must contain only embeddings.")
            first_array = first_archive["embeddings"]
            second_array = second_archive["embeddings"]
        if first_array.shape != second_array.shape or first_array.dtype != second_array.dtype:
            raise ValueError("Compared embedding arrays have different shape or dtype.")
        if first_array.ndim != 2 or first_array.shape[1] != EMBEDDING_WIDTH:
            raise ValueError("Compared embedding arrays are not 512-dimensional matrices.")
        if alias == "study_embeddings" and first_array.shape[0] != len(POSITIVE_CONTROL_ROLES):
            raise ValueError("Study-vector comparison requires exactly three positive controls.")
        if alias == "clip_embeddings" and first_array.shape[0] < len(POSITIVE_CONTROL_ROLES):
            raise ValueError("Clip-vector comparison is missing positive-control clips.")
        first_hash = array_content_sha256(first_array)
        second_hash = array_content_sha256(second_array)
        restricted_rows.append(
            {
                "alias": alias,
                "artifact_type": "array",
                "first_content_sha256": first_hash,
                "second_content_sha256": second_hash,
                "array_shape": list(first_array.shape),
                "array_dtype": str(first_array.dtype),
                "exact_equal": first_hash == second_hash,
            }
        )
    n_equal = sum(bool(row["exact_equal"]) for row in restricted_rows)
    aggregate = {
        "audit": "prospective_two_run_exact_reproducibility",
        "status": "PASS" if n_equal == len(restricted_rows) else "FAIL",
        "n_pairs": len(restricted_rows),
        "n_exact_equal": n_equal,
        "n_not_exact_equal": len(restricted_rows) - n_equal,
        "normalized_manifests_compared": sum(row["artifact_type"] == "manifest" for row in restricted_rows),
        "internal_array_content_hashes_compared": sum(row["artifact_type"] == "array" for row in restricted_rows),
        "extraction_runs_compared": sum(
            row["artifact_type"] == "extraction_run" for row in restricted_rows
        ),
        "extraction_internal_arrays_compared": sum(
            int(row.get("n_internal_arrays_compared", 0)) for row in restricted_rows
        ),
        "npz_container_bytes_used_as_exactness_gate": False,
        "required_artifact_pair_set_exact": True,
        "distinct_run_files_and_extraction_roots": True,
        "row_values_emitted": False,
        "paths_emitted": False,
    }
    restricted = {"audit": aggregate["audit"], "pairs": restricted_rows, "status": aggregate["status"]}
    return restricted, aggregate


def _write_outputs(
    restricted_path: Path,
    aggregate_path: Path,
    restricted_value: pd.DataFrame | Mapping[str, Any],
    aggregate_value: Mapping[str, Any],
) -> None:
    if isinstance(restricted_value, pd.DataFrame):
        write_csv_atomic(restricted_path, restricted_value)
    else:
        write_json_atomic(restricted_path, restricted_value)
    write_json_atomic(aggregate_path, aggregate_value)
    print(json.dumps(dict(aggregate_value), indent=2, sort_keys=True))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    download = sub.add_parser("audit-downloads")
    download.add_argument("--source-manifest", type=Path, required=True)
    download.add_argument("--release-checksums", type=Path, required=True)
    download.add_argument("--download-root", type=Path, required=True)
    download.add_argument("--restricted-output", type=Path, required=True)
    download.add_argument("--aggregate-output", type=Path, required=True)

    dicom = sub.add_parser("audit-dicoms")
    dicom.add_argument("--download-audit", type=Path, required=True)
    dicom.add_argument("--download-root", type=Path, required=True)
    dicom.add_argument("--workers", type=int, default=1)
    dicom.add_argument("--restricted-output", type=Path, required=True)
    dicom.add_argument("--aggregate-output", type=Path, required=True)

    extract = sub.add_parser("extract-cines")
    extract.add_argument("--dicom-audit", type=Path, required=True)
    extract.add_argument("--download-root", type=Path, required=True)
    extract.add_argument("--extraction-root", type=Path, required=True)
    extract.add_argument("--workers", type=int, default=1)
    extract.add_argument("--restricted-output", type=Path, required=True)
    extract.add_argument("--aggregate-output", type=Path, required=True)

    embed = sub.add_parser("embed-clips")
    embed.add_argument("--extraction-manifest", type=Path, required=True)
    embed.add_argument("--extraction-root", type=Path, required=True)
    embed.add_argument("--checkpoint", type=Path, required=True)
    embed.add_argument("--expected-checkpoint-sha256", default=CHECKPOINT_SHA256)
    embed.add_argument("--expected-checkpoint-bytes", type=int, default=CHECKPOINT_BYTES)
    embed.add_argument("--device", choices=["cuda"], default="cuda")
    embed.add_argument("--batch-size", type=int, default=8)
    embed.add_argument("--seed", type=int, default=20260803)
    embed.add_argument("--output-npz", type=Path, required=True)
    embed.add_argument("--restricted-output", type=Path, required=True)
    embed.add_argument("--aggregate-output", type=Path, required=True)

    pool = sub.add_parser("pool-studies")
    pool.add_argument("--clip-embedding-npz", type=Path, required=True)
    pool.add_argument("--clip-embedding-manifest", type=Path, required=True)
    pool.add_argument("--output-npz", type=Path, required=True)
    pool.add_argument("--restricted-output", type=Path, required=True)
    pool.add_argument("--aggregate-output", type=Path, required=True)

    compare = sub.add_parser("compare-runs")
    compare.add_argument("--manifest-pair", action="append", default=[])
    compare.add_argument("--array-pair", action="append", default=[])
    compare.add_argument(
        "--extraction-pair",
        action="append",
        default=[],
        metavar="ALIAS=MANIFEST_A,ROOT_A,MANIFEST_B,ROOT_B",
    )
    compare.add_argument("--restricted-output", type=Path, required=True)
    compare.add_argument("--aggregate-output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    require_outside_repository_output(args.restricted_output)
    if args.command == "extract-cines":
        require_outside_repository_output(args.extraction_root)
    if args.command in {"embed-clips", "pool-studies"}:
        require_outside_repository_output(args.output_npz)
    if args.command == "audit-downloads":
        source = pd.read_csv(args.source_manifest, low_memory=False)
        restricted, aggregate = audit_downloaded_objects(
            source, read_release_checksums(args.release_checksums), args.download_root
        )
    elif args.command == "audit-dicoms":
        restricted, aggregate = audit_dicom_headers(
            pd.read_csv(args.download_audit, low_memory=False), args.download_root, args.workers
        )
    elif args.command == "extract-cines":
        restricted, aggregate = extract_all_cines(
            pd.read_csv(args.dicom_audit, low_memory=False),
            args.download_root,
            args.extraction_root,
            args.workers,
        )
    elif args.command == "embed-clips":
        aggregate = embed_clips(
            pd.read_csv(args.extraction_manifest, low_memory=False),
            args.extraction_root,
            args.checkpoint,
            args.output_npz,
            args.restricted_output,
            expected_checkpoint_sha256=args.expected_checkpoint_sha256,
            expected_checkpoint_bytes=args.expected_checkpoint_bytes,
            device_request=args.device,
            batch_size=args.batch_size,
            seed=args.seed,
        )
        write_json_atomic(args.aggregate_output, aggregate)
        print(json.dumps(aggregate, indent=2, sort_keys=True))
        return 0 if aggregate["status"] == "PASS" else 2
    elif args.command == "pool-studies":
        embeddings, clips = load_embedding_pair(args.clip_embedding_npz, args.clip_embedding_manifest)
        study_array, restricted, aggregate = mean_pool_studies(embeddings, clips)
        write_npz_atomic(args.output_npz, embeddings=study_array)
    elif args.command == "compare-runs":
        restricted, aggregate = compare_reproducibility(
            [parse_pair(value) for value in args.manifest_pair],
            [parse_pair(value) for value in args.array_pair],
            [parse_extraction_pair(value) for value in args.extraction_pair],
        )
    else:  # pragma: no cover
        raise AssertionError(args.command)

    _write_outputs(args.restricted_output, args.aggregate_output, restricted, aggregate)
    return 0 if aggregate["status"] == "PASS" else 2


def guarded_main(argv: Sequence[str] | None = None) -> int:
    """Convert failures to a path- and row-value-free terminal record."""

    try:
        return main(argv)
    except SystemExit:
        raise
    except Exception as exc:
        payload = {
            "audit": "prospective_reconstruction_smoke",
            "status": "BLOCKED_SMOKE_EXCEPTION",
            "error_type": type(exc).__name__,
            "exception_message_emitted": False,
            "row_values_emitted": False,
            "paths_emitted": False,
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(guarded_main())
