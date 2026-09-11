"""SCC-local final review validation and immutable blinded audit handoff.

No fitting, image interpretation, target-value access, or annotation mutation.
All case-level material stays in the restricted finalization directory.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import pwd
import re
import socket
import tarfile
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import audit_protocol_v3 as v3
from .audit_default_preset import _review_state_files, load_default_preset_configuration
from .audit_team_progress import TeamProgressCoordinator
from .reduced_audit import TIER_A, TIER_C
from .reduced_audit_interface import (
    FINALIZED_LOCKED, ReviewerRegistry, RoleAwareCheckpointStore, RoleQueueStore,
)
from .safety import require_restricted_destination, sha256_file, sha256_json


SCHEMA = "JDIM_FINAL_AUDIT_LOCK_V1"
CONFIRM = "FINALIZE JDIM-D-26-02840 WITHOUT CHANGING REVIEWS"
BLOCKED = "BLOCKED_FINAL_REVIEW_INTEGRITY"


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load(path: Path) -> dict[str, Any]:
    result = json.loads(path.read_text())
    if not isinstance(result, dict):
        raise ValueError(BLOCKED + ": expected object")
    return result


def require(value: bool, reason: str) -> None:
    if not value:
        raise ValueError(BLOCKED + ": " + reason)


def write_once(path: Path, payload: Any) -> str:
    """Exclusive creation plus fsync; never replace an existing certificate."""
    raw = (json.dumps(payload, sort_keys=True, indent=2) + "\n").encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
        os.fchmod(stream.fileno(), 0o400)
    return sha256_file(path)


def owner_access(package_root: Path, expected_user: str) -> dict[str, Any]:
    root = require_restricted_destination(package_root)
    identity = pwd.getpwuid(os.geteuid())
    require(identity.pw_name == expected_user, "authenticated Unix account differs")
    host = socket.getfqdn()
    require(bool(re.fullmatch(r"scc[a-zA-Z0-9.-]*\.bu\.edu", host)), "SCC-local execution required")
    require(root == package_root.absolute() and not package_root.is_symlink(), "audit root must be canonical")
    metadata = root.stat()
    require(metadata.st_uid == os.geteuid() or metadata.st_gid in os.getgroups(), "project membership differs")
    require(not metadata.st_mode & 0o002, "world-writable audit root")
    restricted = root / "restricted"
    require(restricted.is_dir() and not restricted.stat().st_mode & 0o007, "restricted root permissions differ")
    return {"status": "OWNER_FINALIZATION_ACCESS_READY", "unix_account_verified": True,
            "project_membership_verified": True, "hostname": host, "browser_password_used": False}


def input_inventory(root: Path) -> dict[str, str]:
    paths = v3.active_protocol_paths(root)
    result = _review_state_files(root)
    for base in (paths.interface_root, root / "restricted/roster",
                 root / "restricted/queue", root / "restricted/checkpoints"):
        for path in sorted(base.rglob("*")):
            if path.is_file():
                require(not path.is_symlink(), "linked audit metadata not permitted")
                result[str(path.relative_to(root))] = sha256_file(path)
    for path in (paths.protocol_definition_path, paths.interface_policy_path):
        result[str(path.relative_to(root))] = sha256_file(path)
    for relative in result:
        require(not (root / relative).is_symlink(), "linked review state not permitted")
    return result


def source_identity(root: Path, review_source_commit: str) -> dict[str, Any]:
    """Verify versioned production metadata without reading annotation values."""
    paths = v3.active_protocol_paths(root)
    config = load_default_preset_configuration(paths.interface_root, expected_source_commit=review_source_commit)
    require(config is not None, "production default configuration absent")
    hashes = {}
    for name in ("default_preset_activation_certificate.json", "team_progress_activation_certificate.json"):
        path = root / "aggregate_safe" / name
        certificate = load(path)
        require(certificate.get("source_commit") == review_source_commit, "deployment certificate source differs")
        hashes[name] = sha256_file(path)
    policy = load(paths.interface_policy_path)
    for field in ("target_visible", "report_label_visible", "prediction_visible", "residual_visible",
                  "split_visible", "identifiers_or_paths_visible", "automated_annotation", "ocr_available"):
        require(policy.get(field) is False, "reader blinding policy differs")
    require(policy.get("localhost_only") is True, "reader network policy differs")
    return {"review_source_commit": review_source_commit, "deployment_certificate_hashes": hashes,
            "interface_policy_sha256": sha256_file(paths.interface_policy_path),
            "protocol_definition_sha256": sha256_file(paths.protocol_definition_path),
            "study_manifest_sha256": sha256_file(paths.study_manifest_path),
            "pilot_archive_manifest_sha256": sha256_file(paths.archive_manifest_path)}


def read_stores(root: Path, review_source_commit: str):
    """Initialize read-only stores. Do not instantiate the network service."""
    v3.validate_active_protocol_v3(root)
    paths = v3.active_protocol_paths(root)
    config = load_default_preset_configuration(paths.interface_root, expected_source_commit=review_source_commit)
    registry = ReviewerRegistry(root / "restricted/reviewer_registry", initialize=False)
    queue = RoleQueueStore(
        paths.queue_root, paths.policy_path, registry, initialize=False,
        expected_protocol_name=v3.PROTOCOL_V3_NAME, queue_schema=v3.V3_QUEUE_SCHEMA,
        event_schema=v3.V3_EVENT_SCHEMA, default_preset_configuration=config,
    )
    manifest = load(paths.study_manifest_path)
    require_blinded_keys(manifest)
    checkpoints = RoleAwareCheckpointStore(
        paths.checkpoint_root, queue, manifest, initialize=False,
        checkpoint_schema=v3.V3_CHECKPOINT_SCHEMA, lock_schema=v3.V3_LOCK_SCHEMA,
        clip_annotation_fields=v3.V3_CLIP_ANNOTATION_FIELDS,
        clip_presence_fields=v3.V3_CLIP_PRESENCE_FIELDS,
        required_clip_fields=v3.V3_REQUIRED_CLIP_ANNOTATION_FIELDS,
        study_annotation_fields=v3.V3_STUDY_ANNOTATION_FIELDS,
        study_outcome_fields=v3.V3_STUDY_OUTCOMES,
        source_only_study_outcome_fields=v3.V3_SOURCE_ONLY_STUDY_OUTCOMES,
        source_only_clip_fields=v3.V3_SOURCE_ONLY_CLIP_FIELDS,
        source_only_category_fields=v3.V3_SOURCE_ONLY_CATEGORY_FIELDS,
        source_only_free_text_fields=v3.V3_SOURCE_ONLY_FREE_TEXT_FIELDS,
        source_only_primary_field=v3.V3_SOURCE_ONLY_PRIMARY_FIELD,
        required_study_fields=v3.V3_REQUIRED_STUDY_ANNOTATION_FIELDS,
        derive_study_outcomes=True, default_preset_configuration=config,
    )
    return paths, registry, queue, checkpoints, manifest, config


def require_blinded_keys(value):
    forbidden = {"target", "target_membership", "study_id", "subject_id", "dicom_id", "split",
                 "report_label_value", "y_true", "y_pred", "prediction", "residual"}
    if isinstance(value, dict):
        require(not (set(value) & forbidden), "blinded payload contains prohibited identity or target field")
        for item in value.values():
            require_blinded_keys(item)
    elif isinstance(value, list):
        for item in value:
            require_blinded_keys(item)


def diagnose_integrity(root: Path, review_source_commit: str):
    """Counts only, to distinguish an invariant failure from a tooling exception."""
    before = input_inventory(root)
    counts = Counter()
    try:
        _, _, queue, checkpoints, manifest, _ = read_stores(root, review_source_commit)
        state = load(queue.state_path)
        for event in v3.aggregation_eligible_events_from_state(state):
            counts["eligible_events"] += 1
            try:
                status = checkpoints.review_state(event)
                counts["finalized_complete_reviews"] += int(status["workflow_state"] == FINALIZED_LOCKED and status["completion_validation_passed"])
                saved = checkpoints._validated_saved_annotations(event)
                if saved is None:
                    counts["missing_checkpoints"] += 1
                    continue
                annotations = saved[1]
                derived = v3.derive_study_summary(annotations["clips"],checkpoints.clip_evidence_tiers)
                summary = annotations["studies"].get(event["physical_study_token"],{})
                mismatch = sum(summary.get(k) != value for k,value in derived.items())
                counts["rollup_mismatch_reviews"] += bool(mismatch)
                counts["rollup_mismatch_fields"] += mismatch
                counts["confirmed_summary_reviews"] += summary.get("derived_summary_confirmed") == "yes"
            except Exception:
                counts["record_validation_exceptions"] += 1
    except Exception:
        counts["source_or_store_validation_exceptions"] += 1
    return {"operational_counts":dict(counts),"state_unchanged":before == input_inventory(root),
            "case_values_emitted":False}


def validate_records(queue, checkpoints, manifest, state):
    events = v3.aggregation_eligible_events_from_state(state)
    effective = [e for e in state["events"] if e["event_id"] not in state["superseded_event_ids"]]
    require(len(effective) == len(events), "incomplete or ineligible current review exists")
    require(len({e["event_id"] for e in events}) == len(events), "duplicate review event")
    primary, secondary, records = {}, {}, {}
    counts = Counter()
    studies = {s["audit_id"]: s for s in manifest["studies"]}
    require(len(studies) == len(manifest["studies"]), "duplicate manifest study")
    for event in events:
        result = checkpoints.review_state(event)
        require(result["workflow_state"] == FINALIZED_LOCKED, "review not finalized")
        require(result["completion_validation_passed"] and not result["stale_active_pointer"], "review completion or pointer differs")
        saved = checkpoints._validated_saved_annotations(event)
        require(saved is not None, "checkpoint unavailable")
        annotations = saved[1]
        study = event["physical_study_token"]
        require(event["role"] in {"primary", "secondary"}, "unexpected role")
        role_map = primary if event["role"] == "primary" else secondary
        require(study not in role_map, "duplicate study-role review")
        role_map[study] = event
        queue.registry.require_active_qualified(event["reviewer_code"])
        require(study in studies, "review outside selected manifest")
        expected_clips = {c["clip_audit_id"] for c in studies[study]["clips"]}
        require(set(annotations["clips"]) == expected_clips, "canonical clip inventory differs")
        require(set(annotations["studies"]) == {study}, "study summary inventory differs")
        derived = v3.derive_study_summary(annotations["clips"], checkpoints.clip_evidence_tiers)
        summary = annotations["studies"][study]
        require(all(summary.get(k) == val for k, val in derived.items()), "clip-to-study rollup differs")
        require(summary.get("derived_summary_confirmed") == "yes", "study summary not physician-confirmed")
        records[event["event_id"]] = annotations
        if event["role"] == "primary":
            tier = studies[study]["clips"][0]["evidence_tier"]
            require({c["evidence_tier"] for c in studies[study]["clips"]} == {tier}, "study evidence tiers differ")
            counts[tier + "_studies"] += 1
            counts[tier + "_clips"] += len(expected_clips)
            counts["confirmed_primary_clips"] += result["completed_clip_count"]
        else:
            counts["confirmed_secondary_clips"] += result["completed_clip_count"]
        counts["preset_reviews" if checkpoints._uses_default_preset(event) else "pre_preset_reviews"] += 1
    require(set(primary) == set(queue.policy["primary_queue"]), "Primary inventory differs from frozen queue")
    require(len(primary) == 30 and len(secondary) == 2, "final review count differs")
    require(counts[TIER_A + "_studies"] == 7 and counts[TIER_C + "_studies"] == 23, "evidence tier counts differ")
    require(set(secondary).issubset(queue.policy["formal_reliability_queue"]), "Secondary outside frozen repeat subset")
    valid_pairs = []
    for study, second in secondary.items():
        first = primary[study]
        if (first["reviewer_code"] != second["reviewer_code"]
                and first["event_id"] != second["event_id"]
                and second.get("formal_reliability") is True):
            valid_pairs.append(study)
    return events, records, primary, secondary, valid_pairs, dict(counts)


def validate(root: Path, review_source_commit: str) -> dict[str, Any]:
    before = input_inventory(root)
    paths, registry, queue, checkpoints, manifest, config = read_stores(root, review_source_commit)
    state = load(queue.state_path)
    events, records, primary, secondary, valid_pairs, counts = validate_records(queue, checkpoints, manifest, state)
    progress = TeamProgressCoordinator(root, queue, checkpoints, registry)._calculated()
    shared = progress["shared"]
    require(all(v["finalized"] == 15 for v in shared["target_progress"].values()), "target count differs")
    require(progress["owner"]["data_quality"]["reviews_blocked_by_unconfirmed_or_incomplete_clips"] == 0, "unconfirmed clips")
    require(before == input_inventory(root), "state changed during validation")
    safe = {
        "status": "FINAL_REVIEW_VALIDATION_PASS", "review_source_commit": review_source_commit,
        "protocol_name": v3.PROTOCOL_V3_NAME,
        "preset_version": config["preset_identifier"] if config else None,
        "progress_schema": shared["schema_version"], "primary_reviews": len(primary),
        "secondary_reviews": len(secondary), "valid_repeat_pairs": len(valid_pairs),
        "excluded_repeat_pairs": len(secondary) - len(valid_pairs),
        "target_counts": {k: v["finalized"] for k, v in shared["target_progress"].items()},
        "counts": counts, "active_claims": 0, "incomplete_reviews": 0,
        "unconfirmed_required_clips": 0, "rollup_mismatches": 0,
        "pilot_excluded_records": progress["owner"]["data_quality"]["pilot_excluded_records"],
        "state_sha256": sha256_json(before), "queue_policy_sha256": sha256_file(paths.policy_path),
        "repeat_subset_sha256": sha256_json(queue.policy["formal_reliability_queue"]),
        "selected_sample_sha256": sha256_json(queue.policy["primary_queue"]),
        "no_annotation_values_emitted": True,
    }
    return {"safe": safe, "inventory": before, "events": events, "records": records,
            "primary": primary, "secondary": secondary, "valid_pairs": valid_pairs,
            "manifest": manifest, "paths": paths}


@contextmanager
def quiet_state(root: Path):
    paths = v3.active_protocol_paths(root)
    with (paths.queue_root / ".queue.lock").open("r+") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def freeze(root: Path, output: Path, *, review_source_commit: str, tool_commit: str,
           expected_user: str, confirmation: str, review_service_stopped: bool) -> dict[str, Any]:
    access = owner_access(root, expected_user)
    require(confirmation == CONFIRM, "explicit confirmation phrase required")
    require(review_service_stopped is True, "review server must be stopped before finalization")
    output = require_restricted_destination(output)
    require(output.parent == root / "restricted/finalization", "finalization must remain under locked audit root")
    require(not output.exists(), "finalization destination already exists")
    require(bool(re.fullmatch(r"[0-9a-f]{40}", tool_commit)), "complete tooling source SHA required")
    with quiet_state(root):
        inventory = input_inventory(root)
        output.parent.mkdir(exist_ok=True, mode=0o700)
        output.mkdir(mode=0o700)
        base = {"schema_version": SCHEMA, "created_at_utc": now(), "tool_commit": tool_commit,
                "review_source_commit": review_source_commit, "audit_root": str(root),
                "state_sha256": sha256_json(inventory)}
        write_once(output / "input_inventory_restricted.json", inventory)
        archive = output / "final_review_state.tar"
        with archive.open("xb") as raw:
            with tarfile.open(fileobj=raw, mode="w") as tar:
                for relative in sorted(inventory):
                    tar.add(root / relative, arcname=relative, recursive=False)
            raw.flush()
            os.fsync(raw.fileno())
        os.chmod(archive, 0o400)
        with tarfile.open(archive) as tar:
            members = tar.getmembers()
            require(all(m.isfile() for m in members), "nonfile in review backup")
            require(len(members) == len(inventory), "backup count differs")
            for member in members:
                require(hashlib.sha256(tar.extractfile(member).read()).hexdigest()
                        == inventory.get(member.name), "backup content differs")
        require(inventory == input_inventory(root), "state changed during backup")
        backup = {**base, "status": "FINAL_AUDIT_STATE_BACKED_UP", "archive_sha256": sha256_file(archive),
                  "archive_path": str(archive), "files_verified": len(inventory),
                  "annotation_content_validation_started": False}
        write_once(output / "backup_certificate.json", backup)
        try:
            result = validate(root, review_source_commit)
        except Exception:
            write_once(output / "blocked_validation_certificate.json", {
                **base, "status": BLOCKED, "backup_sha256": sha256_file(archive),
                "original_state_unchanged": inventory == input_inventory(root),
                "annotation_values_emitted": False, "primary_lock_issued": False,
                "diagnostic":diagnose_integrity(root,review_source_commit)})
            raise
        safe = result["safe"]
        require(inventory == result["inventory"], "state changed before validation")
        primary = {**base, **safe, "schema_version": "JDIM_MANUAL_INPUT_CONTENT_AUDIT_PRIMARY_COMPLETE_V1",
                   "status": "PRIMARY_TARGET_AUDIT_LOCKED", "no_sample_substitutions": True,
                   "all_canonical_clips_reviewed": True, "prior_pilot_records_excluded": True,
                   "pre_preset_confirmation_basis": "attributable complete manually entered clips and finalized confirmed derived summary",
                   "preset_confirmation_basis": "per-clip attributable explicit physician confirmation"}
        write_once(output / "primary_completion_certificate.json", primary)
        amendment = {**base, "schema_version": "JDIM_REPEAT_REVIEW_FEASIBILITY_AMENDMENT_V1",
            "status": "REPEAT_REVIEW_COMPONENT_CLOSED", "original_intended_repeat_studies": 8,
            "completed_repeat_studies": 2, "valid_independent_pairs": len(result["valid_pairs"]),
            "excluded_pairs": 2-len(result["valid_pairs"]), "before_target_unblinding_and_aggregation": True,
            "reason": ["reviewer availability", "revision timeline", "optional procedural quality control"],
            "owner_attestation_not_findings_based": True, "additional_repeats_requested": False,
            "formal_reliability_coefficient_estimated": False,
            "classification": "LIMITED_INDEPENDENT_REPEAT_QC", "original_subset_unchanged": True}
        write_once(output / "repeat_feasibility_amendment.json", amendment)
        # Original attributable records remain untouched; this is a hash-bound snapshot.
        snapshot = {"events": result["events"], "records": result["records"],
                    "valid_repeat_studies": result["valid_pairs"], "manifest": result["manifest"]}
        write_once(output / "blinded_snapshot_restricted.json", snapshot)
        certificate = {**base, "status": "BLINDED_AUDIT_ANNOTATIONS_LOCKED",
            "snapshot_sha256": sha256_file(output / "blinded_snapshot_restricted.json"),
            "backup_sha256": sha256_file(archive), "primary_certificate_sha256": sha256_file(output / "primary_completion_certificate.json"),
            "repeat_amendment_sha256": sha256_file(output / "repeat_feasibility_amendment.json"),
            "input_inventory_sha256": sha256_file(output / "input_inventory_restricted.json"),
            "annotation_values_modified": False, "target_values_read": False,
            "formal_reliability_coefficients": False}
        write_once(output / "blinded_annotation_lock.json", certificate)
        write_once(output / "administrative_log.json", {**base, **access, "action": "final review freeze",
            "confirmation_phrase": confirmation, "review_service_stopped_confirmed": True,
            "certificate_sha256": sha256_file(output / "blinded_annotation_lock.json")})
        return {**safe, "status": certificate["status"], "output_root": str(output),
                "lock_sha256": sha256_file(output / "blinded_annotation_lock.json"),
                "backup_sha256": sha256_file(archive)}


def verify_lock(output: Path) -> dict[str, Any]:
    certificate = load(output / "blinded_annotation_lock.json")
    require(certificate["status"] == "BLINDED_AUDIT_ANNOTATIONS_LOCKED", "blinded lock absent")
    for filename, key in (("blinded_snapshot_restricted.json", "snapshot_sha256"),
                          ("input_inventory_restricted.json", "input_inventory_sha256"),
                          ("primary_completion_certificate.json", "primary_certificate_sha256"),
                          ("repeat_feasibility_amendment.json", "repeat_amendment_sha256"),
                          ("final_review_state.tar", "backup_sha256")):
        require(sha256_file(output / filename) == certificate[key], "locked artifact changed")
    require(input_inventory(Path(certificate["audit_root"])) == load(output / "input_inventory_restricted.json"), "live audit state changed after freeze")
    return certificate


FOCUSED_POSITIVE_FIELDS = frozenset({
    "waveform_or_measurement_tracing", "calipers", "lvot_vti_specific_label",
    "tapse_specific_label", "candidate_target_value_present",
    "source_only_lvot_vti_specific_label", "source_only_tapse_specific_label",
    "source_only_candidate_target_value", "source_only_spectral_doppler_waveform",
    "source_only_m_mode_tracing", "source_only_caliper",
    "source_only_contour_or_measurement_trace", "source_only_other_measurement_annotation",
})
DECISION_FIELDS = v3.V3_CLIP_ANNOTATION_FIELDS - {"restricted_notes", "reader_confidence"}
CANDIDATE_DETAILS = {"candidate_target_value", "visible_unit_text", "visible_measurement_name_text", "display_precision"}


def adjudication_reasons(primary: dict, secondary: dict | None) -> dict[str, list[str]]:
    """Mechanical focused triggers; no free-text interpretation or human decisions."""
    reasons: dict[str, set[str]] = {}

    def add(field, reason):
        reasons.setdefault(field, set()).add(reason)

    for field in DECISION_FIELDS:
        value = primary.get(field, "")
        if value == "uncertain":
            add(field, "uncertain")
        if value == "not_assessable" and field not in v3.V3_CLIP_FREE_TEXT_FIELDS:
            add(field, "not_assessable")
        if value == "yes" and field in FOCUSED_POSITIVE_FIELDS:
            add(field, "measurement_related_positive")
        if secondary is not None and value != secondary.get(field, ""):
            add(field, "paired_disagreement")
    content = primary.get("acquisition_content_type", "")
    if content in {"pulsed_wave_spectral_doppler", "continuous_wave_spectral_doppler", "m_mode"}:
        add("acquisition_content_type", "measurement_modality")
    if content in {"mixed", "other", "tissue_doppler"}:
        add("acquisition_content_type", "modality_relevance_resolution")
    if primary.get("candidate_target_value_present") in {"yes", "uncertain"} or str(primary.get("candidate_target_value", "")).strip():
        for field in CANDIDATE_DETAILS | {"candidate_target_value_present"}:
            add(field, "candidate_displayed_value")
    if primary.get("source_only_candidate_target_value") in {"yes", "uncertain"} or str(primary.get("source_only_candidate_target_value_text", "")).strip():
        for field in {"source_only_candidate_target_value", "source_only_candidate_target_value_text"}:
            add(field, "source_only_candidate_value")
    return {field: sorted(value) for field, value in sorted(reasons.items())}


def adjudication_fields(primary: dict, secondary: dict | None) -> list[str]:
    return sorted(adjudication_reasons(primary, secondary))


def make_queue(output: Path) -> dict[str, Any]:
    import secrets
    lock = verify_lock(output)
    snapshot = load(output / "blinded_snapshot_restricted.json")
    events = snapshot["events"]
    primary = {e["physical_study_token"]: e for e in events if e["role"] == "primary"}
    secondary = {e["physical_study_token"]: e for e in events if e["role"] == "secondary"}
    tasks, linkage = [], {}
    affected, by_reason, by_tier = set(), Counter(), Counter()
    for study in snapshot["manifest"]["studies"]:
        sid = study["audit_id"]
        first = snapshot["records"][primary[sid]["event_id"]]["clips"]
        second = snapshot["records"][secondary[sid]["event_id"]]["clips"] if sid in snapshot["valid_repeat_studies"] else {}
        for clip in study["clips"]:
            cid = clip["clip_audit_id"]
            reasons = adjudication_reasons(first[cid], second.get(cid))
            fields = sorted(reasons)
            if not fields:
                continue
            token = "ADJ-" + secrets.token_hex(12).upper()
            tasks.append({"token": token, "evidence_tier": clip["evidence_tier"], "fields": fields,
                "reasons": reasons,
                "primary": {f: first[cid].get(f, "") for f in fields},
                "secondary": {f: second[cid].get(f, "") for f in fields} if cid in second else None})
            linkage[token] = {"study": sid, "clip": cid, "source_media_id": clip["source_media_id"],
                              "model_input_media_id": clip.get("model_input_media_id", "")}
            affected.add(sid)
            by_tier[clip["evidence_tier"]] += 1
            by_reason.update(set(r for rs in reasons.values() for r in rs))
    queue = {"schema_version": "JDIM_BLINDED_ADJUDICATION_QUEUE_V1", "annotation_lock_sha256": sha256_file(output / "blinded_annotation_lock.json"), "tasks": tasks}
    require_blinded_keys(queue)
    write_once(output / "adjudication_queue_restricted.json", queue)
    write_once(output / "adjudication_linkage_restricted.json", linkage)
    status = "BLINDED_ADJUDICATION_QUEUE_LOCKED" if tasks else "BLINDED_ADJUDICATION_NOT_REQUIRED"
    certificate = {"schema_version": SCHEMA, "status": status, "created_at_utc": now(),
        "annotation_lock_sha256": sha256_file(output / "blinded_annotation_lock.json"),
        "queue_sha256": sha256_file(output / "adjudication_queue_restricted.json"),
        "linkage_sha256": sha256_file(output / "adjudication_linkage_restricted.json"),
        "queue_clips": len(tasks), "queue_fields": sum(len(t["fields"]) for t in tasks),
        "affected_studies": len(affected), "items_by_reason": dict(by_reason), "items_by_tier": dict(by_tier),
        "target_values_read": False, "clinical_decisions_generated": False}
    write_once(output / "adjudication_queue_certificate.json", certificate)
    return certificate
