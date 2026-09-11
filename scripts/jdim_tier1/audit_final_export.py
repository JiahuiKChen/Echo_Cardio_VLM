"""Aggregate-safe export for the no-adjudication-required branch only."""
from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path

from scipy.stats import beta

from . import audit_protocol_v3 as v3
from .audit import CONTENT_TYPES
from .audit_adjudication import verify_queue
from .audit_finalization import load, now, require, write_once
from .safety import sha256_file


def exact_interval(numerator, denominator):
    require(0 <= numerator <= denominator and denominator > 0, "invalid proportion denominator")
    return [0.0 if numerator == 0 else float(beta.ppf(.025, numerator, denominator-numerator+1)),
            1.0 if numerator == denominator else float(beta.ppf(.975, numerator+1, denominator-numerator))]


def export_no_adjudication(root: Path):
    lock, cert, queue = verify_queue(root)
    require(cert["status"] == "BLINDED_ADJUDICATION_NOT_REQUIRED" and not queue["tasks"],
            "human adjudication and matching must be completed before this export")
    snapshot = load(root / "blinded_snapshot_restricted.json")
    path = Path(lock["audit_root"]) / "restricted/roster/reduced_target_assignments_restricted.csv"
    with path.open(newline="") as stream:
        assignments = list(csv.DictReader(stream))
    membership = {}
    for row in assignments:
        require(row["target"] in {"lvot_vti", "tapse"}, "unknown target membership")
        membership.setdefault(row["audit_id"], set()).add(row["target"])
    events = {e["physical_study_token"]:e for e in snapshot["events"] if e["role"] == "primary"}
    require(set(membership) == set(events), "locked assignment membership differs")
    grouped = {}
    for study in snapshot["manifest"]["studies"]:
        sid = study["audit_id"]
        record = snapshot["records"][events[sid]["event_id"]]
        tier = study["clips"][0]["evidence_tier"]
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
    dest = root / "aggregate_safe"
    dest.mkdir(mode=0o700)
    digest = write_once(dest / "manual_input_content_audit_summary.json",payload)
    return {"status":payload["status"],"aggregate_rows":len(rows),"sha256":digest}
