"""Restricted manual input-content audit sampling and aggregation."""
from __future__ import annotations

import hashlib
import hmac
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from .safety import (
    BLOCKED_LINEAGE,
    Tier1BlockedError,
    assert_export_safe_frame,
    require_columns,
    require_restricted_destination,
    safe_file_record,
    sha256_file,
    sha256_json,
    write_json,
    write_safe_csv,
)


PRESENCE_VALUES = {"yes", "no", "uncertain", "not_assessable"}
CONTENT_TYPES = {
    "2d_b_mode",
    "color_doppler",
    "pulsed_wave_spectral_doppler",
    "continuous_wave_spectral_doppler",
    "tissue_doppler",
    "m_mode",
    "mixed",
    "other",
    "uncertain",
    "not_assessable",
}
READER_CONFIDENCE = {"high", "moderate", "low", "not_assessable"}
SAMPLE_TOKEN_COLUMN = "sample_manifest_token"
MANUAL_AUDIT_INPUT_HASH_ROLES = (
    "study_annotations",
    "clip_annotations",
    "audit_linkage",
    "sampling_design",
    "second_reader_manifest",
    "clip_roster",
    "completed_adjudication",
)
POST_UNBLINDING_SHARED_HASH_ROLES = (
    "clip_annotations",
    "audit_linkage",
    "sampling_design",
    "clip_roster",
    "completed_adjudication",
)

STUDY_OUTCOMES = [
    "spectral_doppler_present",
    "m_mode_present",
    "caliper_or_trace_present",
    "visible_numeric_value_present",
    "target_specific_label_present",
    "candidate_target_value_present",
]

CLIP_PRESENCE_FIELDS = [
    "waveform_or_tracing",
    "calipers",
    "contour_or_measurement_trace",
    "visible_text",
    "visible_numeric_value",
    "visible_unit",
    "visible_measurement_name",
    "lvot_vti_specific_label",
    "tapse_specific_label",
    "candidate_target_value_present",
    "source_frame_visibility",
    "processed_input_visibility",
    "reconstruction_success",
]

STUDY_TEMPLATE_COLUMNS = [
    "audit_id",
    SAMPLE_TOKEN_COLUMN,
    "reader_id",
    "reader_role",
    *STUDY_OUTCOMES,
    "reader_confidence",
    "restricted_notes",
]

CLIP_TEMPLATE_COLUMNS = [
    "audit_id",
    "clip_audit_id",
    SAMPLE_TOKEN_COLUMN,
    "reader_id",
    "reader_role",
    "acquisition_content_type",
    *CLIP_PRESENCE_FIELDS,
    "candidate_target_value",
    "visible_unit_text",
    "visible_measurement_name_text",
    "display_precision",
    "reader_confidence",
    "restricted_notes",
]


@dataclass
class AuditSampleResult:
    linkage: pd.DataFrame
    clip_roster: pd.DataFrame
    reader_manifest: pd.DataFrame
    second_reader_manifest: pd.DataFrame
    study_template: pd.DataFrame
    clip_template: pd.DataFrame
    sampling_design: pd.DataFrame
    safe_summary: dict[str, Any]
    config_hash: str
    technical_pilot: bool = False


@dataclass
class AuditAggregateResult:
    study_summary: pd.DataFrame
    clip_summary: pd.DataFrame
    agreement: pd.DataFrame
    summary: dict[str, Any]


@dataclass
class PostUnblindingMatchResult:
    restricted_study_matches: pd.DataFrame
    safe_summary: pd.DataFrame
    provenance: dict[str, Any]


def canonical_clip_source_row_sha256(row: Mapping[str, Any] | pd.Series) -> str:
    """Hash the canonical manifest row used to mint one opaque clip ID."""

    values = row.to_dict() if isinstance(row, pd.Series) else dict(row)
    excluded = {
        "audit_id",
        "clip_audit_id",
        "source_manifest_row_sha256",
        "_study",
        "_subject",
    }
    source_payload = {
        column: "" if pd.isna(values.get(column)) else str(values.get(column))
        for column in sorted(column for column in values if column not in excluded)
    }
    return sha256_json(source_payload)


def _require_exact_sample_token(frame: pd.DataFrame, expected: str, label: str) -> None:
    values = frame[SAMPLE_TOKEN_COLUMN].fillna("").astype(str).str.strip()
    if values.empty or values.eq("").any() or not values.eq(expected).all():
        raise Tier1BlockedError(
            BLOCKED_LINEAGE,
            f"{label} does not match the locked sample manifest token",
        )


def _validated_input_hashes(
    hashes: Mapping[str, str] | None,
    required_roles: Sequence[str],
    label: str,
) -> dict[str, str]:
    payload = dict(hashes or {})
    missing = sorted(set(required_roles) - set(payload))
    if missing:
        raise Tier1BlockedError(BLOCKED_LINEAGE, f"{label} lacks required hash roles: {missing}")
    for role in required_roles:
        value = str(payload[role]).strip().lower()
        if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
            raise Tier1BlockedError(BLOCKED_LINEAGE, f"{label} has an invalid SHA-256 for {role}")
        payload[role] = value
    return payload


def _validate_manual_audit_completion_for_post_unblinding(
    completion: Mapping[str, Any],
    config_hash: str,
    sample_token: str,
    input_hashes: Mapping[str, str] | None,
) -> dict[str, str]:
    if (
        completion.get("schema_version") != "jdim-manual-audit-completion-v1"
        or completion.get("status") != "MANUAL_AUDIT_COMPLETE"
        or completion.get("annotation_roster_validated") is not True
        or completion.get("independent_second_reads_validated") is not True
        or completion.get("completed_adjudication_validated") is not True
    ):
        raise Tier1BlockedError(
            BLOCKED_LINEAGE,
            "post-unblinding analysis requires a validated manual-audit completion certificate",
        )
    if completion.get("configuration_sha256") != config_hash:
        raise Tier1BlockedError(BLOCKED_LINEAGE, "manual-audit completion configuration hash changed")
    if completion.get("sample_manifest_token") != sample_token:
        raise Tier1BlockedError(BLOCKED_LINEAGE, "manual-audit completion sample token changed")
    certified = _validated_input_hashes(
        completion.get("input_file_hashes"),
        MANUAL_AUDIT_INPUT_HASH_ROLES,
        "manual-audit completion certificate",
    )
    current = _validated_input_hashes(
        input_hashes,
        POST_UNBLINDING_SHARED_HASH_ROLES,
        "post-unblinding inputs",
    )
    mismatched = sorted(
        role for role in POST_UNBLINDING_SHARED_HASH_ROLES if current[role] != certified[role]
    )
    if mismatched:
        raise Tier1BlockedError(
            BLOCKED_LINEAGE,
            f"post-unblinding inputs do not match the completed blinded audit: {mismatched}",
        )
    return current


def load_audit_config(path: Path) -> tuple[dict[str, Any], str]:
    if not path.exists():
        raise FileNotFoundError(path)
    config = json.loads(path.read_text(encoding="utf-8"))
    validate_audit_config(config)
    return config, sha256_file(path)


def validate_audit_config(config: Mapping[str, Any]) -> None:
    required = {
        "protocol_version",
        "target_cohorts",
        "intended_sample_size_per_target",
        "seed",
        "split_allocation",
        "overlap_deduplication_rule",
        "canonical_clip_roster",
        "primary_study_level_outcomes",
        "secondary_clip_level_outcomes",
        "reader_blinding",
        "second_reader_fraction",
        "adjudication",
        "post_unblinding_value_match",
        "restricted_output_root_required",
        "clip_level_estimand",
        "safe_aggregate_output_schema",
        "technical_pilot",
        "escalation_triggers",
    }
    missing = sorted(required - set(config))
    if missing:
        raise ValueError(f"Audit config missing required keys: {missing}")
    if config["split_allocation"].get("method") != "proportional_largest_remainder":
        raise ValueError("Default split allocation must be proportional_largest_remainder")
    if config["overlap_deduplication_rule"] != "one_physical_review_per_unique_study":
        raise ValueError("Audit overlap rule must deduplicate physical study review")
    if int(config["seed"]) != 20260824:
        raise ValueError("Audit seed must remain 20260824 for protocol v1")
    size = int(config["intended_sample_size_per_target"])
    if size <= 0:
        raise ValueError("intended_sample_size_per_target must be positive")
    fraction = float(config["second_reader_fraction"])
    if not 0 <= fraction <= 1:
        raise ValueError("second_reader_fraction must be in [0, 1]")
    if not bool(config["restricted_output_root_required"]):
        raise ValueError("restricted_output_root_required must remain true")
    if not bool(config["canonical_clip_roster"].get("all_selected_study_clips_required")) or not bool(
        config["canonical_clip_roster"].get("locked_into_sample_manifest_token")
    ):
        raise ValueError("Canonical clip roster must include all selected clips and be sample-locked")
    if not bool(config["adjudication"].get("completed_queue_required_before_aggregation")):
        raise ValueError("Completed adjudication queue must be required before aggregation")
    if config["clip_level_estimand"] != "unweighted_sampled_clip_composition":
        raise ValueError("Clip-level estimand must be explicitly unweighted sampled-clip composition")
    if set(config["primary_study_level_outcomes"]) != set(STUDY_OUTCOMES):
        raise ValueError("Primary study-level outcome schema differs from protocol v1")
    prohibited = set(config["reader_blinding"].get("prohibited_fields", []))
    required_blinding = {"target_value", "prediction", "residual", "filename", "subject_id", "study_id", "split"}
    if not required_blinding.issubset(prohibited):
        raise ValueError("Reader blinding does not prohibit all prespecified fields")
    if not bool(config["post_unblinding_value_match"].get("reader_target_values_prohibited")):
        raise ValueError("Post-unblinding matching must prohibit reader access to target values")
    allowed_pilot = set(config["technical_pilot"].get("allowed_fields", []))
    if not bool(config["technical_pilot"].get("clinical_content_labels_prohibited")):
        raise ValueError("Technical pilot must prohibit clinical content labels")
    if not allowed_pilot.issubset({"audit_id", "review_order", "reconstruction_success", "review_minutes"}):
        raise ValueError("Technical pilot contains nontechnical annotation fields")


def _sample_manifest_token(
    linkage: pd.DataFrame,
    design: pd.DataFrame,
    clip_roster: pd.DataFrame,
) -> str:
    linkage_columns = sorted(column for column in linkage.columns if column != "_review_rank")
    design_columns = sorted(design.columns)
    clip_columns = sorted(clip_roster.columns)
    if "audit_id" not in linkage_columns:
        raise ValueError("audit linkage lacks audit_id for sample locking")
    if not {"target", "split"}.issubset(design_columns):
        raise ValueError("sampling design lacks target/split for sample locking")
    if not {"audit_id", "clip_audit_id"}.issubset(clip_columns):
        raise ValueError("canonical clip roster lacks opaque study/clip identifiers")
    linkage_records = (
        linkage[linkage_columns]
        .fillna("")
        .astype(str)
        .sort_values(["audit_id", *[column for column in linkage_columns if column != "audit_id"]])
        .to_dict(orient="records")
    )
    design_records = (
        design[design_columns]
        .fillna("")
        .astype(str)
        .sort_values(["target", "split", *[column for column in design_columns if column not in {"target", "split"}]])
        .to_dict(orient="records")
    )
    clip_records = (
        clip_roster[clip_columns]
        .fillna("")
        .astype(str)
        .sort_values(
            [
                "audit_id",
                "clip_audit_id",
                *[
                    column
                    for column in clip_columns
                    if column not in {"audit_id", "clip_audit_id"}
                ],
            ]
        )
        .to_dict(orient="records")
    )
    return sha256_json(
        {
            "linkage": linkage_records,
            "sampling_design": design_records,
            "canonical_clip_roster": clip_records,
        }
    )


def largest_remainder_allocation(
    counts: Mapping[str, int],
    requested: int,
    order: Sequence[str] = ("train", "val", "test"),
) -> dict[str, int]:
    clean = {split: int(counts.get(split, 0)) for split in order}
    if any(value < 0 for value in clean.values()):
        raise ValueError("Allocation counts cannot be negative")
    total_available = sum(clean.values())
    total = min(int(requested), total_available)
    if total < 0:
        raise ValueError("Requested sample size cannot be negative")
    if total_available == 0:
        return {split: 0 for split in order}
    exact = {split: total * clean[split] / total_available for split in order}
    allocation = {split: min(clean[split], int(math.floor(exact[split]))) for split in order}
    remaining = total - sum(allocation.values())
    ranked = sorted(order, key=lambda split: (-(exact[split] - math.floor(exact[split])), order.index(split)))
    while remaining:
        progressed = False
        for split in ranked:
            if allocation[split] < clean[split]:
                allocation[split] += 1
                remaining -= 1
                progressed = True
                if remaining == 0:
                    break
        if not progressed:
            raise RuntimeError("Largest-remainder allocation could not place all samples")
    return allocation


def validate_allocation_override(
    override: Mapping[str, Any] | None,
    targets: Sequence[str],
    counts: Mapping[str, Mapping[str, int]],
) -> dict[str, dict[str, int]] | None:
    if override is None:
        return None
    if not bool(override.get("author_approved")):
        raise ValueError("Allocation override must include author_approved=true")
    allocations = override.get("allocations")
    if not isinstance(allocations, Mapping):
        raise ValueError("Allocation override missing allocations mapping")
    parsed: dict[str, dict[str, int]] = {}
    for target in targets:
        if target not in allocations:
            raise ValueError(f"Allocation override missing target {target}")
        parsed[target] = {}
        for split in ("train", "val", "test"):
            value = int(allocations[target].get(split, 0))
            if value < 0 or value > int(counts[target].get(split, 0)):
                raise ValueError(f"Allocation override for {target}/{split} exceeds available cohort")
            parsed[target][split] = value
    return parsed


def _rank(seed: int, *parts: str) -> str:
    payload = "::".join([str(seed), *map(str, parts)]).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _opaque_id(secret: bytes, protocol: str, study_id: str) -> str:
    digest = hmac.new(secret, f"{protocol}::{study_id}".encode("utf-8"), hashlib.sha256).hexdigest()
    return f"A{digest[:19].upper()}"


def _normalize_cohort(frame: pd.DataFrame, target: str) -> pd.DataFrame:
    require_columns(frame, ["study_id", "subject_id", "split"], f"{target} audit cohort")
    out = frame.copy()
    out["study_id"] = out["study_id"].astype(str)
    out["subject_id"] = out["subject_id"].astype(str)
    out["split"] = out["split"].astype(str).str.lower()
    invalid = sorted(set(out["split"]) - {"train", "val", "test"})
    if invalid:
        raise Tier1BlockedError(BLOCKED_LINEAGE, f"{target} audit cohort has invalid splits: {invalid}")
    conflicts = out.groupby("study_id").agg(subjects=("subject_id", "nunique"), splits=("split", "nunique"))
    if int((conflicts["subjects"] > 1).sum()):
        raise Tier1BlockedError(BLOCKED_LINEAGE, f"{target} audit cohort has conflicting subject assignments")
    if int((conflicts["splits"] > 1).sum()):
        raise Tier1BlockedError(BLOCKED_LINEAGE, f"{target} audit cohort has conflicting split assignments")
    return out.drop_duplicates("study_id", keep="first").reset_index(drop=True)


def _canonical_clip_roster(
    canonical_clips: pd.DataFrame,
    linkage: pd.DataFrame,
    opaque_id_key: bytes,
    protocol: str,
) -> pd.DataFrame:
    require_columns(canonical_clips, ["study_id", "subject_id"], "canonical clip manifest")
    clips = canonical_clips.copy()
    if "write_ok" in clips.columns:
        values = clips["write_ok"]
        if pd.api.types.is_bool_dtype(values):
            keep = values.fillna(False).astype(bool)
        else:
            keep = values.astype(str).str.strip().str.lower().isin({"true", "1", "yes", "y"})
        clips = clips.loc[keep].copy()
    clips = clips.reset_index(drop=True)
    if clips.empty:
        raise Tier1BlockedError(BLOCKED_LINEAGE, "canonical clip manifest has no successful rows")
    clips["study_id"] = clips["study_id"].astype(str)
    clips["subject_id"] = clips["subject_id"].astype(str)
    clips["source_manifest_row"] = np.arange(len(clips), dtype=int)
    stable_columns = [
        column
        for column in ("canonical_clip_id", "embedding_idx", "source_embedding_idx")
        if column in clips.columns
    ]
    if not stable_columns:
        raise Tier1BlockedError(
            BLOCKED_LINEAGE,
            "canonical clip manifest lacks canonical_clip_id or embedding index",
        )

    linkage_pairs = linkage[["audit_id", "study_id", "subject_id"]].copy()
    selected = clips.merge(
        linkage_pairs,
        on=["study_id", "subject_id"],
        how="inner",
        validate="many_to_one",
    )
    selected_studies = set(selected["study_id"])
    expected_studies = set(linkage["study_id"].astype(str))
    if selected_studies != expected_studies:
        raise Tier1BlockedError(
            BLOCKED_LINEAGE,
            "one or more sampled studies lack a canonical clip roster or have subject mismatch",
        )
    selected = selected.sort_values(
        ["audit_id", *stable_columns, "source_manifest_row"],
        kind="mergesort",
    ).reset_index(drop=True)

    row_hashes: list[str] = []
    clip_ids: list[str] = []
    for row in selected.to_dict(orient="records"):
        row_hash = canonical_clip_source_row_sha256(row)
        clip_digest = hmac.new(
            opaque_id_key,
            f"{protocol}::{row['audit_id']}::{row_hash}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        row_hashes.append(row_hash)
        clip_ids.append(f"C{clip_digest[:19].upper()}")
    selected["source_manifest_row_sha256"] = row_hashes
    selected["clip_audit_id"] = clip_ids
    if selected["clip_audit_id"].duplicated().any():
        raise Tier1BlockedError(BLOCKED_LINEAGE, "opaque canonical clip IDs are not unique")
    return selected[["audit_id", "clip_audit_id", *[column for column in selected.columns if column not in {"audit_id", "clip_audit_id"}]]]


def build_audit_sample(
    cohorts: Mapping[str, pd.DataFrame],
    canonical_clips: pd.DataFrame,
    config: Mapping[str, Any],
    config_hash: str,
    opaque_id_key: bytes,
    sample_size_per_target: int | None = None,
    allocation_override: Mapping[str, Any] | None = None,
    technical_pilot: bool = False,
    pilot_n_per_target: int | None = None,
) -> AuditSampleResult:
    validate_audit_config(config)
    targets = list(config["target_cohorts"])
    if set(cohorts) != set(targets):
        raise ValueError(f"Cohorts must exactly match configured targets: {targets}")
    normalized = {target: _normalize_cohort(cohorts[target], target) for target in targets}

    all_pairs = pd.concat(
        [frame[["study_id", "subject_id"]].assign(target=target) for target, frame in normalized.items()],
        ignore_index=True,
    )
    subject_conflicts = all_pairs.groupby("study_id")["subject_id"].nunique()
    if int((subject_conflicts > 1).sum()):
        raise Tier1BlockedError(BLOCKED_LINEAGE, "overlapping target cohorts conflict on study-to-subject assignment")

    seed = int(config["seed"])
    requested = int(sample_size_per_target or config["intended_sample_size_per_target"])
    if technical_pilot:
        requested = int(pilot_n_per_target or config["technical_pilot"]["default_studies_per_target"])
    counts = {
        target: {split: int((frame["split"] == split).sum()) for split in ("train", "val", "test")}
        for target, frame in normalized.items()
    }
    approved_override = validate_allocation_override(allocation_override, targets, counts)

    selected_rows: list[pd.DataFrame] = []
    design_rows: list[dict[str, Any]] = []
    for target, frame in normalized.items():
        allocation = (
            approved_override[target]
            if approved_override is not None
            else largest_remainder_allocation(counts[target], requested)
        )
        for split in ("train", "val", "test"):
            stratum = frame[frame["split"] == split].copy()
            stratum["_rank"] = stratum["study_id"].map(lambda study: _rank(seed, target, split, study))
            stratum = stratum.sort_values(["_rank", "study_id"]).head(allocation[split]).copy()
            stratum["target"] = target
            selected_rows.append(stratum)
            sample_n = int(len(stratum))
            source_n = int(counts[target][split])
            design_rows.append(
                {
                    "target": target,
                    "split": split,
                    "source_n": source_n,
                    "sample_n": sample_n,
                    "design_weight": float(source_n / sample_n) if sample_n else np.nan,
                    "allocation_method": (
                        "author_approved_override"
                        if approved_override is not None
                        else "proportional_largest_remainder"
                    ),
                }
            )

    selected = pd.concat(selected_rows, ignore_index=True, sort=False)
    if selected.empty:
        raise Tier1BlockedError(BLOCKED_LINEAGE, "audit sampling selected zero studies")
    protocol = str(config["protocol_version"])
    selected["audit_id"] = selected["study_id"].map(lambda study: _opaque_id(opaque_id_key, protocol, study))
    selected["target_stratum"] = selected["target"] + ":" + selected["split"]

    linkage_rows: list[dict[str, Any]] = []
    for study_id, group in selected.groupby("study_id", sort=True):
        subjects = sorted(set(group["subject_id"]))
        if len(subjects) != 1:
            raise Tier1BlockedError(BLOCKED_LINEAGE, "sampled study has conflicting subjects")
        row: dict[str, Any] = {
            "audit_id": group["audit_id"].iloc[0],
            "study_id": study_id,
            "subject_id": subjects[0],
            "target_membership": ";".join(sorted(set(group["target"]))),
            "target_strata": ";".join(sorted(set(group["target_stratum"]))),
        }
        for target, target_group in group.groupby("target", sort=True):
            for source_column in ("target_value", "n_target_rows"):
                if source_column not in target_group.columns:
                    continue
                values = target_group[source_column].dropna().unique()
                if len(values) == 1:
                    row[f"{target}_{source_column}"] = values[0]
        for column in group.columns:
            if column in {
                "study_id",
                "subject_id",
                "split",
                "target",
                "target_stratum",
                "target_value",
                "n_target_rows",
                "audit_id",
                "_rank",
            }:
                continue
            nonnull = group[column].dropna().astype(str).unique()
            if len(nonnull) == 1:
                row[column] = nonnull[0]
        linkage_rows.append(row)
    linkage = pd.DataFrame(linkage_rows)
    linkage["_review_rank"] = linkage["audit_id"].map(lambda audit_id: _rank(seed, "review_order", audit_id))
    linkage = linkage.sort_values(["_review_rank", "audit_id"]).reset_index(drop=True)
    linkage["review_order"] = np.arange(1, len(linkage) + 1)

    reader_manifest = linkage[["audit_id", "review_order"]].copy()
    second_reader_n = int(math.ceil(len(reader_manifest) * float(config["second_reader_fraction"])))
    second_reader = reader_manifest.copy()
    second_reader["_second_rank"] = second_reader["audit_id"].map(lambda value: _rank(seed, "second_reader", value))
    second_reader_manifest = (
        second_reader.sort_values(["_second_rank", "audit_id"])
        .head(second_reader_n)[["audit_id", "review_order"]]
        .reset_index(drop=True)
    )

    design = pd.DataFrame(design_rows)
    clip_roster = _canonical_clip_roster(
        canonical_clips,
        linkage,
        opaque_id_key,
        protocol,
    )
    sample_token = _sample_manifest_token(linkage, design, clip_roster)
    if technical_pilot:
        study_template = reader_manifest.copy()
        study_template["reconstruction_success"] = ""
        study_template["review_minutes"] = ""
        clip_template = pd.DataFrame(columns=config["technical_pilot"]["allowed_fields"])
    else:
        reader_manifest[SAMPLE_TOKEN_COLUMN] = sample_token
        second_reader_manifest[SAMPLE_TOKEN_COLUMN] = sample_token
        primary = reader_manifest[["audit_id", SAMPLE_TOKEN_COLUMN]].copy()
        primary["reader_id"] = ""
        primary["reader_role"] = "primary"
        secondary = second_reader_manifest[["audit_id", SAMPLE_TOKEN_COLUMN]].copy()
        secondary["reader_id"] = ""
        secondary["reader_role"] = "secondary"
        study_template = pd.concat([primary, secondary], ignore_index=True).reindex(
            columns=STUDY_TEMPLATE_COLUMNS,
            fill_value="",
        )
        primary_clips = clip_roster[["audit_id", "clip_audit_id"]].copy()
        primary_clips[SAMPLE_TOKEN_COLUMN] = sample_token
        primary_clips["reader_id"] = ""
        primary_clips["reader_role"] = "primary"
        secondary_ids = set(second_reader_manifest["audit_id"].astype(str))
        secondary_clips = primary_clips[
            primary_clips["audit_id"].astype(str).isin(secondary_ids)
        ].copy()
        secondary_clips["reader_role"] = "secondary"
        clip_template = pd.concat([primary_clips, secondary_clips], ignore_index=True).reindex(
            columns=CLIP_TEMPLATE_COLUMNS,
            fill_value="",
        )

    summary = {
        "protocol_version": protocol,
        "configuration_sha256": config_hash,
        "seed": seed,
        "technical_pilot": bool(technical_pilot),
        "intended_sample_size_per_target": requested,
        "unique_physical_studies_selected": int(linkage["study_id"].nunique()),
        "target_sample_counts": {
            target: int((selected["target"] == target).sum()) for target in targets
        },
        "cross_target_overlap_studies": int(selected.groupby("study_id")["target"].nunique().gt(1).sum()),
        "canonical_clips_in_selected_studies": int(len(clip_roster)),
        "selected_studies_with_canonical_clips": int(clip_roster["audit_id"].nunique()),
        "primary_clip_reads_expected": int(len(clip_roster)),
        "secondary_clip_reads_expected": int(
            clip_roster["audit_id"].astype(str).isin(
                set(second_reader_manifest["audit_id"].astype(str))
            ).sum()
        ),
        "second_reader_studies": int(len(second_reader_manifest)),
        "allocation_method": (
            "author_approved_override"
            if approved_override is not None
            else "proportional_largest_remainder"
        ),
        "excluded_from_prevalence_estimates": bool(technical_pilot),
        "sample_manifest_token": sample_token,
    }
    return AuditSampleResult(
        linkage=linkage.drop(columns=["_review_rank"]),
        clip_roster=clip_roster,
        reader_manifest=reader_manifest,
        second_reader_manifest=second_reader_manifest,
        study_template=study_template,
        clip_template=clip_template,
        sampling_design=design,
        safe_summary=summary,
        config_hash=config_hash,
        technical_pilot=technical_pilot,
    )


def write_audit_sample(
    result: AuditSampleResult,
    restricted_output_root: Path,
    safe_output_dir: Path,
) -> None:
    restricted_root = require_restricted_destination(restricted_output_root)
    if restricted_root.exists() or safe_output_dir.exists():
        raise FileExistsError("refusing to overwrite audit sampling outputs")
    restricted_root.mkdir(parents=True, exist_ok=True)
    result.linkage.to_csv(restricted_root / "audit_linkage.csv", index=False)
    result.clip_roster.to_csv(
        restricted_root / "canonical_clip_roster_restricted.csv",
        index=False,
    )
    result.reader_manifest.to_csv(restricted_root / "reader_manifest.csv", index=False)
    result.second_reader_manifest.to_csv(restricted_root / "second_reader_manifest.csv", index=False)
    result.study_template.to_csv(
        restricted_root
        / ("technical_pilot_template.csv" if result.technical_pilot else "study_annotation_template.csv"),
        index=False,
    )
    result.clip_template.to_csv(restricted_root / "clip_annotation_template.csv", index=False)
    result.sampling_design.to_csv(restricted_root / "sampling_design_restricted.csv", index=False)
    packet_text = {
        "reader_instructions.md": (
            "# Blinded JDIM Input-Content Audit\n\n"
            "Review studies in `reader_manifest.csv` order using only opaque audit IDs. "
            "Do not access report labels, predictions, residuals, split assignments, or source "
            "identifiers. Record only the prespecified study- and clip-level fields. Do not use OCR.\n"
        ),
        "adjudication_guide.md": (
            "# Adjudication Guide\n\n"
            "Independently second-read the assigned subset. Queue every positive, uncertain, or "
            "discordant finding for blinded adjudication. Lock reader forms before any restricted "
            "post-unblinding comparison with report-label values.\n"
        ),
        "secure_save_procedure.md": (
            "# Secure Save Procedure\n\n"
            "Keep all row-level forms and linkage files in this restricted output root. Save only "
            "completed CSV templates with their existing columns and opaque identifiers. Do not "
            "export screenshots, source paths, identifiers, or candidate values to aggregate-safe storage.\n"
        ),
        "source_restoration_action_sheet.md": (
            "# Source Restoration Action Sheet\n\n"
            "Use `source_restoration_manifest.csv` only within restricted storage. Restore the "
            "declared source DICOM and processed input for the locked roster; do not replace a "
            "sampled study with a convenience study. Re-run only source availability and the bounded "
            "technical reconstruction pilot after restoration.\n"
        ),
        "disclosure_safe_aggregation_plan.md": (
            "# Disclosure-Safe Aggregation Plan\n\n"
            "Aggregate only after primary reads, second reads, and adjudication are complete. Export "
            "counts, proportions, confidence intervals, reconstruction-failure rates, agreement "
            "statistics, and configuration hashes. Keep identifiers, paths, target values, predictions, "
            "residuals, transcribed candidate values, and split assignments restricted.\n"
        ),
    }
    for name, content in packet_text.items():
        (restricted_root / name).write_text(content, encoding="utf-8")
    write_json(
        restricted_root / "sampling_provenance_restricted.json",
        {
            **result.safe_summary,
            "restricted_output_root": str(restricted_root),
            "row_level_outputs": [
                "audit_linkage.csv",
                "canonical_clip_roster_restricted.csv",
                "reader_manifest.csv",
                "second_reader_manifest.csv",
                "study_annotation_template.csv" if not result.technical_pilot else "technical_pilot_template.csv",
                "clip_annotation_template.csv",
                "sampling_design_restricted.csv",
                *packet_text,
            ],
        },
    )

    safe_output_dir.mkdir(parents=True, exist_ok=True)
    safe_design = result.sampling_design.drop(columns=["design_weight"]).copy()
    assert_export_safe_frame(safe_design, "audit sampling design summary")
    write_safe_csv(safe_output_dir / "audit_sampling_counts.csv", safe_design, "audit sampling counts")
    write_json(safe_output_dir / "audit_sampling_summary.json", result.safe_summary)
    restricted_artifacts = {
        name: sha256_file(restricted_root / name)
        for name in (
            "audit_linkage.csv",
            "canonical_clip_roster_restricted.csv",
            "reader_manifest.csv",
            "second_reader_manifest.csv",
            "sampling_design_restricted.csv",
            "study_annotation_template.csv"
            if not result.technical_pilot
            else "technical_pilot_template.csv",
            "clip_annotation_template.csv",
            *packet_text,
        )
    }
    write_json(
        safe_output_dir / "audit_roster_lock.json",
        {
            "status": "AUDIT_ROSTER_LOCKED",
            **result.safe_summary,
            "restricted_artifact_sha256": restricted_artifacts,
            "roster_lock_independent_of_source_availability": True,
        },
    )


def wilson_interval(successes: float, total: float, z: float = 1.959963984540054) -> tuple[float, float]:
    if total <= 0:
        return np.nan, np.nan
    proportion = successes / total
    denominator = 1 + z * z / total
    center = (proportion + z * z / (2 * total)) / denominator
    margin = z * math.sqrt(proportion * (1 - proportion) / total + z * z / (4 * total * total)) / denominator
    return max(0.0, center - margin), min(1.0, center + margin)


def weighted_interval(values: np.ndarray, weights: np.ndarray) -> tuple[float, float, float, float]:
    if len(values) == 0 or float(weights.sum()) <= 0:
        return np.nan, np.nan, np.nan, np.nan
    estimate = float(np.average(values, weights=weights))
    n_eff = float(weights.sum() ** 2 / np.square(weights).sum())
    low, high = wilson_interval(estimate * n_eff, n_eff)
    return estimate, low, high, n_eff


def agreement_statistics(left: Sequence[str], right: Sequence[str]) -> dict[str, float | None]:
    pairs = [(str(a), str(b)) for a, b in zip(left, right) if pd.notna(a) and pd.notna(b) and str(a) and str(b)]
    if not pairs:
        return {"n_pairs": 0, "raw_agreement": None, "cohen_kappa": None, "gwet_ac1": None}
    categories = sorted({value for pair in pairs for value in pair})
    n = len(pairs)
    observed = sum(a == b for a, b in pairs) / n
    p_left = {category: sum(a == category for a, _ in pairs) / n for category in categories}
    p_right = {category: sum(b == category for _, b in pairs) / n for category in categories}
    expected_kappa = sum(p_left[category] * p_right[category] for category in categories)
    kappa = (observed - expected_kappa) / (1 - expected_kappa) if expected_kappa < 1 else None
    if len(categories) <= 1:
        ac1 = 1.0 if observed == 1 else None
    else:
        marginal = {category: (p_left[category] + p_right[category]) / 2 for category in categories}
        expected_ac1 = sum(value * (1 - value) for value in marginal.values()) / (len(categories) - 1)
        ac1 = (observed - expected_ac1) / (1 - expected_ac1) if expected_ac1 < 1 else None
    return {
        "n_pairs": n,
        "raw_agreement": float(observed),
        "cohen_kappa": float(kappa) if kappa is not None else None,
        "gwet_ac1": float(ac1) if ac1 is not None else None,
    }


def build_adjudication_queue(
    study_annotations: pd.DataFrame,
    clip_annotations: pd.DataFrame,
) -> pd.DataFrame:
    """Create a restricted queue for positive, uncertain, or discordant reads."""

    rows: list[dict[str, Any]] = []
    levels = [
        ("study", study_annotations, ["audit_id"], STUDY_OUTCOMES),
        ("clip", clip_annotations, ["audit_id", "clip_audit_id"], CLIP_PRESENCE_FIELDS),
    ]
    for level, frame, keys, outcomes in levels:
        if frame.empty:
            continue
        require_columns(frame, keys, f"{level} annotations")
        for key_values, group in frame.groupby(keys, dropna=False, sort=True):
            if not isinstance(key_values, tuple):
                key_values = (key_values,)
            key_payload = dict(zip(keys, key_values))
            for outcome in outcomes:
                if outcome not in group.columns:
                    continue
                values = [
                    value
                    for value in group[outcome].dropna().astype(str).str.lower().tolist()
                    if value
                ]
                unique = sorted(set(values))
                positive_or_uncertain = any(value in {"yes", "uncertain"} for value in unique)
                discordant = len(unique) > 1
                if not positive_or_uncertain and not discordant:
                    continue
                reasons = []
                if positive_or_uncertain:
                    reasons.append("positive_or_uncertain")
                if discordant:
                    reasons.append("reader_disagreement")
                rows.append(
                    {
                        **key_payload,
                        "annotation_level": level,
                        "outcome": outcome,
                        "reader_values": ";".join(unique),
                        "adjudication_reason": ";".join(reasons),
                        "adjudicated_value": "",
                        "adjudicator_id": "",
                        "restricted_notes": "",
                    }
                )
    columns = [
        "audit_id",
        "clip_audit_id",
        "annotation_level",
        "outcome",
        "reader_values",
        "adjudication_reason",
        "adjudicated_value",
        "adjudicator_id",
        "restricted_notes",
    ]
    return pd.DataFrame(rows).reindex(columns=columns)


def write_adjudication_queue(queue: pd.DataFrame, restricted_output_csv: Path) -> None:
    destination = require_restricted_destination(restricted_output_csv)
    if destination.exists():
        raise FileExistsError("refusing to overwrite adjudication queue")
    destination.parent.mkdir(parents=True, exist_ok=True)
    queue.to_csv(destination, index=False)


def _apply_completed_adjudications(
    study_annotations: pd.DataFrame,
    clip_annotations: pd.DataFrame,
    adjudication_queue: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    expected = build_adjudication_queue(study_annotations, clip_annotations)
    required = [
        "audit_id",
        "clip_audit_id",
        "annotation_level",
        "outcome",
        "reader_values",
        "adjudication_reason",
        "adjudicated_value",
        "adjudicator_id",
    ]
    require_columns(adjudication_queue, required, "completed adjudication queue")
    compare_columns = [
        "audit_id",
        "clip_audit_id",
        "annotation_level",
        "outcome",
        "reader_values",
        "adjudication_reason",
    ]

    def normalized(frame: pd.DataFrame) -> pd.DataFrame:
        return (
            frame[compare_columns]
            .fillna("")
            .astype(str)
            .sort_values(compare_columns, kind="mergesort")
            .reset_index(drop=True)
        )

    supplied = adjudication_queue.copy()
    if supplied.duplicated(compare_columns).any() or not normalized(supplied).equals(
        normalized(expected)
    ):
        raise Tier1BlockedError(
            BLOCKED_LINEAGE,
            "completed adjudication queue does not exactly match the locked reader findings",
        )
    if supplied.empty:
        return study_annotations.copy(), clip_annotations.copy()
    if (
        supplied["adjudicated_value"].fillna("").astype(str).str.strip().eq("").any()
        or supplied["adjudicator_id"].fillna("").astype(str).str.strip().eq("").any()
    ):
        raise Tier1BlockedError(BLOCKED_LINEAGE, "one or more required adjudications are incomplete")
    supplied["adjudicated_value"] = (
        supplied["adjudicated_value"].astype(str).str.strip().str.lower()
    )
    invalid = sorted(set(supplied["adjudicated_value"]) - PRESENCE_VALUES)
    if invalid:
        raise Tier1BlockedError(
            BLOCKED_LINEAGE,
            f"completed adjudication queue contains invalid values: {invalid}",
        )

    resolved_study = study_annotations.copy()
    resolved_clip = clip_annotations.copy()
    for level, source, keys in (
        ("study", study_annotations, ["audit_id"]),
        ("clip", clip_annotations, ["audit_id", "clip_audit_id"]),
    ):
        level_queue = supplied[supplied["annotation_level"].astype(str).eq(level)]
        appended: list[pd.Series] = []
        group_key: str | list[str] = keys[0] if len(keys) == 1 else keys
        for raw_key, group in level_queue.groupby(group_key, dropna=False, sort=True):
            key_values = raw_key if isinstance(raw_key, tuple) else (raw_key,)
            mask = pd.Series(True, index=source.index)
            for column, value in zip(keys, key_values):
                mask &= source[column].fillna("").astype(str).eq("" if pd.isna(value) else str(value))
            primary = source.loc[
                mask
                & source["reader_role"].astype(str).str.strip().str.lower().eq("primary")
            ]
            if len(primary) != 1:
                raise Tier1BlockedError(
                    BLOCKED_LINEAGE,
                    "adjudication cannot resolve to exactly one primary annotation row",
                )
            row = primary.iloc[0].copy()
            for queue_row in group.itertuples(index=False):
                row[str(queue_row.outcome)] = str(queue_row.adjudicated_value)
            row["reader_role"] = "adjudicated"
            row["reader_id"] = ";".join(
                sorted(set(group["adjudicator_id"].astype(str).str.strip()))
            )
            appended.append(row)
        if appended:
            combined = pd.concat([source, pd.DataFrame(appended)], ignore_index=True)
            if level == "study":
                resolved_study = combined
            else:
                resolved_clip = combined
    return resolved_study, resolved_clip


def _primary_annotations(annotations: pd.DataFrame) -> pd.DataFrame:
    require_columns(annotations, ["audit_id"], "study annotations")
    if "reader_role" in annotations.columns:
        roles = annotations["reader_role"].astype(str).str.lower()
        primary = annotations[roles.isin({"primary", "adjudicated"})].copy()
        primary["_priority"] = primary["reader_role"].astype(str).str.lower().map({"primary": 0, "adjudicated": 1})
        primary = primary.sort_values(["audit_id", "_priority"]).drop_duplicates("audit_id", keep="last")
        return primary.drop(columns=["_priority"])
    return annotations.drop_duplicates("audit_id", keep="first").copy()


def _primary_clip_annotations(annotations: pd.DataFrame) -> pd.DataFrame:
    require_columns(annotations, ["audit_id", "clip_audit_id"], "clip annotations")
    keys = ["audit_id", "clip_audit_id"]
    if "reader_role" in annotations.columns:
        roles = annotations["reader_role"].astype(str).str.lower()
        primary = annotations[roles.isin({"primary", "adjudicated"})].copy()
        primary["_priority"] = primary["reader_role"].astype(str).str.lower().map({"primary": 0, "adjudicated": 1})
        primary = primary.sort_values(keys + ["_priority"]).drop_duplicates(keys, keep="last")
        return primary.drop(columns=["_priority"])
    return annotations.drop_duplicates(keys, keep="first").copy()


def _expand_target_linkage(linkage: pd.DataFrame) -> pd.DataFrame:
    require_columns(linkage, ["audit_id", "target_membership", "target_strata"], "audit linkage")
    rows: list[dict[str, str]] = []
    for row in linkage.itertuples(index=False):
        memberships = str(row.target_membership).split(";")
        strata = {
            item.split(":", 1)[0]: item.split(":", 1)[1]
            for item in str(row.target_strata).split(";")
            if ":" in item
        }
        for target in memberships:
            rows.append({"audit_id": str(row.audit_id), "target": target, "split": strata.get(target, "")})
    return pd.DataFrame(rows)


def _study_summaries(
    study_annotations: pd.DataFrame,
    linkage: pd.DataFrame,
    design: pd.DataFrame,
) -> pd.DataFrame:
    primary = _primary_annotations(study_annotations)
    expanded = _expand_target_linkage(linkage)
    joined = expanded.merge(primary, on="audit_id", how="left", validate="many_to_one")
    joined = joined.merge(
        design[["target", "split", "design_weight"]],
        on=["target", "split"],
        how="left",
        validate="many_to_one",
    )
    rows: list[dict[str, Any]] = []
    for target, target_frame in joined.groupby("target", sort=True):
        for outcome in STUDY_OUTCOMES:
            if outcome not in target_frame.columns:
                continue
            values = target_frame[outcome].astype(str).str.lower()
            assessable = values.isin({"yes", "no"})
            binary = values.loc[assessable].eq("yes").astype(float).to_numpy()
            weights = (
                pd.to_numeric(target_frame.loc[assessable, "design_weight"], errors="coerce")
                .fillna(1.0)
                .to_numpy()
            )
            estimate, low, high, n_eff = weighted_interval(binary, weights)
            rows.append(
                {
                    "target": target,
                    "stratum": "design_weighted_overall",
                    "outcome": outcome,
                    "n_sampled_studies": int(len(target_frame)),
                    "n_assessable_studies": int(assessable.sum()),
                    "n_positive_studies": int(values.eq("yes").sum()),
                    "n_uncertain_studies": int(values.eq("uncertain").sum()),
                    "n_not_assessable_studies": int(values.eq("not_assessable").sum()),
                    "proportion": estimate,
                    "ci_low": low,
                    "ci_high": high,
                    "effective_n": n_eff,
                    "ci_method": "design_weighted_wilson_effective_n",
                }
            )
            for split, stratum in target_frame.groupby("split", sort=True):
                stratum_values = stratum[outcome].astype(str).str.lower()
                stratum_assessable = stratum_values.isin({"yes", "no"})
                n = int(stratum_assessable.sum())
                yes = int(stratum_values.eq("yes").sum())
                stratum_low, stratum_high = wilson_interval(yes, n)
                rows.append(
                    {
                        "target": target,
                        "stratum": split,
                        "outcome": outcome,
                        "n_sampled_studies": int(len(stratum)),
                        "n_assessable_studies": n,
                        "n_positive_studies": yes,
                        "n_uncertain_studies": int(stratum_values.eq("uncertain").sum()),
                        "n_not_assessable_studies": int(stratum_values.eq("not_assessable").sum()),
                        "proportion": yes / n if n else np.nan,
                        "ci_low": stratum_low,
                        "ci_high": stratum_high,
                        "effective_n": float(n),
                        "ci_method": "wilson",
                    }
                )
    return pd.DataFrame(rows)


def _agreement_summaries(study_annotations: pd.DataFrame) -> pd.DataFrame:
    if not {"reader_id", "reader_role"}.issubset(study_annotations.columns):
        return pd.DataFrame(columns=["outcome", "n_pairs", "raw_agreement", "cohen_kappa", "gwet_ac1"])
    roles = study_annotations["reader_role"].astype(str).str.strip().str.lower()
    preadjudication = study_annotations.loc[roles.isin({"primary", "secondary"})].copy()
    rows: list[dict[str, Any]] = []
    for outcome in STUDY_OUTCOMES:
        if outcome not in study_annotations.columns:
            continue
        paired = preadjudication[["audit_id", "reader_id", "reader_role", outcome]].dropna(
            subset=[outcome]
        ).copy()
        left: list[str] = []
        right: list[str] = []
        for _, group in paired.groupby("audit_id"):
            primary = group.loc[group["reader_role"].astype(str).str.lower() == "primary", outcome]
            secondary = group.loc[group["reader_role"].astype(str).str.lower() == "secondary", outcome]
            if len(primary) == 1 and len(secondary) == 1:
                left.append(str(primary.iloc[0]))
                right.append(str(secondary.iloc[0]))
        rows.append({"outcome": outcome, **agreement_statistics(left, right)})
    return pd.DataFrame(rows)


def _cluster_bootstrap_clip_proportion(
    frame: pd.DataFrame,
    outcome: str,
    n_bootstrap: int,
    seed: int,
) -> tuple[float, float, float, int, int]:
    values = frame[outcome].astype(str).str.lower()
    work = frame.loc[values.isin({"yes", "no"}), ["audit_id"]].copy()
    work["positive"] = values.loc[values.isin({"yes", "no"})].eq("yes").astype(float).to_numpy()
    if work.empty:
        return np.nan, np.nan, np.nan, 0, 0
    estimate = float(work["positive"].mean())
    studies = work["audit_id"].drop_duplicates().to_numpy()
    rng = np.random.default_rng(seed)
    values_boot: list[float] = []
    groups = {study: group["positive"].to_numpy() for study, group in work.groupby("audit_id")}
    for _ in range(n_bootstrap):
        sampled = rng.choice(studies, size=len(studies), replace=True)
        combined = np.concatenate([groups[study] for study in sampled])
        values_boot.append(float(combined.mean()))
    low, high = np.percentile(values_boot, [2.5, 97.5])
    return estimate, float(low), float(high), int(len(work)), int(len(studies))


def _clip_summaries(
    clip_annotations: pd.DataFrame,
    linkage: pd.DataFrame,
    n_bootstrap: int,
    seed: int,
) -> pd.DataFrame:
    if clip_annotations.empty:
        return pd.DataFrame(
            columns=[
                "target",
                "outcome",
                "n_clips",
                "n_studies",
                "proportion",
                "ci_low",
                "ci_high",
                "estimand",
                "ci_method",
            ]
        )
    require_columns(clip_annotations, ["audit_id"], "clip annotations")
    primary = _primary_clip_annotations(clip_annotations)
    expanded = _expand_target_linkage(linkage)
    joined = expanded.merge(primary, on="audit_id", how="inner", validate="many_to_many")
    rows: list[dict[str, Any]] = []
    for target, target_frame in joined.groupby("target", sort=True):
        for outcome in CLIP_PRESENCE_FIELDS:
            if outcome not in target_frame.columns:
                continue
            estimate, low, high, n_clips, n_studies = _cluster_bootstrap_clip_proportion(
                target_frame,
                outcome,
                n_bootstrap,
                int(hashlib.sha256(f"{seed}:{target}:{outcome}".encode("utf-8")).hexdigest()[:8], 16),
            )
            rows.append(
                {
                    "target": target,
                    "outcome": outcome,
                    "n_clips": n_clips,
                    "n_studies": n_studies,
                    "proportion": estimate,
                    "ci_low": low,
                    "ci_high": high,
                    "estimand": "unweighted_sampled_clip_composition",
                    "ci_method": "unweighted_sampled_clip_study_cluster_percentile_bootstrap",
                    "bootstrap_n": int(n_bootstrap),
                }
            )
        if "acquisition_content_type" in target_frame.columns:
            for category in sorted(CONTENT_TYPES):
                category_frame = target_frame[["audit_id", "acquisition_content_type"]].copy()
                category_frame["category_present"] = (
                    category_frame["acquisition_content_type"].astype(str).str.lower() == category
                ).map({True: "yes", False: "no"})
                estimate, low, high, n_clips, n_studies = _cluster_bootstrap_clip_proportion(
                    category_frame,
                    "category_present",
                    n_bootstrap,
                    int(hashlib.sha256(f"{seed}:{target}:acquisition:{category}".encode("utf-8")).hexdigest()[:8], 16),
                )
                rows.append(
                    {
                        "target": target,
                        "outcome": f"acquisition_content_type::{category}",
                        "n_clips": n_clips,
                        "n_studies": n_studies,
                        "proportion": estimate,
                        "ci_low": low,
                        "ci_high": high,
                        "estimand": "unweighted_sampled_clip_composition",
                        "ci_method": "unweighted_sampled_clip_study_cluster_percentile_bootstrap",
                        "bootstrap_n": int(n_bootstrap),
                    }
                )
    return pd.DataFrame(rows)


def _validate_annotation_roster(
    study_annotations: pd.DataFrame,
    clip_annotations: pd.DataFrame,
    linkage: pd.DataFrame,
    sampling_design: pd.DataFrame,
    second_reader_manifest: pd.DataFrame,
    clip_roster: pd.DataFrame,
) -> str:
    require_columns(linkage, ["audit_id", "target_membership", "target_strata"], "audit linkage")
    require_columns(study_annotations, ["audit_id", "reader_id", "reader_role", SAMPLE_TOKEN_COLUMN], "study annotations")
    require_columns(second_reader_manifest, ["audit_id", SAMPLE_TOKEN_COLUMN], "second-reader manifest")
    require_columns(clip_roster, ["audit_id", "clip_audit_id"], "canonical clip roster")
    require_columns(clip_annotations, ["audit_id", "clip_audit_id", "reader_id", "reader_role", SAMPLE_TOKEN_COLUMN], "clip annotations")
    if linkage["audit_id"].duplicated().any() or second_reader_manifest["audit_id"].duplicated().any():
        raise Tier1BlockedError(BLOCKED_LINEAGE, "audit linkage or second-reader manifest has duplicate audit IDs")
    if clip_roster[["audit_id", "clip_audit_id"]].astype(str).duplicated().any():
        raise Tier1BlockedError(BLOCKED_LINEAGE, "canonical clip roster contains duplicate opaque clip IDs")
    expected_token = _sample_manifest_token(linkage, sampling_design, clip_roster)
    for label, frame in (
        ("study annotations", study_annotations),
        ("clip annotations", clip_annotations),
        ("second-reader manifest", second_reader_manifest),
    ):
        _require_exact_sample_token(frame, expected_token, label)

    expected_ids = set(linkage["audit_id"].astype(str))
    study_ids = set(study_annotations["audit_id"].astype(str))
    clip_ids = set(clip_annotations["audit_id"].astype(str))
    if study_ids != expected_ids or not clip_ids.issubset(expected_ids):
        raise Tier1BlockedError(BLOCKED_LINEAGE, "annotation audit IDs do not match the locked sample roster")
    roles = study_annotations["reader_role"].astype(str).str.strip().str.lower()
    if not set(roles).issubset({"primary", "secondary"}):
        raise Tier1BlockedError(BLOCKED_LINEAGE, "study annotations contain invalid reader roles")
    if study_annotations["reader_id"].fillna("").astype(str).str.strip().eq("").any():
        raise Tier1BlockedError(BLOCKED_LINEAGE, "study annotations contain missing reader IDs")
    study_role_counts = (
        study_annotations.assign(
            _audit_id=study_annotations["audit_id"].astype(str),
            _reader_role=roles,
        )
        .groupby(["_audit_id", "_reader_role"])
        .size()
    )
    for audit_id in expected_ids:
        if int(study_role_counts.get((audit_id, "primary"), 0)) != 1:
            raise Tier1BlockedError(BLOCKED_LINEAGE, "each sampled study must have exactly one primary read")
    primary = _primary_annotations(study_annotations)
    if set(primary["audit_id"].astype(str)) != expected_ids or len(primary) != len(expected_ids):
        raise Tier1BlockedError(BLOCKED_LINEAGE, "final study annotations do not contain one canonical row per sampled study")
    for outcome in (*STUDY_OUTCOMES, "reader_confidence"):
        require_columns(study_annotations, [outcome], "study annotations")
        if study_annotations[outcome].fillna("").astype(str).str.strip().eq("").any():
            raise Tier1BlockedError(BLOCKED_LINEAGE, f"study annotations are incomplete for {outcome}")

    expected_secondary = set(second_reader_manifest["audit_id"].astype(str))
    secondary = study_annotations.loc[roles == "secondary"]
    secondary_counts = secondary.groupby(secondary["audit_id"].astype(str)).size().to_dict()
    if set(secondary_counts) != expected_secondary or any(count != 1 for count in secondary_counts.values()):
        raise Tier1BlockedError(BLOCKED_LINEAGE, "second-reader annotations do not match the assigned roster")
    for audit_id in expected_secondary:
        readers = study_annotations.loc[
            study_annotations["audit_id"].astype(str).eq(audit_id)
            & roles.isin({"primary", "secondary"}),
            ["reader_role", "reader_id"],
        ]
        primary_reader = readers.loc[
            readers["reader_role"].astype(str).str.strip().str.lower().eq("primary"),
            "reader_id",
        ].astype(str).str.strip()
        secondary_reader = readers.loc[
            readers["reader_role"].astype(str).str.strip().str.lower().eq("secondary"),
            "reader_id",
        ].astype(str).str.strip()
        if primary_reader.iloc[0] == secondary_reader.iloc[0]:
            raise Tier1BlockedError(BLOCKED_LINEAGE, "primary and secondary reads are not independent")

    clip_roles = clip_annotations["reader_role"].astype(str).str.strip().str.lower()
    if not set(clip_roles).issubset({"primary", "secondary"}):
        raise Tier1BlockedError(BLOCKED_LINEAGE, "clip annotations contain invalid reader roles")
    if clip_annotations["reader_id"].fillna("").astype(str).str.strip().eq("").any():
        raise Tier1BlockedError(BLOCKED_LINEAGE, "clip annotations contain missing reader IDs")
    for column in ("acquisition_content_type", *CLIP_PRESENCE_FIELDS, "reader_confidence"):
        require_columns(clip_annotations, [column], "clip annotations")
        if clip_annotations[column].fillna("").astype(str).str.strip().eq("").any():
            raise Tier1BlockedError(BLOCKED_LINEAGE, f"clip annotations are incomplete for {column}")
    clip_with_roles = clip_annotations.assign(
        _audit_id=clip_annotations["audit_id"].astype(str),
        _clip_id=clip_annotations["clip_audit_id"].astype(str),
        _reader_role=clip_roles,
    )
    if clip_with_roles[["_audit_id", "_clip_id"]].eq("").any().any():
        raise Tier1BlockedError(BLOCKED_LINEAGE, "clip annotations contain missing opaque clip IDs")
    clip_role_counts = clip_with_roles.groupby(["_audit_id", "_clip_id", "_reader_role"]).size()
    if bool((clip_role_counts > 1).any()):
        raise Tier1BlockedError(BLOCKED_LINEAGE, "clip annotations contain duplicate reads for one reader role")
    primary_clip_keys = set(
        clip_with_roles.loc[clip_with_roles["_reader_role"] == "primary", ["_audit_id", "_clip_id"]]
        .itertuples(index=False, name=None)
    )
    expected_clip_keys = set(
        clip_roster[["audit_id", "clip_audit_id"]]
        .astype(str)
        .itertuples(index=False, name=None)
    )
    if primary_clip_keys != expected_clip_keys:
        raise Tier1BlockedError(
            BLOCKED_LINEAGE,
            "primary clip annotations do not exactly match the locked canonical clip roster",
        )
    secondary_clip_keys = set(
        clip_with_roles.loc[clip_with_roles["_reader_role"] == "secondary", ["_audit_id", "_clip_id"]]
        .itertuples(index=False, name=None)
    )
    expected_secondary_clip_keys = {
        key for key in expected_clip_keys if key[0] in expected_secondary
    }
    if secondary_clip_keys != expected_secondary_clip_keys:
        raise Tier1BlockedError(BLOCKED_LINEAGE, "second-reader clip annotations do not match the assigned roster")
    study_reader_map = {
        (str(row.audit_id), str(row.reader_role).strip().lower()): str(row.reader_id).strip()
        for row in study_annotations[["audit_id", "reader_role", "reader_id"]].itertuples(index=False)
    }
    for audit_id, _clip_id, reader_role, reader_id in clip_with_roles[
        ["_audit_id", "_clip_id", "_reader_role", "reader_id"]
    ].itertuples(index=False, name=None):
        expected_reader = study_reader_map.get((str(audit_id), str(reader_role)))
        observed_reader = str(reader_id).strip()
        if expected_reader is None or observed_reader != expected_reader:
            raise Tier1BlockedError(
                BLOCKED_LINEAGE,
                "clip readers do not match the independent locked study-reader assignments",
            )
    return expected_token


def aggregate_audit_annotations(
    study_annotations: pd.DataFrame,
    clip_annotations: pd.DataFrame,
    linkage: pd.DataFrame,
    sampling_design: pd.DataFrame,
    config: Mapping[str, Any],
    config_hash: str,
    second_reader_manifest: pd.DataFrame,
    clip_roster: pd.DataFrame,
    adjudication_queue: pd.DataFrame,
    input_hashes: Mapping[str, str] | None = None,
    n_bootstrap: int = 2000,
) -> AuditAggregateResult:
    validate_audit_config(config)
    require_columns(sampling_design, ["target", "split", "source_n", "sample_n", "design_weight"], "sampling design")
    if n_bootstrap <= 0:
        raise ValueError("n_bootstrap must be positive")
    sample_token = _validate_annotation_roster(
        study_annotations,
        clip_annotations,
        linkage,
        sampling_design,
        second_reader_manifest,
        clip_roster,
    )
    for column in STUDY_OUTCOMES:
        if column in study_annotations.columns:
            invalid = sorted(set(study_annotations[column].dropna().astype(str).str.lower()) - PRESENCE_VALUES - {""})
            if invalid:
                raise ValueError(f"Invalid values for {column}: {invalid}")
    invalid_study_confidence = sorted(
        set(study_annotations["reader_confidence"].dropna().astype(str).str.lower())
        - READER_CONFIDENCE
        - {""}
    )
    if invalid_study_confidence:
        raise ValueError(f"Invalid study reader confidence values: {invalid_study_confidence}")
    if "acquisition_content_type" in clip_annotations.columns:
        invalid_content = sorted(
            set(clip_annotations["acquisition_content_type"].dropna().astype(str).str.lower()) - CONTENT_TYPES - {""}
        )
        if invalid_content:
            raise ValueError(f"Invalid acquisition content types: {invalid_content}")
    for column in CLIP_PRESENCE_FIELDS:
        invalid = sorted(
            set(clip_annotations[column].dropna().astype(str).str.lower()) - PRESENCE_VALUES - {""}
        )
        if invalid:
            raise ValueError(f"Invalid values for {column}: {invalid}")
    invalid_confidence = sorted(
        set(clip_annotations["reader_confidence"].dropna().astype(str).str.lower())
        - READER_CONFIDENCE
        - {""}
    )
    if invalid_confidence:
        raise ValueError(f"Invalid reader confidence values: {invalid_confidence}")
    resolved_study, resolved_clips = _apply_completed_adjudications(
        study_annotations,
        clip_annotations,
        adjudication_queue,
    )
    study_summary = _study_summaries(resolved_study, linkage, sampling_design)
    agreement = _agreement_summaries(study_annotations)
    clip_summary = _clip_summaries(resolved_clips, linkage, n_bootstrap, int(config["seed"]))
    for label, frame in (
        ("study audit summary", study_summary),
        ("clip audit summary", clip_summary),
        ("reader agreement", agreement),
    ):
        assert_export_safe_frame(frame, label)
    primary_clip_rows = _primary_clip_annotations(resolved_clips)
    reconstruction_failures = int(
        primary_clip_rows["reconstruction_success"].astype(str).str.lower().eq("no").sum()
    )
    summary = {
        "protocol_version": config["protocol_version"],
        "configuration_sha256": config_hash,
        "sample_manifest_token": sample_token,
        "input_file_hashes": dict(input_hashes or {}),
        "bootstrap_n": int(n_bootstrap),
        "bootstrap_seed": int(config["seed"]),
        "study_summary_rows": int(len(study_summary)),
        "clip_summary_rows": int(len(clip_summary)),
        "clip_summary_estimand": "unweighted_sampled_clip_composition",
        "agreement_rows": int(len(agreement)),
        "required_adjudication_rows": int(len(adjudication_queue)),
        "completed_adjudication_rows": int(len(adjudication_queue)),
        "annotation_roster_validated": True,
        "independent_second_reads_validated": True,
        "completed_adjudication_validated": True,
        "reconstruction_failure_count": reconstruction_failures,
        "row_level_annotations_exported": False,
        "candidate_values_exported": False,
    }
    return AuditAggregateResult(study_summary, clip_summary, agreement, summary)


def _canonical_visible_value(value: Any, unit: Any, target: str) -> tuple[float, float]:
    numeric = float(str(value).strip())
    if not np.isfinite(numeric):
        raise ValueError("visible value is not finite")
    normalized_unit = str(unit).strip().lower().replace(" ", "")
    aliases = {
        "cm": "cm",
        "centimeter": "cm",
        "centimeters": "cm",
        "mm": "mm",
        "millimeter": "mm",
        "millimeters": "mm",
    }
    source_unit = aliases.get(normalized_unit)
    if source_unit is None:
        raise ValueError("visible unit is missing or unsupported")
    if target not in {"lvot_vti", "tapse"}:
        raise ValueError(f"unsupported target for visible-value matching: {target}")
    canonical_unit = "cm" if target == "lvot_vti" else "mm"
    factor = 1.0
    if source_unit == "mm" and canonical_unit == "cm":
        factor = 0.1
    elif source_unit == "cm" and canonical_unit == "mm":
        factor = 10.0
    return numeric * factor, factor


def derive_post_unblinding_value_matches(
    clip_annotations: pd.DataFrame,
    linkage: pd.DataFrame,
    sampling_design: pd.DataFrame,
    clip_roster: pd.DataFrame,
    adjudication_queue: pd.DataFrame,
    config: Mapping[str, Any],
    config_hash: str,
    audit_completion: Mapping[str, Any],
    input_hashes: Mapping[str, str],
) -> PostUnblindingMatchResult:
    """Compare locked blinded transcriptions with report labels in restricted storage."""

    validate_audit_config(config)
    require_columns(
        clip_annotations,
        [
            "audit_id",
            "clip_audit_id",
            "reader_id",
            "reader_role",
            SAMPLE_TOKEN_COLUMN,
            "candidate_target_value_present",
            "candidate_target_value",
            "visible_unit_text",
            "display_precision",
            "lvot_vti_specific_label",
            "tapse_specific_label",
        ],
        "locked clip annotations",
    )
    require_columns(
        linkage,
        [
            "audit_id",
            "target_membership",
            "target_strata",
            "lvot_vti_target_value",
            "tapse_target_value",
        ],
        "restricted audit linkage",
    )
    require_columns(
        sampling_design,
        ["target", "split", "source_n", "sample_n", "design_weight"],
        "restricted sampling design",
    )
    require_columns(
        adjudication_queue,
        ["annotation_level"],
        "completed adjudication queue",
    )
    if linkage["audit_id"].duplicated().any():
        raise Tier1BlockedError(BLOCKED_LINEAGE, "restricted audit linkage has duplicate audit IDs")
    require_columns(clip_roster, ["audit_id", "clip_audit_id"], "canonical clip roster")
    sample_token = _sample_manifest_token(linkage, sampling_design, clip_roster)
    _require_exact_sample_token(clip_annotations, sample_token, "clip transcriptions")
    validated_hashes = _validate_manual_audit_completion_for_post_unblinding(
        audit_completion,
        config_hash,
        sample_token,
        input_hashes,
    )

    clip_queue = adjudication_queue.loc[
        adjudication_queue["annotation_level"].astype(str).eq("clip")
    ].copy()
    _, resolved_clips = _apply_completed_adjudications(
        pd.DataFrame(),
        clip_annotations,
        clip_queue,
    )
    primary = _primary_clip_annotations(resolved_clips)
    expected_clip_keys = set(
        clip_roster[["audit_id", "clip_audit_id"]]
        .astype(str)
        .itertuples(index=False, name=None)
    )
    observed_clip_keys = set(
        primary[["audit_id", "clip_audit_id"]]
        .astype(str)
        .itertuples(index=False, name=None)
    )
    if observed_clip_keys != expected_clip_keys:
        raise Tier1BlockedError(
            BLOCKED_LINEAGE,
            "post-unblinding annotations do not exactly match the locked clip roster",
        )
    linkage_by_id = linkage.set_index("audit_id")
    expanded = _expand_target_linkage(linkage)
    rows: list[dict[str, Any]] = []
    for expanded_row in expanded.itertuples(index=False):
        audit_id = str(expanded_row.audit_id)
        target = str(expanded_row.target)
        split = str(expanded_row.split)
        label_column = f"{target}_specific_label"
        target_column = f"{target}_target_value"
        report_value = pd.to_numeric(
            pd.Series([linkage_by_id.loc[audit_id, target_column]]), errors="coerce"
        ).iloc[0]
        if pd.isna(report_value) or not np.isfinite(float(report_value)):
            raise Tier1BlockedError(BLOCKED_LINEAGE, f"restricted linkage lacks {target} report label")
        study_clips = primary[primary["audit_id"].astype(str) == audit_id]
        candidate_presence = (
            study_clips["candidate_target_value_present"].astype(str).str.lower()
        )
        target_specificity = study_clips[label_column].astype(str).str.lower()
        candidate_mask = candidate_presence.eq("yes") & target_specificity.eq("yes")
        uncertain_candidate_mask = (
            candidate_presence.isin({"yes", "uncertain", "not_assessable"})
            & target_specificity.isin({"yes", "uncertain", "not_assessable"})
            & ~candidate_mask
        )
        candidates = study_clips.loc[candidate_mask]
        uncertain_candidates = int(uncertain_candidate_mask.sum())
        assessed = 0
        matched = 0
        not_assessable = 0
        canonical_candidates: list[float] = []
        for candidate in candidates.itertuples(index=False):
            try:
                precision = float(getattr(candidate, "display_precision"))
                if not np.isfinite(precision) or not precision.is_integer() or not 0 <= precision <= 6:
                    raise ValueError("display precision must be an integer from 0 to 6")
                visible, unit_factor = _canonical_visible_value(
                    getattr(candidate, "candidate_target_value"),
                    getattr(candidate, "visible_unit_text"),
                    target,
                )
                tolerance = 0.5 * (10.0 ** (-int(precision))) * unit_factor
                assessed += 1
                canonical_candidates.append(visible)
                matched += int(abs(visible - float(report_value)) <= tolerance + 1e-12)
            except (TypeError, ValueError):
                not_assessable += 1
        distinct_candidates = sorted(set(canonical_candidates))
        requires_adjudication = bool(
            len(distinct_candidates) > 1 or uncertain_candidates or not_assessable
        )
        status = (
            "not_assessable"
            if requires_adjudication
            else "yes"
            if matched
            else "no"
        )
        rows.append(
            {
                "audit_id": audit_id,
                "target": target,
                "split": split,
                "confirmed_target_value_match_present": status,
                "candidate_clip_count": int(len(candidates)),
                "assessable_candidate_clip_count": int(assessed),
                "matching_candidate_clip_count": int(matched),
                "not_assessable_candidate_clip_count": int(not_assessable),
                "uncertain_candidate_clip_count": uncertain_candidates,
                "restricted_adjudication_required": "yes" if requires_adjudication else "no",
            }
        )
    restricted = pd.DataFrame(rows)
    joined = restricted.merge(
        sampling_design[["target", "split", "design_weight"]],
        on=["target", "split"],
        how="left",
        validate="many_to_one",
    )
    if joined["design_weight"].isna().any() or not np.isfinite(
        pd.to_numeric(joined["design_weight"], errors="coerce").to_numpy(dtype=float)
    ).all():
        raise Tier1BlockedError(BLOCKED_LINEAGE, "post-unblinding rows do not reconcile to sampling weights")
    safe_rows: list[dict[str, Any]] = []
    for target, group in joined.groupby("target", sort=True):
        values = group["confirmed_target_value_match_present"].astype(str)
        assessable = values.isin({"yes", "no"})
        binary = values.loc[assessable].eq("yes").astype(float).to_numpy()
        weights = pd.to_numeric(group.loc[assessable, "design_weight"], errors="coerce").to_numpy()
        estimate, low, high, effective_n = weighted_interval(binary, weights)
        safe_rows.append(
            {
                "target": target,
                "n_sampled_studies": int(len(group)),
                "n_assessable_studies": int(assessable.sum()),
                "n_matching_studies": int(values.eq("yes").sum()),
                "n_not_assessable_studies": int(values.eq("not_assessable").sum()),
                "n_requiring_restricted_adjudication": int(
                    group["restricted_adjudication_required"].eq("yes").sum()
                ),
                "proportion": estimate,
                "ci_low": low,
                "ci_high": high,
                "effective_n": effective_n,
                "ci_method": "design_weighted_wilson_effective_n",
            }
        )
    safe = pd.DataFrame(safe_rows)
    assert_export_safe_frame(safe, "post-unblinding value-match summary")
    provenance = {
        "protocol_version": config["protocol_version"],
        "configuration_sha256": config_hash,
        "sample_manifest_token": sample_token,
        "reader_target_values_prohibited": True,
        "comparison_performed_after_locked_blinded_transcription": True,
        "manual_audit_completion_status": audit_completion["status"],
        "match_tolerance": config["post_unblinding_value_match"]["match_tolerance"],
        "input_file_hashes": validated_hashes,
    }
    return PostUnblindingMatchResult(restricted, safe, provenance)


def write_post_unblinding_value_matches(
    result: PostUnblindingMatchResult,
    restricted_output_csv: Path,
    safe_output_dir: Path,
) -> None:
    restricted = require_restricted_destination(restricted_output_csv)
    if restricted.exists() or safe_output_dir.exists():
        raise FileExistsError("refusing to overwrite post-unblinding match outputs")
    restricted.parent.mkdir(parents=True, exist_ok=True)
    result.restricted_study_matches.to_csv(restricted, index=False)
    safe_output_dir.mkdir(parents=True)
    write_safe_csv(
        safe_output_dir / "post_unblinding_value_match_summary.csv",
        result.safe_summary,
        "post-unblinding value-match summary",
    )
    write_json(
        safe_output_dir / "post_unblinding_value_match_provenance.json",
        result.provenance,
    )


MANUAL_AUDIT_OUTPUT_FILES = {
    "manual_audit_study_summary": "manual_audit_study_summary.csv",
    "manual_audit_clip_summary": "manual_audit_clip_summary.csv",
    "manual_audit_reader_agreement": "manual_audit_reader_agreement.csv",
    "manual_audit_summary": "manual_audit_summary.json",
}


def load_manual_audit_completion(path: Path) -> dict[str, Any]:
    """Load a completion certificate and recheck all certified safe outputs."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise Tier1BlockedError(BLOCKED_LINEAGE, f"invalid manual-audit completion certificate: {exc}") from exc
    if (
        payload.get("schema_version") != "jdim-manual-audit-completion-v1"
        or payload.get("status") != "MANUAL_AUDIT_COMPLETE"
    ):
        raise Tier1BlockedError(BLOCKED_LINEAGE, "manual-audit completion certificate is not complete")
    records = payload.get("artifacts")
    if not isinstance(records, list):
        raise Tier1BlockedError(BLOCKED_LINEAGE, "manual-audit completion artifact matrix is invalid")
    by_role: dict[str, Mapping[str, Any]] = {}
    for record in records:
        if not isinstance(record, Mapping) or not str(record.get("logical_role", "")):
            raise Tier1BlockedError(BLOCKED_LINEAGE, "manual-audit completion has an invalid artifact record")
        role = str(record["logical_role"])
        if role in by_role:
            raise Tier1BlockedError(BLOCKED_LINEAGE, "manual-audit completion has duplicate artifact roles")
        by_role[role] = record
    if set(by_role) != set(MANUAL_AUDIT_OUTPUT_FILES):
        raise Tier1BlockedError(BLOCKED_LINEAGE, "manual-audit completion artifact roles are incomplete")
    for role, filename in MANUAL_AUDIT_OUTPUT_FILES.items():
        artifact = path.parent / filename
        if not artifact.is_file():
            raise Tier1BlockedError(BLOCKED_LINEAGE, f"certified manual-audit artifact is missing: {role}")
        observed = safe_file_record(role, artifact)
        declared = by_role[role]
        if (
            declared.get("sha256") != observed["sha256"]
            or declared.get("size_bytes") != observed["size_bytes"]
        ):
            raise Tier1BlockedError(BLOCKED_LINEAGE, f"certified manual-audit artifact changed: {role}")
    return payload


def write_audit_aggregates(result: AuditAggregateResult, output_dir: Path) -> None:
    if output_dir.exists():
        raise FileExistsError("refusing to overwrite manual-audit aggregate outputs")
    input_hashes = _validated_input_hashes(
        result.summary.get("input_file_hashes"),
        MANUAL_AUDIT_INPUT_HASH_ROLES,
        "manual-audit aggregate inputs",
    )
    for field in (
        "annotation_roster_validated",
        "independent_second_reads_validated",
        "completed_adjudication_validated",
    ):
        if result.summary.get(field) is not True:
            raise Tier1BlockedError(BLOCKED_LINEAGE, f"manual-audit aggregate lacks {field}")
    output_dir.mkdir(parents=True)
    write_safe_csv(output_dir / "manual_audit_study_summary.csv", result.study_summary, "study audit summary")
    write_safe_csv(output_dir / "manual_audit_clip_summary.csv", result.clip_summary, "clip audit summary")
    write_safe_csv(output_dir / "manual_audit_reader_agreement.csv", result.agreement, "reader agreement")
    write_json(output_dir / "manual_audit_summary.json", result.summary)
    completion = {
        "schema_version": "jdim-manual-audit-completion-v1",
        "status": "MANUAL_AUDIT_COMPLETE",
        "protocol_version": result.summary["protocol_version"],
        "configuration_sha256": result.summary["configuration_sha256"],
        "sample_manifest_token": result.summary["sample_manifest_token"],
        "annotation_roster_validated": True,
        "independent_second_reads_validated": True,
        "completed_adjudication_validated": True,
        "input_file_hashes": input_hashes,
        "artifacts": [
            safe_file_record(role, output_dir / filename)
            for role, filename in sorted(MANUAL_AUDIT_OUTPUT_FILES.items())
        ],
    }
    write_json(output_dir / "manual_audit_completion.json", completion)
