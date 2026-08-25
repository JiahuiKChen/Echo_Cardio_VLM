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

STUDY_OUTCOMES = [
    "spectral_doppler_present",
    "m_mode_present",
    "caliper_or_trace_present",
    "visible_numeric_value_present",
    "target_specific_label_present",
    "candidate_target_value_present",
    "confirmed_target_value_match_present",
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
    "reader_id",
    "reader_role",
    *STUDY_OUTCOMES,
    "reader_confidence",
    "restricted_notes",
]

CLIP_TEMPLATE_COLUMNS = [
    "audit_id",
    "clip_audit_id",
    "reader_id",
    "reader_role",
    "acquisition_content_type",
    *CLIP_PRESENCE_FIELDS,
    "candidate_target_value",
    "display_precision",
    "reader_confidence",
    "restricted_notes",
]


@dataclass
class AuditSampleResult:
    linkage: pd.DataFrame
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
        "primary_study_level_outcomes",
        "secondary_clip_level_outcomes",
        "reader_blinding",
        "second_reader_fraction",
        "adjudication",
        "restricted_output_root_required",
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
    if set(config["primary_study_level_outcomes"]) != set(STUDY_OUTCOMES):
        raise ValueError("Primary study-level outcome schema differs from protocol v1")
    prohibited = set(config["reader_blinding"].get("prohibited_fields", []))
    required_blinding = {"target_value", "prediction", "residual", "filename", "subject_id", "study_id", "split"}
    if not required_blinding.issubset(prohibited):
        raise ValueError("Reader blinding does not prohibit all prespecified fields")
    allowed_pilot = set(config["technical_pilot"].get("allowed_fields", []))
    if not bool(config["technical_pilot"].get("clinical_content_labels_prohibited")):
        raise ValueError("Technical pilot must prohibit clinical content labels")
    if not allowed_pilot.issubset({"audit_id", "review_order", "reconstruction_success", "review_minutes"}):
        raise ValueError("Technical pilot contains nontechnical annotation fields")


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


def build_audit_sample(
    cohorts: Mapping[str, pd.DataFrame],
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
    if technical_pilot:
        study_template = reader_manifest.copy()
        study_template["reconstruction_success"] = ""
        study_template["review_minutes"] = ""
        clip_template = pd.DataFrame(columns=config["technical_pilot"]["allowed_fields"])
    else:
        primary = reader_manifest[["audit_id"]].copy()
        primary["reader_id"] = ""
        primary["reader_role"] = "primary"
        study_template = primary.reindex(columns=STUDY_TEMPLATE_COLUMNS, fill_value="")
        clip_template = pd.DataFrame(columns=CLIP_TEMPLATE_COLUMNS)

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
        "second_reader_studies": int(len(second_reader_manifest)),
        "allocation_method": (
            "author_approved_override"
            if approved_override is not None
            else "proportional_largest_remainder"
        ),
        "excluded_from_prevalence_estimates": bool(technical_pilot),
    }
    return AuditSampleResult(
        linkage=linkage.drop(columns=["_review_rank"]),
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
    restricted_root.mkdir(parents=True, exist_ok=True)
    result.linkage.to_csv(restricted_root / "audit_linkage.csv", index=False)
    result.reader_manifest.to_csv(restricted_root / "reader_manifest.csv", index=False)
    result.second_reader_manifest.to_csv(restricted_root / "second_reader_manifest.csv", index=False)
    result.study_template.to_csv(
        restricted_root
        / ("technical_pilot_template.csv" if result.technical_pilot else "study_annotation_template.csv"),
        index=False,
    )
    result.clip_template.to_csv(restricted_root / "clip_annotation_template.csv", index=False)
    result.sampling_design.to_csv(restricted_root / "sampling_design_restricted.csv", index=False)
    write_json(
        restricted_root / "sampling_provenance_restricted.json",
        {
            **result.safe_summary,
            "restricted_output_root": str(restricted_root),
            "row_level_outputs": [
                "audit_linkage.csv",
                "reader_manifest.csv",
                "second_reader_manifest.csv",
                "study_annotation_template.csv" if not result.technical_pilot else "technical_pilot_template.csv",
                "clip_annotation_template.csv",
                "sampling_design_restricted.csv",
            ],
        },
    )

    safe_output_dir.mkdir(parents=True, exist_ok=True)
    safe_design = result.sampling_design.drop(columns=["design_weight"]).copy()
    assert_export_safe_frame(safe_design, "audit sampling design summary")
    write_safe_csv(safe_output_dir / "audit_sampling_counts.csv", safe_design, "audit sampling counts")
    write_json(safe_output_dir / "audit_sampling_summary.json", result.safe_summary)


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
    destination.parent.mkdir(parents=True, exist_ok=True)
    queue.to_csv(destination, index=False)


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
    if "reader_id" not in study_annotations.columns:
        return pd.DataFrame(columns=["outcome", "n_pairs", "raw_agreement", "cohen_kappa", "gwet_ac1"])
    rows: list[dict[str, Any]] = []
    for outcome in STUDY_OUTCOMES:
        if outcome not in study_annotations.columns:
            continue
        paired = study_annotations[["audit_id", "reader_id", outcome]].dropna(subset=[outcome]).copy()
        paired = paired.sort_values(["audit_id", "reader_id"])
        left: list[str] = []
        right: list[str] = []
        for _, group in paired.groupby("audit_id"):
            values = group[outcome].astype(str).tolist()
            if len(values) >= 2:
                left.append(values[0])
                right.append(values[1])
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
            columns=["target", "outcome", "n_clips", "n_studies", "proportion", "ci_low", "ci_high", "ci_method"]
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
                    "ci_method": "study_cluster_percentile_bootstrap",
                    "bootstrap_n": int(n_bootstrap),
                }
            )
    return pd.DataFrame(rows)


def aggregate_audit_annotations(
    study_annotations: pd.DataFrame,
    clip_annotations: pd.DataFrame,
    linkage: pd.DataFrame,
    sampling_design: pd.DataFrame,
    config: Mapping[str, Any],
    config_hash: str,
    n_bootstrap: int = 2000,
) -> AuditAggregateResult:
    validate_audit_config(config)
    require_columns(sampling_design, ["target", "split", "source_n", "sample_n", "design_weight"], "sampling design")
    if n_bootstrap <= 0:
        raise ValueError("n_bootstrap must be positive")
    for column in STUDY_OUTCOMES:
        if column in study_annotations.columns:
            invalid = sorted(set(study_annotations[column].dropna().astype(str).str.lower()) - PRESENCE_VALUES - {""})
            if invalid:
                raise ValueError(f"Invalid values for {column}: {invalid}")
    if "acquisition_content_type" in clip_annotations.columns:
        invalid_content = sorted(
            set(clip_annotations["acquisition_content_type"].dropna().astype(str).str.lower()) - CONTENT_TYPES - {""}
        )
        if invalid_content:
            raise ValueError(f"Invalid acquisition content types: {invalid_content}")
    study_summary = _study_summaries(study_annotations, linkage, sampling_design)
    agreement = _agreement_summaries(study_annotations)
    clip_summary = _clip_summaries(clip_annotations, linkage, n_bootstrap, int(config["seed"]))
    for label, frame in (
        ("study audit summary", study_summary),
        ("clip audit summary", clip_summary),
        ("reader agreement", agreement),
    ):
        assert_export_safe_frame(frame, label)
    reconstruction_failures = 0
    if "reconstruction_success" in clip_annotations.columns:
        reconstruction_failures = int(
            clip_annotations["reconstruction_success"].astype(str).str.lower().eq("no").sum()
        )
    summary = {
        "protocol_version": config["protocol_version"],
        "configuration_sha256": config_hash,
        "bootstrap_n": int(n_bootstrap),
        "bootstrap_seed": int(config["seed"]),
        "study_summary_rows": int(len(study_summary)),
        "clip_summary_rows": int(len(clip_summary)),
        "agreement_rows": int(len(agreement)),
        "reconstruction_failure_count": reconstruction_failures,
        "row_level_annotations_exported": False,
        "candidate_values_exported": False,
    }
    return AuditAggregateResult(study_summary, clip_summary, agreement, summary)


def write_audit_aggregates(result: AuditAggregateResult, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    write_safe_csv(output_dir / "manual_audit_study_summary.csv", result.study_summary, "study audit summary")
    write_safe_csv(output_dir / "manual_audit_clip_summary.csv", result.clip_summary, "clip audit summary")
    write_safe_csv(output_dir / "manual_audit_reader_agreement.csv", result.agreement, "reader agreement")
    write_json(output_dir / "manual_audit_summary.json", result.summary)
