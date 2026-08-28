"""Aggregate-only reconstruction of the JDIM analysis cohort."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from run_tapse_lvot_vti_imaging_baseline import (
    TARGETS,
    extract_target,
    load_splits,
    normalize_text,
    normalize_unit,
)

from . import PROTOCOL_VERSION
from .duplicate_forensics import source_manifest_row_fingerprint
from .safety import (
    BLOCKED_COHORT_CANONICAL_MISMATCH,
    BLOCKED_LINEAGE,
    BLOCKED_UNDECLARED_DUPLICATE_METADATA,
    BLOCKED_UNDECLARED_LEGACY_SCOPE,
    Tier1BlockedError,
    assert_export_safe_frame,
    canonical_id_set_sha256,
    require_columns,
    require_restricted_destination,
    safe_file_record,
    sanitize_for_safe_manifest,
    schema_hash,
    sha256_file,
    write_json,
    write_safe_csv,
)


VALID_SPLITS = ("train", "val", "test")
TARGET_NAMES = ("lvot_vti", "tapse")
OUTSIDE_UNIVERSE_POLICIES = {"canonical_only", "declared_legacy_scope"}
FORENSIC_CLASSES = {
    "LEGITIMATE_DISTINCT_CLIPS",
    "TRUE_DUPLICATE_EXPECTED_ROWS",
    "TRUE_DUPLICATE_MANIFEST_ROWS",
    "TRUE_DUPLICATE_EMBEDDING_ROWS",
    "KEY_GRANULARITY_TOO_COARSE",
    "AMBIGUOUS_REQUIRES_AUTHOR_REVIEW",
}
TRUE_DUPLICATE_CLASSES = {
    "TRUE_DUPLICATE_EXPECTED_ROWS",
    "TRUE_DUPLICATE_MANIFEST_ROWS",
    "TRUE_DUPLICATE_EMBEDDING_ROWS",
}
APPROVED_METADATA_DUPLICATE_GROUPS = 32
APPROVED_METADATA_DUPLICATE_MEMBER_ROWS = 64
APPROVED_METADATA_DUPLICATE_BATCH = "batch_000"
APPROVED_METADATA_DUPLICATE_CLASS = "TRUE_DUPLICATE_EXPECTED_ROWS"
METADATA_PACKET_STAGES = (
    "expected_records",
    "dicom_audit",
    "cine_candidates",
    "extraction_manifest",
    "batch_embedding_manifest",
    "merged_embedding_manifest",
)


@dataclass
class InvariantReport:
    checks: list[dict[str, Any]] = field(default_factory=list)

    def add(
        self,
        name: str,
        passed: bool,
        observed: Any = None,
        expected: Any = None,
        detail: str = "",
    ) -> None:
        self.checks.append(
            {
                "check": name,
                "passed": bool(passed),
                "observed": observed,
                "expected": expected,
                "detail": detail,
            }
        )

    @property
    def passed(self) -> bool:
        return all(check["passed"] for check in self.checks)

    def payload(self) -> dict[str, Any]:
        return {
            "status": "ok" if self.passed else BLOCKED_LINEAGE,
            "n_checks": len(self.checks),
            "n_failed": sum(not check["passed"] for check in self.checks),
            "checks": self.checks,
        }


@dataclass
class CohortFlowInputs:
    source_studies: Path
    eligible_studies: Path
    expected_records: Sequence[Path]
    dicom_audits: Sequence[Path]
    extraction_manifests: Sequence[Path]
    embedding_batches: Mapping[str, Path]
    embedding_batch_npzs: Mapping[str, Path]
    final_study_embeddings: Path
    structured_measurements: Path
    split_map: Path
    canonical_summaries: Mapping[str, Path]
    lineage_metadata: Path
    duplicate_forensics: Path
    duplicate_forensics_provenance: Path
    canonical_predictions: Mapping[str, Path]
    duplicate_metadata_summary: Path | None = None
    duplicate_metadata_groups: Path | None = None
    duplicate_metadata_stage_rows: Path | None = None
    corrected_clip_embeddings: Path | None = None


@dataclass
class CohortFlowResult:
    summary: dict[str, Any]
    stages: pd.DataFrame
    target_splits: pd.DataFrame
    label_multiplicity: pd.DataFrame
    subject_multiplicity: pd.DataFrame
    batch_reconciliation: pd.DataFrame
    invariants: dict[str, Any]
    provenance: dict[str, Any]
    restricted_reconciliation: pd.DataFrame


@dataclass
class EmbeddingBatchResult:
    merged: pd.DataFrame
    canonical_clips: pd.DataFrame
    declared_outside_clips: pd.DataFrame
    stages: list[dict[str, Any]]
    canonical_studies: set[str]
    declared_outside_studies: set[str]
    undeclared_outside_studies: set[str]
    reconciliation: pd.DataFrame


@dataclass(frozen=True)
class MetadataDuplicatePolicy:
    key_to_token: Mapping[tuple[str, str], str]
    key_to_subject: Mapping[tuple[str, str], str]
    expected_batch: str
    n_groups: int
    n_member_rows: int
    n_duplicate_rows: int
    packet_hashes: Mapping[str, str]


def validate_cohort_input_schemas(inputs: CohortFlowInputs) -> dict[str, Any]:
    """Validate headers, lineage metadata, and pinned hashes without row analysis."""

    report = InvariantReport()
    if not inputs.lineage_metadata.exists():
        raise Tier1BlockedError(BLOCKED_LINEAGE, "missing lineage metadata JSON")
    metadata = json.loads(inputs.lineage_metadata.read_text(encoding="utf-8"))
    validate_lineage_metadata(metadata, list(inputs.embedding_batches), inputs.split_map, report)
    _validate_duplicate_forensics_provenance(inputs, report)

    roles: list[tuple[str, Path, Sequence[str], bool]] = [
        ("source study universe", inputs.source_studies, ("study_id", "subject_id"), False),
        ("eligible study universe", inputs.eligible_studies, ("study_id", "subject_id"), False),
        ("final study embedding manifest", inputs.final_study_embeddings, ("study_id", "subject_id"), False),
        (
            "structured measurements",
            inputs.structured_measurements,
            ("study_id", "subject_id", "measurement", "result"),
            False,
        ),
        ("frozen split map", inputs.split_map, ("subject_id", "split"), False),
    ]
    roles.extend(
        (f"expected records {index}", path, ("study_id", "subject_id", "dicom_filepath"), False)
        for index, path in enumerate(inputs.expected_records)
    )
    roles.extend(
        (f"canonical predictions {target}", path, ("study_id", "subject_id", "split"), False)
        for target, path in inputs.canonical_predictions.items()
    )
    roles.append(
        (
            "duplicate forensic decisions",
            inputs.duplicate_forensics,
            (
                "group_token",
                "classification",
                "_batch",
                "_manifest_row",
                "dedup_keep_candidate",
                "source_manifest_row_fingerprint_sha256",
            ),
            False,
        )
    )
    duplicate_packet_roles = (
        (
            "duplicate metadata groups",
            inputs.duplicate_metadata_groups,
            ("group_token", "classification", "first_duplicate_stage"),
            False,
        ),
        (
            "duplicate metadata stage rows",
            inputs.duplicate_metadata_stage_rows,
            ("group_token", "stage", "study_id", "subject_id", "dicom_filepath"),
            False,
        ),
        (
            "corrected clip embedding manifest",
            inputs.corrected_clip_embeddings,
            ("study_id", "subject_id", "dicom_filepath", "forensic_classification"),
            False,
        ),
    )
    if any(path is not None for _, path, _, _ in duplicate_packet_roles):
        if inputs.duplicate_metadata_summary is None or any(
            path is None for _, path, _, _ in duplicate_packet_roles
        ):
            raise Tier1BlockedError(
                BLOCKED_UNDECLARED_DUPLICATE_METADATA,
                "the approved duplicate metadata packet must be supplied in full",
            )
        roles.extend(
            (label, path, required, needs_key)
            for label, path, required, needs_key in duplicate_packet_roles
            if path is not None
        )
    roles.extend(
        (f"DICOM audit {index}", path, ("study_id", "subject_id", "read_ok", "is_multiframe"), True)
        for index, path in enumerate(inputs.dicom_audits)
    )
    roles.extend(
        (f"extraction manifest {index}", path, ("study_id", "subject_id", "write_ok"), True)
        for index, path in enumerate(inputs.extraction_manifests)
    )
    roles.extend(
        (f"embedding batch {name}", path, ("study_id", "subject_id", "embedding_idx"), True)
        for name, path in inputs.embedding_batches.items()
    )
    checked: list[str] = []
    for label, path, required, needs_record_key in roles:
        if not path.exists():
            raise Tier1BlockedError(BLOCKED_LINEAGE, f"missing {label}")
        header = pd.read_csv(path, nrows=0)
        require_columns(header, required, label)
        if needs_record_key and not set(header.columns).intersection(
            {"dicom_filepath", "sop_instance_uid", "clip_path", "npz_path", "embedding_idx"}
        ):
            raise Tier1BlockedError(BLOCKED_LINEAGE, f"{label} lacks a deduplication record key")
        checked.append(label)
    for target in TARGET_NAMES:
        _load_canonical_summary(inputs.canonical_summaries[target], target)
        checked.append(f"canonical summary {target}")
    return {
        "status": "ok",
        "mode": "schema_only",
        "protocol_version": PROTOCOL_VERSION,
        "mimic_iv_echo_release": metadata["mimic_iv_echo_release"],
        "n_logical_inputs_checked": len(checked),
        "embedding_batches_declared": len(inputs.embedding_batches),
        "canonical_targets_checked": list(TARGET_NAMES),
        "row_level_analysis_executed": False,
    }


def _read_csv(path: Path, label: str) -> pd.DataFrame:
    if not path.exists():
        raise Tier1BlockedError(BLOCKED_LINEAGE, f"missing {label}")
    try:
        return pd.read_csv(path)
    except pd.errors.EmptyDataError as exc:
        raise Tier1BlockedError(BLOCKED_LINEAGE, f"empty {label}") from exc


def _read_many(paths: Sequence[Path], label: str) -> pd.DataFrame:
    if not paths:
        raise Tier1BlockedError(BLOCKED_LINEAGE, f"no {label} inputs supplied")
    frames: list[pd.DataFrame] = []
    for index, path in enumerate(paths):
        frame = _read_csv(path, f"{label}[{index}]").copy()
        frame["_source_index"] = index
        path_text = str(path)
        match = re.search(r"(?:^|[/_])(batch_\d{3})(?:[/_]|$)", path_text)
        batch_name = (
            match.group(1)
            if match
            else "legacy_stage_d_500"
            if "stage_d_500" in path_text or "legacy" in path_text.lower()
            else f"input_{index:03d}"
        )
        frame["_source_batch"] = batch_name
        frames.append(frame)
    return pd.concat(frames, ignore_index=True, sort=False)


def _validate_duplicate_forensics_provenance(
    inputs: CohortFlowInputs,
    report: InvariantReport,
) -> dict[str, Any]:
    if set(inputs.embedding_batch_npzs) != set(inputs.embedding_batches):
        raise Tier1BlockedError(
            BLOCKED_LINEAGE,
            "embedding manifest and NPZ batch names differ for forensic provenance",
        )
    required_paths = {
        "duplicate-forensics decisions": inputs.duplicate_forensics,
        "duplicate-forensics provenance": inputs.duplicate_forensics_provenance,
        "frozen split map": inputs.split_map,
        **{
            f"embedding manifest {name}": path
            for name, path in inputs.embedding_batches.items()
        },
        **{
            f"embedding NPZ {name}": path
            for name, path in inputs.embedding_batch_npzs.items()
        },
    }
    missing_paths = sorted(label for label, path in required_paths.items() if not path.is_file())
    if missing_paths:
        raise Tier1BlockedError(
            BLOCKED_LINEAGE,
            f"missing forensic provenance inputs: {missing_paths}",
        )
    if not inputs.duplicate_forensics_provenance.exists():
        raise Tier1BlockedError(BLOCKED_LINEAGE, "missing duplicate-forensics provenance JSON")
    try:
        payload = json.loads(
            inputs.duplicate_forensics_provenance.read_text(encoding="utf-8")
        )
    except (json.JSONDecodeError, OSError) as exc:
        raise Tier1BlockedError(
            BLOCKED_LINEAGE,
            f"invalid duplicate-forensics provenance JSON: {exc}",
        ) from exc
    if payload.get("status") != "ok":
        raise Tier1BlockedError(
            BLOCKED_LINEAGE,
            "duplicate-forensics provenance does not record a resolved status",
        )

    artifact_hashes = payload.get("restricted_artifact_sha256")
    expected_rows_hash = (
        artifact_hashes.get("duplicate_forensics_rows.csv")
        if isinstance(artifact_hashes, Mapping)
        else None
    )
    observed_rows_hash = sha256_file(inputs.duplicate_forensics)
    rows_match = bool(expected_rows_hash and expected_rows_hash == observed_rows_hash)
    report.add(
        "duplicate_forensic_decisions_match_provenance_hash",
        rows_match,
        observed_rows_hash,
        expected_rows_hash,
    )
    if not rows_match:
        raise Tier1BlockedError(
            BLOCKED_LINEAGE,
            "duplicate-forensics decision rows do not match their provenance hash",
        )
    try:
        decision_tokens = pd.read_csv(
            inputs.duplicate_forensics,
            usecols=["group_token"],
        )
    except (ValueError, pd.errors.EmptyDataError) as exc:
        raise Tier1BlockedError(
            BLOCKED_LINEAGE,
            "duplicate-forensics decision rows lack a valid group-token column",
        ) from exc
    provenance = payload.get("input_provenance")
    if not isinstance(provenance, Mapping):
        raise Tier1BlockedError(BLOCKED_LINEAGE, "duplicate-forensics input provenance is missing")
    split_record = provenance.get("split_map")
    expected_split_hash = (
        split_record.get("sha256") if isinstance(split_record, Mapping) else None
    )
    observed_split_hash = sha256_file(inputs.split_map)
    split_matches = bool(expected_split_hash and expected_split_hash == observed_split_hash)
    report.add(
        "duplicate_forensics_match_frozen_split_map",
        split_matches,
        observed_split_hash,
        expected_split_hash,
    )
    if not split_matches:
        raise Tier1BlockedError(
            BLOCKED_LINEAGE,
            "duplicate-forensics provenance does not match the frozen split map",
        )

    raw_batches = provenance.get("batches")
    if not isinstance(raw_batches, list):
        raise Tier1BlockedError(BLOCKED_LINEAGE, "duplicate-forensics batch provenance is missing")
    batch_records: dict[str, Mapping[str, Any]] = {}
    for raw in raw_batches:
        if not isinstance(raw, Mapping) or not str(raw.get("batch_name", "")).strip():
            raise Tier1BlockedError(BLOCKED_LINEAGE, "invalid duplicate-forensics batch provenance")
        name = str(raw["batch_name"])
        if name in batch_records:
            raise Tier1BlockedError(BLOCKED_LINEAGE, "duplicate batch in forensic provenance")
        batch_records[name] = raw
    if set(batch_records) != set(inputs.embedding_batches):
        raise Tier1BlockedError(
            BLOCKED_LINEAGE,
            "duplicate-forensics provenance batch names differ from cohort inputs",
        )

    extraction_hashes = {sha256_file(path) for path in inputs.extraction_manifests}
    provenance_extraction_hashes = {
        str(record.get("extraction_manifest_sha256", ""))
        for record in batch_records.values()
        if str(record.get("extraction_manifest_sha256", ""))
    }
    extraction_set_matches = provenance_extraction_hashes == extraction_hashes
    report.add(
        "duplicate_forensics_match_exact_extraction_manifest_set",
        extraction_set_matches,
        sorted(extraction_hashes),
        sorted(provenance_extraction_hashes),
    )
    if not extraction_set_matches:
        raise Tier1BlockedError(
            BLOCKED_LINEAGE,
            "duplicate-forensics provenance is stale for the extraction-manifest set",
        )
    mismatched_batches: list[str] = []
    for name, record in sorted(batch_records.items()):
        manifest_hash = sha256_file(inputs.embedding_batches[name])
        embedding_hash = sha256_file(inputs.embedding_batch_npzs[name])
        if (
            record.get("embedding_manifest_sha256") != manifest_hash
            or record.get("embedding_npz_sha256") != embedding_hash
            or record.get("extraction_manifest_sha256") not in extraction_hashes
        ):
            mismatched_batches.append(name)
    report.add(
        "duplicate_forensics_match_batch_manifests_embeddings_and_extractions",
        not mismatched_batches,
        mismatched_batches,
        [],
    )
    if mismatched_batches:
        raise Tier1BlockedError(
            BLOCKED_LINEAGE,
            "duplicate-forensics provenance is stale for one or more batch inputs",
        )
    return payload


def _normalize_ids(frame: pd.DataFrame, label: str, require_subject: bool = True) -> pd.DataFrame:
    required = ["study_id", "subject_id"] if require_subject else ["study_id"]
    require_columns(frame, required, label)
    out = frame.copy()
    if out["study_id"].isna().any():
        raise Tier1BlockedError(BLOCKED_LINEAGE, f"{label} contains missing study_id values")
    out["_study"] = out["study_id"].astype(str)
    if "subject_id" in out.columns:
        out["_subject"] = out["subject_id"].astype(str)
        if out["subject_id"].isna().any():
            raise Tier1BlockedError(BLOCKED_LINEAGE, f"{label} contains missing subject_id values")
    return out


def _bool_series(frame: pd.DataFrame, column: str, default: bool = False) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(default, index=frame.index, dtype=bool)
    values = frame[column]
    if pd.api.types.is_bool_dtype(values):
        return values.fillna(default).astype(bool)
    normalized = values.astype(str).str.strip().str.lower()
    return normalized.isin({"true", "1", "yes", "y"})


def _record_key(frame: pd.DataFrame, label: str) -> pd.Series:
    for column in ("dicom_filepath", "sop_instance_uid", "clip_path", "npz_path"):
        if column in frame.columns:
            values = frame[column].astype(str)
            return frame["_study"] + "::" + values
    if "embedding_idx" in frame.columns:
        return frame["_study"] + "::embedding_idx::" + frame["embedding_idx"].astype(str)
    raise Tier1BlockedError(
        BLOCKED_LINEAGE,
        f"{label} lacks a DICOM/clip record key needed for deduplication",
    )


def _metadata_key(frame: pd.DataFrame, label: str) -> pd.Series:
    require_columns(frame, ["study_id", "dicom_filepath"], label)
    studies = frame["study_id"].astype(str).str.strip()
    paths = frame["dicom_filepath"].astype(str).str.strip()
    if studies.eq("").any() or paths.eq("").any():
        raise Tier1BlockedError(
            BLOCKED_UNDECLARED_DUPLICATE_METADATA,
            f"{label} contains an empty approved duplicate key component",
        )
    return pd.Series(list(zip(studies, paths)), index=frame.index, dtype=object)


def _block_duplicate_metadata(detail: str) -> None:
    raise Tier1BlockedError(BLOCKED_UNDECLARED_DUPLICATE_METADATA, detail)


def _validate_metadata_duplicate_policy(
    inputs: CohortFlowInputs,
    forensic_payload: Mapping[str, Any],
    forensic_rows: pd.DataFrame,
    report: InvariantReport,
) -> MetadataDuplicatePolicy | None:
    packet_paths = (
        inputs.duplicate_metadata_summary,
        inputs.duplicate_metadata_groups,
        inputs.duplicate_metadata_stage_rows,
        inputs.corrected_clip_embeddings,
    )
    if all(path is None for path in packet_paths):
        return None
    if any(path is None for path in packet_paths):
        _block_duplicate_metadata("the approved duplicate-metadata packet is incomplete")
    summary_path, groups_path, stage_rows_path, corrected_path = packet_paths
    assert summary_path is not None
    assert groups_path is not None
    assert stage_rows_path is not None
    assert corrected_path is not None
    if not all(path.is_file() for path in (summary_path, groups_path, stage_rows_path, corrected_path)):
        _block_duplicate_metadata("one or more approved duplicate-metadata artifacts are missing")

    resolution = forensic_payload.get("metadata_resolution")
    if not isinstance(resolution, Mapping):
        _block_duplicate_metadata("duplicate-forensics provenance lacks metadata_resolution")
    observed_hashes = {
        "metadata_summary_sha256": sha256_file(summary_path),
        "metadata_group_classification_sha256": sha256_file(groups_path),
        "metadata_stage_rows_sha256": sha256_file(stage_rows_path),
        "corrected_clip_manifest_sha256": sha256_file(corrected_path),
    }
    for field in ("metadata_summary_sha256", "metadata_group_classification_sha256"):
        if observed_hashes[field] != str(resolution.get(field, "")):
            _block_duplicate_metadata(f"{field} does not match the approved decision packet")

    try:
        metadata_summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        _block_duplicate_metadata(f"invalid duplicate-metadata summary: {exc}")
    expected_stage_hash = (
        metadata_summary.get("restricted_evidence_packet_sha256", {}).get(
            "metadata_stage_rows.csv"
        )
        if isinstance(metadata_summary.get("restricted_evidence_packet_sha256"), Mapping)
        else None
    )
    if expected_stage_hash != observed_hashes["metadata_stage_rows_sha256"]:
        _block_duplicate_metadata("metadata stage rows do not match the approved metadata packet")
    if metadata_summary.get("status") != "DUPLICATE_SEMANTICS_RESOLVED_FROM_PROVENANCE":
        _block_duplicate_metadata("duplicate-metadata summary is not provenance-resolved")

    groups = _read_csv(groups_path, "duplicate metadata group classifications")
    require_columns(
        groups,
        [
            "group_token",
            "classification",
            "first_duplicate_stage",
            "semantic_identity_equal_all_stages",
            "n_expected_records_rows",
            "n_dicom_audit_rows",
            "n_cine_candidates_rows",
            "n_extraction_manifest_rows",
            "n_batch_embedding_manifest_rows",
            "n_merged_embedding_manifest_rows",
        ],
        "duplicate metadata group classifications",
    )
    groups["group_token"] = groups["group_token"].astype(str).str.strip()
    group_tokens = set(groups["group_token"])
    count_columns = [column for column in groups if column.startswith("n_") and column.endswith("_rows")]
    exact_group_packet = (
        len(groups) == APPROVED_METADATA_DUPLICATE_GROUPS
        and groups["group_token"].nunique() == APPROVED_METADATA_DUPLICATE_GROUPS
        and groups["group_token"].ne("").all()
        and groups["classification"].astype(str).eq(APPROVED_METADATA_DUPLICATE_CLASS).all()
        and groups["first_duplicate_stage"].astype(str).eq("expected_records").all()
        and _bool_series(groups, "semantic_identity_equal_all_stages").all()
        and all(pd.to_numeric(groups[column], errors="coerce").eq(2).all() for column in count_columns)
    )
    if not exact_group_packet:
        _block_duplicate_metadata("the group-classification packet is not the exact approved 32-pair set")

    stage_rows = _read_csv(stage_rows_path, "duplicate metadata stage rows")
    require_columns(
        stage_rows,
        ["group_token", "stage", "stage_row_ordinal", "study_id", "subject_id", "dicom_filepath"],
        "duplicate metadata stage rows",
    )
    stage_rows["group_token"] = stage_rows["group_token"].astype(str).str.strip()
    stage_rows["stage"] = stage_rows["stage"].astype(str).str.strip()
    exact_stage_packet = (
        len(stage_rows) == APPROVED_METADATA_DUPLICATE_MEMBER_ROWS * len(METADATA_PACKET_STAGES)
        and set(stage_rows["group_token"]) == group_tokens
        and set(stage_rows["stage"]) == set(METADATA_PACKET_STAGES)
        and stage_rows.groupby(["stage", "group_token"]).size().eq(2).all()
        and stage_rows.groupby("stage").size().eq(APPROVED_METADATA_DUPLICATE_MEMBER_ROWS).all()
    )
    if not exact_stage_packet:
        _block_duplicate_metadata("metadata stage rows do not contain exactly two rows per approved group and stage")

    decisions = _normalize_ids(forensic_rows, "duplicate forensic decisions")
    require_columns(
        decisions,
        [
            "group_token",
            "classification",
            "_batch",
            "dicom_filepath",
            "dedup_keep_candidate",
        ],
        "duplicate forensic decisions",
    )
    decisions["group_token"] = decisions["group_token"].astype(str).str.strip()
    decision_sizes = decisions.groupby("group_token").size()
    keep_counts = decisions.assign(
        _keeper=_bool_series(decisions, "dedup_keep_candidate")
    ).groupby("group_token")["_keeper"].sum()
    exact_decisions = (
        len(decisions) == APPROVED_METADATA_DUPLICATE_MEMBER_ROWS
        and set(decisions["group_token"]) == group_tokens
        and decision_sizes.eq(2).all()
        and decisions["_batch"].astype(str).eq(APPROVED_METADATA_DUPLICATE_BATCH).all()
        and decisions["classification"].astype(str).eq(APPROVED_METADATA_DUPLICATE_CLASS).all()
        and decisions["_study"].nunique() == 1
        and decisions["_subject"].nunique() == 1
        and keep_counts.eq(1).all()
    )
    if not exact_decisions:
        _block_duplicate_metadata("forensic decision rows are not the approved 64 batch_000 rows")
    if "semantic_clip_identity_sha256" in decisions.columns and not decisions.groupby(
        "group_token"
    )["semantic_clip_identity_sha256"].nunique().eq(1).all():
        _block_duplicate_metadata("an approved group has conflicting semantic clip identity")

    decisions["_metadata_key"] = _metadata_key(decisions, "duplicate forensic decisions")
    if decisions.groupby("group_token")["_metadata_key"].nunique().ne(1).any():
        _block_duplicate_metadata("an approved group maps to more than one metadata key")
    if decisions.groupby("group_token")["_subject"].nunique().ne(1).any():
        _block_duplicate_metadata("an approved group maps to more than one subject")
    group_keys = decisions.groupby("group_token")["_metadata_key"].first().to_dict()
    group_subjects = decisions.groupby("group_token")["_subject"].first().to_dict()
    key_to_token = {key: token for token, key in group_keys.items()}
    key_to_subject = {group_keys[token]: subject for token, subject in group_subjects.items()}
    if len(key_to_token) != APPROVED_METADATA_DUPLICATE_GROUPS:
        _block_duplicate_metadata("approved group tokens do not map one-to-one to metadata keys")

    stage_rows["_metadata_key"] = _metadata_key(stage_rows, "duplicate metadata stage rows")
    for stage, frame in stage_rows.groupby("stage", sort=True):
        observed = frame.groupby("group_token")["_metadata_key"].agg(lambda values: set(values))
        if any(values != {group_keys[token]} for token, values in observed.items()):
            _block_duplicate_metadata(f"{stage} packet rows do not match approved metadata keys")

    corrected = _normalize_ids(
        _read_csv(corrected_path, "corrected clip embedding manifest"),
        "corrected clip embedding manifest",
    )
    require_columns(
        corrected,
        ["dicom_filepath", "forensic_classification", "dedup_group_size"],
        "corrected clip embedding manifest",
    )
    if "write_ok" in corrected.columns:
        corrected = corrected.loc[_bool_series(corrected, "write_ok")].copy()
    corrected["_metadata_key"] = _metadata_key(corrected, "corrected clip embedding manifest")
    approved_corrected = corrected.loc[corrected["_metadata_key"].isin(key_to_token)]
    marked_duplicate = corrected.loc[
        corrected["forensic_classification"].astype(str).eq(APPROVED_METADATA_DUPLICATE_CLASS)
        | pd.to_numeric(corrected["dedup_group_size"], errors="coerce").fillna(1).gt(1)
    ]
    exact_retention = (
        len(approved_corrected) == APPROVED_METADATA_DUPLICATE_GROUPS
        and approved_corrected.groupby("_metadata_key").size().eq(1).all()
        and approved_corrected["forensic_classification"].astype(str).eq(
            APPROVED_METADATA_DUPLICATE_CLASS
        ).all()
        and pd.to_numeric(approved_corrected["dedup_group_size"], errors="coerce").eq(2).all()
        and set(marked_duplicate["_metadata_key"]) == set(key_to_token)
    )
    if not exact_retention:
        _block_duplicate_metadata("corrected clip manifest does not retain exactly one row per approved group")

    report.add(
        "approved_duplicate_metadata_packet_exactly_32_groups",
        True,
        APPROVED_METADATA_DUPLICATE_GROUPS,
        APPROVED_METADATA_DUPLICATE_GROUPS,
    )
    report.add(
        "approved_duplicate_metadata_packet_exactly_64_member_rows",
        True,
        APPROVED_METADATA_DUPLICATE_MEMBER_ROWS,
        APPROVED_METADATA_DUPLICATE_MEMBER_ROWS,
    )
    report.add(
        "corrected_clip_manifest_retains_one_semantic_clip_per_approved_group",
        True,
        len(approved_corrected),
        APPROVED_METADATA_DUPLICATE_GROUPS,
    )
    return MetadataDuplicatePolicy(
        key_to_token=key_to_token,
        key_to_subject=key_to_subject,
        expected_batch=APPROVED_METADATA_DUPLICATE_BATCH,
        n_groups=APPROVED_METADATA_DUPLICATE_GROUPS,
        n_member_rows=APPROVED_METADATA_DUPLICATE_MEMBER_ROWS,
        n_duplicate_rows=APPROVED_METADATA_DUPLICATE_GROUPS,
        packet_hashes=observed_hashes,
    )


def _study_subject_pairs(frame: pd.DataFrame, label: str) -> pd.DataFrame:
    normalized = _normalize_ids(frame, label, require_subject=True)
    return normalized[["_study", "_subject"]].drop_duplicates()


def _assert_subject_consistency(
    named_frames: Mapping[str, pd.DataFrame], report: InvariantReport
) -> dict[str, str]:
    pairs: list[pd.DataFrame] = []
    for name, frame in named_frames.items():
        if frame.empty or not {"study_id", "subject_id"}.issubset(frame.columns):
            continue
        piece = _study_subject_pairs(frame, name).copy()
        piece["_source"] = name
        pairs.append(piece)
    if not pairs:
        raise Tier1BlockedError(BLOCKED_LINEAGE, "no study-to-subject mappings were available")
    combined = pd.concat(pairs, ignore_index=True)
    counts = combined.groupby("_study")["_subject"].nunique()
    conflicts = int((counts > 1).sum())
    report.add("consistent_subject_assignment_across_pipeline", conflicts == 0, conflicts, 0)
    if conflicts:
        raise Tier1BlockedError(
            BLOCKED_LINEAGE,
            f"{conflicts} studies have conflicting subject assignments across pipeline sources",
        )
    return combined.drop_duplicates("_study").set_index("_study")["_subject"].to_dict()


def validate_lineage_metadata(
    metadata: Mapping[str, Any],
    batch_names: Sequence[str],
    split_map: Path,
    report: InvariantReport,
) -> None:
    if not split_map.exists():
        raise Tier1BlockedError(BLOCKED_LINEAGE, "frozen split map is missing")
    required_text = (
        "mimic_iv_echo_release",
        "source_denominator_definition",
        "imaging_lineage",
        "label_lineage",
    )
    missing = [key for key in required_text if not str(metadata.get(key, "")).strip()]
    if metadata.get("protocol_version") != PROTOCOL_VERSION:
        missing.append(f"protocol_version={PROTOCOL_VERSION}")
    split_meta = metadata.get("split_map")
    if not isinstance(split_meta, Mapping):
        missing.append("split_map")
    else:
        for key in ("version", "generator", "expected_sha256"):
            if not str(split_meta.get(key, "")).strip():
                missing.append(f"split_map.{key}")
    batch_meta = metadata.get("batch_sources")
    if not isinstance(batch_meta, Mapping):
        missing.append("batch_sources")
        batch_meta = {}
    missing_batches = sorted(set(batch_names) - set(batch_meta))
    unexpected_batches = sorted(set(batch_meta) - set(batch_names))
    if missing_batches:
        missing.append(f"batch_sources entries: {missing_batches}")
    if unexpected_batches:
        missing.append(f"batch_sources not supplied as inputs: {unexpected_batches}")
    invalid_batch_classes = sorted(
        name
        for name in set(batch_names) & set(batch_meta)
        if not isinstance(batch_meta[name], Mapping)
        or batch_meta[name].get("source_class") not in {"legacy", "fullscale"}
    )
    if invalid_batch_classes:
        missing.append(f"invalid batch source_class entries: {invalid_batch_classes}")
    invalid_outside_policies = sorted(
        name
        for name in set(batch_names) & set(batch_meta)
        if not isinstance(batch_meta[name], Mapping)
        or batch_meta[name].get("outside_universe_policy", "canonical_only")
        not in OUTSIDE_UNIVERSE_POLICIES
    )
    if invalid_outside_policies:
        missing.append(f"invalid outside_universe_policy entries: {invalid_outside_policies}")
    nonlegacy_outside_declarations = sorted(
        name
        for name in set(batch_names) & set(batch_meta)
        if isinstance(batch_meta[name], Mapping)
        and batch_meta[name].get("outside_universe_policy", "canonical_only")
        == "declared_legacy_scope"
        and batch_meta[name].get("source_class") != "legacy"
    )
    if nonlegacy_outside_declarations:
        missing.append(
            "outside-universe scope declared for nonlegacy batches: "
            f"{nonlegacy_outside_declarations}"
        )
    declared_legacy_batches = sorted(
        name
        for name in set(batch_names) & set(batch_meta)
        if isinstance(batch_meta[name], Mapping)
        and batch_meta[name].get("outside_universe_policy", "canonical_only")
        == "declared_legacy_scope"
    )
    selected_scope = metadata.get("selected_analysis_universe")
    if declared_legacy_batches:
        if not isinstance(selected_scope, Mapping):
            missing.append("selected_analysis_universe")
        else:
            for key in (
                "study_count",
                "subject_count",
                "study_set_sha256",
                "source_sha256",
                "deterministic_selection_rule",
            ):
                if selected_scope.get(key) in (None, ""):
                    missing.append(f"selected_analysis_universe.{key}")
        scope_fields = (
            "version",
            "legacy_studies_total",
            "legacy_study_set_sha256",
            "inside_selected_universe_count",
            "inside_selected_universe_study_set_sha256",
            "outside_selected_universe_count",
            "outside_selected_universe_study_set_sha256",
            "later_fullscale_overlap_count",
            "later_fullscale_overlap_study_set_sha256",
            "deduplicated_canonical_contribution_count",
            "deduplicated_canonical_contribution_study_set_sha256",
        )
        for name in declared_legacy_batches:
            scope = batch_meta[name].get("declared_legacy_scope")
            if not isinstance(scope, Mapping):
                missing.append(f"batch_sources.{name}.declared_legacy_scope")
                continue
            for key in scope_fields:
                if scope.get(key) in (None, ""):
                    missing.append(f"batch_sources.{name}.declared_legacy_scope.{key}")
    unexpected_scope = sorted(
        name
        for name in set(batch_names) & set(batch_meta)
        if isinstance(batch_meta[name], Mapping)
        and "declared_legacy_scope" in batch_meta[name]
        and name not in declared_legacy_batches
    )
    if unexpected_scope:
        missing.append(f"unexpected declared_legacy_scope entries: {unexpected_scope}")
    invalid_overlap_pairs: list[str] = []
    for raw_pair in metadata.get("allowed_batch_overlap_pairs", []):
        pair = str(raw_pair).split("|")
        if len(pair) != 2 or pair[0] == pair[1] or not set(pair).issubset(set(batch_names)):
            invalid_overlap_pairs.append(str(raw_pair))
    if invalid_overlap_pairs:
        missing.append(f"invalid allowed_batch_overlap_pairs: {sorted(invalid_overlap_pairs)}")
    flow_structure = metadata.get("flow_structure")
    report.add(
        "parallel_flow_structure_declared",
        flow_structure == "parallel_branches",
        flow_structure,
        "parallel_branches",
        "A forced linear funnel is invalid for partially different lineage artifacts.",
    )
    if flow_structure != "parallel_branches":
        raise Tier1BlockedError(
            BLOCKED_LINEAGE,
            "invalid lineage metadata: cohort construction must declare parallel_branches",
        )
    report.add("release_and_lineage_metadata_present", not missing, len(missing), 0)
    if missing:
        raise Tier1BlockedError(BLOCKED_LINEAGE, f"missing lineage metadata: {missing}")
    actual_split_hash = sha256_file(split_map)
    expected_split_hash = str(split_meta["expected_sha256"]).lower()
    report.add(
        "split_map_sha256_matches_pinned_provenance",
        actual_split_hash == expected_split_hash,
        actual_split_hash,
        expected_split_hash,
    )
    if actual_split_hash != expected_split_hash:
        raise Tier1BlockedError(BLOCKED_LINEAGE, "split-map SHA-256 does not match pinned lineage metadata")


def _load_canonical_summary(path: Path, target: str) -> dict[str, Any]:
    if not path.exists():
        raise Tier1BlockedError(BLOCKED_LINEAGE, f"missing canonical summary for {target}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload.get("target_summaries"), list):
        matches = [item for item in payload["target_summaries"] if item.get("target") == target]
        if len(matches) != 1:
            raise Tier1BlockedError(BLOCKED_LINEAGE, f"canonical summary does not uniquely identify {target}")
        return matches[0]
    if payload.get("target") == target:
        return payload
    raise Tier1BlockedError(BLOCKED_LINEAGE, f"canonical summary target mismatch for {target}")


def _stage(
    branch: str,
    stage: str,
    frame: pd.DataFrame,
    row_mask: pd.Series | None = None,
    target: str = "",
    notes: str = "",
) -> dict[str, Any]:
    subset = frame if row_mask is None else frame.loc[row_mask]
    return {
        "branch": branch,
        "target": target,
        "stage": stage,
        "n_rows": int(len(subset)),
        "n_studies": int(subset["_study"].nunique()) if "_study" in subset.columns else None,
        "n_subjects": int(subset["_subject"].nunique()) if "_subject" in subset.columns else None,
        "notes": notes,
    }


def _prepare_dicom_branch(
    expected: pd.DataFrame,
    audits: pd.DataFrame,
    extraction: pd.DataFrame,
    report: InvariantReport,
    duplicate_policy: MetadataDuplicatePolicy | None = None,
) -> tuple[
    list[dict[str, Any]],
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
    dict[str, dict[str, int]],
]:
    expected = _normalize_ids(expected, "expected DICOM records")
    audits = _normalize_ids(audits, "DICOM audits")
    extraction = _normalize_ids(extraction, "extraction manifests")
    for frame, label in (
        (expected, "expected records"),
        (audits, "DICOM audits"),
        (extraction, "extraction manifests"),
    ):
        frame["_record"] = _record_key(frame, label)

    def adjudicate(
        frame: pd.DataFrame,
        packet_stage: str,
        invariant_name: str,
    ) -> tuple[pd.DataFrame, dict[str, int]]:
        repeated_mask = frame.duplicated("_record", keep=False)
        repeated = frame.loc[repeated_mask].copy()
        duplicate_rows = int(frame.duplicated("_record").sum())
        if duplicate_policy is None:
            if duplicate_rows:
                _block_duplicate_metadata(
                    f"{packet_stage} contains duplicate metadata without an approved packet"
                )
        else:
            repeated["_metadata_key"] = _metadata_key(
                repeated,
                f"{packet_stage} duplicate rows",
            )
            observed_keys = set(repeated["_metadata_key"])
            group_sizes = repeated.groupby("_metadata_key").size()
            expected_keys = set(duplicate_policy.key_to_token)
            exact = (
                len(repeated) == duplicate_policy.n_member_rows
                and duplicate_rows == duplicate_policy.n_duplicate_rows
                and observed_keys == expected_keys
                and group_sizes.eq(2).all()
                and set(repeated["_source_batch"].astype(str))
                == {duplicate_policy.expected_batch}
                and all(
                    set(group["_subject"].astype(str))
                    == {duplicate_policy.key_to_subject[key]}
                    for key, group in repeated.groupby("_metadata_key", sort=False)
                )
            )
            if not exact:
                _block_duplicate_metadata(
                    f"{packet_stage} does not exactly match the approved 32 batch_000 groups"
                )
            report.add(
                f"{packet_stage}_approved_duplicate_groups_exact",
                True,
                len(observed_keys),
                duplicate_policy.n_groups,
            )
        adjudicated = frame.drop_duplicates("_record", keep="first").copy()
        post_duplicates = int(adjudicated.duplicated("_record").sum())
        report.add(invariant_name, post_duplicates == 0, post_duplicates, 0)
        return adjudicated, {
            "raw_manifest_rows": int(len(frame)),
            "provenance_resolved_duplicate_member_rows": int(len(repeated)),
            "provenance_resolved_duplicate_rows": duplicate_rows,
            "adjudicated_unique_clip_rows": int(len(adjudicated)),
        }

    expected, expected_counts = adjudicate(
        expected,
        "expected_records",
        "unique_expected_dicom_keys_after_adjudication",
    )
    audits, audit_counts = adjudicate(
        audits,
        "dicom_audit",
        "unique_dicom_audit_keys_after_adjudication",
    )
    extraction, extraction_counts = adjudicate(
        extraction,
        "extraction_manifest",
        "unique_extraction_keys_after_adjudication",
    )

    missing_audit = len(set(expected["_record"]) - set(audits["_record"]))
    unexpected_audit = len(set(audits["_record"]) - set(expected["_record"]))
    report.add("all_expected_dicoms_audited", missing_audit == 0, missing_audit, 0)
    report.add("no_unexpected_dicoms_in_audit", unexpected_audit == 0, unexpected_audit, 0)

    read_ok = _bool_series(audits, "read_ok")
    multiframe = _bool_series(audits, "is_multiframe")
    extracted_ok = _bool_series(extraction, "write_ok")
    candidate_keys = set(audits.loc[read_ok & multiframe, "_record"])
    extraction_keys = set(extraction["_record"])
    report.add(
        "multiframe_candidates_match_extraction_input",
        candidate_keys == extraction_keys,
        len(candidate_keys.symmetric_difference(extraction_keys)),
        0,
    )

    stages = [
        {
            "branch": "imaging",
            "target": "",
            "stage": f"{stage_name}_{count_name}",
            "n_rows": int(value),
            "n_studies": None,
            "n_subjects": None,
            "notes": "Raw and provenance-adjudicated metadata counts are reported separately.",
        }
        for stage_name, counts in (
            ("expected_records", expected_counts),
            ("dicom_audit", audit_counts),
            ("extraction_manifest", extraction_counts),
        )
        for count_name, value in counts.items()
    ] + [
        _stage("imaging", "expected_dicom_records", expected),
        _stage("imaging", "readable_dicom_records", audits, read_ok),
        _stage("imaging", "unreadable_dicom_records", audits, ~read_ok),
        _stage("imaging", "single_frame_readable_records", audits, read_ok & ~multiframe),
        _stage("imaging", "multiframe_readable_records", audits, read_ok & multiframe),
        _stage("imaging", "successfully_extracted_clips", extraction, extracted_ok),
        _stage("imaging", "failed_clip_extractions", extraction, ~extracted_ok),
    ]
    return stages, expected, audits, extraction, {
        "expected_records": expected_counts,
        "dicom_audit": audit_counts,
        "extraction_manifest": extraction_counts,
    }


def _prepare_embedding_batches(
    batches: Mapping[str, pd.DataFrame],
    eligible_studies: set[str],
    metadata: Mapping[str, Any],
    forensic_rows: pd.DataFrame,
    report: InvariantReport,
) -> EmbeddingBatchResult:
    normalized: list[pd.DataFrame] = []
    batch_rows: list[dict[str, Any]] = []
    batch_study_sets: dict[str, set[str]] = {}
    batch_meta = metadata["batch_sources"]

    for name, raw in batches.items():
        frame = _normalize_ids(raw, f"embedding batch {name}")
        success = _bool_series(frame, "write_ok", default=True)
        frame = frame.loc[success].reset_index(drop=True)
        frame = frame.reset_index(drop=False).rename(columns={"index": "_manifest_row"})
        frame["_source_manifest_row_fingerprint_sha256"] = frame.apply(
            lambda row: source_manifest_row_fingerprint(
                row.to_dict(),
                name,
                int(row["_manifest_row"]),
            ),
            axis=1,
        )
        frame["_record"] = _record_key(frame, f"embedding batch {name}")
        duplicate_clip_keys = int(frame.duplicated("_record").sum())
        frame["_batch"] = name
        frame["_in_canonical_universe"] = frame["_study"].isin(eligible_studies)
        outside_policy = str(batch_meta[name].get("outside_universe_policy", "canonical_only"))
        frame["_outside_scope_class"] = "canonical_universe"
        outside_mask = ~frame["_in_canonical_universe"]
        if outside_policy == "declared_legacy_scope":
            frame.loc[outside_mask, "_outside_scope_class"] = "declared_legacy_scope"
        else:
            frame.loc[outside_mask, "_outside_scope_class"] = "undeclared_outside_scope"
        normalized.append(frame)
        studies = set(frame["_study"])
        batch_study_sets[name] = studies
        study_counts = frame.groupby("_study").size()
        batch_rows.append(
            {
                "batch_name": name,
                "source_class": str(batch_meta[name].get("source_class", "unspecified")),
                "outside_universe_policy": outside_policy,
                "n_successful_clip_rows": int(len(frame)),
                "n_unique_studies": int(len(studies)),
                "n_unique_subjects": int(frame["_subject"].nunique()),
                "n_studies_with_multiple_clips": int((study_counts > 1).sum()),
                "n_repeated_coarse_clip_rows": duplicate_clip_keys,
                "n_studies_in_canonical_universe": int(frame.loc[frame["_in_canonical_universe"], "_study"].nunique()),
                "n_studies_outside_canonical_universe": int(
                    frame.loc[~frame["_in_canonical_universe"], "_study"].nunique()
                ),
                "n_declared_legacy_studies_outside_canonical_universe": int(
                    frame.loc[
                        frame["_outside_scope_class"] == "declared_legacy_scope", "_study"
                    ].nunique()
                ),
                "n_undeclared_studies_outside_canonical_universe": int(
                    frame.loc[
                        frame["_outside_scope_class"] == "undeclared_outside_scope", "_study"
                    ].nunique()
                ),
            }
        )

    fullscale_union = set().union(
        *(
            batch_study_sets[name]
            for name in batch_study_sets
            if batch_meta[name].get("source_class") == "fullscale"
        )
    )
    for row in batch_rows:
        name = str(row["batch_name"])
        if row["outside_universe_policy"] != "declared_legacy_scope":
            continue
        studies = batch_study_sets[name]
        inside = studies & eligible_studies
        outside = studies - eligible_studies
        later_overlap = studies & fullscale_union
        canonical_contribution = inside - fullscale_union
        observed_scope = {
            "version": "jdim-declared-legacy-scope-v1",
            "legacy_studies_total": len(studies),
            "legacy_study_set_sha256": canonical_id_set_sha256(studies),
            "inside_selected_universe_count": len(inside),
            "inside_selected_universe_study_set_sha256": canonical_id_set_sha256(inside),
            "outside_selected_universe_count": len(outside),
            "outside_selected_universe_study_set_sha256": canonical_id_set_sha256(outside),
            "later_fullscale_overlap_count": len(later_overlap),
            "later_fullscale_overlap_study_set_sha256": canonical_id_set_sha256(later_overlap),
            "deduplicated_canonical_contribution_count": len(canonical_contribution),
            "deduplicated_canonical_contribution_study_set_sha256": canonical_id_set_sha256(
                canonical_contribution
            ),
        }
        declared_scope = batch_meta[name].get("declared_legacy_scope")
        if not isinstance(declared_scope, Mapping) or dict(declared_scope) != observed_scope:
            raise Tier1BlockedError(
                BLOCKED_UNDECLARED_LEGACY_SCOPE,
                f"{name} study sets do not match the exact declared legacy scope",
            )
        row.update(
            {
                "legacy_studies_total": len(studies),
                "legacy_studies_inside_selected_universe": len(inside),
                "declared_legacy_outside_analysis_universe": len(outside),
                "later_fullscale_overlap": len(later_overlap),
                "deduplicated_canonical_universe_contribution": len(canonical_contribution),
                "legacy_study_set_sha256": observed_scope["legacy_study_set_sha256"],
                "inside_selected_universe_study_set_sha256": observed_scope[
                    "inside_selected_universe_study_set_sha256"
                ],
                "outside_selected_universe_study_set_sha256": observed_scope[
                    "outside_selected_universe_study_set_sha256"
                ],
                "later_fullscale_overlap_study_set_sha256": observed_scope[
                    "later_fullscale_overlap_study_set_sha256"
                ],
                "deduplicated_canonical_contribution_study_set_sha256": observed_scope[
                    "deduplicated_canonical_contribution_study_set_sha256"
                ],
            }
        )
        report.add(
            f"declared_legacy_scope_exact__{name}",
            True,
            observed_scope["outside_selected_universe_count"],
            declared_scope["outside_selected_universe_count"],
        )

    merged = pd.concat(normalized, ignore_index=True, sort=False)
    merged, forensic_counts = _apply_forensic_clip_decisions(merged, forensic_rows, report)
    for row in batch_rows:
        batch = str(row["batch_name"])
        selected = merged[merged["_batch"] == batch]
        row["n_canonical_clip_rows_after_forensics"] = int(selected["_canonical_keep"].sum())
        for classification in sorted(FORENSIC_CLASSES):
            row[f"n_rows_{classification.lower()}"] = int(
                (selected["_forensic_classification"] == classification).sum()
            )
    allowed_pairs = {
        tuple(sorted(str(item).split("|")))
        for item in metadata.get("allowed_batch_overlap_pairs", [])
        if "|" in str(item)
    }
    for left_index, left in enumerate(sorted(batch_study_sets)):
        for right in sorted(batch_study_sets)[left_index + 1 :]:
            overlap = len(batch_study_sets[left] & batch_study_sets[right])
            pair = tuple(sorted((left, right)))
            explained = overlap == 0 or pair in allowed_pairs
            report.add(
                f"batch_overlap_explained__{left}__{right}",
                explained,
                overlap,
                0 if pair not in allowed_pairs else "declared_allowed_overlap",
            )

    in_universe = merged[merged["_in_canonical_universe"]].copy()
    canonical_clips = in_universe.loc[in_universe["_canonical_keep"]].sort_values(
        ["_study", "_batch", "_manifest_row"], kind="mergesort"
    )
    declared_outside_clips = (
        merged.loc[merged["_outside_scope_class"] == "declared_legacy_scope"]
        .loc[lambda frame: frame["_canonical_keep"]]
        .sort_values(["_study", "_batch", "_manifest_row"], kind="mergesort")
    )
    undeclared_outside = merged.loc[merged["_outside_scope_class"] == "undeclared_outside_scope"]
    canonical_studies = set(canonical_clips["_study"])
    declared_outside_studies = set(declared_outside_clips["_study"])
    undeclared_outside_studies = set(undeclared_outside["_study"])
    report.add(
        "unique_canonical_study_keys_after_batch_deduplication",
        len(canonical_studies) == canonical_clips["_study"].nunique(),
        canonical_clips["_study"].nunique(),
        len(canonical_studies),
    )
    report.add(
        "no_undeclared_outside_universe_batch_studies",
        not undeclared_outside_studies,
        len(undeclared_outside_studies),
        0,
    )
    if undeclared_outside_studies:
        raise Tier1BlockedError(
            BLOCKED_UNDECLARED_LEGACY_SCOPE,
            "an embedding batch contains studies outside the exact declared legacy scope",
        )
    stages = [
        _stage("imaging", "canonical_universe_embedded_clips_forensically_adjudicated", canonical_clips),
        _stage(
            "imaging",
            "canonical_universe_studies_with_usable_embeddings",
            canonical_clips.drop_duplicates("_study"),
        ),
        _stage(
            "imaging",
            "outside_universe_declared_legacy_embedded_clips",
            declared_outside_clips,
        ),
        _stage(
            "imaging",
            "declared_legacy_outside_analysis_universe",
            declared_outside_clips.drop_duplicates("_study"),
        ),
        _stage(
            "imaging",
            "outside_universe_undeclared_embedding_studies",
            undeclared_outside.drop_duplicates("_study"),
        ),
    ]
    for classification, n_rows in sorted(forensic_counts.items()):
        stages.append(
            {
                "branch": "imaging",
                "target": "",
                "stage": f"repeated_clip_rows_{classification.lower()}",
                "n_rows": int(n_rows),
                "n_studies": None,
                "n_subjects": None,
                "notes": "Restricted forensic decisions; no row identifiers exported.",
            }
        )
    return EmbeddingBatchResult(
        merged=merged,
        canonical_clips=canonical_clips,
        declared_outside_clips=declared_outside_clips,
        stages=stages,
        canonical_studies=canonical_studies,
        declared_outside_studies=declared_outside_studies,
        undeclared_outside_studies=undeclared_outside_studies,
        reconciliation=pd.DataFrame(batch_rows),
    )


def _apply_forensic_clip_decisions(
    merged: pd.DataFrame,
    evidence: pd.DataFrame,
    report: InvariantReport,
) -> tuple[pd.DataFrame, dict[str, int]]:
    work = merged.copy()
    repeated_mask = work.duplicated("_record", keep=False)
    repeated = work.loc[repeated_mask].copy()
    work["_forensic_classification"] = "UNIQUE"
    work["_forensic_group_token"] = ""
    work["_canonical_keep"] = True

    required = [
        "group_token",
        "classification",
        "_batch",
        "_manifest_row",
        "study_id",
        "subject_id",
        "dedup_keep_candidate",
        "source_manifest_row_fingerprint_sha256",
    ]
    if repeated.empty:
        extra = 0 if evidence.empty else len(evidence)
        report.add("forensic_rows_exactly_match_repeated_embedding_rows", extra == 0, extra, 0)
        return work, {}
    require_columns(evidence, required, "duplicate forensic decisions")
    decisions = _normalize_ids(evidence, "duplicate forensic decisions")
    decisions["_manifest_row"] = pd.to_numeric(decisions["_manifest_row"], errors="coerce")
    if decisions["_manifest_row"].isna().any():
        raise Tier1BlockedError(BLOCKED_LINEAGE, "forensic decisions contain invalid manifest rows")
    decisions["_manifest_row"] = decisions["_manifest_row"].astype(int)
    decisions["classification"] = decisions["classification"].astype(str).str.strip().str.upper()
    invalid = sorted(set(decisions["classification"]) - FORENSIC_CLASSES)
    ambiguous = int((decisions["classification"] == "AMBIGUOUS_REQUIRES_AUTHOR_REVIEW").sum())
    report.add("forensic_classifications_are_valid", not invalid, invalid, [])
    report.add("no_ambiguous_repeated_clip_groups", ambiguous == 0, ambiguous, 0)
    if invalid or ambiguous:
        raise Tier1BlockedError(BLOCKED_LINEAGE, "duplicate forensic decisions are invalid or ambiguous")

    repeated_keys = set(
        zip(repeated["_batch"].astype(str), repeated["_manifest_row"].astype(int))
    )
    decision_keys = set(
        zip(decisions["_batch"].astype(str), decisions["_manifest_row"].astype(int))
    )
    exact = repeated_keys == decision_keys and len(repeated) == len(decisions)
    report.add(
        "forensic_rows_exactly_match_repeated_embedding_rows",
        exact,
        len(repeated_keys.symmetric_difference(decision_keys)),
        0,
    )
    if not exact:
        raise Tier1BlockedError(BLOCKED_LINEAGE, "forensic decisions do not exactly cover repeated clip rows")

    decision_lookup = decisions.set_index(["_batch", "_manifest_row"])
    for index, row in repeated.iterrows():
        key = (str(row["_batch"]), int(row["_manifest_row"]))
        matched = decision_lookup.loc[key]
        if isinstance(matched, pd.DataFrame):
            raise Tier1BlockedError(BLOCKED_LINEAGE, "forensic decision row identity is not unique")
        if str(matched["_study"]) != str(row["_study"]) or str(matched["_subject"]) != str(
            row["_subject"]
        ):
            raise Tier1BlockedError(
                BLOCKED_LINEAGE,
                "forensic decision identity does not match the repeated embedding row",
            )
        if str(matched["source_manifest_row_fingerprint_sha256"]) != str(
            row["_source_manifest_row_fingerprint_sha256"]
        ):
            raise Tier1BlockedError(
                BLOCKED_LINEAGE,
                "forensic decision source-row fingerprint does not match the embedding manifest",
            )
        classification = str(matched["classification"])
        work.at[index, "_forensic_classification"] = classification
        work.at[index, "_forensic_group_token"] = str(matched["group_token"])
        if classification in TRUE_DUPLICATE_CLASSES:
            value = str(matched["dedup_keep_candidate"]).strip().lower()
            work.at[index, "_canonical_keep"] = value in {"true", "1", "yes", "y"}

    repeated_with_decisions = work.loc[repeated.index]
    if (repeated_with_decisions["_forensic_group_token"] == "").any():
        raise Tier1BlockedError(BLOCKED_LINEAGE, "a repeated embedding row lacks a forensic group")
    tokens_per_record = repeated_with_decisions.groupby("_record")["_forensic_group_token"].nunique()
    records_per_token = repeated_with_decisions.groupby("_forensic_group_token")["_record"].nunique()
    if bool((tokens_per_record != 1).any()) or bool((records_per_token != 1).any()):
        raise Tier1BlockedError(
            BLOCKED_LINEAGE,
            "forensic groups do not map one-to-one to repeated coarse-record groups",
        )

    for token, group in decisions.groupby("group_token", sort=True):
        classes = set(group["classification"])
        if len(classes) != 1:
            raise Tier1BlockedError(BLOCKED_LINEAGE, f"forensic group {token} has conflicting classes")
        classification = next(iter(classes))
        keep_count = sum(
            str(value).strip().lower() in {"true", "1", "yes", "y"}
            for value in group["dedup_keep_candidate"]
        )
        if classification in TRUE_DUPLICATE_CLASSES and keep_count != 1:
            raise Tier1BlockedError(BLOCKED_LINEAGE, f"forensic duplicate group {token} lacks one keeper")
        if classification not in TRUE_DUPLICATE_CLASSES and keep_count != len(group):
            raise Tier1BlockedError(BLOCKED_LINEAGE, f"forensic distinct group {token} would remove a clip")

    counts = {
        classification: int((decisions["classification"] == classification).sum())
        for classification in sorted(set(decisions["classification"]))
    }
    return work, counts


def _target_branch(
    measures: pd.DataFrame,
    target: str,
) -> tuple[pd.DataFrame, dict[str, Any], list[dict[str, Any]], pd.DataFrame]:
    require_columns(measures, ["study_id", "subject_id", "measurement", "result"], "structured measurements")
    work = _normalize_ids(measures, "structured measurements")
    work["_measurement_norm"] = work["measurement"].map(normalize_text)
    exact = work[work["_measurement_norm"] == normalize_text(TARGETS[target]["measurement"])].copy()
    exact["_result_num"] = pd.to_numeric(exact["result"], errors="coerce")
    numeric = exact[exact["_result_num"].notna()].copy()
    unit_col = "unit" if "unit" in numeric.columns else "units" if "units" in numeric.columns else None
    if unit_col is None:
        numeric["_unit_norm"] = "unknown"
    else:
        numeric["_unit_norm"] = numeric[unit_col].map(normalize_unit)
    recognized = numeric["_unit_norm"].isin({"cm", "mm"})

    grouped, canonical_summary = extract_target(measures, target, exclude_hard_extremes=False)
    grouped = grouped.rename(columns={"study_id_str": "_study", "subject_id_str": "_subject"})
    if grouped["_study"].duplicated().any():
        raise Tier1BlockedError(BLOCKED_LINEAGE, f"{target} labels map one study to multiple subjects")
    multiplicity = (
        grouped["n_target_rows"]
        .value_counts()
        .sort_index()
        .rename_axis("eligible_rows_per_study")
        .reset_index(name="n_studies")
    )
    multiplicity.insert(0, "target", target)
    stages = [
        _stage("labels", "raw_matching_measurement_rows", exact, target=target),
        _stage("labels", "numeric_parsable_rows", numeric, target=target),
        _stage("labels", "recognized_unit_rows", numeric, recognized, target=target),
        _stage(
            "labels",
            "rows_surviving_primary_canonical_rules",
            numeric,
            target=target,
            notes="Primary analysis retains numeric hard extremes; median study aggregation follows.",
        ),
        _stage("labels", "study_level_median_report_labels", grouped, target=target),
    ]
    branch_summary = {
        **canonical_summary,
        "raw_matching_rows": int(len(exact)),
        "numeric_parsable_rows": int(len(numeric)),
        "recognized_unit_rows": int(recognized.sum()),
        "unknown_or_unrecognized_unit_rows": int((~recognized).sum()),
        "study_label_aggregation": "median",
    }
    return grouped, branch_summary, stages, multiplicity


def _intersection_branch(
    target: str,
    labels: pd.DataFrame,
    embedded_studies: set[str],
    split_frame: pd.DataFrame,
    canonical_summary: Mapping[str, Any],
    report: InvariantReport,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], pd.DataFrame, pd.DataFrame]:
    label_studies = set(labels["_study"])
    target_without_embedding = label_studies - embedded_studies
    embedding_without_target = embedded_studies - label_studies
    intersection = labels[labels["_study"].isin(embedded_studies)].copy()
    split_lookup = split_frame.set_index("subject_id_str")["split"].to_dict()
    intersection["split"] = intersection["_subject"].map(split_lookup)
    missing_split = intersection["split"].isna()
    final = intersection[intersection["split"].isin(VALID_SPLITS)].copy()

    report.add(
        f"{target}_target_partition_reconciles",
        len(target_without_embedding) + len(set(intersection["_study"])) == len(label_studies),
        len(target_without_embedding) + len(set(intersection["_study"])),
        len(label_studies),
    )
    report.add(
        f"{target}_embedding_partition_reconciles",
        len(embedding_without_target) + len(set(intersection["_study"])) == len(embedded_studies),
        len(embedding_without_target) + len(set(intersection["_study"])),
        len(embedded_studies),
    )

    split_rows: list[dict[str, Any]] = []
    for split in VALID_SPLITS:
        subset = final[final["split"] == split]
        split_rows.append(
            {
                "target": target,
                "split": split,
                "n_studies": int(subset["_study"].nunique()),
                "n_subjects": int(subset["_subject"].nunique()),
            }
        )
    split_counts = {row["split"]: row["n_studies"] for row in split_rows}
    expected_splits = {key: int(value) for key, value in canonical_summary.get("split_counts", {}).items()}
    report.add(
        f"{target}_final_split_totals_match_canonical_analysis",
        split_counts == expected_splits,
        split_counts,
        expected_splits,
    )
    expected_joined = int(canonical_summary.get("joined_target_embedding_studies", -1))
    report.add(
        f"{target}_final_study_total_matches_canonical_analysis",
        final["_study"].nunique() == expected_joined,
        int(final["_study"].nunique()),
        expected_joined,
    )
    expected_subjects = canonical_summary.get("joined_target_embedding_subjects")
    if expected_subjects is not None:
        report.add(
            f"{target}_final_subject_total_matches_canonical_analysis",
            final["_subject"].nunique() == int(expected_subjects),
            int(final["_subject"].nunique()),
            int(expected_subjects),
        )
    if split_counts != expected_splits or final["_study"].nunique() != expected_joined:
        raise Tier1BlockedError(
            BLOCKED_COHORT_CANONICAL_MISMATCH,
            f"{target} reconstructed split or total diverges from the locked canonical analysis",
        )
    if expected_subjects is not None and final["_subject"].nunique() != int(expected_subjects):
        raise Tier1BlockedError(
            BLOCKED_COHORT_CANONICAL_MISMATCH,
            f"{target} reconstructed subject count diverges from the locked canonical analysis",
        )

    overlap_counts: dict[str, int] = {}
    subjects_by_split = {
        split: set(final.loc[final["split"] == split, "_subject"])
        for split in VALID_SPLITS
    }
    for left_index, left in enumerate(VALID_SPLITS):
        for right in VALID_SPLITS[left_index + 1 :]:
            overlap_counts[f"{left}_vs_{right}"] = len(subjects_by_split[left] & subjects_by_split[right])
    report.add(
        f"{target}_no_subject_overlap_across_splits",
        sum(overlap_counts.values()) == 0,
        overlap_counts,
        {key: 0 for key in overlap_counts},
    )

    multiplicity = (
        final.groupby("_subject")["_study"]
        .nunique()
        .value_counts()
        .sort_index()
        .rename_axis("studies_per_subject")
        .reset_index(name="n_subjects")
    )
    multiplicity.insert(0, "target", target)
    stages = [
        {
            "branch": "intersection",
            "target": target,
            "stage": "target_positive_studies_without_usable_embeddings",
            "n_rows": None,
            "n_studies": len(target_without_embedding),
            "n_subjects": int(labels.loc[labels["_study"].isin(target_without_embedding), "_subject"].nunique()),
            "notes": "",
        },
        {
            "branch": "intersection",
            "target": target,
            "stage": "embedded_studies_without_target",
            "n_rows": None,
            "n_studies": len(embedding_without_target),
            "n_subjects": None,
            "notes": "",
        },
        _stage("intersection", "target_plus_embedding_studies", intersection, target=target),
        _stage(
            "intersection",
            "target_plus_embedding_studies_lacking_split",
            intersection,
            missing_split,
            target=target,
        ),
        _stage("intersection", "final_analysis_studies", final, target=target),
    ]
    summary = {
        "target": target,
        "target_positive_studies": int(len(label_studies)),
        "target_positive_studies_without_usable_embeddings": int(len(target_without_embedding)),
        "embedded_studies_without_target": int(len(embedding_without_target)),
        "target_plus_embedding_studies": int(intersection["_study"].nunique()),
        "studies_lacking_split_assignment": int(missing_split.sum()),
        "final_analysis_studies": int(final["_study"].nunique()),
        "final_analysis_subjects": int(final["_subject"].nunique()),
        "split_study_counts": split_counts,
        "subject_overlap_counts": overlap_counts,
    }
    restricted = final[["_study", "_subject", "split", "target_value"]].copy()
    restricted["target"] = target
    return summary, stages, split_rows, multiplicity, restricted


def _verify_prediction_rows(
    target: str,
    reconstructed: pd.DataFrame,
    predictions: pd.DataFrame,
    report: InvariantReport,
) -> None:
    work = predictions.copy()
    if "target_value" not in work.columns and "y_true" in work.columns:
        work = work.rename(columns={"y_true": "target_value"})
    require_columns(
        work,
        ["study_id", "subject_id", "split", "target_value"],
        f"{target} canonical predictions",
    )
    if "target" in work.columns:
        observed_targets = set(work["target"].dropna().astype(str))
        if observed_targets != {target}:
            raise Tier1BlockedError(
                BLOCKED_LINEAGE,
                f"{target} canonical prediction file contains targets {sorted(observed_targets)}",
            )
    work = _normalize_ids(work, f"{target} canonical predictions")
    work["split"] = work["split"].astype(str).str.strip().str.lower()
    work["target_value"] = pd.to_numeric(work["target_value"], errors="coerce")
    if (
        work["target_value"].isna().any()
        or not np.isfinite(work["target_value"].to_numpy(dtype=float)).all()
        or work["_study"].duplicated().any()
    ):
        raise Tier1BlockedError(BLOCKED_LINEAGE, f"{target} canonical prediction rows are invalid")
    left = reconstructed.sort_values("_study", kind="mergesort").reset_index(drop=True)
    right = work[["_study", "_subject", "split", "target_value"]].sort_values(
        "_study", kind="mergesort"
    ).reset_index(drop=True)
    keys_match = left["_study"].equals(right["_study"])
    subjects_match = keys_match and left["_subject"].equals(right["_subject"])
    splits_match = keys_match and left["split"].equals(right["split"])
    values_match = keys_match and pd.Series(
        left["target_value"].to_numpy(dtype=float)
    ).equals(pd.Series(right["target_value"].to_numpy(dtype=float)))
    report.add(f"{target}_prediction_study_rows_match_reconstruction", keys_match, len(left), len(right))
    report.add(f"{target}_prediction_subjects_match_reconstruction", subjects_match, subjects_match, True)
    report.add(f"{target}_prediction_splits_match_reconstruction", splits_match, splits_match, True)
    report.add(f"{target}_prediction_labels_match_reconstruction", values_match, values_match, True)


def reconstruct_cohort_flow(inputs: CohortFlowInputs) -> CohortFlowResult:
    report = InvariantReport()
    if not inputs.lineage_metadata.exists():
        raise Tier1BlockedError(BLOCKED_LINEAGE, "missing lineage metadata JSON")
    metadata = json.loads(inputs.lineage_metadata.read_text(encoding="utf-8"))
    validate_lineage_metadata(metadata, list(inputs.embedding_batches), inputs.split_map, report)
    forensic_provenance = _validate_duplicate_forensics_provenance(inputs, report)

    source = _normalize_ids(_read_csv(inputs.source_studies, "source study universe"), "source study universe")
    eligible = _normalize_ids(_read_csv(inputs.eligible_studies, "eligible study universe"), "eligible study universe")
    expected = _read_many(inputs.expected_records, "expected DICOM records")
    audits = _read_many(inputs.dicom_audits, "DICOM audits")
    extraction = _read_many(inputs.extraction_manifests, "extraction manifests")
    batch_frames = {
        name: _read_csv(path, f"embedding batch {name}")
        for name, path in inputs.embedding_batches.items()
    }
    forensic_rows = _read_csv(inputs.duplicate_forensics, "duplicate forensic decisions")
    duplicate_metadata_policy = _validate_metadata_duplicate_policy(
        inputs,
        forensic_provenance,
        forensic_rows,
        report,
    )
    canonical_prediction_frames = {
        target: _read_csv(path, f"{target} canonical predictions")
        for target, path in inputs.canonical_predictions.items()
    }
    if set(canonical_prediction_frames) != set(TARGET_NAMES):
        raise Tier1BlockedError(BLOCKED_LINEAGE, "canonical prediction inputs must be lvot_vti and tapse")
    final_embeddings = _normalize_ids(
        _read_csv(inputs.final_study_embeddings, "final study embedding manifest"),
        "final study embedding manifest",
    )
    measures = _read_csv(inputs.structured_measurements, "structured measurements")
    raw_split = _read_csv(inputs.split_map, "split map")
    try:
        split_frame, split_warnings = load_splits(inputs.split_map)
    except (ValueError, RuntimeError) as exc:
        raise Tier1BlockedError(BLOCKED_LINEAGE, f"invalid frozen split map: {exc}") from exc
    report.add("split_map_has_no_unexpected_labels", not split_warnings, len(split_warnings), 0)

    named_for_subjects: dict[str, pd.DataFrame] = {
        "source_studies": source,
        "eligible_studies": eligible,
        "expected_records": expected,
        "dicom_audits": audits,
        "extraction_manifests": extraction,
        "final_study_embeddings": final_embeddings,
        "structured_measurements": measures,
    }
    named_for_subjects.update({f"embedding_batch:{name}": frame for name, frame in batch_frames.items()})
    _assert_subject_consistency(named_for_subjects, report)

    source_dup = int(source["_study"].duplicated().sum())
    eligible_dup = int(eligible["_study"].duplicated().sum())
    final_dup = int(final_embeddings["_study"].duplicated().sum())
    report.add("unique_source_study_keys", source_dup == 0, source_dup, 0)
    report.add("unique_eligible_study_keys", eligible_dup == 0, eligible_dup, 0)
    report.add("unique_final_study_embedding_keys", final_dup == 0, final_dup, 0)
    report.add(
        "eligible_universe_is_subset_of_source_denominator",
        set(eligible["_study"]).issubset(set(source["_study"])),
        len(set(eligible["_study"]) - set(source["_study"])),
        0,
    )
    declared_selected = metadata.get("selected_analysis_universe")
    if isinstance(declared_selected, Mapping):
        observed_selected = {
            "study_count": int(eligible["_study"].nunique()),
            "subject_count": int(eligible["_subject"].nunique()),
            "study_set_sha256": canonical_id_set_sha256(eligible["_study"]),
            "source_sha256": sha256_file(inputs.eligible_studies),
            "deterministic_selection_rule": str(
                declared_selected.get("deterministic_selection_rule", "")
            ),
        }
        if dict(declared_selected) != observed_selected:
            raise Tier1BlockedError(
                BLOCKED_UNDECLARED_LEGACY_SCOPE,
                "selected analysis universe does not match its hash-pinned lineage declaration",
            )
        report.add(
            "selected_analysis_universe_matches_declared_hash",
            True,
            observed_selected["study_set_sha256"],
            declared_selected["study_set_sha256"],
        )

    stage_rows: list[dict[str, Any]] = [
        _stage("imaging", "official_release_source_studies", source),
        _stage("imaging", "project_eligible_studies", eligible),
    ]
    dicom_stages, expected, audits, extraction, metadata_adjudication = _prepare_dicom_branch(
        expected,
        audits,
        extraction,
        report,
        duplicate_metadata_policy,
    )
    stage_rows.extend(dicom_stages)

    eligible_set = set(eligible["_study"])
    embedding_result = _prepare_embedding_batches(
        batch_frames,
        eligible_set,
        metadata,
        forensic_rows,
        report,
    )
    stage_rows.extend(embedding_result.stages)
    final_set = set(final_embeddings["_study"])
    final_canonical_set = final_set & eligible_set
    final_outside_set = final_set - eligible_set
    report.add(
        "canonical_batch_union_matches_final_manifest_canonical_intersection",
        embedding_result.canonical_studies == final_canonical_set,
        len(embedding_result.canonical_studies.symmetric_difference(final_canonical_set)),
        0,
    )
    report.add(
        "final_outside_universe_embeddings_are_declared_legacy",
        final_outside_set.issubset(embedding_result.declared_outside_studies),
        len(final_outside_set - embedding_result.declared_outside_studies),
        0,
    )
    report.add(
        "declared_legacy_outside_union_matches_final_manifest_outside_scope",
        embedding_result.declared_outside_studies == final_outside_set,
        len(embedding_result.declared_outside_studies.symmetric_difference(final_outside_set)),
        0,
    )
    manifest_identity = final_set == (
        embedding_result.canonical_studies | embedding_result.declared_outside_studies
    )
    report.add(
        "final_manifest_equals_canonical_union_declared_legacy_after_study_deduplication",
        manifest_identity,
        len(
            final_set.symmetric_difference(
                embedding_result.canonical_studies
                | embedding_result.declared_outside_studies
            )
        ),
        0,
    )
    if (
        not final_outside_set.issubset(embedding_result.declared_outside_studies)
        or embedding_result.declared_outside_studies != final_outside_set
        or not manifest_identity
    ):
        raise Tier1BlockedError(
            BLOCKED_UNDECLARED_LEGACY_SCOPE,
            "final embedding manifest does not reconcile to canonical plus declared legacy scope",
        )
    final_canonical_embeddings = final_embeddings.loc[final_embeddings["_study"].isin(final_canonical_set)]
    final_outside_embeddings = final_embeddings.loc[final_embeddings["_study"].isin(final_outside_set)]
    stage_rows.extend(
        [
            _stage(
                "imaging",
                "final_manifest_canonical_universe_study_embeddings",
                final_canonical_embeddings,
            ),
            _stage(
                "imaging",
                "final_manifest_outside_universe_retained_legacy_embeddings",
                final_outside_embeddings,
            ),
        ]
    )
    require_columns(final_embeddings, ["n_clips"], "final study embedding manifest")
    final_embeddings["n_clips"] = pd.to_numeric(final_embeddings["n_clips"], errors="coerce")
    if (
        final_embeddings["n_clips"].isna().any()
        or not np.isfinite(final_embeddings["n_clips"].to_numpy(dtype=float)).all()
        or not np.all(final_embeddings["n_clips"] == np.floor(final_embeddings["n_clips"]))
        or bool((final_embeddings["n_clips"] <= 0).any())
    ):
        raise Tier1BlockedError(BLOCKED_LINEAGE, "final study embedding manifest has invalid clip counts")
    canonical_input_clips = pd.concat(
        [embedding_result.canonical_clips, embedding_result.declared_outside_clips],
        ignore_index=True,
    )
    replay_counts = canonical_input_clips.groupby("_study").size().astype(int).to_dict()
    frozen_counts = final_embeddings.set_index("_study")["n_clips"].astype(int).to_dict()
    report.add(
        "forensically_adjudicated_clip_counts_match_final_study_manifest",
        replay_counts == frozen_counts,
        len(set(replay_counts) ^ set(frozen_counts))
        + sum(replay_counts.get(study) != frozen_counts.get(study) for study in set(replay_counts) & set(frozen_counts)),
        0,
    )

    label_summaries: dict[str, Any] = {}
    target_summaries: dict[str, Any] = {}
    split_rows: list[dict[str, Any]] = []
    label_multiplicity_frames: list[pd.DataFrame] = []
    subject_multiplicity_frames: list[pd.DataFrame] = []
    restricted_target_frames: list[pd.DataFrame] = []
    label_frames: dict[str, pd.DataFrame] = {}
    for target in TARGET_NAMES:
        labels, label_summary, label_stages, label_multiplicity = _target_branch(measures, target)
        outside_labels = set(labels["_study"]) & final_outside_set
        report.add(
            f"{target}_declared_legacy_outside_studies_do_not_enter_target_labels",
            not outside_labels,
            len(outside_labels),
            0,
        )
        if outside_labels:
            raise Tier1BlockedError(
                BLOCKED_UNDECLARED_LEGACY_SCOPE,
                f"declared legacy outside-universe studies entered the {target} label cohort",
            )
        label_frames[target] = labels
        label_summaries[target] = label_summary
        stage_rows.extend(label_stages)
        label_multiplicity_frames.append(label_multiplicity)
        canonical = _load_canonical_summary(inputs.canonical_summaries[target], target)
        target_summary, target_stages, target_split_rows, subject_multiplicity, restricted = _intersection_branch(
            target,
            labels,
            final_canonical_set,
            split_frame,
            canonical,
            report,
        )
        _verify_prediction_rows(
            target,
            restricted,
            canonical_prediction_frames[target],
            report,
        )
        prediction_studies = set(
            canonical_prediction_frames[target]["study_id"].astype(str)
        )
        outside_predictions = prediction_studies & final_outside_set
        report.add(
            f"{target}_declared_legacy_outside_studies_receive_no_canonical_split",
            not outside_predictions,
            len(outside_predictions),
            0,
        )
        if outside_predictions:
            raise Tier1BlockedError(
                BLOCKED_UNDECLARED_LEGACY_SCOPE,
                f"declared legacy outside-universe studies entered canonical {target} predictions",
            )
        target_summaries[target] = target_summary
        stage_rows.extend(target_stages)
        split_rows.extend(target_split_rows)
        subject_multiplicity_frames.append(subject_multiplicity)
        restricted_target_frames.append(restricted)

    stage_frame = pd.DataFrame(stage_rows)
    split_output = pd.DataFrame(split_rows)
    label_multiplicity = pd.concat(label_multiplicity_frames, ignore_index=True)
    subject_multiplicity = pd.concat(subject_multiplicity_frames, ignore_index=True)
    for name, frame in (
        ("cohort stages", stage_frame),
        ("target split counts", split_output),
        ("label multiplicity", label_multiplicity),
        ("subject multiplicity", subject_multiplicity),
        ("batch reconciliation", embedding_result.reconciliation),
    ):
        assert_export_safe_frame(frame, name)

    numeric_columns = stage_frame.select_dtypes(include="number").columns
    negative_counts = int((stage_frame[numeric_columns].fillna(0) < 0).sum().sum())
    report.add("no_negative_aggregate_counts", negative_counts == 0, negative_counts, 0)

    restricted_base = eligible[["_study", "_subject"]].drop_duplicates().copy()
    batch_memberships = (
        embedding_result.merged[embedding_result.merged["_in_canonical_universe"]]
        .groupby("_study")["_batch"]
        .agg(lambda values: ";".join(sorted(set(values))))
    )
    restricted_base["embedding_batches"] = restricted_base["_study"].map(batch_memberships).fillna("")
    restricted_base["final_embedding_present"] = restricted_base["_study"].isin(final_canonical_set)
    for target, labels in label_frames.items():
        restricted_base[f"{target}_label_present"] = restricted_base["_study"].isin(set(labels["_study"]))
        target_lookup = labels.set_index("_study")
        restricted_base[f"{target}_target_value"] = restricted_base["_study"].map(
            target_lookup["target_value"].to_dict()
        )
        restricted_base[f"{target}_n_target_rows"] = restricted_base["_study"].map(
            target_lookup["n_target_rows"].to_dict()
        )
    restricted_intersections = pd.concat(restricted_target_frames, ignore_index=True)
    restricted_intersections = restricted_intersections.pivot(
        index=["_study", "_subject"], columns="target", values="split"
    ).reset_index()
    restricted_intersections.columns.name = None
    restricted_intersections = restricted_intersections.rename(
        columns={target: f"{target}_split" for target in TARGET_NAMES if target in restricted_intersections.columns}
    )
    restricted_reconciliation = restricted_base.merge(
        restricted_intersections,
        on=["_study", "_subject"],
        how="left",
    ).rename(columns={"_study": "study_id", "_subject": "subject_id"})

    summary = {
        "protocol_version": PROTOCOL_VERSION,
        "status": "ok" if report.passed else BLOCKED_LINEAGE,
        "mimic_iv_echo_release": metadata["mimic_iv_echo_release"],
        "source_denominator_definition": metadata["source_denominator_definition"],
        "source_studies": int(source["_study"].nunique()),
        "source_subjects": int(source["_subject"].nunique()),
        "eligible_studies": int(eligible["_study"].nunique()),
        "eligible_subjects": int(eligible["_subject"].nunique()),
        "final_study_embeddings": int(len(final_set)),
        "final_embedding_subjects": int(final_embeddings["_subject"].nunique()),
        "canonical_universe_study_embeddings": int(len(final_canonical_set)),
        "canonical_universe_embedding_subjects": int(
            final_canonical_embeddings["_subject"].nunique()
        ),
        "outside_universe_retained_legacy_embeddings": int(len(final_outside_set)),
        "outside_universe_retained_legacy_subjects": int(
            final_outside_embeddings["_subject"].nunique()
        ),
        "outside_universe_unexplained_embeddings": int(
            len(final_outside_set - embedding_result.declared_outside_studies)
        ),
        "declared_legacy_outside_analysis_universe": int(len(final_outside_set)),
        "declared_legacy_outside_analysis_universe_study_set_sha256": canonical_id_set_sha256(
            final_outside_set
        ),
        "canonical_universe_study_set_sha256": canonical_id_set_sha256(final_canonical_set),
        "final_manifest_study_set_sha256": canonical_id_set_sha256(final_set),
        "metadata_duplicate_adjudication": metadata_adjudication,
        "selected_studies_per_subject_distribution": {
            str(int(studies_per_subject)): int(n_subjects)
            for studies_per_subject, n_subjects in eligible.groupby("_subject")["_study"]
            .nunique()
            .value_counts()
            .sort_index()
            .items()
        },
        "label_branches": label_summaries,
        "target_intersections": target_summaries,
        "split_map_subjects": int(raw_split["subject_id"].nunique()),
        "split_map_sha256": sha256_file(inputs.split_map),
        "duplicate_forensics_provenance_sha256": sha256_file(
            inputs.duplicate_forensics_provenance
        ),
        "duplicate_metadata_packet_hashes": (
            dict(duplicate_metadata_policy.packet_hashes)
            if duplicate_metadata_policy is not None
            else None
        ),
        "all_invariants_passed": report.passed,
    }

    input_paths: list[tuple[str, Path, int | None, pd.DataFrame | None]] = [
        ("source_study_universe", inputs.source_studies, len(source), source),
        ("eligible_study_universe", inputs.eligible_studies, len(eligible), eligible),
        ("final_study_embedding_manifest", inputs.final_study_embeddings, len(final_embeddings), final_embeddings),
        ("structured_measurements", inputs.structured_measurements, len(measures), measures),
        ("frozen_split_map", inputs.split_map, len(raw_split), raw_split),
        ("lineage_metadata", inputs.lineage_metadata, None, None),
        ("duplicate_forensic_decisions", inputs.duplicate_forensics, len(forensic_rows), forensic_rows),
        (
            "duplicate_forensics_provenance",
            inputs.duplicate_forensics_provenance,
            None,
            None,
        ),
    ]
    if inputs.duplicate_metadata_summary is not None:
        input_paths.append(
            ("duplicate_metadata_summary", inputs.duplicate_metadata_summary, None, None)
        )
    if inputs.duplicate_metadata_groups is not None:
        input_paths.append(
            ("duplicate_metadata_groups", inputs.duplicate_metadata_groups, None, None)
        )
    if inputs.duplicate_metadata_stage_rows is not None:
        input_paths.append(
            (
                "duplicate_metadata_stage_rows",
                inputs.duplicate_metadata_stage_rows,
                None,
                None,
            )
        )
    if inputs.corrected_clip_embeddings is not None:
        input_paths.append(
            ("corrected_clip_embedding_manifest", inputs.corrected_clip_embeddings, None, None)
        )
    input_paths.extend(
        (f"expected_records_{index}", path, None, None)
        for index, path in enumerate(inputs.expected_records)
    )
    input_paths.extend(
        (f"canonical_predictions_{target}", path, len(canonical_prediction_frames[target]), canonical_prediction_frames[target])
        for target, path in inputs.canonical_predictions.items()
    )
    input_paths.extend(
        (f"dicom_audit_{index}", path, None, None)
        for index, path in enumerate(inputs.dicom_audits)
    )
    input_paths.extend(
        (f"extraction_manifest_{index}", path, None, None)
        for index, path in enumerate(inputs.extraction_manifests)
    )
    input_paths.extend(
        (f"embedding_batch_{name}", path, len(batch_frames[name]), batch_frames[name])
        for name, path in inputs.embedding_batches.items()
    )
    input_paths.extend(
        (f"embedding_batch_array_{name}", path, None, None)
        for name, path in inputs.embedding_batch_npzs.items()
    )
    input_paths.extend(
        (f"canonical_summary_{target}", path, None, None)
        for target, path in inputs.canonical_summaries.items()
    )
    files: list[dict[str, Any]] = []
    for role, path, row_count, frame in input_paths:
        record = safe_file_record(role, path, row_count)
        if frame is not None:
            record["schema_sha256"] = schema_hash(frame)
            record["field_count"] = int(len(frame.columns))
        files.append(record)
    provenance = {
        "protocol_version": PROTOCOL_VERSION,
        "inputs": files,
        "lineage": sanitize_for_safe_manifest(metadata),
        "duplicate_forensics": sanitize_for_safe_manifest(forensic_provenance),
        "split_map_schema_sha256": schema_hash(raw_split),
        "split_map_field_count": int(len(raw_split.columns)),
    }

    return CohortFlowResult(
        summary=summary,
        stages=stage_frame,
        target_splits=split_output,
        label_multiplicity=label_multiplicity,
        subject_multiplicity=subject_multiplicity,
        batch_reconciliation=embedding_result.reconciliation,
        invariants=report.payload(),
        provenance=provenance,
        restricted_reconciliation=restricted_reconciliation,
    )


def write_cohort_flow_outputs(
    result: CohortFlowResult,
    output_dir: Path,
    restricted_reconciliation_csv: Path | None = None,
) -> None:
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite cohort-flow output directory: {output_dir}")
    if restricted_reconciliation_csv is not None:
        destination = require_restricted_destination(restricted_reconciliation_csv)
        restricted_candidates = [
            destination,
            *(destination.parent / f"jdim_target_cohort_{target}.csv" for target in TARGET_NAMES),
        ]
        if any(path.exists() for path in restricted_candidates):
            raise FileExistsError("refusing to overwrite restricted cohort-flow outputs")
    output_dir.mkdir(parents=True)
    write_json(output_dir / "cohort_flow_invariants.json", result.invariants)
    if result.invariants["status"] != "ok":
        raise Tier1BlockedError(BLOCKED_LINEAGE, "one or more cohort-flow invariants failed")
    validate_cohort_output_generation(result)
    write_json(output_dir / "cohort_flow_summary.json", result.summary)
    write_safe_csv(output_dir / "cohort_flow_stages.csv", result.stages, "cohort stages")
    write_safe_csv(output_dir / "cohort_flow_target_splits.csv", result.target_splits, "target split counts")
    write_safe_csv(
        output_dir / "cohort_flow_label_multiplicity.csv",
        result.label_multiplicity,
        "label multiplicity",
    )
    write_safe_csv(
        output_dir / "cohort_flow_subject_multiplicity.csv",
        result.subject_multiplicity,
        "subject multiplicity",
    )
    write_safe_csv(
        output_dir / "cohort_flow_batch_reconciliation.csv",
        result.batch_reconciliation,
        "batch reconciliation",
    )
    write_json(output_dir / "cohort_flow_provenance.json", result.provenance)
    write_safe_csv(
        output_dir / "supplementary_cohort_flow_table.csv",
        result.stages,
        "supplementary cohort-flow table",
    )
    summary = result.summary
    targets = summary["target_intersections"]
    diagram = (
        "flowchart LR\n"
        f"  A[Official MIMIC-IV-ECHO studies: {summary['source_studies']}] --> "
        f"B[Selected analysis universe: {summary['eligible_studies']}]\n"
        f"  B --> C[Corrected canonical embedding studies: {summary['canonical_universe_study_embeddings']}]\n"
        f"  B --> D[LVOT VTI report-label studies: {targets['lvot_vti']['target_positive_studies']}]\n"
        f"  B --> E[TAPSE report-label studies: {targets['tapse']['target_positive_studies']}]\n"
        f"  C --> F[LVOT VTI final cohort: {targets['lvot_vti']['final_analysis_studies']}]\n"
        f"  D --> F\n"
        f"  C --> G[TAPSE final cohort: {targets['tapse']['final_analysis_studies']}]\n"
        f"  E --> G\n"
        f"  H[Declared legacy outside analysis universe: {summary['declared_legacy_outside_analysis_universe']}] -. excluded .-> F\n"
        "  H -. excluded .-> G\n"
    )
    (output_dir / "cohort_flow_diagram.mmd").write_text(diagram, encoding="utf-8")
    methods_text = (
        f"We reconstructed imaging and structured-label lineage as parallel branches from "
        f"MIMIC-IV-ECHO {summary['mimic_iv_echo_release']}. The official source contained "
        f"{summary['source_studies']:,} studies from {summary['source_subjects']:,} subjects; "
        f"the deterministic selected analysis universe contained {summary['eligible_studies']:,} "
        f"studies from {summary['eligible_subjects']:,} subjects. Historical metadata unions "
        "retained their raw rows, while the 32 hash-pinned provenance-resolved duplicate pairs "
        "were adjudicated to one semantic clip per group. Structured LVOT VTI and TAPSE rows "
        "were parsed using the unchanged target definitions and aggregated by the study median. "
        "Final target-plus-embedding cohorts used the frozen subject-level train, validation, "
        "and test assignment."
    )
    lvot_splits = targets["lvot_vti"]["split_study_counts"]
    tapse_splits = targets["tapse"]["split_study_counts"]
    results_text = (
        f"The corrected canonical embedding universe contained "
        f"{summary['canonical_universe_study_embeddings']:,} studies. The final LVOT VTI cohort "
        f"contained {targets['lvot_vti']['final_analysis_studies']:,} studies "
        f"({lvot_splits['train']:,} train, {lvot_splits['val']:,} validation, "
        f"{lvot_splits['test']:,} test), and the final TAPSE cohort contained "
        f"{targets['tapse']['final_analysis_studies']:,} studies "
        f"({tapse_splits['train']:,} train, {tapse_splits['val']:,} validation, "
        f"{tapse_splits['test']:,} test). No subject overlapped across splits."
    )
    duplicate_disclosure = (
        "During revision-stage provenance review, we identified duplicate manifest rows "
        "affecting one training study. We retained one row per provenance-defined semantic "
        "clip and repeated all affected analyses using the unchanged prespecified protocol. "
        "The correction did not alter rounded primary performance estimates or study conclusions."
    )
    reviewer_text = methods_text + " " + results_text + " " + duplicate_disclosure
    for name, value in (
        ("manuscript_methods_insertion.txt", methods_text),
        ("manuscript_results_insertion.txt", results_text),
        ("reviewer_1_comment_2_insertion.txt", reviewer_text),
        ("duplicate_correction_disclosure.txt", duplicate_disclosure),
    ):
        (output_dir / name).write_text(value + "\n", encoding="utf-8")
    lock_files = sorted(
        path for path in output_dir.iterdir() if path.is_file() and path.name != "cohort_flow_lock.json"
    )
    write_json(
        output_dir / "cohort_flow_lock.json",
        {
            "status": "JDIM_COHORT_FLOW_LOCKED",
            "protocol_version": summary["protocol_version"],
            "target_endpoints": {
                target: {
                    "total": targets[target]["final_analysis_studies"],
                    "split_study_counts": targets[target]["split_study_counts"],
                }
                for target in TARGET_NAMES
            },
            "artifacts": [safe_file_record(path.stem, path) for path in lock_files],
        },
    )
    if restricted_reconciliation_csv is not None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        result.restricted_reconciliation.to_csv(destination, index=False)
        for target in TARGET_NAMES:
            split_column = f"{target}_split"
            if split_column not in result.restricted_reconciliation.columns:
                continue
            cohort = result.restricted_reconciliation.loc[
                result.restricted_reconciliation[split_column].isin(VALID_SPLITS),
                [
                    "study_id",
                    "subject_id",
                    split_column,
                    f"{target}_target_value",
                    f"{target}_n_target_rows",
                ],
            ].rename(
                columns={
                    split_column: "split",
                    f"{target}_target_value": "target_value",
                    f"{target}_n_target_rows": "n_target_rows",
                }
            )
            cohort.to_csv(destination.parent / f"jdim_target_cohort_{target}.csv", index=False)


def validate_cohort_output_generation(result: CohortFlowResult) -> dict[str, Any]:
    """Traverse every output policy without creating an output directory."""

    if result.invariants["status"] != "ok":
        raise Tier1BlockedError(BLOCKED_LINEAGE, "one or more cohort-flow invariants failed")
    for label, frame in (
        ("cohort stages", result.stages),
        ("target split counts", result.target_splits),
        ("label multiplicity", result.label_multiplicity),
        ("subject multiplicity", result.subject_multiplicity),
        ("batch reconciliation", result.batch_reconciliation),
    ):
        assert_export_safe_frame(frame, label)
    required_restricted = {"study_id", "subject_id"}
    if not required_restricted.issubset(result.restricted_reconciliation.columns):
        raise Tier1BlockedError(
            BLOCKED_LINEAGE,
            "restricted cohort reconciliation lacks study and subject identity",
        )
    return {
        "status": "ok",
        "mode": "no_write_preflight",
        "output_generation_validated": True,
        "invariant_checks": int(result.invariants["n_checks"]),
        "approved_duplicate_groups": int(
            result.summary["metadata_duplicate_adjudication"]["expected_records"][
                "provenance_resolved_duplicate_rows"
            ]
        ),
        "declared_legacy_outside_analysis_universe": int(
            result.summary["declared_legacy_outside_analysis_universe"]
        ),
        "target_endpoints": {
            target: {
                "total": result.summary["target_intersections"][target][
                    "final_analysis_studies"
                ],
                "split_study_counts": result.summary["target_intersections"][target][
                    "split_study_counts"
                ],
            }
            for target in TARGET_NAMES
        },
        "row_level_output_written": False,
        "aggregate_output_written": False,
    }
