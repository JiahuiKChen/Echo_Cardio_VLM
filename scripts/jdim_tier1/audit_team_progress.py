"""Server-derived team progress and separately authorized audit coordination."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from .audit_default_preset import (
    _review_state_files,
    aggregate_review_state_counts,
)
from .audit_protocol_v3 import PROTOCOL_V3_NAME, active_protocol_paths, validate_active_protocol_v3
from .reduced_audit import FORMAL_RELIABILITY_N, LVOT_VTI, TAPSE, TIER_A, TIER_C
from .safety import (
    BLOCKED_LINEAGE,
    Tier1BlockedError,
    require_columns,
    require_restricted_destination,
    sha256_file,
    sha256_json,
)


TEAM_PROGRESS_SCHEMA = "jdim-audit-team-cumulative-progress-v1"
TEAM_PROGRESS_CONFIG_FILENAME = "team_cumulative_progress_v1_restricted.json"
OWNER_ACCESS_SCHEMA = "jdim-audit-owner-access-v1"
OWNER_ACCESS_CONFIG_FILENAME = "owner_access_v1_restricted.json"
OWNER_SECRET_FILENAME = "owner_access_secret_v1.txt"
ADMIN_LOG_SCHEMA = "jdim-audit-owner-action-log-v1"
PROGRESS_SCHEMA_VERSION = "JDIM_AUDIT_TEAM_CUMULATIVE_PROGRESS_V1"
LVOT_VTI_TARGET_REVIEW_GOAL = 15
TAPSE_TARGET_REVIEW_GOAL = 15
STALE_CLAIM_HOURS = 24
PBKDF2_ITERATIONS = 240_000

TEAM_CUMULATIVE_PROGRESS_DRY_RUN_PASS = "TEAM_CUMULATIVE_PROGRESS_DRY_RUN_PASS"
OWNER_VIEW_DRY_RUN_PASS = "OWNER_VIEW_DRY_RUN_PASS"
TEAM_CUMULATIVE_PROGRESS_PRODUCTION_VERIFIED = (
    "TEAM_CUMULATIVE_PROGRESS_PRODUCTION_VERIFIED"
)
OWNER_VIEW_PRODUCTION_VERIFIED = "OWNER_VIEW_PRODUCTION_VERIFIED"
READY_FOR_TEAM_CUMULATIVE_V3_HUMAN_AUDIT = (
    "READY_FOR_TEAM_CUMULATIVE_V3_HUMAN_AUDIT"
)

TARGET_AUDIT_MINIMUM_REACHED = "TARGET_AUDIT_MINIMUM_REACHED"
FORMAL_REPEAT_REVIEW_COMPLETE = "FORMAL_REPEAT_REVIEW_COMPLETE"
BLINDED_ADJUDICATION_COMPLETE = "BLINDED_ADJUDICATION_COMPLETE"
AUDIT_READY_FOR_LOCKED_AGGREGATION = "AUDIT_READY_FOR_LOCKED_AGGREGATION"


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
    if len(commit) != 40 or any(character not in "0123456789abcdef" for character in commit):
        raise ValueError("source commit must be a complete Git SHA")
    return commit


def _parse_utc(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _hours_since(value: Any, now: datetime) -> float | None:
    timestamp = _parse_utc(value)
    if timestamp is None:
        return None
    return max(0.0, (now - timestamp).total_seconds() / 3600.0)


def _owner_digest(secret: str, salt_hex: str, iterations: int = PBKDF2_ITERATIONS) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256",
        secret.encode("utf-8"),
        bytes.fromhex(salt_hex),
        iterations,
    ).hex()


def build_owner_access_configuration(
    *, source_commit: str, secret: str, created_at_utc: str | None = None
) -> dict[str, Any]:
    secret = str(secret)
    if len(secret) < 20:
        raise ValueError("owner access secret must contain at least 20 characters")
    salt = secrets.token_hex(16)
    return {
        "schema_version": OWNER_ACCESS_SCHEMA,
        "source_commit": _source_commit(source_commit),
        "created_at_utc": str(created_at_utc or utc_now()),
        "pbkdf2_hash": "sha256",
        "pbkdf2_iterations": PBKDF2_ITERATIONS,
        "salt_hex": salt,
        "secret_digest": _owner_digest(secret, salt),
        "ordinary_reviewer_codes_are_owner_credentials": False,
    }


class OwnerAccessControl:
    """Verify a restricted owner secret without exposing it to reviewer APIs."""

    def __init__(self, config_path: Path):
        self.config_path = require_restricted_destination(config_path)
        self.config = _load_object(self.config_path)
        if (
            self.config.get("schema_version") != OWNER_ACCESS_SCHEMA
            or self.config.get("pbkdf2_hash") != "sha256"
            or int(self.config.get("pbkdf2_iterations", -1)) != PBKDF2_ITERATIONS
            or len(str(self.config.get("salt_hex", ""))) != 32
            or len(str(self.config.get("secret_digest", ""))) != 64
            or self.config.get("ordinary_reviewer_codes_are_owner_credentials") is not False
        ):
            raise ValueError("owner access configuration is invalid")

    def verify(self, secret: str) -> bool:
        supplied = _owner_digest(
            str(secret),
            str(self.config["salt_hex"]),
            int(self.config["pbkdf2_iterations"]),
        )
        return hmac.compare_digest(supplied, str(self.config["secret_digest"]))


def _target_assignment_state(
    assignments_path: Path,
    primary_queue: list[str],
) -> tuple[dict[str, set[str]], dict[str, str]]:
    assignments = pd.read_csv(assignments_path).fillna("")
    require_columns(assignments, ["audit_id", "target", "evidence_tier"], "target assignments")
    assignments["audit_id"] = assignments["audit_id"].astype(str)
    assignments["target"] = assignments["target"].astype(str)
    assignments["evidence_tier"] = assignments["evidence_tier"].astype(str)
    if set(assignments["target"]) != {LVOT_VTI, TAPSE}:
        raise Tier1BlockedError(BLOCKED_LINEAGE, "target assignments contain an unexpected target")
    if assignments.duplicated(["audit_id", "target"]).any():
        raise Tier1BlockedError(BLOCKED_LINEAGE, "target assignments contain duplicate target-study rows")
    target_studies = {
        target: set(assignments.loc[assignments["target"].eq(target), "audit_id"])
        for target in (LVOT_VTI, TAPSE)
    }
    if (
        len(target_studies[LVOT_VTI]) != LVOT_VTI_TARGET_REVIEW_GOAL
        or len(target_studies[TAPSE]) != TAPSE_TARGET_REVIEW_GOAL
    ):
        raise Tier1BlockedError(BLOCKED_LINEAGE, "target denominators are not exactly 15 and 15")
    selected = set(map(str, primary_queue))
    if target_studies[LVOT_VTI] | target_studies[TAPSE] != selected:
        raise Tier1BlockedError(BLOCKED_LINEAGE, "target assignments differ from the Primary queue")
    tier_by_study: dict[str, str] = {}
    for audit_id, group in assignments.groupby("audit_id"):
        tiers = set(group["evidence_tier"])
        if len(tiers) != 1 or not tiers.issubset({TIER_A, TIER_C}):
            raise Tier1BlockedError(BLOCKED_LINEAGE, "one selected study has ambiguous evidence tier")
        tier_by_study[str(audit_id)] = next(iter(tiers))
    return target_studies, tier_by_study


def _effective_events(state: Mapping[str, Any], protocol_name: str) -> list[dict[str, Any]]:
    events = state.get("events", [])
    if not isinstance(events, list):
        raise ValueError("queue events are invalid")
    superseded = set(map(str, state.get("superseded_event_ids", [])))
    effective = []
    for raw in events:
        if not isinstance(raw, Mapping):
            raise ValueError("queue event is invalid")
        event = dict(raw)
        if event.get("protocol_name", protocol_name) != protocol_name:
            raise Tier1BlockedError(BLOCKED_LINEAGE, "current queue contains a protocol mismatch")
        if str(event.get("event_id", "")) not in superseded:
            effective.append(event)
    return effective


def calculate_team_progress(
    *,
    primary_queue: list[str],
    formal_queue: list[str],
    target_studies: Mapping[str, set[str]],
    tier_by_study: Mapping[str, str],
    events: list[Mapping[str, Any]],
    review_states: Mapping[str, Mapping[str, Any]],
    reviewers: list[Mapping[str, Any]],
    protocol_name: str = PROTOCOL_V3_NAME,
    now_utc: str | None = None,
) -> dict[str, Any]:
    """Calculate aggregate progress without persisting counters or reading annotations."""

    selected = set(map(str, primary_queue))
    formal = set(map(str, formal_queue))
    if len(formal) != FORMAL_RELIABILITY_N or not formal.issubset(selected):
        raise Tier1BlockedError(BLOCKED_LINEAGE, "formal reliability denominator is not locked at 8")
    if set(tier_by_study) != selected:
        raise Tier1BlockedError(BLOCKED_LINEAGE, "evidence tiers differ from the Primary queue")
    now = _parse_utc(now_utc or utc_now())
    if now is None:
        raise ValueError("current UTC timestamp is invalid")

    locked_primary: set[str] = set()
    primary_in_progress: set[str] = set()
    locked_formal_secondary: set[str] = set()
    formal_secondary_in_progress: set[str] = set()
    supplemental_secondary = 0
    complete_not_finalized = 0
    blocked_unconfirmed = 0
    stale_claims = 0
    archived = 0
    restarted = 0
    seen_locked_primary: set[str] = set()
    reviewer_rows: dict[str, dict[str, Any]] = {
        str(row.get("reviewer_code", "")): {
            "reviewer_code": str(row.get("reviewer_code", "")),
            "active": row.get("active") is True,
            "incomplete_primary": 0,
            "finalized_primary": 0,
            "incomplete_secondary": 0,
            "finalized_secondary": 0,
            "last_save_timestamp_utc": None,
            "current_claim_age_hours": None,
        }
        for row in reviewers
    }
    operational_active: list[dict[str, Any]] = []
    operational_locked: list[dict[str, Any]] = []

    for event in events:
        if event.get("protocol_name", protocol_name) != protocol_name:
            raise Tier1BlockedError(BLOCKED_LINEAGE, "progress event protocol does not match")
        event_id = str(event.get("event_id", ""))
        audit_id = str(event.get("physical_study_token", ""))
        reviewer = str(event.get("reviewer_code", ""))
        role = str(event.get("role", ""))
        if audit_id not in selected or role not in {"primary", "secondary"} or not event_id:
            raise Tier1BlockedError(BLOCKED_LINEAGE, "progress event identity is invalid")
        state = review_states.get(event_id)
        if not isinstance(state, Mapping):
            raise Tier1BlockedError(BLOCKED_LINEAGE, "progress event lacks validated review state")
        workflow = str(state.get("workflow_state", ""))
        if reviewer not in reviewer_rows:
            reviewer_rows[reviewer] = {
                "reviewer_code": reviewer,
                "active": False,
                "incomplete_primary": 0,
                "finalized_primary": 0,
                "incomplete_secondary": 0,
                "finalized_secondary": 0,
                "last_save_timestamp_utc": None,
                "current_claim_age_hours": None,
            }
        workload = reviewer_rows[reviewer]
        last_save = str(event.get("last_save_timestamp_utc") or "")
        if last_save and (
            not workload["last_save_timestamp_utc"]
            or last_save > str(workload["last_save_timestamp_utc"])
        ):
            workload["last_save_timestamp_utc"] = last_save
        if event.get("restarted_from_event_id"):
            restarted += 1

        if workflow == "FINALIZED_LOCKED":
            if state.get("completion_validation_passed") is not True:
                raise Tier1BlockedError(BLOCKED_LINEAGE, "locked review failed completion validation")
            if role == "primary":
                if audit_id in seen_locked_primary:
                    raise Tier1BlockedError(BLOCKED_LINEAGE, "duplicate effective Primary completion")
                seen_locked_primary.add(audit_id)
                locked_primary.add(audit_id)
                workload["finalized_primary"] += 1
            else:
                workload["finalized_secondary"] += 1
                if event.get("formal_reliability") is True and audit_id in formal:
                    locked_formal_secondary.add(audit_id)
                elif event.get("supplemental_review") is True:
                    supplemental_secondary += 1
            operational_locked.append(
                {
                    "event_id": event_id,
                    "audit_id": audit_id,
                    "reviewer_code": reviewer,
                    "role": role,
                    "locked_at_utc": str(event.get("locked_at_utc") or ""),
                }
            )
        elif workflow in {"CLAIMED_INCOMPLETE", "COMPLETE_NOT_FINALIZED"}:
            age = _hours_since(event.get("claim_timestamp_utc"), now)
            workload[f"incomplete_{role}"] += 1
            workload["current_claim_age_hours"] = round(age, 1) if age is not None else None
            stale_claims += int(age is not None and age >= STALE_CLAIM_HOURS)
            complete_not_finalized += int(workflow == "COMPLETE_NOT_FINALIZED")
            required = int(state.get("required_clip_count", 0))
            complete = int(state.get("completed_clip_count", 0))
            blocked_unconfirmed += int(required > complete)
            if role == "primary":
                primary_in_progress.add(audit_id)
            elif event.get("formal_reliability") is True and audit_id in formal:
                formal_secondary_in_progress.add(audit_id)
            operational_active.append(
                {
                    "event_id": event_id,
                    "audit_id": audit_id,
                    "reviewer_code": reviewer,
                    "role": role,
                    "workflow_state": workflow,
                    "claim_age_hours": round(age, 1) if age is not None else None,
                }
            )
        elif workflow == "ARCHIVED_INCOMPLETE":
            archived += 1
        else:
            raise Tier1BlockedError(BLOCKED_LINEAGE, "unsupported progress workflow state")

    lvot_done = len(locked_primary & set(target_studies[LVOT_VTI]))
    tapse_done = len(locked_primary & set(target_studies[TAPSE]))
    target_minimum = (
        lvot_done >= LVOT_VTI_TARGET_REVIEW_GOAL
        and tapse_done >= TAPSE_TARGET_REVIEW_GOAL
    )
    primary_complete = target_minimum and locked_primary == selected
    repeat_complete = len(locked_formal_secondary) == FORMAL_RELIABILITY_N
    tier_totals = {
        tier: len({audit_id for audit_id, assigned in tier_by_study.items() if assigned == tier})
        for tier in (TIER_A, TIER_C)
    }
    tier_done = {
        tier: len({audit_id for audit_id in locked_primary if tier_by_study[audit_id] == tier})
        for tier in (TIER_A, TIER_C)
    }
    shared = {
        "schema_version": PROGRESS_SCHEMA_VERSION,
        "team_cumulative": True,
        "target_progress": {
            LVOT_VTI: {"finalized": lvot_done, "goal": LVOT_VTI_TARGET_REVIEW_GOAL},
            TAPSE: {"finalized": tapse_done, "goal": TAPSE_TARGET_REVIEW_GOAL},
        },
        "physical_studies": {
            "finalized": len(locked_primary),
            "total": len(selected),
        },
        "primary": {
            "finalized_events": len(locked_primary),
            "in_progress": len(primary_in_progress),
            "not_yet_claimed": len(selected - locked_primary - primary_in_progress),
            "formal_claiming_closed": primary_complete,
        },
        "formal_repeat_reviews": {
            "finalized": len(locked_formal_secondary),
            "goal": FORMAL_RELIABILITY_N,
            "in_progress": len(formal_secondary_in_progress),
            "not_yet_claimed": len(formal - locked_formal_secondary - formal_secondary_in_progress),
        },
        "evidence_tiers": {
            TIER_A: {"finalized": tier_done[TIER_A], "total": tier_totals[TIER_A]},
            TIER_C: {"finalized": tier_done[TIER_C], "total": tier_totals[TIER_C]},
        },
        "statuses": {
            TARGET_AUDIT_MINIMUM_REACHED: target_minimum,
            FORMAL_REPEAT_REVIEW_COMPLETE: repeat_complete,
            BLINDED_ADJUDICATION_COMPLETE: False,
            AUDIT_READY_FOR_LOCKED_AGGREGATION: False,
        },
        "counting_note": (
            "Cumulative team totals; only finalized and locked Primary reviews count "
            "toward the 15-study target goals."
        ),
        "repeat_note": "Repeat reviews assess agreement and do not count toward target totals.",
    }
    owner = {
        "shared": shared,
        "reviewer_workload": sorted(reviewer_rows.values(), key=lambda row: row["reviewer_code"]),
        "role_activity": {
            "primary_finalized": len(locked_primary),
            "primary_in_progress": len(primary_in_progress),
            "primary_unclaimed": shared["primary"]["not_yet_claimed"],
            "formal_secondary_finalized": len(locked_formal_secondary),
            "formal_secondary_in_progress": len(formal_secondary_in_progress),
            "formal_secondary_unclaimed": shared["formal_repeat_reviews"]["not_yet_claimed"],
            "supplemental_secondary_finalized": supplemental_secondary,
        },
        "data_quality": {
            "complete_not_finalized_reviews": complete_not_finalized,
            "reviews_blocked_by_unconfirmed_or_incomplete_clips": blocked_unconfirmed,
            "archived_or_superseded_current_protocol_records": archived,
            "owner_restarted_effective_records": restarted,
            "stale_claims": stale_claims,
            "stale_claim_threshold_hours": STALE_CLAIM_HOURS,
            "current_protocol_review_events": len(events),
            "post_cutover_preset_events": sum(
                bool(event.get("clip_default_preset_version")) for event in events
            ),
            "pre_cutover_events": sum(
                not bool(event.get("clip_default_preset_version")) for event in events
            ),
        },
        "active_events": operational_active,
        "finalized_events": operational_locked,
        "annotation_content_included": False,
        "target_membership_by_study_included": False,
    }
    return {"shared": shared, "owner": owner}


class TeamProgressCoordinator:
    """Read live progress from validated queue/checkpoint state without persisting counters."""

    def __init__(self, package_root: Path, queue: Any, checkpoints: Any, registry: Any):
        self.package_root = require_restricted_destination(package_root)
        self.queue = queue
        self.checkpoints = checkpoints
        self.registry = registry
        assignments_path = (
            self.package_root
            / "restricted"
            / "roster"
            / "reduced_target_assignments_restricted.csv"
        )
        self.target_studies, self.tier_by_study = _target_assignment_state(
            assignments_path,
            list(map(str, self.queue.policy["primary_queue"])),
        )

    def _calculated(self) -> dict[str, Any]:
        state = _load_object(self.queue.state_path)
        events = _effective_events(state, self.queue.protocol_name)
        review_states = {
            str(event["event_id"]): self.checkpoints.review_state(event) for event in events
        }
        registry = self.registry.load()
        calculated = calculate_team_progress(
            primary_queue=list(map(str, self.queue.policy["primary_queue"])),
            formal_queue=list(map(str, self.queue.policy["formal_reliability_queue"])),
            target_studies=self.target_studies,
            tier_by_study=self.tier_by_study,
            events=events,
            review_states=review_states,
            reviewers=list(registry["reviewers"]),
            protocol_name=self.queue.protocol_name,
        )
        pointer = self.package_root / "restricted/active_audit_protocol.json"
        if pointer.is_file():
            archive = _load_object(active_protocol_paths(self.package_root).archive_manifest_path)
            legacy_counts = archive.get("legacy_event_counts", {})
            calculated["owner"]["data_quality"]["pilot_excluded_records"] = (
                sum(int(value) for value in legacy_counts.values())
                if isinstance(legacy_counts, Mapping)
                else 0
            )
        else:
            calculated["owner"]["data_quality"]["pilot_excluded_records"] = 0
        return calculated

    def shared(self) -> dict[str, Any]:
        before = sha256_file(self.queue.state_path)
        payload = self._calculated()["shared"]
        if sha256_file(self.queue.state_path) != before:
            raise Tier1BlockedError(BLOCKED_LINEAGE, "progress read changed queue state")
        return payload

    def role_status(self, reviewer_code: str, role: str) -> dict[str, Any]:
        code = str(reviewer_code).strip()
        role = str(role).strip().lower()
        self.registry.require_active_qualified(code)
        if role not in {"primary", "secondary"}:
            raise ValueError("review role must be primary or secondary")
        calculated = self._calculated()
        workload = next(
            row for row in calculated["owner"]["reviewer_workload"] if row["reviewer_code"] == code
        )
        if role == "primary":
            remaining = calculated["shared"]["primary"]["not_yet_claimed"]
        else:
            remaining = calculated["shared"]["formal_repeat_reviews"]["not_yet_claimed"]
        return {
            "shared": calculated["shared"],
            "role": role,
            "eligible_remaining": remaining,
            "reviewer_incomplete": workload[f"incomplete_{role}"],
            "reviewer_finalized": workload[f"finalized_{role}"],
            "individual_target_obligation": False,
            "next_study_target_visible": False,
        }

    def owner(self) -> dict[str, Any]:
        return self._calculated()["owner"]


def build_team_progress_configuration(
    package_root: Path,
    *,
    source_commit: str,
    created_at_utc: str | None = None,
) -> dict[str, Any]:
    package_root = require_restricted_destination(package_root)
    paths = active_protocol_paths(package_root)
    assignments = package_root / "restricted/roster/reduced_target_assignments_restricted.csv"
    target_studies, _ = _target_assignment_state(
        assignments,
        list(map(str, _load_object(paths.policy_path)["primary_queue"])),
    )
    return {
        "schema_version": TEAM_PROGRESS_SCHEMA,
        "progress_schema_version": PROGRESS_SCHEMA_VERSION,
        "source_commit": _source_commit(source_commit),
        "created_at_utc": str(created_at_utc or utc_now()),
        "target_goals": {
            LVOT_VTI: LVOT_VTI_TARGET_REVIEW_GOAL,
            TAPSE: TAPSE_TARGET_REVIEW_GOAL,
        },
        "formal_repeat_review_goal": FORMAL_RELIABILITY_N,
        "unique_physical_study_denominator": len(
            target_studies[LVOT_VTI] | target_studies[TAPSE]
        ),
        "cross_target_overlap_count": len(
            target_studies[LVOT_VTI] & target_studies[TAPSE]
        ),
        "target_assignments_sha256": sha256_file(assignments),
        "queue_policy_sha256": sha256_file(paths.policy_path),
        "server_side_source_of_truth": True,
        "secondary_excluded_from_target_totals": True,
        "displayed_defaults_excluded_from_progress": True,
        "target_membership_by_study_reader_visible": False,
    }


def load_team_progress_configuration(
    interface_root: Path,
    *,
    expected_source_commit: str | None = None,
) -> dict[str, Any] | None:
    path = require_restricted_destination(interface_root) / TEAM_PROGRESS_CONFIG_FILENAME
    if not path.is_file():
        return None
    config = _load_object(path)
    if (
        config.get("schema_version") != TEAM_PROGRESS_SCHEMA
        or config.get("progress_schema_version") != PROGRESS_SCHEMA_VERSION
        or config.get("target_goals")
        != {LVOT_VTI: LVOT_VTI_TARGET_REVIEW_GOAL, TAPSE: TAPSE_TARGET_REVIEW_GOAL}
        or config.get("formal_repeat_review_goal") != FORMAL_RELIABILITY_N
        or config.get("server_side_source_of_truth") is not True
        or config.get("secondary_excluded_from_target_totals") is not True
        or config.get("displayed_defaults_excluded_from_progress") is not True
        or config.get("target_membership_by_study_reader_visible") is not False
    ):
        raise ValueError("team progress configuration is invalid")
    if expected_source_commit is not None and config.get("source_commit") != _source_commit(
        expected_source_commit
    ):
        raise Tier1BlockedError(BLOCKED_LINEAGE, "team progress source commit changed")
    return config


def activate_team_progress(
    package_root: Path,
    *,
    source_commit: str,
    backup_certificate_path: Path,
    owner_quiet_window_confirmed: bool,
    created_at_utc: str | None = None,
) -> dict[str, Any]:
    """Activate progress and owner access without altering queue or checkpoints."""

    if owner_quiet_window_confirmed is not True:
        raise PermissionError("OWNER_QUIET_WINDOW_CONFIRMED=TRUE is required")
    package_root = require_restricted_destination(package_root)
    commit = _source_commit(source_commit)
    validate_active_protocol_v3(package_root)
    paths = active_protocol_paths(package_root)
    progress_path = paths.interface_root / TEAM_PROGRESS_CONFIG_FILENAME
    owner_root = paths.protocol_root / "owner"
    owner_config_path = owner_root / OWNER_ACCESS_CONFIG_FILENAME
    owner_secret_path = owner_root / OWNER_SECRET_FILENAME
    if progress_path.exists() or owner_config_path.exists() or owner_secret_path.exists():
        raise FileExistsError("team progress or owner access is already active")
    backup_path = require_restricted_destination(backup_certificate_path)
    backup = _load_object(backup_path)
    before = _review_state_files(package_root)
    counts_before = aggregate_review_state_counts(package_root)
    if (
        backup.get("status") != "CURRENT_REVIEW_PROGRESS_BACKED_UP"
        or backup.get("source_commit") != commit
        or backup.get("live_state_sha256") != sha256_json(before)
        or not Path(str(backup.get("archive_path", ""))).is_file()
        or sha256_file(Path(str(backup["archive_path"]))) != backup.get("archive_sha256")
    ):
        raise Tier1BlockedError(BLOCKED_LINEAGE, "team progress backup does not match live state")
    created = str(created_at_utc or utc_now())
    progress = build_team_progress_configuration(
        package_root,
        source_commit=commit,
        created_at_utc=created,
    )
    secret = secrets.token_urlsafe(24)
    owner_config = build_owner_access_configuration(
        source_commit=commit,
        secret=secret,
        created_at_utc=created,
    )
    _atomic_write_json(progress_path, progress)
    owner_root.mkdir(parents=True, exist_ok=False, mode=0o700)
    _atomic_write_json(owner_config_path, owner_config)
    descriptor = os.open(owner_secret_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(secret + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    after = _review_state_files(package_root)
    counts_after = aggregate_review_state_counts(package_root)
    if before != after or counts_before != counts_after:
        raise Tier1BlockedError(BLOCKED_LINEAGE, "team progress activation changed review state")
    certificate = {
        "schema_version": TEAM_PROGRESS_SCHEMA,
        "status": TEAM_CUMULATIVE_PROGRESS_PRODUCTION_VERIFIED,
        "owner_status": OWNER_VIEW_PRODUCTION_VERIFIED,
        "readiness_status": READY_FOR_TEAM_CUMULATIVE_V3_HUMAN_AUDIT,
        "source_commit": commit,
        "created_at_utc": created,
        "progress_configuration_sha256": sha256_file(progress_path),
        "owner_access_configuration_sha256": sha256_file(owner_config_path),
        "owner_secret_path": str(owner_secret_path),
        "backup_sha256": backup["archive_sha256"],
        "review_state_sha256_before": sha256_json(before),
        "review_state_sha256_after": sha256_json(after),
        "aggregate_counts_before": counts_before,
        "aggregate_counts_after": counts_after,
        "existing_records_modified": False,
        "pre_cutover_records_defaulted": False,
        "owner_secret_emitted": False,
    }
    certificate_path = package_root / "aggregate_safe/team_progress_activation_certificate.json"
    _atomic_write_json(certificate_path, certificate)
    return {**certificate, "certificate_path": str(certificate_path)}


def metadata_only_team_progress_dry_run(
    package_root: Path,
    *,
    source_commit: str,
) -> dict[str, Any]:
    """Exercise counting and owner authentication without reading production annotations."""

    package_root = require_restricted_destination(package_root)
    commit = _source_commit(source_commit)
    validate_active_protocol_v3(package_root)
    paths = active_protocol_paths(package_root)
    policy = _load_object(paths.policy_path)
    assignments = package_root / "restricted/roster/reduced_target_assignments_restricted.csv"
    production_target_studies, _ = _target_assignment_state(
        assignments,
        list(map(str, policy["primary_queue"])),
    )
    if any(len(production_target_studies[target]) != 15 for target in (LVOT_VTI, TAPSE)):
        raise Tier1BlockedError(BLOCKED_LINEAGE, "production target denominators changed")
    synthetic_lvot = {"DRY-CROSS", *(f"DRY-LVOT-{index:02d}" for index in range(1, 15))}
    synthetic_tapse = {"DRY-CROSS", *(f"DRY-TAPSE-{index:02d}" for index in range(1, 15))}
    synthetic_primary = sorted(synthetic_lvot | synthetic_tapse)
    synthetic_formal = synthetic_primary[:FORMAL_RELIABILITY_N]
    synthetic_tiers = {
        audit_id: TIER_A if index < 7 else TIER_C
        for index, audit_id in enumerate(synthetic_primary)
    }
    lvot_only = sorted(synthetic_lvot - synthetic_tapse)
    tapse_only = sorted(synthetic_tapse - synthetic_lvot)
    overlap = sorted(synthetic_lvot & synthetic_tapse)
    incomplete_id = lvot_only[1]
    formal_id = synthetic_formal[0]
    events = [
        {
            "event_id": "DRY-PRI-LVOT",
            "physical_study_token": lvot_only[0],
            "reviewer_code": "DRYREVIEWER1",
            "role": "primary",
            "protocol_name": PROTOCOL_V3_NAME,
            "status": "locked",
            "locked_at_utc": "2026-09-09T10:00:00Z",
        },
        {
            "event_id": "DRY-PRI-TAPSE",
            "physical_study_token": tapse_only[0],
            "reviewer_code": "DRYREVIEWER2",
            "role": "primary",
            "protocol_name": PROTOCOL_V3_NAME,
            "status": "locked",
            "locked_at_utc": "2026-09-09T10:05:00Z",
        },
        {
            "event_id": "DRY-PRI-CROSS",
            "physical_study_token": overlap[0],
            "reviewer_code": "DRYREVIEWER3",
            "role": "primary",
            "protocol_name": PROTOCOL_V3_NAME,
            "status": "locked",
            "locked_at_utc": "2026-09-09T10:10:00Z",
        },
        {
            "event_id": "DRY-PRI-INCOMPLETE",
            "physical_study_token": incomplete_id,
            "reviewer_code": "DRYREVIEWER1",
            "role": "primary",
            "protocol_name": PROTOCOL_V3_NAME,
            "status": "in_progress",
            "claim_timestamp_utc": "2026-09-10T10:00:00Z",
        },
        {
            "event_id": "DRY-SEC-FORMAL",
            "physical_study_token": formal_id,
            "reviewer_code": "DRYREVIEWER2",
            "role": "secondary",
            "formal_reliability": True,
            "supplemental_review": False,
            "protocol_name": PROTOCOL_V3_NAME,
            "status": "locked",
            "locked_at_utc": "2026-09-09T10:15:00Z",
        },
        {
            "event_id": "DRY-PILOT-EXCLUDED",
            "physical_study_token": incomplete_id,
            "reviewer_code": "DRYREVIEWER3",
            "role": "secondary",
            "formal_reliability": False,
            "supplemental_review": False,
            "protocol_name": PROTOCOL_V3_NAME,
            "status": "archived_incomplete",
        },
    ]
    review_states = {
        event["event_id"]: {
            "workflow_state": (
                "FINALIZED_LOCKED"
                if event["status"] == "locked"
                else "CLAIMED_INCOMPLETE"
                if event["status"] == "in_progress"
                else "ARCHIVED_INCOMPLETE"
            ),
            "completion_validation_passed": event["status"] == "locked",
            "required_clip_count": 2,
            "completed_clip_count": 0 if event["status"] == "in_progress" else 2,
        }
        for event in events
    }
    reviewers = [
        {"reviewer_code": f"DRYREVIEWER{index}", "active": True}
        for index in range(1, 4)
    ]
    before = _review_state_files(package_root)
    counts_before = aggregate_review_state_counts(package_root)
    result = calculate_team_progress(
        primary_queue=synthetic_primary,
        formal_queue=synthetic_formal,
        target_studies={LVOT_VTI: synthetic_lvot, TAPSE: synthetic_tapse},
        tier_by_study=synthetic_tiers,
        events=events,
        review_states=review_states,
        reviewers=reviewers,
        now_utc="2026-09-10T12:00:00Z",
    )
    shared = result["shared"]
    if (
        shared["target_progress"][LVOT_VTI]["finalized"] != 2
        or shared["target_progress"][TAPSE]["finalized"] != 2
        or shared["physical_studies"]["finalized"] != 3
        or shared["formal_repeat_reviews"]["finalized"] != 1
        or shared["primary"]["in_progress"] != 1
    ):
        raise Tier1BlockedError(BLOCKED_LINEAGE, "team progress dry-run counts failed")
    with tempfile.TemporaryDirectory(prefix="team-progress-owner-dry-run-", dir=paths.protocol_root) as temp:
        config_path = Path(temp) / OWNER_ACCESS_CONFIG_FILENAME
        secret = "synthetic-owner-secret-for-dry-run"
        _atomic_write_json(
            config_path,
            build_owner_access_configuration(source_commit=commit, secret=secret),
        )
        owner_access = OwnerAccessControl(config_path)
        if owner_access.verify("wrong-owner-secret") or not owner_access.verify(secret):
            raise Tier1BlockedError(BLOCKED_LINEAGE, "owner authentication dry run failed")
    after = _review_state_files(package_root)
    counts_after = aggregate_review_state_counts(package_root)
    if before != after or counts_before != counts_after:
        raise Tier1BlockedError(BLOCKED_LINEAGE, "team progress dry run changed production state")
    return {
        "status": TEAM_CUMULATIVE_PROGRESS_DRY_RUN_PASS,
        "owner_status": OWNER_VIEW_DRY_RUN_PASS,
        "source_commit": commit,
        "synthetic_lvot_finalized": 2,
        "synthetic_tapse_finalized": 2,
        "synthetic_unique_physical_finalized": 3,
        "synthetic_formal_secondary_finalized": 1,
        "cross_target_counted_once_physically": True,
        "cross_target_counted_once_per_target": True,
        "secondary_excluded_from_target_totals": True,
        "pilot_excluded_from_totals": True,
        "owner_authentication_separate": True,
        "ordinary_reviewer_code_grants_owner_access": False,
        "production_state_sha256_before": sha256_json(before),
        "production_state_sha256_after": sha256_json(after),
        "aggregate_counts_before": counts_before,
        "aggregate_counts_after": counts_after,
        "production_annotation_content_read": False,
        "production_annotation_content_emitted": False,
        "identifiers_emitted": False,
    }
