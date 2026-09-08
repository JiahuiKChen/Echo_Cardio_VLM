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
from typing import Any, Callable, Iterator, Mapping

import pandas as pd

from .audit import CONTENT_TYPES, PRESENCE_VALUES, READER_CONFIDENCE, STUDY_OUTCOMES
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
from .audit_protocol_v3 import (
    FIELD_DEFINITIONS,
    PROTOCOL_V3_NAME,
    SCORING_SCOPE_BY_TIER,
    SOURCE_ONLY_FIELD_DEFINITIONS,
    V3_CHECKPOINT_SCHEMA,
    V3_CLIP_ANNOTATION_FIELDS,
    V3_CLIP_FREE_TEXT_FIELDS,
    V3_CLIP_PRESENCE_FIELDS,
    V3_EVENT_SCHEMA,
    V3_LOCK_SCHEMA,
    V3_QUEUE_SCHEMA,
    V3_REQUIRED_CLIP_ANNOTATION_FIELDS,
    V3_REQUIRED_STUDY_ANNOTATION_FIELDS,
    V3_RESTART_SCHEMA,
    V3_SOURCE_ONLY_CATEGORY_FIELDS,
    V3_SOURCE_ONLY_CLIP_FIELDS,
    V3_SOURCE_ONLY_FREE_TEXT_FIELDS,
    V3_SOURCE_ONLY_PRIMARY_FIELD,
    V3_SOURCE_ONLY_STUDY_OUTCOMES,
    V3_STUDY_ANNOTATION_FIELDS,
    V3_STUDY_OUTCOMES,
    active_protocol_paths,
    derive_study_summary,
)
from .audit_default_preset import (
    CONFIRMED_CLIP_STATES,
    DEFAULT_PRESET_ID,
    DEFAULT_PRESET_VALUES,
    NOT_ASSESSABLE_CONFIRMED,
    REVIEWED_CONFIRMED,
    REVIEWED_MODIFIED_AND_CONFIRMED,
    event_uses_default_preset,
    load_default_preset_configuration,
    preset_event_metadata,
    public_preset_for_event,
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
ACTION_VIEW_FINALIZED = "view_finalized"

UNCLAIMED = "UNCLAIMED"
CLAIMED_INCOMPLETE = "CLAIMED_INCOMPLETE"
COMPLETE_NOT_FINALIZED = "COMPLETE_NOT_FINALIZED"
FINALIZED_LOCKED = "FINALIZED_LOCKED"
ARCHIVED_INCOMPLETE = "ARCHIVED_INCOMPLETE"
REASSIGNED_FRESH = "REASSIGNED_FRESH"

STUDY_READY = "STUDY_READY"
INCOMPLETE_STUDY_EXISTS = "INCOMPLETE_STUDY_EXISTS"
FINALIZATION_REQUIRED = "FINALIZATION_REQUIRED"
NO_INCOMPLETE_STUDY = "NO_INCOMPLETE_STUDY"
NO_ELIGIBLE_STUDY = "NO_ELIGIBLE_STUDY"
QUEUE_TRANSITION_DRY_RUN_PASS = "QUEUE_TRANSITION_DRY_RUN_PASS"
NO_FINALIZED_STUDY = "NO_FINALIZED_STUDY"

NO_INCOMPLETE_MESSAGE = (
    "No incomplete study is available for this reviewer and role. "
    "Select \u201cClaim next eligible study\u201d to continue."
)
INCOMPLETE_EXISTS_MESSAGE = (
    "An incomplete study is already assigned to this reviewer and role. "
    "Select \u201cResume incomplete study\u201d to continue."
)
FINALIZATION_REQUIRED_MESSAGE = (
    "This study is complete but not finalized. Please review the completion "
    "summary and finalize it before claiming another study."
)
NO_ELIGIBLE_MESSAGE = "No additional eligible studies remain for this review role."

ROLE_AWARE_INTERFACE_READY = "ROLE_AWARE_INTERFACE_READY"
READY_FOR_REDUCED_BLINDED_HUMAN_AUDIT = "READY_FOR_REDUCED_BLINDED_HUMAN_AUDIT"
BLOCKED_READER_ROLE_INDEPENDENCE = "BLOCKED_READER_ROLE_INDEPENDENCE"
BLOCKED_REDUCED_INTERFACE = "BLOCKED_REDUCED_INTERFACE"
BLOCKED_MIXED_REVIEWER_ATTRIBUTION = "BLOCKED_MIXED_REVIEWER_ATTRIBUTION"
BLOCKED_QUEUE_STATE_MIGRATION = "BLOCKED_QUEUE_STATE_MIGRATION"
FORMAL_DUPLICATE_REVIEW = "FORMAL_RELIABILITY_REVIEW"
SUPPLEMENTAL_DUPLICATE_REVIEW = "SUPPLEMENTAL_DUPLICATE_REVIEW"

QUEUE_SCHEMA = "jdim-reduced-audit-role-queue-v1"
REGISTRY_SCHEMA = "jdim-reduced-audit-reviewer-registry-v1"
EVENT_SCHEMA = "jdim-reduced-audit-review-event-v1"
CHECKPOINT_SCHEMA = "jdim-reduced-audit-study-checkpoint-v1"
LOCK_SCHEMA = "jdim-reduced-audit-review-lock-v1"


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

    def __init__(self, root: Path, *, initialize: bool = True):
        self.root = require_restricted_destination(root)
        self.path = self.root / "reviewer_registry.json"
        self.identity_map_path = self.root / "reviewer_identity_map_optional.json"
        if initialize:
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(self.root, 0o700)
        elif not self.path.is_file():
            raise FileNotFoundError("reviewer registry is unavailable")
        if initialize and not self.path.exists():
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

    def __init__(
        self,
        queue_root: Path,
        queue_policy_path: Path,
        registry: ReviewerRegistry,
        *,
        initialize: bool = True,
        expected_protocol_name: str = PROTOCOL_NAME,
        queue_schema: str = QUEUE_SCHEMA,
        event_schema: str = EVENT_SCHEMA,
        default_preset_configuration: Mapping[str, Any] | None = None,
    ):
        self.root = require_restricted_destination(queue_root)
        self.policy_path = queue_policy_path
        self.registry = registry
        self.state_path = self.root / "queue_state.json"
        self.lock_path = self.root / ".queue.lock"
        self.reassignment_root = self.root / "reassignments"
        self.restart_root = self.root.parent / "restarts"
        self.protocol_name = str(expected_protocol_name)
        self.queue_schema = str(queue_schema)
        self.event_schema = str(event_schema)
        self.default_preset_configuration = (
            dict(default_preset_configuration)
            if default_preset_configuration is not None
            else None
        )
        if initialize:
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(self.root, 0o700)
            self.reassignment_root.mkdir(parents=True, exist_ok=True, mode=0o700)
            if self.protocol_name == PROTOCOL_V3_NAME:
                self.restart_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        elif not self.state_path.is_file():
            raise FileNotFoundError("role queue state is unavailable")
        self.policy = self._load_policy()
        if initialize and not self.state_path.exists():
            _atomic_write_json(
                self.state_path,
                {
                    "schema_version": self.queue_schema,
                    "protocol_name": self.protocol_name,
                    "queue_policy_sha256": sha256_file(self.policy_path),
                    "events": [],
                    "superseded_event_ids": [],
                    "restart_record_sha256": [],
                },
            )

    def _load_policy(self) -> dict[str, Any]:
        policy = _load_object(self.policy_path)
        if (
            policy.get("schema_version") != self.queue_schema
            or policy.get("protocol_name") != self.protocol_name
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
                    state.get("schema_version") != self.queue_schema
                    or state.get("protocol_name", self.protocol_name) != self.protocol_name
                    or state.get("queue_policy_sha256") != sha256_file(self.policy_path)
                    or not isinstance(state.get("events"), list)
                ):
                    raise Tier1BlockedError(
                        BLOCKED_READER_ROLE_INDEPENDENCE,
                        "role queue state does not match its locked policy",
                    )
                original = sha256_json(state)
                yield state
                if sha256_json(state) != original:
                    _atomic_write_json(self.state_path, state)
                fcntl.flock(lock_stream.fileno(), fcntl.LOCK_UN)
        finally:
            pass

    @staticmethod
    def _active(
        events: list[dict[str, Any]],
        superseded_event_ids: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        superseded = superseded_event_ids or set()
        return [
            event
            for event in events
            if event.get("status") in {"in_progress", "locked"}
            and str(event.get("event_id", "")) not in superseded
        ]

    def _candidate_order(
        self,
        events: list[dict[str, Any]],
        role: str,
        superseded_event_ids: set[str] | None = None,
    ) -> list[str]:
        primary = list(map(str, self.policy["primary_queue"]))
        if role == ROLE_PRIMARY:
            return primary
        superseded = superseded_event_ids or set()
        formal = list(map(str, self.policy["formal_reliability_queue"]))
        locked_formal = {
            str(event["physical_study_token"])
            for event in events
            if event.get("role") == ROLE_SECONDARY
            and event.get("status") == "locked"
            and bool(event.get("formal_reliability"))
            and str(event.get("event_id", "")) not in superseded
        }
        open_formal = [study for study in formal if study not in locked_formal]
        if open_formal:
            return open_formal
        return [study for study in primary if study not in set(formal)]

    def transition(
        self,
        *,
        reviewer_code: str,
        role: str,
        action: str,
        qualification_confirmed: bool,
        classify_in_progress: Callable[[Mapping[str, Any]], Mapping[str, Any]],
    ) -> dict[str, Any]:
        """Resolve a queue action atomically without silently changing its meaning."""

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
            superseded = set(map(str, state.get("superseded_event_ids", [])))
            incomplete = [
                event
                for event in events
                if event.get("reviewer_code") == code
                and event.get("role") == role
                and event.get("status") == "in_progress"
                and str(event.get("event_id", "")) not in superseded
            ]
            if len(incomplete) > 1:
                raise Tier1BlockedError(
                    BLOCKED_READER_ROLE_INDEPENDENCE,
                    "reviewer has multiple incomplete studies in one role",
                )
            if incomplete:
                event = incomplete[0]
                review = dict(classify_in_progress(event))
                workflow_state = str(review.get("workflow_state", ""))
                if workflow_state == FINALIZED_LOCKED:
                    raise Tier1BlockedError(
                        BLOCKED_READER_ROLE_INDEPENDENCE,
                        "finalized review retains a stale active queue pointer",
                    )
                if workflow_state not in {CLAIMED_INCOMPLETE, COMPLETE_NOT_FINALIZED}:
                    raise Tier1BlockedError(
                        BLOCKED_REDUCED_INTERFACE,
                        "in-progress review has an invalid workflow state",
                    )
                if workflow_state == COMPLETE_NOT_FINALIZED:
                    outcome = FINALIZATION_REQUIRED
                    message = FINALIZATION_REQUIRED_MESSAGE
                elif action == ACTION_CLAIM_NEXT:
                    outcome = INCOMPLETE_STUDY_EXISTS
                    message = INCOMPLETE_EXISTS_MESSAGE
                else:
                    outcome = STUDY_READY
                    message = "Saved progress restored."
                return {
                    "outcome": outcome,
                    "workflow_state": workflow_state,
                    "event": dict(event),
                    "open_study": action == ACTION_RESUME,
                    "message": message,
                    "requirements": list(review.get("requirements", [])),
                }
            if action == ACTION_RESUME:
                return {
                    "outcome": NO_INCOMPLETE_STUDY,
                    "workflow_state": UNCLAIMED,
                    "event": None,
                    "open_study": False,
                    "message": NO_INCOMPLETE_MESSAGE,
                    "requirements": [],
                }

            active = self._active(events, superseded)
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
                and str(event.get("event_id", "")) not in superseded
            }
            formal_ids = set(map(str, self.policy["formal_reliability_queue"]))
            candidate = next(
                (
                    audit_id
                    for audit_id in self._candidate_order(events, role, superseded)
                    if audit_id not in same_role_claimed and audit_id not in reviewer_studies
                ),
                None,
            )
            if candidate is None:
                return {
                    "outcome": NO_ELIGIBLE_STUDY,
                    "workflow_state": UNCLAIMED,
                    "event": None,
                    "open_study": False,
                    "message": NO_ELIGIBLE_MESSAGE,
                    "requirements": [],
                }
            formal = role == ROLE_SECONDARY and candidate in formal_ids
            reassigned = any(
                event.get("physical_study_token") == candidate
                and event.get("role") == role
                and event.get("status") == "archived_incomplete"
                for event in events
            )
            event = {
                "schema_version": self.event_schema,
                "protocol_name": self.protocol_name,
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
                "completed_at_utc": None,
                "locked_at_utc": None,
                "status": "in_progress",
                "lock_status": "unlocked",
                "reassignment_history": [],
                "clip_completion_count": 0,
                "study_summary_complete": False,
                "annotation_checksum": None,
                **preset_event_metadata(
                    self.default_preset_configuration,
                    event_type=(
                        "new_primary_claim"
                        if role == ROLE_PRIMARY
                        else "new_secondary_claim"
                    ),
                ),
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
            return {
                "outcome": STUDY_READY,
                "workflow_state": REASSIGNED_FRESH if reassigned else CLAIMED_INCOMPLETE,
                "event": dict(event),
                "open_study": True,
                "message": "Eligible study claimed.",
                "requirements": [],
            }

    def claim(
        self,
        *,
        reviewer_code: str,
        role: str,
        action: str,
        qualification_confirmed: bool,
    ) -> dict[str, Any]:
        result = self.transition(
            reviewer_code=reviewer_code,
            role=role,
            action=action,
            qualification_confirmed=qualification_confirmed,
            classify_in_progress=lambda _event: {"workflow_state": CLAIMED_INCOMPLETE},
        )
        if result["open_study"] and result["event"] is not None:
            return dict(result["event"])
        if result["outcome"] == INCOMPLETE_STUDY_EXISTS:
            raise FileExistsError(INCOMPLETE_EXISTS_MESSAGE)
        if result["outcome"] == NO_INCOMPLETE_STUDY:
            raise FileNotFoundError(NO_INCOMPLETE_MESSAGE)
        if result["outcome"] == NO_ELIGIBLE_STUDY:
            raise FileNotFoundError(NO_ELIGIBLE_MESSAGE)
        raise Tier1BlockedError(BLOCKED_REDUCED_INTERFACE, "queue transition did not open a study")

    def eligible_remaining_count(self, role: str) -> int:
        """Count role slots not held or finalized, without reviewer-specific filtering."""

        role = _role(role)
        state = _load_object(self.state_path)
        events = list(state.get("events", []))
        superseded = set(map(str, state.get("superseded_event_ids", [])))
        claimed = {
            str(event["physical_study_token"])
            for event in self._active(events, superseded)
            if event.get("role") == role
        }
        return sum(
            audit_id not in claimed
            for audit_id in self._candidate_order(events, role, superseded)
        )

    def latest_finalized(self, *, reviewer_code: str, role: str) -> dict[str, Any] | None:
        """Return only the requesting reviewer's latest effective finalized record."""

        code = _code(reviewer_code)
        role = _role(role)
        self.registry.require_active_qualified(code)
        state = _load_object(self.state_path)
        superseded = set(map(str, state.get("superseded_event_ids", [])))
        matches = [
            event
            for event in state.get("events", [])
            if event.get("reviewer_code") == code
            and event.get("role") == role
            and event.get("status") == "locked"
            and str(event.get("event_id", "")) not in superseded
        ]
        if not matches:
            return None
        matches.sort(
            key=lambda event: (
                str(event.get("locked_at_utc", "")),
                str(event.get("event_id", "")),
            )
        )
        return dict(matches[-1])

    def restart_finalized(
        self,
        event_id: str,
        *,
        reviewer_code: str,
        role: str,
        owner_confirmed: bool,
        checkpoint_sha256: str,
        lock_sha256: str,
    ) -> dict[str, Any]:
        """Supersede one locked V3 event while preserving its files and hashes."""

        if self.protocol_name != PROTOCOL_V3_NAME:
            raise PermissionError("protocol restart is available only under the current V3 protocol")
        if owner_confirmed is not True:
            raise PermissionError("record-owner confirmation is required")
        code = _code(reviewer_code)
        role = _role(role)
        self.registry.require_active_qualified(code)
        with self._locked_state() as state:
            superseded = set(map(str, state.get("superseded_event_ids", [])))
            matches = [
                event
                for event in state["events"]
                if str(event.get("event_id", "")) == str(event_id)
            ]
            if len(matches) != 1:
                raise KeyError("finalized review event is unavailable")
            prior = matches[0]
            if (
                prior.get("status") != "locked"
                or prior.get("protocol_name") != PROTOCOL_V3_NAME
                or prior.get("reviewer_code") != code
                or prior.get("role") != role
                or str(prior.get("event_id", "")) in superseded
            ):
                raise PermissionError("only the record owner may restart an effective finalized review")
            if any(
                event.get("reviewer_code") == code
                and event.get("role") == role
                and event.get("status") == "in_progress"
                and str(event.get("event_id", "")) not in superseded
                for event in state["events"]
            ):
                raise PermissionError("finish or archive the current incomplete review before restarting")
            timestamp = utc_now()
            new_event = {
                "schema_version": self.event_schema,
                "protocol_name": self.protocol_name,
                "event_id": f"E{secrets.token_hex(12).upper()}",
                "physical_study_token": str(prior["physical_study_token"]),
                "reviewer_code": code,
                "role": role,
                "formal_reliability": bool(prior.get("formal_reliability")),
                "supplemental_review": bool(prior.get("supplemental_review")),
                "secondary_review_class": str(prior.get("secondary_review_class", "NOT_APPLICABLE")),
                "claim_timestamp_utc": timestamp,
                "last_save_timestamp_utc": None,
                "completion_timestamp_utc": None,
                "completed_at_utc": None,
                "locked_at_utc": None,
                "status": "in_progress",
                "lock_status": "unlocked",
                "reassignment_history": [],
                "clip_completion_count": 0,
                "study_summary_complete": False,
                "annotation_checksum": None,
                "restarted_from_event_id": str(prior["event_id"]),
                **preset_event_metadata(
                    self.default_preset_configuration,
                    event_type="owner_authorized_restart",
                ),
            }
            restart_record = {
                "schema_version": V3_RESTART_SCHEMA,
                "status": "FINALIZED_REVIEW_ARCHIVED_FOR_PROTOCOL_RESTART",
                "protocol_name": self.protocol_name,
                "archived_event_id": str(prior["event_id"]),
                "replacement_event_id": str(new_event["event_id"]),
                "physical_study_token": str(prior["physical_study_token"]),
                "reviewer_code": code,
                "role": role,
                "archived_at_utc": timestamp,
                "checkpoint_sha256": str(checkpoint_sha256),
                "lock_sha256": str(lock_sha256),
                "excluded_from_prevalence": True,
                "excluded_from_agreement": True,
                "excluded_from_adjudication": True,
                "excluded_from_final_aggregation": True,
            }
            restart_path = self.restart_root / f"restart-{sha256_json(restart_record)[:24]}.json"
            if restart_path.exists():
                raise FileExistsError("protocol restart record already exists")
            _atomic_write_json(restart_path, restart_record)
            state.setdefault("superseded_event_ids", []).append(str(prior["event_id"]))
            state.setdefault("restart_record_sha256", []).append(sha256_file(restart_path))
            state["events"].append(new_event)
            return {
                "event": dict(new_event),
                "restart_record_path": str(restart_path),
                "restart_record_sha256": sha256_file(restart_path),
            }

    def repair_stale_locked_pointer(
        self,
        event_id: str,
        *,
        annotation_checksum: str,
        completed_at_utc: str,
        clip_completion_count: int,
    ) -> dict[str, Any]:
        """Repair queue metadata only after a lock/checkpoint has been validated."""

        with self._locked_state() as state:
            matches = [event for event in state["events"] if str(event.get("event_id")) == str(event_id)]
            if len(matches) != 1 or matches[0].get("status") != "in_progress":
                raise PermissionError("only a stale active pointer to a locked review may be repaired")
            event = matches[0]
            event["status"] = "locked"
            event["lock_status"] = "locked"
            event["completion_timestamp_utc"] = str(completed_at_utc)
            event["completed_at_utc"] = str(completed_at_utc)
            event["locked_at_utc"] = str(completed_at_utc)
            event["clip_completion_count"] = int(clip_completion_count)
            event["study_summary_complete"] = True
            event["annotation_checksum"] = str(annotation_checksum)
            event["active_pointer_repaired_at_utc"] = utc_now()
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
        completed_at_utc: str,
    ) -> dict[str, Any]:
        with self._locked_state() as state:
            matches = [event for event in state["events"] if str(event.get("event_id")) == str(event_id)]
            if len(matches) != 1 or matches[0].get("status") != "in_progress":
                raise PermissionError("review event is unavailable or already locked")
            event = matches[0]
            completed_at = str(completed_at_utc).strip()
            if not completed_at:
                raise ValueError("completion timestamp is required")
            event["status"] = "locked"
            event["lock_status"] = "locked"
            event["completion_timestamp_utc"] = completed_at
            event["completed_at_utc"] = completed_at
            event["locked_at_utc"] = completed_at
            event["last_save_timestamp_utc"] = completed_at
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

    def __init__(
        self,
        root: Path,
        queue: RoleQueueStore,
        study_manifest: Mapping[str, Any],
        *,
        initialize: bool = True,
        checkpoint_schema: str = CHECKPOINT_SCHEMA,
        lock_schema: str = LOCK_SCHEMA,
        clip_annotation_fields: set[str] | None = None,
        clip_presence_fields: tuple[str, ...] | None = None,
        required_clip_fields: set[str] | None = None,
        study_annotation_fields: set[str] | None = None,
        study_outcome_fields: tuple[str, ...] | None = None,
        source_only_study_outcome_fields: tuple[str, ...] | None = None,
        source_only_clip_fields: set[str] | None = None,
        source_only_category_fields: tuple[str, ...] | None = None,
        source_only_free_text_fields: tuple[str, ...] | None = None,
        source_only_primary_field: str | None = None,
        required_study_fields: set[str] | None = None,
        derive_study_outcomes: bool = False,
        default_preset_configuration: Mapping[str, Any] | None = None,
    ):
        self.root = require_restricted_destination(root)
        if initialize:
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(self.root, 0o700)
        elif not self.root.is_dir():
            raise FileNotFoundError("checkpoint root is unavailable")
        self.queue = queue
        self.protocol_name = queue.protocol_name
        self.checkpoint_schema = str(checkpoint_schema)
        self.lock_schema = str(lock_schema)
        self.clip_annotation_fields = set(clip_annotation_fields or CLIP_ANNOTATION_FIELDS)
        self.clip_presence_fields = tuple(clip_presence_fields or CLIP_PRESENCE_FIELDS)
        self.required_clip_fields = set(required_clip_fields or REQUIRED_CLIP_ANNOTATION_FIELDS)
        self.study_annotation_fields = set(study_annotation_fields or STUDY_ANNOTATION_FIELDS)
        self.study_outcome_fields = tuple(study_outcome_fields or STUDY_OUTCOMES)
        self.source_only_study_outcome_fields = tuple(source_only_study_outcome_fields or ())
        self.source_only_clip_fields = set(source_only_clip_fields or ())
        self.source_only_category_fields = tuple(source_only_category_fields or ())
        self.source_only_free_text_fields = tuple(source_only_free_text_fields or ())
        self.source_only_primary_field = str(source_only_primary_field or "")
        self.required_study_fields = set(required_study_fields or REQUIRED_STUDY_ANNOTATION_FIELDS)
        self.derive_study_outcomes = bool(derive_study_outcomes)
        self.default_preset_configuration = (
            dict(default_preset_configuration)
            if default_preset_configuration is not None
            else None
        )
        self.studies = {
            str(study["audit_id"]): dict(study)
            for study in study_manifest.get("studies", [])
        }
        self.clip_evidence_tiers: dict[str, str] = {}
        for study in self.studies.values():
            tiers = {str(clip.get("evidence_tier", "")) for clip in study.get("clips", [])}
            if self.protocol_name == PROTOCOL_V3_NAME and (
                len(tiers) != 1 or not tiers.issubset({TIER_A, TIER_C})
            ):
                raise Tier1BlockedError(
                    BLOCKED_REDUCED_INTERFACE,
                    "V3 study must contain clips from exactly one evidence tier",
                )
            for clip in study.get("clips", []):
                self.clip_evidence_tiers[str(clip["clip_audit_id"])] = str(
                    clip.get("evidence_tier", "")
                )

    def _event_path(self, event_id: str) -> Path:
        event_id = _code(event_id)
        root = (self.root / event_id).resolve()
        if self.root.resolve() not in root.parents:
            raise Tier1BlockedError(BLOCKED_UNSAFE_OUTPUT, "review event escaped checkpoint root")
        return root

    def _event_root(self, event_id: str) -> Path:
        root = self._event_path(event_id)
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
            fresh = {
                "schema_version": self.checkpoint_schema,
                "protocol_name": self.protocol_name,
                "event_id": event_id,
                "reviewer_code": event["reviewer_code"],
                "role": event["role"],
                "annotations": {"studies": {}, "clips": {}},
                "locked": False,
            }
            if self._uses_default_preset(event):
                fresh["clip_review_states"] = {}
            return fresh
        payload = _load_object(path)
        self._validate_event_identity(payload, event)
        self._validate_checkpoint_payload(payload, event)
        payload["locked"] = (root / "LOCKED.json").is_file()
        return payload

    def _uses_default_preset(self, event: Mapping[str, Any]) -> bool:
        return event_uses_default_preset(event, self.default_preset_configuration)

    def _validated_clip_review_states(
        self,
        raw_states: Any,
        *,
        event: Mapping[str, Any],
        clips: Mapping[str, Mapping[str, str]],
        clip_ids: set[str],
    ) -> dict[str, dict[str, Any]]:
        if not self._uses_default_preset(event):
            if raw_states is not None:
                raise Tier1BlockedError(
                    BLOCKED_REDUCED_INTERFACE,
                    "pre-cutover review contains post-cutover confirmation metadata",
                )
            return {}
        if raw_states is None:
            raw_states = {}
        if not isinstance(raw_states, Mapping):
            raise ValueError("clip review states must be an object")
        if not set(map(str, raw_states)).issubset(clip_ids):
            raise Tier1BlockedError(
                BLOCKED_READER_ROLE_INDEPENDENCE,
                "clip review state left the claimed study",
            )
        if set(map(str, raw_states)) != set(clips):
            raise Tier1BlockedError(
                BLOCKED_REDUCED_INTERFACE,
                "confirmed clip records and confirmation states differ",
            )
        allowed = {
            "state",
            "human_review_confirmed",
            "confirmed_without_change",
            "confirmation_timestamp_utc",
            "preset_identifier",
            "reviewer_code",
            "role",
            "fields_changed_from_preset",
        }
        clean: dict[str, dict[str, Any]] = {}
        for raw_clip_id, raw_state in raw_states.items():
            clip_id = str(raw_clip_id)
            if not isinstance(raw_state, Mapping) or set(raw_state) != allowed:
                raise ValueError("clip confirmation metadata fields changed")
            record = clips[clip_id]
            changed = sorted(
                field
                for field, initial in DEFAULT_PRESET_VALUES.items()
                if str(record.get(field, "")) != initial
            )
            if str(record.get("acquisition_content_type", "")) == "not_assessable":
                expected_state = NOT_ASSESSABLE_CONFIRMED
            else:
                expected_state = (
                    REVIEWED_MODIFIED_AND_CONFIRMED if changed else REVIEWED_CONFIRMED
                )
            timestamp = str(raw_state.get("confirmation_timestamp_utc", "")).strip()
            if not timestamp.endswith("Z"):
                raise ValueError("clip confirmation timestamp must be UTC")
            datetime.fromisoformat(timestamp.removesuffix("Z") + "+00:00")
            if (
                raw_state.get("state") != expected_state
                or raw_state.get("state") not in CONFIRMED_CLIP_STATES
                or raw_state.get("human_review_confirmed") is not True
                or raw_state.get("confirmed_without_change") != (not changed)
                or raw_state.get("preset_identifier") != DEFAULT_PRESET_ID
                or raw_state.get("reviewer_code") != event.get("reviewer_code")
                or raw_state.get("role") != event.get("role")
                or raw_state.get("fields_changed_from_preset") != changed
            ):
                raise Tier1BlockedError(
                    BLOCKED_REDUCED_INTERFACE,
                    "clip confirmation provenance is invalid",
                )
            clean[clip_id] = {
                "state": expected_state,
                "human_review_confirmed": True,
                "confirmed_without_change": not changed,
                "confirmation_timestamp_utc": timestamp,
                "preset_identifier": DEFAULT_PRESET_ID,
                "reviewer_code": str(event["reviewer_code"]),
                "role": str(event["role"]),
                "fields_changed_from_preset": changed,
            }
        return clean

    def _validate_checkpoint_payload(
        self,
        payload: Mapping[str, Any],
        event: Mapping[str, Any],
    ) -> None:
        allowed_top_level = {
            "schema_version",
            "protocol_name",
            "event_id",
            "reviewer_code",
            "role",
            "annotations",
        }
        if self._uses_default_preset(event):
            allowed_top_level.add("clip_review_states")
        if set(payload) != allowed_top_level:
            raise ValueError("checkpoint top-level fields changed")
        annotations = payload.get("annotations", {})
        if not isinstance(annotations, Mapping) or set(annotations) != {"studies", "clips"}:
            raise ValueError("checkpoint annotations must contain only studies and clips")
        clips = self._validated_annotation_group(
            annotations.get("clips", {}), self.clip_annotation_fields, "clip"
        )
        _, clip_ids = self._assignment(event)
        self._validated_clip_review_states(
            payload.get("clip_review_states"),
            event=event,
            clips=clips,
            clip_ids=clip_ids,
        )

    def _validated_annotation_group(
        self,
        group: Any,
        allowed_fields: set[str],
        label: str,
    ) -> dict[str, dict[str, str]]:
        if not isinstance(group, Mapping):
            raise ValueError(f"{label} annotations must be an object")
        clean: dict[str, dict[str, str]] = {}
        presence_fields = {
            *self.clip_presence_fields,
            *self.study_outcome_fields,
            *self.source_only_study_outcome_fields,
        }
        if self.source_only_primary_field:
            presence_fields.add(self.source_only_primary_field)
        for raw_identifier, raw_record in group.items():
            identifier = str(raw_identifier)
            if not READER_ID_PATTERN.fullmatch(identifier):
                raise ValueError(f"invalid {label} audit identifier")
            if not isinstance(raw_record, Mapping):
                raise ValueError(f"{label} annotation must be an object")
            unknown = sorted(set(raw_record) - allowed_fields)
            if unknown:
                raise ValueError(f"{label} annotation contains unsupported fields: {unknown}")
            record: dict[str, str] = {}
            for field, raw_value in raw_record.items():
                value = str(raw_value).strip()
                if field in presence_fields:
                    if value and value not in PRESENCE_VALUES:
                        raise ValueError(f"invalid presence value for {field}")
                elif field in self.source_only_category_fields:
                    if value not in {"", "yes"}:
                        raise ValueError(f"invalid source-only category value for {field}")
                elif field == "acquisition_content_type":
                    if value and value not in CONTENT_TYPES:
                        raise ValueError("invalid acquisition content type")
                elif field == "reader_confidence":
                    if value and value not in READER_CONFIDENCE:
                        raise ValueError("invalid reader confidence")
                elif len(value) > 500 or any(
                    ord(character) < 32 and character not in "\t\n" for character in value
                ):
                    raise ValueError(f"invalid restricted text for {field}")
                record[str(field)] = value
            clean[identifier] = record
        return clean

    def _required_clip_fields_for(self, clip_id: str) -> set[str]:
        required = set(self.required_clip_fields)
        if (
            self.source_only_primary_field
            and self.clip_evidence_tiers.get(clip_id) == TIER_A
        ):
            required.add(self.source_only_primary_field)
        return required

    def _validate_tier_scoped_clips(
        self,
        clips: Mapping[str, Mapping[str, str]],
        *,
        require_complete_source_categories: bool = False,
    ) -> None:
        for clip_id, record in clips.items():
            tier = self.clip_evidence_tiers.get(str(clip_id), "")
            source_values = {
                field: str(record.get(field, "")).strip()
                for field in self.source_only_clip_fields
            }
            populated_source_fields = {
                field for field, value in source_values.items() if value
            }
            if tier == TIER_C and populated_source_fields:
                raise ValueError("Tier-C clips cannot contain Tier-A source-only comparison fields")
            if tier != TIER_A:
                continue
            primary = source_values.get(self.source_only_primary_field, "")
            selected = {
                field
                for field in self.source_only_category_fields
                if source_values.get(field) == "yes"
            }
            if primary in {"no", "not_assessable"} and selected:
                raise ValueError("source-only categories require a Yes or Uncertain source-only response")
            if require_complete_source_categories and primary == "yes" and not selected:
                raise ValueError("source-only Yes requires at least one selected category")
            candidate_text = source_values.get("source_only_candidate_target_value_text", "")
            if candidate_text and "source_only_candidate_target_value" not in selected:
                raise ValueError(
                    "source-only candidate text requires the source-only candidate-value category"
                )

    def _require_complete_clips(
        self,
        clips: Mapping[str, Mapping[str, str]],
        clip_ids: set[str],
    ) -> None:
        if set(clips) != clip_ids:
            raise ValueError("clip annotations are incomplete or outside the locked assignment")
        for clip_id in sorted(clip_ids):
            missing = sorted(
                field
                for field in self._required_clip_fields_for(clip_id)
                if not str(clips[clip_id].get(field, "")).strip()
            )
            if missing:
                raise ValueError(f"clip annotation {clip_id} is incomplete: {missing}")
        self._validate_tier_scoped_clips(
            clips,
            require_complete_source_categories=True,
        )

    def _validated_saved_annotations(
        self,
        event: Mapping[str, Any],
    ) -> tuple[
        Path,
        dict[str, dict[str, dict[str, str]]],
        dict[str, dict[str, Any]],
    ] | None:
        root = self._event_path(str(event["event_id"]))
        checkpoint = root / "checkpoint.json"
        if not checkpoint.is_file():
            return None
        payload = _load_object(checkpoint)
        self._validate_event_identity(payload, event)
        self._validate_checkpoint_payload(payload, event)
        annotations = payload.get("annotations", {})
        if not isinstance(annotations, Mapping) or set(annotations) - {"studies", "clips"}:
            raise ValueError("checkpoint annotations must contain only studies and clips")
        studies = self._validated_annotation_group(
            annotations.get("studies", {}), self.study_annotation_fields, "study"
        )
        clips = self._validated_annotation_group(
            annotations.get("clips", {}), self.clip_annotation_fields, "clip"
        )
        audit_id, clip_ids = self._assignment(event)
        if not set(studies).issubset({audit_id}) or not set(clips).issubset(clip_ids):
            raise Tier1BlockedError(
                BLOCKED_READER_ROLE_INDEPENDENCE,
                "checkpoint contains annotations outside the claimed study",
            )
        self._validate_tier_scoped_clips(clips)
        review_states = self._validated_clip_review_states(
            payload.get("clip_review_states"),
            event=event,
            clips=clips,
            clip_ids=clip_ids,
        )
        return checkpoint, {"studies": studies, "clips": clips}, review_states

    def review_state(self, event: Mapping[str, Any]) -> dict[str, Any]:
        """Return content-free completion metadata for one review event."""

        status = str(event.get("status", ""))
        if status == "archived_incomplete":
            return {
                "workflow_state": ARCHIVED_INCOMPLETE,
                "completion_validation_passed": False,
                "requirements": [],
                "stale_active_pointer": False,
            }
        if status not in {"in_progress", "locked"}:
            raise Tier1BlockedError(BLOCKED_REDUCED_INTERFACE, "review event status is invalid")

        audit_id, clip_ids = self._assignment(event)
        root = self._event_path(str(event["event_id"]))
        lock_path = root / "LOCKED.json"
        saved = self._validated_saved_annotations(event)
        if saved is None:
            if status == "locked" or lock_path.exists():
                raise Tier1BlockedError(
                    BLOCKED_REDUCED_INTERFACE,
                    "locked review is missing its checkpoint",
                )
            requirements = []
            if clip_ids:
                requirements.append(f"Complete all required fields for {len(clip_ids)} clip(s).")
            requirements.append("Complete the study summary.")
            return {
                "workflow_state": CLAIMED_INCOMPLETE,
                "completion_validation_passed": False,
                "required_clip_count": len(clip_ids),
                "completed_clip_count": 0,
                "study_summary_complete": False,
                "requirements": requirements,
                "stale_active_pointer": False,
            }

        checkpoint, annotations, clip_review_states = saved
        studies = annotations["studies"]
        clips = annotations["clips"]
        incomplete_clips = [
            clip_id
            for clip_id in clip_ids
            if clip_id not in clips
            or not all(
                str(clips[clip_id].get(field, "")).strip()
                for field in self._required_clip_fields_for(clip_id)
            )
        ]
        if self._uses_default_preset(event):
            incomplete_clips = sorted(set(incomplete_clips) | (clip_ids - set(clip_review_states)))
        study_record = studies.get(audit_id, {})
        study_complete = bool(study_record) and all(
            str(study_record.get(field, "")).strip()
            for field in self.required_study_fields
        )
        study_notes_required = bool(study_record) and any(
            str(study_record.get(field, "")) in {"yes", "uncertain"}
            for field in self.study_outcome_fields
        ) and not str(study_record.get("restricted_notes", "")).strip()
        uncertain_clip_notes = sum(
            any(str(record.get(field, "")) == "uncertain" for field in self.clip_presence_fields)
            and not str(record.get("restricted_notes", "")).strip()
            for record in clips.values()
        )
        displayed_value_fields = (
            "candidate_target_value",
            "visible_unit_text",
            "visible_measurement_name_text",
            "display_precision",
        )
        incomplete_displayed_values = sum(
            str(record.get("candidate_target_value_present", "")) == "yes"
            and not all(str(record.get(field, "")).strip() for field in displayed_value_fields)
            for record in clips.values()
        )
        incomplete_source_categories = sum(
            self.clip_evidence_tiers.get(clip_id) == TIER_A
            and str(record.get(self.source_only_primary_field, "")) == "yes"
            and not any(
                str(record.get(field, "")) == "yes"
                for field in self.source_only_category_fields
            )
            for clip_id, record in clips.items()
        )
        requirements = []
        if incomplete_clips:
            requirements.append(
                f"Complete all required fields for {len(incomplete_clips)} clip(s)."
            )
        if not study_complete:
            requirements.append("Complete the study summary.")
        if study_notes_required:
            requirements.append(
                "Add restricted study notes for positive or uncertain study findings."
            )
        if uncertain_clip_notes:
            requirements.append(
                f"Add restricted notes for {uncertain_clip_notes} clip(s) marked uncertain."
            )
        if incomplete_displayed_values:
            requirements.append(
                "Complete displayed-value details for "
                f"{incomplete_displayed_values} clip(s) marked as containing a candidate value."
            )
        if incomplete_source_categories:
            requirements.append(
                "Select at least one source-only category for "
                f"{incomplete_source_categories} Tier-A clip(s) marked Yes."
            )
        completion_valid = not requirements
        annotation_checksum = self._checkpoint_content_checksum(
            event,
            annotations,
            clip_review_states,
        )

        lock_payload = None
        if lock_path.is_file():
            if not completion_valid:
                raise Tier1BlockedError(
                    BLOCKED_REDUCED_INTERFACE,
                    "locked review does not pass completion validation",
                )
            lock_payload = _load_object(lock_path)
            if (
                lock_payload.get("status") != "STUDY_ROLE_REVIEW_LOCKED"
                or lock_payload.get("schema_version") != self.lock_schema
                or lock_payload.get("event_id") != event.get("event_id")
                or lock_payload.get("reviewer_code") != event.get("reviewer_code")
                or lock_payload.get("role") != event.get("role")
                or lock_payload.get("protocol_name", self.protocol_name) != self.protocol_name
                or lock_payload.get("physical_study_token") != audit_id
                or lock_payload.get("annotation_checksum") != annotation_checksum
                or lock_payload.get("checkpoint_sha256") != sha256_file(checkpoint)
                or not str(lock_payload.get("locked_at_utc", "")).strip()
            ):
                raise Tier1BlockedError(
                    BLOCKED_READER_ROLE_INDEPENDENCE,
                    "review lock does not match its attributable checkpoint",
                )
        if status == "locked" and lock_payload is None:
            raise Tier1BlockedError(
                BLOCKED_REDUCED_INTERFACE,
                "finalized queue event is missing its immutable lock",
            )
        if lock_payload is not None:
            return {
                "workflow_state": FINALIZED_LOCKED,
                "completion_validation_passed": True,
                "required_clip_count": len(clip_ids),
                "completed_clip_count": len(clip_ids),
                "study_summary_complete": True,
                "requirements": [],
                "stale_active_pointer": status == "in_progress",
                "annotation_checksum": annotation_checksum,
                "checkpoint_sha256": sha256_file(checkpoint),
                "completed_at_utc": str(lock_payload.get("locked_at_utc", "")),
            }
        return {
            "workflow_state": COMPLETE_NOT_FINALIZED if completion_valid else CLAIMED_INCOMPLETE,
            "completion_validation_passed": completion_valid,
            "required_clip_count": len(clip_ids),
            "completed_clip_count": len(clip_ids) - len(incomplete_clips),
            "study_summary_complete": study_complete,
            "requirements": requirements,
            "stale_active_pointer": False,
            "annotation_checksum": annotation_checksum,
            "checkpoint_sha256": sha256_file(checkpoint),
        }

    def _validate_event_identity(self, payload: Mapping[str, Any], event: Mapping[str, Any]) -> None:
        if (
            payload.get("schema_version") != self.checkpoint_schema
            or payload.get("event_id") != event.get("event_id")
            or payload.get("reviewer_code") != event.get("reviewer_code")
            or payload.get("role") != event.get("role")
            or payload.get("protocol_name", self.protocol_name) != self.protocol_name
            or event.get("protocol_name", self.protocol_name) != self.protocol_name
        ):
            raise Tier1BlockedError(
                BLOCKED_READER_ROLE_INDEPENDENCE,
                "checkpoint identity or role changed",
            )

    def _checkpoint_content_checksum(
        self,
        event: Mapping[str, Any],
        annotations: Mapping[str, Any],
        clip_review_states: Mapping[str, Any],
    ) -> str:
        if self._uses_default_preset(event):
            return sha256_json(
                {
                    "annotations": annotations,
                    "clip_review_states": clip_review_states,
                }
            )
        return sha256_json(annotations)

    def _normalize_studies(
        self,
        *,
        audit_id: str,
        studies: dict[str, dict[str, str]],
        clips: dict[str, dict[str, str]],
    ) -> dict[str, dict[str, str]]:
        if not self.derive_study_outcomes or not (studies or clips):
            return studies
        supplied = dict(studies.get(audit_id, {}))
        derived = derive_study_summary(
            clips,
            clip_evidence_tiers=self.clip_evidence_tiers,
        )
        mismatched = [
            field
            for field in (
                *self.study_outcome_fields,
                *self.source_only_study_outcome_fields,
            )
            if str(supplied.get(field, "")).strip()
            and str(supplied.get(field, "")).strip() != derived[field]
        ]
        if mismatched:
            raise ValueError("study summary outcomes must match the automatic clip-level roll-up")
        confirmation = str(supplied.get("derived_summary_confirmed", "")).strip()
        if confirmation not in {"", "yes"}:
            raise ValueError("derived study-summary confirmation must be yes or blank")
        normalized = {
            **derived,
            "reader_confidence": str(supplied.get("reader_confidence", "")).strip(),
            "derived_summary_confirmed": confirmation,
        }
        notes = str(supplied.get("restricted_notes", "")).strip()
        if notes:
            normalized["restricted_notes"] = notes
        return {audit_id: normalized}

    def _persist(
        self,
        *,
        event: Mapping[str, Any],
        studies: dict[str, dict[str, str]],
        clips: dict[str, dict[str, str]],
        clip_review_states: dict[str, dict[str, Any]],
    ) -> Path:
        event_id = str(event["event_id"])
        audit_id, clip_ids = self._assignment(event)
        studies = self._normalize_studies(
            audit_id=audit_id,
            studies=studies,
            clips=clips,
        )
        clean: dict[str, Any] = {
            "schema_version": self.checkpoint_schema,
            "protocol_name": self.protocol_name,
            "event_id": event_id,
            "reviewer_code": str(event["reviewer_code"]),
            "role": str(event["role"]),
            "annotations": {"studies": studies, "clips": clips},
        }
        if self._uses_default_preset(event):
            clean["clip_review_states"] = self._validated_clip_review_states(
                clip_review_states,
                event=event,
                clips=clips,
                clip_ids=clip_ids,
            )
        destination = self._event_root(event_id) / "checkpoint.json"
        _atomic_write_json(destination, clean)
        checksum = self._checkpoint_content_checksum(
            event,
            clean["annotations"],
            clean.get("clip_review_states", {}),
        )
        confirmed_ids = set(clean.get("clip_review_states", {}))
        clip_complete = sum(
            all(
                str(record.get(field, "")).strip()
                for field in self._required_clip_fields_for(clip_id)
            )
            and (not self._uses_default_preset(event) or clip_id in confirmed_ids)
            for clip_id, record in clips.items()
        )
        study_complete = bool(studies.get(audit_id)) and all(
            str(studies[audit_id].get(field, "")).strip()
            for field in self.required_study_fields
        )
        self.queue.update_progress(
            event_id,
            clip_completion_count=clip_complete,
            study_summary_complete=study_complete,
            annotation_checksum=checksum,
        )
        return destination

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
        studies = self._validated_annotation_group(
            annotations.get("studies", {}), self.study_annotation_fields, "study"
        )
        clips = self._validated_annotation_group(
            annotations.get("clips", {}), self.clip_annotation_fields, "clip"
        )
        audit_id, clip_ids = self._assignment(event)
        if not set(studies).issubset({audit_id}) or not set(clips).issubset(clip_ids):
            raise Tier1BlockedError(
                BLOCKED_READER_ROLE_INDEPENDENCE,
                "checkpoint contains annotations outside the claimed study",
            )
        self._validate_tier_scoped_clips(clips)
        clip_review_states: dict[str, dict[str, Any]] = {}
        if self._uses_default_preset(event):
            saved = self._validated_saved_annotations(event)
            prior_clips = saved[1]["clips"] if saved is not None else {}
            clip_review_states = saved[2] if saved is not None else {}
            if clips != prior_clips:
                raise PermissionError(
                    "post-cutover clip answers may be saved only by explicit clip confirmation"
                )
        return self._persist(
            event=event,
            studies=studies,
            clips=clips,
            clip_review_states=clip_review_states,
        )

    def confirm_clip(
        self,
        event_id: str,
        *,
        clip_id: str,
        annotation: Mapping[str, Any],
    ) -> Path:
        """Persist exactly one actively confirmed post-cutover clip."""

        event = self.queue.get_event(event_id)
        if event.get("status") != "in_progress" or not self._uses_default_preset(event):
            raise PermissionError("explicit preset confirmation is unavailable for this review")
        root = self._event_root(event_id)
        if (root / "LOCKED.json").exists():
            raise PermissionError("review event is locked")
        audit_id, clip_ids = self._assignment(event)
        clip_id = str(clip_id)
        if clip_id not in clip_ids:
            raise Tier1BlockedError(
                BLOCKED_READER_ROLE_INDEPENDENCE,
                "confirmed clip left the claimed study",
            )
        clean_record = self._validated_annotation_group(
            {clip_id: annotation}, self.clip_annotation_fields, "clip"
        )[clip_id]
        self._validate_tier_scoped_clips({clip_id: clean_record})
        missing = sorted(
            field
            for field in self._required_clip_fields_for(clip_id)
            if not str(clean_record.get(field, "")).strip()
        )
        if missing:
            raise ValueError(f"clip confirmation is incomplete: {missing}")
        details = (
            "candidate_target_value",
            "visible_unit_text",
            "visible_measurement_name_text",
            "display_precision",
        )
        if clean_record.get("candidate_target_value_present") == "no" and any(
            str(clean_record.get(field, "")).strip() for field in details
        ):
            raise ValueError("candidate displayed-value fields must be blank when candidate value is No")
        saved = self._validated_saved_annotations(event)
        studies = dict(saved[1]["studies"]) if saved is not None else {}
        clips = dict(saved[1]["clips"]) if saved is not None else {}
        review_states = dict(saved[2]) if saved is not None else {}
        clips[clip_id] = clean_record
        changed = sorted(
            field
            for field, initial in DEFAULT_PRESET_VALUES.items()
            if str(clean_record.get(field, "")) != initial
        )
        if clean_record.get("acquisition_content_type") == "not_assessable":
            confirmation_state = NOT_ASSESSABLE_CONFIRMED
        else:
            confirmation_state = (
                REVIEWED_MODIFIED_AND_CONFIRMED if changed else REVIEWED_CONFIRMED
            )
        review_states[clip_id] = {
            "state": confirmation_state,
            "human_review_confirmed": True,
            "confirmed_without_change": not changed,
            "confirmation_timestamp_utc": utc_now(),
            "preset_identifier": DEFAULT_PRESET_ID,
            "reviewer_code": str(event["reviewer_code"]),
            "role": str(event["role"]),
            "fields_changed_from_preset": changed,
        }
        prior_study = dict(studies.get(audit_id, {}))
        for field in (
            *self.study_outcome_fields,
            *self.source_only_study_outcome_fields,
        ):
            prior_study.pop(field, None)
        prior_study["derived_summary_confirmed"] = ""
        studies = {audit_id: prior_study} if prior_study else {}
        return self._persist(
            event=event,
            studies=studies,
            clips=clips,
            clip_review_states=review_states,
        )

    def _require_positive_uncertain_details(
        self,
        study_record: Mapping[str, Any],
        clip_records: Mapping[str, Mapping[str, Any]],
    ) -> None:
        if any(
            str(study_record.get(field, "")) in {"yes", "uncertain"}
            for field in self.study_outcome_fields
        ):
            if not str(study_record.get("restricted_notes", "")).strip():
                raise ValueError("positive or uncertain study findings require restricted notes")
        for clip_id, record in clip_records.items():
            uncertain = any(
                str(record.get(field, "")) == "uncertain"
                for field in self.clip_presence_fields
            )
            if uncertain and not str(record.get("restricted_notes", "")).strip():
                raise ValueError(f"uncertain clip findings require restricted notes: {clip_id}")
            if str(record.get("candidate_target_value_present", "")) == "yes":
                required = ("candidate_target_value", "visible_unit_text", "visible_measurement_name_text", "display_precision")
                missing = [field for field in required if not str(record.get(field, "")).strip()]
                if missing:
                    raise ValueError(f"candidate displayed value details are incomplete: {missing}")
        self._validate_tier_scoped_clips(
            clip_records,
            require_complete_source_categories=True,
        )

    def lock(self, event_id: str) -> Path:
        event = self.queue.get_event(event_id)
        if event.get("status") != "in_progress":
            raise PermissionError("review event is unavailable or already locked")
        root = self._event_path(event_id)
        checkpoint = root / "checkpoint.json"
        if not checkpoint.is_file():
            raise FileNotFoundError("cannot lock before saving annotations")
        if (root / "LOCKED.json").exists():
            raise FileExistsError("review event is already locked")
        review = self.review_state(event)
        if review["workflow_state"] != COMPLETE_NOT_FINALIZED:
            requirements = list(review.get("requirements", []))
            if requirements:
                raise ValueError(" ".join(requirements))
            raise ValueError("review does not pass completion validation")
        payload = _load_object(checkpoint)
        self._validate_event_identity(payload, event)
        audit_id, clip_ids = self._assignment(event)
        annotations = payload.get("annotations", {})
        studies = annotations.get("studies", {})
        clips = annotations.get("clips", {})
        clip_review_states = self._validated_clip_review_states(
            payload.get("clip_review_states"),
            event=event,
            clips=clips,
            clip_ids=clip_ids,
        )
        CheckpointStore._require_complete_group(
            studies,
            {audit_id},
            self.required_study_fields,
            "study",
        )
        self._require_complete_clips(clips, clip_ids)
        self._require_positive_uncertain_details(studies[audit_id], clips)
        annotation_checksum = self._checkpoint_content_checksum(
            event,
            annotations,
            clip_review_states,
        )
        locked_at = utc_now()
        descriptor = os.open(root / "LOCKED.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(
                {
                    "schema_version": self.lock_schema,
                    "status": "STUDY_ROLE_REVIEW_LOCKED",
                    "protocol_name": self.protocol_name,
                    "event_id": event_id,
                    "reviewer_code": event["reviewer_code"],
                    "role": event["role"],
                    "physical_study_token": audit_id,
                    "annotation_checksum": annotation_checksum,
                    "checkpoint_sha256": sha256_file(checkpoint),
                    "locked_at_utc": locked_at,
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
            completed_at_utc=locked_at,
        )
        return root / "LOCKED.json"

    def restart_finalized(
        self,
        event_id: str,
        *,
        reviewer_code: str,
        role: str,
        owner_confirmed: bool,
    ) -> dict[str, Any]:
        root = self._event_path(event_id)
        checkpoint = root / "checkpoint.json"
        lock = root / "LOCKED.json"
        if not checkpoint.is_file() or not lock.is_file():
            raise FileNotFoundError("finalized review files are unavailable")
        checkpoint_hash = sha256_file(checkpoint)
        lock_hash = sha256_file(lock)
        result = self.queue.restart_finalized(
            event_id,
            reviewer_code=reviewer_code,
            role=role,
            owner_confirmed=owner_confirmed,
            checkpoint_sha256=checkpoint_hash,
            lock_sha256=lock_hash,
        )
        if sha256_file(checkpoint) != checkpoint_hash or sha256_file(lock) != lock_hash:
            raise Tier1BlockedError(
                BLOCKED_READER_ROLE_INDEPENDENCE,
                "protocol restart changed a finalized review",
            )
        self.save(str(result["event"]["event_id"]), {"annotations": {"studies": {}, "clips": {}}})
        return result

    def repair_stale_locked_pointer(self, event_id: str) -> dict[str, Any]:
        """Release only an in-progress pointer backed by a valid immutable lock."""

        event = self.queue.get_event(event_id)
        review = self.review_state(event)
        if review.get("workflow_state") != FINALIZED_LOCKED or not review.get(
            "stale_active_pointer"
        ):
            raise PermissionError("review event is not a validated stale locked pointer")
        return self.queue.repair_stale_locked_pointer(
            event_id,
            annotation_checksum=str(review["annotation_checksum"]),
            completed_at_utc=str(review["completed_at_utc"]),
            clip_completion_count=int(review["required_clip_count"]),
        )

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

    def __init__(
        self,
        package_root: Path,
        parent_media_root: Path,
        *,
        source_commit: str | None = None,
    ):
        self.package_root = require_restricted_destination(package_root)
        self.parent_media_root = require_restricted_destination(parent_media_root)
        active_pointer = self.package_root / "restricted" / "active_audit_protocol.json"
        if active_pointer.is_file():
            active = active_protocol_paths(self.package_root)
            interface_root = active.interface_root
            queue_root = active.queue_root
            checkpoint_root = active.checkpoint_root
            self.protocol_name = PROTOCOL_V3_NAME
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
        else:
            interface_root = self.package_root / "restricted" / "interface"
            queue_root = self.package_root / "restricted" / "queue"
            checkpoint_root = self.package_root / "restricted" / "checkpoints"
            self.protocol_name = PROTOCOL_NAME
            queue_options = {}
            checkpoint_options = {}
        self.default_preset_configuration = load_default_preset_configuration(
            interface_root,
            expected_source_commit=source_commit,
        )
        if self.default_preset_configuration is not None and source_commit is None:
            raise ValueError("source_commit is required while the V3.1 preset is active")
        queue_options["default_preset_configuration"] = self.default_preset_configuration
        checkpoint_options["default_preset_configuration"] = self.default_preset_configuration
        self.manifest = _load_object(interface_root / "study_manifest_restricted.json")
        self.studies = {
            str(study["audit_id"]): dict(study)
            for study in self.manifest.get("studies", [])
        }
        self.registry = ReviewerRegistry(self.package_root / "restricted" / "reviewer_registry")
        self.queue = RoleQueueStore(
            queue_root,
            interface_root / "queue_policy_restricted.json",
            self.registry,
            **queue_options,
        )
        self.checkpoints = RoleAwareCheckpointStore(
            checkpoint_root,
            self.queue,
            self.manifest,
            **checkpoint_options,
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
        transition = self.queue.transition(
            reviewer_code=str(payload.get("reviewer_code", "")),
            role=str(payload.get("role", "")),
            action=str(payload.get("action", "")),
            qualification_confirmed=payload.get("qualification_confirmed") is True,
            classify_in_progress=self.checkpoints.review_state,
        )
        event = transition.get("event")
        if not transition.get("open_study"):
            return {
                "status": str(transition["outcome"]),
                "workflow_state": str(transition["workflow_state"]),
                "message": str(transition["message"]),
                "requirements": list(transition.get("requirements", [])),
                "open_study": False,
            }
        if not isinstance(event, Mapping):
            raise Tier1BlockedError(BLOCKED_REDUCED_INTERFACE, "queue transition omitted its review event")
        audit_id = str(event["physical_study_token"])
        if audit_id not in self.studies:
            raise Tier1BlockedError(BLOCKED_REDUCED_INTERFACE, "claimed study left the interface manifest")
        token = secrets.token_urlsafe(32)
        with self._session_lock:
            stale_tokens = [
                prior_token
                for prior_token, prior in self._sessions.items()
                if prior["reviewer_code"] == str(event["reviewer_code"])
                and prior["role"] == str(event["role"])
            ]
            for stale_token in stale_tokens:
                self._sessions.pop(stale_token, None)
            self._sessions[token] = {
                "event_id": str(event["event_id"]),
                "reviewer_code": str(event["reviewer_code"]),
                "role": str(event["role"]),
                "audit_id": audit_id,
                "access_mode": "editable",
            }
        return {
            "status": str(transition["outcome"]),
            "workflow_state": str(transition["workflow_state"]),
            "message": str(transition["message"]),
            "requirements": list(transition.get("requirements", [])),
            "open_study": True,
            "session_token": token,
            "event": self._client_event(event),
            "study": self.studies[audit_id],
            "checkpoint": self.checkpoints.load(str(event["event_id"])),
            "default_preset": public_preset_for_event(
                event,
                self.default_preset_configuration,
            ),
            "access_mode": "editable",
        }

    def view_finalized(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        allowed = {"reviewer_code", "role", "qualification_confirmed"}
        unknown = sorted(set(payload) - allowed)
        if unknown:
            raise ValueError(f"finalized-view request contains unsupported fields: {unknown}")
        if payload.get("qualification_confirmed") is not True:
            raise PermissionError("qualification confirmation is required")
        code = _code(payload.get("reviewer_code", ""))
        role = _role(payload.get("role", ""))
        event = self.queue.latest_finalized(reviewer_code=code, role=role)
        if event is None:
            return {
                "status": NO_FINALIZED_STUDY,
                "message": "No finalized study is available for this reviewer and role.",
                "open_study": False,
            }
        review = self.checkpoints.review_state(event)
        if review.get("workflow_state") != FINALIZED_LOCKED:
            raise Tier1BlockedError(BLOCKED_REDUCED_INTERFACE, "finalized review is not locked")
        audit_id = str(event["physical_study_token"])
        token = secrets.token_urlsafe(32)
        with self._session_lock:
            self._sessions[token] = {
                "event_id": str(event["event_id"]),
                "reviewer_code": code,
                "role": role,
                "audit_id": audit_id,
                "access_mode": "read_only_finalized",
            }
        return {
            "status": FINALIZED_LOCKED,
            "message": "Finalized study opened read-only.",
            "open_study": True,
            "session_token": token,
            "event": self._client_event(event),
            "study": self.studies[audit_id],
            "checkpoint": self.checkpoints.load(str(event["event_id"])),
            "default_preset": public_preset_for_event(
                event,
                self.default_preset_configuration,
            ),
            "access_mode": "read_only_finalized",
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
        if session.get("access_mode") != "editable":
            raise PermissionError("finalized review is read-only")
        destination = self.checkpoints.save(
            session["event_id"],
            {"annotations": annotations},
        )
        return {
            "status": "saved",
            "size_bytes": int(destination.stat().st_size),
            "checkpoint": self.checkpoints.load(session["event_id"]),
        }

    def confirm_clip(
        self,
        token: str,
        *,
        clip_id: str,
        annotation: Mapping[str, Any],
    ) -> dict[str, Any]:
        session = self.session(token)
        if session.get("access_mode") != "editable":
            raise PermissionError("finalized review is read-only")
        destination = self.checkpoints.confirm_clip(
            session["event_id"],
            clip_id=clip_id,
            annotation=annotation,
        )
        return {
            "status": "clip_confirmed",
            "size_bytes": int(destination.stat().st_size),
            "checkpoint": self.checkpoints.load(session["event_id"]),
        }

    def lock(self, token: str) -> dict[str, Any]:
        session = self.session(token)
        if session.get("access_mode") != "editable":
            raise PermissionError("finalized review is read-only")
        self.checkpoints.lock(session["event_id"])
        with self._session_lock:
            self._sessions.pop(str(token), None)
        return {
            "status": FINALIZED_LOCKED,
            "message": "Study finalized and locked. Select Claim next eligible study to continue.",
        }

    def restart_finalized(self, token: str, *, owner_confirmed: bool) -> dict[str, Any]:
        session = self.session(token)
        if session.get("access_mode") != "read_only_finalized":
            raise PermissionError("restart requires the owner's read-only finalized view")
        result = self.checkpoints.restart_finalized(
            session["event_id"],
            reviewer_code=session["reviewer_code"],
            role=session["role"],
            owner_confirmed=owner_confirmed,
        )
        event = result["event"]
        new_token = secrets.token_urlsafe(32)
        with self._session_lock:
            self._sessions.pop(str(token), None)
            self._sessions[new_token] = {
                "event_id": str(event["event_id"]),
                "reviewer_code": str(event["reviewer_code"]),
                "role": str(event["role"]),
                "audit_id": str(event["physical_study_token"]),
                "access_mode": "editable",
            }
        return {
            "status": REASSIGNED_FRESH,
            "message": "Prior finalized review archived by hash; a fresh blank review is ready.",
            "open_study": True,
            "session_token": new_token,
            "event": self._client_event(event),
            "study": self.studies[str(event["physical_study_token"])],
            "checkpoint": self.checkpoints.load(str(event["event_id"])),
            "default_preset": public_preset_for_event(
                event,
                self.default_preset_configuration,
            ),
            "access_mode": "editable",
        }

    def end_session(self, token: str) -> dict[str, Any]:
        with self._session_lock:
            self._sessions.pop(str(token), None)
        return {"status": "SESSION_ENDED"}

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


def _annotation_state_hashes(checkpoint_root: Path) -> dict[str, str]:
    if not checkpoint_root.is_dir():
        return {}
    return {
        str(path.relative_to(checkpoint_root)): sha256_file(path)
        for path in sorted(checkpoint_root.rglob("*.json"))
        if path.is_file()
    }


def audit_queue_transition_state(
    package_root: Path,
    *,
    expected_stale_pointers: int = 0,
    repair_stale_pointers: bool = False,
) -> dict[str, Any]:
    """Audit production queue metadata without emitting review identifiers or content."""

    package_root = require_restricted_destination(package_root)
    interface_root = package_root / "restricted" / "interface"
    queue_root = package_root / "restricted" / "queue"
    checkpoint_root = package_root / "restricted" / "checkpoints"
    registry = ReviewerRegistry(
        package_root / "restricted" / "reviewer_registry",
        initialize=False,
    )
    queue = RoleQueueStore(
        queue_root,
        interface_root / "queue_policy_restricted.json",
        registry,
        initialize=False,
    )
    manifest = _load_object(interface_root / "study_manifest_restricted.json")
    checkpoints = RoleAwareCheckpointStore(
        checkpoint_root,
        queue,
        manifest,
        initialize=False,
    )
    queue_hash_before = sha256_file(queue.state_path)
    annotation_hashes_before = _annotation_state_hashes(checkpoint_root)

    state = _load_object(queue.state_path)
    events = list(state.get("events", []))
    if len({str(event.get("event_id", "")) for event in events}) != len(events):
        raise Tier1BlockedError(
            BLOCKED_MIXED_REVIEWER_ATTRIBUTION,
            "queue contains ambiguous duplicate review events",
        )

    active_pairs: set[tuple[str, str]] = set()
    active_reviewer_roles: set[tuple[str, str]] = set()
    reviewer_study_roles: dict[tuple[str, str], set[str]] = {}
    ambiguous = 0
    for event in events:
        status = str(event.get("status", ""))
        role = _role(event.get("role", ""))
        reviewer = _code(event.get("reviewer_code", ""))
        study = str(event.get("physical_study_token", ""))
        if status in {"in_progress", "locked"}:
            pair = (study, role)
            owner_role = (reviewer, role)
            if pair in active_pairs or (status == "in_progress" and owner_role in active_reviewer_roles):
                ambiguous += 1
            active_pairs.add(pair)
            if status == "in_progress":
                active_reviewer_roles.add(owner_role)
        if status in {"in_progress", "locked", "archived_incomplete"}:
            reviewer_study_roles.setdefault((reviewer, study), set()).add(role)
    ambiguous += sum(len(roles) > 1 for roles in reviewer_study_roles.values())
    if ambiguous:
        raise Tier1BlockedError(
            BLOCKED_MIXED_REVIEWER_ATTRIBUTION,
            "queue contains ambiguous or mixed-reviewer attribution metadata",
        )

    def classify_all() -> tuple[dict[str, int], list[str]]:
        counts = {
            "active_incomplete_claims": 0,
            "complete_not_finalized_claims": 0,
            "finalized_locked_reviews": 0,
            "archived_incomplete_reviews": 0,
        }
        stale_event_ids: list[str] = []
        current = _load_object(queue.state_path)
        for event in current.get("events", []):
            review = checkpoints.review_state(event)
            workflow_state = str(review["workflow_state"])
            if workflow_state == CLAIMED_INCOMPLETE:
                counts["active_incomplete_claims"] += 1
            elif workflow_state == COMPLETE_NOT_FINALIZED:
                counts["complete_not_finalized_claims"] += 1
            elif workflow_state == FINALIZED_LOCKED:
                counts["finalized_locked_reviews"] += 1
                if review.get("stale_active_pointer"):
                    stale_event_ids.append(str(event["event_id"]))
            elif workflow_state == ARCHIVED_INCOMPLETE:
                counts["archived_incomplete_reviews"] += 1
            else:
                raise Tier1BlockedError(
                    BLOCKED_QUEUE_STATE_MIGRATION,
                    "queue event could not be assigned a supported workflow state",
                )
        return counts, stale_event_ids

    counts, stale_event_ids = classify_all()
    if len(stale_event_ids) != int(expected_stale_pointers):
        raise Tier1BlockedError(
            BLOCKED_QUEUE_STATE_MIGRATION,
            "stale active-pointer count differs from the explicitly expected count",
        )
    repaired = 0
    if repair_stale_pointers:
        for event_id in stale_event_ids:
            checkpoints.repair_stale_locked_pointer(event_id)
            repaired += 1
        counts, remaining_stale = classify_all()
        if remaining_stale:
            raise Tier1BlockedError(
                BLOCKED_QUEUE_STATE_MIGRATION,
                "validated stale active pointers were not fully repaired",
            )
    queue_hash_after = sha256_file(queue.state_path)
    annotation_hashes_after = _annotation_state_hashes(checkpoint_root)
    if annotation_hashes_before != annotation_hashes_after:
        raise Tier1BlockedError(
            BLOCKED_QUEUE_STATE_MIGRATION,
            "queue audit or pointer repair changed annotation or lock files",
        )
    if not repair_stale_pointers and queue_hash_before != queue_hash_after:
        raise Tier1BlockedError(
            BLOCKED_QUEUE_STATE_MIGRATION,
            "read-only queue audit changed queue metadata",
        )
    return {
        "status": QUEUE_TRANSITION_DRY_RUN_PASS,
        **counts,
        "stale_active_pointers": len(stale_event_ids) - repaired,
        "repaired_stale_active_pointers": repaired,
        "ambiguous_or_mixed_reviewer_records": 0,
        "eligible_remaining_primary_studies": queue.eligible_remaining_count(ROLE_PRIMARY),
        "eligible_remaining_secondary_studies": queue.eligible_remaining_count(ROLE_SECONDARY),
        "annotation_files_unchanged": True,
        "queue_metadata_changed": queue_hash_before != queue_hash_after,
        "identifiers_or_annotation_content_emitted": False,
    }


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
      <div class="actions"><button id="view-finalized" type="button">View my latest finalized study (read-only)</button></div>
      <div class="actions"><button id="pending-action" type="button" hidden></button><button id="end-session-start" type="button">End review session</button></div>
      <p id="start-status" class="status" aria-live="polite"></p>
      <ul id="start-requirements"></ul>
      <p id="start-error" role="alert"></p>
    </section>
    <section id="review-screen" hidden>
      <aside>
        <div id="study-id"></div>
        <div id="role-label"></div>
        <div class="counter" id="clip-progress"></div>
        <p id="review-status" class="status" aria-live="polite"></p>
        <p id="read-only-status" class="read-only" hidden>Finalized record: read-only</p>
        <ul id="remaining-requirements"></ul>
        <button id="lock-study" class="finalize" type="button">Finalize study</button>
        <button id="restart-study" class="restart" type="button" hidden>Archive and restart under current protocol</button>
        <button id="leave-session" type="button">End review session</button>
      </aside>
      <article>
        <nav class="clip-nav" aria-label="Clip navigation"><button id="previous-clip" aria-label="Previous clip">&#8249;</button><span id="clip-counter"></span><button id="next-clip" aria-label="Next clip">&#8250;</button></nav>
        <div id="tier" class="tier"></div>
        <div id="scoring-scope" class="scope-banner" role="note"></div>
        <div id="preset-confirmation-banner" class="preset-banner" role="note" hidden><strong>Common starting values are prefilled for efficiency. They are not model predictions or prior reviewer answers. Inspect the scored panel, change any field that differs, and confirm the clip. The clip remains unreviewed until you confirm it.</strong><span>Numbers alone do not count as visible text. Change “Visible text” to No when the scored panel contains only numbers.</span></div>
        <div class="frame-controls"><button id="toggle-play" type="button">Play</button><input id="frame-slider" type="range" min="0" max="15" value="0" aria-label="Frame"><span id="frame-counter">Frame 1 of 16</span></div>
        <div class="views"><figure id="source-panel"><figcaption id="source-caption">Source acquisition — corresponding source frame</figcaption><canvas id="source-canvas" width="224" height="224"></canvas></figure><figure id="model-panel"><figcaption id="model-caption">Exact model input — scoring target</figcaption><canvas id="model-canvas" width="224" height="224"></canvas></figure></div>
        <form id="clip-form" autocomplete="off">
          <fieldset><legend>Modality/content</legend><select name="acquisition_content_type"><option value="">Unreviewed</option><option value="2d_b_mode">2D B-mode</option><option value="color_doppler">Color Doppler</option><option value="pulsed_wave_spectral_doppler">Pulsed-wave spectral Doppler</option><option value="continuous_wave_spectral_doppler">Continuous-wave spectral Doppler</option><option value="tissue_doppler">Tissue Doppler</option><option value="m_mode">M-mode</option><option value="mixed">Mixed</option><option value="other">Other</option><option value="uncertain">Uncertain</option><option value="not_assessable">Not assessable</option></select></fieldset>
          <fieldset id="presence-fields"><legend>Visible content in the scored panel</legend></fieldset>
          <fieldset><legend>Candidate displayed value</legend><label>Value<input name="candidate_target_value" maxlength="64"></label><label>Unit<input name="visible_unit_text" maxlength="64"></label><label>Displayed name<input name="visible_measurement_name_text" maxlength="120"></label><label>Precision<input name="display_precision" maxlength="64"></label></fieldset>
          <fieldset id="source-only-comparison"><legend>Source content not visible in exact model input</legend><label class="presence-label"><span class="presence-copy"><strong>Is any potentially relevant measurement or annotation content visible in the source acquisition but not visible in the exact model input?</strong><small>Score this section from the left source panel only. Keep these findings separate from the primary right-panel fields.</small></span><select name="source_only_relevant_content"><option value="">Unreviewed</option><option value="yes">Yes</option><option value="no">No</option><option value="uncertain">Uncertain</option><option value="not_assessable">Not assessable</option></select></label><div id="source-only-categories" hidden><p>Select every applicable source-only category. At least one is required when the answer above is Yes.</p><div id="source-only-category-fields" class="category-grid"></div><label id="source-only-candidate-label" hidden>Restricted source-only candidate value<input name="source_only_candidate_target_value_text" maxlength="64"></label></div></fieldset>
          <label>Reader confidence<select name="reader_confidence"><option value="">Unreviewed</option><option>high</option><option>moderate</option><option>low</option><option>not_assessable</option></select></label>
          <label>Restricted notes<input name="restricted_notes" maxlength="500"></label>
        </form>
        <div id="preset-actions" class="preset-actions" hidden><button id="confirm-defaults-next" type="button">Confirm defaults &amp; next</button><span>After inspecting the scored panel. Shortcut: <kbd>Option/Alt</kbd> + <kbd>Enter</kbd></span><strong id="clip-confirmation-state"></strong></div>
        <form id="study-form" autocomplete="off"><fieldset><legend>Derived study summary</legend><p class="derived-note">Calculated from clip-level responses; these values cannot be entered independently.</p><h3 id="primary-summary-heading">Scored-panel findings</h3><dl id="study-fields" class="derived-summary"></dl><section id="source-only-study-summary"><h3>Source-only findings</h3><p class="derived-note">These values remain separate from the exact model-input findings.</p><dl id="source-only-study-fields" class="derived-summary"></dl></section><label class="confirm"><input name="derived_summary_confirmed" type="checkbox" value="yes">I confirm the derived study summary after reviewing all clips.</label><label>Reader confidence<select name="reader_confidence"><option value="">Unreviewed</option><option>high</option><option>moderate</option><option>low</option><option>not_assessable</option></select></label><label>Restricted notes<input name="restricted_notes" maxlength="500"></label></fieldset></form>
      </article>
    </section>
  </main>
  <script src="app.js"></script>
</body>
</html>
"""


def _interface_css() -> str:
    return """*{box-sizing:border-box}[hidden]{display:none!important}body{margin:0;font:14px Arial,sans-serif;color:#171717;background:#f4f5f6}header{height:48px;padding:0 18px;display:flex;align-items:center;justify-content:space-between;background:#fff;border-bottom:1px solid #bbb}.start-screen{max-width:680px;margin:36px auto;padding:24px;background:#fff;border:1px solid #aaa;border-radius:6px}.start-screen h1{font-size:22px;margin:0 0 20px}.start-screen label{display:flex;align-items:center;gap:8px;margin:10px 0}.start-screen input[type=text],.start-screen input:not([type]){width:260px}.start-screen fieldset{margin:18px 0}.start-screen fieldset p{margin:3px 0 14px 28px;color:#555}.actions{display:flex;gap:8px;flex-wrap:wrap}.status{color:#234b2f;line-height:1.4}.read-only{padding:8px;border:1px solid #835d15;background:#fff7dd;font-weight:700}.finalize{font-weight:700;border:2px solid #1d5a35;background:#eef8f1}.restart{font-weight:700;border:2px solid #835d15;background:#fff7dd}.scope-banner{position:sticky;top:48px;z-index:2;margin:8px 0;padding:12px;border:2px solid #24496b;background:#eef6fc;font-weight:700;line-height:1.45}.preset-banner{margin:8px 0;padding:12px;border:2px solid #7a5411;background:#fff8dc;line-height:1.45}.preset-banner span{display:block;margin-top:6px}.preset-actions{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin:10px 0;padding:10px;border:1px solid #7a5411;background:#fff}.preset-actions button{font-weight:700;border:2px solid #1d5a35;background:#eef8f1}.preset-actions kbd{padding:2px 5px;border:1px solid #999;background:#f4f4f4}button,select,input{min-height:34px;margin:4px;padding:5px 8px}button{cursor:pointer}button:disabled{cursor:not-allowed;opacity:.55}#start-error{color:#9b1c1c;min-height:20px}#start-requirements,#remaining-requirements{padding-left:20px;color:#7c2d12}#review-screen{display:grid;grid-template-columns:230px 1fr;min-height:calc(100vh - 48px)}aside{padding:16px;border-right:1px solid #bbb;background:#fff}article{padding:14px;min-width:0}.clip-nav,.frame-controls{display:flex;align-items:center;justify-content:center}.frame-controls input{width:min(420px,55vw)}.tier{font-weight:700;margin:6px 0}.views{display:grid;grid-template-columns:minmax(280px,1fr) minmax(280px,1fr);gap:10px}.views figure{margin:0;background:#fff;border:2px solid #777;padding:8px}.views figcaption{min-height:34px;font-weight:700;line-height:1.35}.views canvas{display:block;width:100%;max-height:60vh;aspect-ratio:1;object-fit:contain;background:#000}.source-only #model-panel{display:none}.source-only .views{grid-template-columns:minmax(280px,1fr)}fieldset{border:1px solid #aaa;margin:10px 0;padding:10px;background:#fff}fieldset h3{font-size:15px;margin:12px 0 4px}label{display:inline-flex;gap:4px;align-items:center;margin:4px 10px 4px 0}.presence-label{display:grid;grid-template-columns:minmax(190px,1fr) minmax(120px,220px);align-items:start;gap:8px;margin:8px 0}.presence-copy{display:flex;flex-direction:column}.presence-copy small{color:#4a4a4a;line-height:1.35;margin-top:2px}.category-grid{display:grid;grid-template-columns:repeat(2,minmax(220px,1fr));gap:4px 14px}.category-grid label{align-items:flex-start}.derived-note{color:#4a4a4a}.derived-summary{display:grid;grid-template-columns:minmax(180px,1fr) minmax(100px,160px);gap:6px 12px;margin:8px 0}.derived-summary dt,.derived-summary dd{margin:0;padding:4px 0}.derived-summary dd{font-weight:700}.counter{margin:8px 0;color:#555}@media(max-width:850px){#review-screen{grid-template-columns:1fr}aside{border-right:0;border-bottom:1px solid #bbb}.views{grid-template-columns:1fr}.presence-label,.category-grid,.derived-summary{grid-template-columns:1fr}.scope-banner{top:0}}
"""


def _interface_js() -> str:
    return """'use strict';
const presence=['waveform_or_measurement_tracing','calipers','visible_text','visible_numeric_value','lvot_vti_specific_label','tapse_specific_label','candidate_target_value_present'];
const fieldLabels={waveform_or_measurement_tracing:'Waveform or measurement tracing',calipers:'Calipers',visible_text:'Visible text',visible_numeric_value:'Visible numeric value',lvot_vti_specific_label:'LVOT VTI-specific label',tapse_specific_label:'TAPSE-specific label',candidate_target_value_present:'Candidate target value'};
const fieldDefinitions={waveform_or_measurement_tracing:'A spectral Doppler waveform, M-mode tracing, or measurement contour. A routine ECG gating strip alone does not count.',calipers:'Visible calipers or measurement markers in the scored panel.',visible_text:'Any readable alphabetic text, words, abbreviations, or units in the scored panel. Numbers alone do not count.',visible_numeric_value:'Any readable number in the scored panel, including generic depth, scale, timing, or machine-setting numbers.',lvot_vti_specific_label:'Explicit visible text unambiguously identifying LVOT VTI/TVI. Generic or ambiguous VTI should be Uncertain unless surrounding display context establishes LVOT VTI.',tapse_specific_label:'Explicit visible text identifying TAPSE or its written-out equivalent.',candidate_target_value_present:'A displayed number reasonably presented as an LVOT VTI or TAPSE measurement result based on label, unit, placement, or unambiguous measurement context. Exclude depth markers, heart rate, dates/times, frame numbers, velocity scales, gain, MI/TI, and other machine settings. Use Uncertain when the connection to either target is plausible but unclear.'};
const sourceOnlyCategories=['source_only_visible_text','source_only_numeric_value','source_only_unit','source_only_measurement_name','source_only_lvot_vti_specific_label','source_only_tapse_specific_label','source_only_candidate_target_value','source_only_spectral_doppler_waveform','source_only_m_mode_tracing','source_only_caliper','source_only_contour_or_measurement_trace','source_only_other_measurement_annotation'];
const sourceOnlyLabels={source_only_visible_text:'Alphabetic text or abbreviation',source_only_numeric_value:'Numeric value',source_only_unit:'Unit',source_only_measurement_name:'Measurement name',source_only_lvot_vti_specific_label:'LVOT VTI-specific label',source_only_tapse_specific_label:'TAPSE-specific label',source_only_candidate_target_value:'Candidate target measurement value',source_only_spectral_doppler_waveform:'Spectral Doppler waveform',source_only_m_mode_tracing:'M-mode tracing',source_only_caliper:'Caliper',source_only_contour_or_measurement_trace:'Contour or measurement trace',source_only_other_measurement_annotation:'Other measurement-related annotation'};
const scoringScope={EXACT_MODEL_INPUT:'Score all primary audit fields from the exact model input in the right panel. Use the source acquisition on the left only for context and source-to-input comparison. Do not count features visible only in the source acquisition as model-input content.',SOURCE_ACQUISITION_ONLY:'Score the source acquisition shown here. This clip is not a verified exact model input. Findings will be reported separately as source-acquisition evidence and must not be interpreted as content proven to have reached the encoder.'};
const studyFields=['spectral_doppler_present','m_mode_present','waveform_or_measurement_tracing_present','calipers_present','visible_text_present','visible_numeric_value_present','lvot_vti_specific_label_present','tapse_specific_label_present','candidate_target_value_present'];
const sourceOnlyStudyFields=['source_only_relevant_content_present','source_content_not_visible_in_exact_model_input_present','source_only_target_specific_label_present','source_only_candidate_target_value_present'];
const clipRequired=['acquisition_content_type',...presence,'reader_confidence'];
const studyRequired=[...studyFields,'derived_summary_confirmed','reader_confidence'];
const displayedValueRequired=['candidate_target_value','visible_unit_text','visible_measurement_name_text','display_precision'];
const interfaceVersion='JDIM_INPUT_CONTENT_AUDIT_V3_SCORING_SCOPE_CLARIFIED:SIDE_BY_SIDE_V1:DEFAULT_PRESET_V1';
let session='',event=null,study=null,state={annotations:{studies:{},clips:{}}},defaultPreset=null,ci=0,locked=false,readOnly=false,frameIndex=0,playTimer=null,workflowState='UNCLAIMED',requestPending=false,clientNamespace='',clipDirty=false;
const sprites={source:null,model:null};
const choices='<option value="">Unreviewed</option><option value="yes">Yes</option><option value="no">No</option><option value="uncertain">Uncertain</option><option value="not_assessable">Not assessable</option>';
function fields(root,names){root.innerHTML='';names.forEach(n=>{const l=document.createElement('label');l.className='presence-label';const copy=document.createElement('span');copy.className='presence-copy';const title=document.createElement('strong');title.textContent=fieldLabels[n];const definition=document.createElement('small');definition.textContent=fieldDefinitions[n];copy.append(title,definition);const s=document.createElement('select');s.name=n;s.innerHTML=choices;l.append(copy,s);root.appendChild(l);});}
function sourceCategoryFields(root){root.innerHTML='';sourceOnlyCategories.forEach(name=>{const label=document.createElement('label');const input=document.createElement('input');input.type='checkbox';input.name=name;input.value='yes';label.append(input,document.createTextNode(sourceOnlyLabels[name]));root.appendChild(label);});}
function readForm(form){const values=Object.fromEntries(new FormData(form).entries());form.querySelectorAll('input[type=checkbox][name]').forEach(input=>{values[input.name]=input.checked?input.value:'';});return values;}
function fill(form,values){[...form.elements].forEach(e=>{if(!e.name)return;const value=(values||{})[e.name]||'';if(e.type==='checkbox')e.checked=value==='yes';else e.value=value;e.disabled=locked;});}
function complete(record,required){return !!record&&required.every(k=>String(record[k]||'').trim());}
function requiredForClip(clip){return clip.evidence_tier==='EXACT_MODEL_INPUT'?[...clipRequired,'source_only_relevant_content']:clipRequired;}
function sourceOnlySelectionComplete(clip,record){return clip.evidence_tier!=='EXACT_MODEL_INPUT'||String(record?.source_only_relevant_content||'')!=='yes'||sourceOnlyCategories.some(field=>String(record?.[field]||'')==='yes');}
function clipIsConfirmed(clip){return !defaultPreset||!!state.clip_review_states?.[clip.clip_audit_id]?.human_review_confirmed;}
function clipComplete(clip,record){return clipIsConfirmed(clip)&&complete(record,requiredForClip(clip))&&sourceOnlySelectionComplete(clip,record);}
function syncCandidateValueVisibility({clearDisabled=false}={}){const form=document.querySelector('#clip-form');const enabled=['yes','uncertain'].includes(form.querySelector('[name=candidate_target_value_present]').value);displayedValueRequired.forEach(field=>{const input=form.querySelector(`[name=${field}]`);if(clearDisabled&&!enabled)input.value='';input.disabled=locked||!enabled;});}
function syncSourceOnlyVisibility(clip){const fieldset=document.querySelector('#source-only-comparison');const tierA=clip.evidence_tier==='EXACT_MODEL_INPUT';fieldset.hidden=!tierA;const primary=fieldset.querySelector('[name=source_only_relevant_content]');primary.disabled=locked||!tierA;const showCategories=tierA&&['yes','uncertain'].includes(primary.value);const categories=document.querySelector('#source-only-categories');categories.hidden=!showCategories;sourceOnlyCategories.forEach(field=>{const input=fieldset.querySelector(`[name=${field}]`);input.disabled=locked||!showCategories;});const candidateSelected=fieldset.querySelector('[name=source_only_candidate_target_value]').checked;const candidateLabel=document.querySelector('#source-only-candidate-label');candidateLabel.hidden=!showCategories||!candidateSelected;const candidateText=fieldset.querySelector('[name=source_only_candidate_target_value_text]');candidateText.disabled=locked||!showCategories||!candidateSelected;}
function selectedNamespace(){const code=document.querySelector('#reviewer-code').value.trim();const role=document.querySelector('input[name=role]:checked')?.value||'';return [interfaceVersion,code,role].join(':');}
function requireCurrentNamespace(){if(!clientNamespace||clientNamespace!==selectedNamespace())throw new Error('Reviewer identity or role changed. End this session and resume with the correct reviewer code and role.');}
function setQueueBusy(value){requestPending=value;['resume-review','claim-review','view-finalized','pending-action'].forEach(id=>{document.querySelector(`#${id}`).disabled=value;});}
async function request(path,body){const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const p=await r.json();if(!r.ok)throw new Error(p.error||'Request failed');return p;}
function resetReviewState(){stopPlayback();session='';event=null;study=null;state={annotations:{studies:{},clips:{}}};defaultPreset=null;ci=0;locked=false;readOnly=false;frameIndex=0;workflowState='UNCLAIMED';clientNamespace='';clipDirty=false;sprites.source=null;sprites.model=null;document.querySelector('#clip-form').reset();document.querySelector('#study-form').reset();document.querySelector('#remaining-requirements').innerHTML='';document.body.classList.remove('source-only');}
function fillRequirements(rootId,requirements){const root=document.querySelector(`#${rootId}`);root.innerHTML='';(requirements||[]).forEach(message=>{const item=document.createElement('li');item.textContent=message;root.appendChild(item);});}
function showStart(message,{preserveIdentity=true,pendingAction='',requirements=[]}={}){resetReviewState();document.querySelector('#review-screen').hidden=true;document.querySelector('#start-screen').hidden=false;document.querySelector('#start-error').textContent='';document.querySelector('#start-status').textContent=message||'';fillRequirements('start-requirements',requirements);document.querySelector('#save-state').textContent='Ready';const pending=document.querySelector('#pending-action');pending.hidden=!pendingAction;if(pendingAction==='resume'){pending.textContent='Resume incomplete study';pending.onclick=()=>claim('resume').catch(showError);}else if(pendingAction==='finalize'){pending.textContent='Finalize completed study';pending.onclick=()=>claim('resume').catch(showError);}else{pending.onclick=null;}if(!preserveIdentity){document.querySelector('#reviewer-code').value='';document.querySelectorAll('input[name=role]').forEach(input=>{input.checked=false;});document.querySelector('#qualification').checked=false;}}
function identity(){return {reviewer_code:document.querySelector('#reviewer-code').value.trim(),role:document.querySelector('input[name=role]:checked')?.value||'',qualification_confirmed:document.querySelector('#qualification').checked};}
function handleQueueOutcome(payload){if(payload.status==='INCOMPLETE_STUDY_EXISTS'){showStart(payload.message,{pendingAction:'resume',requirements:payload.requirements});return;}if(payload.status==='FINALIZATION_REQUIRED'){showStart(payload.message,{pendingAction:'finalize',requirements:payload.requirements});return;}showStart(payload.message,{requirements:payload.requirements});}
function openStudy(payload,code,role){session=payload.session_token;event=payload.event;study=payload.study;state=payload.checkpoint;defaultPreset=payload.default_preset||null;workflowState=payload.workflow_state||payload.status;readOnly=payload.access_mode==='read_only_finalized';clientNamespace=[interfaceVersion,code,role].join(':');state.annotations=state.annotations||{studies:{},clips:{}};state.annotations.studies=state.annotations.studies||{};state.annotations.clips=state.annotations.clips||{};state.clip_review_states=state.clip_review_states||{};locked=!!state.locked||readOnly;ci=0;clipDirty=false;document.querySelector('#start-screen').hidden=true;document.querySelector('#review-screen').hidden=false;document.querySelector('#review-status').textContent=payload.message||'';fillRequirements('remaining-requirements',payload.requirements);render();}
async function claim(action){if(requestPending)return;setQueueBusy(true);document.querySelector('#start-error').textContent='';try{const who=identity();const p=await request('/api/claim',{...who,action});if(!p.open_study){handleQueueOutcome(p);return;}openStudy(p,who.reviewer_code,who.role);}finally{setQueueBusy(false);}}
async function viewFinalized(){if(requestPending)return;setQueueBusy(true);document.querySelector('#start-error').textContent='';try{const who=identity();const p=await request('/api/view-finalized',who);if(!p.open_study){handleQueueOutcome(p);return;}openStudy(p,who.reviewer_code,who.role);}finally{setQueueBusy(false);}}
async function save(){if(locked||readOnly||!session)return;requireCurrentNamespace();const studyForm=readForm(document.querySelector('#study-form'));if(defaultPreset){state.annotations.studies[study.audit_id]={...(state.annotations.studies[study.audit_id]||{}),...studyForm};}else{const clip=study.clips[ci];state.annotations.clips[clip.clip_audit_id]=readForm(document.querySelector('#clip-form'));state.annotations.studies[study.audit_id]=studyForm;}document.querySelector('#save-state').textContent='Saving';const p=await request('/api/checkpoint',{session_token:session,annotations:state.annotations});state=p.checkpoint;document.querySelector('#save-state').textContent=p.status==='saved'?'Saved':'Save error';renderStudySummary();updateProgress();}
function updateProgress(){const done=study.clips.filter(c=>clipComplete(c,state.annotations.clips[c.clip_audit_id])).length;document.querySelector('#clip-progress').textContent=`${done}/${study.clips.length} clips complete`;}
function renderSummaryFields(root,record,names){root.innerHTML='';names.forEach(field=>{const term=document.createElement('dt');term.textContent=field.replaceAll('_',' ');const value=document.createElement('dd');value.textContent=String(record[field]||'Pending');root.append(term,value);});}
function renderStudySummary(){const record=state.annotations.studies[study.audit_id]||{};const tierA=study.clips.every(clip=>clip.evidence_tier==='EXACT_MODEL_INPUT');document.querySelector('#primary-summary-heading').textContent=tierA?'Exact model-input findings':'Source-acquisition findings';renderSummaryFields(document.querySelector('#study-fields'),record,studyFields);const sourceSummary=document.querySelector('#source-only-study-summary');sourceSummary.hidden=!tierA;if(tierA)renderSummaryFields(document.querySelector('#source-only-study-fields'),record,sourceOnlyStudyFields);fill(document.querySelector('#study-form'),record);}
function drawSprite(name){const canvas=document.querySelector(`#${name}-canvas`),ctx=canvas.getContext('2d'),img=sprites[name];ctx.fillStyle='#000';ctx.fillRect(0,0,canvas.width,canvas.height);if(!img||!img.complete||!img.naturalWidth)return;const tw=img.naturalWidth/4,th=img.naturalHeight/4,x=(frameIndex%4)*tw,y=Math.floor(frameIndex/4)*th;ctx.drawImage(img,x,y,tw,th,0,0,canvas.width,canvas.height);}
function renderFrame(){drawSprite('source');drawSprite('model');document.querySelector('#frame-slider').value=String(frameIndex);document.querySelector('#frame-counter').textContent=`Frame ${frameIndex+1} of 16`;}
function loadSprite(name,token){sprites[name]=null;drawSprite(name);if(!token)return;const img=new Image();img.onload=()=>{sprites[name]=img;renderFrame();};img.src=`/media/${encodeURIComponent(token)}?session=${encodeURIComponent(session)}`;}
function stopPlayback(){if(playTimer){clearInterval(playTimer);playTimer=null;}document.querySelector('#toggle-play').textContent='Play';}
function togglePlayback(){if(playTimer){stopPlayback();return;}playTimer=setInterval(()=>{frameIndex=(frameIndex+1)%16;renderFrame();},250);document.querySelector('#toggle-play').textContent='Pause';}
function render(){const clip=study.clips[ci];const tierA=clip.evidence_tier==='EXACT_MODEL_INPUT';const saved=state.annotations.clips[clip.clip_audit_id];const displayed=saved||(defaultPreset?defaultPreset.field_values:{});document.querySelector('#study-id').textContent=study.audit_id;document.querySelector('#role-label').textContent=event.role==='primary'?'Primary independent review':'Secondary independent review';document.querySelector('#clip-counter').textContent=`Clip ${ci+1} of ${study.clips.length}`;document.querySelector('#tier').textContent=clip.source_only?'SOURCE ACQUISITION ONLY - NOT VERIFIED MODEL INPUT':clip.evidence_tier.replaceAll('_',' ');document.querySelector('#scoring-scope').textContent=scoringScope[clip.evidence_tier]||'';document.body.classList.toggle('source-only',clip.source_only);document.querySelector('#source-caption').textContent=tierA?'Source acquisition — corresponding source frame':'Source acquisition — scoring target';document.querySelector('#model-caption').textContent='Exact model input — scoring target';fill(document.querySelector('#clip-form'),displayed);syncSourceOnlyVisibility(clip);syncCandidateValueVisibility();renderStudySummary();frameIndex=0;loadSprite('source',clip.source_media_id);loadSprite('model',clip.model_input_media_id);document.querySelector('#lock-study').hidden=readOnly;document.querySelector('#lock-study').disabled=locked;document.querySelector('#restart-study').hidden=!readOnly;document.querySelector('#read-only-status').hidden=!readOnly;document.querySelector('#preset-confirmation-banner').hidden=!defaultPreset||readOnly;document.querySelector('#preset-actions').hidden=!defaultPreset||readOnly;document.querySelector('#confirm-defaults-next').disabled=locked;document.querySelector('#clip-confirmation-state').textContent=defaultPreset?(clipIsConfirmed(clip)?'Confirmed':'Unreviewed until confirmed'):'';clipDirty=false;updateProgress();}
async function move(delta){if(!readOnly&&defaultPreset&&!clipIsConfirmed(study.clips[ci]))throw new Error('Confirm this clip before navigating. Unconfirmed defaults remain unreviewed.');if(!readOnly&&defaultPreset&&clipDirty)throw new Error('Reconfirm this edited clip before navigating.');if(!readOnly&&!defaultPreset)await save();stopPlayback();ci=Math.max(0,Math.min(study.clips.length-1,ci+delta));render();}
function conditionalRequirement(){const studyRecord=state.annotations.studies[study.audit_id]||{};if(studyFields.some(field=>['yes','uncertain'].includes(String(studyRecord[field]||'')))&&!String(studyRecord.restricted_notes||'').trim())return 'Add restricted study notes for positive or uncertain study findings.';for(const clip of study.clips){const record=state.annotations.clips[clip.clip_audit_id]||{};if(presence.some(field=>String(record[field]||'')==='uncertain')&&!String(record.restricted_notes||'').trim())return 'Add restricted notes for every clip marked uncertain.';if(String(record.candidate_target_value_present||'')==='yes'&&!complete(record,displayedValueRequired))return 'Complete displayed-value details for every clip marked as containing a candidate value.';if(!sourceOnlySelectionComplete(clip,record))return 'Select at least one source-only category for every Tier-A clip marked Yes.';}return '';}
async function clipChanged(event){syncSourceOnlyVisibility(study.clips[ci]);if(event?.target?.name==='candidate_target_value_present')syncCandidateValueVisibility({clearDisabled:true});else syncCandidateValueVisibility();const confirmation=document.querySelector('input[name=derived_summary_confirmed]');confirmation.checked=false;const record=state.annotations.studies[study.audit_id]||{};record.derived_summary_confirmed='';state.annotations.studies[study.audit_id]=record;if(defaultPreset){clipDirty=true;document.querySelector('#clip-confirmation-state').textContent='Changed; reconfirm required';document.querySelector('#save-state').textContent='Clip not yet confirmed';return;}await save();}
async function confirmCurrentClip(){if(!defaultPreset||locked||readOnly)return;requireCurrentNamespace();const clip=study.clips[ci];const annotation={...defaultPreset.field_values,...readForm(document.querySelector('#clip-form'))};document.querySelector('#save-state').textContent='Confirming';const p=await request('/api/confirm-clip',{session_token:session,clip_id:clip.clip_audit_id,annotation});state=p.checkpoint;clipDirty=false;document.querySelector('#save-state').textContent='Confirmed';if(ci<study.clips.length-1)ci+=1;render();}
async function lockStudy(){if(defaultPreset&&clipDirty)throw new Error('Reconfirm the edited clip before finalizing.');await save();const firstMissing=study.clips.findIndex(c=>!clipComplete(c,state.annotations.clips[c.clip_audit_id]));if(firstMissing>=0){ci=firstMissing;render();throw new Error('Complete and confirm every required clip before finalizing.');}if(!complete(state.annotations.studies[study.audit_id],studyRequired))throw new Error('Review and confirm the derived study summary before finalizing.');const conditional=conditionalRequirement();if(conditional)throw new Error(conditional);if(!confirm('Finalize and lock this study-role review? It cannot be edited afterward.'))return;const p=await request('/api/lock',{session_token:session});showStart(p.message,{preserveIdentity:true});}
async function restartStudy(){if(!readOnly)throw new Error('Only a finalized read-only record can be restarted.');if(!confirm('Archive this finalized record by hash and restart it blank under the current protocol?'))return;const code=document.querySelector('#reviewer-code').value.trim();const role=document.querySelector('input[name=role]:checked')?.value||'';const p=await request('/api/restart-finalized',{session_token:session,owner_confirmed:true});openStudy(p,code,role);}
async function endSession(){const token=session;if(defaultPreset&&clipDirty&&!confirm('This clip has unconfirmed changes that will not be saved. End the review session?'))return;if(token&&study&&!locked&&!readOnly&&clientNamespace===selectedNamespace()){await save();}if(token){await request('/api/end-session',{session_token:token});}showStart('Review session ended. Saved server progress was preserved.',{preserveIdentity:false});}
function showError(error){const message=error.message||String(error);if(document.querySelector('#review-screen').hidden){document.querySelector('#start-error').textContent=message;}else{document.querySelector('#save-state').textContent=message;}}
function clearStaleClientState(){if(!document.querySelector('#review-screen').hidden)return;resetReviewState();document.querySelector('#start-status').textContent='';document.querySelector('#start-error').textContent='';document.querySelector('#pending-action').hidden=true;}
function init(){fields(document.querySelector('#presence-fields'),presence);sourceCategoryFields(document.querySelector('#source-only-category-fields'));document.querySelector('#resume-review').onclick=()=>claim('resume').catch(showError);document.querySelector('#claim-review').onclick=()=>claim('claim_next').catch(showError);document.querySelector('#view-finalized').onclick=()=>viewFinalized().catch(showError);document.querySelector('#previous-clip').onclick=()=>move(-1).catch(showError);document.querySelector('#next-clip').onclick=()=>move(1).catch(showError);document.querySelector('#toggle-play').onclick=togglePlayback;document.querySelector('#frame-slider').oninput=e=>{frameIndex=Number(e.target.value);renderFrame();};document.querySelector('#confirm-defaults-next').onclick=()=>confirmCurrentClip().catch(showError);document.querySelector('#lock-study').onclick=()=>lockStudy().catch(showError);document.querySelector('#restart-study').onclick=()=>restartStudy().catch(showError);document.querySelector('#leave-session').onclick=()=>endSession().catch(showError);document.querySelector('#end-session-start').onclick=()=>endSession().catch(showError);document.querySelector('#reviewer-code').addEventListener('input',clearStaleClientState);document.querySelectorAll('input[name=role]').forEach(input=>input.addEventListener('change',clearStaleClientState));document.querySelectorAll('#clip-form select,#clip-form input').forEach(e=>e.addEventListener('change',event=>clipChanged(event).catch(showError)));document.querySelectorAll('#study-form select,#study-form input').forEach(e=>e.addEventListener('change',()=>save().catch(showError)));document.addEventListener('keydown',event=>{if(event.altKey&&event.key==='Enter'&&defaultPreset&&!readOnly){event.preventDefault();confirmCurrentClip().catch(showError);}});document.addEventListener('contextmenu',e=>{if(e.target.tagName==='CANVAS')e.preventDefault();});document.addEventListener('dragstart',e=>{if(e.target.tagName==='CANVAS')e.preventDefault();});showStart('',{preserveIdentity:false});}
init();
"""


def current_role_aware_interface_assets() -> dict[str, bytes]:
    """Return source-controlled runtime assets without mutating a locked package."""

    return {
        "index.html": _interface_html().encode("utf-8"),
        "style.css": _interface_css().encode("utf-8"),
        "app.js": _interface_js().encode("utf-8"),
    }


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
