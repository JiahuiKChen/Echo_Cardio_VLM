"""Prospectively amended, reduced JDIM input-content audit roster.

This module derives a 15-assignment-per-target audit from the immutable parent
roster. It never reads image pixels or uses annotation content for sampling.
"""
from __future__ import annotations

import itertools
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import pandas as pd

from .audit_interface import (
    REQUIRED_CLIP_ANNOTATION_FIELDS,
    REQUIRED_STUDY_ANNOTATION_FIELDS,
)
from .safety import (
    BLOCKED_LINEAGE,
    Tier1BlockedError,
    canonical_id_set_sha256,
    require_columns,
    require_restricted_destination,
    safe_file_record,
    sha256_file,
    sha256_json,
    write_json,
)


PROTOCOL_NAME = "JDIM_INPUT_CONTENT_AUDIT_AMENDMENT_V2_15_PER_TARGET"
PROTOCOL_SCHEMA = "jdim-reduced-input-content-audit-v2"
PROTOCOL_STATUS = "REDUCED_AUDIT_PROTOCOL_LOCKED"
PILOT_STATUS = "INTERFACE_FAMILIARIZATION_PILOT_NOT_FOR_FINAL_ANALYSIS"
BLOCKED_REDUCED_SAMPLE_SELECTION = "BLOCKED_REDUCED_SAMPLE_SELECTION"
BLOCKED_PILOT_ANNOTATION_ISOLATION = "BLOCKED_PILOT_ANNOTATION_ISOLATION"
BLOCKED_RELIABILITY_SUBSET = "BLOCKED_RELIABILITY_SUBSET"

LVOT_VTI = "lvot_vti"
TAPSE = "tapse"
TARGETS = (LVOT_VTI, TAPSE)
TIER_A = "EXACT_MODEL_INPUT"
TIER_C = "SOURCE_ACQUISITION_ONLY"
FORMAL_RELIABILITY_N = 8
TARGET_ASSIGNMENT_N = 15

LOCKED_PARENT_SHA256 = {
    "audit_roster_lock": "5c9d4ac72b62c6bec6fa09735535c0b2625f8de09572e7006dd91c46df5019ef",
    "audit_linkage": "94d5142bf44045903f008f614d8ae24999d93d1700054bac9b5c4f0aa094b667",
    "canonical_clip_roster": "4220c60eacc201c9de4193719fc79b4cd78289909f7169ba618ecbb8af52c0eb",
    "parent_reader_manifest": "d042fbc621fed3a97c2f68581e7983e46d3b559c32dfa47c45cedb886ea3b0a6",
    "parent_second_reader_manifest": "d130fa54e1142323fd515ccc72d162ae7f6c4c31fe8887b67a3cd339dbb1ec64",
    "ready_certificate": "327e80fac770b3e34b54c54289c373df220cebf5544f41b0414d431adcb3a0c4",
    "technical_lock_certificate": "9969b35c06ec929c190fa48c14e06ea85f2d4bd3b3dacf5297bf8ca1ae74d7ce",
    "technical_interface_manifest": "6eaefcf9f34d57972256129e0877243b2569872ef854e7871e8a3b9612595584",
    "parent_primary_interface_manifest": "5c1df84a5cb7b59c9370fa5bcda6b7a98d215255f826372c293ece27d19f6428",
    "parent_secondary_interface_manifest": "8ec9336ddfc194e08d449f92908637df15778bfff6683f998ffcd089298cd164",
    "parent_interface_policy": "cd6f4bcc11a3319ddfe235569b1fe8d91e5bb0071cb462ca4fe75496d543f8a6",
}


@dataclass(frozen=True)
class ParentAuditPaths:
    audit_roster_lock: Path
    audit_linkage: Path
    canonical_clip_roster: Path
    parent_reader_manifest: Path
    parent_second_reader_manifest: Path
    ready_certificate: Path
    technical_lock_certificate: Path
    technical_interface_manifest: Path
    parent_primary_interface_manifest: Path
    parent_secondary_interface_manifest: Path
    parent_interface_policy: Path
    parent_media_root: Path
    pilot_checkpoint: Path


@dataclass(frozen=True)
class PilotCheckpointSummary:
    checkpoint_sha256: str
    contains_entries: bool
    study_records: int
    clip_records: int
    incomplete_study_records: int
    incomplete_clip_records: int
    structurally_empty_records: int
    substantive_studies: int
    pilot_audit_id: str

    def aggregate_safe(self) -> dict[str, Any]:
        return {
            "status": PILOT_STATUS,
            "checkpoint_sha256": self.checkpoint_sha256,
            "contains_entries": self.contains_entries,
            "study_records": self.study_records,
            "clip_records": self.clip_records,
            "incomplete_study_records": self.incomplete_study_records,
            "incomplete_clip_records": self.incomplete_clip_records,
            "structurally_empty_records": self.structurally_empty_records,
            "substantive_studies": self.substantive_studies,
            "excluded_from_final_analysis": True,
            "excluded_from_agreement": True,
            "excluded_from_adjudication": True,
        }


@dataclass(frozen=True)
class ReducedAuditResult:
    assignments: pd.DataFrame
    linkage: pd.DataFrame
    clip_roster: pd.DataFrame
    technical_manifest: pd.DataFrame
    formal_reliability: pd.DataFrame
    pilot: PilotCheckpointSummary
    parent_hashes: dict[str, str]
    summary: dict[str, Any]
    replacement_records: list[dict[str, Any]]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected a JSON object: {path.name}")
    return payload


def validate_parent_artifacts(paths: ParentAuditPaths) -> dict[str, str]:
    observed: dict[str, str] = {}
    for role, expected in LOCKED_PARENT_SHA256.items():
        path = getattr(paths, role)
        if not path.is_file():
            raise Tier1BlockedError(BLOCKED_LINEAGE, f"locked parent artifact is missing: {role}")
        observed[role] = sha256_file(path)
        if observed[role] != expected:
            raise Tier1BlockedError(BLOCKED_LINEAGE, f"locked parent artifact changed: {role}")
    if not paths.parent_media_root.is_dir():
        raise Tier1BlockedError(BLOCKED_LINEAGE, "protected parent media root is missing")
    if not paths.pilot_checkpoint.is_file():
        raise Tier1BlockedError(
            BLOCKED_PILOT_ANNOTATION_ISOLATION,
            "preserved full-roster pilot checkpoint is missing",
        )

    ready = _read_json(paths.ready_certificate)
    expected_ready = {
        "status": ready.get("status") == "READY_FOR_BLINDED_HUMAN_AUDIT",
        "source_commit": ready.get("source_commit")
        == "4e0ec3248b88ed8286de3a91b589627bb2da09b6",
        "technical_lock": ready.get("technical_lock_status")
        == "AUDIT_INPUTS_TECHNICALLY_LOCKED",
        "parent_studies": ready.get("primary_studies") == 116,
        "parent_clips": ready.get("clips") == 5071,
        "parent_secondary": ready.get("second_reader_studies") == 24,
        "technical_manifest": ready.get("technical_manifest_sha256")
        == observed["technical_interface_manifest"],
        "primary_manifest": ready.get("primary_package_sha256")
        == observed["parent_primary_interface_manifest"],
        "secondary_manifest": ready.get("second_reader_package_sha256")
        == observed["parent_secondary_interface_manifest"],
        "interface_policy": ready.get("interface_policy_sha256")
        == observed["parent_interface_policy"],
        "no_ocr": ready.get("ocr_used") is False,
        "no_annotations": ready.get("clinical_annotations_generated") is False,
        "no_public_binding": ready.get("public_network_binding_required") is False,
    }
    if not all(expected_ready.values()):
        failed = sorted(key for key, passed in expected_ready.items() if not passed)
        raise Tier1BlockedError(BLOCKED_LINEAGE, f"parent ready certificate failed: {failed}")
    return observed


def _record_nonempty_count(record: Any) -> int:
    if not isinstance(record, Mapping):
        raise Tier1BlockedError(
            BLOCKED_PILOT_ANNOTATION_ISOLATION,
            "pilot checkpoint annotation record is malformed",
        )
    return sum(bool(str(value).strip()) for value in record.values())


def _record_incomplete(record: Any, required: set[str]) -> bool:
    if not isinstance(record, Mapping):
        return True
    return any(not str(record.get(field, "")).strip() for field in required)


def classify_pilot_checkpoint(
    checkpoint_path: Path,
    technical_manifest: pd.DataFrame,
) -> PilotCheckpointSummary:
    payload = _read_json(checkpoint_path)
    annotations = payload.get("annotations", {})
    if not isinstance(annotations, Mapping) or set(annotations) - {"studies", "clips"}:
        raise Tier1BlockedError(
            BLOCKED_PILOT_ANNOTATION_ISOLATION,
            "pilot checkpoint annotation schema is invalid",
        )
    studies = annotations.get("studies", {})
    clips = annotations.get("clips", {})
    if not isinstance(studies, Mapping) or not isinstance(clips, Mapping):
        raise Tier1BlockedError(
            BLOCKED_PILOT_ANNOTATION_ISOLATION,
            "pilot checkpoint study or clip records are invalid",
        )
    require_columns(
        technical_manifest,
        ["audit_id", "clip_audit_id"],
        "parent technical interface manifest",
    )
    clip_to_study = dict(
        zip(
            technical_manifest["clip_audit_id"].astype(str),
            technical_manifest["audit_id"].astype(str),
        )
    )
    unknown_clips = sorted(set(map(str, clips)) - set(clip_to_study))
    if unknown_clips:
        raise Tier1BlockedError(
            BLOCKED_PILOT_ANNOTATION_ISOLATION,
            "pilot checkpoint contains clips outside the locked parent manifest",
        )

    structural_counts: dict[str, int] = {}
    for audit_id, record in studies.items():
        structural_counts[str(audit_id)] = structural_counts.get(str(audit_id), 0) + _record_nonempty_count(record)
    for clip_id, record in clips.items():
        audit_id = clip_to_study[str(clip_id)]
        structural_counts[audit_id] = structural_counts.get(audit_id, 0) + _record_nonempty_count(record)
    substantive = sorted(audit_id for audit_id, count in structural_counts.items() if count > 0)
    if len(substantive) != 1:
        raise Tier1BlockedError(
            BLOCKED_PILOT_ANNOTATION_ISOLATION,
            "pilot checkpoint must identify exactly one substantive familiarization study",
        )
    pilot_id = substantive[0]
    if pilot_id not in set(technical_manifest["audit_id"].astype(str)):
        raise Tier1BlockedError(
            BLOCKED_PILOT_ANNOTATION_ISOLATION,
            "pilot study is outside the locked parent roster",
        )
    return PilotCheckpointSummary(
        checkpoint_sha256=sha256_file(checkpoint_path),
        contains_entries=bool(studies or clips),
        study_records=len(studies),
        clip_records=len(clips),
        incomplete_study_records=sum(
            _record_incomplete(record, REQUIRED_STUDY_ANNOTATION_FIELDS)
            for record in studies.values()
        ),
        incomplete_clip_records=sum(
            _record_incomplete(record, REQUIRED_CLIP_ANNOTATION_FIELDS)
            for record in clips.values()
        ),
        structurally_empty_records=sum(count == 0 for count in structural_counts.values()),
        substantive_studies=1,
        pilot_audit_id=pilot_id,
    )


def _target_memberships(value: Any) -> set[str]:
    return {item.strip() for item in str(value).split(";") if item.strip()}


def _study_tiers(technical_manifest: pd.DataFrame) -> pd.Series:
    require_columns(
        technical_manifest,
        ["audit_id", "clip_audit_id", "evidence_tier"],
        "parent technical interface manifest",
    )
    if technical_manifest[["audit_id", "clip_audit_id"]].astype(str).duplicated().any():
        raise Tier1BlockedError(BLOCKED_LINEAGE, "parent technical manifest contains duplicate clips")
    grouped = technical_manifest.groupby(technical_manifest["audit_id"].astype(str))["evidence_tier"].agg(
        lambda values: sorted(set(map(str, values)))
    )
    mixed = grouped[grouped.map(len).ne(1)]
    if not mixed.empty:
        raise Tier1BlockedError(
            BLOCKED_REDUCED_SAMPLE_SELECTION,
            "a parent study spans multiple evidence tiers",
        )
    result = grouped.map(lambda values: values[0])
    if not set(result).issubset({TIER_A, TIER_C}):
        raise Tier1BlockedError(
            BLOCKED_REDUCED_SAMPLE_SELECTION,
            "parent roster contains an unsupported evidence tier",
        )
    return result.rename("evidence_tier")


def _ordered_target_stratum(
    studies: pd.DataFrame,
    target: str,
    tier: str,
) -> pd.DataFrame:
    membership = studies["target_membership"].map(_target_memberships)
    result = studies.loc[membership.map(lambda values: target in values) & studies["evidence_tier"].eq(tier)].copy()
    return result.sort_values(["review_order", "audit_id"], kind="mergesort").reset_index(drop=True)


def _force_include_pilot(
    assignments: pd.DataFrame,
    studies: pd.DataFrame,
    pilot_audit_id: str,
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    pilot_rows = studies.loc[studies["audit_id"].astype(str).eq(pilot_audit_id)]
    if len(pilot_rows) != 1:
        raise Tier1BlockedError(
            BLOCKED_PILOT_ANNOTATION_ISOLATION,
            "pilot study is absent or duplicated in parent linkage",
        )
    pilot = pilot_rows.iloc[0]
    memberships = _target_memberships(pilot["target_membership"])
    tier = str(pilot["evidence_tier"])
    replacements: list[dict[str, Any]] = []
    result = assignments.copy()
    for target in TARGETS:
        if target not in memberships:
            continue
        target_ids = set(result.loc[result["target"].eq(target), "audit_id"].astype(str))
        if pilot_audit_id in target_ids:
            continue
        candidates = result.loc[
            result["target"].eq(target)
            & result["evidence_tier"].eq(tier)
            & ~result["audit_id"].astype(str).eq(pilot_audit_id)
        ].sort_values(["parent_review_order", "audit_id"], ascending=[False, False])
        if candidates.empty:
            raise Tier1BlockedError(
                BLOCKED_REDUCED_SAMPLE_SELECTION,
                "pilot study cannot be included within its locked target/tier stratum",
            )
        removed = candidates.iloc[0]
        drop_mask = result["target"].eq(target) & result["audit_id"].astype(str).eq(
            str(removed["audit_id"])
        )
        result = result.loc[~drop_mask].copy()
        result = pd.concat(
            [
                result,
                pd.DataFrame(
                    [
                        {
                            "target": target,
                            "audit_id": pilot_audit_id,
                            "evidence_tier": tier,
                            "parent_review_order": int(pilot["review_order"]),
                            "selection_reason": "pilot_force_include_same_target_tier",
                        }
                    ]
                ),
            ],
            ignore_index=True,
        )
        replacements.append(
            {
                "target": target,
                "evidence_tier": tier,
                "included_audit_id": pilot_audit_id,
                "replaced_audit_id": str(removed["audit_id"]),
                "rule": "replace_last_selected_same_target_tier_by_locked_order",
            }
        )
    return result, replacements


def select_reduced_assignments(
    linkage: pd.DataFrame,
    technical_manifest: pd.DataFrame,
    pilot_audit_id: str,
) -> tuple[pd.DataFrame, pd.DataFrame, list[dict[str, Any]]]:
    require_columns(
        linkage,
        ["audit_id", "study_id", "subject_id", "target_membership", "review_order"],
        "parent audit linkage",
    )
    if len(linkage) != 116 or linkage["audit_id"].astype(str).duplicated().any():
        raise Tier1BlockedError(BLOCKED_LINEAGE, "parent linkage identity differs from the locked roster")
    studies = linkage.copy()
    studies["audit_id"] = studies["audit_id"].astype(str)
    studies["review_order"] = pd.to_numeric(studies["review_order"], errors="raise").astype(int)
    tiers = _study_tiers(technical_manifest)
    studies = studies.merge(tiers, left_on="audit_id", right_index=True, validate="one_to_one")

    lvot_a = _ordered_target_stratum(studies, LVOT_VTI, TIER_A)
    lvot_c = _ordered_target_stratum(studies, LVOT_VTI, TIER_C)
    tapse_c = _ordered_target_stratum(studies, TAPSE, TIER_C)
    if len(lvot_a) != 7 or len(lvot_c) != 53 or len(tapse_c) != 60:
        raise Tier1BlockedError(
            BLOCKED_REDUCED_SAMPLE_SELECTION,
            "parent target/evidence-tier counts differ from the locked amendment premise",
        )

    rows: list[dict[str, Any]] = []
    for target, tier, frame, count, reason in (
        (LVOT_VTI, TIER_A, lvot_a, 7, "all_available_tier_a"),
        (LVOT_VTI, TIER_C, lvot_c, 8, "first_by_locked_parent_order"),
        (TAPSE, TIER_C, tapse_c, 15, "first_by_locked_parent_order"),
    ):
        for row in frame.head(count).itertuples(index=False):
            rows.append(
                {
                    "target": target,
                    "audit_id": str(row.audit_id),
                    "evidence_tier": tier,
                    "parent_review_order": int(row.review_order),
                    "selection_reason": reason,
                }
            )
    assignments = pd.DataFrame(rows)
    assignments, replacements = _force_include_pilot(assignments, studies, pilot_audit_id)
    assignments = assignments.sort_values(
        ["target", "evidence_tier", "parent_review_order", "audit_id"],
        kind="mergesort",
    ).reset_index(drop=True)
    assignments["target_selection_order"] = assignments.groupby("target").cumcount() + 1

    expected = {
        (LVOT_VTI, TIER_A): 7,
        (LVOT_VTI, TIER_C): 8,
        (TAPSE, TIER_C): 15,
    }
    observed = assignments.groupby(["target", "evidence_tier"]).size().to_dict()
    if observed != expected or assignments.groupby("target").size().to_dict() != {
        LVOT_VTI: TARGET_ASSIGNMENT_N,
        TAPSE: TARGET_ASSIGNMENT_N,
    }:
        raise Tier1BlockedError(
            BLOCKED_REDUCED_SAMPLE_SELECTION,
            "reduced target/tier assignments do not match the prospective amendment",
        )
    if pilot_audit_id not in set(assignments["audit_id"].astype(str)):
        raise Tier1BlockedError(
            BLOCKED_REDUCED_SAMPLE_SELECTION,
            "substantive pilot study was not retained in the reduced audit",
        )
    return assignments, studies, replacements


def _constraint_targets(pool: pd.DataFrame) -> dict[str, int]:
    tier_a_available = int(pool["evidence_tier"].eq(TIER_A).sum())
    overlap_available = int(pool["target_membership"].map(_target_memberships).map(len).gt(1).sum())
    targets: dict[str, int] = {
        "tier_a": 2 if tier_a_available >= 2 else tier_a_available,
        "overlap": 1 if overlap_available else 0,
    }
    for target in TARGETS:
        available = int(pool["target_membership"].map(_target_memberships).map(lambda values: target in values).sum())
        targets[target] = 3 if available >= 3 else available
    return targets


def _composition_satisfies(frame: pd.DataFrame, required: Mapping[str, int]) -> bool:
    membership = frame["target_membership"].map(_target_memberships)
    return (
        int(frame["evidence_tier"].eq(TIER_A).sum()) >= int(required["tier_a"])
        and int(membership.map(len).gt(1).sum()) >= int(required["overlap"])
        and all(
            int(membership.map(lambda values, target=target: target in values).sum())
            >= int(required[target])
            for target in TARGETS
        )
    )


def _first_feasible_combination(
    fixed: pd.DataFrame,
    candidates: pd.DataFrame,
    choose_n: int,
    required: Mapping[str, int],
) -> pd.DataFrame | None:
    if choose_n == 0:
        return fixed.copy() if _composition_satisfies(fixed, required) else None
    fixed_membership = fixed["target_membership"].map(_target_memberships)
    fixed_counts = {
        "tier_a": int(fixed["evidence_tier"].eq(TIER_A).sum()),
        "overlap": int(fixed_membership.map(len).gt(1).sum()),
        **{
            target: int(
                fixed_membership.map(lambda values, target=target: target in values).sum()
            )
            for target in TARGETS
        },
    }
    candidate_records = []
    for index, row in candidates.iterrows():
        membership = _target_memberships(row["target_membership"])
        candidate_records.append(
            (
                index,
                int(str(row["evidence_tier"]) == TIER_A),
                int(len(membership) > 1),
                int(LVOT_VTI in membership),
                int(TAPSE in membership),
            )
        )
    for records in itertools.combinations(candidate_records, choose_n):
        totals = {
            "tier_a": fixed_counts["tier_a"] + sum(record[1] for record in records),
            "overlap": fixed_counts["overlap"] + sum(record[2] for record in records),
            LVOT_VTI: fixed_counts[LVOT_VTI] + sum(record[3] for record in records),
            TAPSE: fixed_counts[TAPSE] + sum(record[4] for record in records),
        }
        if all(totals[key] >= int(required[key]) for key in totals):
            indices = [record[0] for record in records]
            return pd.concat([fixed, candidates.loc[indices]], ignore_index=False)
    return None


def select_formal_reliability_subset(
    selected_studies: pd.DataFrame,
    parent_second_reader_manifest: pd.DataFrame,
) -> pd.DataFrame:
    require_columns(selected_studies, ["audit_id", "review_order", "evidence_tier", "target_membership"], "reduced studies")
    require_columns(parent_second_reader_manifest, ["audit_id"], "parent second-reader manifest")
    if len(parent_second_reader_manifest) != 24:
        raise Tier1BlockedError(BLOCKED_LINEAGE, "parent second-reader roster no longer contains 24 studies")
    selected = selected_studies.copy()
    selected["audit_id"] = selected["audit_id"].astype(str)
    selected = selected.sort_values(["review_order", "audit_id"], kind="mergesort")
    parent_secondary_ids = set(parent_second_reader_manifest["audit_id"].astype(str))
    selected["in_parent_second_reader_subset"] = selected["audit_id"].isin(parent_secondary_ids)
    preferred = selected.loc[selected["in_parent_second_reader_subset"]].copy()
    remaining = selected.loc[~selected["in_parent_second_reader_subset"]].copy()
    if len(selected) < FORMAL_RELIABILITY_N:
        raise Tier1BlockedError(BLOCKED_RELIABILITY_SUBSET, "reduced sample has fewer than eight physical studies")

    if len(preferred) >= FORMAL_RELIABILITY_N:
        fixed = preferred.iloc[0:0].copy()
        pool = preferred
        choose_n = FORMAL_RELIABILITY_N
    else:
        fixed = preferred
        pool = remaining
        choose_n = FORMAL_RELIABILITY_N - len(fixed)
    allowed = pd.concat([fixed, pool], ignore_index=False)
    required = _constraint_targets(allowed)
    formal = _first_feasible_combination(fixed, pool, choose_n, required)
    jointly_feasible = formal is not None
    if formal is None:
        formal = pd.concat([fixed, pool.head(choose_n)], ignore_index=False)
    formal = formal.sort_values(["review_order", "audit_id"], kind="mergesort").copy()
    if len(formal) != FORMAL_RELIABILITY_N or formal["audit_id"].duplicated().any():
        raise Tier1BlockedError(BLOCKED_RELIABILITY_SUBSET, "formal reliability subset is not eight unique studies")
    formal["formal_reliability_order"] = range(1, FORMAL_RELIABILITY_N + 1)
    formal["selection_source"] = formal["in_parent_second_reader_subset"].map(
        {True: "locked_parent_second_reader_subset", False: "deterministic_reduced_sample_supplement"}
    )
    formal["composition_constraints_jointly_feasible"] = jointly_feasible
    return formal[
        [
            "audit_id",
            "review_order",
            "evidence_tier",
            "target_membership",
            "in_parent_second_reader_subset",
            "formal_reliability_order",
            "selection_source",
            "composition_constraints_jointly_feasible",
        ]
    ].reset_index(drop=True)


def build_reduced_audit_result(paths: ParentAuditPaths) -> ReducedAuditResult:
    parent_hashes = validate_parent_artifacts(paths)
    linkage = pd.read_csv(paths.audit_linkage).fillna("")
    clip_roster = pd.read_csv(paths.canonical_clip_roster).fillna("")
    technical = pd.read_csv(paths.technical_interface_manifest).fillna("")
    parent_second = pd.read_csv(paths.parent_second_reader_manifest).fillna("")
    pilot = classify_pilot_checkpoint(paths.pilot_checkpoint, technical)
    assignments, studies, replacements = select_reduced_assignments(
        linkage,
        technical,
        pilot.pilot_audit_id,
    )
    selected_ids = set(assignments["audit_id"].astype(str))
    selected_studies = studies.loc[studies["audit_id"].astype(str).isin(selected_ids)].copy()
    selected_membership = (
        assignments.groupby("audit_id")["target"]
        .agg(lambda values: ";".join(sorted(set(map(str, values)))))
        .to_dict()
    )
    selected_studies = selected_studies.rename(
        columns={"target_membership": "parent_target_membership"}
    )
    selected_studies["target_membership"] = selected_studies["audit_id"].map(
        selected_membership
    )
    selected_studies = selected_studies.sort_values(["review_order", "audit_id"], kind="mergesort")
    selected_studies["reduced_review_order"] = range(1, len(selected_studies) + 1)
    reduced_clips = clip_roster.loc[clip_roster["audit_id"].astype(str).isin(selected_ids)].copy()
    reduced_technical = technical.loc[technical["audit_id"].astype(str).isin(selected_ids)].copy()
    clip_keys = set(
        map(tuple, reduced_clips[["audit_id", "clip_audit_id"]].astype(str).to_numpy())
    )
    technical_keys = set(
        map(tuple, reduced_technical[["audit_id", "clip_audit_id"]].astype(str).to_numpy())
    )
    if clip_keys != technical_keys or set(reduced_clips["audit_id"].astype(str)) != selected_ids:
        raise Tier1BlockedError(
            BLOCKED_REDUCED_SAMPLE_SELECTION,
            "reduced roster does not contain every canonical clip",
        )
    if reduced_clips[["audit_id", "clip_audit_id"]].astype(str).duplicated().any():
        raise Tier1BlockedError(BLOCKED_LINEAGE, "reduced canonical clip roster contains duplicates")

    media_tokens = {
        token
        for column in ("source_media_id", "model_input_media_id")
        for token in reduced_technical[column].astype(str)
        if token
    }
    missing_media = sorted(
        token for token in media_tokens if not (paths.parent_media_root / f"{token}.png").is_file()
    )
    if missing_media:
        raise Tier1BlockedError(
            BLOCKED_REDUCED_SAMPLE_SELECTION,
            "selected reduced-audit media are missing from the protected parent package",
        )

    formal = select_formal_reliability_subset(selected_studies, parent_second)
    membership = selected_studies["target_membership"].map(_target_memberships)
    formal_membership = formal["target_membership"].map(_target_memberships)
    summary = {
        "protocol_name": PROTOCOL_NAME,
        "status": PROTOCOL_STATUS,
        "target_assignments": {target: int(assignments["target"].eq(target).sum()) for target in TARGETS},
        "lvot_tier_a_assignments": int(
            (assignments["target"].eq(LVOT_VTI) & assignments["evidence_tier"].eq(TIER_A)).sum()
        ),
        "lvot_tier_c_assignments": int(
            (assignments["target"].eq(LVOT_VTI) & assignments["evidence_tier"].eq(TIER_C)).sum()
        ),
        "tapse_tier_c_assignments": int(
            (assignments["target"].eq(TAPSE) & assignments["evidence_tier"].eq(TIER_C)).sum()
        ),
        "unique_physical_studies": int(len(selected_studies)),
        "cross_target_overlap_studies": int(membership.map(len).gt(1).sum()),
        "canonical_clips": int(len(reduced_clips)),
        "tier_a_studies": int(selected_studies["evidence_tier"].eq(TIER_A).sum()),
        "tier_c_studies": int(selected_studies["evidence_tier"].eq(TIER_C).sum()),
        "formal_reliability_studies": int(len(formal)),
        "formal_tier_a_studies": int(formal["evidence_tier"].eq(TIER_A).sum()),
        "formal_tier_c_studies": int(formal["evidence_tier"].eq(TIER_C).sum()),
        "formal_cross_target_studies": int(formal_membership.map(len).gt(1).sum()),
        "formal_lvot_assignments": int(formal_membership.map(lambda values: LVOT_VTI in values).sum()),
        "formal_tapse_assignments": int(formal_membership.map(lambda values: TAPSE in values).sum()),
        "formal_parent_second_reader_studies": int(formal["in_parent_second_reader_subset"].sum()),
        "formal_selection_before_production_resume": True,
        "named_physicians_preassigned": False,
        "pilot_force_included": pilot.pilot_audit_id in selected_ids,
        "pilot_prior_responses_reused": False,
        "protected_media_reused": True,
        "protected_media_copied": False,
        "clinical_content_used_for_selection": False,
        "annotations_used_for_selection": False,
        "ocr_used": False,
        "automated_annotation": False,
        "reduced_physical_study_set_sha256": canonical_id_set_sha256(selected_ids),
        "formal_reliability_set_sha256": canonical_id_set_sha256(formal["audit_id"]),
    }
    return ReducedAuditResult(
        assignments=assignments,
        linkage=selected_studies,
        clip_roster=reduced_clips,
        technical_manifest=reduced_technical,
        formal_reliability=formal,
        pilot=pilot,
        parent_hashes=parent_hashes,
        summary=summary,
        replacement_records=replacements,
    )


def _restricted_protocol_payload(
    result: ReducedAuditResult,
    *,
    created_at: str,
    source_commit: str,
) -> dict[str, Any]:
    return {
        "schema_version": PROTOCOL_SCHEMA,
        "protocol_name": PROTOCOL_NAME,
        "created_at_utc": created_at,
        "source_commit": source_commit,
        "status": PROTOCOL_STATUS,
        "parent_artifact_sha256": result.parent_hashes,
        "selection_algorithm": {
            "source": "immutable_parent_60_assignment_per_target_roster",
            "order": "locked_parent_review_order_then_opaque_audit_id",
            "lvot_vti": "all_7_tier_a_plus_first_8_tier_c",
            "tapse": "first_15_tier_c",
            "pilot": "force_include_unique_substantive_pilot_with_same_target_tier_replacement",
            "cross_target": "count_each_target_assignment_review_physical_study_once",
            "all_clips_required": True,
        },
        "summary": result.summary,
        "pilot": {
            **result.pilot.aggregate_safe(),
            "pilot_audit_id": result.pilot.pilot_audit_id,
            "fresh_review_required": True,
        },
        "replacement_records": result.replacement_records,
        "assignments": result.assignments.to_dict(orient="records"),
        "formal_reliability": result.formal_reliability.to_dict(orient="records"),
    }


def write_reduced_audit_roster(
    result: ReducedAuditResult,
    output_root: Path,
    *,
    source_commit: str,
    created_at: str | None = None,
) -> dict[str, Any]:
    output_root = require_restricted_destination(output_root)
    if output_root.exists():
        raise FileExistsError("refusing to overwrite a reduced-audit output root")
    created_at = created_at or utc_now()
    restricted = output_root / "restricted" / "roster"
    safe = output_root / "aggregate_safe"
    restricted.mkdir(parents=True, mode=0o700)
    safe.mkdir(parents=True, mode=0o700)
    os.chmod(output_root, 0o700)
    os.chmod(output_root / "restricted", 0o700)
    os.chmod(restricted, 0o700)
    os.chmod(safe, 0o700)

    result.assignments.to_csv(restricted / "reduced_target_assignments_restricted.csv", index=False)
    result.linkage.to_csv(restricted / "reduced_audit_linkage_restricted.csv", index=False)
    result.clip_roster.to_csv(restricted / "reduced_canonical_clip_roster_restricted.csv", index=False)
    result.technical_manifest.to_csv(
        restricted / "reduced_technical_interface_manifest_restricted.csv",
        index=False,
    )
    result.formal_reliability.to_csv(
        restricted / "formal_reliability_subset_restricted.csv",
        index=False,
    )
    pilot_payload = {
        **result.pilot.aggregate_safe(),
        "pilot_audit_id": result.pilot.pilot_audit_id,
        "fresh_review_required": True,
    }
    write_json(restricted / "pilot_checkpoint_classification_restricted.json", pilot_payload)
    protocol_path = restricted / "protocol_amendment_restricted.json"
    write_json(
        protocol_path,
        _restricted_protocol_payload(
            result,
            created_at=created_at,
            source_commit=source_commit,
        ),
    )
    for path in restricted.iterdir():
        if path.is_file():
            os.chmod(path, 0o600)

    artifact_paths = {
        "reduced_target_assignments": restricted / "reduced_target_assignments_restricted.csv",
        "reduced_audit_linkage": restricted / "reduced_audit_linkage_restricted.csv",
        "reduced_canonical_clip_roster": restricted / "reduced_canonical_clip_roster_restricted.csv",
        "reduced_technical_interface_manifest": restricted
        / "reduced_technical_interface_manifest_restricted.csv",
        "formal_reliability_subset": restricted / "formal_reliability_subset_restricted.csv",
        "pilot_checkpoint_classification": restricted
        / "pilot_checkpoint_classification_restricted.json",
        "protocol_amendment": protocol_path,
    }
    safe_certificate = {
        "schema_version": "jdim-reduced-audit-protocol-lock-v2",
        "status": PROTOCOL_STATUS,
        "protocol_name": PROTOCOL_NAME,
        "created_at_utc": created_at,
        "source_commit": source_commit,
        **result.summary,
        "parent_artifact_sha256": result.parent_hashes,
        "pilot": result.pilot.aggregate_safe(),
        "restricted_artifacts": [
            safe_file_record(role, path)
            for role, path in sorted(artifact_paths.items())
        ],
        "protocol_amendment_sha256": sha256_file(protocol_path),
    }
    write_json(safe / "reduced_audit_protocol_lock.json", safe_certificate)
    os.chmod(safe / "reduced_audit_protocol_lock.json", 0o600)
    return safe_certificate


def validate_locked_reduced_roster(output_root: Path) -> dict[str, Any]:
    output_root = require_restricted_destination(output_root)
    safe_path = output_root / "aggregate_safe" / "reduced_audit_protocol_lock.json"
    restricted = output_root / "restricted" / "roster"
    certificate = _read_json(safe_path)
    if certificate.get("status") != PROTOCOL_STATUS:
        raise Tier1BlockedError(BLOCKED_REDUCED_SAMPLE_SELECTION, "reduced protocol lock status is invalid")
    expected_roles = {
        "reduced_target_assignments",
        "reduced_audit_linkage",
        "reduced_canonical_clip_roster",
        "reduced_technical_interface_manifest",
        "formal_reliability_subset",
        "pilot_checkpoint_classification",
        "protocol_amendment",
    }
    records = certificate.get("restricted_artifacts", [])
    if not isinstance(records, list) or {record.get("logical_role") for record in records} != expected_roles:
        raise Tier1BlockedError(BLOCKED_REDUCED_SAMPLE_SELECTION, "reduced protocol artifact matrix is incomplete")
    filenames = {
        "reduced_target_assignments": "reduced_target_assignments_restricted.csv",
        "reduced_audit_linkage": "reduced_audit_linkage_restricted.csv",
        "reduced_canonical_clip_roster": "reduced_canonical_clip_roster_restricted.csv",
        "reduced_technical_interface_manifest": "reduced_technical_interface_manifest_restricted.csv",
        "formal_reliability_subset": "formal_reliability_subset_restricted.csv",
        "pilot_checkpoint_classification": "pilot_checkpoint_classification_restricted.json",
        "protocol_amendment": "protocol_amendment_restricted.json",
    }
    by_role = {str(record["logical_role"]): record for record in records}
    for role, filename in filenames.items():
        path = restricted / filename
        observed = safe_file_record(role, path)
        if observed != by_role[role]:
            raise Tier1BlockedError(BLOCKED_REDUCED_SAMPLE_SELECTION, f"locked reduced artifact changed: {role}")
    assignments = pd.read_csv(restricted / filenames["reduced_target_assignments"])
    linkage = pd.read_csv(restricted / filenames["reduced_audit_linkage"])
    clips = pd.read_csv(restricted / filenames["reduced_canonical_clip_roster"])
    formal = pd.read_csv(restricted / filenames["formal_reliability_subset"])
    checks = {
        "target_counts": assignments.groupby("target").size().to_dict()
        == {LVOT_VTI: TARGET_ASSIGNMENT_N, TAPSE: TARGET_ASSIGNMENT_N},
        "lvot_tier_a": int(
            (assignments["target"].eq(LVOT_VTI) & assignments["evidence_tier"].eq(TIER_A)).sum()
        )
        == 7,
        "lvot_tier_c": int(
            (assignments["target"].eq(LVOT_VTI) & assignments["evidence_tier"].eq(TIER_C)).sum()
        )
        == 8,
        "tapse_tier_c": int(
            (assignments["target"].eq(TAPSE) & assignments["evidence_tier"].eq(TIER_C)).sum()
        )
        == 15,
        "formal_eight": len(formal) == FORMAL_RELIABILITY_N,
        "unique_studies": linkage["audit_id"].astype(str).nunique() == len(linkage),
        "all_selected_have_clips": set(linkage["audit_id"].astype(str))
        == set(clips["audit_id"].astype(str)),
        "no_duplicate_clips": not clips[["audit_id", "clip_audit_id"]].astype(str).duplicated().any(),
    }
    if not all(checks.values()):
        failed = sorted(key for key, passed in checks.items() if not passed)
        raise Tier1BlockedError(BLOCKED_REDUCED_SAMPLE_SELECTION, f"locked reduced roster failed: {failed}")
    return {"status": PROTOCOL_STATUS, **checks}
