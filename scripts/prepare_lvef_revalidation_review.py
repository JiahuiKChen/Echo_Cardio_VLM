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
from build_target_dependency_registry import STRICT_TARGETS

REVIEWED_TECHNICAL_MANIFEST_SHA256 = "4e6c7ab3e957cfb5a369739021e2c71a41400a35103e7a77aa4bce2080e7b22a"
REVIEWED_INPUT_SHA256 = "b82fe7d4a7c3f3cb8aed57038af409d7861aa1b09130292052f0c330cebae4de"
REVIEWED_TECHNICAL_DECISIONS_SHA256 = "427fd59fe75b4985cb2556e94a6b949af1edeabe98282bac0e0eab1c2d28a975"
REVIEWED_REGISTRY_SHA256 = "33b0b91619c02e90cb067d63ddbacb46326145771a965c1d7a5b9cc283aa6752"
REVIEWED_CLINICAL_REGISTRY_SHA256 = "974aa61a6b7c746c5f0411152535fb60a4dd4998eb1c8a3ae873e6ef7949da14"

# Positive measurement definitions, independently of gaps in the old edge table.
# Independence below means a separately defined measurement, not statistical
# independence, proven acquisition provenance, or absence of physiological association.
MEASUREMENT_DEFINITIONS = {
    "lvef": "exact-name reported left-ventricular ejection fraction",
    "arch_diam": "transverse aortic-arch linear diameter",
    "ascending_aorta_diameter": "tubular ascending-aortic linear diameter",
    "av_pk_vel": "aortic-valve peak blood-flow velocity",
    "inf_lat_thickness": "end-diastolic inferolateral/posterior wall thickness, one construct",
    "ivc_diam": "end-expiratory inferior-vena-cava diameter without collapse or RAP inference",
    "la_4ch_length": "left-atrial four-chamber linear length",
    "la_dimen": "parasternal-long-axis anteroposterior left-atrial dimension at LV end systole",
    "lat_e_prime": "lateral mitral-annular early-diastolic tissue velocity",
    "left_ventricular_end_diastolic_diameter": "left-ventricular end-diastolic linear diameter",
    "left_ventricular_end_systolic_diameter": "left-ventricular end-systolic linear diameter",
    "lvot_diam": "left-ventricular-outflow-tract linear diameter",
    "lvot_vti": "left-ventricular-outflow-tract velocity-time integral, a distance",
    "mv_peak_a": "mitral inflow late-diastolic peak blood-flow velocity",
    "mv_peak_e": "mitral inflow early-diastolic peak blood-flow velocity from its compatible source alone",
    "ra_length": "right-atrial linear length",
    "rv_diam": "right-ventricular linear diameter",
    "sept_e_prime": "septal mitral-annular early-diastolic tissue velocity",
    "septal_thickness": "interventricular septal wall thickness",
    "sinus_diam": "sinus-of-Valsalva linear diameter",
    "tricuspid_annular_plane_systolic_excursion": "tricuspid-annular longitudinal systolic excursion",
    "tricuspid_regurgitant_peak_velocity": "peak tricuspid-regurgitant blood-flow velocity",
    "resting_hr": "recorded resting heart rate, not cardiac output",
    "resting_sbp": "recorded resting systolic blood pressure, not pulse pressure or mean arterial pressure",
    "resting_dbp": "recorded resting diastolic blood pressure, not pulse pressure or mean arterial pressure",
}

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


def finalized_records(inputs: dict[str, Any], mapping: pd.DataFrame, evidence: pd.DataFrame,
                      registry: pd.DataFrame, clinical_registry: pd.DataFrame, *,
                      clinical: dict[str, Any], technical: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    """Apply the fixed expert operational review and positive measurement definitions.

    This constructs a new authority from source evidence; it does not mutate or
    approve the old UNREVIEWED draft. Unknown and derived-only fields stay out.
    """
    from build_lvef_clinician_signoff_packet import OWNER_RELAY_Q6_PROCESSING, clinical_review_provenance, OWNER_RELAY_DECISIONS, CLINICAL_ISSUE_IDS
    require(clinical.get("status") == "PASS_OWNER_RELAYED_QUALIFIED_ECHO_REVIEW"
            and clinical.get("clinical_adjudication_complete") is True
            and clinical.get("human_signoff_complete") is False
            and clinical.get("review_mode") == "OWNER_RELAYED_QUALIFIED_ECHO_REVIEW"
            and clinical.get("n_pending_questions") == 0, "REVIEW_OWNER_RELAYED_ADJUDICATION_REQUIRED")
    require([(r["issue_id"], r["selected_option"]) for r in clinical["questions"]]
            == [(issue, row[0]) for issue, row in zip(CLINICAL_ISSUE_IDS, OWNER_RELAY_DECISIONS)],
            "REVIEW_FIXED_CLINICAL_DECISIONS_MISMATCH")
    targets = inputs["target_names"]
    require(len(targets) == 22 and targets[0] == "lvef"
            and set(targets[1:]) == STRICT_TARGETS, "REVIEW_FIXED_TARGET_SET_MISMATCH")
    require(set(inputs["structured_units"]) == set(inputs["structured_names"]), "REVIEW_RAW_UNIT_UNIVERSE_MISMATCH")
    draft = grouped_review(inputs, mapping, evidence, registry, clinical_registry,
                           clinical=clinical, technical=technical)
    indexed = {(r["target"], r["raw_predictor"]): r for r in draft["raw_field_decisions"]}
    approvals, policies, positives, excluded = {}, [], [], []
    for row in draft["policies"]:
        name = row["name"]
        actual = inputs["targets"][name]
        require(actual["support_floors_passed"] is True and actual["source_raw_fields"]
                and actual["valid_unit_raw_fields"]
                and set(actual["valid_unit_raw_fields"]) <= set(actual["source_raw_fields"]),
                "REVIEW_TARGET_SOURCE_OR_SUPPORT_INVALID")
        candidates = {raw for group in row["candidate_positive_groups"].values() for raw in group}
        allowed = []
        for raw in inputs["structured_names"]:
            record = indexed[name, raw]
            canonicals = record["canonical_names"]
            canonical_name = canonicals[0] if len(canonicals) == 1 else None
            explicit = (raw in candidates and canonical_name in MEASUREMENT_DEFINITIONS
                        and canonical_name != "lvef")
            if explicit and canonical_name in inputs["targets"]:
                source_policy = inputs["targets"][canonical_name]
                explicit = (raw in source_policy["valid_unit_raw_fields"]
                            and inputs["structured_units"][raw] == source_policy["unit"])
            elif explicit:
                explicit = inputs["structured_units"][raw] == {
                    "resting_hr": "bpm", "resting_sbp": "mmhg", "resting_dbp": "mmhg"}[canonical_name]
            if explicit:
                allowed.append(raw)
                positives.append({"target": name, "raw_predictor": raw, "disposition": "INDEPENDENT_ALLOWED",
                    "evidence_sha256": technical["technical_manifest_sha256"],
                    "rationale": (
                        f"Target: {MEASUREMENT_DEFINITIONS[name]}. Context: {MEASUREMENT_DEFINITIONS[canonical_name]}. "
                        "The exact unambiguous source has compatible declared units and represents a separately "
                        "defined measurement in a distinct reviewed family. Target/alias, shared formula, "
                        "method-dependent shortcut, joint family, and technical exclusions were applied first. "
                        "INDEPENDENT_ALLOWED means non-shortcut measurement context, not statistical independence "
                        "or verified acquisition conventions for every source examination.")})
            else:
                excluded.append({"target": name, "raw_predictor": raw,
                    "disposition": record["proposed_disposition"] if raw not in candidates else "EXCLUDE_NO_COMPATIBLE_POSITIVE_MEASUREMENT_AUTHORITY",
                    "evidence_sha256": technical["technical_manifest_sha256"]})
        require(bool(allowed), "REVIEW_NO_POSITIVELY_SUPPORTED_CONTEXT")
        masks = row["masks"]
        masks["family_fields"] = sorted(set(masks["family_fields"]) | {
            raw for raw in inputs["structured_names"]
            if set(indexed[name, raw]["source_families"]) & set(row["family_candidates"])})
        policy = {"name": name, "unit": actual["unit"], "family": "__".join(row["family_candidates"]),
                  "allowed_predictors": sorted(allowed),
                  **{key: sorted(masks[key]) for key in ("exact_target_fields", "aliases", "deterministic_fields", "near_deterministic_fields", "family_fields")},
                  "dependencies_resolved": True, "row_fingerprints": actual["row_fingerprints"],
                  "support_counts": {s: actual["counts"][s] for s in ("train", "val", "test")},
                  "binary_class_counts": actual["binary_class_counts"], "null_reference": None}
        policies.append(policy)
        expert_scoped = any(name in issue["targets"] for issue in CLINICAL_ISSUE_SPECS)
        label_status = ("EXPERT_ADJUDICATED_OPERATIONAL_DEFINITION_WITH_LIMITATION" if expert_scoped
                        else "TECHNICALLY_REVIEWED_OPERATIONAL_DEFINITION_WITH_LIMITATION")
        evidence_strength = ("EXPERT_ENDORSED_GUIDELINE_INFORMED_OPERATIONAL_INTERPRETATION" if expert_scoped
                             else "PROJECT_METADATA_AND_TECHNICAL_PROCESSING_REVIEW")
        limitations = ["Individual source acquisition conventions were not verified; the operational definition follows the recorded field and the stated review scope.",
                       "Physiological association and report-level correlation may remain after prohibited-shortcut masking."]
        if name == "lvef":
            label_status = "OPERATIONAL_DEFINITION_WITH_LIMITATION"
            evidence_strength = "SEPARATE_EXACT_NAME_LABEL_AUTHORITY_WITH_LIMITATION"
            limitations = ["Exact-name operational analytical EF scale; native source unit is unverified.",
                           "Measurement method is unspecified and unstratifiable; actual method mixture is unknown."]
        if name == "inf_lat_thickness":
            limitations.append("Posterior wall is a nomenclature synonym for this single inferolateral construct; unrelated exports are not merged.")
        if name == "mv_peak_e":
            limitations.append("Probable same construct as mitral_e_velocity is only a hypothesis; the ms source is excluded, with no merge or time-to-velocity conversion.")
        approvals[name] = {"unit": actual["unit"], "source_raw_fields": actual["source_raw_fields"],
            "valid_unit_raw_fields": actual["valid_unit_raw_fields"], "aggregation_rule": actual["aggregation_rule"],
            "construct_id": "END_DIASTOLIC_INFEROLATERAL_POSTERIOR_WALL" if name == "inf_lat_thickness" else name,
            "label_definition_status": label_status,
            "rationale": (MEASUREMENT_DEFINITIONS[name] + ". Retain the exact source set and within-selected-report aggregation from the unchanged input authority; only its compatible numeric source rows supply labels."),
            "evidence_strength": evidence_strength,
            "source_acquisition_conventions_verified": False, "limitations": limitations}
    provenance = clinical_review_provenance(clinical)
    common = {"clinical_response_sha256": clinical["response_sha256"],
              "clinical_review_provenance": provenance,
              "technical_decisions_sha256": REVIEWED_TECHNICAL_DECISIONS_SHA256,
              "input_receipt_sha256": REVIEWED_INPUT_SHA256, "new_test_performance_used": False}
    panel = {**common, "artifact_type": "lvef_revalidation_panel_v1", "status": "APPROVED_CLINICAL_PANEL",
             "strict_targets": targets[1:], "target_approvals": approvals,
             "mitral_e_processing": json.loads(json.dumps(OWNER_RELAY_Q6_PROCESSING)),
             "excluded_targets": ["mitral_e_velocity", "fs", "tr_mmhg", "body_surface_area", "height_cm", "resting_hr", "resting_sbp", "resting_dbp"],
             "lvef_is_separate_anchor": True, "non_lvef_clinical_margins": "UNRESOLVED_NOT_REQUIRED_FOR_ERROR_INTERVAL_REPORTING"}
    policy_fields = ("name", "unit", "family", "allowed_predictors", "exact_target_fields", "aliases", "deterministic_fields", "near_deterministic_fields", "family_fields", "dependencies_resolved")
    dependencies = {**common, "artifact_type": "lvef_revalidation_dependencies_v1", "status": "APPROVED_REVIEWED_DEPENDENCIES",
        "policies": [{k: p[k] for k in policy_fields} for p in policies],
        "reviewed_predictor_universe": sorted(inputs["structured_names"]), "predictor_decisions": positives,
        "excluded_raw_decisions": excluded, "technical_manifest_sha256": technical["technical_manifest_sha256"],
        "positive_definition_scope": MEASUREMENT_DEFINITIONS,
        "family_identifier_separator": "__", "family_masks_use_all_memberships": True,
        "absence_of_registry_edge_implies_independence": False,
        "statistical_independence_claimed": False, "source_arrays_read": False}
    return panel, dependencies, policies


def finalize_review(*, inputs_path: Path, metadata_root: Path, review_rows_path: Path, mapping_path: Path,
                    packet_dir: Path, owner_relayed_response_path: Path, technical_decisions_path: Path,
                    output_dir: Path, registry_path: Path, clinical_registry_path: Path) -> dict[str, Any]:
    """Publish the fixed reviewed authorities without fitting or numerical input changes."""
    safe_policy, _ = load_policy(Path(__file__).resolve().parents[1] / "configs/lvef_multitask_safe_export_policy.yaml")
    output_dir = bind_approved_restricted_path(output_dir, policy=safe_policy, must_exist=False,
                                              expect="directory", root_kind="direct")
    require(not output_dir.exists(), "REVIEW_OUTPUT_ALREADY_EXISTS")
    input_bytes, technical_bytes = private_bytes(inputs_path), private_bytes(technical_decisions_path)
    require(digest(input_bytes) == REVIEWED_INPUT_SHA256, "REVIEW_FIXED_INPUT_HASH_MISMATCH")
    require(digest(technical_bytes) == REVIEWED_TECHNICAL_DECISIONS_SHA256, "REVIEW_FIXED_TECHNICAL_HASH_MISMATCH")
    inputs, technical = decode(input_bytes), decode(technical_bytes)
    expected = {"clinical_review_rows": digest(readiness._bytes(review_rows_path)),
                "raw_canonical_mapping": SOURCE_HASHES["mapping"], "structured_measurements": SOURCE_HASHES["structured"],
                "selected_studies": SOURCE_HASHES["selected"], "subject_split_map": SOURCE_HASHES["split"]}
    require(technical == prepare_technical_decisions(metadata_root, expected), "REVIEW_FIXED_TECHNICAL_RECORD_CHANGED")
    clinical = readiness.inspect_clinician_packet(packet_dir, review_rows_path,
                                                 owner_relayed_response_path=owner_relayed_response_path)
    manifest = decode(readiness._bytes(metadata_root / "aggregate/technical_metadata/technical_metadata_manifest.json"))
    evidence_name = "technical_metadata_evidence_restricted.csv"
    evidence_sha = next(row["sha256"] for row in manifest["output_checksums"] if row["relative_name"] == evidence_name)
    panel, dependencies, policies = finalized_records(inputs, _csv(mapping_path, SOURCE_HASHES["mapping"]),
        _csv(metadata_root / "restricted/technical_metadata" / evidence_name, evidence_sha),
        _csv(registry_path, REVIEWED_REGISTRY_SHA256),
        _csv(clinical_registry_path, REVIEWED_CLINICAL_REGISTRY_SHA256), clinical=clinical, technical=technical)
    for record in (panel, dependencies):
        record["source_bindings"] = {"mapping_sha256": SOURCE_HASHES["mapping"],
            "registry_sha256": digest(readiness._bytes(registry_path)),
            "clinical_registry_sha256": digest(readiness._bytes(clinical_registry_path)),
            "implementation_sha256": digest(readiness._bytes(Path(__file__).resolve()))}
    output_dir.mkdir(mode=0o700)
    panel_sha = publish(output_dir / "panel.restricted.json", panel)
    dependency_sha = publish(output_dir / "dependencies.restricted.json", dependencies)
    policy_sha = publish(output_dir / "policy_specification.restricted.json", {
        "artifact_type": "lvef_revalidation_policy_specification_v1", "status": "PASS_FINALIZED_POLICY_PREPARATION",
        "panel_sha256": panel_sha, "dependencies_sha256": dependency_sha, "input_receipt_sha256": REVIEWED_INPUT_SHA256,
        "strict_panel": panel["strict_targets"], "policies": policies, "new_test_performance_used": False})
    return {"status": "PASS_FINALIZED_PANEL_AND_DEPENDENCIES", "strict_target_count": len(panel["strict_targets"]),
            "separate_lvef_anchor": True, "positive_raw_decisions": len(dependencies["predictor_decisions"]),
            "excluded_raw_decisions": len(dependencies["excluded_raw_decisions"]),
            "panel_sha256": panel_sha, "dependencies_sha256": dependency_sha, "policy_specification_sha256": policy_sha,
            "source_arrays_read": False, "numerical_inputs_changed": False, "new_test_performance_used": False}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("inputs", "metadata-root", "review-rows", "mapping", "packet-dir", "output-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    root = Path(__file__).resolve().parents[1]
    parser.add_argument("--registry", type=Path, default=root / "docs/lvef_multitask/target_dependency_registry.csv")
    parser.add_argument("--clinical-registry", type=Path, default=root / "docs/lvef_multitask/target_dependency_registry_clinical_draft.csv")
    parser.add_argument("--finalize", action="store_true")
    parser.add_argument("--owner-relayed-response", type=Path)
    parser.add_argument("--technical-decisions", type=Path)
    args = parser.parse_args()
    try:
        parameters = dict(inputs_path=args.inputs, metadata_root=args.metadata_root, review_rows_path=args.review_rows,
                          mapping_path=args.mapping, packet_dir=args.packet_dir, output_dir=args.output_dir,
                          registry_path=args.registry, clinical_registry_path=args.clinical_registry)
        if args.finalize:
            require(args.owner_relayed_response is not None and args.technical_decisions is not None,
                    "REVIEW_FINALIZATION_BINDINGS_REQUIRED")
            result = finalize_review(**parameters, owner_relayed_response_path=args.owner_relayed_response,
                                     technical_decisions_path=args.technical_decisions)
        else:
            require(args.owner_relayed_response is None and args.technical_decisions is None, "REVIEW_DRAFT_ROUTE_ARGUMENT_MISMATCH")
            result = prepare_review(**parameters)
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception as exc:
        code = getattr(exc, "code", "REVIEW_PREPARATION_INVALID")
        print(json.dumps({"status": "BLOCKED_REVIEW_PREPARATION", "failure_code": code}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
