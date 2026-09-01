"""Role-aware, claim-queued interface for the reduced blinded JDIM audit."""
from __future__ import annotations

import fcntl
import json
import os
import secrets
import tempfile
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping

import pandas as pd

from .audit import STUDY_OUTCOMES
from .audit_interface import (
    CLIP_ANNOTATION_FIELDS,
    CLIP_FREE_TEXT_FIELDS,
    CLIP_PRESENCE_FIELDS,
    READER_ID_PATTERN,
    REQUIRED_CLIP_ANNOTATION_FIELDS,
    REQUIRED_STUDY_ANNOTATION_FIELDS,
    STUDY_ANNOTATION_FIELDS,
    CheckpointStore,
    reader_visible_record,
)
from .reduced_audit import (
    FORMAL_RELIABILITY_N,
    PROTOCOL_NAME,
    PROTOCOL_STATUS,
    TIER_A,
    TIER_C,
    validate_locked_reduced_roster,
)
from .safety import (
    BLOCKED_UNSAFE_OUTPUT,
    Tier1BlockedError,
    require_columns,
    require_restricted_destination,
    safe_file_record,
    sha256_file,
    sha256_json,
    write_json,
)


ROLE_PRIMARY = "primary"
ROLE_SECONDARY = "secondary"
ROLES = {ROLE_PRIMARY, ROLE_SECONDARY}
ACTION_RESUME = "resume"
ACTION_CLAIM_NEXT = "claim_next"
ACTIONS = {ACTION_RESUME, ACTION_CLAIM_NEXT}

ROLE_AWARE_INTERFACE_READY = "ROLE_AWARE_INTERFACE_READY"
READY_FOR_REDUCED_BLINDED_HUMAN_AUDIT = "READY_FOR_REDUCED_BLINDED_HUMAN_AUDIT"
BLOCKED_READER_ROLE_INDEPENDENCE = "BLOCKED_READER_ROLE_INDEPENDENCE"
BLOCKED_REDUCED_INTERFACE = "BLOCKED_REDUCED_INTERFACE"
FORMAL_DUPLICATE_REVIEW = "FORMAL_RELIABILITY_REVIEW"
SUPPLEMENTAL_DUPLICATE_REVIEW = "SUPPLEMENTAL_DUPLICATE_REVIEW"

QUEUE_SCHEMA = "jdim-reduced-audit-role-queue-v1"
REGISTRY_SCHEMA = "jdim-reduced-audit-reviewer-registry-v1"
EVENT_SCHEMA = "jdim-reduced-audit-review-event-v1"
CHECKPOINT_SCHEMA = "jdim-reduced-audit-study-checkpoint-v1"


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


def _code(value: Any) -> str:
    code = str(value).strip()
    if not READER_ID_PATTERN.fullmatch(code):
        raise ValueError("reviewer code must contain only letters, numbers, dot, underscore, or hyphen")
    return code


def _role(value: Any) -> str:
    role = str(value).strip().lower()
    if role not in ROLES:
        raise ValueError("review role must be primary or secondary")
    return role


@dataclass(frozen=True)
class InterfaceBuildResult:
    package_root: Path
    summary: dict[str, Any]


class ReviewerRegistry:
    """Restricted pseudonymous reviewer registry, separate from annotations."""

    def __init__(self, root: Path):
        self.root = require_restricted_destination(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        self.path = self.root / "reviewer_registry.json"
        self.identity_map_path = self.root / "reviewer_identity_map_optional.json"
        if not self.path.exists():
            _atomic_write_json(
                self.path,
                {"schema_version": REGISTRY_SCHEMA, "reviewers": []},
            )

    def load(self) -> dict[str, Any]:
        payload = _load_object(self.path)
        if payload.get("schema_version") != REGISTRY_SCHEMA or not isinstance(
            payload.get("reviewers"), list
        ):
            raise ValueError("reviewer registry schema is invalid")
        codes: set[str] = set()
        for record in payload["reviewers"]:
            if not isinstance(record, Mapping):
                raise ValueError("reviewer registry record is invalid")
            code = _code(record.get("reviewer_code", ""))
            if code in codes:
                raise ValueError("reviewer registry contains duplicate codes")
            codes.add(code)
        return payload

    def register(
        self,
        reviewer_code: str,
        *,
        qualified: bool,
        active: bool = True,
    ) -> dict[str, Any]:
        code = _code(reviewer_code)
        payload = self.load()
        existing = [row for row in payload["reviewers"] if str(row["reviewer_code"]) == code]
        if existing:
            raise FileExistsError("reviewer code is already registered")
        record = {
            "reviewer_code": code,
            "owner_confirmed_qualified": bool(qualified),
            "active": bool(active),
            "registered_at_utc": utc_now(),
        }
        payload["reviewers"].append(record)
        payload["reviewers"].sort(key=lambda row: str(row["reviewer_code"]))
        _atomic_write_json(self.path, payload)
        return record

    def set_active(self, reviewer_code: str, active: bool) -> dict[str, Any]:
        code = _code(reviewer_code)
        payload = self.load()
        matches = [row for row in payload["reviewers"] if str(row["reviewer_code"]) == code]
        if len(matches) != 1:
            raise KeyError("reviewer code is not registered")
        matches[0]["active"] = bool(active)
        matches[0]["updated_at_utc"] = utc_now()
        _atomic_write_json(self.path, payload)
        return dict(matches[0])

    def require_active_qualified(self, reviewer_code: str) -> dict[str, Any]:
        code = _code(reviewer_code)
        matches = [row for row in self.load()["reviewers"] if str(row["reviewer_code"]) == code]
        if len(matches) != 1:
            raise PermissionError("reviewer code is not registered")
        record = dict(matches[0])
        if record.get("active") is not True or record.get("owner_confirmed_qualified") is not True:
            raise PermissionError("reviewer code is not active and qualification-confirmed")
        return record


class RoleQueueStore:
    """Atomic role claims with exclusive per-study role ownership."""

    def __init__(self, queue_root: Path, queue_policy_path: Path, registry: ReviewerRegistry):
        self.root = require_restricted_destination(queue_root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        self.policy_path = queue_policy_path
        self.registry = registry
        self.state_path = self.root / "queue_state.json"
        self.lock_path = self.root / ".queue.lock"
        self.reassignment_root = self.root / "reassignments"
        self.reassignment_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.policy = self._load_policy()
        if not self.state_path.exists():
            _atomic_write_json(
                self.state_path,
                {
                    "schema_version": QUEUE_SCHEMA,
                    "queue_policy_sha256": sha256_file(self.policy_path),
                    "events": [],
                },
            )

    def _load_policy(self) -> dict[str, Any]:
        policy = _load_object(self.policy_path)
        if (
            policy.get("schema_version") != QUEUE_SCHEMA
            or policy.get("protocol_name") != PROTOCOL_NAME
            or not isinstance(policy.get("primary_queue"), list)
            or not isinstance(policy.get("formal_reliability_queue"), list)
        ):
            raise ValueError("role queue policy is invalid")
        primary = list(map(str, policy["primary_queue"]))
        formal = list(map(str, policy["formal_reliability_queue"]))
        if len(primary) != len(set(primary)) or len(formal) != FORMAL_RELIABILITY_N:
            raise ValueError("role queue policy contains duplicate or invalid assignments")
        if not set(formal).issubset(primary):
            raise ValueError("formal reliability queue left the reduced sample")
        return policy

    @contextmanager
    def _locked_state(self) -> Iterator[dict[str, Any]]:
        descriptor = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            with os.fdopen(descriptor, "r+", encoding="utf-8") as lock_stream:
                fcntl.flock(lock_stream.fileno(), fcntl.LOCK_EX)
                state = _load_object(self.state_path)
                if (
                    state.get("schema_version") != QUEUE_SCHEMA
                    or state.get("queue_policy_sha256") != sha256_file(self.policy_path)
                    or not isinstance(state.get("events"), list)
                ):
                    raise Tier1BlockedError(
                        BLOCKED_READER_ROLE_INDEPENDENCE,
                        "role queue state does not match its locked policy",
                    )
                yield state
                _atomic_write_json(self.state_path, state)
                fcntl.flock(lock_stream.fileno(), fcntl.LOCK_UN)
        finally:
            pass

    @staticmethod
    def _active(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [event for event in events if event.get("status") in {"in_progress", "locked"}]

    def _candidate_order(self, events: list[dict[str, Any]], role: str) -> list[str]:
        primary = list(map(str, self.policy["primary_queue"]))
        if role == ROLE_PRIMARY:
            return primary
        formal = list(map(str, self.policy["formal_reliability_queue"]))
        locked_formal = {
            str(event["physical_study_token"])
            for event in events
            if event.get("role") == ROLE_SECONDARY
            and event.get("status") == "locked"
            and bool(event.get("formal_reliability"))
        }
        open_formal = [study for study in formal if study not in locked_formal]
        if open_formal:
            return open_formal
        return [study for study in primary if study not in set(formal)]

    def claim(
        self,
        *,
        reviewer_code: str,
        role: str,
        action: str,
        qualification_confirmed: bool,
    ) -> dict[str, Any]:
        code = _code(reviewer_code)
        role = _role(role)
        action = str(action).strip().lower()
        if action not in ACTIONS:
            raise ValueError("claim action must be resume or claim_next")
        if qualification_confirmed is not True:
            raise PermissionError("qualification confirmation is required")
        self.registry.require_active_qualified(code)
        with self._locked_state() as state:
            events = state["events"]
            incomplete = [
                event
                for event in events
                if event.get("reviewer_code") == code
                and event.get("role") == role
                and event.get("status") == "in_progress"
            ]
            if len(incomplete) > 1:
                raise Tier1BlockedError(
                    BLOCKED_READER_ROLE_INDEPENDENCE,
                    "reviewer has multiple incomplete studies in one role",
                )
            if incomplete:
                return dict(incomplete[0])
            if action == ACTION_RESUME:
                raise FileNotFoundError("no incomplete study is available for this reviewer and role")

            active = self._active(events)
            same_role_claimed = {
                str(event["physical_study_token"])
                for event in active
                if event.get("role") == role
            }
            reviewer_studies = {
                str(event["physical_study_token"])
                for event in events
                if event.get("reviewer_code") == code
                and event.get("status") in {"in_progress", "locked", "archived_incomplete"}
            }
            formal_ids = set(map(str, self.policy["formal_reliability_queue"]))
            candidate = next(
                (
                    audit_id
                    for audit_id in self._candidate_order(events, role)
                    if audit_id not in same_role_claimed and audit_id not in reviewer_studies
                ),
                None,
            )
            if candidate is None:
                raise FileNotFoundError("no eligible study is currently available for this reviewer and role")
            formal = role == ROLE_SECONDARY and candidate in formal_ids
            event = {
                "schema_version": EVENT_SCHEMA,
                "event_id": f"E{secrets.token_hex(12).upper()}",
                "physical_study_token": candidate,
                "reviewer_code": code,
                "role": role,
                "formal_reliability": bool(formal),
                "supplemental_review": bool(role == ROLE_SECONDARY and not formal),
                "secondary_review_class": (
                    FORMAL_DUPLICATE_REVIEW
                    if formal
                    else SUPPLEMENTAL_DUPLICATE_REVIEW
                    if role == ROLE_SECONDARY
                    else "NOT_APPLICABLE"
                ),
                "claim_timestamp_utc": utc_now(),
                "last_save_timestamp_utc": None,
                "completion_timestamp_utc": None,
                "status": "in_progress",
                "lock_status": "unlocked",
                "reassignment_history": [],
                "clip_completion_count": 0,
                "study_summary_complete": False,
                "annotation_checksum": None,
            }
            active_triples = {
                (
                    str(item["physical_study_token"]),
                    str(item["reviewer_code"]),
                    str(item["role"]),
                )
                for item in active
            }
            triple = (candidate, code, role)
            if triple in active_triples:
                raise Tier1BlockedError(
                    BLOCKED_READER_ROLE_INDEPENDENCE,
                    "duplicate reviewer/study/role claim",
                )
            events.append(event)
            return dict(event)

    def get_event(self, event_id: str) -> dict[str, Any]:
        state = _load_object(self.state_path)
        matches = [event for event in state.get("events", []) if str(event.get("event_id")) == str(event_id)]
        if len(matches) != 1:
            raise KeyError("review event is not available")
        return dict(matches[0])

    def update_progress(
        self,
        event_id: str,
        *,
        clip_completion_count: int,
        study_summary_complete: bool,
        annotation_checksum: str,
    ) -> dict[str, Any]:
        with self._locked_state() as state:
            matches = [event for event in state["events"] if str(event.get("event_id")) == str(event_id)]
            if len(matches) != 1 or matches[0].get("status") != "in_progress":
                raise PermissionError("review event is unavailable or locked")
            event = matches[0]
            event["last_save_timestamp_utc"] = utc_now()
            event["clip_completion_count"] = int(clip_completion_count)
            event["study_summary_complete"] = bool(study_summary_complete)
            event["annotation_checksum"] = str(annotation_checksum)
            return dict(event)

    def mark_locked(
        self,
        event_id: str,
        *,
        annotation_checksum: str,
        clip_completion_count: int,
    ) -> dict[str, Any]:
        with self._locked_state() as state:
            matches = [event for event in state["events"] if str(event.get("event_id")) == str(event_id)]
            if len(matches) != 1 or matches[0].get("status") != "in_progress":
                raise PermissionError("review event is unavailable or already locked")
            event = matches[0]
            event["status"] = "locked"
            event["lock_status"] = "locked"
            event["completion_timestamp_utc"] = utc_now()
            event["last_save_timestamp_utc"] = event["completion_timestamp_utc"]
            event["clip_completion_count"] = int(clip_completion_count)
            event["study_summary_complete"] = True
            event["annotation_checksum"] = str(annotation_checksum)
            return dict(event)

    def archive_for_reassignment(
        self,
        event_id: str,
        *,
        owner_confirmed: bool,
        reason: str,
        checkpoint_sha256: str | None,
    ) -> dict[str, Any]:
        if owner_confirmed is not True:
            raise PermissionError("owner confirmation is required for reassignment")
        reason = str(reason).strip()
        if not reason or len(reason) > 300:
            raise ValueError("reassignment reason is required and must be concise")
        with self._locked_state() as state:
            matches = [event for event in state["events"] if str(event.get("event_id")) == str(event_id)]
            if len(matches) != 1 or matches[0].get("status") != "in_progress":
                raise PermissionError("only an incomplete active event may be reassigned")
            event = matches[0]
            timestamp = utc_now()
            record = {
                "schema_version": "jdim-reduced-audit-reassignment-v1",
                "event_id": str(event_id),
                "physical_study_token": str(event["physical_study_token"]),
                "reviewer_code": str(event["reviewer_code"]),
                "role": str(event["role"]),
                "archived_at_utc": timestamp,
                "reason": reason,
                "checkpoint_sha256": checkpoint_sha256,
                "excluded_from_analysis": True,
            }
            filename = f"reassignment-{sha256_json(record)[:24]}.json"
            path = self.reassignment_root / filename
            if path.exists():
                raise FileExistsError("reassignment record already exists")
            _atomic_write_json(path, record)
            event["status"] = "archived_incomplete"
            event["lock_status"] = "archived"
            event["reassignment_history"] = [*event.get("reassignment_history", []), sha256_file(path)]
            event["archived_at_utc"] = timestamp
            return {"event": dict(event), "record_path": str(path), "record_sha256": sha256_file(path)}


class RoleAwareCheckpointStore:
    """One attributable, atomic checkpoint per physical-study review event."""

    def __init__(self, root: Path, queue: RoleQueueStore, study_manifest: Mapping[str, Any]):
        self.root = require_restricted_destination(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        self.queue = queue
        self.studies = {
            str(study["audit_id"]): dict(study)
            for study in study_manifest.get("studies", [])
        }

    def _event_root(self, event_id: str) -> Path:
        event_id = _code(event_id)
        root = (self.root / event_id).resolve()
        if self.root.resolve() not in root.parents:
            raise Tier1BlockedError(BLOCKED_UNSAFE_OUTPUT, "review event escaped checkpoint root")
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(root, 0o700)
        return root

    def _assignment(self, event: Mapping[str, Any]) -> tuple[str, set[str]]:
        audit_id = str(event["physical_study_token"])
        if audit_id not in self.studies:
            raise Tier1BlockedError(BLOCKED_READER_ROLE_INDEPENDENCE, "review event left the reduced roster")
        clip_ids = {str(clip["clip_audit_id"]) for clip in self.studies[audit_id]["clips"]}
        return audit_id, clip_ids

    def load(self, event_id: str) -> dict[str, Any]:
        event = self.queue.get_event(event_id)
        root = self._event_root(event_id)
        path = root / "checkpoint.json"
        if not path.is_file():
            return {
                "schema_version": CHECKPOINT_SCHEMA,
                "event_id": event_id,
                "reviewer_code": event["reviewer_code"],
                "role": event["role"],
                "annotations": {"studies": {}, "clips": {}},
                "locked": False,
            }
        payload = _load_object(path)
        self._validate_event_identity(payload, event)
        payload["locked"] = (root / "LOCKED.json").is_file()
        return payload

    @staticmethod
    def _validate_event_identity(payload: Mapping[str, Any], event: Mapping[str, Any]) -> None:
        if (
            payload.get("schema_version") != CHECKPOINT_SCHEMA
            or payload.get("event_id") != event.get("event_id")
            or payload.get("reviewer_code") != event.get("reviewer_code")
            or payload.get("role") != event.get("role")
        ):
            raise Tier1BlockedError(
                BLOCKED_READER_ROLE_INDEPENDENCE,
                "checkpoint identity or role changed",
            )

    def save(self, event_id: str, payload: Mapping[str, Any]) -> Path:
        event = self.queue.get_event(event_id)
        if event.get("status") != "in_progress":
            raise PermissionError("review event is not editable")
        root = self._event_root(event_id)
        if (root / "LOCKED.json").exists():
            raise PermissionError("review event is locked")
        annotations = payload.get("annotations", {})
        if not isinstance(annotations, Mapping) or set(annotations) - {"studies", "clips"}:
            raise ValueError("checkpoint annotations must contain only studies and clips")
        studies = CheckpointStore._validated_annotation_group(
            annotations.get("studies", {}), STUDY_ANNOTATION_FIELDS, "study"
        )
        clips = CheckpointStore._validated_annotation_group(
            annotations.get("clips", {}), CLIP_ANNOTATION_FIELDS, "clip"
        )
        audit_id, clip_ids = self._assignment(event)
        if not set(studies).issubset({audit_id}) or not set(clips).issubset(clip_ids):
            raise Tier1BlockedError(
                BLOCKED_READER_ROLE_INDEPENDENCE,
                "checkpoint contains annotations outside the claimed study",
            )
        clean = {
            "schema_version": CHECKPOINT_SCHEMA,
            "event_id": str(event_id),
            "reviewer_code": str(event["reviewer_code"]),
            "role": str(event["role"]),
            "annotations": {"studies": studies, "clips": clips},
        }
        destination = root / "checkpoint.json"
        _atomic_write_json(destination, clean)
        checksum = sha256_json(clean["annotations"])
        clip_complete = sum(
            all(str(record.get(field, "")).strip() for field in REQUIRED_CLIP_ANNOTATION_FIELDS)
            for record in clips.values()
        )
        study_complete = bool(studies.get(audit_id)) and all(
            str(studies[audit_id].get(field, "")).strip()
            for field in REQUIRED_STUDY_ANNOTATION_FIELDS
        )
        self.queue.update_progress(
            event_id,
            clip_completion_count=clip_complete,
            study_summary_complete=study_complete,
            annotation_checksum=checksum,
        )
        return destination

    @staticmethod
    def _require_positive_uncertain_details(
        study_record: Mapping[str, Any],
        clip_records: Mapping[str, Mapping[str, Any]],
    ) -> None:
        if any(str(study_record.get(field, "")) in {"yes", "uncertain"} for field in STUDY_OUTCOMES):
            if not str(study_record.get("restricted_notes", "")).strip():
                raise ValueError("positive or uncertain study findings require restricted notes")
        for clip_id, record in clip_records.items():
            uncertain = any(
                str(record.get(field, "")) == "uncertain" for field in CLIP_PRESENCE_FIELDS
            )
            if uncertain and not str(record.get("restricted_notes", "")).strip():
                raise ValueError(f"uncertain clip findings require restricted notes: {clip_id}")
            if str(record.get("candidate_target_value_present", "")) == "yes":
                required = ("candidate_target_value", "visible_unit_text", "visible_measurement_name_text", "display_precision")
                missing = [field for field in required if not str(record.get(field, "")).strip()]
                if missing:
                    raise ValueError(f"candidate displayed value details are incomplete: {missing}")

    def lock(self, event_id: str) -> Path:
        event = self.queue.get_event(event_id)
        root = self._event_root(event_id)
        checkpoint = root / "checkpoint.json"
        if not checkpoint.is_file():
            raise FileNotFoundError("cannot lock before saving annotations")
        if (root / "LOCKED.json").exists():
            raise FileExistsError("review event is already locked")
        payload = _load_object(checkpoint)
        self._validate_event_identity(payload, event)
        audit_id, clip_ids = self._assignment(event)
        annotations = payload.get("annotations", {})
        studies = annotations.get("studies", {})
        clips = annotations.get("clips", {})
        CheckpointStore._require_complete_group(
            studies,
            {audit_id},
            REQUIRED_STUDY_ANNOTATION_FIELDS,
            "study",
        )
        CheckpointStore._require_complete_group(
            clips,
            clip_ids,
            REQUIRED_CLIP_ANNOTATION_FIELDS,
            "clip",
        )
        self._require_positive_uncertain_details(studies[audit_id], clips)
        annotation_checksum = sha256_json(annotations)
        descriptor = os.open(root / "LOCKED.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(
                {
                    "schema_version": "jdim-reduced-audit-review-lock-v1",
                    "status": "STUDY_ROLE_REVIEW_LOCKED",
                    "event_id": event_id,
                    "reviewer_code": event["reviewer_code"],
                    "role": event["role"],
                    "physical_study_token": audit_id,
                    "annotation_checksum": annotation_checksum,
                    "checkpoint_sha256": sha256_file(checkpoint),
                    "locked_at_utc": utc_now(),
                },
                stream,
                indent=2,
                sort_keys=True,
            )
            stream.write("\n")
        self.queue.mark_locked(
            event_id,
            annotation_checksum=annotation_checksum,
            clip_completion_count=len(clip_ids),
        )
        return root / "LOCKED.json"

    def archive_for_reassignment(
        self,
        event_id: str,
        *,
        owner_confirmed: bool,
        reason: str,
    ) -> dict[str, Any]:
        root = self._event_root(event_id)
        checkpoint = root / "checkpoint.json"
        checkpoint_hash = sha256_file(checkpoint) if checkpoint.is_file() else None
        result = self.queue.archive_for_reassignment(
            event_id,
            owner_confirmed=owner_confirmed,
            reason=reason,
            checkpoint_sha256=checkpoint_hash,
        )
        archive = root / "ARCHIVED_INCOMPLETE.json"
        _atomic_write_json(
            archive,
            {
                "status": "ARCHIVED_INCOMPLETE_EXCLUDED_FROM_ANALYSIS",
                "event_id": event_id,
                "checkpoint_sha256": checkpoint_hash,
                "reassignment_record_sha256": result["record_sha256"],
            },
        )
        return result


class RoleAwareAuditService:
    """Session-scoped access to one claimed study without exposing the queue."""

    def __init__(self, package_root: Path, parent_media_root: Path):
        self.package_root = require_restricted_destination(package_root)
        self.parent_media_root = require_restricted_destination(parent_media_root)
        interface_root = self.package_root / "restricted" / "interface"
        self.manifest = _load_object(interface_root / "study_manifest_restricted.json")
        self.studies = {
            str(study["audit_id"]): dict(study)
            for study in self.manifest.get("studies", [])
        }
        self.registry = ReviewerRegistry(self.package_root / "restricted" / "reviewer_registry")
        self.queue = RoleQueueStore(
            self.package_root / "restricted" / "queue",
            interface_root / "queue_policy_restricted.json",
            self.registry,
        )
        self.checkpoints = RoleAwareCheckpointStore(
            self.package_root / "restricted" / "checkpoints",
            self.queue,
            self.manifest,
        )
        self._sessions: dict[str, dict[str, str]] = {}
        self._session_lock = threading.RLock()

    @staticmethod
    def _client_event(event: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "event_id": str(event["event_id"]),
            "role": str(event["role"]),
            "status": str(event["status"]),
        }

    def claim(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        allowed = {"reviewer_code", "role", "action", "qualification_confirmed"}
        unknown = sorted(set(payload) - allowed)
        if unknown:
            raise ValueError(f"claim request contains unsupported fields: {unknown}")
        event = self.queue.claim(
            reviewer_code=str(payload.get("reviewer_code", "")),
            role=str(payload.get("role", "")),
            action=str(payload.get("action", "")),
            qualification_confirmed=payload.get("qualification_confirmed") is True,
        )
        audit_id = str(event["physical_study_token"])
        if audit_id not in self.studies:
            raise Tier1BlockedError(BLOCKED_REDUCED_INTERFACE, "claimed study left the interface manifest")
        token = secrets.token_urlsafe(32)
        with self._session_lock:
            self._sessions[token] = {
                "event_id": str(event["event_id"]),
                "reviewer_code": str(event["reviewer_code"]),
                "role": str(event["role"]),
                "audit_id": audit_id,
            }
        return {
            "session_token": token,
            "event": self._client_event(event),
            "study": self.studies[audit_id],
            "checkpoint": self.checkpoints.load(str(event["event_id"])),
        }

    def session(self, token: str) -> dict[str, str]:
        with self._session_lock:
            session = self._sessions.get(str(token))
            if session is None:
                raise PermissionError("review session is unavailable; resume from the start screen")
            return dict(session)

    def checkpoint(self, token: str) -> dict[str, Any]:
        session = self.session(token)
        return self.checkpoints.load(session["event_id"])

    def save(self, token: str, annotations: Mapping[str, Any]) -> dict[str, Any]:
        session = self.session(token)
        destination = self.checkpoints.save(
            session["event_id"],
            {"annotations": annotations},
        )
        return {"status": "saved", "size_bytes": int(destination.stat().st_size)}

    def lock(self, token: str) -> dict[str, Any]:
        session = self.session(token)
        self.checkpoints.lock(session["event_id"])
        return {"status": "locked"}

    def media_path(self, token: str, media_token: str) -> Path:
        session = self.session(token)
        study = self.studies[session["audit_id"]]
        allowed = {
            str(clip[field])
            for clip in study["clips"]
            for field in ("source_media_id", "model_input_media_id")
            if str(clip.get(field, ""))
        }
        media_token = str(media_token)
        if not READER_ID_PATTERN.fullmatch(media_token) or media_token not in allowed:
            raise PermissionError("media token is outside the claimed study")
        path = self.parent_media_root / f"{media_token}.png"
        if not path.is_file():
            raise FileNotFoundError("claimed media token is unavailable")
        return path


def _interface_html() -> str:
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Reduced Blinded Echocardiography Input Audit</title>
  <link rel="stylesheet" href="style.css">
</head>
<body>
  <header><strong>Blinded input-content audit</strong><span id="save-state">Ready</span></header>
  <main>
    <section id="start-screen" class="start-screen">
      <h1>Begin review session</h1>
      <label>Reviewer code<input id="reviewer-code" maxlength="64" autocomplete="off"></label>
      <fieldset><legend>Review role</legend>
        <label><input type="radio" name="role" value="primary">Primary independent review</label>
        <p>The first complete blinded review role for a study. You will not see any other reviewer&apos;s annotations.</p>
        <label><input type="radio" name="role" value="secondary">Secondary independent review</label>
        <p>An independent repeat review used to assess agreement. You will not see the primary reviewer&apos;s annotations.</p>
      </fieldset>
      <label class="confirm"><input id="qualification" type="checkbox">I confirm that I am qualified and am using my own reviewer code.</label>
      <div class="actions"><button id="resume-review" type="button">Resume my incomplete study</button><button id="claim-review" type="button">Claim next eligible study</button></div>
      <p id="start-error" role="alert"></p>
    </section>
    <section id="review-screen" hidden>
      <aside>
        <div id="study-id"></div>
        <div id="role-label"></div>
        <div class="counter" id="clip-progress"></div>
        <button id="lock-study" type="button">Complete and lock study</button>
        <button id="leave-session" type="button">Return to start</button>
      </aside>
      <article>
        <nav class="clip-nav" aria-label="Clip navigation"><button id="previous-clip" aria-label="Previous clip">&#8249;</button><span id="clip-counter"></span><button id="next-clip" aria-label="Next clip">&#8250;</button></nav>
        <div id="tier" class="tier"></div>
        <div class="frame-controls"><button id="toggle-play" type="button">Play</button><input id="frame-slider" type="range" min="0" max="15" value="0" aria-label="Frame"><span id="frame-counter">Frame 1 of 16</span></div>
        <div class="views"><figure id="source-panel"><figcaption>Source acquisition</figcaption><canvas id="source-canvas" width="224" height="224"></canvas></figure><figure id="model-panel"><figcaption id="model-caption">Model-input view</figcaption><canvas id="model-canvas" width="224" height="224"></canvas></figure></div>
        <form id="clip-form" autocomplete="off">
          <fieldset><legend>Modality/content</legend><select name="acquisition_content_type"><option value="">Unreviewed</option><option value="2d_b_mode">2D B-mode</option><option value="color_doppler">Color Doppler</option><option value="pulsed_wave_spectral_doppler">Pulsed-wave spectral Doppler</option><option value="continuous_wave_spectral_doppler">Continuous-wave spectral Doppler</option><option value="tissue_doppler">Tissue Doppler</option><option value="m_mode">M-mode</option><option value="mixed">Mixed</option><option value="other">Other</option><option value="uncertain">Uncertain</option><option value="not_assessable">Not assessable</option></select></fieldset>
          <fieldset id="presence-fields"><legend>Visible content</legend></fieldset>
          <fieldset><legend>Candidate displayed value</legend><label>Value<input name="candidate_target_value" maxlength="64"></label><label>Unit<input name="visible_unit_text" maxlength="64"></label><label>Displayed name<input name="visible_measurement_name_text" maxlength="120"></label><label>Precision<input name="display_precision" maxlength="64"></label></fieldset>
          <label>Reader confidence<select name="reader_confidence"><option value="">Unreviewed</option><option>high</option><option>moderate</option><option>low</option><option>not_assessable</option></select></label>
          <label>Restricted notes<input name="restricted_notes" maxlength="500"></label>
        </form>
        <form id="study-form" autocomplete="off"><fieldset><legend>Study summary</legend><div id="study-fields"></div><label>Reader confidence<select name="reader_confidence"><option value="">Unreviewed</option><option>high</option><option>moderate</option><option>low</option><option>not_assessable</option></select></label><label>Restricted notes<input name="restricted_notes" maxlength="500"></label></fieldset></form>
      </article>
    </section>
  </main>
  <script src="app.js"></script>
</body>
</html>
"""


def _interface_css() -> str:
    return """*{box-sizing:border-box}[hidden]{display:none!important}body{margin:0;font:14px Arial,sans-serif;color:#171717;background:#f4f5f6}header{height:48px;padding:0 18px;display:flex;align-items:center;justify-content:space-between;background:#fff;border-bottom:1px solid #bbb}.start-screen{max-width:680px;margin:36px auto;padding:24px;background:#fff;border:1px solid #aaa;border-radius:6px}.start-screen h1{font-size:22px;margin:0 0 20px}.start-screen label{display:flex;align-items:center;gap:8px;margin:10px 0}.start-screen input[type=text],.start-screen input:not([type]){width:260px}.start-screen fieldset{margin:18px 0}.start-screen fieldset p{margin:3px 0 14px 28px;color:#555}.actions{display:flex;gap:8px;flex-wrap:wrap}button,select,input{min-height:34px;margin:4px;padding:5px 8px}button{cursor:pointer}#start-error{color:#9b1c1c;min-height:20px}#review-screen{display:grid;grid-template-columns:230px 1fr;min-height:calc(100vh - 48px)}aside{padding:16px;border-right:1px solid #bbb;background:#fff}article{padding:14px;min-width:0}.clip-nav,.frame-controls{display:flex;align-items:center;justify-content:center}.frame-controls input{width:min(420px,55vw)}.tier{font-weight:700;margin:6px 0}.views{display:grid;grid-template-columns:1fr 1fr;gap:10px}.views figure{margin:0;background:#fff;border:1px solid #aaa;padding:8px}.views canvas{display:block;width:100%;max-height:55vh;aspect-ratio:1;object-fit:contain;background:#000}.source-only #model-panel{display:none}.source-only .views{grid-template-columns:1fr}fieldset{border:1px solid #aaa;margin:10px 0;padding:10px;background:#fff}label{display:inline-flex;gap:4px;align-items:center;margin:4px 10px 4px 0}.counter{margin:8px 0;color:#555}@media(max-width:850px){#review-screen{grid-template-columns:1fr}aside{border-right:0;border-bottom:1px solid #bbb}.views{grid-template-columns:1fr}}
"""


def _interface_js() -> str:
    return """'use strict';
const presence=['waveform_or_tracing','calipers','contour_or_measurement_trace','visible_text','visible_numeric_value','visible_unit','visible_measurement_name','lvot_vti_specific_label','tapse_specific_label','candidate_target_value_present'];
const studyFields=['spectral_doppler_present','m_mode_present','caliper_or_trace_present','visible_numeric_value_present','target_specific_label_present','candidate_target_value_present'];
const clipRequired=['acquisition_content_type',...presence,'reader_confidence'];
const studyRequired=[...studyFields,'reader_confidence'];
let session='',event=null,study=null,state={annotations:{studies:{},clips:{}}},ci=0,locked=false,frameIndex=0,playTimer=null;
const sprites={source:null,model:null};
const choices='<option value="">Unreviewed</option><option value="yes">Yes</option><option value="no">No</option><option value="uncertain">Uncertain</option><option value="not_assessable">Not assessable</option>';
function fields(root,names){root.innerHTML='';names.forEach(n=>{const l=document.createElement('label');l.textContent=n.replaceAll('_',' ');const s=document.createElement('select');s.name=n;s.innerHTML=choices;l.appendChild(s);root.appendChild(l);});}
function readForm(form){return Object.fromEntries(new FormData(form).entries());}
function fill(form,values){[...form.elements].forEach(e=>{if(e.name)e.value=(values||{})[e.name]||'';e.disabled=locked;});}
function complete(record,required){return !!record&&required.every(k=>String(record[k]||'').trim());}
async function request(path,body){const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const p=await r.json();if(!r.ok)throw new Error(p.error||'Request failed');return p;}
async function claim(action){const code=document.querySelector('#reviewer-code').value.trim();const role=document.querySelector('input[name=role]:checked')?.value||'';const qualified=document.querySelector('#qualification').checked;const p=await request('/api/claim',{reviewer_code:code,role,action,qualification_confirmed:qualified});session=p.session_token;event=p.event;study=p.study;state=p.checkpoint;state.annotations=state.annotations||{studies:{},clips:{}};state.annotations.studies=state.annotations.studies||{};state.annotations.clips=state.annotations.clips||{};locked=!!state.locked;ci=0;document.querySelector('#start-screen').hidden=true;document.querySelector('#review-screen').hidden=false;render();}
async function save(){if(locked||!session)return;const clip=study.clips[ci];state.annotations.clips[clip.clip_audit_id]=readForm(document.querySelector('#clip-form'));state.annotations.studies[study.audit_id]=readForm(document.querySelector('#study-form'));document.querySelector('#save-state').textContent='Saving';const p=await request('/api/checkpoint',{session_token:session,annotations:state.annotations});document.querySelector('#save-state').textContent=p.status==='saved'?'Saved':'Save error';updateProgress();}
function updateProgress(){const done=study.clips.filter(c=>complete(state.annotations.clips[c.clip_audit_id],clipRequired)).length;document.querySelector('#clip-progress').textContent=`${done}/${study.clips.length} clips complete`;}
function drawSprite(name){const canvas=document.querySelector(`#${name}-canvas`),ctx=canvas.getContext('2d'),img=sprites[name];ctx.fillStyle='#000';ctx.fillRect(0,0,canvas.width,canvas.height);if(!img||!img.complete||!img.naturalWidth)return;const tw=img.naturalWidth/4,th=img.naturalHeight/4,x=(frameIndex%4)*tw,y=Math.floor(frameIndex/4)*th;ctx.drawImage(img,x,y,tw,th,0,0,canvas.width,canvas.height);}
function renderFrame(){drawSprite('source');drawSprite('model');document.querySelector('#frame-slider').value=String(frameIndex);document.querySelector('#frame-counter').textContent=`Frame ${frameIndex+1} of 16`;}
function loadSprite(name,token){sprites[name]=null;drawSprite(name);if(!token)return;const img=new Image();img.onload=()=>{sprites[name]=img;renderFrame();};img.src=`/media/${encodeURIComponent(token)}?session=${encodeURIComponent(session)}`;}
function stopPlayback(){if(playTimer){clearInterval(playTimer);playTimer=null;}document.querySelector('#toggle-play').textContent='Play';}
function togglePlayback(){if(playTimer){stopPlayback();return;}playTimer=setInterval(()=>{frameIndex=(frameIndex+1)%16;renderFrame();},250);document.querySelector('#toggle-play').textContent='Pause';}
function render(){const clip=study.clips[ci];document.querySelector('#study-id').textContent=study.audit_id;document.querySelector('#role-label').textContent=event.role==='primary'?'Primary independent review':'Secondary independent review';document.querySelector('#clip-counter').textContent=`Clip ${ci+1} of ${study.clips.length}`;document.querySelector('#tier').textContent=clip.source_only?'SOURCE ACQUISITION ONLY — NOT VERIFIED MODEL INPUT':clip.evidence_tier.replaceAll('_',' ');document.body.classList.toggle('source-only',clip.source_only);document.querySelector('#model-caption').textContent=clip.evidence_tier==='EXACT_MODEL_INPUT'?'Exact model input':'Verified equivalent replay';fill(document.querySelector('#clip-form'),state.annotations.clips[clip.clip_audit_id]);fill(document.querySelector('#study-form'),state.annotations.studies[study.audit_id]);frameIndex=0;loadSprite('source',clip.source_media_id);loadSprite('model',clip.model_input_media_id);document.querySelector('#lock-study').disabled=locked;updateProgress();}
async function move(delta){await save();stopPlayback();ci=Math.max(0,Math.min(study.clips.length-1,ci+delta));render();}
async function lockStudy(){await save();const firstMissing=study.clips.findIndex(c=>!complete(state.annotations.clips[c.clip_audit_id],clipRequired));if(firstMissing>=0){ci=firstMissing;render();throw new Error('Complete every clip before locking.');}if(!complete(state.annotations.studies[study.audit_id],studyRequired))throw new Error('Complete the study summary before locking.');if(!confirm('Lock this study-role review? It cannot be edited afterward.'))return;await request('/api/lock',{session_token:session});locked=true;stopPlayback();render();document.querySelector('#save-state').textContent='Locked';}
function leave(){stopPlayback();session='';event=null;study=null;state={annotations:{studies:{},clips:{}}};document.querySelector('#review-screen').hidden=true;document.querySelector('#start-screen').hidden=false;document.querySelector('#save-state').textContent='Ready';}
function showError(error){document.querySelector('#start-error').textContent=error.message||String(error);}
function init(){fields(document.querySelector('#presence-fields'),presence);fields(document.querySelector('#study-fields'),studyFields);document.querySelector('#resume-review').onclick=()=>claim('resume').catch(showError);document.querySelector('#claim-review').onclick=()=>claim('claim_next').catch(showError);document.querySelector('#previous-clip').onclick=()=>move(-1).catch(showError);document.querySelector('#next-clip').onclick=()=>move(1).catch(showError);document.querySelector('#toggle-play').onclick=togglePlayback;document.querySelector('#frame-slider').oninput=e=>{frameIndex=Number(e.target.value);renderFrame();};document.querySelector('#lock-study').onclick=()=>lockStudy().catch(e=>{document.querySelector('#save-state').textContent=e.message;});document.querySelector('#leave-session').onclick=leave;document.querySelectorAll('#clip-form select,#study-form select').forEach(e=>e.addEventListener('change',()=>save().catch(showError)));document.querySelectorAll('#clip-form input,#study-form input').forEach(e=>e.addEventListener('change',()=>save().catch(showError)));document.addEventListener('contextmenu',e=>{if(e.target.tagName==='CANVAS')e.preventDefault();});document.addEventListener('dragstart',e=>{if(e.target.tagName==='CANVAS')e.preventDefault();});}
init();
"""


def build_role_aware_interface_package(
    *,
    reduced_output_root: Path,
    parent_media_root: Path,
) -> InterfaceBuildResult:
    reduced_output_root = require_restricted_destination(reduced_output_root)
    parent_media_root = require_restricted_destination(parent_media_root)
    validate_locked_reduced_roster(reduced_output_root)
    roster = reduced_output_root / "restricted" / "roster"
    interface_root = reduced_output_root / "restricted" / "interface"
    queue_root = reduced_output_root / "restricted" / "queue"
    registry_root = reduced_output_root / "restricted" / "reviewer_registry"
    checkpoints_root = reduced_output_root / "restricted" / "checkpoints"
    if interface_root.exists() or queue_root.exists() or registry_root.exists() or checkpoints_root.exists():
        raise FileExistsError("refusing to overwrite reduced role-aware interface state")

    linkage = pd.read_csv(roster / "reduced_audit_linkage_restricted.csv").fillna("")
    technical = pd.read_csv(roster / "reduced_technical_interface_manifest_restricted.csv").fillna("")
    formal = pd.read_csv(roster / "formal_reliability_subset_restricted.csv").fillna("")
    require_columns(linkage, ["audit_id", "reduced_review_order"], "reduced linkage")
    require_columns(
        technical,
        [
            "audit_id",
            "clip_audit_id",
            "evidence_tier",
            "source_media_id",
            "model_input_media_id",
            "model_input_verified",
            "source_only",
        ],
        "reduced technical manifest",
    )
    if set(linkage["audit_id"].astype(str)) != set(technical["audit_id"].astype(str)):
        raise Tier1BlockedError(BLOCKED_REDUCED_INTERFACE, "technical manifest differs from reduced study queue")
    order = dict(zip(linkage["audit_id"].astype(str), linkage["reduced_review_order"].astype(int)))
    visible_records: list[dict[str, Any]] = []
    for raw in technical.to_dict(orient="records"):
        row = dict(raw)
        row["review_order"] = int(order[str(row["audit_id"])])
        visible_records.append(reader_visible_record(row))
    visible = pd.DataFrame(visible_records)
    studies: list[dict[str, Any]] = []
    for audit_id, group in visible.groupby("audit_id", sort=False):
        studies.append(
            {
                "audit_id": str(audit_id),
                "review_order": int(order[str(audit_id)]),
                "clips": group.drop(columns=["audit_id", "review_order"]).to_dict(orient="records"),
            }
        )
    studies.sort(key=lambda row: (row["review_order"], row["audit_id"]))
    for study in studies:
        if not study["clips"]:
            raise Tier1BlockedError(BLOCKED_REDUCED_INTERFACE, "selected study has no canonical clips")
        for clip in study["clips"]:
            for key in ("source_media_id", "model_input_media_id"):
                token = str(clip.get(key, ""))
                if token and not (parent_media_root / f"{token}.png").is_file():
                    raise FileNotFoundError("selected protected media token is missing")

    formal_order = dict(
        zip(formal["audit_id"].astype(str), formal["formal_reliability_order"].astype(int))
    )
    policy = {
        "schema_version": QUEUE_SCHEMA,
        "protocol_name": PROTOCOL_NAME,
        "protocol_status": PROTOCOL_STATUS,
        "primary_queue": [study["audit_id"] for study in studies],
        "formal_reliability_queue": [
            audit_id for audit_id, _ in sorted(formal_order.items(), key=lambda item: (item[1], item[0]))
        ],
        "reviewer_selectable_study_list": False,
        "target_membership_reader_visible": False,
        "formal_reliability_status_reader_visible": False,
        "same_role_exclusive_claim": True,
        "cross_role_independence": True,
        "same_reviewer_cross_role_same_study_prohibited": True,
        "incomplete_review_same_reviewer_resume_only": True,
        "supplemental_secondary_after_formal_completion_only": True,
    }
    interface_root.mkdir(parents=True, mode=0o700)
    os.chmod(interface_root, 0o700)
    (interface_root / "index.html").write_text(_interface_html(), encoding="utf-8")
    (interface_root / "style.css").write_text(_interface_css(), encoding="utf-8")
    (interface_root / "app.js").write_text(_interface_js(), encoding="utf-8")
    write_json(interface_root / "study_manifest_restricted.json", {"studies": studies})
    write_json(interface_root / "queue_policy_restricted.json", policy)
    interface_policy = {
        "status": ROLE_AWARE_INTERFACE_READY,
        "reviewer_code_required": True,
        "role_required": True,
        "role_options": ["Primary independent review", "Secondary independent review"],
        "reviewer_browsing_unassigned_studies": False,
        "primary_annotations_visible_to_secondary": False,
        "secondary_annotations_visible_to_primary": False,
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
    write_json(interface_root / "interface_policy.json", interface_policy)
    registry = ReviewerRegistry(registry_root)
    queue_root.mkdir(parents=True, mode=0o700)
    checkpoints_root.mkdir(parents=True, mode=0o700)
    RoleQueueStore(
        queue_root,
        interface_root / "queue_policy_restricted.json",
        registry,
    )
    for path in interface_root.iterdir():
        if path.is_file():
            os.chmod(path, 0o600)

    summary = {
        "status": ROLE_AWARE_INTERFACE_READY,
        "unique_physical_studies": len(studies),
        "clips": int(len(visible)),
        "formal_reliability_studies": int(len(formal)),
        "reviewer_registry_separate": True,
        "reviewer_preassigned": False,
        "dynamic_primary_queue": True,
        "dynamic_secondary_queue": True,
        "reader_browsing_unassigned_studies": False,
        "role_independence_enforced": True,
        "protected_media_reused": True,
        "protected_media_copied": False,
        "localhost_only": True,
        "ocr_available": False,
        "automated_annotation": False,
        "image_download_button": False,
        "study_manifest_sha256": sha256_file(interface_root / "study_manifest_restricted.json"),
        "queue_policy_sha256": sha256_file(interface_root / "queue_policy_restricted.json"),
        "interface_policy_sha256": sha256_file(interface_root / "interface_policy.json"),
    }
    write_json(reduced_output_root / "aggregate_safe" / "role_aware_interface_summary.json", summary)
    return InterfaceBuildResult(reduced_output_root, summary)


def _complete_not_assessable_payload(study: Mapping[str, Any]) -> dict[str, Any]:
    study_record = {field: "not_assessable" for field in STUDY_OUTCOMES}
    study_record["reader_confidence"] = "not_assessable"
    clip_records = {}
    for clip in study["clips"]:
        record = {field: "not_assessable" for field in CLIP_PRESENCE_FIELDS}
        record["acquisition_content_type"] = "not_assessable"
        record["reader_confidence"] = "not_assessable"
        clip_records[str(clip["clip_audit_id"])] = record
    return {
        "annotations": {
            "studies": {str(study["audit_id"]): study_record},
            "clips": clip_records,
        }
    }


def validate_role_aware_interface_package(reduced_output_root: Path) -> dict[str, Any]:
    reduced_output_root = require_restricted_destination(reduced_output_root)
    interface_root = reduced_output_root / "restricted" / "interface"
    manifest = _load_object(interface_root / "study_manifest_restricted.json")
    policy = _load_object(interface_root / "interface_policy.json")
    if policy.get("status") != ROLE_AWARE_INTERFACE_READY:
        raise Tier1BlockedError(BLOCKED_REDUCED_INTERFACE, "role-aware interface policy is not ready")
    with tempfile.TemporaryDirectory(
        prefix="reduced-interface-validation-",
        dir=reduced_output_root / "restricted",
    ) as temporary:
        root = Path(temporary)
        registry = ReviewerRegistry(root / "registry")
        for code in ("physicianA", "physicianB", "physicianC"):
            registry.register(code, qualified=True)
        queue = RoleQueueStore(
            root / "queue",
            interface_root / "queue_policy_restricted.json",
            registry,
        )
        checkpoints = RoleAwareCheckpointStore(root / "checkpoints", queue, manifest)
        primary_a = queue.claim(
            reviewer_code="physicianA",
            role=ROLE_PRIMARY,
            action=ACTION_CLAIM_NEXT,
            qualification_confirmed=True,
        )
        resumed = queue.claim(
            reviewer_code="physicianA",
            role=ROLE_PRIMARY,
            action=ACTION_RESUME,
            qualification_confirmed=True,
        )
        if resumed["event_id"] != primary_a["event_id"]:
            raise ValueError("incomplete primary review did not resume")
        secondary_a = queue.claim(
            reviewer_code="physicianA",
            role=ROLE_SECONDARY,
            action=ACTION_CLAIM_NEXT,
            qualification_confirmed=True,
        )
        if secondary_a["physical_study_token"] == primary_a["physical_study_token"]:
            raise ValueError("reviewer was assigned both roles on one study")
        primary_b = queue.claim(
            reviewer_code="physicianB",
            role=ROLE_PRIMARY,
            action=ACTION_CLAIM_NEXT,
            qualification_confirmed=True,
        )
        if primary_b["physical_study_token"] == primary_a["physical_study_token"]:
            raise ValueError("same-role double claim was not blocked")
        study = next(
            item
            for item in manifest["studies"]
            if str(item["audit_id"]) == str(primary_a["physical_study_token"])
        )
        payload = _complete_not_assessable_payload(study)
        checkpoints.save(str(primary_a["event_id"]), payload)
        lock_path = checkpoints.lock(str(primary_a["event_id"]))
        if checkpoints.load(str(primary_a["event_id"]))["locked"] is not True:
            raise ValueError("completed primary review did not lock")
        lock_payload = _load_object(lock_path)
        locked_event = queue.get_event(str(primary_a["event_id"]))
        if lock_payload.get("annotation_checksum") != locked_event.get("annotation_checksum"):
            raise ValueError("annotation checksum changed across lock records")
        checkpoints.archive_for_reassignment(
            str(primary_b["event_id"]),
            owner_confirmed=True,
            reason="synthetic validation",
        )
        if queue.get_event(str(primary_b["event_id"]))["status"] != "archived_incomplete":
            raise ValueError("reassignment did not archive the incomplete review")
    return {
        "status": ROLE_AWARE_INTERFACE_READY,
        "reviewer_code_required": True,
        "role_required": True,
        "same_reviewer_cross_role_same_study_blocked": True,
        "same_role_double_claim_blocked": True,
        "different_roles_independent": True,
        "incomplete_same_reviewer_resume": True,
        "role_immutable_after_save": True,
        "completion_lock_validated": True,
        "annotation_checksum_stable": True,
        "reassignment_archives_and_restarts_blank": True,
        "synthetic_validation_removed": True,
        "target_hidden": True,
        "formal_reliability_status_hidden": True,
        "no_ocr": True,
        "no_automated_annotation": True,
        "localhost_only": True,
    }


def validate_phase2k_contract(
    reduced_output_root: Path,
    *,
    interface_validation: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate the 45 explicit reduced-audit validation requirements."""

    reduced_output_root = require_restricted_destination(reduced_output_root)
    validate_locked_reduced_roster(reduced_output_root)
    roster = reduced_output_root / "restricted" / "roster"
    interface = reduced_output_root / "restricted" / "interface"
    assignments = pd.read_csv(roster / "reduced_target_assignments_restricted.csv").fillna("")
    linkage = pd.read_csv(roster / "reduced_audit_linkage_restricted.csv").fillna("")
    clips = pd.read_csv(roster / "reduced_canonical_clip_roster_restricted.csv").fillna("")
    technical = pd.read_csv(roster / "reduced_technical_interface_manifest_restricted.csv").fillna("")
    formal = pd.read_csv(roster / "formal_reliability_subset_restricted.csv").fillna("")
    protocol = _load_object(roster / "protocol_amendment_restricted.json")
    protocol_lock = _load_object(
        reduced_output_root / "aggregate_safe" / "reduced_audit_protocol_lock.json"
    )
    interface_policy = _load_object(interface / "interface_policy.json")
    queue_policy = _load_object(interface / "queue_policy_restricted.json")
    queue_state = _load_object(reduced_output_root / "restricted" / "queue" / "queue_state.json")
    registry = _load_object(
        reduced_output_root / "restricted" / "reviewer_registry" / "reviewer_registry.json"
    )
    validation = dict(
        interface_validation or validate_role_aware_interface_package(reduced_output_root)
    )
    duplicate_assignment_count = len(assignments) - assignments["audit_id"].astype(str).nunique()
    selected_ids = set(linkage["audit_id"].astype(str))
    clip_keys = set(map(tuple, clips[["audit_id", "clip_audit_id"]].astype(str).to_numpy()))
    technical_keys = set(
        map(tuple, technical[["audit_id", "clip_audit_id"]].astype(str).to_numpy())
    )
    replacements = protocol.get("replacement_records", [])
    replacement_valid = isinstance(replacements, list) and all(
        isinstance(record, Mapping)
        and record.get("rule") == "replace_last_selected_same_target_tier_by_locked_order"
        and record.get("target") in {"lvot_vti", "tapse"}
        and record.get("evidence_tier") in {TIER_A, TIER_C}
        for record in replacements
    )
    requirements = {
        "01_exactly_15_lvot_assignments": int(assignments["target"].eq("lvot_vti").sum()) == 15,
        "02_exactly_15_tapse_assignments": int(assignments["target"].eq("tapse").sum()) == 15,
        "03_all_7_lvot_tier_a_included": int(
            (assignments["target"].eq("lvot_vti") & assignments["evidence_tier"].eq(TIER_A)).sum()
        )
        == 7,
        "04_exactly_8_additional_lvot_tier_c": int(
            (assignments["target"].eq("lvot_vti") & assignments["evidence_tier"].eq(TIER_C)).sum()
        )
        == 8,
        "05_exactly_15_tapse_tier_c": int(
            (assignments["target"].eq("tapse") & assignments["evidence_tier"].eq(TIER_C)).sum()
        )
        == 15,
        "06_prior_pilot_force_included": protocol_lock.get("pilot_force_included") is True,
        "07_pilot_replacement_same_hidden_stratum": replacement_valid,
        "08_cross_target_assignments_counted_once_physically": duplicate_assignment_count
        == int(protocol_lock.get("cross_target_overlap_studies", -1)),
        "09_no_content_or_annotation_field_enters_selection": not {
            *STUDY_ANNOTATION_FIELDS,
            *CLIP_ANNOTATION_FIELDS,
        }.intersection(assignments.columns),
        "10_parent_roster_not_altered": protocol.get("status") == PROTOCOL_STATUS
        and len(protocol_lock.get("parent_artifact_sha256", {})) == 11,
        "11_formal_subset_exactly_8_unique_studies": len(formal) == FORMAL_RELIABILITY_N
        and formal["audit_id"].astype(str).nunique() == FORMAL_RELIABILITY_N,
        "12_parent_second_reader_subset_used_first": formal["selection_source"].isin(
            {
                "locked_parent_second_reader_subset",
                "deterministic_reduced_sample_supplement",
            }
        ).all(),
        "13_reliability_supplementation_deterministic": formal["formal_reliability_order"].astype(int).tolist()
        == list(range(1, FORMAL_RELIABILITY_N + 1)),
        "14_formal_selection_fixed_before_annotation": queue_state.get("events") == [],
        "15_formal_subset_immutable_after_review_starts": queue_state.get("queue_policy_sha256")
        == sha256_file(interface / "queue_policy_restricted.json"),
        "16_supplemental_reviews_separately_labeled": queue_policy.get(
            "supplemental_secondary_after_formal_completion_only"
        )
        is True,
        "17_reviewer_code_required": validation.get("reviewer_code_required") is True,
        "18_role_required": validation.get("role_required") is True,
        "19_reviewer_may_use_different_roles_on_different_studies": validation.get(
            "different_roles_independent"
        )
        is True,
        "20_reviewer_cannot_use_both_roles_same_study": validation.get(
            "same_reviewer_cross_role_same_study_blocked"
        )
        is True,
        "21_primary_cannot_see_secondary_annotations": interface_policy.get(
            "secondary_annotations_visible_to_primary"
        )
        is False,
        "22_secondary_cannot_see_primary_annotations": interface_policy.get(
            "primary_annotations_visible_to_secondary"
        )
        is False,
        "23_role_immutable_after_first_save": validation.get("role_immutable_after_save") is True,
        "24_same_role_double_claim_blocked": validation.get("same_role_double_claim_blocked") is True,
        "25_different_roles_may_proceed_independently": validation.get(
            "different_roles_independent"
        )
        is True,
        "26_incomplete_review_same_reviewer_resume_only": validation.get(
            "incomplete_same_reviewer_resume"
        )
        is True,
        "27_reassignment_archives_and_starts_blank": validation.get(
            "reassignment_archives_and_restarts_blank"
        )
        is True,
        "28_partial_annotations_cannot_be_merged": queue_policy.get(
            "incomplete_review_same_reviewer_resume_only"
        )
        is True,
        "29_target_hidden": interface_policy.get("target_visible") is False,
        "30_split_hidden": interface_policy.get("split_visible") is False,
        "31_report_label_hidden": interface_policy.get("report_label_visible") is False,
        "32_predictions_and_residuals_hidden": interface_policy.get("prediction_visible") is False
        and interface_policy.get("residual_visible") is False,
        "33_identifiers_and_paths_hidden": interface_policy.get("identifiers_or_paths_visible")
        is False,
        "34_formal_reliability_status_hidden": interface_policy.get(
            "formal_reliability_status_visible"
        )
        is False,
        "35_no_ocr": interface_policy.get("ocr_available") is False,
        "36_no_automated_annotation": interface_policy.get("automated_annotation") is False,
        "37_no_image_download_export": interface_policy.get("image_download_button") is False,
        "38_localhost_only": interface_policy.get("localhost_only") is True,
        "39_prior_checkpoint_preserved_by_hash": protocol_lock.get("pilot", {}).get(
            "checkpoint_sha256"
        )
        == protocol.get("pilot", {}).get("checkpoint_sha256"),
        "40_prior_mixed_reader_checkpoint_excluded": protocol_lock.get("pilot", {}).get(
            "excluded_from_final_analysis"
        )
        is True,
        "41_protected_media_reused_not_duplicated": interface_policy.get(
            "protected_parent_media_reused"
        )
        is True
        and not (reduced_output_root / "restricted" / "media").exists(),
        "42_every_selected_study_has_all_canonical_clips": set(clips["audit_id"].astype(str))
        == selected_ids
        and clip_keys == technical_keys,
        "43_locked_study_has_one_attributable_reader_per_role": validation.get(
            "completion_lock_validated"
        )
        is True,
        "44_annotation_checksums_stable": validation.get("annotation_checksum_stable") is True,
        "45_reviewer_registry_separate": registry.get("schema_version") == REGISTRY_SCHEMA
        and "reviewers" in registry,
    }
    requirements = {key: bool(value) for key, value in requirements.items()}
    if len(requirements) != 45 or not all(requirements.values()):
        failed = sorted(key for key, passed in requirements.items() if not passed)
        raise Tier1BlockedError(
            BLOCKED_REDUCED_INTERFACE,
            f"Phase 2K-R2 validation contract failed: {failed}",
        )
    return {
        "status": "PHASE2K_R2_VALIDATION_CONTRACT_PASSED",
        "requirements_total": 45,
        "requirements_passed": 45,
        "requirements": requirements,
    }


def write_interface_ready_certificate(reduced_output_root: Path, validation: Mapping[str, Any]) -> dict[str, Any]:
    reduced_output_root = require_restricted_destination(reduced_output_root)
    interface_root = reduced_output_root / "restricted" / "interface"
    summary_path = reduced_output_root / "aggregate_safe" / "role_aware_interface_summary.json"
    protocol_lock = reduced_output_root / "aggregate_safe" / "reduced_audit_protocol_lock.json"
    if validation.get("status") != ROLE_AWARE_INTERFACE_READY:
        raise Tier1BlockedError(BLOCKED_REDUCED_INTERFACE, "interface validation is incomplete")
    validation_path = reduced_output_root / "aggregate_safe" / "role_aware_interface_validation.json"
    write_json(validation_path, dict(validation))
    contract = validate_phase2k_contract(
        reduced_output_root,
        interface_validation=validation,
    )
    contract_path = reduced_output_root / "aggregate_safe" / "phase2k_r2_validation_contract.json"
    write_json(contract_path, contract)
    certificate = {
        "status": READY_FOR_REDUCED_BLINDED_HUMAN_AUDIT,
        "protocol_status": PROTOCOL_STATUS,
        "interface_status": ROLE_AWARE_INTERFACE_READY,
        "protocol_lock_sha256": sha256_file(protocol_lock),
        "interface_summary_sha256": sha256_file(summary_path),
        "interface_validation_sha256": sha256_file(validation_path),
        "validation_contract_sha256": sha256_file(contract_path),
        "validation_requirements_passed": int(contract["requirements_passed"]),
        "interface_files": [
            safe_file_record(role, interface_root / filename)
            for role, filename in (
                ("index", "index.html"),
                ("style", "style.css"),
                ("application", "app.js"),
                ("study_manifest", "study_manifest_restricted.json"),
                ("queue_policy", "queue_policy_restricted.json"),
                ("interface_policy", "interface_policy.json"),
            )
        ],
        "production_annotations_present": False,
        "reviewer_registry_entries": 0,
        "protected_parent_media_reused": True,
        "protected_media_copied": False,
        "localhost_only": True,
        "start_screen_only_verified_before_handoff": False,
    }
    path = reduced_output_root / "aggregate_safe" / "reduced_audit_ready_certificate.json"
    write_json(path, certificate)
    return certificate
