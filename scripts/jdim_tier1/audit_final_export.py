"""Aggregate-only export of locked reviews and completed human adjudication."""
from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path

from scipy.stats import beta

from . import audit_protocol_v3 as v3
from .audit import CONTENT_TYPES
from .audit_adjudication import decision_values, verify_queue
from .audit_finalization import load, now, require, write_once
from .reduced_audit_interface import ReviewerRegistry
from .safety import sha256_file, sha256_json


def exact_interval(numerator, denominator):
    require(0 <= numerator <= denominator and denominator > 0, "invalid proportion denominator")
    return [0.0 if numerator == 0 else float(beta.ppf(.025, numerator, denominator-numerator+1)),
            1.0 if numerator == denominator else float(beta.ppf(.975, numerator+1, denominator-numerator))]


def export_no_adjudication(root: Path):
    lock, cert, queue = verify_queue(root)
    require(cert["status"] == "BLINDED_ADJUDICATION_NOT_REQUIRED" and not queue["tasks"],
            "human adjudication and matching must be completed before this export")
    snapshot = load(root / "blinded_snapshot_restricted.json")
    return _export_snapshot(root, lock, snapshot)


def adjudicated_snapshot(root: Path):
    """Apply only hash-verified human choices to a disposable in-memory copy."""
    lock, cert, queue = verify_queue(root)
    require(cert["status"] == "BLINDED_ADJUDICATION_QUEUE_LOCKED" and queue["tasks"],
            "nonempty locked adjudication queue required")
    completion = load(root / "adjudication_completion_lock.json")
    tasks = {t["token"]: t for t in queue["tasks"]}
    require(len(tasks) == len(queue["tasks"]), "duplicate adjudication item")
    require(completion.get("status") == "BLINDED_ADJUDICATION_LOCKED"
            and completion.get("queue_sha256") == cert["queue_sha256"]
            and completion.get("completed_items") == len(tasks)
            and completion.get("human_decisions_only") is True
            and completion.get("target_values_read") is False,
            "adjudication completion certificate differs")
    expected = {token + ".json" for token in tasks}
    require(set(completion["record_hashes"]) == expected, "adjudication record set differs")
    records_root = root / "human_adjudication"
    require({p.name for p in records_root.iterdir()} == expected,
            "unexpected adjudication record")
    registry = ReviewerRegistry(Path(lock["audit_root"]) / "restricted/reviewer_registry",
                                initialize=False)
    linkage = load(root / "adjudication_linkage_restricted.json")
    require(set(linkage) == set(tasks), "adjudication linkage set differs")
    snapshot = load(root / "blinded_snapshot_restricted.json")
    primary = {e["physical_study_token"]: e for e in snapshot["events"] if e["role"] == "primary"}
    require(len(primary) == sum(e["role"] == "primary" for e in snapshot["events"]),
            "duplicate primary event")
    manifest = {s["audit_id"]: s for s in snapshot["manifest"]["studies"]}
    require(set(manifest) == set(primary), "primary study set differs")
    applied = set()
    changed_fields = 0
    for token, task in tasks.items():
        path = records_root / (token + ".json")
        require(not path.is_symlink() and sha256_file(path) == completion["record_hashes"][path.name],
                "locked human adjudication record changed")
        record = load(path)
        require(record.get("schema_version") == "JDIM_BLINDED_HUMAN_ADJUDICATION_V1"
                and record.get("token") == token
                and record.get("queue_sha256") == cert["queue_sha256"]
                and record.get("human_confirmed") is True
                and record.get("qualified_physician_attested") is True,
                "human adjudication confirmation differs")
        require(record.get("content_sha256") == sha256_json(
            {k: v for k, v in record.items() if k != "content_sha256"}),
            "human adjudication content hash differs")
        registry.require_active_qualified(record["adjudicator_code"])
        decisions = decision_values(task, record["decisions"])
        sid, cid = linkage[token]["study"], linkage[token]["clip"]
        require(sid in primary and (sid, cid) not in applied, "adjudication study linkage differs")
        clips = {c["clip_audit_id"]: c for c in manifest[sid]["clips"]}
        require(cid in clips and clips[cid]["evidence_tier"] == task["evidence_tier"],
                "adjudication evidence tier differs")
        clip = snapshot["records"][primary[sid]["event_id"]]["clips"][cid]
        require(task["primary"] == {f: clip.get(f, "") for f in task["fields"]},
                "adjudication original response differs")
        changed_fields += sum(clip.get(f, "") != value for f, value in decisions.items())
        clip.update(decisions)
        applied.add((sid, cid))
    for sid, event in primary.items():
        record = snapshot["records"][event["event_id"]]
        tiers = {c["clip_audit_id"]: c["evidence_tier"] for c in manifest[sid]["clips"]}
        require(set(record["clips"]) == set(tiers), "primary clip set differs")
        require(set(record["studies"]) == {sid}, "primary summary identity differs")
        record["studies"][sid].update(v3.derive_study_summary(record["clips"], tiers))
        for clip in record["clips"].values():
            # Matching is not inferred from labels, free text, or image pixels.
            require(not any(clip.get(f) in {"yes", "uncertain", "not_assessable"} for f in
                            ("candidate_target_value_present", "source_only_candidate_target_value"))
                    and not any(str(clip.get(f, "")).strip() for f in
                                ("candidate_target_value", "source_only_candidate_target_value_text")),
                    "candidate-value matching requires a separate verified matching step")
    evidence = {"completed_items": len(tasks), "applied_items": len(applied),
                "changed_clip_fields": changed_fields,
                "completion_lock_sha256": sha256_file(root / "adjudication_completion_lock.json"),
                "human_decisions_only": True, "original_reviews_modified": False,
                "report_label_values_read": False, "candidate_matching_required": False,
                "summary_method": "existing V3 clip-to-study roll-up after locked human adjudication"}
    return lock, snapshot, evidence


def export_after_adjudication(root: Path, *, write: bool = True):
    lock, snapshot, evidence = adjudicated_snapshot(root)
    return _export_snapshot(root, lock, snapshot, adjudication=evidence, write=write)


def _export_snapshot(root, lock, snapshot, *, adjudication=None, write=True):
    path = Path(lock["audit_root"]) / "restricted/roster/reduced_target_assignments_restricted.csv"
    with path.open(newline="") as stream:
        assignments = list(csv.DictReader(stream))
    membership = {}
    assignment_keys = set()
    for row in assignments:
        require(row["target"] in {"lvot_vti", "tapse"}, "unknown target membership")
        key = (row["audit_id"], row["target"])
        require(key not in assignment_keys, "duplicate target assignment")
        assignment_keys.add(key)
        membership.setdefault(row["audit_id"], set()).add(row["target"])
    events = {e["physical_study_token"]:e for e in snapshot["events"] if e["role"] == "primary"}
    require(set(membership) == set(events), "locked assignment membership differs")
    grouped = {}
    for study in snapshot["manifest"]["studies"]:
        sid = study["audit_id"]
        record = snapshot["records"][events[sid]["event_id"]]
        tier = study["clips"][0]["evidence_tier"]
        require(tier in {"EXACT_MODEL_INPUT", "SOURCE_ACQUISITION_ONLY"}
                and all(c["evidence_tier"] == tier for c in study["clips"]),
                "mixed or unknown study evidence tier")
        for target in membership[sid]:
            grouped.setdefault((target,tier), []).append(record)
    rows = []
    for (target,tier), records in sorted(grouped.items()):
        n = len(records)
        clips = [clip for r in records for clip in r["clips"].values()]
        fields = [*v3.V3_STUDY_OUTCOMES]
        if tier == "EXACT_MODEL_INPUT": fields += list(v3.V3_SOURCE_ONLY_STUDY_OUTCOMES)
        for field in fields:
            counts = Counter(next(iter(r["studies"].values())).get(field, "") for r in records)
            require(sum(counts.values()) == n and "" not in counts, "study outcome missing")
            rows.append({"target":target,"evidence_tier":tier,"unit":"study","field":field,
                         "denominator":n,"counts":dict(counts),"yes_ci_95":exact_interval(counts["yes"],n),
                         "zero_wording":"not observed in the audited sample" if counts["yes"] == 0 else None})
        clip_fields = list(v3.V3_CLIP_PRESENCE_FIELDS)
        if tier == "EXACT_MODEL_INPUT": clip_fields += [v3.V3_SOURCE_ONLY_PRIMARY_FIELD, *v3.V3_SOURCE_ONLY_CATEGORY_FIELDS]
        for field in ["acquisition_content_type", *clip_fields]:
            counts = Counter(c.get(field, "not_recorded") for c in clips)
            rows.append({"target":target,"evidence_tier":tier,"unit":"clip","field":field,
                         "denominator":len(clips),"counts":dict(counts),"confidence_interval":None})
        for modality in sorted(CONTENT_TYPES - {"not_assessable","uncertain","mixed","other"}):
            count = sum(any(c.get("acquisition_content_type") == modality for c in r["clips"].values()) for r in records)
            rows.append({"target":target,"evidence_tier":tier,"unit":"study","field":"modality_"+modality,
                         "denominator":n,"yes":count,"yes_ci_95":exact_interval(count,n)})
    payload = {"status":"MANUAL_INPUT_CONTENT_AUDIT_LOCKED","created_at_utc":now(),
               "annotation_lock_sha256":sha256_file(root / "blinded_annotation_lock.json"),
               "adjudication_certificate_sha256":sha256_file(root / "adjudication_queue_certificate.json"),
               "assignment_source_sha256":sha256_file(path),"rows":rows,
               "candidate_values_require_adjudication":True,"candidate_matching_not_applicable":True,
               "study_proportion_denominator":"all reviewed studies in the target and evidence tier; uncertain/not-assessable reported separately",
               "clip_independence_assumed":False,"formal_reliability_estimated":False,
               "uncollected_fields":"No inference from free-text notes or image pixels. Source-only categories are not a full source-panel annotation."}
    if adjudication is not None:
        payload["post_adjudication"] = adjudication
    if not write:
        return {"status": "POST_ADJUDICATION_EXPORT_PREFLIGHT_PASS", "aggregate_rows": len(rows),
                "files_written": False, "post_adjudication": adjudication}
    dest = root / "aggregate_safe"
    dest.mkdir(mode=0o700)
    digest = write_once(dest / "manual_input_content_audit_summary.json",payload)
    return {"status":payload["status"],"aggregate_rows":len(rows),"sha256":digest}
