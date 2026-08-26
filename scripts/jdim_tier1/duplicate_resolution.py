"""Bind metadata-resolved duplicate provenance to downstream correction decisions."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from .duplicate_metadata import (
    DUPLICATE_SEMANTICS_RESOLVED_FROM_PROVENANCE,
    TRUE_DUPLICATE_EXPECTED_ROWS,
)
from .duplicate_recovery import DUPLICATE_SEMANTICS_RESOLVED
from .safety import (
    BLOCKED_LINEAGE,
    Tier1BlockedError,
    require_columns,
    require_restricted_destination,
    sha256_file,
    write_json,
)


RESOLVED_DECISION_STATUS = "ok"


@dataclass
class ResolvedDuplicateDecisions:
    rows: pd.DataFrame
    summary: dict[str, Any]


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise Tier1BlockedError(BLOCKED_LINEAGE, f"invalid {label}: {exc}") from exc
    if not isinstance(payload, dict):
        raise Tier1BlockedError(BLOCKED_LINEAGE, f"{label} must be a JSON object")
    return payload


def _expected_hash(payload: Mapping[str, Any], section: str, filename: str) -> str:
    raw = payload.get(section)
    if not isinstance(raw, Mapping):
        return ""
    return str(raw.get(filename, "")).strip()


def _semantic_identity(token: str, metadata_packet_hash: str) -> str:
    encoded = json.dumps(
        ["metadata_provenance_semantic_clip_v1", token, metadata_packet_hash],
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def resolve_duplicate_decisions(
    prior_rows_csv: Path,
    prior_summary_json: Path,
    metadata_groups_csv: Path,
    metadata_summary_json: Path,
) -> ResolvedDuplicateDecisions:
    prior_summary = _load_json(prior_summary_json, "prior duplicate summary")
    metadata_summary = _load_json(metadata_summary_json, "metadata duplicate summary")
    expected_prior = _expected_hash(
        prior_summary, "restricted_artifact_sha256", "duplicate_forensics_rows.csv"
    )
    expected_groups = _expected_hash(
        metadata_summary,
        "restricted_evidence_packet_sha256",
        "metadata_group_classification.csv",
    )
    if not expected_prior or sha256_file(prior_rows_csv) != expected_prior:
        raise Tier1BlockedError(BLOCKED_LINEAGE, "prior duplicate rows do not match provenance")
    if not expected_groups or sha256_file(metadata_groups_csv) != expected_groups:
        raise Tier1BlockedError(BLOCKED_LINEAGE, "metadata group rows do not match provenance")
    if metadata_summary.get("status") != DUPLICATE_SEMANTICS_RESOLVED_FROM_PROVENANCE:
        raise Tier1BlockedError(BLOCKED_LINEAGE, "metadata summary is not provenance-resolved")

    prior = pd.read_csv(prior_rows_csv, dtype=str, keep_default_na=False)
    groups = pd.read_csv(metadata_groups_csv, dtype=str, keep_default_na=False)
    require_columns(
        prior,
        [
            "group_token",
            "classification",
            "_batch",
            "_manifest_row",
            "study_id",
            "subject_id",
            "source_manifest_row_fingerprint_sha256",
            "embedding_vector_sha256",
            "embedding_idx",
        ],
        "prior duplicate rows",
    )
    require_columns(
        groups,
        [
            "group_token",
            "classification",
            "first_duplicate_stage",
            "semantic_identity_equal_all_stages",
            "distinct_window_metadata_present",
            "historical_vectors_exact_equal",
            "one_source_path",
            "one_processed_path",
        ],
        "metadata duplicate groups",
    )
    if groups["group_token"].duplicated().any():
        raise Tier1BlockedError(BLOCKED_LINEAGE, "metadata group tokens are not unique")
    if set(prior["group_token"]) != set(groups["group_token"]):
        raise Tier1BlockedError(BLOCKED_LINEAGE, "prior and metadata group-token sets differ")
    if not prior.groupby("group_token").size().eq(2).all():
        raise Tier1BlockedError(BLOCKED_LINEAGE, "resolved groups must contain exactly two rows")

    truthy = {"1", "true", "t", "yes", "y"}
    expected_conditions = (
        groups["classification"].eq(TRUE_DUPLICATE_EXPECTED_ROWS)
        & groups["first_duplicate_stage"].eq("expected_records")
        & groups["semantic_identity_equal_all_stages"].str.lower().isin(truthy)
        & ~groups["distinct_window_metadata_present"].str.lower().isin(truthy)
        & groups["historical_vectors_exact_equal"].str.lower().isin(truthy)
        & groups["one_source_path"].str.lower().isin(truthy)
        & groups["one_processed_path"].str.lower().isin(truthy)
    )
    if not expected_conditions.all():
        raise Tier1BlockedError(
            BLOCKED_LINEAGE,
            "one or more metadata groups fail the true-duplicate provenance standard",
        )

    packet_hash = expected_groups
    resolved = prior.copy()
    resolved["classification"] = TRUE_DUPLICATE_EXPECTED_ROWS
    resolved["classification_reason"] = (
        "multiplicity first appears in expected records; all semantic manifest fields are "
        "single-valued, no distinct window metadata exists, and historical vectors are exact"
    )
    resolved["semantic_clip_identity_sha256"] = resolved["group_token"].map(
        lambda token: _semantic_identity(str(token), packet_hash)
    )
    resolved["resolution_evidence_sha256"] = packet_hash
    resolved["dedup_keep_candidate"] = False
    for _, group in resolved.groupby("group_token", sort=True):
        ordered = group.sort_values(
            ["embedding_vector_sha256", "_batch", "_manifest_row", "embedding_idx"],
            kind="mergesort",
        )
        resolved.loc[ordered.index[0], "dedup_keep_candidate"] = True

    output_summary = dict(prior_summary)
    output_summary.update(
        {
            "status": RESOLVED_DECISION_STATUS,
            "resolution_status": DUPLICATE_SEMANTICS_RESOLVED_FROM_PROVENANCE,
            "n_ambiguous_groups": 0,
            "n_groups_with_valid_dedup_rule": int(groups.shape[0]),
            "classification_counts": {
                TRUE_DUPLICATE_EXPECTED_ROWS: int(groups.shape[0])
            },
            "deduplication_rule": (
                "within each provenance-resolved expected-row pair, retain the deterministic "
                "first historical manifest row; exact vectors make representative choice "
                "target- and performance-independent"
            ),
            "metadata_resolution": {
                "metadata_summary_sha256": sha256_file(metadata_summary_json),
                "metadata_group_classification_sha256": expected_groups,
                "classification": TRUE_DUPLICATE_EXPECTED_ROWS,
                "first_duplicate_stage": "expected_records",
                "n_groups": int(groups.shape[0]),
            },
        }
    )
    output_summary.pop("restricted_artifact_sha256", None)
    return ResolvedDuplicateDecisions(rows=resolved, summary=output_summary)


def resolve_duplicate_decisions_from_recovery(
    prior_rows_csv: Path,
    prior_summary_json: Path,
    metadata_groups_csv: Path,
    metadata_summary_json: Path,
    recovery_rows_csv: Path,
    recovery_summary_json: Path,
) -> ResolvedDuplicateDecisions:
    result = resolve_duplicate_decisions(
        prior_rows_csv,
        prior_summary_json,
        metadata_groups_csv,
        metadata_summary_json,
    )
    recovery_summary = _load_json(recovery_summary_json, "duplicate recovery summary")
    expected_rows = _expected_hash(
        recovery_summary,
        "restricted_evidence_packet_sha256",
        "duplicate_recovery_rows.csv",
    )
    if not expected_rows or sha256_file(recovery_rows_csv) != expected_rows:
        raise Tier1BlockedError(BLOCKED_LINEAGE, "recovery rows do not match provenance")
    recovery = pd.read_csv(recovery_rows_csv, dtype=str, keep_default_na=False)
    require_columns(
        recovery,
        ["group_token", "status", "processed_array_sha256"],
        "duplicate recovery rows",
    )
    if set(recovery["group_token"]) != set(result.rows["group_token"]):
        raise Tier1BlockedError(BLOCKED_LINEAGE, "recovery and decision group-token sets differ")
    if not recovery["status"].eq(DUPLICATE_SEMANTICS_RESOLVED).all():
        raise Tier1BlockedError(BLOCKED_LINEAGE, "one or more recovered groups remain unresolved")
    processed_by_group: dict[str, str] = {}
    for token, group in recovery.groupby("group_token", sort=True):
        hashes = {
            str(value).strip()
            for value in group["processed_array_sha256"]
            if str(value).strip()
        }
        if len(hashes) != 1:
            raise Tier1BlockedError(
                BLOCKED_LINEAGE,
                "a recovered group lacks one processed-array identity",
            )
        processed_by_group[str(token)] = next(iter(hashes))
    result.rows["processed_array_sha256"] = result.rows["group_token"].map(
        processed_by_group
    )
    result.summary["recovery_resolution"] = {
        "recovery_summary_sha256": sha256_file(recovery_summary_json),
        "recovery_rows_sha256": expected_rows,
        "n_groups": int(len(processed_by_group)),
        "status": DUPLICATE_SEMANTICS_RESOLVED,
    }
    return result


def write_resolved_duplicate_decisions(
    result: ResolvedDuplicateDecisions,
    output_root: Path,
) -> None:
    root = require_restricted_destination(output_root)
    if root.exists():
        raise FileExistsError(f"refusing to overwrite duplicate-resolution root: {root}")
    restricted = root / "restricted"
    safe = root / "aggregate_safe"
    restricted.mkdir(parents=True)
    safe.mkdir(parents=True)
    rows_path = restricted / "duplicate_forensics_rows.csv"
    result.rows.to_csv(rows_path, index=False)
    summary = dict(result.summary)
    summary["restricted_artifact_sha256"] = {
        "duplicate_forensics_rows.csv": sha256_file(rows_path)
    }
    write_json(safe / "duplicate_forensics_summary.json", summary)
