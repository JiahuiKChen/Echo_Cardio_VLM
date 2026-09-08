from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from jdim_tier1.audit import STUDY_OUTCOMES  # noqa: E402
from jdim_tier1.audit_interface import CLIP_PRESENCE_FIELDS  # noqa: E402
from jdim_tier1.audit_protocol_v3 import (  # noqa: E402
    BASE_SCORING_SCOPE_BY_TIER,
    FIELD_DEFINITIONS,
    PROTOCOL_ARCHIVE_STATUS,
    PROTOCOL_V3_BASE_COMMIT,
    PROTOCOL_V3_NAME,
    SCORING_SCOPE_BY_TIER,
    SOURCE_ONLY_FIELD_DEFINITIONS,
    V3_ACTIVE,
    V3_CLIP_PRESENCE_FIELDS,
    V3_REQUIRED_CLIP_ANNOTATION_FIELDS,
    V3_REQUIRED_STUDY_ANNOTATION_FIELDS,
    V3_SOURCE_ONLY_CATEGORY_FIELDS,
    V3_SOURCE_ONLY_PRIMARY_FIELD,
    V3_SOURCE_ONLY_STUDY_OUTCOMES,
    V3_STUDY_OUTCOMES,
    aggregation_eligible_events_from_state,
    apply_side_by_side_addendum,
    apply_protocol_v3_transition,
    derive_study_summary,
    plan_protocol_v3_transition,
    rollup_presence,
    validate_active_protocol_v3,
)
from jdim_tier1.reduced_audit import (  # noqa: E402
    BLOCKED_PILOT_ANNOTATION_ISOLATION,
    FORMAL_RELIABILITY_N,
    LOCKED_PARENT_SHA256,
    LVOT_VTI,
    PILOT_STATUS,
    PROTOCOL_STATUS,
    TAPSE,
    TARGET_ASSIGNMENT_N,
    TIER_A,
    TIER_C,
    ParentAuditPaths,
    build_reduced_audit_result,
    classify_pilot_checkpoint,
    select_formal_reliability_subset,
    validate_locked_reduced_roster,
    write_reduced_audit_roster,
)
from jdim_tier1.reduced_audit_interface import (  # noqa: E402
    ACTION_CLAIM_NEXT,
    ACTION_RESUME,
    ARCHIVED_INCOMPLETE,
    BLOCKED_MIXED_REVIEWER_ATTRIBUTION,
    BLOCKED_READER_ROLE_INDEPENDENCE,
    CLAIMED_INCOMPLETE,
    COMPLETE_NOT_FINALIZED,
    FINALIZATION_REQUIRED,
    FINALIZED_LOCKED,
    INCOMPLETE_STUDY_EXISTS,
    NO_ELIGIBLE_STUDY,
    NO_INCOMPLETE_STUDY,
    QUEUE_TRANSITION_DRY_RUN_PASS,
    REASSIGNED_FRESH,
    ROLE_AWARE_INTERFACE_READY,
    ROLE_PRIMARY,
    ROLE_SECONDARY,
    STUDY_READY,
    ReviewerRegistry,
    RoleAwareAuditService,
    RoleAwareCheckpointStore,
    RoleQueueStore,
    audit_queue_transition_state,
    build_role_aware_interface_package,
    current_role_aware_interface_assets,
    validate_role_aware_interface_package,
    write_interface_ready_certificate,
)
from jdim_tier1.safety import Tier1BlockedError, sha256_file  # noqa: E402


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


class ParentFixture:
    def __init__(self, root: Path):
        self.root = root
        locked = root / "parent/locked"
        interface = root / "parent/interface"
        aggregate = root / "parent/aggregate"
        media = root / "parent/media"
        checkpoint = root / "parent/checkpoints/primary.reader1/checkpoint.json"
        for path in (locked, interface, aggregate, media, checkpoint.parent):
            path.mkdir(parents=True, exist_ok=True)

        linkage_rows = []
        technical_rows = []
        clip_rows = []
        for index in range(116):
            audit_id = f"A{index:03d}"
            clip_id = f"C{index:03d}"
            targets = []
            if index < 60:
                targets.append(LVOT_VTI)
            if index >= 56:
                targets.append(TAPSE)
            tier = TIER_A if index < 7 else TIER_C
            linkage_rows.append(
                {
                    "audit_id": audit_id,
                    "study_id": f"S{index:03d}",
                    "subject_id": f"P{index:03d}",
                    "target_membership": ";".join(targets),
                    "target_strata": ";".join(f"{target}:train" for target in targets),
                    "review_order": index + 1,
                }
            )
            source_token = f"SRC{index:03d}"
            model_token = f"MOD{index:03d}" if tier == TIER_A else ""
            (media / f"{source_token}.png").write_bytes(b"source")
            if model_token:
                (media / f"{model_token}.png").write_bytes(b"model")
            technical_rows.append(
                {
                    "audit_id": audit_id,
                    "clip_audit_id": clip_id,
                    "evidence_tier": tier,
                    "source_media_id": source_token,
                    "model_input_media_id": model_token,
                    "model_input_verified": tier == TIER_A,
                    "source_only": tier == TIER_C,
                }
            )
            clip_rows.append(
                {
                    "audit_id": audit_id,
                    "clip_audit_id": clip_id,
                    "study_id": f"S{index:03d}",
                    "subject_id": f"P{index:03d}",
                    "canonical_clip_id": f"K{index:03d}",
                }
            )
        self.linkage = pd.DataFrame(linkage_rows)
        self.technical = pd.DataFrame(technical_rows)
        self.clips = pd.DataFrame(clip_rows)
        self.linkage_path = locked / "audit_linkage.csv"
        self.clips_path = locked / "canonical_clip_roster_restricted.csv"
        self.primary_path = locked / "reader_manifest.csv"
        self.secondary_path = locked / "second_reader_manifest.csv"
        self.technical_path = root / "parent/technical_interface_manifest_restricted.csv"
        self.linkage.to_csv(self.linkage_path, index=False)
        self.clips.to_csv(self.clips_path, index=False)
        self.linkage[["audit_id", "review_order"]].to_csv(self.primary_path, index=False)
        self.linkage.iloc[:24][["audit_id", "review_order"]].to_csv(self.secondary_path, index=False)
        self.technical.to_csv(self.technical_path, index=False)

        self.lock_path = aggregate / "audit_roster_lock.json"
        self.technical_lock_path = aggregate / "technical_lock_certificate.json"
        self.primary_interface_path = interface / "primary_reader_manifest.json"
        self.secondary_interface_path = interface / "second_reader_manifest.json"
        self.interface_policy_path = interface / "interface_policy.json"
        write_json(self.lock_path, {"status": "AUDIT_ROSTER_LOCKED"})
        write_json(self.technical_lock_path, {"status": "AUDIT_INPUTS_TECHNICALLY_LOCKED"})
        write_json(self.primary_interface_path, {"studies": 116})
        write_json(self.secondary_interface_path, {"studies": 24})
        write_json(self.interface_policy_path, {"status": "AUDIT_INTERFACE_READY"})
        self.ready_path = aggregate / "phase2jr_ready_certificate.json"
        write_json(
            self.ready_path,
            {
                "status": "READY_FOR_BLINDED_HUMAN_AUDIT",
                "source_commit": "4e0ec3248b88ed8286de3a91b589627bb2da09b6",
                "technical_lock_status": "AUDIT_INPUTS_TECHNICALLY_LOCKED",
                "primary_studies": 116,
                "clips": 5071,
                "second_reader_studies": 24,
                "technical_manifest_sha256": sha256_file(self.technical_path),
                "primary_package_sha256": sha256_file(self.primary_interface_path),
                "second_reader_package_sha256": sha256_file(self.secondary_interface_path),
                "interface_policy_sha256": sha256_file(self.interface_policy_path),
                "ocr_used": False,
                "clinical_annotations_generated": False,
                "public_network_binding_required": False,
            },
        )
        write_json(
            checkpoint,
            {
                "reader_id": "primary.reader1",
                "annotations": {
                    "studies": {
                        "A050": {"reader_confidence": "moderate"},
                        "A051": {"reader_confidence": ""},
                    },
                    "clips": {
                        "C050": {"acquisition_content_type": "2d_b_mode"},
                        "C051": {"acquisition_content_type": ""},
                    },
                },
            },
        )
        self.checkpoint_path = checkpoint
        self.paths = ParentAuditPaths(
            audit_roster_lock=self.lock_path,
            audit_linkage=self.linkage_path,
            canonical_clip_roster=self.clips_path,
            parent_reader_manifest=self.primary_path,
            parent_second_reader_manifest=self.secondary_path,
            ready_certificate=self.ready_path,
            technical_lock_certificate=self.technical_lock_path,
            technical_interface_manifest=self.technical_path,
            parent_primary_interface_manifest=self.primary_interface_path,
            parent_secondary_interface_manifest=self.secondary_interface_path,
            parent_interface_policy=self.interface_policy_path,
            parent_media_root=media,
            pilot_checkpoint=self.checkpoint_path,
        )
        self.hashes = {
            role: sha256_file(getattr(self.paths, role)) for role in LOCKED_PARENT_SHA256
        }

    def build_result(self):
        with patch.dict(LOCKED_PARENT_SHA256, self.hashes, clear=True):
            return build_reduced_audit_result(self.paths)

    def build_package(self, output: Path):
        result = self.build_result()
        write_reduced_audit_roster(
            result,
            output,
            source_commit="f" * 40,
            created_at="2026-09-01T12:00:00Z",
        )
        build_role_aware_interface_package(
            reduced_output_root=output,
            parent_media_root=self.paths.parent_media_root,
        )
        validation = validate_role_aware_interface_package(output)
        write_interface_ready_certificate(output, validation)
        return result


def complete_payload(study: dict[str, object]) -> dict[str, object]:
    study_record = {field: "not_assessable" for field in STUDY_OUTCOMES}
    study_record["reader_confidence"] = "not_assessable"
    clips = {}
    for clip in study["clips"]:
        record = {field: "not_assessable" for field in CLIP_PRESENCE_FIELDS}
        record["acquisition_content_type"] = "not_assessable"
        record["reader_confidence"] = "not_assessable"
        clips[clip["clip_audit_id"]] = record
    return {"annotations": {"studies": {study["audit_id"]: study_record}, "clips": clips}}


def complete_v3_payload(study: dict[str, object]) -> dict[str, object]:
    clips = {}
    clip_tiers = {}
    for clip in study["clips"]:
        record = {field: "not_assessable" for field in V3_CLIP_PRESENCE_FIELDS}
        record["acquisition_content_type"] = "not_assessable"
        record["reader_confidence"] = "not_assessable"
        if clip["evidence_tier"] == TIER_A:
            record[V3_SOURCE_ONLY_PRIMARY_FIELD] = "not_assessable"
        clips[clip["clip_audit_id"]] = record
        clip_tiers[clip["clip_audit_id"]] = clip["evidence_tier"]
    study_record = derive_study_summary(clips, clip_evidence_tiers=clip_tiers)
    study_record["reader_confidence"] = "not_assessable"
    study_record["derived_summary_confirmed"] = "yes"
    return {
        "annotations": {
            "studies": {study["audit_id"]: study_record},
            "clips": clips,
        }
    }


class ReducedAuditSamplingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.fixture = ParentFixture(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_exact_target_and_tier_assignments(self) -> None:
        result = self.fixture.build_result()
        counts = result.assignments.groupby("target").size().to_dict()
        self.assertEqual(counts, {LVOT_VTI: TARGET_ASSIGNMENT_N, TAPSE: TARGET_ASSIGNMENT_N})
        self.assertEqual(
            len(result.assignments[(result.assignments.target == LVOT_VTI) & (result.assignments.evidence_tier == TIER_A)]),
            7,
        )
        self.assertEqual(
            len(result.assignments[(result.assignments.target == LVOT_VTI) & (result.assignments.evidence_tier == TIER_C)]),
            8,
        )
        self.assertEqual(
            len(result.assignments[(result.assignments.target == TAPSE) & (result.assignments.evidence_tier == TIER_C)]),
            15,
        )

    def test_pilot_is_structurally_identified_force_included_and_not_reused(self) -> None:
        result = self.fixture.build_result()
        self.assertEqual(result.pilot.substantive_studies, 1)
        self.assertEqual(result.pilot.structurally_empty_records, 1)
        self.assertEqual(result.pilot.aggregate_safe()["status"], PILOT_STATUS)
        self.assertIn(result.pilot.pilot_audit_id, set(result.assignments.audit_id))
        self.assertTrue(result.replacement_records)
        self.assertFalse(result.summary["pilot_prior_responses_reused"])

    def test_pilot_ambiguity_fails_closed(self) -> None:
        payload = json.loads(self.fixture.checkpoint_path.read_text(encoding="utf-8"))
        payload["annotations"]["studies"]["A051"]["reader_confidence"] = "high"
        write_json(self.fixture.checkpoint_path, payload)
        with self.assertRaises(Tier1BlockedError) as caught:
            classify_pilot_checkpoint(self.fixture.checkpoint_path, self.fixture.technical)
        self.assertEqual(caught.exception.status, BLOCKED_PILOT_ANNOTATION_ISOLATION)

    def test_selection_is_deterministic_and_does_not_mutate_parent(self) -> None:
        before = {role: sha256_file(getattr(self.fixture.paths, role)) for role in LOCKED_PARENT_SHA256}
        first = self.fixture.build_result()
        second = self.fixture.build_result()
        pd.testing.assert_frame_equal(first.assignments, second.assignments)
        after = {role: sha256_file(getattr(self.fixture.paths, role)) for role in LOCKED_PARENT_SHA256}
        self.assertEqual(before, after)

    def test_cross_target_studies_count_twice_but_are_reviewed_once(self) -> None:
        result = self.fixture.build_result()
        assignment_total = len(result.assignments)
        unique_total = result.assignments.audit_id.nunique()
        self.assertEqual(assignment_total, 30)
        self.assertEqual(assignment_total - unique_total, result.summary["cross_target_overlap_studies"])

    def test_every_selected_study_contains_all_canonical_clips(self) -> None:
        result = self.fixture.build_result()
        self.assertEqual(set(result.linkage.audit_id), set(result.clip_roster.audit_id))
        self.assertFalse(result.clip_roster[["audit_id", "clip_audit_id"]].duplicated().any())

    def test_formal_reliability_subset_is_fixed_deterministic_and_unassigned(self) -> None:
        result = self.fixture.build_result()
        formal = result.formal_reliability
        self.assertEqual(len(formal), FORMAL_RELIABILITY_N)
        self.assertEqual(formal.audit_id.nunique(), FORMAL_RELIABILITY_N)
        self.assertNotIn("reviewer_code", formal.columns)
        again = select_formal_reliability_subset(
            result.linkage,
            pd.read_csv(self.fixture.secondary_path),
        )
        pd.testing.assert_frame_equal(formal, again)

    def test_formal_subset_prefers_locked_parent_then_supplements(self) -> None:
        result = self.fixture.build_result()
        selected = result.linkage.copy()
        preferred = list(selected.audit_id.head(3))
        outside = [
            audit_id
            for audit_id in self.fixture.linkage.audit_id
            if audit_id not in set(selected.audit_id)
        ][:21]
        parent_second = pd.DataFrame({"audit_id": [*preferred, *outside]})
        formal = select_formal_reliability_subset(selected, parent_second)
        self.assertEqual(len(formal), 8)
        self.assertTrue(set(preferred).issubset(set(formal.audit_id)))
        self.assertEqual(int(formal.in_parent_second_reader_subset.sum()), 3)

    def test_locked_roster_round_trip_and_hash_validation(self) -> None:
        output = self.root / "reduced"
        result = self.fixture.build_result()
        certificate = write_reduced_audit_roster(
            result,
            output,
            source_commit="f" * 40,
            created_at="2026-09-01T12:00:00Z",
        )
        self.assertEqual(certificate["status"], PROTOCOL_STATUS)
        self.assertEqual(validate_locked_reduced_roster(output)["status"], PROTOCOL_STATUS)
        path = output / "restricted/roster/reduced_target_assignments_restricted.csv"
        path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        with self.assertRaises(Tier1BlockedError):
            validate_locked_reduced_roster(output)


class RoleAwareQueueTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.fixture = ParentFixture(self.root)
        self.output = self.root / "reduced"
        self.fixture.build_package(self.output)
        interface = self.output / "restricted/interface"
        self.manifest = json.loads((interface / "study_manifest_restricted.json").read_text(encoding="utf-8"))
        self.registry = ReviewerRegistry(self.output / "restricted/reviewer_registry")
        for code in ("readerA", "readerB", "readerC"):
            self.registry.register(code, qualified=True)
        self.queue = RoleQueueStore(
            self.output / "restricted/queue",
            interface / "queue_policy_restricted.json",
            self.registry,
        )
        self.checkpoints = RoleAwareCheckpointStore(
            self.output / "restricted/checkpoints",
            self.queue,
            self.manifest,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def claim(self, code: str, role: str, action: str = ACTION_CLAIM_NEXT):
        return self.queue.claim(
            reviewer_code=code,
            role=role,
            action=action,
            qualification_confirmed=True,
        )

    def study_for(self, event: dict[str, object]) -> dict[str, object]:
        return next(
            study
            for study in self.manifest["studies"]
            if study["audit_id"] == event["physical_study_token"]
        )

    def test_reviewer_code_role_and_qualification_are_required(self) -> None:
        with self.assertRaises((ValueError, PermissionError)):
            self.queue.claim(reviewer_code="", role=ROLE_PRIMARY, action=ACTION_CLAIM_NEXT, qualification_confirmed=True)
        with self.assertRaises(ValueError):
            self.queue.claim(reviewer_code="readerA", role="", action=ACTION_CLAIM_NEXT, qualification_confirmed=True)
        with self.assertRaises(PermissionError):
            self.queue.claim(reviewer_code="readerA", role=ROLE_PRIMARY, action=ACTION_CLAIM_NEXT, qualification_confirmed=False)

    def test_primary_claim_is_exclusive_and_resume_is_same_reviewer(self) -> None:
        first = self.claim("readerA", ROLE_PRIMARY)
        resumed = self.claim("readerA", ROLE_PRIMARY, ACTION_RESUME)
        second = self.claim("readerB", ROLE_PRIMARY)
        self.assertEqual(first["event_id"], resumed["event_id"])
        self.assertNotEqual(first["physical_study_token"], second["physical_study_token"])
        with self.assertRaises(FileNotFoundError):
            self.claim("readerC", ROLE_PRIMARY, ACTION_RESUME)

    def test_one_reviewer_cannot_hold_both_roles_on_one_study(self) -> None:
        primary = self.claim("readerA", ROLE_PRIMARY)
        secondary = self.claim("readerA", ROLE_SECONDARY)
        self.assertNotEqual(primary["physical_study_token"], secondary["physical_study_token"])

    def test_different_reviewers_may_claim_different_roles_concurrently(self) -> None:
        primary = self.claim("readerA", ROLE_PRIMARY)
        secondary = self.claim("readerB", ROLE_SECONDARY)
        self.assertEqual(primary["status"], "in_progress")
        self.assertEqual(secondary["status"], "in_progress")

    def test_secondary_queue_prioritizes_hidden_formal_subset(self) -> None:
        formal_ids = set(self.queue.policy["formal_reliability_queue"])
        event = self.claim("readerA", ROLE_SECONDARY)
        self.assertIn(event["physical_study_token"], formal_ids)
        self.assertTrue(event["formal_reliability"])
        self.assertFalse(event["supplemental_review"])

    def test_supplemental_secondary_opens_only_after_all_formal_slots_lock(self) -> None:
        formal_ids = set(self.queue.policy["formal_reliability_queue"])
        for _ in range(FORMAL_RELIABILITY_N):
            event = self.claim("readerA", ROLE_SECONDARY)
            self.assertIn(event["physical_study_token"], formal_ids)
            study = self.study_for(event)
            self.checkpoints.save(event["event_id"], complete_payload(study))
            self.checkpoints.lock(event["event_id"])
        supplemental = self.claim("readerA", ROLE_SECONDARY)
        self.assertNotIn(supplemental["physical_study_token"], formal_ids)
        self.assertTrue(supplemental["supplemental_review"])
        self.assertEqual(supplemental["secondary_review_class"], "SUPPLEMENTAL_DUPLICATE_REVIEW")

    def test_role_is_immutable_after_first_save(self) -> None:
        event = self.claim("readerA", ROLE_PRIMARY)
        study = self.study_for(event)
        self.checkpoints.save(event["event_id"], complete_payload(study))
        checkpoint = self.checkpoints.load(event["event_id"])
        checkpoint["role"] = ROLE_SECONDARY
        path = self.output / f"restricted/checkpoints/{event['event_id']}/checkpoint.json"
        write_json(path, checkpoint)
        with self.assertRaises(Tier1BlockedError) as caught:
            self.checkpoints.load(event["event_id"])
        self.assertEqual(caught.exception.status, BLOCKED_READER_ROLE_INDEPENDENCE)

    def test_checkpoint_rejects_annotations_outside_claimed_study(self) -> None:
        event = self.claim("readerA", ROLE_PRIMARY)
        payload = complete_payload(self.study_for(event))
        payload["annotations"]["studies"]["A999"] = {"reader_confidence": "high"}
        with self.assertRaises(Tier1BlockedError):
            self.checkpoints.save(event["event_id"], payload)

    def test_complete_study_locks_with_stable_checksum(self) -> None:
        event = self.claim("readerA", ROLE_PRIMARY)
        study = self.study_for(event)
        self.checkpoints.save(event["event_id"], complete_payload(study))
        lock_path = self.checkpoints.lock(event["event_id"])
        first = json.loads(lock_path.read_text(encoding="utf-8"))["annotation_checksum"]
        loaded = self.checkpoints.load(event["event_id"])
        self.assertTrue(loaded["locked"])
        self.assertEqual(first, self.queue.get_event(event["event_id"])["annotation_checksum"])
        with self.assertRaises(PermissionError):
            self.checkpoints.save(event["event_id"], complete_payload(study))

    def test_positive_or_uncertain_details_are_required_before_lock(self) -> None:
        event = self.claim("readerA", ROLE_PRIMARY)
        study = self.study_for(event)
        payload = complete_payload(study)
        payload["annotations"]["studies"][study["audit_id"]]["spectral_doppler_present"] = "uncertain"
        self.checkpoints.save(event["event_id"], payload)
        with self.assertRaises(ValueError):
            self.checkpoints.lock(event["event_id"])

    def test_reassignment_archives_incomplete_record_and_allows_fresh_claim(self) -> None:
        event = self.claim("readerA", ROLE_PRIMARY)
        result = self.checkpoints.archive_for_reassignment(
            event["event_id"],
            owner_confirmed=True,
            reason="reader unavailable",
        )
        self.assertEqual(result["event"]["status"], "archived_incomplete")
        replacement = self.claim("readerB", ROLE_PRIMARY)
        self.assertEqual(replacement["physical_study_token"], event["physical_study_token"])
        self.assertEqual(self.checkpoints.load(replacement["event_id"])["annotations"]["clips"], {})

    def test_reassignment_requires_owner_confirmation(self) -> None:
        event = self.claim("readerA", ROLE_PRIMARY)
        with self.assertRaises(PermissionError):
            self.checkpoints.archive_for_reassignment(
                event["event_id"],
                owner_confirmed=False,
                reason="reader unavailable",
            )

    def test_reviewer_registry_is_separate_and_inactive_codes_fail(self) -> None:
        self.registry.set_active("readerC", False)
        with self.assertRaises(PermissionError):
            self.claim("readerC", ROLE_PRIMARY)
        queue_text = (self.output / "restricted/queue/queue_state.json").read_text(encoding="utf-8")
        self.assertNotIn("owner_confirmed_qualified", queue_text)


class QueueTransitionRepairTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.fixture = ParentFixture(self.root)
        self.output = self.root / "reduced"
        self.fixture.build_package(self.output)
        self.service = RoleAwareAuditService(self.output, self.fixture.paths.parent_media_root)
        for code in ("queueR1", "queueR2", "queueR3"):
            self.service.registry.register(code, qualified=True)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def claim(self, code: str, role: str, action: str = ACTION_CLAIM_NEXT) -> dict[str, object]:
        return self.service.claim(
            {
                "reviewer_code": code,
                "role": role,
                "action": action,
                "qualification_confirmed": True,
            }
        )

    def save_complete(self, response: dict[str, object]) -> None:
        payload = complete_payload(response["study"])
        self.service.save(response["session_token"], payload["annotations"])

    def finalize(self, response: dict[str, object]) -> None:
        self.save_complete(response)
        result = self.service.lock(response["session_token"])
        self.assertEqual(result["status"], FINALIZED_LOCKED)

    def queue_events(self) -> list[dict[str, object]]:
        path = self.output / "restricted/queue/queue_state.json"
        return json.loads(path.read_text(encoding="utf-8"))["events"]

    def test_01_primary_claim_creates_one_incomplete_review(self) -> None:
        response = self.claim("queueR1", ROLE_PRIMARY)
        self.assertEqual(response["status"], STUDY_READY)
        self.assertEqual(response["workflow_state"], CLAIMED_INCOMPLETE)
        self.assertEqual(len(self.queue_events()), 1)

    def test_02_resume_restores_the_same_incomplete_review(self) -> None:
        first = self.claim("queueR1", ROLE_PRIMARY)
        self.service.save(first["session_token"], {"studies": {}, "clips": {}})
        self.service.end_session(first["session_token"])
        resumed = self.claim("queueR1", ROLE_PRIMARY, ACTION_RESUME)
        self.assertEqual(first["event"]["event_id"], resumed["event"]["event_id"])

    def test_03_incomplete_review_prevents_silent_next_claim(self) -> None:
        self.claim("queueR1", ROLE_PRIMARY)
        blocked = self.claim("queueR1", ROLE_PRIMARY)
        self.assertFalse(blocked["open_study"])
        self.assertEqual(len(self.queue_events()), 1)

    def test_04_incomplete_next_claim_returns_explicit_state(self) -> None:
        self.claim("queueR1", ROLE_PRIMARY)
        blocked = self.claim("queueR1", ROLE_PRIMARY)
        self.assertEqual(blocked["status"], INCOMPLETE_STUDY_EXISTS)
        self.assertIn("Resume incomplete study", blocked["message"])

    def test_05_complete_unfinalized_review_requires_finalization(self) -> None:
        first = self.claim("queueR1", ROLE_PRIMARY)
        self.save_complete(first)
        self.service.end_session(first["session_token"])
        blocked = self.claim("queueR1", ROLE_PRIMARY)
        self.assertEqual(blocked["status"], FINALIZATION_REQUIRED)
        self.assertEqual(blocked["workflow_state"], COMPLETE_NOT_FINALIZED)
        resumed = self.claim("queueR1", ROLE_PRIMARY, ACTION_RESUME)
        self.assertTrue(resumed["open_study"])
        self.assertEqual(resumed["status"], FINALIZATION_REQUIRED)

    def test_06_finalization_clears_active_claim_and_session(self) -> None:
        response = self.claim("queueR1", ROLE_PRIMARY)
        token = response["session_token"]
        self.finalize(response)
        event = self.service.queue.get_event(response["event"]["event_id"])
        self.assertEqual(event["status"], "locked")
        self.assertTrue(event["completed_at_utc"])
        self.assertTrue(event["locked_at_utc"])
        with self.assertRaises(PermissionError):
            self.service.session(token)

    def test_07_next_claim_after_finalization_assigns_a_different_study(self) -> None:
        first = self.claim("queueR1", ROLE_PRIMARY)
        first_study = first["study"]["audit_id"]
        self.finalize(first)
        second = self.claim("queueR1", ROLE_PRIMARY)
        self.assertNotEqual(first_study, second["study"]["audit_id"])

    def test_08_resume_after_finalization_reports_no_incomplete_review(self) -> None:
        first = self.claim("queueR1", ROLE_PRIMARY)
        self.finalize(first)
        response = self.claim("queueR1", ROLE_PRIMARY, ACTION_RESUME)
        self.assertEqual(response["status"], NO_INCOMPLETE_STUDY)
        self.assertFalse(response["open_study"])

    def test_09_finalized_review_never_reopens_as_editable(self) -> None:
        first = self.claim("queueR1", ROLE_PRIMARY)
        self.finalize(first)
        with self.assertRaises(PermissionError):
            self.service.checkpoints.save(
                first["event"]["event_id"], complete_payload(first["study"])
            )

    def test_10_one_reviewer_can_complete_several_primary_studies(self) -> None:
        studies = []
        for _ in range(3):
            response = self.claim("queueR1", ROLE_PRIMARY)
            studies.append(response["study"]["audit_id"])
            self.finalize(response)
        self.assertEqual(len(studies), len(set(studies)))

    def test_11_two_primary_reviewers_receive_different_studies(self) -> None:
        first = self.claim("queueR1", ROLE_PRIMARY)
        second = self.claim("queueR2", ROLE_PRIMARY)
        self.assertNotEqual(first["study"]["audit_id"], second["study"]["audit_id"])

    def test_12_same_reviewer_cannot_receive_same_study_cross_role(self) -> None:
        primary = self.claim("queueR1", ROLE_PRIMARY)
        secondary = self.claim("queueR1", ROLE_SECONDARY)
        self.assertNotEqual(primary["study"]["audit_id"], secondary["study"]["audit_id"])

    def test_13_other_reviewer_may_receive_eligible_cross_role_study(self) -> None:
        formal_first = self.service.queue.policy["formal_reliability_queue"][0]
        primary = None
        while primary is None or primary["study"]["audit_id"] != formal_first:
            primary = self.claim("queueR1", ROLE_PRIMARY)
            self.finalize(primary)
        secondary = self.claim("queueR2", ROLE_SECONDARY)
        self.assertEqual(secondary["study"]["audit_id"], formal_first)

    def test_14_formal_secondary_queue_is_unchanged(self) -> None:
        policy_path = self.output / "restricted/interface/queue_policy_restricted.json"
        before = sha256_file(policy_path)
        self.claim("queueR1", ROLE_PRIMARY)
        self.claim("queueR2", ROLE_SECONDARY)
        self.assertEqual(sha256_file(policy_path), before)

    def test_15_same_role_double_click_creates_only_one_claim(self) -> None:
        responses: list[dict[str, object]] = []
        errors: list[BaseException] = []

        def worker() -> None:
            try:
                responses.append(self.claim("queueR1", ROLE_PRIMARY))
            except BaseException as exc:  # pragma: no cover - assertion captures failures
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertFalse(errors)
        self.assertEqual(len(self.queue_events()), 1)
        self.assertEqual(
            {response["status"] for response in responses},
            {STUDY_READY, INCOMPLETE_STUDY_EXISTS},
        )

    def test_16_concurrent_primary_claims_cannot_assign_same_study(self) -> None:
        responses: list[dict[str, object]] = []

        def worker(code: str) -> None:
            responses.append(self.claim(code, ROLE_PRIMARY))

        threads = [
            threading.Thread(target=worker, args=("queueR1",)),
            threading.Thread(target=worker, args=("queueR2",)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len(responses), 2)
        self.assertEqual(len({response["study"]["audit_id"] for response in responses}), 2)

    def test_17_stale_client_session_cannot_override_locked_backend(self) -> None:
        response = self.claim("queueR1", ROLE_PRIMARY)
        token = response["session_token"]
        self.finalize(response)
        with self.assertRaises(PermissionError):
            self.service.checkpoint(token)

    def test_18_reviewer_code_change_clears_client_review_state(self) -> None:
        javascript = current_role_aware_interface_assets()["app.js"].decode("utf-8")
        self.assertIn("addEventListener('input',clearStaleClientState)", javascript)
        self.assertIn("resetReviewState()", javascript)
        self.assertNotIn("localStorage", javascript)

    def test_19_role_change_clears_incompatible_client_review_state(self) -> None:
        javascript = current_role_aware_interface_assets()["app.js"].decode("utf-8")
        self.assertIn("input.addEventListener('change',clearStaleClientState)", javascript)
        self.assertIn("workflowState='UNCLAIMED'", javascript)

    def test_20_end_session_clears_session_but_preserves_checkpoint(self) -> None:
        response = self.claim("queueR1", ROLE_PRIMARY)
        self.service.save(response["session_token"], {"studies": {}, "clips": {}})
        checkpoint = self.output / f"restricted/checkpoints/{response['event']['event_id']}/checkpoint.json"
        before = sha256_file(checkpoint)
        self.service.end_session(response["session_token"])
        with self.assertRaises(PermissionError):
            self.service.session(response["session_token"])
        self.assertEqual(sha256_file(checkpoint), before)

    def test_21_server_restart_preserves_incomplete_and_finalized_reviews(self) -> None:
        incomplete = self.claim("queueR1", ROLE_PRIMARY)
        self.service.save(incomplete["session_token"], {"studies": {}, "clips": {}})
        restarted = RoleAwareAuditService(self.output, self.fixture.paths.parent_media_root)
        resumed = restarted.claim(
            {
                "reviewer_code": "queueR1",
                "role": ROLE_PRIMARY,
                "action": ACTION_RESUME,
                "qualification_confirmed": True,
            }
        )
        self.assertEqual(resumed["event"]["event_id"], incomplete["event"]["event_id"])
        restarted.save(
            resumed["session_token"],
            complete_payload(resumed["study"])["annotations"],
        )
        restarted.lock(resumed["session_token"])
        restarted_again = RoleAwareAuditService(self.output, self.fixture.paths.parent_media_root)
        no_incomplete = restarted_again.claim(
            {
                "reviewer_code": "queueR1",
                "role": ROLE_PRIMARY,
                "action": ACTION_RESUME,
                "qualification_confirmed": True,
            }
        )
        self.assertEqual(no_incomplete["status"], NO_INCOMPLETE_STUDY)

    def test_22_completed_annotations_remain_immutable(self) -> None:
        response = self.claim("queueR1", ROLE_PRIMARY)
        self.finalize(response)
        checkpoint = self.output / f"restricted/checkpoints/{response['event']['event_id']}/checkpoint.json"
        before = sha256_file(checkpoint)
        with self.assertRaises(PermissionError):
            self.service.checkpoints.save(
                response["event"]["event_id"], complete_payload(response["study"])
            )
        self.assertEqual(sha256_file(checkpoint), before)

    def test_23_stale_active_pointer_to_valid_lock_is_repairable(self) -> None:
        response = self.claim("queueR1", ROLE_PRIMARY)
        self.save_complete(response)
        with patch.object(self.service.queue, "mark_locked", side_effect=RuntimeError("interrupted")):
            with self.assertRaises(RuntimeError):
                self.service.checkpoints.lock(response["event"]["event_id"])
        result = audit_queue_transition_state(
            self.output,
            expected_stale_pointers=1,
            repair_stale_pointers=True,
        )
        self.assertEqual(result["status"], QUEUE_TRANSITION_DRY_RUN_PASS)
        self.assertEqual(result["repaired_stale_active_pointers"], 1)
        self.assertEqual(
            self.service.queue.get_event(response["event"]["event_id"])["status"], "locked"
        )

    def test_24_active_incomplete_pointer_is_not_cleared(self) -> None:
        response = self.claim("queueR1", ROLE_PRIMARY)
        before = sha256_file(self.service.queue.state_path)
        result = audit_queue_transition_state(self.output)
        self.assertEqual(result["active_incomplete_claims"], 1)
        self.assertEqual(result["repaired_stale_active_pointers"], 0)
        self.assertEqual(sha256_file(self.service.queue.state_path), before)
        self.assertEqual(
            self.service.queue.get_event(response["event"]["event_id"])["status"], "in_progress"
        )

    def test_25_pointer_repair_changes_no_annotation_values(self) -> None:
        response = self.claim("queueR1", ROLE_PRIMARY)
        self.save_complete(response)
        checkpoint = self.output / f"restricted/checkpoints/{response['event']['event_id']}/checkpoint.json"
        with patch.object(self.service.queue, "mark_locked", side_effect=RuntimeError("interrupted")):
            with self.assertRaises(RuntimeError):
                self.service.checkpoints.lock(response["event"]["event_id"])
        before = sha256_file(checkpoint)
        audit_queue_transition_state(
            self.output,
            expected_stale_pointers=1,
            repair_stale_pointers=True,
        )
        self.assertEqual(sha256_file(checkpoint), before)

    def test_26_transition_response_exposes_no_scientific_or_path_fields(self) -> None:
        self.claim("queueR1", ROLE_PRIMARY)
        response = self.claim("queueR1", ROLE_PRIMARY)
        text = json.dumps(response).lower()
        for prohibited in ("target", "split", "prediction", "residual", "report_label", "/restricted/"):
            self.assertNotIn(prohibited, text)

    def test_27_transition_tests_use_synthetic_state_only(self) -> None:
        self.assertTrue(self.output.is_relative_to(self.root))
        self.assertTrue(self.root.is_relative_to(Path(tempfile.gettempdir()).parent))

    def test_28_mixed_reviewer_records_are_not_silently_merged(self) -> None:
        self.claim("queueR1", ROLE_PRIMARY)
        path = self.service.queue.state_path
        state = json.loads(path.read_text(encoding="utf-8"))
        duplicate = dict(state["events"][0])
        duplicate["event_id"] = "ESYNTHETICDUPLICATE"
        duplicate["reviewer_code"] = "queueR2"
        state["events"].append(duplicate)
        write_json(path, state)
        with self.assertRaises(Tier1BlockedError) as caught:
            audit_queue_transition_state(self.output)
        self.assertEqual(caught.exception.status, BLOCKED_MIXED_REVIEWER_ATTRIBUTION)

    def test_explicit_archived_and_reassigned_states_are_preserved(self) -> None:
        response = self.claim("queueR1", ROLE_PRIMARY)
        archived = self.service.checkpoints.archive_for_reassignment(
            response["event"]["event_id"],
            owner_confirmed=True,
            reason="synthetic reassignment test",
        )
        self.assertEqual(archived["event"]["status"], "archived_incomplete")
        self.assertEqual(
            self.service.checkpoints.review_state(archived["event"])["workflow_state"],
            ARCHIVED_INCOMPLETE,
        )
        replacement = self.claim("queueR2", ROLE_PRIMARY)
        self.assertEqual(replacement["workflow_state"], REASSIGNED_FRESH)

    def test_no_eligible_queue_returns_explicit_state_without_reopening(self) -> None:
        for _ in self.service.queue.policy["primary_queue"]:
            response = self.claim("queueR1", ROLE_PRIMARY)
            self.finalize(response)
        exhausted = self.claim("queueR1", ROLE_PRIMARY)
        self.assertEqual(exhausted["status"], NO_ELIGIBLE_STUDY)
        self.assertFalse(exhausted["open_study"])

    def test_missing_conditional_note_is_reported_as_an_operational_requirement(self) -> None:
        response = self.claim("queueR1", ROLE_PRIMARY)
        payload = complete_payload(response["study"])
        study_id = response["study"]["audit_id"]
        payload["annotations"]["studies"][study_id]["spectral_doppler_present"] = "uncertain"
        self.service.save(response["session_token"], payload["annotations"])
        self.service.end_session(response["session_token"])
        blocked = self.claim("queueR1", ROLE_PRIMARY)
        self.assertEqual(blocked["status"], INCOMPLETE_STUDY_EXISTS)
        self.assertEqual(
            blocked["requirements"],
            ["Add restricted study notes for positive or uncertain study findings."],
        )

class ReducedInterfaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.fixture = ParentFixture(self.root)
        self.output = self.root / "reduced"
        self.result = self.fixture.build_package(self.output)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_interface_package_is_role_aware_and_blinded(self) -> None:
        policy = json.loads(
            (self.output / "restricted/interface/interface_policy.json").read_text(encoding="utf-8")
        )
        self.assertEqual(policy["status"], ROLE_AWARE_INTERFACE_READY)
        for field in (
            "target_visible",
            "split_visible",
            "report_label_visible",
            "prediction_visible",
            "residual_visible",
            "identifiers_or_paths_visible",
            "formal_reliability_status_visible",
            "ocr_available",
            "automated_annotation",
            "image_download_button",
        ):
            self.assertFalse(policy[field])

    def test_start_screen_has_code_role_and_claim_controls_but_no_study_list(self) -> None:
        html = (self.output / "restricted/interface/index.html").read_text(encoding="utf-8")
        css = (self.output / "restricted/interface/style.css").read_text(encoding="utf-8")
        self.assertIn("Reviewer code", html)
        self.assertIn("Primary independent review", html)
        self.assertIn("Secondary independent review", html)
        self.assertIn("Resume my incomplete study", html)
        self.assertIn("Claim next eligible study", html)
        self.assertNotIn("select a study", html.lower())
        self.assertIn("[hidden]{display:none!important}", css)

    def test_protected_media_are_reused_and_not_copied(self) -> None:
        self.assertFalse((self.output / "restricted/media").exists())
        summary = json.loads(
            (self.output / "aggregate_safe/role_aware_interface_summary.json").read_text(encoding="utf-8")
        )
        self.assertTrue(summary["protected_media_reused"])
        self.assertFalse(summary["protected_media_copied"])

    def test_service_rejects_client_selected_study_and_scopes_media_to_claim(self) -> None:
        service = RoleAwareAuditService(self.output, self.fixture.paths.parent_media_root)
        service.registry.register("readerD", qualified=True)
        with self.assertRaises(ValueError):
            service.claim(
                {
                    "reviewer_code": "readerD",
                    "role": ROLE_PRIMARY,
                    "action": ACTION_CLAIM_NEXT,
                    "qualification_confirmed": True,
                    "audit_id": "A050",
                }
            )
        session = service.claim(
            {
                "reviewer_code": "readerD",
                "role": ROLE_PRIMARY,
                "action": ACTION_CLAIM_NEXT,
                "qualification_confirmed": True,
            }
        )
        self.assertNotIn("formal_reliability", session["event"])
        self.assertNotIn("target", json.dumps(session["study"]))
        allowed = session["study"]["clips"][0]["source_media_id"]
        self.assertTrue(service.media_path(session["session_token"], allowed).is_file())
        with self.assertRaises(PermissionError):
            service.media_path(session["session_token"], "SRC115")

    def test_synthetic_interface_validation_leaves_no_annotations(self) -> None:
        validation = validate_role_aware_interface_package(self.output)
        self.assertEqual(validation["status"], ROLE_AWARE_INTERFACE_READY)
        self.assertTrue(validation["synthetic_validation_removed"])
        production_path = self.output / "restricted/queue/queue_state.json"
        production_state = json.loads(production_path.read_text(encoding="utf-8"))
        self.assertEqual(production_state["events"], [])

    def test_all_45_phase2k_r2_contract_requirements_are_locked(self) -> None:
        contract = json.loads(
            (self.output / "aggregate_safe/phase2k_r2_validation_contract.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(contract["requirements_total"], 45)
        self.assertEqual(contract["requirements_passed"], 45)
        for requirement, passed in contract["requirements"].items():
            with self.subTest(requirement=requirement):
                self.assertTrue(passed)


class ProtocolV3ClarificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.fixture = ParentFixture(self.root)
        self.output = self.root / "reduced"
        self.fixture.build_package(self.output)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def activate(self) -> dict[str, object]:
        plan = plan_protocol_v3_transition(
            self.output,
            created_at_utc="2026-09-03T12:00:00Z",
        )
        return apply_protocol_v3_transition(plan, source_commit="a" * 40)

    def activate_legacy_for_addendum(self) -> None:
        plan = plan_protocol_v3_transition(
            self.output,
            created_at_utc="2026-09-03T12:00:00Z",
        )
        apply_protocol_v3_transition(plan, source_commit=PROTOCOL_V3_BASE_COMMIT)
        pointer_path = self.output / "restricted/active_audit_protocol.json"
        pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
        definition_path = self.output / pointer["protocol_definition_relative"]
        definition = json.loads(definition_path.read_text(encoding="utf-8"))
        definition["scoring_scope_by_tier"] = BASE_SCORING_SCOPE_BY_TIER
        for key in (
            "display_addendum",
            "source_only_field_definitions",
            "source_only_primary_field",
            "source_only_category_fields",
            "source_only_free_text_fields",
            "source_only_study_outcome_fields",
        ):
            definition.pop(key, None)
        definition["study_rollup"].pop(
            "model_input_and_source_only_rollups_separate", None
        )
        definition["source_commit"] = PROTOCOL_V3_BASE_COMMIT
        write_json(definition_path, definition)
        interface_policy_path = self.output / pointer["interface_policy_relative"]
        interface_policy = json.loads(interface_policy_path.read_text(encoding="utf-8"))
        for key in (
            "display_addendum",
            "tier_a_synchronized_side_by_side",
            "tier_a_shared_frame_index",
            "tier_a_encoder_frames",
            "tier_a_source_only_comparison",
            "tier_c_exact_model_input_panel",
            "model_input_and_source_only_rollups_separate",
        ):
            interface_policy.pop(key, None)
        write_json(interface_policy_path, interface_policy)
        pointer["source_commit"] = PROTOCOL_V3_BASE_COMMIT
        pointer.pop("display_addendum", None)
        pointer.pop("protocol_definition_relative", None)
        pointer.pop("interface_policy_relative", None)
        pointer["protocol_definition_sha256"] = sha256_file(definition_path)
        pointer["interface_policy_sha256"] = sha256_file(interface_policy_path)
        write_json(pointer_path, pointer)

    def service(self) -> RoleAwareAuditService:
        return RoleAwareAuditService(self.output, self.fixture.paths.parent_media_root)

    @staticmethod
    def claim(service: RoleAwareAuditService, code: str, role: str) -> dict[str, object]:
        return service.claim(
            {
                "reviewer_code": code,
                "role": role,
                "action": ACTION_CLAIM_NEXT,
                "qualification_confirmed": True,
            }
        )

    def test_scoring_scope_and_field_definitions_are_exact(self) -> None:
        self.assertEqual(
            SCORING_SCOPE_BY_TIER[TIER_A],
            "Score all primary audit fields from the exact model input in the right panel. "
            "Use the source acquisition on the left only for context and source-to-input "
            "comparison. Do not count features visible only in the source acquisition as "
            "model-input content.",
        )
        self.assertEqual(
            SCORING_SCOPE_BY_TIER[TIER_C],
            "Score the source acquisition shown here. This clip is not a verified exact "
            "model input. Findings will be reported separately as source-acquisition "
            "evidence and must not be interpreted as content proven to have reached the encoder.",
        )
        self.assertIn("Numbers alone do not count", FIELD_DEFINITIONS["visible_text"])
        self.assertIn("generic depth", FIELD_DEFINITIONS["visible_numeric_value"])
        self.assertIn("ambiguous VTI should be Uncertain", FIELD_DEFINITIONS["lvot_vti_specific_label"])
        self.assertIn("written-out equivalent", FIELD_DEFINITIONS["tapse_specific_label"])
        self.assertIn("Exclude depth markers", FIELD_DEFINITIONS["candidate_target_value_present"])
        self.assertIn("routine ECG gating strip alone does not count", FIELD_DEFINITIONS["waveform_or_measurement_tracing"])
        self.assertIn(
            "source acquisition but not visible in the exact model input",
            SOURCE_ONLY_FIELD_DEFINITIONS[V3_SOURCE_ONLY_PRIMARY_FIELD],
        )

    def test_clip_rollup_hierarchy_and_not_assessable_rule(self) -> None:
        self.assertEqual(rollup_presence(["no", "yes", "uncertain"]), "yes")
        self.assertEqual(rollup_presence(["no", "uncertain", "not_assessable"]), "uncertain")
        self.assertEqual(rollup_presence(["no", "not_assessable"]), "no")
        self.assertEqual(rollup_presence(["not_assessable", "not_assessable"]), "not_assessable")
        self.assertEqual(rollup_presence([]), "")
        with self.assertRaises(ValueError):
            rollup_presence(["unknown"])
        self.assertEqual(
            set(V3_STUDY_OUTCOMES),
            {
                "spectral_doppler_present",
                "m_mode_present",
                "waveform_or_measurement_tracing_present",
                "calipers_present",
                "visible_text_present",
                "visible_numeric_value_present",
                "lvot_vti_specific_label_present",
                "tapse_specific_label_present",
                "candidate_target_value_present",
            },
        )

    def test_transition_archives_legacy_state_by_hash_and_starts_fresh(self) -> None:
        legacy = self.service()
        legacy.registry.register("legacyR1", qualified=True)
        prior = self.claim(legacy, "legacyR1", ROLE_PRIMARY)
        legacy.save(prior["session_token"], complete_payload(prior["study"])["annotations"])
        legacy.lock(prior["session_token"])
        source_hashes = {
            path: sha256_file(path)
            for root in (
                self.output / "restricted/queue",
                self.output / "restricted/checkpoints",
            )
            for path in root.rglob("*")
            if path.is_file()
        }

        result = self.activate()

        self.assertEqual(result["status"], V3_ACTIVE)
        self.assertEqual(result["current_protocol_events"], 0)
        self.assertEqual(result["current_protocol_checkpoint_files"], 0)
        self.assertEqual(result["aggregation_eligible_locked_reviews"], 0)
        pointer = json.loads(
            (self.output / "restricted/active_audit_protocol.json").read_text(encoding="utf-8")
        )
        archive_path = self.output / pointer["archive_manifest_relative"]
        archive = json.loads(archive_path.read_text(encoding="utf-8"))
        self.assertEqual(archive["status"], PROTOCOL_ARCHIVE_STATUS)
        for field in (
            "excluded_from_prevalence",
            "excluded_from_agreement",
            "excluded_from_adjudication",
            "excluded_from_final_aggregation",
        ):
            self.assertTrue(archive[field])
        for source_path, expected_hash in source_hashes.items():
            self.assertEqual(sha256_file(source_path), expected_hash)
        archive_root = archive_path.parent
        for record in archive["file_records"]:
            copied_root = "queue" if record["archive_role"] == "queue" else "checkpoints"
            copied = archive_root / copied_root / record["relative_path"]
            self.assertEqual(sha256_file(copied), record["sha256"])

    def test_v3_has_blank_attributable_primary_and_secondary_slots(self) -> None:
        self.activate()
        service = self.service()
        service.registry.register("v3primary", qualified=True)
        service.registry.register("v3secondary", qualified=True)
        primary = self.claim(service, "v3primary", ROLE_PRIMARY)
        secondary = self.claim(service, "v3secondary", ROLE_SECONDARY)
        for response in (primary, secondary):
            self.assertEqual(response["checkpoint"]["annotations"], {"studies": {}, "clips": {}})
            self.assertEqual(response["checkpoint"]["protocol_name"], PROTOCOL_V3_NAME)
            self.assertEqual(response["access_mode"], "editable")
        self.assertEqual(primary["event"]["role"], ROLE_PRIMARY)
        self.assertEqual(secondary["event"]["role"], ROLE_SECONDARY)

    def test_familiarization_study_is_replaced_if_formally_selected(self) -> None:
        pilot_path = self.output / "restricted/roster/pilot_checkpoint_classification_restricted.json"
        familiarization = json.loads(pilot_path.read_text(encoding="utf-8"))["pilot_audit_id"]
        policy_path = self.output / "restricted/interface/queue_policy_restricted.json"
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
        formal = list(policy["formal_reliability_queue"])
        if familiarization not in formal:
            formal[-1] = familiarization
            self.assertEqual(len(set(formal)), FORMAL_RELIABILITY_N)
            policy["formal_reliability_queue"] = formal
            write_json(policy_path, policy)
            state_path = self.output / "restricted/queue/queue_state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["queue_policy_sha256"] = sha256_file(policy_path)
            write_json(state_path, state)

        plan = plan_protocol_v3_transition(
            self.output,
            created_at_utc="2026-09-03T12:00:00Z",
        )
        self.assertTrue(plan.familiarization_was_formal)
        self.assertIsNotNone(plan.formal_replacement)
        self.assertNotIn(familiarization, plan.v3_formal_queue)
        self.assertIn(familiarization, plan.primary_queue)
        apply_protocol_v3_transition(plan, source_commit="b" * 40)
        validation = validate_active_protocol_v3(self.output, require_fresh=True)
        self.assertTrue(validation["familiarization_excluded_from_formal_reliability"])

    def test_server_derives_summary_and_rejects_inconsistent_manual_values(self) -> None:
        self.activate()
        service = self.service()
        service.registry.register("v3derive", qualified=True)
        response = self.claim(service, "v3derive", ROLE_PRIMARY)
        payload = complete_v3_payload(response["study"])
        audit_id = response["study"]["audit_id"]
        payload["annotations"]["studies"][audit_id]["spectral_doppler_present"] = "yes"
        with self.assertRaises(ValueError):
            service.save(response["session_token"], payload["annotations"])

        payload = complete_v3_payload(response["study"])
        saved = service.save(response["session_token"], payload["annotations"])
        study_record = saved["checkpoint"]["annotations"]["studies"][audit_id]
        self.assertEqual(study_record, payload["annotations"]["studies"][audit_id])
        self.assertEqual(study_record["derived_summary_confirmed"], "yes")

    def test_finalized_view_is_read_only_and_owner_restart_preserves_hashes(self) -> None:
        self.activate()
        service = self.service()
        service.registry.register("v3owner", qualified=True)
        service.registry.register("v3other", qualified=True)
        response = self.claim(service, "v3owner", ROLE_PRIMARY)
        service.save(
            response["session_token"],
            complete_v3_payload(response["study"])["annotations"],
        )
        service.lock(response["session_token"])
        event_id = response["event"]["event_id"]
        event_root = service.checkpoints.root / event_id
        checkpoint_path = event_root / "checkpoint.json"
        lock_path = event_root / "LOCKED.json"
        checkpoint_hash = sha256_file(checkpoint_path)
        lock_hash = sha256_file(lock_path)

        read_only = service.view_finalized(
            {
                "reviewer_code": "v3owner",
                "role": ROLE_PRIMARY,
                "qualification_confirmed": True,
            }
        )
        self.assertEqual(read_only["access_mode"], "read_only_finalized")
        with self.assertRaises(PermissionError):
            service.save(read_only["session_token"], {"studies": {}, "clips": {}})
        with self.assertRaises(PermissionError):
            service.checkpoints.restart_finalized(
                event_id,
                reviewer_code="v3other",
                role=ROLE_PRIMARY,
                owner_confirmed=True,
            )

        restarted = service.restart_finalized(
            read_only["session_token"], owner_confirmed=True
        )
        self.assertEqual(restarted["access_mode"], "editable")
        self.assertEqual(restarted["checkpoint"]["annotations"], {"studies": {}, "clips": {}})
        self.assertEqual(sha256_file(checkpoint_path), checkpoint_hash)
        self.assertEqual(sha256_file(lock_path), lock_hash)
        old_event = service.queue.get_event(event_id)
        self.assertEqual(old_event["status"], "locked")
        state = json.loads(service.queue.state_path.read_text(encoding="utf-8"))
        self.assertIn(event_id, state["superseded_event_ids"])
        self.assertEqual(aggregation_eligible_events_from_state(state), [])
        restart_records = list(service.queue.restart_root.glob("restart-*.json"))
        self.assertEqual(len(restart_records), 1)
        restart_record = json.loads(restart_records[0].read_text(encoding="utf-8"))
        self.assertEqual(restart_record["checkpoint_sha256"], checkpoint_hash)
        self.assertEqual(restart_record["lock_sha256"], lock_hash)
        self.assertTrue(restart_record["excluded_from_final_aggregation"])

    def test_interface_exposes_scope_banner_definitions_rollup_and_restart(self) -> None:
        assets = current_role_aware_interface_assets()
        html = assets["index.html"].decode("utf-8")
        javascript = assets["app.js"].decode("utf-8")
        self.assertIn('id="scoring-scope" class="scope-banner"', html)
        self.assertIn("View my latest finalized study", html)
        self.assertIn("Archive and restart under current protocol", html)
        self.assertIn(SCORING_SCOPE_BY_TIER[TIER_A], javascript)
        self.assertIn(SCORING_SCOPE_BY_TIER[TIER_C], javascript)
        self.assertIn(FIELD_DEFINITIONS["candidate_target_value_present"], javascript)
        self.assertIn("renderStudySummary", javascript)
        self.assertIn("derived_summary_confirmed", javascript)
        self.assertIn("Source acquisition — corresponding source frame", html)
        self.assertIn("Exact model input — scoring target", html)
        self.assertIn("Source content not visible in exact model input", html)
        self.assertIn('max="15"', html)
        self.assertIn("drawSprite('source');drawSprite('model')", javascript)
        self.assertIn("frameIndex=(frameIndex+1)%16", javascript)
        self.assertIn("frameIndex=Number(e.target.value);renderFrame()", javascript)
        self.assertNotIn('name="spectral_doppler_present"', html)

    def test_source_only_rollup_is_separate_from_model_input_rollup(self) -> None:
        clips = {
            "clipA": {
                "acquisition_content_type": "2d_b_mode",
                "visible_text": "no",
                "source_only_relevant_content": "yes",
                "source_only_visible_text": "yes",
                "source_only_lvot_vti_specific_label": "yes",
                "source_only_candidate_target_value": "yes",
            }
        }
        summary = derive_study_summary(
            clips,
            clip_evidence_tiers={"clipA": TIER_A},
        )
        self.assertEqual(summary["visible_text_present"], "no")
        self.assertEqual(summary["source_only_relevant_content_present"], "yes")
        self.assertEqual(summary["source_only_target_specific_label_present"], "yes")
        self.assertEqual(summary["source_only_candidate_target_value_present"], "yes")
        self.assertEqual(
            set(V3_SOURCE_ONLY_STUDY_OUTCOMES),
            {
                "source_only_relevant_content_present",
                "source_content_not_visible_in_exact_model_input_present",
                "source_only_target_specific_label_present",
                "source_only_candidate_target_value_present",
            },
        )

    def test_tier_a_yes_requires_category_and_tier_c_rejects_source_only_fields(self) -> None:
        self.activate()
        service = self.service()
        responses = []
        for index in range(8):
            code = f"scopeR{index}"
            service.registry.register(code, qualified=True)
            responses.append(self.claim(service, code, ROLE_PRIMARY))
        tier_a = responses[0]
        tier_a_payload = complete_v3_payload(tier_a["study"])
        tier_a_clip = tier_a["study"]["clips"][0]["clip_audit_id"]
        tier_a_payload["annotations"]["clips"][tier_a_clip][
            V3_SOURCE_ONLY_PRIMARY_FIELD
        ] = "yes"
        tier_a_study_record = tier_a_payload["annotations"]["studies"][
            tier_a["study"]["audit_id"]
        ]
        for field in (*V3_STUDY_OUTCOMES, *V3_SOURCE_ONLY_STUDY_OUTCOMES):
            tier_a_study_record.pop(field, None)
        service.save(tier_a["session_token"], tier_a_payload["annotations"])
        review = service.checkpoints.review_state(
            service.queue.get_event(tier_a["event"]["event_id"])
        )
        self.assertTrue(
            any("source-only category" in message for message in review["requirements"])
        )
        with self.assertRaises(ValueError):
            service.lock(tier_a["session_token"])
        tier_a_payload["annotations"]["clips"][tier_a_clip][
            V3_SOURCE_ONLY_CATEGORY_FIELDS[0]
        ] = "yes"
        saved = service.save(tier_a["session_token"], tier_a_payload["annotations"])
        self.assertEqual(
            saved["checkpoint"]["annotations"]["studies"][tier_a["study"]["audit_id"]][
                "visible_text_present"
            ],
            "not_assessable",
        )

        tier_c = responses[-1]
        tier_c_payload = complete_v3_payload(tier_c["study"])
        tier_c_clip = tier_c["study"]["clips"][0]["clip_audit_id"]
        tier_c_payload["annotations"]["clips"][tier_c_clip][
            V3_SOURCE_ONLY_PRIMARY_FIELD
        ] = "yes"
        tier_c_payload["annotations"]["clips"][tier_c_clip][
            V3_SOURCE_ONLY_CATEGORY_FIELDS[0]
        ] = "yes"
        with self.assertRaisesRegex(ValueError, "Tier-C clips cannot contain"):
            service.save(tier_c["session_token"], tier_c_payload["annotations"])

    def test_side_by_side_addendum_updates_only_versioned_metadata_when_blank(self) -> None:
        self.activate_legacy_for_addendum()
        pointer_path = self.output / "restricted/active_audit_protocol.json"
        pointer_before = json.loads(pointer_path.read_text(encoding="utf-8"))
        preserved_paths = [
            self.output / pointer_before["archive_manifest_relative"],
            self.output / pointer_before["protocol_root_relative"] / "queue/queue_state.json",
            self.output / pointer_before["protocol_root_relative"] / "interface/queue_policy_restricted.json",
            self.output / pointer_before["protocol_root_relative"] / "interface/study_manifest_restricted.json",
        ]
        preserved_hashes = {path: sha256_file(path) for path in preserved_paths}
        result = apply_side_by_side_addendum(
            self.output,
            source_commit="b" * 40,
            applied_at_utc="2026-09-08T12:00:00Z",
        )
        self.assertEqual(result["status"], "V3_SIDE_BY_SIDE_SCORING_VERIFIED")
        self.assertEqual(result["deployment_readiness_status"], "READY_FOR_V3_BLINDED_HUMAN_AUDIT")
        self.assertEqual(result["current_protocol_events"], 0)
        self.assertEqual(result["current_protocol_checkpoint_files"], 0)
        self.assertTrue(result["protected_media_reused"])
        self.assertEqual(preserved_hashes, {path: sha256_file(path) for path in preserved_paths})
        pointer_after = json.loads(pointer_path.read_text(encoding="utf-8"))
        self.assertEqual(pointer_after["source_commit"], "b" * 40)
        self.assertNotEqual(
            pointer_after["protocol_definition_relative"],
            pointer_before.get("protocol_definition_relative"),
        )

    def test_side_by_side_addendum_fails_closed_after_review_state_exists(self) -> None:
        self.activate_legacy_for_addendum()
        service = self.service()
        service.registry.register("startedR", qualified=True)
        self.claim(service, "startedR", ROLE_PRIMARY)
        with self.assertRaisesRegex(Tier1BlockedError, "fresh blank V3"):
            apply_side_by_side_addendum(self.output, source_commit="b" * 40)

    def test_metadata_validation_fails_if_familiarization_reenters_formal_queue(self) -> None:
        self.activate()
        pointer = json.loads(
            (self.output / "restricted/active_audit_protocol.json").read_text(encoding="utf-8")
        )
        protocol_root = self.output / pointer["protocol_root_relative"]
        definition_path = protocol_root / "interface/protocol_definition_restricted.json"
        definition = json.loads(definition_path.read_text(encoding="utf-8"))
        familiarization = definition["familiarization_study"]["physical_study_token"]
        policy_path = protocol_root / "interface/queue_policy_restricted.json"
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
        policy["formal_reliability_queue"][0] = familiarization
        write_json(policy_path, policy)
        pointer["queue_policy_sha256"] = sha256_file(policy_path)
        pointer_path = self.output / "restricted/active_audit_protocol.json"
        write_json(pointer_path, pointer)
        state_path = protocol_root / "queue/queue_state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["queue_policy_sha256"] = sha256_file(policy_path)
        write_json(state_path, state)
        with self.assertRaises(Tier1BlockedError):
            validate_active_protocol_v3(self.output)


if __name__ == "__main__":
    unittest.main()
