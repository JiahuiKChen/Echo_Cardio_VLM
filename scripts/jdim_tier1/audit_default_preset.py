"""Versioned, event-scoped defaults for the JDIM V3 human-audit interface."""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .audit_protocol_v3 import (
    PROTOCOL_V3_NAME,
    V3_SOURCE_ONLY_CLIP_FIELDS,
    V3_STUDY_ANNOTATION_FIELDS,
    active_protocol_paths,
    validate_active_protocol_v3,
)
from .safety import (
    BLOCKED_LINEAGE,
    Tier1BlockedError,
    require_restricted_destination,
    sha256_file,
    sha256_json,
)


DEFAULT_PRESET_CONFIG_SCHEMA = "jdim-audit-clip-default-preset-v1"
DEFAULT_PRESET_BACKUP_SCHEMA = "jdim-audit-default-preset-backup-v1"
DEFAULT_PRESET_ACTIVATION_SCHEMA = "jdim-audit-default-preset-activation-v1"
DEFAULT_PRESET_ID = "JDIM_AUDIT_CLIP_DEFAULT_PRESET_V1"
DEFAULT_PRESET_CONFIG_FILENAME = "clip_default_preset_v1_restricted.json"
DEFAULT_PRESET_DRY_RUN_PASS = "DEFAULT_PRESET_DRY_RUN_PASS"
CURRENT_REVIEW_PROGRESS_BACKED_UP = "CURRENT_REVIEW_PROGRESS_BACKED_UP"
READY_FOR_QUIET_DEPLOYMENT = "READY_FOR_QUIET_DEPLOYMENT"
DEFAULT_PRESET_PRODUCTION_VERIFIED = "DEFAULT_PRESET_PRODUCTION_VERIFIED"
READY_FOR_DEFAULT_ASSISTED_V3_HUMAN_AUDIT = (
    "READY_FOR_DEFAULT_ASSISTED_V3_HUMAN_AUDIT"
)

UNREVIEWED_PRESET_DISPLAYED = "UNREVIEWED_PRESET_DISPLAYED"
REVIEWED_CONFIRMED = "REVIEWED_CONFIRMED"
REVIEWED_MODIFIED_AND_CONFIRMED = "REVIEWED_MODIFIED_AND_CONFIRMED"
NOT_ASSESSABLE_CONFIRMED = "NOT_ASSESSABLE_CONFIRMED"
CONFIRMED_CLIP_STATES = {
    REVIEWED_CONFIRMED,
    REVIEWED_MODIFIED_AND_CONFIRMED,
    NOT_ASSESSABLE_CONFIRMED,
}

DEFAULT_PRESET_VALUES = {
    "acquisition_content_type": "2d_b_mode",
    "waveform_or_measurement_tracing": "no",
    "calipers": "no",
    "visible_text": "yes",
    "visible_numeric_value": "yes",
    "lvot_vti_specific_label": "no",
    "tapse_specific_label": "no",
    "candidate_target_value_present": "no",
    "candidate_target_value": "",
    "visible_unit_text": "",
    "visible_measurement_name_text": "",
    "display_precision": "",
    "reader_confidence": "high",
    "restricted_notes": "",
}

ELIGIBLE_REVIEW_EVENT_TYPES = (
    "new_primary_claim",
    "new_secondary_claim",
    "owner_authorized_restart",
)

PRESET_EVENT_FIELDS = (
    "clip_default_preset_version",
    "preset_cutover_id",
    "preset_cutover_timestamp_utc",
    "preset_event_type",
    "default_preset_confirmation_required",
)

_SOURCE_COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}")
_CUTOVER_ID_PATTERN = re.compile(r"V31-[A-Za-z0-9_.-]{8,80}")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _atomic_write_json(path: Path, payload: Any, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
        os.chmod(path, mode)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _load_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path.name}")
    return payload


def _source_commit(value: str) -> str:
    commit = str(value).strip().lower()
    if not _SOURCE_COMMIT_PATTERN.fullmatch(commit):
        raise ValueError("deployed source commit must be a complete lowercase SHA")
    return commit


def _cutover_timestamp(value: str | None) -> str:
    timestamp = str(value or utc_now()).strip()
    if not timestamp.endswith("Z"):
        raise ValueError("cutover timestamp must be an explicit UTC timestamp")
    datetime.fromisoformat(timestamp.removesuffix("Z") + "+00:00")
    return timestamp


def build_default_preset_configuration(
    *,
    source_commit: str,
    cutover_timestamp_utc: str | None = None,
    cutover_id: str | None = None,
) -> dict[str, Any]:
    """Build the exact V3.1 configuration without writing production state."""

    commit = _source_commit(source_commit)
    timestamp = _cutover_timestamp(cutover_timestamp_utc)
    identifier = str(cutover_id or "").strip()
    if not identifier:
        compact = timestamp.replace("-", "").replace(":", "").replace(".", "")
        identifier = f"V31-{compact}-{secrets.token_hex(4).upper()}"
    if not _CUTOVER_ID_PATTERN.fullmatch(identifier):
        raise ValueError("cutover identifier is invalid")
    return {
        "schema_version": DEFAULT_PRESET_CONFIG_SCHEMA,
        "status": READY_FOR_DEFAULT_ASSISTED_V3_HUMAN_AUDIT,
        "protocol_name": PROTOCOL_V3_NAME,
        "preset_identifier": DEFAULT_PRESET_ID,
        "deployed_source_commit": commit,
        "cutover": {
            "identifier": identifier,
            "timestamp_utc": timestamp,
            "event_metadata_controls_eligibility": True,
            "browser_time_controls_eligibility": False,
        },
        "eligible_review_event_types": list(ELIGIBLE_REVIEW_EVENT_TYPES),
        "field_values": dict(DEFAULT_PRESET_VALUES),
        "excluded_fields": sorted(
            {
                *V3_SOURCE_ONLY_CLIP_FIELDS,
                *V3_STUDY_ANNOTATION_FIELDS,
                "adjudication",
                "owner_admin",
                "reviewer_code",
                "role",
                "not_assessable_reason",
            }
        ),
        "confirmation": {
            "required_per_clip": True,
            "display_does_not_count_as_reviewed": True,
            "study_rollup_uses_confirmed_clips_only": True,
            "finalization_requires_all_clips_confirmed": True,
        },
        "provenance": {
            "interface_efficiency_feature": True,
            "model_output_used": False,
            "report_label_value_used": False,
            "target_value_used": False,
            "prediction_used": False,
            "prior_reviewer_answer_used": False,
            "automated_annotation": False,
            "possible_anchoring_acknowledged": True,
        },
    }


def validate_default_preset_configuration(
    payload: Mapping[str, Any],
    *,
    expected_source_commit: str | None = None,
) -> dict[str, Any]:
    """Fail closed unless the configuration is exactly the approved V3.1 preset."""

    expected_keys = {
        "schema_version",
        "status",
        "protocol_name",
        "preset_identifier",
        "deployed_source_commit",
        "cutover",
        "eligible_review_event_types",
        "field_values",
        "excluded_fields",
        "confirmation",
        "provenance",
    }
    if set(payload) != expected_keys:
        raise Tier1BlockedError(BLOCKED_LINEAGE, "default-preset configuration fields changed")
    commit = _source_commit(str(payload.get("deployed_source_commit", "")))
    if expected_source_commit is not None and commit != _source_commit(expected_source_commit):
        raise Tier1BlockedError(BLOCKED_LINEAGE, "default-preset source commit changed")
    cutover = payload.get("cutover")
    confirmation = payload.get("confirmation")
    provenance = payload.get("provenance")
    if not isinstance(cutover, Mapping) or set(cutover) != {
        "identifier",
        "timestamp_utc",
        "event_metadata_controls_eligibility",
        "browser_time_controls_eligibility",
    }:
        raise Tier1BlockedError(BLOCKED_LINEAGE, "default-preset cutover metadata changed")
    if not _CUTOVER_ID_PATTERN.fullmatch(str(cutover.get("identifier", ""))):
        raise Tier1BlockedError(BLOCKED_LINEAGE, "default-preset cutover identifier is invalid")
    _cutover_timestamp(str(cutover.get("timestamp_utc", "")))
    expected_confirmation = {
        "required_per_clip": True,
        "display_does_not_count_as_reviewed": True,
        "study_rollup_uses_confirmed_clips_only": True,
        "finalization_requires_all_clips_confirmed": True,
    }
    expected_provenance = {
        "interface_efficiency_feature": True,
        "model_output_used": False,
        "report_label_value_used": False,
        "target_value_used": False,
        "prediction_used": False,
        "prior_reviewer_answer_used": False,
        "automated_annotation": False,
        "possible_anchoring_acknowledged": True,
    }
    if (
        payload.get("schema_version") != DEFAULT_PRESET_CONFIG_SCHEMA
        or payload.get("status") != READY_FOR_DEFAULT_ASSISTED_V3_HUMAN_AUDIT
        or payload.get("protocol_name") != PROTOCOL_V3_NAME
        or payload.get("preset_identifier") != DEFAULT_PRESET_ID
        or payload.get("eligible_review_event_types") != list(ELIGIBLE_REVIEW_EVENT_TYPES)
        or payload.get("field_values") != DEFAULT_PRESET_VALUES
        or confirmation != expected_confirmation
        or provenance != expected_provenance
        or cutover.get("event_metadata_controls_eligibility") is not True
        or cutover.get("browser_time_controls_eligibility") is not False
    ):
        raise Tier1BlockedError(BLOCKED_LINEAGE, "default-preset policy changed")
    expected_excluded = sorted(
        {
            *V3_SOURCE_ONLY_CLIP_FIELDS,
            *V3_STUDY_ANNOTATION_FIELDS,
            "adjudication",
            "owner_admin",
            "reviewer_code",
            "role",
            "not_assessable_reason",
        }
    )
    excluded = payload.get("excluded_fields")
    if excluded != expected_excluded:
        raise Tier1BlockedError(BLOCKED_LINEAGE, "source-only fields are not excluded from defaults")
    return json.loads(json.dumps(dict(payload)))


def load_default_preset_configuration(
    interface_root: Path,
    *,
    expected_source_commit: str | None = None,
) -> dict[str, Any] | None:
    path = interface_root / DEFAULT_PRESET_CONFIG_FILENAME
    if not path.is_file():
        return None
    return validate_default_preset_configuration(
        _load_object(path), expected_source_commit=expected_source_commit
    )


def preset_event_metadata(
    configuration: Mapping[str, Any] | None,
    *,
    event_type: str,
) -> dict[str, Any]:
    if configuration is None:
        return {}
    config = validate_default_preset_configuration(configuration)
    event_type = str(event_type)
    if event_type not in config["eligible_review_event_types"]:
        raise ValueError("review event type is not eligible for the default preset")
    return {
        "clip_default_preset_version": DEFAULT_PRESET_ID,
        "preset_cutover_id": str(config["cutover"]["identifier"]),
        "preset_cutover_timestamp_utc": str(config["cutover"]["timestamp_utc"]),
        "preset_event_type": event_type,
        "default_preset_confirmation_required": True,
    }


def event_uses_default_preset(
    event: Mapping[str, Any],
    configuration: Mapping[str, Any] | None,
) -> bool:
    present = {field for field in PRESET_EVENT_FIELDS if field in event}
    if not present:
        return False
    if present != set(PRESET_EVENT_FIELDS) or configuration is None:
        raise Tier1BlockedError(BLOCKED_LINEAGE, "review event has incomplete preset lineage")
    event_type = str(event.get("preset_event_type", ""))
    expected = preset_event_metadata(configuration, event_type=event_type)
    actual = {field: event.get(field) for field in PRESET_EVENT_FIELDS}
    if actual != expected:
        raise Tier1BlockedError(BLOCKED_LINEAGE, "review event preset lineage changed")
    return True


def public_preset_for_event(
    event: Mapping[str, Any],
    configuration: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    if not event_uses_default_preset(event, configuration):
        return None
    return {
        "preset_identifier": DEFAULT_PRESET_ID,
        "field_values": dict(DEFAULT_PRESET_VALUES),
        "confirmation_required": True,
        "unreviewed_state": UNREVIEWED_PRESET_DISPLAYED,
        "confirmation_states": sorted(CONFIRMED_CLIP_STATES),
    }


def _review_state_files(package_root: Path) -> dict[str, str]:
    paths = active_protocol_paths(package_root)
    roots = [
        package_root / "restricted" / "active_audit_protocol.json",
        paths.queue_root,
        paths.checkpoint_root,
        paths.restart_root,
        package_root / "restricted" / "reviewer_registry",
        package_root / "restricted" / "protocol_archives",
    ]
    records: dict[str, str] = {}
    for root in roots:
        candidates = [root] if root.is_file() else sorted(root.rglob("*")) if root.is_dir() else []
        for path in candidates:
            if path.is_file() and not path.name.endswith(".lock"):
                records[str(path.relative_to(package_root))] = sha256_file(path)
    return records


def aggregate_review_state_counts(package_root: Path) -> dict[str, int]:
    """Return content-free production counts without exposing review identities."""

    paths = active_protocol_paths(package_root)
    queue = _load_object(paths.queue_root / "queue_state.json")
    manifest = _load_object(paths.study_manifest_path)
    clip_counts = {
        str(study["audit_id"]): len(study.get("clips", []))
        for study in manifest.get("studies", [])
    }
    superseded = set(map(str, queue.get("superseded_event_ids", [])))
    counts = {
        "events_total": 0,
        "active_claims": 0,
        "incomplete_reviews": 0,
        "complete_not_finalized_reviews": 0,
        "finalized_reviews": 0,
        "archived_reviews": 0,
        "clip_annotations": 0,
        "study_summaries": 0,
        "primary_reviews": 0,
        "secondary_reviews": 0,
    }
    for event in queue.get("events", []):
        counts["events_total"] += 1
        role = str(event.get("role", ""))
        if role == "primary":
            counts["primary_reviews"] += 1
        elif role == "secondary":
            counts["secondary_reviews"] += 1
        status = str(event.get("status", ""))
        effective = str(event.get("event_id", "")) not in superseded
        if status == "archived_incomplete" or not effective:
            counts["archived_reviews"] += 1
        elif status == "locked":
            counts["finalized_reviews"] += 1
        elif status == "in_progress":
            counts["active_claims"] += 1
            required = clip_counts.get(str(event.get("physical_study_token", "")), -1)
            complete = (
                required >= 0
                and int(event.get("clip_completion_count", -1)) == required
                and event.get("study_summary_complete") is True
            )
            counts[
                "complete_not_finalized_reviews" if complete else "incomplete_reviews"
            ] += 1
    for checkpoint in sorted(paths.checkpoint_root.glob("*/checkpoint.json")):
        payload = _load_object(checkpoint)
        annotations = payload.get("annotations", {})
        if isinstance(annotations, Mapping):
            clips = annotations.get("clips", {})
            studies = annotations.get("studies", {})
            counts["clip_annotations"] += len(clips) if isinstance(clips, Mapping) else 0
            counts["study_summaries"] += len(studies) if isinstance(studies, Mapping) else 0
    return counts


def metadata_only_default_preset_dry_run(
    package_root: Path,
    *,
    source_commit: str,
) -> dict[str, Any]:
    """Exercise the cutover in disposable restricted state without reading media."""

    from .audit_protocol_v3 import (
        V3_CHECKPOINT_SCHEMA,
        V3_CLIP_ANNOTATION_FIELDS,
        V3_CLIP_PRESENCE_FIELDS,
        V3_EVENT_SCHEMA,
        V3_LOCK_SCHEMA,
        V3_QUEUE_SCHEMA,
        V3_REQUIRED_CLIP_ANNOTATION_FIELDS,
        V3_REQUIRED_STUDY_ANNOTATION_FIELDS,
        V3_SOURCE_ONLY_CATEGORY_FIELDS,
        V3_SOURCE_ONLY_CLIP_FIELDS,
        V3_SOURCE_ONLY_FREE_TEXT_FIELDS,
        V3_SOURCE_ONLY_PRIMARY_FIELD,
        V3_SOURCE_ONLY_STUDY_OUTCOMES,
        V3_STUDY_ANNOTATION_FIELDS,
        V3_STUDY_OUTCOMES,
        derive_study_summary,
    )
    from .reduced_audit import TIER_A
    from .reduced_audit_interface import (
        ACTION_CLAIM_NEXT,
        ACTION_RESUME,
        ROLE_PRIMARY,
        ROLE_SECONDARY,
        ReviewerRegistry,
        RoleAwareCheckpointStore,
        RoleQueueStore,
    )

    package_root = require_restricted_destination(package_root)
    commit = _source_commit(source_commit)
    validate_active_protocol_v3(package_root)
    paths = active_protocol_paths(package_root)
    if load_default_preset_configuration(paths.interface_root) is not None:
        raise FileExistsError("default preset is already active")
    live_before = _review_state_files(package_root)
    counts_before = aggregate_review_state_counts(package_root)
    staging = Path(
        tempfile.mkdtemp(
            prefix=".default_preset_v1_dry_run.",
            dir=package_root / "restricted",
        )
    )
    try:
        interface = staging / "interface"
        queue_root = staging / "queue"
        checkpoint_root = staging / "checkpoints"
        registry_root = staging / "reviewer_registry"
        interface.mkdir(parents=True, mode=0o700)
        shutil.copy2(paths.policy_path, interface / paths.policy_path.name)
        shutil.copy2(paths.study_manifest_path, interface / paths.study_manifest_path.name)
        manifest = _load_object(interface / paths.study_manifest_path.name)
        registry = ReviewerRegistry(registry_root)
        for code in (
            "V31DRYRUNPRE",
            "V31DRYRUNLOCK",
            "V31DRYRUNREADY",
            "V31DRYRUNPRI",
            "V31DRYRUNSEC",
        ):
            registry.register(code, qualified=True)
        queue_options = {
            "expected_protocol_name": PROTOCOL_V3_NAME,
            "queue_schema": V3_QUEUE_SCHEMA,
            "event_schema": V3_EVENT_SCHEMA,
        }
        checkpoint_options = {
            "checkpoint_schema": V3_CHECKPOINT_SCHEMA,
            "lock_schema": V3_LOCK_SCHEMA,
            "clip_annotation_fields": V3_CLIP_ANNOTATION_FIELDS,
            "clip_presence_fields": V3_CLIP_PRESENCE_FIELDS,
            "required_clip_fields": V3_REQUIRED_CLIP_ANNOTATION_FIELDS,
            "study_annotation_fields": V3_STUDY_ANNOTATION_FIELDS,
            "study_outcome_fields": V3_STUDY_OUTCOMES,
            "source_only_study_outcome_fields": V3_SOURCE_ONLY_STUDY_OUTCOMES,
            "source_only_clip_fields": V3_SOURCE_ONLY_CLIP_FIELDS,
            "source_only_category_fields": V3_SOURCE_ONLY_CATEGORY_FIELDS,
            "source_only_free_text_fields": V3_SOURCE_ONLY_FREE_TEXT_FIELDS,
            "source_only_primary_field": V3_SOURCE_ONLY_PRIMARY_FIELD,
            "required_study_fields": V3_REQUIRED_STUDY_ANNOTATION_FIELDS,
            "derive_study_outcomes": True,
        }
        pre_queue = RoleQueueStore(
            queue_root,
            interface / paths.policy_path.name,
            registry,
            **queue_options,
        )
        pre_checkpoints = RoleAwareCheckpointStore(
            checkpoint_root,
            pre_queue,
            manifest,
            **checkpoint_options,
        )

        def claim(queue: RoleQueueStore, code: str, role: str) -> dict[str, Any]:
            return queue.transition(
                reviewer_code=code,
                role=role,
                action=ACTION_CLAIM_NEXT,
                qualification_confirmed=True,
                classify_in_progress=pre_checkpoints.review_state,
            )["event"]

        pre_incomplete = claim(pre_queue, "V31DRYRUNPRE", ROLE_PRIMARY)
        pre_checkpoints.save(
            str(pre_incomplete["event_id"]),
            {"annotations": {"studies": {}, "clips": {}}},
        )
        def synthetic_complete_payload(event: Mapping[str, Any]) -> dict[str, Any]:
            study = next(
                item
                for item in manifest["studies"]
                if str(item["audit_id"]) == str(event["physical_study_token"])
            )
            clips: dict[str, dict[str, str]] = {}
            tiers: dict[str, str] = {}
            for clip in study["clips"]:
                clip_id = str(clip["clip_audit_id"])
                tiers[clip_id] = str(clip["evidence_tier"])
                record = {
                    "acquisition_content_type": "not_assessable",
                    **{field: "not_assessable" for field in V3_CLIP_PRESENCE_FIELDS},
                    "reader_confidence": "not_assessable",
                }
                if clip["evidence_tier"] == TIER_A:
                    record[V3_SOURCE_ONLY_PRIMARY_FIELD] = "no"
                clips[clip_id] = record
            summary = derive_study_summary(clips, clip_evidence_tiers=tiers)
            summary.update(
                {
                    "derived_summary_confirmed": "yes",
                    "reader_confidence": "not_assessable",
                }
            )
            return {
                "annotations": {
                    "studies": {str(study["audit_id"]): summary},
                    "clips": clips,
                }
            }

        pre_locked = claim(pre_queue, "V31DRYRUNLOCK", ROLE_PRIMARY)
        pre_checkpoints.save(
            str(pre_locked["event_id"]),
            synthetic_complete_payload(pre_locked),
        )
        pre_checkpoints.lock(str(pre_locked["event_id"]))
        pre_ready = claim(pre_queue, "V31DRYRUNREADY", ROLE_PRIMARY)
        pre_checkpoints.save(
            str(pre_ready["event_id"]),
            synthetic_complete_payload(pre_ready),
        )
        preserved_events = {
            str(event["event_id"]): pre_queue.get_event(str(event["event_id"]))
            for event in (pre_incomplete, pre_locked, pre_ready)
        }
        preserved_files = {
            str(path.relative_to(staging)): sha256_file(path)
            for event in (pre_incomplete, pre_locked, pre_ready)
            for path in sorted((checkpoint_root / str(event["event_id"])).glob("*.json"))
        }

        configuration = build_default_preset_configuration(
            source_commit=commit,
            cutover_timestamp_utc="2026-09-08T16:00:00Z",
            cutover_id="V31-METADATA-DRY-RUN",
        )
        post_queue = RoleQueueStore(
            queue_root,
            interface / paths.policy_path.name,
            registry,
            initialize=False,
            default_preset_configuration=configuration,
            **queue_options,
        )
        post_checkpoints = RoleAwareCheckpointStore(
            checkpoint_root,
            post_queue,
            manifest,
            initialize=False,
            default_preset_configuration=configuration,
            **checkpoint_options,
        )
        resumed = post_queue.transition(
            reviewer_code="V31DRYRUNPRE",
            role=ROLE_PRIMARY,
            action=ACTION_RESUME,
            qualification_confirmed=True,
            classify_in_progress=post_checkpoints.review_state,
        )["event"]
        if event_uses_default_preset(resumed, configuration):
            raise Tier1BlockedError(BLOCKED_LINEAGE, "pre-cutover event received defaults")

        def post_claim(code: str, role: str) -> dict[str, Any]:
            result = post_queue.transition(
                reviewer_code=code,
                role=role,
                action=ACTION_CLAIM_NEXT,
                qualification_confirmed=True,
                classify_in_progress=post_checkpoints.review_state,
            )
            event = result["event"]
            if not event_uses_default_preset(event, configuration):
                raise Tier1BlockedError(BLOCKED_LINEAGE, "post-cutover event missed defaults")
            return event

        primary = post_claim("V31DRYRUNPRI", ROLE_PRIMARY)
        secondary = post_claim("V31DRYRUNSEC", ROLE_SECONDARY)
        if post_checkpoints.review_state(primary)["completed_clip_count"] != 0:
            raise Tier1BlockedError(BLOCKED_LINEAGE, "displayed defaults counted as reviewed")
        post_study = next(
            study
            for study in manifest["studies"]
            if str(study["audit_id"]) == str(primary["physical_study_token"])
        )
        first_clip = post_study["clips"][0]
        annotation = dict(DEFAULT_PRESET_VALUES)
        if first_clip["evidence_tier"] == TIER_A:
            annotation[V3_SOURCE_ONLY_PRIMARY_FIELD] = "no"
        post_checkpoints.confirm_clip(
            str(primary["event_id"]),
            clip_id=str(first_clip["clip_audit_id"]),
            annotation=annotation,
        )
        if post_checkpoints.review_state(primary)["completed_clip_count"] != 1:
            raise Tier1BlockedError(BLOCKED_LINEAGE, "one confirmation did not count exactly one clip")
        try:
            post_checkpoints.lock(str(primary["event_id"]))
        except (FileNotFoundError, ValueError):
            pass
        else:
            raise Tier1BlockedError(BLOCKED_LINEAGE, "incomplete post-cutover review finalized")
        for event_id, expected in preserved_events.items():
            if post_queue.get_event(event_id) != expected:
                raise Tier1BlockedError(BLOCKED_LINEAGE, "pre-cutover event changed")
        after_files = {
            str(path.relative_to(staging)): sha256_file(path)
            for event in (pre_incomplete, pre_locked, pre_ready)
            for path in sorted((checkpoint_root / str(event["event_id"])).glob("*.json"))
        }
        if after_files != preserved_files:
            raise Tier1BlockedError(BLOCKED_LINEAGE, "pre-cutover checkpoint or lock changed")
        if public_preset_for_event(primary, configuration) is None or public_preset_for_event(
            secondary, configuration
        ) is None:
            raise Tier1BlockedError(BLOCKED_LINEAGE, "preset payload is unavailable")
    finally:
        shutil.rmtree(staging)
    live_after = _review_state_files(package_root)
    counts_after = aggregate_review_state_counts(package_root)
    if live_after != live_before or counts_after != counts_before:
        raise Tier1BlockedError(BLOCKED_LINEAGE, "metadata-only dry run changed production state")
    return {
        "status": DEFAULT_PRESET_DRY_RUN_PASS,
        "source_commit": commit,
        "production_state_sha256_before": sha256_json(live_before),
        "production_state_sha256_after": sha256_json(live_after),
        "aggregate_counts_before": counts_before,
        "aggregate_counts_after": counts_after,
        "pre_cutover_records_unchanged": True,
        "post_cutover_primary_verified": True,
        "post_cutover_secondary_verified": True,
        "displayed_defaults_counted_as_reviewed": False,
        "one_confirmation_completed_clips": 1,
        "incomplete_finalization_blocked": True,
        "source_only_fields_defaulted": False,
        "media_read": False,
        "annotation_content_emitted": False,
        "identifiers_emitted": False,
    }


def create_production_review_backup(
    package_root: Path,
    *,
    source_commit: str,
    created_at_utc: str | None = None,
) -> dict[str, Any]:
    """Create and verify a restricted immutable archive of current review state."""

    package_root = require_restricted_destination(package_root)
    commit = _source_commit(source_commit)
    validate_active_protocol_v3(package_root)
    paths = active_protocol_paths(package_root)
    if load_default_preset_configuration(paths.interface_root) is not None:
        raise FileExistsError("default preset is already active")
    timestamp = _cutover_timestamp(created_at_utc)
    compact = timestamp.replace("-", "").replace(":", "").replace(".", "")
    backup_root = package_root / "restricted" / "backups" / f"default_preset_v1_{compact}"
    backup_root.mkdir(parents=True, exist_ok=False, mode=0o700)
    state_records = _review_state_files(package_root)
    counts = aggregate_review_state_counts(package_root)
    archive_path = backup_root / "current_review_progress.tar"
    with tarfile.open(archive_path, "w") as archive:
        for relative in sorted(state_records):
            archive.add(package_root / relative, arcname=relative, recursive=False)
    os.chmod(archive_path, 0o400)
    verified = 0
    with tarfile.open(archive_path, "r") as archive:
        members = {member.name: member for member in archive.getmembers() if member.isfile()}
        if set(members) != set(state_records):
            raise Tier1BlockedError(BLOCKED_LINEAGE, "backup file inventory changed")
        for relative, expected_hash in state_records.items():
            stream = archive.extractfile(members[relative])
            if stream is None or hashlib.sha256(stream.read()).hexdigest() != expected_hash:
                raise Tier1BlockedError(BLOCKED_LINEAGE, "backup content hash changed")
            verified += 1
    certificate = {
        "schema_version": DEFAULT_PRESET_BACKUP_SCHEMA,
        "status": CURRENT_REVIEW_PROGRESS_BACKED_UP,
        "created_at_utc": timestamp,
        "source_commit": commit,
        "package_root": str(package_root),
        "archive_path": str(archive_path),
        "archive_sha256": sha256_file(archive_path),
        "file_count": len(state_records),
        "files_verified": verified,
        "live_state_sha256": sha256_json(state_records),
        "aggregate_counts": counts,
        "review_values_exposed": False,
        "identifiers_exposed": False,
    }
    certificate_path = backup_root / "backup_certificate_restricted.json"
    _atomic_write_json(certificate_path, certificate, mode=0o400)
    safe_path = package_root / "aggregate_safe" / f"default_preset_backup_{compact}.json"
    _atomic_write_json(
        safe_path,
        {
            key: certificate[key]
            for key in (
                "schema_version",
                "status",
                "created_at_utc",
                "source_commit",
                "archive_sha256",
                "file_count",
                "files_verified",
                "live_state_sha256",
                "aggregate_counts",
                "review_values_exposed",
                "identifiers_exposed",
            )
        },
    )
    return {
        **certificate,
        "certificate_path": str(certificate_path),
        "certificate_sha256": sha256_file(certificate_path),
        "aggregate_safe_certificate_path": str(safe_path),
    }


def activate_default_preset(
    package_root: Path,
    *,
    source_commit: str,
    backup_certificate_path: Path,
    owner_quiet_window_confirmed: bool,
    cutover_timestamp_utc: str | None = None,
    cutover_id: str | None = None,
) -> dict[str, Any]:
    """Activate V3.1 without rewriting any existing queue or checkpoint file."""

    if owner_quiet_window_confirmed is not True:
        raise PermissionError("OWNER_QUIET_WINDOW_CONFIRMED=TRUE is required")
    package_root = require_restricted_destination(package_root)
    commit = _source_commit(source_commit)
    validate_active_protocol_v3(package_root)
    paths = active_protocol_paths(package_root)
    config_path = paths.interface_root / DEFAULT_PRESET_CONFIG_FILENAME
    if config_path.exists():
        raise FileExistsError("default preset configuration already exists")
    backup_certificate_path = require_restricted_destination(backup_certificate_path)
    backup = _load_object(backup_certificate_path)
    before = _review_state_files(package_root)
    if (
        backup.get("schema_version") != DEFAULT_PRESET_BACKUP_SCHEMA
        or backup.get("status") != CURRENT_REVIEW_PROGRESS_BACKED_UP
        or backup.get("source_commit") != commit
        or backup.get("live_state_sha256") != sha256_json(before)
        or not Path(str(backup.get("archive_path", ""))).is_file()
        or sha256_file(Path(str(backup.get("archive_path")))) != backup.get("archive_sha256")
    ):
        raise Tier1BlockedError(BLOCKED_LINEAGE, "production backup does not match live review state")
    counts_before = aggregate_review_state_counts(package_root)
    configuration = build_default_preset_configuration(
        source_commit=commit,
        cutover_timestamp_utc=cutover_timestamp_utc,
        cutover_id=cutover_id,
    )
    _atomic_write_json(config_path, configuration)
    loaded = load_default_preset_configuration(
        paths.interface_root, expected_source_commit=commit
    )
    if loaded != configuration:
        raise Tier1BlockedError(BLOCKED_LINEAGE, "activated preset configuration changed")
    after = _review_state_files(package_root)
    counts_after = aggregate_review_state_counts(package_root)
    if before != after or counts_before != counts_after:
        raise Tier1BlockedError(BLOCKED_LINEAGE, "existing review state changed during cutover")
    certificate = {
        "schema_version": DEFAULT_PRESET_ACTIVATION_SCHEMA,
        "status": DEFAULT_PRESET_PRODUCTION_VERIFIED,
        "readiness_status": READY_FOR_DEFAULT_ASSISTED_V3_HUMAN_AUDIT,
        "source_commit": commit,
        "preset_identifier": DEFAULT_PRESET_ID,
        "cutover": configuration["cutover"],
        "configuration_sha256": sha256_file(config_path),
        "backup_sha256": backup["archive_sha256"],
        "review_state_sha256_before": sha256_json(before),
        "review_state_sha256_after": sha256_json(after),
        "aggregate_counts_before": counts_before,
        "aggregate_counts_after": counts_after,
        "existing_records_modified": False,
        "pre_cutover_records_defaulted": False,
        "protected_media_modified": False,
        "clinical_annotations_generated": False,
    }
    certificate_path = package_root / "aggregate_safe" / "default_preset_activation_certificate.json"
    _atomic_write_json(certificate_path, certificate)
    return {**certificate, "certificate_path": str(certificate_path)}


def validate_default_preset_deployment(
    package_root: Path,
    *,
    source_commit: str,
) -> dict[str, Any]:
    package_root = require_restricted_destination(package_root)
    commit = _source_commit(source_commit)
    validation = validate_active_protocol_v3(package_root)
    paths = active_protocol_paths(package_root)
    config = load_default_preset_configuration(
        paths.interface_root, expected_source_commit=commit
    )
    if config is None:
        raise FileNotFoundError("default preset configuration is not active")
    return {
        "status": DEFAULT_PRESET_PRODUCTION_VERIFIED,
        "readiness_status": READY_FOR_DEFAULT_ASSISTED_V3_HUMAN_AUDIT,
        "source_commit": commit,
        "protocol_name": validation["protocol_name"],
        "preset_identifier": config["preset_identifier"],
        "cutover": config["cutover"],
        "aggregate_counts": aggregate_review_state_counts(package_root),
        "existing_records_modified": False,
        "pre_cutover_records_defaulted": False,
        "review_values_exposed": False,
        "identifiers_exposed": False,
    }
