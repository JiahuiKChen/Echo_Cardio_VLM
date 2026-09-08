"""Versioned scoring-scope clarification for the reduced JDIM human audit.

This module changes only the human-review protocol and its persistence namespace.
It never reads image pixels, interprets annotation values, or changes the locked
sample, technical evidence, or protected media.
"""
from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import pandas as pd

from .audit import CONTENT_TYPES, PRESENCE_VALUES
from .reduced_audit import FORMAL_RELIABILITY_N, PROTOCOL_NAME, TIER_A, TIER_C
from .safety import (
    BLOCKED_LINEAGE,
    Tier1BlockedError,
    require_restricted_destination,
    sha256_file,
    sha256_json,
    write_json,
)


PROTOCOL_V3_NAME = "JDIM_INPUT_CONTENT_AUDIT_V3_SCORING_SCOPE_CLARIFIED"
PROTOCOL_V3_STATUS = "JDIM_INPUT_CONTENT_AUDIT_V3_SCORING_SCOPE_CLARIFIED"
PROTOCOL_V3_SCHEMA = "jdim-input-content-audit-protocol-v3"
PROTOCOL_V3_SIDE_BY_SIDE_ADDENDUM = "JDIM_AUDIT_V3_SIDE_BY_SIDE_DISPLAY_ADDENDUM_V1"
PROTOCOL_V3_BASE_COMMIT = "c9d1f2c5f84ad738437017626aad5cb9f86de903"
V3_SIDE_BY_SIDE_SCORING_VERIFIED = "V3_SIDE_BY_SIDE_SCORING_VERIFIED"
READY_FOR_V3_BLINDED_HUMAN_AUDIT = "READY_FOR_V3_BLINDED_HUMAN_AUDIT"
ACTIVE_PROTOCOL_SCHEMA = "jdim-active-audit-protocol-v1"
PROTOCOL_ARCHIVE_STATUS = "PROTOCOL_CLARIFICATION_PILOT_EXCLUDED"
PROTOCOL_ARCHIVE_SCHEMA = "jdim-protocol-clarification-archive-v1"
V3_QUEUE_SCHEMA = "jdim-reduced-audit-role-queue-v2"
V3_EVENT_SCHEMA = "jdim-reduced-audit-review-event-v2"
V3_CHECKPOINT_SCHEMA = "jdim-reduced-audit-study-checkpoint-v2"
V3_LOCK_SCHEMA = "jdim-reduced-audit-review-lock-v2"
V3_RESTART_SCHEMA = "jdim-reduced-audit-protocol-restart-v1"
V3_ROOT_NAME = "jdim_input_content_audit_v3_scoring_scope_clarified"
V3_DRY_RUN_PASS = "JDIM_INPUT_CONTENT_AUDIT_V3_DRY_RUN_PASS"
V3_ACTIVE = "JDIM_INPUT_CONTENT_AUDIT_V3_ACTIVE"

BASE_SCORING_SCOPE_BY_TIER = {
    TIER_A: (
        "Score the exact model input in the right panel. Use the source acquisition "
        "only for context. Do not count source-only features as model-input content."
    ),
    TIER_C: (
        "Score the source acquisition. Findings will be reported separately as "
        "source-acquisition evidence and not as verified model-input content."
    ),
}

SCORING_SCOPE_BY_TIER = {
    TIER_A: (
        "Score all primary audit fields from the exact model input in the right panel. "
        "Use the source acquisition on the left only for context and source-to-input "
        "comparison. Do not count features visible only in the source acquisition as "
        "model-input content."
    ),
    TIER_C: (
        "Score the source acquisition shown here. This clip is not a verified exact "
        "model input. Findings will be reported separately as source-acquisition "
        "evidence and must not be interpreted as content proven to have reached the encoder."
    ),
}

FIELD_DEFINITIONS = {
    "visible_text": (
        "Any readable alphabetic text, words, abbreviations, or units in the scored "
        "panel. Numbers alone do not count."
    ),
    "visible_numeric_value": (
        "Any readable number in the scored panel, including generic depth, scale, "
        "timing, or machine-setting numbers."
    ),
    "lvot_vti_specific_label": (
        "Explicit visible text unambiguously identifying LVOT VTI/TVI. Generic or "
        "ambiguous VTI should be Uncertain unless surrounding display context "
        "establishes LVOT VTI."
    ),
    "tapse_specific_label": (
        "Explicit visible text identifying TAPSE or its written-out equivalent."
    ),
    "candidate_target_value_present": (
        "A displayed number reasonably presented as an LVOT VTI or TAPSE measurement "
        "result based on label, unit, placement, or unambiguous measurement context. "
        "Exclude depth markers, heart rate, dates/times, frame numbers, velocity "
        "scales, gain, MI/TI, and other machine settings. Use Uncertain when the "
        "connection to either target is plausible but unclear."
    ),
    "waveform_or_measurement_tracing": (
        "A spectral Doppler waveform, M-mode tracing, or measurement contour. A "
        "routine ECG gating strip alone does not count."
    ),
    "calipers": "Visible calipers or measurement markers in the scored panel.",
}

SOURCE_ONLY_FIELD_DEFINITIONS = {
    "source_only_relevant_content": (
        "Is any potentially relevant measurement or annotation content visible in the "
        "source acquisition but not visible in the exact model input?"
    ),
    "source_only_visible_text": "Alphabetic text or abbreviation",
    "source_only_numeric_value": "Numeric value",
    "source_only_unit": "Unit",
    "source_only_measurement_name": "Measurement name",
    "source_only_lvot_vti_specific_label": "LVOT VTI-specific label",
    "source_only_tapse_specific_label": "TAPSE-specific label",
    "source_only_candidate_target_value": "Candidate target measurement value",
    "source_only_spectral_doppler_waveform": "Spectral Doppler waveform",
    "source_only_m_mode_tracing": "M-mode tracing",
    "source_only_caliper": "Caliper",
    "source_only_contour_or_measurement_trace": "Contour or measurement trace",
    "source_only_other_measurement_annotation": "Other measurement-related annotation",
}

V3_CLIP_PRESENCE_FIELDS = (
    "waveform_or_measurement_tracing",
    "calipers",
    "visible_text",
    "visible_numeric_value",
    "lvot_vti_specific_label",
    "tapse_specific_label",
    "candidate_target_value_present",
)
V3_CLIP_FREE_TEXT_FIELDS = (
    "candidate_target_value",
    "visible_unit_text",
    "visible_measurement_name_text",
    "display_precision",
    "restricted_notes",
)
V3_SOURCE_ONLY_PRIMARY_FIELD = "source_only_relevant_content"
V3_SOURCE_ONLY_CATEGORY_FIELDS = (
    "source_only_visible_text",
    "source_only_numeric_value",
    "source_only_unit",
    "source_only_measurement_name",
    "source_only_lvot_vti_specific_label",
    "source_only_tapse_specific_label",
    "source_only_candidate_target_value",
    "source_only_spectral_doppler_waveform",
    "source_only_m_mode_tracing",
    "source_only_caliper",
    "source_only_contour_or_measurement_trace",
    "source_only_other_measurement_annotation",
)
V3_SOURCE_ONLY_FREE_TEXT_FIELDS = (
    "source_only_candidate_target_value_text",
)
V3_SOURCE_ONLY_CLIP_FIELDS = {
    V3_SOURCE_ONLY_PRIMARY_FIELD,
    *V3_SOURCE_ONLY_CATEGORY_FIELDS,
    *V3_SOURCE_ONLY_FREE_TEXT_FIELDS,
}
V3_CLIP_ANNOTATION_FIELDS = {
    "acquisition_content_type",
    *V3_CLIP_PRESENCE_FIELDS,
    *V3_CLIP_FREE_TEXT_FIELDS,
    *V3_SOURCE_ONLY_CLIP_FIELDS,
    "reader_confidence",
}
V3_STUDY_OUTCOMES = (
    "spectral_doppler_present",
    "m_mode_present",
    "waveform_or_measurement_tracing_present",
    "calipers_present",
    "visible_text_present",
    "visible_numeric_value_present",
    "lvot_vti_specific_label_present",
    "tapse_specific_label_present",
    "candidate_target_value_present",
)
V3_SOURCE_ONLY_STUDY_OUTCOMES = (
    "source_only_relevant_content_present",
    "source_content_not_visible_in_exact_model_input_present",
    "source_only_target_specific_label_present",
    "source_only_candidate_target_value_present",
)
V3_REQUIRED_CLIP_ANNOTATION_FIELDS = {
    "acquisition_content_type",
    *V3_CLIP_PRESENCE_FIELDS,
    "reader_confidence",
}
V3_STUDY_ANNOTATION_FIELDS = {
    *V3_STUDY_OUTCOMES,
    *V3_SOURCE_ONLY_STUDY_OUTCOMES,
    "reader_confidence",
    "restricted_notes",
    "derived_summary_confirmed",
}
V3_REQUIRED_STUDY_ANNOTATION_FIELDS = {
    *V3_STUDY_OUTCOMES,
    "reader_confidence",
    "derived_summary_confirmed",
}
V3_CLIENT_STUDY_FIELDS = {
    "reader_confidence",
    "restricted_notes",
    "derived_summary_confirmed",
    *V3_STUDY_OUTCOMES,
    *V3_SOURCE_ONLY_STUDY_OUTCOMES,
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _load_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path.name}")
    return payload


def rollup_presence(values: Iterable[Any]) -> str:
    """Apply the prespecified clip-to-study presence hierarchy."""

    normalized = [str(value).strip() for value in values if str(value).strip()]
    unknown = sorted(set(normalized) - PRESENCE_VALUES)
    if unknown:
        raise ValueError("study roll-up received an invalid clip-level presence value")
    if not normalized:
        return ""
    if "yes" in normalized:
        return "yes"
    if "uncertain" in normalized:
        return "uncertain"
    if "no" in normalized:
        return "no"
    if all(value == "not_assessable" for value in normalized):
        return "not_assessable"
    return ""


def _content_presence(value: Any, requested: str) -> str:
    value = str(value).strip()
    if not value:
        return ""
    if value not in CONTENT_TYPES:
        raise ValueError("study roll-up received an invalid acquisition content type")
    if value == "not_assessable":
        return "not_assessable"
    if value in {"uncertain", "mixed", "other"}:
        return "uncertain"
    if requested == "spectral":
        return (
            "yes"
            if value
            in {
                "pulsed_wave_spectral_doppler",
                "continuous_wave_spectral_doppler",
                "tissue_doppler",
            }
            else "no"
        )
    if requested == "m_mode":
        return "yes" if value == "m_mode" else "no"
    raise ValueError("unknown acquisition-content roll-up")


def derive_study_summary(
    clip_records: Mapping[str, Mapping[str, Any]],
    clip_evidence_tiers: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Derive study outcomes exclusively from V3 clip-level responses."""

    records = list(clip_records.values())
    for record in records:
        if not isinstance(record, Mapping):
            raise ValueError("clip annotation must be an object")
    primary = {
        "spectral_doppler_present": rollup_presence(
            _content_presence(record.get("acquisition_content_type", ""), "spectral")
            for record in records
        ),
        "m_mode_present": rollup_presence(
            _content_presence(record.get("acquisition_content_type", ""), "m_mode")
            for record in records
        ),
        "waveform_or_measurement_tracing_present": rollup_presence(
            record.get("waveform_or_measurement_tracing", "") for record in records
        ),
        "calipers_present": rollup_presence(
            record.get("calipers", "") for record in records
        ),
        "visible_text_present": rollup_presence(
            record.get("visible_text", "") for record in records
        ),
        "visible_numeric_value_present": rollup_presence(
            record.get("visible_numeric_value", "") for record in records
        ),
        "lvot_vti_specific_label_present": rollup_presence(
            record.get("lvot_vti_specific_label", "") for record in records
        ),
        "tapse_specific_label_present": rollup_presence(
            record.get("tapse_specific_label", "") for record in records
        ),
        "candidate_target_value_present": rollup_presence(
            record.get("candidate_target_value_present", "") for record in records
        ),
    }
    if clip_evidence_tiers is None:
        source_records = [
            record
            for record in records
            if str(record.get(V3_SOURCE_ONLY_PRIMARY_FIELD, "")).strip()
        ]
    else:
        unknown = sorted(set(clip_records) - set(clip_evidence_tiers))
        if unknown:
            raise ValueError("study roll-up is missing clip evidence tiers")
        source_records = [
            clip_records[clip_id]
            for clip_id in clip_records
            if clip_evidence_tiers[clip_id] == TIER_A
        ]

    source_values = [
        str(record.get(V3_SOURCE_ONLY_PRIMARY_FIELD, "")).strip()
        for record in source_records
    ]
    source_values = [value for value in source_values if value]
    relevant = rollup_presence(source_values)

    def category_rollup(fields: Sequence[str]) -> str:
        values: list[str] = []
        for record in source_records:
            selected = any(str(record.get(field, "")).strip() == "yes" for field in fields)
            source_scope = str(record.get(V3_SOURCE_ONLY_PRIMARY_FIELD, "")).strip()
            if selected:
                values.append("yes")
            elif source_scope == "uncertain":
                values.append("uncertain")
            elif source_scope == "not_assessable":
                values.append("not_assessable")
            elif source_scope:
                values.append("no")
        return rollup_presence(values)

    source_only = {
        "source_only_relevant_content_present": relevant,
        "source_content_not_visible_in_exact_model_input_present": relevant,
        "source_only_target_specific_label_present": category_rollup(
            (
                "source_only_lvot_vti_specific_label",
                "source_only_tapse_specific_label",
            )
        ),
        "source_only_candidate_target_value_present": category_rollup(
            ("source_only_candidate_target_value",)
        ),
    }
    return {**primary, **source_only}


@dataclass(frozen=True)
class ActiveProtocolPaths:
    protocol_root: Path
    interface_root: Path
    queue_root: Path
    checkpoint_root: Path
    restart_root: Path
    archive_manifest_path: Path
    policy_path: Path
    study_manifest_path: Path
    protocol_definition_path: Path
    interface_policy_path: Path


@dataclass(frozen=True)
class V3TransitionPlan:
    package_root: Path
    created_at_utc: str
    legacy_queue_root: Path
    legacy_checkpoint_root: Path
    legacy_file_records: tuple[dict[str, Any], ...]
    legacy_event_counts: dict[str, int]
    primary_queue: tuple[str, ...]
    prior_formal_queue: tuple[str, ...]
    v3_formal_queue: tuple[str, ...]
    familiarization_study: str
    familiarization_was_formal: bool
    formal_replacement: str | None
    legacy_policy_sha256: str
    legacy_manifest_sha256: str
    legacy_queue_state_sha256: str

    def aggregate_safe(self) -> dict[str, Any]:
        return {
            "status": V3_DRY_RUN_PASS,
            "protocol_name": PROTOCOL_V3_NAME,
            "selected_physical_studies_unchanged": True,
            "selected_physical_studies": len(self.primary_queue),
            "formal_reliability_studies": len(self.v3_formal_queue),
            "familiarization_excluded_from_formal_reliability": True,
            "formal_reliability_replacement_count": int(self.familiarization_was_formal),
            "legacy_annotation_checkpoint_files": sum(
                record["archive_role"] == "checkpoint" for record in self.legacy_file_records
            ),
            "legacy_queue_files": sum(
                record["archive_role"] == "queue" for record in self.legacy_file_records
            ),
            "legacy_event_counts": dict(self.legacy_event_counts),
            "fresh_primary_slots": len(self.primary_queue),
            "fresh_formal_secondary_slots": len(self.v3_formal_queue),
            "fresh_events": 0,
            "fresh_checkpoints": 0,
            "prior_answers_prepopulated": False,
            "legacy_records_excluded_from_prevalence": True,
            "legacy_records_excluded_from_agreement": True,
            "legacy_records_excluded_from_adjudication": True,
            "legacy_records_excluded_from_final_aggregation": True,
            "clinical_annotations_inspected": False,
            "image_pixels_inspected": False,
        }


def _tree_file_records(root: Path, archive_role: str) -> list[dict[str, Any]]:
    if not root.is_dir():
        raise FileNotFoundError(f"legacy {archive_role} root is unavailable")
    records: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise Tier1BlockedError(BLOCKED_LINEAGE, "legacy audit state contains a symbolic link")
        if path.is_file():
            records.append(
                {
                    "archive_role": archive_role,
                    "relative_path": str(path.relative_to(root)),
                    "sha256": sha256_file(path),
                    "size_bytes": int(path.stat().st_size),
                }
            )
    return records


def _study_tier(study: Mapping[str, Any]) -> str:
    tiers = {str(clip.get("evidence_tier", "")) for clip in study.get("clips", [])}
    if len(tiers) != 1 or not tiers.issubset({TIER_A, TIER_C}):
        raise Tier1BlockedError(BLOCKED_LINEAGE, "selected study has an invalid evidence tier")
    return next(iter(tiers))


def _formal_queue_without_familiarization(
    *,
    primary: Sequence[str],
    formal: Sequence[str],
    familiarization: str,
    manifest: Mapping[str, Any],
    linkage: pd.DataFrame,
) -> tuple[tuple[str, ...], str | None]:
    if familiarization not in formal:
        return tuple(formal), None
    studies = {
        str(study.get("audit_id", "")): dict(study)
        for study in manifest.get("studies", [])
    }
    if familiarization not in studies:
        raise Tier1BlockedError(BLOCKED_LINEAGE, "familiarization study left the interface manifest")
    membership = dict(
        zip(linkage["audit_id"].astype(str), linkage["target_membership"].astype(str))
    )
    pilot_tier = _study_tier(studies[familiarization])
    pilot_membership = membership.get(familiarization, "")
    candidates = [value for value in primary if value not in set(formal) and value != familiarization]
    if not candidates:
        raise Tier1BlockedError(BLOCKED_LINEAGE, "no reliability replacement is available")
    same_stratum = [
        value
        for value in candidates
        if value in studies
        and _study_tier(studies[value]) == pilot_tier
        and membership.get(value, "") == pilot_membership
    ]
    same_tier = [
        value
        for value in candidates
        if value in studies and _study_tier(studies[value]) == pilot_tier
    ]
    replacement = (same_stratum or same_tier or candidates)[0]
    revised = list(formal)
    revised[revised.index(familiarization)] = replacement
    if len(revised) != FORMAL_RELIABILITY_N or len(set(revised)) != FORMAL_RELIABILITY_N:
        raise Tier1BlockedError(BLOCKED_LINEAGE, "revised reliability subset is invalid")
    return tuple(revised), replacement


def plan_protocol_v3_transition(
    package_root: Path,
    *,
    created_at_utc: str | None = None,
) -> V3TransitionPlan:
    """Build a read-only transition plan without parsing annotation values."""

    package_root = require_restricted_destination(package_root)
    restricted = package_root / "restricted"
    active_pointer = restricted / "active_audit_protocol.json"
    if active_pointer.exists():
        raise FileExistsError("an active versioned audit protocol already exists")
    protocol_root = restricted / "protocols" / V3_ROOT_NAME
    if protocol_root.exists():
        raise FileExistsError("the V3 protocol root already exists")

    legacy_interface = restricted / "interface"
    legacy_policy_path = legacy_interface / "queue_policy_restricted.json"
    legacy_manifest_path = legacy_interface / "study_manifest_restricted.json"
    legacy_policy = _load_object(legacy_policy_path)
    legacy_manifest = _load_object(legacy_manifest_path)
    if legacy_policy.get("protocol_name") != PROTOCOL_NAME:
        raise Tier1BlockedError(BLOCKED_LINEAGE, "legacy queue policy identity changed")
    primary = tuple(map(str, legacy_policy.get("primary_queue", [])))
    formal = tuple(map(str, legacy_policy.get("formal_reliability_queue", [])))
    if not primary or len(primary) != len(set(primary)):
        raise Tier1BlockedError(BLOCKED_LINEAGE, "legacy primary queue is invalid")
    if len(formal) != FORMAL_RELIABILITY_N or not set(formal).issubset(primary):
        raise Tier1BlockedError(BLOCKED_LINEAGE, "legacy reliability queue is invalid")

    legacy_queue_root = restricted / "queue"
    legacy_checkpoint_root = restricted / "checkpoints"
    queue_state_path = legacy_queue_root / "queue_state.json"
    queue_state = _load_object(queue_state_path)
    if queue_state.get("queue_policy_sha256") != sha256_file(legacy_policy_path):
        raise Tier1BlockedError(BLOCKED_LINEAGE, "legacy queue state differs from its policy")
    events = queue_state.get("events", [])
    if not isinstance(events, list):
        raise Tier1BlockedError(BLOCKED_LINEAGE, "legacy queue events are invalid")
    event_counts: dict[str, int] = {}
    for event in events:
        if not isinstance(event, Mapping):
            raise Tier1BlockedError(BLOCKED_LINEAGE, "legacy queue event is invalid")
        status = str(event.get("status", ""))
        event_counts[status] = event_counts.get(status, 0) + 1

    pilot_path = restricted / "roster" / "pilot_checkpoint_classification_restricted.json"
    pilot = _load_object(pilot_path)
    familiarization = str(pilot.get("pilot_audit_id", ""))
    if not familiarization or familiarization not in set(primary):
        raise Tier1BlockedError(BLOCKED_LINEAGE, "familiarization study is not in the reduced sample")
    linkage = pd.read_csv(
        restricted / "roster" / "reduced_audit_linkage_restricted.csv"
    ).fillna("")
    if not {"audit_id", "target_membership"}.issubset(linkage.columns):
        raise Tier1BlockedError(BLOCKED_LINEAGE, "reduced linkage lacks reliability strata")
    revised_formal, replacement = _formal_queue_without_familiarization(
        primary=primary,
        formal=formal,
        familiarization=familiarization,
        manifest=legacy_manifest,
        linkage=linkage,
    )
    if familiarization in revised_formal:
        raise Tier1BlockedError(BLOCKED_LINEAGE, "familiarization study remains in reliability subset")

    records = [
        *_tree_file_records(legacy_queue_root, "queue"),
        *_tree_file_records(legacy_checkpoint_root, "checkpoint"),
    ]
    return V3TransitionPlan(
        package_root=package_root,
        created_at_utc=created_at_utc or utc_now(),
        legacy_queue_root=legacy_queue_root,
        legacy_checkpoint_root=legacy_checkpoint_root,
        legacy_file_records=tuple(records),
        legacy_event_counts=event_counts,
        primary_queue=primary,
        prior_formal_queue=formal,
        v3_formal_queue=revised_formal,
        familiarization_study=familiarization,
        familiarization_was_formal=familiarization in formal,
        formal_replacement=replacement,
        legacy_policy_sha256=sha256_file(legacy_policy_path),
        legacy_manifest_sha256=sha256_file(legacy_manifest_path),
        legacy_queue_state_sha256=sha256_file(queue_state_path),
    )


def _copy_archive_tree(
    source: Path,
    destination: Path,
    records: Sequence[Mapping[str, Any]],
    archive_role: str,
) -> None:
    destination.mkdir(parents=True, exist_ok=False, mode=0o700)
    for record in records:
        if record["archive_role"] != archive_role:
            continue
        relative = Path(str(record["relative_path"]))
        source_path = source / relative
        destination_path = destination / relative
        destination_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        shutil.copy2(source_path, destination_path)
        if sha256_file(destination_path) != record["sha256"]:
            raise Tier1BlockedError(BLOCKED_LINEAGE, "archived audit file hash changed")


def _v3_queue_policy(plan: V3TransitionPlan) -> dict[str, Any]:
    primary_slots = [
        {"physical_study_token": value, "role": "primary", "formal_reliability": False}
        for value in plan.primary_queue
    ]
    secondary_slots = [
        {"physical_study_token": value, "role": "secondary", "formal_reliability": True}
        for value in plan.v3_formal_queue
    ]
    return {
        "schema_version": V3_QUEUE_SCHEMA,
        "protocol_name": PROTOCOL_V3_NAME,
        "protocol_status": PROTOCOL_V3_STATUS,
        "primary_queue": list(plan.primary_queue),
        "formal_reliability_queue": list(plan.v3_formal_queue),
        "required_review_slots": [*primary_slots, *secondary_slots],
        "reviewer_selectable_study_list": False,
        "target_membership_reader_visible": False,
        "formal_reliability_status_reader_visible": False,
        "same_role_exclusive_claim": True,
        "cross_role_independence": True,
        "same_reviewer_cross_role_same_study_prohibited": True,
        "incomplete_review_same_reviewer_resume_only": True,
        "supplemental_secondary_after_formal_completion_only": True,
        "familiarization_study_retained_for_primary_description": True,
        "familiarization_study_excluded_from_formal_reliability": True,
        "prior_protocol_answers_reused": False,
        "study_outcomes_derived_from_clip_responses": True,
    }


def _protocol_definition(
    *,
    created_at_utc: str,
    source_commit: str,
    familiarization_study: str,
) -> dict[str, Any]:
    return {
        "schema_version": PROTOCOL_V3_SCHEMA,
        "protocol_name": PROTOCOL_V3_NAME,
        "status": PROTOCOL_V3_STATUS,
        "created_at_utc": created_at_utc,
        "source_commit": source_commit,
        "display_addendum": PROTOCOL_V3_SIDE_BY_SIDE_ADDENDUM,
        "scoring_scope_by_tier": SCORING_SCOPE_BY_TIER,
        "clip_field_definitions": FIELD_DEFINITIONS,
        "source_only_field_definitions": SOURCE_ONLY_FIELD_DEFINITIONS,
        "clip_presence_fields": list(V3_CLIP_PRESENCE_FIELDS),
        "source_only_primary_field": V3_SOURCE_ONLY_PRIMARY_FIELD,
        "source_only_category_fields": list(V3_SOURCE_ONLY_CATEGORY_FIELDS),
        "source_only_free_text_fields": list(V3_SOURCE_ONLY_FREE_TEXT_FIELDS),
        "study_outcome_fields": list(V3_STUDY_OUTCOMES),
        "source_only_study_outcome_fields": list(V3_SOURCE_ONLY_STUDY_OUTCOMES),
        "study_rollup": {
            "yes": "any clip Yes",
            "uncertain": "no Yes and at least one Uncertain",
            "no": "all assessable clips No",
            "not_assessable": "all relevant clips Not assessable",
            "reviewer_confirmation_required": True,
            "independent_manual_study_outcome_entry": False,
            "model_input_and_source_only_rollups_separate": True,
        },
        "familiarization_study": {
            "physical_study_token": familiarization_study,
            "retained_in_primary_descriptive_audit": True,
            "fresh_attributable_review_required": True,
            "excluded_from_formal_reliability": True,
        },
    }


def _interface_policy() -> dict[str, Any]:
    return {
        "status": PROTOCOL_V3_STATUS,
        "display_addendum": PROTOCOL_V3_SIDE_BY_SIDE_ADDENDUM,
        "mandatory_tier_scoring_banner": True,
        "tier_a_synchronized_side_by_side": True,
        "tier_a_shared_frame_index": True,
        "tier_a_encoder_frames": 16,
        "tier_a_source_only_comparison": True,
        "tier_c_exact_model_input_panel": False,
        "clip_level_scoring": True,
        "derived_study_summary": True,
        "model_input_and_source_only_rollups_separate": True,
        "independent_study_outcome_entry": False,
        "finalized_read_only_view": True,
        "owner_only_protocol_restart": True,
        "target_visible": False,
        "split_visible": False,
        "report_label_visible": False,
        "prediction_visible": False,
        "residual_visible": False,
        "identifiers_or_paths_visible": False,
        "formal_reliability_status_visible": False,
        "ocr_available": False,
        "automated_annotation": False,
        "image_download_button": False,
        "localhost_only": True,
        "protected_parent_media_reused": True,
    }


def apply_protocol_v3_transition(
    plan: V3TransitionPlan,
    *,
    source_commit: str,
) -> dict[str, Any]:
    """Archive legacy records and activate a fresh, blank V3 namespace."""

    source_commit = str(source_commit).strip()
    if len(source_commit) != 40 or any(character not in "0123456789abcdef" for character in source_commit):
        raise ValueError("source commit must be a full lowercase Git SHA")
    package_root = require_restricted_destination(plan.package_root)
    restricted = package_root / "restricted"
    protocols_root = restricted / "protocols"
    archives_root = restricted / "protocol_archives"
    protocols_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    archives_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    compact_time = plan.created_at_utc.replace("-", "").replace(":", "").replace(".", "")
    archive_root = archives_root / f"protocol_clarification_pilot_excluded_{compact_time}"
    protocol_root = protocols_root / V3_ROOT_NAME
    active_pointer_path = restricted / "active_audit_protocol.json"
    for path in (archive_root, protocol_root, active_pointer_path):
        if path.exists():
            raise FileExistsError(f"refusing to overwrite V3 transition path: {path.name}")

    archive_root.mkdir(mode=0o700)
    _copy_archive_tree(
        plan.legacy_queue_root,
        archive_root / "queue",
        plan.legacy_file_records,
        "queue",
    )
    _copy_archive_tree(
        plan.legacy_checkpoint_root,
        archive_root / "checkpoints",
        plan.legacy_file_records,
        "checkpoint",
    )
    archive_manifest_path = archive_root / "PROTOCOL_CLARIFICATION_PILOT_EXCLUDED.json"
    archive_manifest = {
        "schema_version": PROTOCOL_ARCHIVE_SCHEMA,
        "status": PROTOCOL_ARCHIVE_STATUS,
        "archived_at_utc": plan.created_at_utc,
        "source_protocol_name": PROTOCOL_NAME,
        "source_commit": source_commit,
        "source_queue_policy_sha256": plan.legacy_policy_sha256,
        "source_study_manifest_sha256": plan.legacy_manifest_sha256,
        "source_queue_state_sha256": plan.legacy_queue_state_sha256,
        "legacy_event_counts": plan.legacy_event_counts,
        "file_records": list(plan.legacy_file_records),
        "source_files_retained_unchanged": True,
        "archived_copies_hash_verified": True,
        "finalized_records_unlocked": False,
        "finalized_records_overwritten": False,
        "excluded_from_prevalence": True,
        "excluded_from_agreement": True,
        "excluded_from_adjudication": True,
        "excluded_from_final_aggregation": True,
    }
    write_json(archive_manifest_path, archive_manifest)

    interface_root = protocol_root / "interface"
    queue_root = protocol_root / "queue"
    checkpoint_root = protocol_root / "checkpoints"
    restart_root = protocol_root / "restarts"
    for path in (interface_root, queue_root, checkpoint_root, restart_root):
        path.mkdir(parents=True, exist_ok=False, mode=0o700)
    legacy_manifest_path = restricted / "interface" / "study_manifest_restricted.json"
    study_manifest_path = interface_root / "study_manifest_restricted.json"
    shutil.copy2(legacy_manifest_path, study_manifest_path)
    if sha256_file(study_manifest_path) != plan.legacy_manifest_sha256:
        raise Tier1BlockedError(BLOCKED_LINEAGE, "V3 study manifest differs from the locked sample")

    policy_path = interface_root / "queue_policy_restricted.json"
    write_json(policy_path, _v3_queue_policy(plan))
    protocol_definition_path = interface_root / "protocol_definition_restricted.json"
    write_json(
        protocol_definition_path,
        _protocol_definition(
            created_at_utc=plan.created_at_utc,
            source_commit=source_commit,
            familiarization_study=plan.familiarization_study,
        ),
    )
    queue_state_path = queue_root / "queue_state.json"
    write_json(
        queue_state_path,
        {
            "schema_version": V3_QUEUE_SCHEMA,
            "protocol_name": PROTOCOL_V3_NAME,
            "queue_policy_sha256": sha256_file(policy_path),
            "events": [],
            "superseded_event_ids": [],
            "restart_record_sha256": [],
        },
    )
    (queue_root / ".queue.lock").touch(mode=0o600, exist_ok=False)
    interface_policy_path = interface_root / "interface_policy.json"
    write_json(interface_policy_path, _interface_policy())

    active_pointer = {
        "schema_version": ACTIVE_PROTOCOL_SCHEMA,
        "status": V3_ACTIVE,
        "protocol_name": PROTOCOL_V3_NAME,
        "activated_at_utc": plan.created_at_utc,
        "source_commit": source_commit,
        "protocol_root_relative": str(protocol_root.relative_to(package_root)),
        "queue_policy_sha256": sha256_file(policy_path),
        "study_manifest_sha256": sha256_file(study_manifest_path),
        "protocol_definition_sha256": sha256_file(protocol_definition_path),
        "interface_policy_sha256": sha256_file(interface_policy_path),
        "protocol_definition_relative": str(protocol_definition_path.relative_to(package_root)),
        "interface_policy_relative": str(interface_policy_path.relative_to(package_root)),
        "archive_manifest_relative": str(archive_manifest_path.relative_to(package_root)),
        "archive_manifest_sha256": sha256_file(archive_manifest_path),
    }
    write_json(active_pointer_path, active_pointer)
    result = validate_active_protocol_v3(package_root, require_fresh=True)
    certificate_path = package_root / "aggregate_safe" / "audit_protocol_v3_activation_certificate.json"
    write_json(certificate_path, {**result, "source_commit": source_commit})
    return {**result, "activation_certificate_sha256": sha256_file(certificate_path)}


def active_protocol_paths(package_root: Path) -> ActiveProtocolPaths:
    package_root = require_restricted_destination(package_root)
    pointer_path = package_root / "restricted" / "active_audit_protocol.json"
    pointer = _load_object(pointer_path)
    if (
        pointer.get("schema_version") != ACTIVE_PROTOCOL_SCHEMA
        or pointer.get("status") != V3_ACTIVE
        or pointer.get("protocol_name") != PROTOCOL_V3_NAME
    ):
        raise Tier1BlockedError(BLOCKED_LINEAGE, "active audit protocol pointer is invalid")
    protocol_root = (package_root / str(pointer.get("protocol_root_relative", ""))).resolve()
    if package_root.resolve() not in protocol_root.parents:
        raise Tier1BlockedError(BLOCKED_LINEAGE, "active protocol root escaped the package")
    interface_root = protocol_root / "interface"
    policy_path = interface_root / "queue_policy_restricted.json"
    study_manifest_path = interface_root / "study_manifest_restricted.json"
    protocol_definition_path = (
        package_root
        / str(
            pointer.get(
                "protocol_definition_relative",
                str(
                    (interface_root / "protocol_definition_restricted.json").relative_to(
                        package_root
                    )
                ),
            )
        )
    ).resolve()
    interface_policy_path = (
        package_root
        / str(
            pointer.get(
                "interface_policy_relative",
                str((interface_root / "interface_policy.json").relative_to(package_root)),
            )
        )
    ).resolve()
    for path in (protocol_definition_path, interface_policy_path):
        if protocol_root.resolve() not in path.parents:
            raise Tier1BlockedError(BLOCKED_LINEAGE, "active protocol artifact escaped its root")
    archive_manifest_path = (
        package_root / str(pointer.get("archive_manifest_relative", ""))
    ).resolve()
    if package_root.resolve() not in archive_manifest_path.parents:
        raise Tier1BlockedError(BLOCKED_LINEAGE, "archive manifest escaped the package")
    expected_hashes = {
        policy_path: pointer.get("queue_policy_sha256"),
        study_manifest_path: pointer.get("study_manifest_sha256"),
        protocol_definition_path: pointer.get("protocol_definition_sha256"),
        interface_policy_path: pointer.get("interface_policy_sha256"),
        archive_manifest_path: pointer.get("archive_manifest_sha256"),
    }
    for path, expected in expected_hashes.items():
        if not path.is_file() or sha256_file(path) != expected:
            raise Tier1BlockedError(BLOCKED_LINEAGE, "active protocol artifact hash changed")
    return ActiveProtocolPaths(
        protocol_root=protocol_root,
        interface_root=interface_root,
        queue_root=protocol_root / "queue",
        checkpoint_root=protocol_root / "checkpoints",
        restart_root=protocol_root / "restarts",
        archive_manifest_path=archive_manifest_path,
        policy_path=policy_path,
        study_manifest_path=study_manifest_path,
        protocol_definition_path=protocol_definition_path,
        interface_policy_path=interface_policy_path,
    )


def _flag(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes"}


def validate_side_by_side_manifest(study_manifest: Mapping[str, Any]) -> dict[str, int]:
    """Validate display evidence without reading protected image pixels."""

    studies = study_manifest.get("studies", [])
    if not isinstance(studies, list) or not studies:
        raise Tier1BlockedError(BLOCKED_LINEAGE, "V3 study manifest is invalid")
    counts = {"tier_a_clips": 0, "tier_c_clips": 0, "clips": 0}
    for study in studies:
        if not isinstance(study, Mapping):
            raise Tier1BlockedError(BLOCKED_LINEAGE, "V3 study manifest entry is invalid")
        clips = study.get("clips", [])
        if not isinstance(clips, list) or not clips:
            raise Tier1BlockedError(BLOCKED_LINEAGE, "V3 study is missing clips")
        tiers = {str(clip.get("evidence_tier", "")) for clip in clips if isinstance(clip, Mapping)}
        if len(tiers) != 1:
            raise Tier1BlockedError(BLOCKED_LINEAGE, "V3 study mixes evidence tiers")
        for clip in clips:
            if not isinstance(clip, Mapping):
                raise Tier1BlockedError(BLOCKED_LINEAGE, "V3 clip manifest entry is invalid")
            tier = str(clip.get("evidence_tier", ""))
            source_token = str(clip.get("source_media_id", "")).strip()
            model_token = str(clip.get("model_input_media_id", "")).strip()
            counts["clips"] += 1
            if tier == TIER_A:
                if (
                    not source_token
                    or not model_token
                    or not _flag(clip.get("model_input_verified"))
                    or _flag(clip.get("source_only"))
                ):
                    raise Tier1BlockedError(
                        BLOCKED_LINEAGE,
                        "Tier-A clip lacks verified paired source and exact-input media",
                    )
                counts["tier_a_clips"] += 1
            elif tier == TIER_C:
                if (
                    not source_token
                    or model_token
                    or _flag(clip.get("model_input_verified"))
                    or not _flag(clip.get("source_only"))
                ):
                    raise Tier1BlockedError(
                        BLOCKED_LINEAGE,
                        "Tier-C clip incorrectly declares an exact model input",
                    )
                counts["tier_c_clips"] += 1
            else:
                raise Tier1BlockedError(BLOCKED_LINEAGE, "V3 clip has an invalid evidence tier")
    if not counts["tier_a_clips"] or not counts["tier_c_clips"]:
        raise Tier1BlockedError(BLOCKED_LINEAGE, "V3 manifest lacks a required evidence tier")
    return counts


def apply_side_by_side_addendum(
    package_root: Path,
    *,
    source_commit: str,
    applied_at_utc: str | None = None,
) -> dict[str, Any]:
    """Apply the display addendum only to an untouched, blank base-V3 namespace."""

    source_commit = str(source_commit).strip()
    if len(source_commit) != 40 or any(
        character not in "0123456789abcdef" for character in source_commit
    ):
        raise ValueError("source commit must be a full lowercase Git SHA")
    package_root = require_restricted_destination(package_root)
    pointer_path = package_root / "restricted" / "active_audit_protocol.json"
    pointer = _load_object(pointer_path)
    paths = active_protocol_paths(package_root)
    protocol_definition = _load_object(paths.protocol_definition_path)
    if protocol_definition.get("display_addendum") == PROTOCOL_V3_SIDE_BY_SIDE_ADDENDUM:
        result = validate_active_protocol_v3(package_root, require_fresh=True)
        return {**result, "addendum_already_applied": True}

    if pointer.get("source_commit") != PROTOCOL_V3_BASE_COMMIT:
        raise Tier1BlockedError(BLOCKED_LINEAGE, "active V3 source is not the approved base commit")
    if (
        protocol_definition.get("schema_version") != PROTOCOL_V3_SCHEMA
        or protocol_definition.get("protocol_name") != PROTOCOL_V3_NAME
        or protocol_definition.get("source_commit") != PROTOCOL_V3_BASE_COMMIT
        or protocol_definition.get("scoring_scope_by_tier") != BASE_SCORING_SCOPE_BY_TIER
        or protocol_definition.get("clip_field_definitions") != FIELD_DEFINITIONS
        or protocol_definition.get("clip_presence_fields") != list(V3_CLIP_PRESENCE_FIELDS)
        or protocol_definition.get("study_outcome_fields") != list(V3_STUDY_OUTCOMES)
        or protocol_definition.get("study_rollup", {}).get(
            "independent_manual_study_outcome_entry"
        )
        is not False
        or "source_only_primary_field" in protocol_definition
        or "source_only_category_fields" in protocol_definition
    ):
        raise Tier1BlockedError(BLOCKED_LINEAGE, "active V3 base definition is not exact")

    base_interface_policy = _load_object(paths.interface_policy_path)
    required_base_policy = {
        "status": PROTOCOL_V3_STATUS,
        "mandatory_tier_scoring_banner": True,
        "clip_level_scoring": True,
        "derived_study_summary": True,
        "independent_study_outcome_entry": False,
        "target_visible": False,
        "split_visible": False,
        "report_label_visible": False,
        "prediction_visible": False,
        "residual_visible": False,
        "identifiers_or_paths_visible": False,
        "ocr_available": False,
        "automated_annotation": False,
        "localhost_only": True,
        "protected_parent_media_reused": True,
    }
    if any(
        base_interface_policy.get(key) != value
        for key, value in required_base_policy.items()
    ):
        raise Tier1BlockedError(BLOCKED_LINEAGE, "active V3 base interface policy is not exact")
    archive = _load_object(paths.archive_manifest_path)
    if archive.get("status") != PROTOCOL_ARCHIVE_STATUS or not all(
        archive.get(field) is True
        for field in (
            "excluded_from_prevalence",
            "excluded_from_agreement",
            "excluded_from_adjudication",
            "excluded_from_final_aggregation",
        )
    ):
        raise Tier1BlockedError(BLOCKED_LINEAGE, "prior pilot archive is not excluded")

    queue_state_path = paths.queue_root / "queue_state.json"
    queue_state = _load_object(queue_state_path)
    checkpoint_files = [path for path in paths.checkpoint_root.rglob("*") if path.is_file()]
    if (
        queue_state.get("schema_version") != V3_QUEUE_SCHEMA
        or queue_state.get("protocol_name") != PROTOCOL_V3_NAME
        or queue_state.get("queue_policy_sha256") != sha256_file(paths.policy_path)
        or queue_state.get("events") != []
        or queue_state.get("superseded_event_ids") != []
        or checkpoint_files
    ):
        raise Tier1BlockedError(
            BLOCKED_LINEAGE,
            "side-by-side addendum requires a fresh blank V3 review namespace",
        )
    manifest_counts = validate_side_by_side_manifest(_load_object(paths.study_manifest_path))
    archive_hash = sha256_file(paths.archive_manifest_path)
    policy_hash = sha256_file(paths.policy_path)
    manifest_hash = sha256_file(paths.study_manifest_path)
    queue_hash = sha256_file(queue_state_path)
    prior_definition_hash = sha256_file(paths.protocol_definition_path)
    prior_interface_policy_hash = sha256_file(paths.interface_policy_path)

    timestamp = applied_at_utc or utc_now()
    familiarization = str(
        protocol_definition.get("familiarization_study", {}).get(
            "physical_study_token", ""
        )
    )
    new_definition = _protocol_definition(
        created_at_utc=str(protocol_definition.get("created_at_utc", "")),
        source_commit=source_commit,
        familiarization_study=familiarization,
    )
    new_definition["display_addendum_applied_at_utc"] = timestamp
    new_definition["base_protocol_definition_sha256"] = prior_definition_hash
    new_interface_policy = _interface_policy()
    new_interface_policy["display_addendum_applied_at_utc"] = timestamp
    new_interface_policy["base_interface_policy_sha256"] = prior_interface_policy_hash

    definition_path = paths.interface_root / "protocol_definition_side_by_side_v1_restricted.json"
    interface_policy_path = paths.interface_root / "interface_policy_side_by_side_v1.json"
    certificate_path = (
        package_root / "aggregate_safe" / "audit_v3_side_by_side_addendum_certificate.json"
    )
    for destination in (definition_path, interface_policy_path, certificate_path):
        if destination.exists():
            raise FileExistsError(f"refusing to overwrite side-by-side artifact: {destination.name}")
    write_json(definition_path, new_definition)
    write_json(interface_policy_path, new_interface_policy)
    updated_pointer = {
        **pointer,
        "source_commit": source_commit,
        "display_addendum": PROTOCOL_V3_SIDE_BY_SIDE_ADDENDUM,
        "display_addendum_applied_at_utc": timestamp,
        "protocol_definition_relative": str(definition_path.relative_to(package_root)),
        "protocol_definition_sha256": sha256_file(definition_path),
        "interface_policy_relative": str(interface_policy_path.relative_to(package_root)),
        "interface_policy_sha256": sha256_file(interface_policy_path),
    }
    temporary_pointer = pointer_path.with_name(f".{pointer_path.name}.side-by-side.tmp")
    if temporary_pointer.exists():
        raise FileExistsError("temporary active-protocol pointer already exists")
    write_json(temporary_pointer, updated_pointer)
    temporary_pointer.replace(pointer_path)

    validation = validate_active_protocol_v3(package_root, require_fresh=True)
    if (
        sha256_file(paths.archive_manifest_path) != archive_hash
        or sha256_file(paths.policy_path) != policy_hash
        or sha256_file(paths.study_manifest_path) != manifest_hash
        or sha256_file(queue_state_path) != queue_hash
    ):
        raise Tier1BlockedError(BLOCKED_LINEAGE, "display addendum changed locked audit state")
    certificate = {
        **validation,
        "status": V3_SIDE_BY_SIDE_SCORING_VERIFIED,
        "deployment_readiness_status": READY_FOR_V3_BLINDED_HUMAN_AUDIT,
        "source_commit": source_commit,
        "display_addendum": PROTOCOL_V3_SIDE_BY_SIDE_ADDENDUM,
        "applied_at_utc": timestamp,
        "selected_sample_unchanged": True,
        "technical_evidence_tiers_unchanged": True,
        "queue_and_checkpoint_state_blank": True,
        "protected_media_reused": True,
        "protected_media_regenerated": False,
        "prior_pilot_archive_unchanged": True,
        "scientific_analysis_changed": False,
        **manifest_counts,
    }
    write_json(certificate_path, certificate)
    return {**certificate, "certificate_sha256": sha256_file(certificate_path)}


def validate_active_protocol_v3(
    package_root: Path,
    *,
    require_fresh: bool = False,
) -> dict[str, Any]:
    paths = active_protocol_paths(package_root)
    archive = _load_object(paths.archive_manifest_path)
    exclusion_flags = (
        "excluded_from_prevalence",
        "excluded_from_agreement",
        "excluded_from_adjudication",
        "excluded_from_final_aggregation",
    )
    if archive.get("status") != PROTOCOL_ARCHIVE_STATUS or not all(
        archive.get(field) is True for field in exclusion_flags
    ):
        raise Tier1BlockedError(BLOCKED_LINEAGE, "legacy protocol archive is not excluded")
    records = archive.get("file_records", [])
    if not isinstance(records, list):
        raise Tier1BlockedError(BLOCKED_LINEAGE, "legacy archive file manifest is invalid")
    archive_root = paths.archive_manifest_path.parent
    for record in records:
        role = str(record.get("archive_role", ""))
        if role not in {"queue", "checkpoint"}:
            raise Tier1BlockedError(BLOCKED_LINEAGE, "legacy archive role is invalid")
        path = archive_root / ("queue" if role == "queue" else "checkpoints") / str(
            record.get("relative_path", "")
        )
        if not path.is_file() or sha256_file(path) != record.get("sha256"):
            raise Tier1BlockedError(BLOCKED_LINEAGE, "legacy archive copy hash changed")
        source_root = (
            paths.protocol_root.parents[1] / "queue"
            if role == "queue"
            else paths.protocol_root.parents[1] / "checkpoints"
        )
        source_path = source_root / str(record.get("relative_path", ""))
        if not source_path.is_file() or sha256_file(source_path) != record.get("sha256"):
            raise Tier1BlockedError(BLOCKED_LINEAGE, "legacy source audit file changed")
    policy = _load_object(paths.policy_path)
    protocol_definition = _load_object(paths.protocol_definition_path)
    interface_policy = _load_object(paths.interface_policy_path)
    queue_state_path = paths.queue_root / "queue_state.json"
    queue = _load_object(queue_state_path)
    if (
        policy.get("schema_version") != V3_QUEUE_SCHEMA
        or policy.get("protocol_name") != PROTOCOL_V3_NAME
        or queue.get("schema_version") != V3_QUEUE_SCHEMA
        or queue.get("protocol_name") != PROTOCOL_V3_NAME
        or queue.get("queue_policy_sha256") != sha256_file(paths.policy_path)
        or not isinstance(queue.get("events"), list)
        or not isinstance(queue.get("superseded_event_ids"), list)
    ):
        raise Tier1BlockedError(BLOCKED_LINEAGE, "V3 queue state is invalid")
    primary = list(map(str, policy.get("primary_queue", [])))
    formal = list(map(str, policy.get("formal_reliability_queue", [])))
    manifest_counts = validate_side_by_side_manifest(_load_object(paths.study_manifest_path))
    if len(primary) != len(set(primary)) or not primary:
        raise Tier1BlockedError(BLOCKED_LINEAGE, "V3 primary queue is invalid")
    if len(formal) != FORMAL_RELIABILITY_N or not set(formal).issubset(primary):
        raise Tier1BlockedError(BLOCKED_LINEAGE, "V3 reliability queue is invalid")
    familiarization = str(
        protocol_definition.get("familiarization_study", {}).get(
            "physical_study_token", ""
        )
    )
    if (
        not familiarization
        or familiarization not in primary
        or familiarization in formal
        or policy.get("familiarization_study_excluded_from_formal_reliability") is not True
    ):
        raise Tier1BlockedError(BLOCKED_LINEAGE, "V3 familiarization exclusion is missing")
    if (
        protocol_definition.get("protocol_name") != PROTOCOL_V3_NAME
        or protocol_definition.get("scoring_scope_by_tier") != SCORING_SCOPE_BY_TIER
        or protocol_definition.get("clip_field_definitions") != FIELD_DEFINITIONS
        or protocol_definition.get("clip_presence_fields")
        != list(V3_CLIP_PRESENCE_FIELDS)
        or protocol_definition.get("source_only_primary_field")
        != V3_SOURCE_ONLY_PRIMARY_FIELD
        or protocol_definition.get("source_only_category_fields")
        != list(V3_SOURCE_ONLY_CATEGORY_FIELDS)
        or protocol_definition.get("source_only_free_text_fields")
        != list(V3_SOURCE_ONLY_FREE_TEXT_FIELDS)
        or protocol_definition.get("study_outcome_fields")
        != list(V3_STUDY_OUTCOMES)
        or protocol_definition.get("source_only_study_outcome_fields")
        != list(V3_SOURCE_ONLY_STUDY_OUTCOMES)
        or protocol_definition.get("study_rollup", {}).get(
            "independent_manual_study_outcome_entry"
        )
        is not False
        or protocol_definition.get("study_rollup", {}).get(
            "model_input_and_source_only_rollups_separate"
        )
        is not True
        or protocol_definition.get("display_addendum")
        != PROTOCOL_V3_SIDE_BY_SIDE_ADDENDUM
    ):
        raise Tier1BlockedError(BLOCKED_LINEAGE, "V3 scoring protocol definition changed")
    required_interface_policy = {
        "display_addendum": PROTOCOL_V3_SIDE_BY_SIDE_ADDENDUM,
        "mandatory_tier_scoring_banner": True,
        "tier_a_synchronized_side_by_side": True,
        "tier_a_shared_frame_index": True,
        "tier_a_encoder_frames": 16,
        "tier_a_source_only_comparison": True,
        "tier_c_exact_model_input_panel": False,
        "model_input_and_source_only_rollups_separate": True,
        "ocr_available": False,
        "automated_annotation": False,
        "protected_parent_media_reused": True,
    }
    if any(interface_policy.get(key) != value for key, value in required_interface_policy.items()):
        raise Tier1BlockedError(BLOCKED_LINEAGE, "V3 side-by-side interface policy changed")
    required_slots = policy.get("required_review_slots", [])
    expected_slots = [
        {"physical_study_token": value, "role": "primary", "formal_reliability": False}
        for value in primary
    ] + [
        {"physical_study_token": value, "role": "secondary", "formal_reliability": True}
        for value in formal
    ]
    if required_slots != expected_slots:
        raise Tier1BlockedError(BLOCKED_LINEAGE, "V3 review-slot inventory changed")
    checkpoint_files = [path for path in paths.checkpoint_root.rglob("*") if path.is_file()]
    if require_fresh and (queue["events"] or checkpoint_files):
        raise Tier1BlockedError(BLOCKED_LINEAGE, "V3 review state is not fresh")
    eligible = aggregation_eligible_events_from_state(queue)
    return {
        "status": V3_ACTIVE,
        "protocol_name": PROTOCOL_V3_NAME,
        "selected_physical_studies_unchanged": True,
        "selected_physical_studies": len(primary),
        "formal_reliability_studies": len(formal),
        "familiarization_excluded_from_formal_reliability": True,
        "archived_legacy_files": len(records),
        "legacy_records_excluded_from_all_final_aggregations": True,
        "current_protocol_events": len(queue["events"]),
        "current_protocol_checkpoint_files": len(checkpoint_files),
        "aggregation_eligible_locked_reviews": len(eligible),
        "prior_answers_prepopulated": False if require_fresh else None,
        "mandatory_scoring_scope_banner": True,
        "automatic_study_rollup": True,
        "side_by_side_status": V3_SIDE_BY_SIDE_SCORING_VERIFIED,
        "deployment_readiness_status": READY_FOR_V3_BLINDED_HUMAN_AUDIT,
        "tier_a_synchronized_side_by_side": True,
        "tier_c_source_acquisition_only": True,
        "source_only_fields_separate": True,
        **manifest_counts,
        "finalized_read_only_view": True,
        "owner_only_protocol_restart": True,
        "clinical_annotations_emitted": False,
        "identifiers_emitted": False,
    }


def aggregation_eligible_events_from_state(
    queue_state: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Return only active-protocol finalized reviews eligible for aggregation."""

    if queue_state.get("protocol_name") != PROTOCOL_V3_NAME:
        raise Tier1BlockedError(BLOCKED_LINEAGE, "aggregation source is not the active V3 protocol")
    superseded = set(map(str, queue_state.get("superseded_event_ids", [])))
    events = queue_state.get("events", [])
    if not isinstance(events, list):
        raise Tier1BlockedError(BLOCKED_LINEAGE, "aggregation queue events are invalid")
    eligible = []
    for event in events:
        if not isinstance(event, Mapping):
            raise Tier1BlockedError(BLOCKED_LINEAGE, "aggregation queue event is invalid")
        if (
            event.get("protocol_name") == PROTOCOL_V3_NAME
            and event.get("status") == "locked"
            and str(event.get("event_id", "")) not in superseded
        ):
            eligible.append(dict(event))
    return eligible


def protocol_definition_digest() -> str:
    return sha256_json(
        {
            "protocol_name": PROTOCOL_V3_NAME,
            "display_addendum": PROTOCOL_V3_SIDE_BY_SIDE_ADDENDUM,
            "scoring_scope_by_tier": SCORING_SCOPE_BY_TIER,
            "field_definitions": FIELD_DEFINITIONS,
            "source_only_field_definitions": SOURCE_ONLY_FIELD_DEFINITIONS,
            "clip_presence_fields": V3_CLIP_PRESENCE_FIELDS,
            "source_only_primary_field": V3_SOURCE_ONLY_PRIMARY_FIELD,
            "source_only_category_fields": V3_SOURCE_ONLY_CATEGORY_FIELDS,
            "source_only_free_text_fields": V3_SOURCE_ONLY_FREE_TEXT_FIELDS,
            "study_outcome_fields": V3_STUDY_OUTCOMES,
            "source_only_study_outcome_fields": V3_SOURCE_ONLY_STUDY_OUTCOMES,
        }
    )
