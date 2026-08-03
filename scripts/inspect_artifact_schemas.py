#!/usr/bin/env python3
"""Report checksums and schemas without printing paths or row values.

Artifacts are named on the command line as ALIAS=PATH. The output contains the
alias, byte count, SHA-256 digest, and format-specific structural metadata only.
It never includes a supplied path, table value, JSON scalar value, prediction,
label, identifier, or embedding value.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
import hashlib
import json
import zipfile
from pathlib import Path
from typing import Any, BinaryIO

import numpy as np
import pandas as pd

from lvef_multitask_audit_utils import parse_named_path, run_guarded, write_json


TABLE_CHUNK_ROWS = 100_000


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact", action="append", required=True, metavar="ALIAS=PATH")
    parser.add_argument(
        "--embedding-pair",
        action="append",
        default=[],
        metavar="ALIAS=NPZ_PATH,MANIFEST_PATH",
    )
    parser.add_argument("--embedding-width", type=int, default=512)
    parser.add_argument("--output-json", type=Path, required=True)
    return parser.parse_args()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def combine_dtype_observations(observations: dict[str, set[str]]) -> list[dict[str, Any]]:
    combined: list[dict[str, Any]] = []
    for column, values in observations.items():
        ordered = sorted(values)
        combined.append(
            {
                "column_name": column,
                "dtype": ordered[0] if len(ordered) == 1 else ordered,
            }
        )
    return combined


def inspect_delimited(path: Path, separator: str) -> dict[str, Any]:
    with path.open(newline="", errors="replace") as handle:
        columns = next(csv.reader(handle, delimiter=separator))
    observations = {str(column): set() for column in columns}
    n_rows = 0
    for chunk in pd.read_csv(path, sep=separator, chunksize=TABLE_CHUNK_ROWS, low_memory=False):
        n_rows += len(chunk)
        for column, dtype in chunk.dtypes.items():
            observations[str(column)].add(str(dtype))
    return {
        "n_rows": int(n_rows),
        "n_columns": len(columns),
        "columns": [str(column) for column in columns],
        "inferred_dtypes": combine_dtype_observations(observations),
    }


def json_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    if isinstance(value, str):
        return "string"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    return type(value).__name__


def inspect_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text())
    result: dict[str, Any] = {
        "top_level_type": json_type(payload),
        "source_key_names_emitted": False,
        "scalar_values_emitted": False,
    }
    if isinstance(payload, dict):
        result["n_top_level_keys"] = len(payload)
        result["top_level_value_type_counts"] = dict(
            sorted(Counter(json_type(value) for value in payload.values()).items())
        )
    elif isinstance(payload, list):
        result["n_items"] = len(payload)
        result["item_types"] = sorted({json_type(value) for value in payload})
        object_key_counts = [len(value) for value in payload if isinstance(value, dict)]
        if object_key_counts:
            result["object_item_key_count_min"] = min(object_key_counts)
            result["object_item_key_count_max"] = max(object_key_counts)
    return result


def npy_header(handle: BinaryIO) -> dict[str, Any]:
    version = np.lib.format.read_magic(handle)
    shape, fortran_order, dtype = np.lib.format._read_array_header(handle, version)  # type: ignore[attr-defined]
    return {
        "shape": [int(value) for value in shape],
        "dtype": str(dtype),
        "fortran_order": bool(fortran_order),
        "npy_format_version": [int(value) for value in version],
    }


def inspect_numpy(path: Path) -> dict[str, Any]:
    if path.suffix.lower() == ".npy":
        with path.open("rb") as handle:
            return {"n_arrays": 1, "arrays": [{"array_name": "array", **npy_header(handle)}]}
    arrays: list[dict[str, Any]] = []
    with zipfile.ZipFile(path) as archive:
        for member in sorted(archive.infolist(), key=lambda value: value.filename):
            if member.is_dir() or not member.filename.endswith(".npy"):
                continue
            key = member.filename[:-4]
            with archive.open(member) as handle:
                arrays.append({"array_name": key, **npy_header(handle)})
    return {"n_arrays": len(arrays), "arrays": arrays}


def count_lines(path: Path) -> int:
    count = 0
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            count += block.count(b"\n")
    return count


def inspect_path(alias: str, path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {"alias": alias}
    if not path.exists() or not path.is_file():
        result.update({"status": "MISSING", "format": "unknown"})
        return result
    suffix = path.suffix.lower()
    result.update(
        {
            "status": "OK",
            "format": suffix.lstrip(".") or "no_extension",
            "bytes": int(path.stat().st_size),
            "sha256": sha256_file(path),
        }
    )
    try:
        if suffix == ".csv":
            result["schema"] = inspect_delimited(path, ",")
        elif suffix in {".tsv", ".txt"}:
            if suffix == ".tsv":
                result["schema"] = inspect_delimited(path, "\t")
            else:
                result["schema"] = {"n_lines": count_lines(path), "content_inspected": False}
        elif suffix == ".json":
            result["schema"] = inspect_json(path)
        elif suffix in {".npz", ".npy"}:
            result["schema"] = inspect_numpy(path)
        else:
            result["schema"] = {"content_inspected": False}
    except Exception as exc:
        result["status"] = "ERROR"
        result["error_type"] = type(exc).__name__
        result.pop("schema", None)
    return result


def parse_embedding_pair(value: str) -> tuple[str, Path, Path]:
    alias, raw_paths = value.split("=", 1) if "=" in value else ("", "")
    if not alias.strip() or "," not in raw_paths:
        raise ValueError("Expected embedding pair as ALIAS=NPZ_PATH,MANIFEST_PATH")
    npz_text, manifest_text = raw_paths.split(",", 1)
    return alias.strip(), Path(npz_text).expanduser(), Path(manifest_text).expanduser()


def inspect_embedding_pair(
    alias: str, npz_path: Path, manifest_path: Path, expected_width: int
) -> dict[str, Any]:
    result: dict[str, Any] = {"alias": alias, "expected_width": expected_width}
    if not npz_path.is_file() or not manifest_path.is_file():
        result.update({"status": "MISSING", "checks_passed": False})
        return result
    numpy_schema = inspect_numpy(npz_path)
    arrays = numpy_schema.get("arrays", [])
    embedding_arrays = [item for item in arrays if item.get("array_name") == "embeddings"]
    manifest = pd.read_csv(manifest_path, low_memory=False)
    subject_columns = {"subject_id", "patient_id", "person_id"} & set(manifest.columns)
    study_columns = {"study_id", "dicom_study_id", "study"} & set(manifest.columns)
    index_columns = {"embedding_idx", "study_idx"} & set(manifest.columns)
    success_flag_values_valid = True
    if "write_ok" in manifest.columns:
        values = manifest["write_ok"]
        if values.dtype == bool:
            success_mask = values.fillna(False)
            success_flag_values_valid = bool(values.notna().all())
        else:
            normalized_success = values.astype("string").str.strip().str.lower()
            success_flag_values_valid = bool(
                normalized_success.notna().all()
                and normalized_success.isin({"true", "false", "1", "0", "yes", "no", "y", "n"}).all()
            )
            success_mask = normalized_success.isin({"true", "1", "yes", "y"})
        n_manifest_success_rows = int(success_mask.sum())
        success_rule = "WRITE_OK_TRUE"
    else:
        success_mask = pd.Series(True, index=manifest.index)
        n_manifest_success_rows = int(len(manifest))
        success_rule = "ALL_ROWS_NO_WRITE_OK_COLUMN"
    array_shape: list[int] = []
    array_dtype: str | None = None
    if len(embedding_arrays) == 1:
        array_shape = list(embedding_arrays[0]["shape"])
        array_dtype = str(embedding_arrays[0]["dtype"])
    index_is_integer_nonnegative = False
    index_is_unique = False
    index_covers_array = False
    if len(index_columns) == 1:
        index_col = next(iter(index_columns))
        successful_indices = pd.to_numeric(
            manifest.loc[success_mask, index_col], errors="coerce"
        )
        index_values = successful_indices.to_numpy(dtype=float)
        index_is_integer_nonnegative = bool(
            successful_indices.notna().all()
            and np.isfinite(index_values).all()
            and np.equal(index_values, np.floor(index_values)).all()
            and (index_values >= 0).all()
        )
        index_is_unique = bool(
            index_is_integer_nonnegative and not successful_indices.duplicated().any()
        )
        if index_is_unique and len(array_shape) == 2:
            index_covers_array = set(successful_indices.astype(int)) == set(range(array_shape[0]))

    ids_nonmissing = False
    study_to_subject_consistent = False
    study_ids_unique_when_study_indexed = False
    if len(subject_columns) == 1 and len(study_columns) == 1:
        subject_col = next(iter(subject_columns))
        study_col = next(iter(study_columns))
        successful_ids = manifest.loc[success_mask, [subject_col, study_col]].copy()
        subject_valid = successful_ids[subject_col].notna() & successful_ids[subject_col].astype(str).str.strip().ne("")
        study_valid = successful_ids[study_col].notna() & successful_ids[study_col].astype(str).str.strip().ne("")
        ids_nonmissing = bool((subject_valid & study_valid).all())
        if ids_nonmissing:
            normalized = successful_ids.assign(
                _subject=successful_ids[subject_col].astype(str).str.strip(),
                _study=successful_ids[study_col].astype(str).str.strip(),
            )
            study_to_subject_consistent = bool(
                normalized.groupby("_study", dropna=False)["_subject"].nunique(dropna=False).le(1).all()
            )
            if len(index_columns) == 1 and next(iter(index_columns)) == "study_idx":
                study_ids_unique_when_study_indexed = bool(not normalized["_study"].duplicated().any())
            else:
                study_ids_unique_when_study_indexed = True
    checks = {
        "single_embeddings_array": len(embedding_arrays) == 1,
        "array_is_rank_2": len(array_shape) == 2,
        "array_has_rows": len(array_shape) == 2 and array_shape[0] > 0,
        "array_width_matches_expected": len(array_shape) == 2 and array_shape[1] == expected_width,
        "array_dtype_float32": array_dtype == "float32",
        "manifest_success_flag_values_valid": success_flag_values_valid,
        "manifest_has_success_rows": n_manifest_success_rows > 0,
        "manifest_has_single_subject_column": len(subject_columns) == 1,
        "manifest_has_single_study_column": len(study_columns) == 1,
        "manifest_ids_nonmissing": ids_nonmissing,
        "manifest_study_to_subject_consistent": study_to_subject_consistent,
        "manifest_study_ids_unique_when_study_indexed": study_ids_unique_when_study_indexed,
        "manifest_has_single_embedding_index_column": len(index_columns) == 1,
        "manifest_embedding_index_integer_nonnegative": index_is_integer_nonnegative,
        "manifest_embedding_index_unique": index_is_unique,
        "manifest_embedding_index_covers_array": index_covers_array,
        "manifest_success_rows_match_array": (
            len(array_shape) == 2 and n_manifest_success_rows == array_shape[0]
        ),
    }
    result.update(
        {
            "status": "OK" if all(checks.values()) else "ERROR",
            "checks_passed": all(checks.values()),
            "checks": checks,
            "embedding_shape": array_shape,
            "embedding_dtype": array_dtype,
            "n_manifest_rows": int(len(manifest)),
            "n_manifest_success_rows": n_manifest_success_rows,
            "manifest_success_rule": success_rule,
        }
    )
    return result


def main() -> int:
    args = parse_args()
    artifacts = []
    for raw in args.artifact:
        alias, path = parse_named_path(raw)
        artifacts.append(inspect_path(alias, path))
    embedding_pairs = []
    for raw in args.embedding_pair:
        alias, npz_path, manifest_path = parse_embedding_pair(raw)
        embedding_pairs.append(
            inspect_embedding_pair(alias, npz_path, manifest_path, args.embedding_width)
        )
    n_pair_blocking = sum(item["status"] != "OK" for item in embedding_pairs)
    payload = {
        "audit": "artifact_schema_inspection",
        "n_artifacts": len(artifacts),
        "n_ok": sum(item["status"] == "OK" for item in artifacts),
        "n_blocking": sum(item["status"] != "OK" for item in artifacts),
        "artifacts": artifacts,
        "n_embedding_pairs": len(embedding_pairs),
        "n_embedding_pair_blocking": n_pair_blocking,
        "embedding_pairs": embedding_pairs,
        "row_values_emitted": False,
        "supplied_paths_emitted": False,
    }
    write_json(payload, args.output_json)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 2 if payload["n_blocking"] or n_pair_blocking else 0


if __name__ == "__main__":
    raise SystemExit(run_guarded(main))
