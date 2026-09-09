#!/usr/bin/env python3
"""Prepare restricted grouped panel/mask drafts; never authorize fitting or clinical choices."""
from __future__ import annotations
import argparse
import io
import json
from pathlib import Path
from typing import Any
import pandas as pd
import audit_lvef_analysis_readiness as readiness
from build_target_dependency_registry import FAMILIES
from build_lvef_clinician_signoff_packet import CLINICAL_ISSUE_SPECS
from lvef_multitask_clinical_metadata import ALLOWED_TARGETS
from lvef_multitask_analysis_modes import load_policy, bind_approved_restricted_path
from lvef_revalidation_authority import C3_COMPLETION, canonical, decode, digest, private_bytes, publish, require
from prepare_lvef_revalidation_inputs import SOURCE_HASHES

REVIEWED_TECHNICAL_MANIFEST_SHA256 = "4e6c7ab3e957cfb5a369739021e2c71a41400a35103e7a77aa4bce2080e7b22a"

TECHNICAL_RULINGS = (('BSA_FORMULA_WEIGHT_AVAILABILITY', 'CONSERVATIVE_EXCLUSION', 'Five candidate fields are present in training. Metadata classes include area, length, mass and two unrecognized numeric unit classes. Anthropometrics remain outside the primary echo macro; no BSA formula or weight-source pathway is inferred. Use only explicitly supported units for any separately reviewed context predictor; prohibit uncertain derived shortcuts and keep BSA confined to any verified indexed pathway.'), ('DIMENSION_CM_MM_UNITS', 'RESOLVED_PROJECT_METADATA', 'Eighteen dimension candidate fields are present: sixteen have explicit cm numeric measurements and two have unknown-unit categorical values with zero numeric measurements. Convert the compatible cm measurements to mm by exactly 10 before the declared within-report aggregation. Unknown or incompatible unit rows cannot supply numeric dimension labels. Clinical construct and alias aggregation decisions remain separate.'), ('LVEDV_LVESV_FIELDS', 'CONSERVATIVE_EXCLUSION', 'Four candidate volume exports are present with numeric training values. Prohibit them as LVEF predictors without asserting method/beat matching or formula equivalence to the separate exact-name LVEF target. A projection class unrecognized by a finite unit vocabulary is not evidence that the source unit is invalid; the conservative shortcut exclusion does not depend on that classification.'), ('LVEF_ALIASES', 'CONSERVATIVE_EXCLUSION', 'Seven candidate EF-related fields are present. Preserve only exact case-sensitive raw lvef as label authority and prohibit other candidate EF exports as LVEF predictors. The fifteen prespecified same-report training pair diagnostics are diagnostic only; neither numerical agreement nor metadata similarity promotes an alias, and no synthetic canonical lvef row is created.'), ('LVEF_METHOD_MIXTURE', 'OPERATIONAL_DEFINITION_WITH_LIMITATION', 'Retain the operational exact-name numeric-median LVEF label on its historical EF-percentage-point analytical scale. The retained export and builder do not establish the acquisition method of each exact-name label. Method-specific candidate exports are not a verified method indicator for that label. Record method unspecified and unstratifiable, actual method mixture unknown, and native source-unit declaration unverified; do not claim homogeneous or demonstrated mixed methods.'), ('LV_MASS_RWT_FIELDS', 'CONSERVATIVE_EXCLUSION', 'The one candidate field has 135 categorical training values and zero numeric values. Do not infer category coding or a numeric LV-mass/RWT target or formula edge; exclude this unadjudicated export from primary predictors. LV mass, RWT and indexed mass remain separate possible dependency sets; BSA is relevant only to an indexed pathway.'), ('MITRAL_EA_EEPRIME_RATIO_FIELDS', 'CONSERVATIVE_EXCLUSION', 'Two ratio candidate fields have numeric training values but unknown declared units. Do not invent a unitless normalization, component identity or ratio-to-target equivalence. Prohibit these candidate ratio shortcuts as primary predictors. The relationship between the two E-labelled fields remains the separate clinician question, including its actual recorded unit mismatch.'), ('VELOCITY_MPS_CMPS_UNITS', 'RESOLVED_PROJECT_METADATA', 'Seven velocity-labelled candidate fields are present: six have explicit m/s numeric measurements and mitral_e_velocity has ms numeric measurements. Convert only m/s to cm/s by exactly 100. Keep mitral_e_velocity classified as time and exclude its incompatible rows from a velocity target before aggregation, common-row construction and support checks. It is outside candidate21. Do not reinterpret time as velocity, rename the canonical target, or merge it with mv_peak_e; any correction requires separate source-unit authority even if clinician Q6 describes the same construct.'), ('WALL_MOTION_FIELDS', 'CONSERVATIVE_EXCLUSION', 'Seventeen candidate wall-motion fields have categorical training values and zero numeric values. Prohibit all unadjudicated category exports as primary predictors rather than assigning numeric scores. Available technical evidence does not establish which are segmental correlates, global summaries or deterministic aggregates; none is treated as an EF-equivalent field or formula solely from its name.'))


def _csv(path: Path, expected_sha256: str | None = None) -> pd.DataFrame:
    data = readiness._bytes(path)
    if expected_sha256 is not None:
        require(digest(data) == expected_sha256, "REVIEW_SOURCE_HASH_MISMATCH")
    return pd.read_csv(io.BytesIO(data), keep_default_na=False, low_memory=False)


def prepare_technical_decisions(metadata_root: Path, expected_input_checksums: dict[str, str]) -> dict[str, Any]:
    """Record the reviewed nine bounded rulings with actual replayed evidence hashes."""
    source_roles = {"raw_canonical_mapping": "mapping", "structured_measurements": "structured",
                    "selected_studies": "selected", "subject_split_map": "split"}
    require(all(expected_input_checksums.get(role) == SOURCE_HASHES[key] for role, key in source_roles.items()),
            "REVIEW_TECHNICAL_SOURCE_SCOPE_MISMATCH")
    inspected = readiness.inspect_technical_packet(metadata_root, expected_input_checksums=expected_input_checksums)
    manifest_sha = inspected["manifest_sha256"]
    require(manifest_sha == REVIEWED_TECHNICAL_MANIFEST_SHA256, "REVIEW_TECHNICAL_EVIDENCE_NOT_REVIEWED")
    return {"artifact_type": "lvef_revalidation_technical_decisions_v1",
            "status": "APPROVED_TECHNICAL_DISPOSITIONS", "technical_manifest_sha256": manifest_sha,
            "input_checksums": dict(expected_input_checksums),
            "decisions": [{"issue_id": issue, "disposition": disposition, "rationale": rationale,
                           "evidence_sha256": manifest_sha} for issue, disposition, rationale in TECHNICAL_RULINGS],
            "new_test_performance_used": False}


def _families(canonical_name: str) -> set[str]:
    return {family for family, members in FAMILIES.items() if canonical_name in members}


def grouped_review(inputs: dict[str, Any], mapping: pd.DataFrame, evidence: pd.DataFrame,
                   registry: pd.DataFrame, clinical_registry: pd.DataFrame, *, clinical: dict[str, Any],
                   technical: dict[str, Any]) -> dict[str, Any]:
    """Materialize exact raw masks and grouped candidate context from existing authorities.

    Candidate contexts are not independent by default: each retains UNREVIEWED
    status and its concrete metadata/family basis. Only explicit reviewed registry
    positives could enter an approved allowlist; no final approval is issued here.
    """
    require(inputs.get("c3_completion_sha256") == C3_COMPLETION
            and inputs.get("status") == "PASS_MODEL_INDEPENDENT_INPUTS_PANEL_PENDING",
            "REVIEW_INPUT_RECEIPT_INVALID")
    require(set(mapping.columns) >= {"measurement", "canonical_measurement"}, "REVIEW_MAPPING_SCHEMA_INVALID")
    relations = mapping[["measurement", "canonical_measurement"]].drop_duplicates()
    raw_to_canonical = relations.groupby("measurement").canonical_measurement.agg(lambda x: sorted(set(x))).to_dict()
    canonical_to_raw = relations.groupby("canonical_measurement").measurement.agg(lambda x: sorted(set(x))).to_dict()
    raw_names = inputs["structured_names"]
    # The exact raw LVEF label has separate authority, including when absent from
    # the canonical map. This read view does not add or alter a source map row.
    if "lvef" in raw_names:
        raw_to_canonical["lvef"] = ["lvef"]
    require(len(raw_names) == len(set(raw_names)) and set(raw_names) <= set(raw_to_canonical), "REVIEW_RAW_UNIVERSE_MISMATCH")
    targets = inputs["target_names"]
    require(set(targets) <= set(ALLOWED_TARGETS) and "lvef" in targets, "REVIEW_TARGET_UNIVERSE_INVALID")
    unresolved_clinical = {row["issue_id"] for row in clinical["questions"]
                          if row["selected_option"] is None or row["selected_option"] == "UNRESOLVED_EXCLUDE"
                          or row["selected_option"].startswith("MIXED_")}
    question_targets = {target for issue in CLINICAL_ISSUE_SPECS if issue["issue_id"] in unresolved_clinical for target in issue["targets"]}
    # No clinical choice is supplied: pending affected constructs remain draft-only.
    technical_issues = {"BSA_FORMULA_WEIGHT_AVAILABILITY", "LVEDV_LVESV_FIELDS", "LVEF_ALIASES",
                        "LVEF_METHOD_MIXTURE", "LV_MASS_RWT_FIELDS", "MITRAL_EA_EEPRIME_RATIO_FIELDS", "WALL_MOTION_FIELDS"}
    technical_raw = set(evidence.loc[evidence.issue_id.isin(technical_issues), "raw_name"])
    technical_raw |= set(canonical_to_raw.get("mitral_e_velocity", []))
    raw_records, drafts = [], []
    for target in targets:
        policy = inputs["targets"][target]
        target_families = _families(target)
        direct = {"lvef"} if target == "lvef" else set(canonical_to_raw.get(target, []))
        target_rows = registry[registry.target == target]
        clinical_rows = clinical_registry[clinical_registry.target == target]
        deterministic_canonical = set(target_rows.loc[target_rows.relationship_category == "DETERMINISTIC_DERIVATIVE", "predictor"])
        deterministic_canonical |= set(clinical_rows.loc[clinical_rows.relationship_category == "DETERMINISTIC_DERIVATIVE", "predictor"])
        near_canonical = set(target_rows.loc[target_rows.relationship_category == "NEAR_DETERMINISTIC_CLINICAL_DERIVATIVE", "predictor"])
        category_masks = {"exact_target_fields": [], "aliases": [], "deterministic_fields": [],
                          "near_deterministic_fields": [], "family_fields": [], "technical_exclusions": [],
                          "clinical_pending_fields": [], "uncertain_exclusions": []}
        candidates: dict[str, list[str]] = {}
        for raw in raw_names:
            canonical_names = raw_to_canonical[raw]
            name = canonical_names[0] if len(canonical_names) == 1 else None
            families = _families(name) if name is not None else set()
            unit = inputs["structured_units"].get(raw, "UNRESOLVED")
            reason = None
            if raw in direct:
                reason = "exact_target_fields"
            elif name == target:
                reason = "aliases"
            elif name in deterministic_canonical:
                reason = "deterministic_fields"
            elif name in near_canonical:
                reason = "near_deterministic_fields"
            elif raw in technical_raw:
                reason = "technical_exclusions"
            elif name in question_targets:
                reason = "clinical_pending_fields"
            elif families & target_families:
                reason = "family_fields"
            elif name is None or name not in ALLOWED_TARGETS or not families or unit in {"UNRESOLVED", "unknown"}:
                reason = "uncertain_exclusions"
            if reason is not None:
                category_masks[reason].append(raw)
                disposition = "EXCLUDE_" + reason.upper()
                basis = "Explicit source identity or conservative prespecified dependency/family/technical/clinical gate; no alias equivalence inferred."
            else:
                group = "+".join(sorted(families))
                candidates.setdefault(group, []).append(raw)
                disposition = "CANDIDATE_DISTINCT_MEASUREMENT_CONTEXT"
                basis = "Exact unambiguous mapped measurement and supported units in a distinct prespecified family; no direct/formula/technical shortcut rule survived. This is a grouped review candidate, not proof of independence."
            raw_records.append({"target": target, "raw_predictor": raw, "canonical_names": canonical_names,
                                "source_families": sorted(families), "target_families": sorted(target_families),
                                "unit": unit, "proposed_disposition": disposition, "review_status": "UNREVIEWED",
                                "rationale": basis, "evidence_sha256": technical["technical_manifest_sha256"]})
        source_raw = policy.get("source_raw_fields", sorted(direct))
        valid_raw = policy.get("valid_unit_raw_fields", [])
        drafts.append({"name": target, "unit": policy["unit"], "family_candidates": sorted(target_families),
                       "support_floors_passed": policy["support_floors_passed"], "counts": policy["counts"],
                       "clinical_questions_pending": sorted(issue["issue_id"] for issue in CLINICAL_ISSUE_SPECS
                                                              if target in issue["targets"] and issue["issue_id"] in unresolved_clinical),
                       "source_raw_aggregation_approval": {"unit": policy["unit"], "source_raw_fields": source_raw,
                           "valid_unit_raw_fields": valid_raw, "aggregation_rule": policy.get("aggregation_rule", "UNREVIEWED"),
                           "construct_id": None, "label_definition_status": "UNREVIEWED", "rationale": "",
                           "final_approval": False},
                       "masks": category_masks, "candidate_positive_groups": candidates,
                       "allowed_predictors": [], "dependencies_resolved": False, "final_approval": False})
    return {"artifact_type": "lvef_revalidation_grouped_review_draft_v1", "status": "READY_FOR_GROUPED_REVIEW",
            "final_approval": False, "clinical_choices_generated": False,
            "clinical_packet_sha256": clinical["packet_sha256"], "clinical_response_sha256": clinical["response_sha256"],
            "human_signoff_complete": clinical["human_signoff_complete"],
            "technical_manifest_sha256": technical["technical_manifest_sha256"],
            "candidate_panel_targets": targets, "policies": drafts, "raw_field_decisions": raw_records,
            "operational_raw_authorities": {"lvef": "SEPARATE_EXACT_CASE_SENSITIVE_NUMERIC_MEDIAN"},
            "new_test_performance_used": False, "source_arrays_read": False,
            "absence_of_registry_edge_implies_independence": False}


def prepare_review(*, inputs_path: Path, metadata_root: Path, review_rows_path: Path, mapping_path: Path,
                   packet_dir: Path, output_dir: Path, registry_path: Path, clinical_registry_path: Path) -> dict[str, Any]:
    safe_policy, _ = load_policy(Path(__file__).resolve().parents[1] / "configs/lvef_multitask_safe_export_policy.yaml")
    output_dir = bind_approved_restricted_path(output_dir, policy=safe_policy, must_exist=False,
                                               expect="directory", root_kind="direct")
    require(not output_dir.exists(), "REVIEW_OUTPUT_ALREADY_EXISTS")
    input_bytes = private_bytes(inputs_path)
    inputs = decode(input_bytes)
    require(inputs.get("source_checksums") == SOURCE_HASHES, "REVIEW_INPUT_SOURCE_SCOPE_MISMATCH")
    review_bytes = readiness._bytes(review_rows_path)
    expected = {"clinical_review_rows": digest(review_bytes), "raw_canonical_mapping": SOURCE_HASHES["mapping"],
                "structured_measurements": SOURCE_HASHES["structured"], "selected_studies": SOURCE_HASHES["selected"],
                "subject_split_map": SOURCE_HASHES["split"]}
    technical = prepare_technical_decisions(metadata_root, expected)
    clinical = readiness.inspect_clinician_packet(packet_dir, review_rows_path)
    manifest = decode(readiness._bytes(metadata_root / "aggregate/technical_metadata/technical_metadata_manifest.json"))
    evidence_name = "technical_metadata_evidence_restricted.csv"
    evidence_ref = next(row for row in manifest["output_checksums"] if row["relative_name"] == evidence_name)
    evidence = _csv(metadata_root / "restricted/technical_metadata" / evidence_name, evidence_ref["sha256"])
    draft = grouped_review(inputs, _csv(mapping_path, SOURCE_HASHES["mapping"]), evidence,
                           _csv(registry_path), _csv(clinical_registry_path), clinical=clinical, technical=technical)
    draft["input_receipt_sha256"] = digest(input_bytes)
    draft["source_sha256"] = {"mapping": SOURCE_HASHES["mapping"], "registry": digest(readiness._bytes(registry_path)),
                               "clinical_registry": digest(readiness._bytes(clinical_registry_path)), "review_rows": digest(review_bytes)}
    output_dir.mkdir(mode=0o700)
    technical_sha = publish(output_dir / "technical_decisions.restricted.json", technical)
    draft["technical_decisions_sha256"] = technical_sha
    draft_sha = publish(output_dir / "panel_dependency_review_draft.restricted.json", draft)
    return {"status": "PASS_RESTRICTED_REVIEW_PREPARATION", "technical_dispositions": 9,
            "candidate_targets": len(draft["policies"]), "human_questions_pending": clinical["n_pending_questions"],
            "final_approval": False, "clinical_choices_generated": False, "technical_decisions_sha256": technical_sha,
            "draft_sha256": draft_sha, "raw_metadata_exported": False, "source_arrays_read": False}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("inputs", "metadata-root", "review-rows", "mapping", "packet-dir", "output-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    root = Path(__file__).resolve().parents[1]
    parser.add_argument("--registry", type=Path, default=root / "docs/lvef_multitask/target_dependency_registry.csv")
    parser.add_argument("--clinical-registry", type=Path, default=root / "docs/lvef_multitask/target_dependency_registry_clinical_draft.csv")
    args = parser.parse_args()
    try:
        result = prepare_review(inputs_path=args.inputs, metadata_root=args.metadata_root, review_rows_path=args.review_rows,
                                mapping_path=args.mapping, packet_dir=args.packet_dir, output_dir=args.output_dir,
                                registry_path=args.registry, clinical_registry_path=args.clinical_registry)
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception as exc:
        code = getattr(exc, "code", "REVIEW_PREPARATION_INVALID")
        print(json.dumps({"status": "BLOCKED_REVIEW_PREPARATION", "failure_code": code}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
