"""Adjudicate duplicate clip keys using physical-source evidence when available.

This Phase 1D audit keeps three decisions separate:

1. what source/extracted evidence is available;
2. which prespecified duplicate category the evidence supports; and
3. which non-mutating resolution is proposed.

Vector equality is corroborating evidence only. It can never, by itself,
authorize deduplication. Identifier-, locator-, file-hash-, and per-group detail
is written only beneath ``--restricted-output-dir``. The aggregate directory
receives exactly five fixed-schema, identifier-free outputs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
import pandas as pd

from audit_lvef_multitask_artifacts import (
    CLIP_LOCATOR_COLUMNS,
    _canonical_manifest_scalar,
    _prepare_clip_manifest_keys,
)
from lvef_multitask_audit_utils import (
    load_table,
    require_restricted_path,
    run_guarded,
    write_aggregate_csv,
    write_json,
)
from lvef_multitask_clip_provenance import (
    DICOM_HASH_COLUMNS,
    DICOM_LOCATOR_COLUMNS,
    EXPECTED_COMPONENTS,
    FRAME_HASH_COLUMNS,
    NPZ_HASH_COLUMNS,
    NPZ_LOCATOR_COLUMNS,
    canonical_metadata_json,
    dicom_audit_lookup,
    equality_status,
    existence_status,
    extraction_lookup,
    extraction_records_for_row,
    first_locator,
    first_manifest_hash,
    named_component_paths,
    normalized_locator,
    npz_descriptor,
    parse_path_rewrites,
    prepared_clip_manifest,
    resolve_locator_path,
    selected_study_map,
    sha256_file,
)


ADJUDICATION_CATEGORIES: tuple[str, ...] = (
    "EXACT_REPEATED_MANIFEST_ROW_CONFIRMED",
    "SAME_SOURCE_LOCATOR_IDENTICAL_VECTOR_FILE_HASH_UNAVAILABLE",
    "SAME_SOURCE_LOCATOR_DIFFERENT_VECTOR",
    "DIFFERENT_SOURCE_LOCATORS_IDENTICAL_VECTOR",
    "DIFFERENT_PHYSICAL_CLIPS_KEY_COLLISION",
    "MERGE_OR_INDEX_REWRITE_ONLY",
    "SOURCE_ARTIFACT_PURGED",
    "MISSING_LOCATOR",
    "OTHER_UNRESOLVED",
)

RESOLUTIONS: dict[str, str] = {
    "EXACT_REPEATED_MANIFEST_ROW_CONFIRMED": (
        "DETERMINISTIC_DEDUPLICATION_AND_REPOOL_PERMITTED_AFTER_OWNER_AUTHORIZATION"
    ),
    "SAME_SOURCE_LOCATOR_IDENTICAL_VECTOR_FILE_HASH_UNAVAILABLE": (
        "REQUIRE_PHYSICAL_FILE_HASH_CONFIRMATION_OR_REEXTRACT_BEFORE_DEDUPLICATION"
    ),
    "SAME_SOURCE_LOCATOR_DIFFERENT_VECTOR": (
        "REEXTRACT_OR_REEMBED_AFFECTED_SOURCE_AND_REBUILD_DOWNSTREAM_STORES"
    ),
    "DIFFERENT_SOURCE_LOCATORS_IDENTICAL_VECTOR": (
        "QUARANTINE_DISTINCT_SOURCES_AND_REPAIR_KEY_SCHEMA_NO_VECTOR_BASED_DEDUPLICATION"
    ),
    "DIFFERENT_PHYSICAL_CLIPS_KEY_COLLISION": (
        "QUARANTINE_COLLISION_REPAIR_KEY_SCHEMA_AND_REPROCESS_AFFECTED_SCOPE"
    ),
    "MERGE_OR_INDEX_REWRITE_ONLY": (
        "REBUILD_MERGE_AND_STUDY_POOL_FROM_VALIDATED_COMPONENT_ROWS"
    ),
    "SOURCE_ARTIFACT_PURGED": (
        "REEXTRACT_FROM_RETAINED_DICOM_OR_REDOWNLOAD_IF_SOURCE_IS_UNAVAILABLE"
    ),
    "MISSING_LOCATOR": "RECONSTRUCT_SOURCE_MAPPING_OR_QUARANTINE_AND_REPROCESS",
    "OTHER_UNRESOLVED": "QUARANTINE_PENDING_RESTRICTED_PROVENANCE_REVIEW",
}

EVIDENCE_ITEMS: tuple[str, ...] = (
    "ownership",
    "dicom_locator",
    "dicom_file",
    "dicom_hash_in_manifest",
    "dicom_hash_newly_computable",
    "npz_locator",
    "npz_file",
    "npz_hash_in_manifest",
    "npz_hash_newly_computable",
    "frame_shape",
    "frame_count",
    "extraction_metadata",
    "extracted_content_assessment",
    "payload_excluding_permitted_rewrites",
    "vector_exact_and_numerical_1e_6",
    "stored_vs_recomputed_l2",
    "merged_component_correspondence",
    "physical_source_assessment",
    "purged_source_evidence",
)

EVIDENCE_COUNT_COLUMNS: tuple[str, ...] = (
    "component",
    "selected_scope",
    "evidence_item",
    "availability_status",
    "n_duplicate_groups",
)
REASON_COUNT_COLUMNS: tuple[str, ...] = (
    "component",
    "selected_scope",
    "adjudication_category",
    "resolution_category",
    "deterministic_deduplication_permitted",
    "quarantine_required",
    "n_duplicate_groups",
)

AGGREGATE_FILENAMES: tuple[str, ...] = (
    "duplicate_clip_evidence_availability.summary.json",
    "duplicate_clip_evidence_availability_counts.csv",
    "duplicate_clip_adjudication_v2.summary.json",
    "duplicate_clip_adjudication_v2_reason_counts.csv",
    "duplicate_clip_adjudication_v2_safety_gate.json",
)

ALLOWED_AVAILABILITY_STATUSES: frozenset[str] = frozenset(
    {
        "MISSING",
        "PARTIAL",
        "COMPLETE_EQUAL",
        "COMPLETE_DIFFERENT",
        "ALL_EXIST",
        "SOME_EXIST",
        "NONE_EXIST",
        "EQUAL_SELECTED",
        "EQUAL_OUTSIDE_SELECTED",
        "MISMATCH",
        "EQUAL",
        "DIFFERENT",
        "EXACT_EQUAL",
        "NUMERIC_EQUAL_1E_6",
        "COMPLETE_EQUAL_1E_6",
        "EXACT",
        "PERMITTED_INDEX_OR_NORM_REWRITE_ONLY",
        "VECTOR_NUMERICAL_ONLY",
        "VECTOR_MISMATCH",
        "PAYLOAD_MISMATCH",
        "MERGE_CARDINALITY_OR_INDEX_REWRITE_ONLY",
        "COUNT_MISMATCH",
        "CONFIRMED_SINGLE_PHYSICAL_SOURCE",
        "CONFIRMED_MULTIPLE_PHYSICAL_SOURCES",
        "SAME_LOCATOR_CONTENT_UNVERIFIED",
        "DIFFERENT_LOCATORS_CONTENT_UNVERIFIED",
        "MISSING_LOCATOR",
        "PARTIAL_LOCATOR",
        "UNRESOLVED",
        "CONFIRMED_EQUAL",
        "CONFIRMED_DIFFERENT",
        "UNAVAILABLE",
        "PURGED",
        "NOT_PURGED_OR_HASH_DECLARED",
    }
)
FORBIDDEN_AGGREGATE_TEXT = re.compile(
    r"(?:subject_id|study_id|clip_key|dicom_filepath|npz_path|embedding_idx|"
    r"/restricted/|/Users/|[0-9a-f]{64})",
    re.IGNORECASE,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--component-manifest", action="append", required=True)
    parser.add_argument("--component-embedding-npz", action="append", required=True)
    parser.add_argument("--component-extraction-manifest", action="append", required=True)
    parser.add_argument("--component-dicom-audit", action="append", required=True)
    parser.add_argument("--merged-manifest", type=Path, required=True)
    parser.add_argument("--merged-embedding-npz", type=Path, required=True)
    parser.add_argument("--selected-studies", type=Path, required=True)
    parser.add_argument("--dicom-root", type=Path, action="append", default=[])
    parser.add_argument(
        "--path-rewrite",
        action="append",
        default=[],
        metavar="FROM=TO",
        help="Repeat for known historical-to-SCC locator prefix rewrites.",
    )
    parser.add_argument("--aggregate-output-dir", type=Path, required=True)
    parser.add_argument("--restricted-output-dir", type=Path, required=True)
    parser.add_argument("--expected-duplicate-keys", type=int, default=32)
    parser.add_argument("--expected-embedding-dim", type=int, default=512)
    parser.add_argument("--vector-rtol", type=float, default=1e-6)
    parser.add_argument("--vector-atol", type=float, default=1e-6)
    return parser.parse_args()


def _load_embeddings(path: Path, expected_dim: int) -> np.ndarray:
    if not path.is_file():
        raise FileNotFoundError("Embedding NPZ is unavailable")
    with np.load(path, allow_pickle=False) as data:
        if set(data.files) != {"embeddings"}:
            raise ValueError("Embedding NPZ must contain exactly the embeddings array")
        embeddings = np.asarray(data["embeddings"])
    if embeddings.ndim != 2 or embeddings.shape[1] != expected_dim:
        raise ValueError("Embedding array has an unexpected shape")
    if not np.issubdtype(embeddings.dtype, np.floating):
        raise ValueError("Embedding array must be floating point")
    if not np.isfinite(embeddings).all():
        raise ValueError("Embedding array contains nonfinite values")
    return embeddings


def _attach_duplicate_vectors(
    frame: pd.DataFrame, embeddings: np.ndarray, component: str
) -> pd.DataFrame:
    if "embedding_idx" not in frame.columns:
        raise ValueError("Clip manifest lacks embedding_idx")
    numeric = pd.to_numeric(frame["embedding_idx"], errors="coerce")
    if numeric.isna().any() or not np.equal(numeric, np.floor(numeric)).all():
        raise ValueError("Clip manifest has a nonintegral embedding index")
    indices = numeric.astype(np.int64)
    if (indices < 0).any() or (indices >= len(embeddings)).any():
        raise ValueError("Embedding index is outside the paired array")
    work = frame.copy()
    work["_component"] = component
    work["_component_row"] = [int(index) for index in work.index]
    work["_embedding_vector"] = [embeddings[index].copy() for index in indices]
    return work


def _choose_locator(
    records: Sequence[Mapping[str, object]],
    candidates: Sequence[str],
    *,
    roots: Sequence[Path],
    rewrites: Sequence[tuple[str, str]],
) -> tuple[str | None, str | None, Path | None]:
    possibilities: list[tuple[str, str, Path | None]] = []
    for offset in range(len(records)):
        locator, column = first_locator(records[offset:], candidates)
        if locator is None or column is None:
            break
        resolved = resolve_locator_path(locator, roots=roots, rewrites=rewrites)
        item = (locator, column, resolved)
        if item not in possibilities:
            possibilities.append(item)
        # Avoid repeatedly returning the same first record. Explicitly inspect
        # each record on the next loop instead.
        if offset == 0:
            possibilities = []
            for record in records:
                one_locator, one_column = first_locator([record], candidates)
                if one_locator is None or one_column is None:
                    continue
                one_path = resolve_locator_path(
                    one_locator, roots=roots, rewrites=rewrites
                )
                one_item = (one_locator, one_column, one_path)
                if one_item not in possibilities:
                    possibilities.append(one_item)
            break
    if not possibilities:
        return None, None, None
    return next(
        (item for item in possibilities if item[2] is not None and item[2].is_file()),
        possibilities[0],
    )


def _row_descriptor(
    row: pd.Series,
    extraction_records: Sequence[Mapping[str, object]],
    dicom_records: Sequence[Mapping[str, object]],
    *,
    dicom_roots: Sequence[Path],
    rewrites: Sequence[tuple[str, str]],
) -> dict[str, Any]:
    vector = np.asarray(row["_embedding_vector"])
    row_record = row.to_dict()
    records: list[Mapping[str, object]] = [
        row_record,
        *extraction_records,
        *dicom_records,
    ]
    dicom_locator, dicom_locator_column, dicom_path = _choose_locator(
        records,
        DICOM_LOCATOR_COLUMNS,
        roots=dicom_roots,
        rewrites=rewrites,
    )
    npz_locator, npz_locator_column, npz_path = _choose_locator(
        records,
        NPZ_LOCATOR_COLUMNS,
        roots=(),
        rewrites=rewrites,
    )
    dicom_manifest_hash, dicom_hash_column = first_manifest_hash(
        records, DICOM_HASH_COLUMNS
    )
    npz_manifest_hash, npz_hash_column = first_manifest_hash(records, NPZ_HASH_COLUMNS)
    frame_manifest_hash, frame_hash_column = first_manifest_hash(
        records, FRAME_HASH_COLUMNS
    )
    npz = npz_descriptor(npz_path, compute_hash=True)
    stored_norm = pd.to_numeric(
        pd.Series([row.get("embedding_l2_norm")]), errors="coerce"
    ).iloc[0]
    return {
        "component": str(row["_component"]),
        "component_row": int(row["_component_row"]),
        "subject_id": str(row["_audit_subject"]),
        "study_id": str(row["_audit_study"]),
        "clip_key": json.dumps(row["_audit_clip_key"], separators=(",", ":")),
        "dicom_locator": dicom_locator,
        "dicom_locator_source_column": dicom_locator_column,
        "dicom_resolved_path": None if dicom_path is None else str(dicom_path),
        "dicom_exists": bool(dicom_path is not None and dicom_path.is_file()),
        "dicom_manifest_sha256": dicom_manifest_hash,
        "dicom_manifest_hash_source_column": dicom_hash_column,
        "dicom_computed_sha256": sha256_file(dicom_path),
        "npz_locator": npz_locator,
        "npz_locator_source_column": npz_locator_column,
        "npz_resolved_path": None if npz_path is None else str(npz_path),
        "npz_exists": bool(npz["exists"]),
        "npz_manifest_sha256": npz_manifest_hash,
        "npz_manifest_hash_source_column": npz_hash_column,
        "npz_computed_sha256": npz["computed_sha256"],
        "frame_manifest_sha256": frame_manifest_hash,
        "frame_manifest_hash_source_column": frame_hash_column,
        "frames_sha256": npz["frames_sha256"],
        "frames_shape": npz["frames_shape"],
        "frames_count": npz["frames_count"],
        "frames_dtype": npz["frames_dtype"],
        "npz_inspection_status": npz["status"],
        "npz_array_metadata_json": npz["array_metadata_json"],
        "extraction_match_count": len(extraction_records),
        "extraction_metadata_json": canonical_metadata_json(extraction_records),
        "embedding_idx": int(float(row["embedding_idx"])),
        "embedding_vector_sha256": hashlib.sha256(
            np.ascontiguousarray(vector).tobytes()
        ).hexdigest(),
        "embedding_vector_dtype": str(vector.dtype),
        "embedding_vector_shape": json.dumps(list(vector.shape)),
        "embedding_l2_norm_stored": None if pd.isna(stored_norm) else float(stored_norm),
        "embedding_l2_norm_recomputed": float(np.linalg.norm(vector)),
        "write_ok": True,
        "_embedding_vector": vector,
    }


def _manifest_columns(frame: pd.DataFrame, *, permitted_rewrites_removed: bool) -> list[str]:
    excluded = {"_component", "_component_row", "_embedding_vector"}
    excluded.update(column for column in frame.columns if str(column).startswith("_audit_"))
    if permitted_rewrites_removed:
        excluded.update({"embedding_idx", "embedding_l2_norm"})
    return sorted(column for column in frame.columns if column not in excluded)


def _canonical_row_tuple(row: pd.Series, columns: Iterable[str]) -> tuple[str, ...]:
    return tuple(_canonical_manifest_scalar(row.get(column)) for column in columns)


def _vectors_equal(
    vectors: Sequence[np.ndarray], *, rtol: float, atol: float
) -> tuple[bool, bool]:
    if not vectors:
        return False, False
    first = vectors[0]
    return (
        all(np.array_equal(first, vector) for vector in vectors[1:]),
        all(
            np.allclose(first, vector, rtol=rtol, atol=atol, equal_nan=False)
            for vector in vectors[1:]
        ),
    )


def _vector_multisets_match(
    left: Sequence[np.ndarray],
    right: Sequence[np.ndarray],
    *,
    rtol: float,
    atol: float,
) -> tuple[bool, bool]:
    if len(left) != len(right):
        return False, False
    left_hashes = Counter(
        hashlib.sha256(np.ascontiguousarray(vector).tobytes()).hexdigest()
        for vector in left
    )
    right_hashes = Counter(
        hashlib.sha256(np.ascontiguousarray(vector).tobytes()).hexdigest()
        for vector in right
    )
    exact = left_hashes == right_hashes
    unmatched = list(range(len(right)))
    for vector in left:
        match = next(
            (
                index
                for index in unmatched
                if np.allclose(
                    vector, right[index], rtol=rtol, atol=atol, equal_nan=False
                )
            ),
            None,
        )
        if match is None:
            return exact, False
        unmatched.remove(match)
    return exact, True


def _norm_status(
    descriptors: Sequence[Mapping[str, object]], *, rtol: float, atol: float
) -> str:
    stored = [descriptor.get("embedding_l2_norm_stored") for descriptor in descriptors]
    recomputed = [
        descriptor.get("embedding_l2_norm_recomputed") for descriptor in descriptors
    ]
    if not any(value is not None for value in stored):
        return "MISSING"
    if any(value is None for value in stored):
        return "PARTIAL"
    equal = all(
        math.isclose(float(left), float(right), rel_tol=rtol, abs_tol=atol)
        for left, right in zip(stored, recomputed, strict=True)
    )
    return "COMPLETE_EQUAL_1E_6" if equal else "DIFFERENT"


def _selected_scope(
    descriptors: Sequence[Mapping[str, object]], selected: Mapping[str, str]
) -> tuple[str, str]:
    pairs = {(str(row["subject_id"]), str(row["study_id"])) for row in descriptors}
    if len(pairs) != 1:
        return "ownership_mismatch", "MISMATCH"
    subject, study = next(iter(pairs))
    if study not in selected:
        return "outside_selected", "EQUAL_OUTSIDE_SELECTED"
    if selected[study] != subject:
        return "ownership_mismatch", "MISMATCH"
    return "selected", "EQUAL_SELECTED"


def _source_locator_status(descriptors: Sequence[Mapping[str, object]]) -> str:
    dicom = equality_status(
        [row.get("dicom_locator") for row in descriptors], len(descriptors)
    )
    if dicom != "MISSING":
        return dicom
    return equality_status(
        [row.get("npz_locator") for row in descriptors], len(descriptors)
    )


def _physical_source_assessment(
    descriptors: Sequence[Mapping[str, object]],
    *,
    source_locator_status: str,
) -> tuple[str, bool]:
    count = len(descriptors)
    hash_statuses = {
        "dicom_manifest": equality_status(
            [row.get("dicom_manifest_sha256") for row in descriptors], count
        ),
        "dicom_computed": equality_status(
            [row.get("dicom_computed_sha256") for row in descriptors], count
        ),
        "npz_manifest": equality_status(
            [row.get("npz_manifest_sha256") for row in descriptors], count
        ),
        "npz_computed": equality_status(
            [row.get("npz_computed_sha256") for row in descriptors], count
        ),
        "frames": equality_status(
            [row.get("frames_sha256") or row.get("frame_manifest_sha256") for row in descriptors],
            count,
        ),
    }
    confirmed_same = any(status == "COMPLETE_EQUAL" for status in hash_statuses.values())
    confirmed_different = any(
        status == "COMPLETE_DIFFERENT" for status in hash_statuses.values()
    )
    any_locator = any(
        row.get("dicom_locator") is not None or row.get("npz_locator") is not None
        for row in descriptors
    )
    all_artifacts_missing = bool(descriptors) and all(
        not bool(row.get("dicom_exists")) and not bool(row.get("npz_exists"))
        for row in descriptors
    )
    no_declared_hash = not any(
        row.get("dicom_manifest_sha256")
        or row.get("npz_manifest_sha256")
        or row.get("frame_manifest_sha256")
        for row in descriptors
    )
    purged = any_locator and all_artifacts_missing and no_declared_hash
    if source_locator_status == "MISSING":
        return "MISSING_LOCATOR", purged
    if source_locator_status == "PARTIAL":
        return "PARTIAL_LOCATOR", purged
    if source_locator_status == "COMPLETE_DIFFERENT" and confirmed_different:
        return "CONFIRMED_MULTIPLE_PHYSICAL_SOURCES", purged
    if source_locator_status == "COMPLETE_EQUAL" and confirmed_same:
        return "CONFIRMED_SINGLE_PHYSICAL_SOURCE", purged
    if source_locator_status == "COMPLETE_EQUAL":
        return "SAME_LOCATOR_CONTENT_UNVERIFIED", purged
    if source_locator_status == "COMPLETE_DIFFERENT":
        return "DIFFERENT_LOCATORS_CONTENT_UNVERIFIED", purged
    return "UNRESOLVED", purged


def _merged_correspondence(
    source_group: pd.DataFrame,
    merged_group: pd.DataFrame,
    source_vectors: Sequence[np.ndarray],
    merged_vectors: Sequence[np.ndarray],
    *,
    rtol: float,
    atol: float,
) -> str:
    common_payload = sorted(
        (
            set(_manifest_columns(source_group, permitted_rewrites_removed=True))
            | set(_manifest_columns(merged_group, permitted_rewrites_removed=True))
        )
    )
    source_payload = Counter(
        _canonical_row_tuple(row, common_payload)
        for _, row in source_group.iterrows()
    )
    merged_payload = Counter(
        _canonical_row_tuple(row, common_payload)
        for _, row in merged_group.iterrows()
    )
    exact_vectors, close_vectors = _vector_multisets_match(
        source_vectors, merged_vectors, rtol=rtol, atol=atol
    )
    if len(source_group) == len(merged_group):
        if source_payload != merged_payload:
            return "PAYLOAD_MISMATCH"
        if exact_vectors:
            source_full = Counter(
                _canonical_row_tuple(
                    row,
                    sorted(
                        set(_manifest_columns(source_group, permitted_rewrites_removed=False))
                        | set(_manifest_columns(merged_group, permitted_rewrites_removed=False))
                    ),
                )
                for _, row in source_group.iterrows()
            )
            merged_full = Counter(
                _canonical_row_tuple(
                    row,
                    sorted(
                        set(_manifest_columns(source_group, permitted_rewrites_removed=False))
                        | set(_manifest_columns(merged_group, permitted_rewrites_removed=False))
                    ),
                )
                for _, row in merged_group.iterrows()
            )
            return "EXACT" if source_full == merged_full else "PERMITTED_INDEX_OR_NORM_REWRITE_ONLY"
        return "VECTOR_NUMERICAL_ONLY" if close_vectors else "VECTOR_MISMATCH"

    # A merge-only cardinality defect is recognized only if every merged row's
    # permitted payload and vector corresponds to a component row.
    source_payload_values = set(source_payload)
    payload_contained = all(
        _canonical_row_tuple(row, common_payload) in source_payload_values
        for _, row in merged_group.iterrows()
    )
    vector_contained = all(
        any(
            np.allclose(vector, candidate, rtol=rtol, atol=atol, equal_nan=False)
            for candidate in source_vectors
        )
        for vector in merged_vectors
    )
    if payload_contained and vector_contained:
        return "MERGE_CARDINALITY_OR_INDEX_REWRITE_ONLY"
    return "COUNT_MISMATCH"


def classify_duplicate_group_v2(evidence: Mapping[str, object]) -> str:
    """Assign one requested category without permitting vector-only deduplication."""
    locator_status = str(evidence["source_locator_status"])
    physical = str(evidence["physical_source_assessment"])
    vectors_equal = bool(evidence["source_vectors_numerically_equal"])
    vectors_exact = bool(evidence["source_vectors_exact_equal"])
    merge_status = str(evidence["merged_component_correspondence"])
    extracted_content = str(evidence["extracted_content_assessment"])
    extraction_metadata = str(evidence["extraction_metadata_status"])

    if locator_status in {"MISSING", "PARTIAL"}:
        return "MISSING_LOCATOR"
    if merge_status == "MERGE_CARDINALITY_OR_INDEX_REWRITE_ONLY":
        return "MERGE_OR_INDEX_REWRITE_ONLY"
    if physical == "CONFIRMED_MULTIPLE_PHYSICAL_SOURCES":
        return "DIFFERENT_PHYSICAL_CLIPS_KEY_COLLISION"
    if locator_status == "COMPLETE_EQUAL" and not vectors_equal:
        return "SAME_SOURCE_LOCATOR_DIFFERENT_VECTOR"
    if (
        bool(evidence["payload_equal_excluding_permitted_rewrites"])
        and physical == "CONFIRMED_SINGLE_PHYSICAL_SOURCE"
        and extracted_content == "CONFIRMED_EQUAL"
        and extraction_metadata == "COMPLETE_EQUAL"
        and vectors_exact
        and str(evidence["stored_vs_recomputed_l2"]) == "COMPLETE_EQUAL_1E_6"
        and merge_status in {"EXACT", "PERMITTED_INDEX_OR_NORM_REWRITE_ONLY"}
    ):
        return "EXACT_REPEATED_MANIFEST_ROW_CONFIRMED"
    if bool(evidence["source_artifact_purged"]):
        return "SOURCE_ARTIFACT_PURGED"
    if (
        locator_status == "COMPLETE_EQUAL"
        and vectors_equal
        and extracted_content != "CONFIRMED_EQUAL"
    ):
        return "SAME_SOURCE_LOCATOR_IDENTICAL_VECTOR_FILE_HASH_UNAVAILABLE"
    if locator_status == "COMPLETE_DIFFERENT" and vectors_equal:
        return "DIFFERENT_SOURCE_LOCATORS_IDENTICAL_VECTOR"
    if merge_status in {"PERMITTED_INDEX_OR_NORM_REWRITE_ONLY", "EXACT"} and int(
        evidence["source_count"]
    ) != int(evidence["merged_count"]):
        return "MERGE_OR_INDEX_REWRITE_ONLY"
    return "OTHER_UNRESOLVED"


def resolution_for_category(category: str) -> dict[str, object]:
    if category not in RESOLUTIONS:
        raise ValueError("Unknown duplicate adjudication category")
    dedup = category == "EXACT_REPEATED_MANIFEST_ROW_CONFIRMED"
    merge_only = category == "MERGE_OR_INDEX_REWRITE_ONLY"
    return {
        "resolution_category": RESOLUTIONS[category],
        "deterministic_deduplication_permitted": dedup,
        "quarantine_required": not (dedup or merge_only),
        "canonical_store_mutated": False,
        "owner_authorization_required": True,
    }


def _group_evidence(
    key: tuple[str, ...],
    source_group: pd.DataFrame,
    merged_group: pd.DataFrame,
    extraction_lookups: Mapping[str, Mapping[tuple[str, str, str], Sequence[Mapping[str, object]]]],
    dicom_lookups: Mapping[str, Mapping[tuple[str, str, str], Sequence[Mapping[str, object]]]],
    selected: Mapping[str, str],
    *,
    dicom_roots: Sequence[Path],
    rewrites: Sequence[tuple[str, str]],
    rtol: float,
    atol: float,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], list[dict[str, str]]]:
    source_descriptors: list[dict[str, Any]] = []
    for _, row in source_group.iterrows():
        component = str(row["_component"])
        extraction_records = extraction_records_for_row(
            row, extraction_lookups[component]
        )
        dicom_records = extraction_records_for_row(row, dicom_lookups[component])
        source_descriptors.append(
            _row_descriptor(
                row,
                extraction_records,
                dicom_records,
                dicom_roots=dicom_roots,
                rewrites=rewrites,
            )
        )
    merged_descriptors: list[dict[str, Any]] = []
    for _, row in merged_group.iterrows():
        matching_records: list[Mapping[str, object]] = []
        matching_dicom_records: list[Mapping[str, object]] = []
        for lookup in extraction_lookups.values():
            matching_records.extend(extraction_records_for_row(row, lookup))
        for lookup in dicom_lookups.values():
            matching_dicom_records.extend(extraction_records_for_row(row, lookup))
        merged_descriptors.append(
            _row_descriptor(
                row,
                matching_records,
                matching_dicom_records,
                dicom_roots=dicom_roots,
                rewrites=rewrites,
            )
        )

    all_descriptors = [*source_descriptors, *merged_descriptors]
    scope, ownership_status = _selected_scope(all_descriptors, selected)
    source_count = len(source_descriptors)
    source_vectors = [np.asarray(row["_embedding_vector"]) for row in source_descriptors]
    merged_vectors = [np.asarray(row["_embedding_vector"]) for row in merged_descriptors]
    source_exact, source_close = _vectors_equal(
        source_vectors, rtol=rtol, atol=atol
    )
    source_full_columns = _manifest_columns(
        source_group, permitted_rewrites_removed=False
    )
    source_payload_columns = _manifest_columns(
        source_group, permitted_rewrites_removed=True
    )
    source_full_rows_equal = len(
        {
            _canonical_row_tuple(row, source_full_columns)
            for _, row in source_group.iterrows()
        }
    ) == 1
    source_payload_equal = len(
        {
            _canonical_row_tuple(row, source_payload_columns)
            for _, row in source_group.iterrows()
        }
    ) == 1
    source_locator_status = _source_locator_status(source_descriptors)
    physical, purged = _physical_source_assessment(
        source_descriptors, source_locator_status=source_locator_status
    )
    extraction_metadata_status = equality_status(
        [row.get("extraction_metadata_json") for row in source_descriptors],
        source_count,
    )
    extracted_hash_statuses = (
        equality_status(
            [row.get("npz_manifest_sha256") for row in source_descriptors],
            source_count,
        ),
        equality_status(
            [row.get("npz_computed_sha256") for row in source_descriptors],
            source_count,
        ),
        equality_status(
            [
                row.get("frames_sha256") or row.get("frame_manifest_sha256")
                for row in source_descriptors
            ],
            source_count,
        ),
    )
    if "COMPLETE_DIFFERENT" in extracted_hash_statuses:
        extracted_content_assessment = "CONFIRMED_DIFFERENT"
    elif "COMPLETE_EQUAL" in extracted_hash_statuses:
        extracted_content_assessment = "CONFIRMED_EQUAL"
    elif "PARTIAL" in extracted_hash_statuses:
        extracted_content_assessment = "PARTIAL"
    else:
        extracted_content_assessment = "UNAVAILABLE"
    merge_status = _merged_correspondence(
        source_group,
        merged_group,
        source_vectors,
        merged_vectors,
        rtol=rtol,
        atol=atol,
    )
    norm_status = _norm_status(
        [*source_descriptors, *merged_descriptors], rtol=rtol, atol=atol
    )
    components = sorted(set(source_group["_component"].astype(str))) or ["merged_only"]
    evidence: dict[str, Any] = {
        "source_count": source_count,
        "merged_count": len(merged_descriptors),
        "source_full_rows_equal": source_full_rows_equal,
        "payload_equal_excluding_permitted_rewrites": source_payload_equal,
        "source_vectors_exact_equal": source_exact,
        "source_vectors_numerically_equal": source_close,
        "source_locator_status": source_locator_status,
        "physical_source_assessment": physical,
        "extraction_metadata_status": extraction_metadata_status,
        "extracted_content_assessment": extracted_content_assessment,
        "source_artifact_purged": purged,
        "stored_vs_recomputed_l2": norm_status,
        "merged_component_correspondence": merge_status,
    }
    category = classify_duplicate_group_v2(evidence)
    resolution = resolution_for_category(category)

    availability = {
        "ownership": ownership_status,
        "dicom_locator": equality_status(
            [row.get("dicom_locator") for row in source_descriptors], source_count
        ),
        "dicom_file": existence_status(
            [bool(row.get("dicom_exists")) for row in source_descriptors], source_count
        ),
        "dicom_hash_in_manifest": equality_status(
            [row.get("dicom_manifest_sha256") for row in source_descriptors], source_count
        ),
        "dicom_hash_newly_computable": equality_status(
            [row.get("dicom_computed_sha256") for row in source_descriptors], source_count
        ),
        "npz_locator": equality_status(
            [row.get("npz_locator") for row in source_descriptors], source_count
        ),
        "npz_file": existence_status(
            [bool(row.get("npz_exists")) for row in source_descriptors], source_count
        ),
        "npz_hash_in_manifest": equality_status(
            [row.get("npz_manifest_sha256") for row in source_descriptors], source_count
        ),
        "npz_hash_newly_computable": equality_status(
            [row.get("npz_computed_sha256") for row in source_descriptors], source_count
        ),
        "frame_shape": equality_status(
            [row.get("frames_shape") for row in source_descriptors], source_count
        ),
        "frame_count": equality_status(
            [row.get("frames_count") for row in source_descriptors], source_count
        ),
        "extraction_metadata": extraction_metadata_status,
        "extracted_content_assessment": extracted_content_assessment,
        "payload_excluding_permitted_rewrites": (
            "EQUAL" if source_payload_equal else "DIFFERENT"
        ),
        "vector_exact_and_numerical_1e_6": (
            "EXACT_EQUAL"
            if source_exact
            else "NUMERIC_EQUAL_1E_6"
            if source_close
            else "DIFFERENT"
        ),
        "stored_vs_recomputed_l2": norm_status,
        "merged_component_correspondence": merge_status,
        "physical_source_assessment": physical,
        "purged_source_evidence": "PURGED" if purged else "NOT_PURGED_OR_HASH_DECLARED",
    }

    restricted_evidence: dict[str, Any] = {
        "subject_id": key[0] if key else "<MISSING>",
        "study_id": key[1] if len(key) > 1 else "<MISSING>",
        "clip_key": json.dumps(key, separators=(",", ":")),
        "components": ";".join(components),
        "selected_scope": scope,
        **evidence,
        **{f"availability__{name}": status for name, status in availability.items()},
        "source_dicom_locators_json": json.dumps(
            [row.get("dicom_locator") for row in source_descriptors]
        ),
        "source_npz_locators_json": json.dumps(
            [row.get("npz_locator") for row in source_descriptors]
        ),
        "source_dicom_manifest_hashes_json": json.dumps(
            [row.get("dicom_manifest_sha256") for row in source_descriptors]
        ),
        "source_dicom_computed_hashes_json": json.dumps(
            [row.get("dicom_computed_sha256") for row in source_descriptors]
        ),
        "source_npz_manifest_hashes_json": json.dumps(
            [row.get("npz_manifest_sha256") for row in source_descriptors]
        ),
        "source_npz_computed_hashes_json": json.dumps(
            [row.get("npz_computed_sha256") for row in source_descriptors]
        ),
        "source_frame_hashes_json": json.dumps(
            [row.get("frames_sha256") for row in source_descriptors]
        ),
        "source_frame_shapes_json": json.dumps(
            [row.get("frames_shape") for row in source_descriptors]
        ),
        "source_frame_counts_json": json.dumps(
            [row.get("frames_count") for row in source_descriptors]
        ),
        "source_extraction_metadata_json": json.dumps(
            [row.get("extraction_metadata_json") for row in source_descriptors]
        ),
        "source_vector_hashes_json": json.dumps(
            [row.get("embedding_vector_sha256") for row in source_descriptors]
        ),
        "merged_vector_hashes_json": json.dumps(
            [row.get("embedding_vector_sha256") for row in merged_descriptors]
        ),
    }
    restricted_adjudication = {
        "subject_id": restricted_evidence["subject_id"],
        "study_id": restricted_evidence["study_id"],
        "clip_key": restricted_evidence["clip_key"],
        "components": restricted_evidence["components"],
        "selected_scope": scope,
        "adjudication_category": category,
        "physical_source_assessment": physical,
        "vector_equality_is_sufficient_for_deduplication": False,
        "evidence_basis": ";".join(
            sorted(
                {
                    f"{item}={status}"
                    for item, status in availability.items()
                }
            )
        ),
    }
    restricted_resolution = {
        "subject_id": restricted_evidence["subject_id"],
        "study_id": restricted_evidence["study_id"],
        "clip_key": restricted_evidence["clip_key"],
        "components": restricted_evidence["components"],
        "selected_scope": scope,
        "adjudication_category": category,
        **resolution,
    }
    aggregate_availability = [
        {
            "component": component,
            "selected_scope": scope,
            "evidence_item": item,
            "availability_status": availability[item],
        }
        for component in components
        for item in EVIDENCE_ITEMS
    ]
    return (
        restricted_evidence,
        restricted_adjudication,
        restricted_resolution,
        aggregate_availability,
    )


def _target_paths_available(root: Path) -> None:
    existing = [name for name in AGGREGATE_FILENAMES if (root / name).exists()]
    if existing:
        raise ValueError("Phase 1D duplicate aggregate output already exists")


def _validate_written_aggregate_outputs(
    root: Path,
    availability_counts: pd.DataFrame,
    reason_counts: pd.DataFrame,
) -> None:
    expected_before_safety = set(AGGREGATE_FILENAMES) - {
        "duplicate_clip_adjudication_v2_safety_gate.json"
    }
    actual = {path.name for path in root.iterdir() if path.is_file()}
    if actual != expected_before_safety:
        raise ValueError("Duplicate audit did not emit the exact pre-safety output set")
    if list(availability_counts.columns) != list(EVIDENCE_COUNT_COLUMNS):
        raise ValueError("Unexpected duplicate evidence-count schema")
    if list(reason_counts.columns) != list(REASON_COUNT_COLUMNS):
        raise ValueError("Unexpected duplicate reason-count schema")
    if not set(availability_counts["component"]).issubset(
        set(EXPECTED_COMPONENTS) | {"merged_only"}
    ):
        raise ValueError("Unexpected component in duplicate aggregate output")
    if not set(availability_counts["selected_scope"]).issubset(
        {"selected", "outside_selected", "ownership_mismatch"}
    ):
        raise ValueError("Unexpected selected scope in duplicate aggregate output")
    if not set(availability_counts["evidence_item"]).issubset(set(EVIDENCE_ITEMS)):
        raise ValueError("Unexpected evidence item in duplicate aggregate output")
    if not set(availability_counts["availability_status"]).issubset(
        ALLOWED_AVAILABILITY_STATUSES
    ):
        raise ValueError("Unexpected evidence status in duplicate aggregate output")
    if not set(reason_counts["adjudication_category"]).issubset(
        set(ADJUDICATION_CATEGORIES)
    ):
        raise ValueError("Unexpected duplicate category in aggregate output")
    if not set(reason_counts["resolution_category"]).issubset(set(RESOLUTIONS.values())):
        raise ValueError("Unexpected duplicate resolution in aggregate output")
    aggregate_text = "\n".join(
        (root / name).read_text(errors="replace") for name in sorted(actual)
    )
    if FORBIDDEN_AGGREGATE_TEXT.search(aggregate_text):
        raise ValueError("Restricted token or value detected in duplicate aggregate output")


def main() -> int:
    args = parse_args()
    if args.expected_duplicate_keys < 0 or args.expected_embedding_dim <= 0:
        raise ValueError("Expected counts/dimension are invalid")
    if args.vector_rtol != 1e-6 or args.vector_atol != 1e-6:
        raise ValueError("Phase 1D vector tolerance is prespecified at rtol=atol=1e-6")

    manifests = named_component_paths(
        args.component_manifest, label="clip manifest"
    )
    embeddings = named_component_paths(
        args.component_embedding_npz, label="clip embedding"
    )
    extractions = named_component_paths(
        args.component_extraction_manifest, label="extraction manifest"
    )
    dicom_audits = named_component_paths(
        args.component_dicom_audit, label="DICOM audit"
    )
    if (
        set(manifests) != set(embeddings)
        or set(manifests) != set(extractions)
        or set(manifests) != set(dicom_audits)
    ):
        raise ValueError("Component input labels differ")
    required_paths = [
        *manifests.values(),
        *embeddings.values(),
        *extractions.values(),
        *dicom_audits.values(),
        args.merged_manifest,
        args.merged_embedding_npz,
        args.selected_studies,
    ]
    if not all(path.is_file() for path in required_paths):
        raise FileNotFoundError("A required duplicate-audit input is unavailable")

    rewrites = parse_path_rewrites(args.path_rewrite)
    dicom_roots = tuple(path.expanduser() for path in args.dicom_root)
    selected = selected_study_map(load_table(args.selected_studies))
    extraction_lookups = {
        name: extraction_lookup(load_table(path)) for name, path in extractions.items()
    }
    dicom_lookups = {
        name: dicom_audit_lookup(load_table(path))
        for name, path in dicom_audits.items()
    }

    component_success = {
        name: prepared_clip_manifest(path) for name, path in manifests.items()
    }
    merged_success = prepared_clip_manifest(args.merged_manifest)
    all_frames = [merged_success, *component_success.values()]
    locators = tuple(
        column
        for column in CLIP_LOCATOR_COLUMNS
        if all(column in frame.columns for frame in all_frames)
    )
    if not locators:
        raise ValueError("No shared stable clip-key locator schema")
    prepared_components = {
        name: _prepare_clip_manifest_keys(frame, locators)
        for name, frame in component_success.items()
    }
    prepared_merged = _prepare_clip_manifest_keys(merged_success, locators)
    nonempty_prepared_components = [
        frame for frame in prepared_components.values() if not frame.empty
    ]
    if not nonempty_prepared_components:
        raise ValueError("All component clip manifests are empty")
    prepared_source = pd.concat(
        nonempty_prepared_components, ignore_index=True, sort=False
    )
    if prepared_source["_audit_key_missing"].any() or prepared_merged[
        "_audit_key_missing"
    ].any():
        raise ValueError("Clip-key locator schema has missing values")
    source_counts = Counter(prepared_source["_audit_clip_key"])
    merged_counts = Counter(prepared_merged["_audit_clip_key"])
    duplicate_keys = {
        key
        for key in set(source_counts) | set(merged_counts)
        if source_counts[key] > 1 or merged_counts[key] > 1
    }

    source_frames: list[pd.DataFrame] = []
    for name in EXPECTED_COMPONENTS:
        rows = prepared_components[name]
        duplicate_rows = rows[rows["_audit_clip_key"].isin(duplicate_keys)].copy()
        source_frames.append(
            _attach_duplicate_vectors(
                duplicate_rows,
                _load_embeddings(embeddings[name], args.expected_embedding_dim),
                name,
            )
        )
    nonempty_source_frames = [frame for frame in source_frames if not frame.empty]
    if not nonempty_source_frames and duplicate_keys:
        raise ValueError("Duplicate keys are absent from component rows")
    source = (
        pd.concat(nonempty_source_frames, ignore_index=True, sort=False)
        if nonempty_source_frames
        else pd.DataFrame()
    )
    merged = _attach_duplicate_vectors(
        prepared_merged[
            prepared_merged["_audit_clip_key"].isin(duplicate_keys)
        ].copy(),
        _load_embeddings(args.merged_embedding_npz, args.expected_embedding_dim),
        "merged",
    )

    restricted_evidence_rows: list[dict[str, Any]] = []
    restricted_adjudication_rows: list[dict[str, Any]] = []
    restricted_resolution_rows: list[dict[str, Any]] = []
    availability_memberships: defaultdict[
        tuple[str, str, str, str], set[tuple[str, ...]]
    ] = defaultdict(set)
    reason_memberships: defaultdict[
        tuple[str, str, str, str, bool, bool], set[tuple[str, ...]]
    ] = defaultdict(set)

    for key in sorted(duplicate_keys):
        source_group = source[source["_audit_clip_key"] == key].copy()
        merged_group = merged[merged["_audit_clip_key"] == key].copy()
        evidence_row, adjudication_row, resolution_row, availability_rows = _group_evidence(
            key,
            source_group,
            merged_group,
            extraction_lookups,
            dicom_lookups,
            selected,
            dicom_roots=dicom_roots,
            rewrites=rewrites,
            rtol=args.vector_rtol,
            atol=args.vector_atol,
        )
        restricted_evidence_rows.append(evidence_row)
        restricted_adjudication_rows.append(adjudication_row)
        restricted_resolution_rows.append(resolution_row)
        for row in availability_rows:
            availability_memberships[
                (
                    row["component"],
                    row["selected_scope"],
                    row["evidence_item"],
                    row["availability_status"],
                )
            ].add(key)
        for component in str(adjudication_row["components"]).split(";"):
            reason_memberships[
                (
                    component,
                    str(adjudication_row["selected_scope"]),
                    str(adjudication_row["adjudication_category"]),
                    str(resolution_row["resolution_category"]),
                    bool(resolution_row["deterministic_deduplication_permitted"]),
                    bool(resolution_row["quarantine_required"]),
                )
            ].add(key)

    availability_counts = pd.DataFrame(
        [
            {
                "component": component,
                "selected_scope": scope,
                "evidence_item": item,
                "availability_status": status,
                "n_duplicate_groups": len(keys),
            }
            for (component, scope, item, status), keys in sorted(
                availability_memberships.items()
            )
        ],
        columns=EVIDENCE_COUNT_COLUMNS,
    )
    reason_counts = pd.DataFrame(
        [
            {
                "component": component,
                "selected_scope": scope,
                "adjudication_category": category,
                "resolution_category": resolution,
                "deterministic_deduplication_permitted": dedup,
                "quarantine_required": quarantine,
                "n_duplicate_groups": len(keys),
            }
            for (
                component,
                scope,
                category,
                resolution,
                dedup,
                quarantine,
            ), keys in sorted(reason_memberships.items())
        ],
        columns=REASON_COUNT_COLUMNS,
    )

    restricted_dir = require_restricted_path(args.restricted_output_dir)
    aggregate_dir = args.aggregate_output_dir.expanduser().resolve()
    if (
        restricted_dir == aggregate_dir
        or restricted_dir in aggregate_dir.parents
        or aggregate_dir in restricted_dir.parents
    ):
        raise ValueError("Aggregate and restricted output trees must be disjoint")
    aggregate_dir.mkdir(parents=True, exist_ok=True)
    _target_paths_available(aggregate_dir)

    pd.DataFrame(restricted_evidence_rows).to_csv(
        restricted_dir / "duplicate_clip_evidence_availability_v2_restricted.csv",
        index=False,
    )
    pd.DataFrame(restricted_adjudication_rows).to_csv(
        restricted_dir / "duplicate_clip_adjudication_v2_restricted.csv", index=False
    )
    pd.DataFrame(restricted_resolution_rows).to_csv(
        restricted_dir / "duplicate_clip_resolution_v2_restricted.csv", index=False
    )

    write_aggregate_csv(
        availability_counts,
        aggregate_dir / "duplicate_clip_evidence_availability_counts.csv",
    )
    category_counts = Counter(
        str(row["adjudication_category"]) for row in restricted_adjudication_rows
    )
    availability_summary = {
        "audit": "duplicate_clip_evidence_availability_v2",
        "status": "COMPLETE" if len(duplicate_keys) == args.expected_duplicate_keys else "BLOCKED_COUNT_MISMATCH",
        "n_duplicate_groups": len(duplicate_keys),
        "n_expected_duplicate_groups": args.expected_duplicate_keys,
        "expected_duplicate_group_count_matches": len(duplicate_keys)
        == args.expected_duplicate_keys,
        "n_components": len(EXPECTED_COMPONENTS),
        "n_evidence_dimensions": len(EVIDENCE_ITEMS),
        "n_group_level_restricted_records": len(restricted_evidence_rows),
        "physical_source_evidence_required_for_deduplication": True,
        "vector_only_deduplication_permitted": False,
        "identifiers_or_locators_emitted": False,
    }
    write_json(
        availability_summary,
        aggregate_dir / "duplicate_clip_evidence_availability.summary.json",
    )
    adjudication_summary = {
        "audit": "duplicate_clip_adjudication_v2",
        "status": "AUDIT_COMPLETE_STORE_UNCHANGED"
        if len(duplicate_keys) == args.expected_duplicate_keys
        else "BLOCKED_COUNT_MISMATCH",
        "n_duplicate_groups": len(duplicate_keys),
        "n_expected_duplicate_groups": args.expected_duplicate_keys,
        "expected_duplicate_group_count_matches": len(duplicate_keys)
        == args.expected_duplicate_keys,
        "category_counts": {
            category: int(category_counts.get(category, 0))
            for category in ADJUDICATION_CATEGORIES
        },
        "n_deterministic_deduplication_permitted": int(
            category_counts.get("EXACT_REPEATED_MANIFEST_ROW_CONFIRMED", 0)
        ),
        "n_quarantine_required": int(
            sum(bool(row["quarantine_required"]) for row in restricted_resolution_rows)
        ),
        "vector_rtol": args.vector_rtol,
        "vector_atol": args.vector_atol,
        "vector_only_deduplication_permitted": False,
        "canonical_store_mutated": False,
        "embedding_regeneration_performed": False,
        "model_fitting_performed": False,
        "test_performance_accessed": False,
        "embedding_authority_resolved": False,
    }
    write_json(
        adjudication_summary,
        aggregate_dir / "duplicate_clip_adjudication_v2.summary.json",
    )
    write_aggregate_csv(
        reason_counts,
        aggregate_dir / "duplicate_clip_adjudication_v2_reason_counts.csv",
    )
    _validate_written_aggregate_outputs(
        aggregate_dir, availability_counts, reason_counts
    )
    safety = {
        "audit": "duplicate_clip_adjudication_v2_safety_gate",
        "status": "PASS",
        "n_expected_aggregate_outputs": len(AGGREGATE_FILENAMES),
        "aggregate_output_names_exact": True,
        "evidence_count_schema_exact": True,
        "reason_count_schema_exact": True,
        "adjudication_vocabulary_valid": True,
        "aggregate_content_allowlist_validated": True,
        "forbidden_token_scan_passed": True,
        "identifier_values_emitted": False,
        "locator_values_emitted": False,
        "file_hash_values_emitted": False,
        "restricted_records_written_outside_repository": True,
        "aggregate_safety_gate_passed": True,
    }
    write_json(
        safety,
        aggregate_dir / "duplicate_clip_adjudication_v2_safety_gate.json",
    )
    print(json.dumps(adjudication_summary, sort_keys=True))
    return 0 if len(duplicate_keys) == args.expected_duplicate_keys else 1


if __name__ == "__main__":
    raise SystemExit(run_guarded(main))
