"""Versioned analysis authorities that consume, and never reopen, completed C3.

Owner permission, clinical adjudication, an analysis lock, and test release are
separate records. The C3 producer's commit is historical input provenance, not
the required HEAD of a subsequent analysis controller.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import importlib
import importlib.metadata
from datetime import datetime, timezone
from typing import Any, Mapping


C3_COMMIT = "cda842d04cc18eb3669ad5377c31a6e955fc43cb"
C3_SCIENTIFIC_COMMIT = "e1cdb674ada23bbc9f3a1ff77c33927bd324d3ed"
C3_ATTEMPT = "lvef_c3_full_904d0ab65f003c1e_e1cdb674"
C3_PLAN = "904d0ab65f003c1eb68adeee8c0b1dd786ec7a9ef4bb496b646b22cc7a540247"
C3_COMPLETION = "48ab39b48fb95e8257ab07c076a587151eeb993dbe638ce7d6908d3be7168605"
OWNER_REQUEST = "f8d3285a2615af4321a416f4850e078a9b5180708c43fe7ff2c87a58a5849d8d"
SCOPES = ("MODEL_INDEPENDENT_INPUT_PREPARATION", "TRAIN_VALIDATION_FITTING_AFTER_ANALYSIS_LOCK",
          "FIXED_TEST_EVALUATION_AFTER_MODEL_FREEZE", "AGGREGATE_ASA_MANUSCRIPT_PREPARATION")
REQUIRED_GATES = ("clinical_signoff", "technical_adjudication", "panel_and_dependencies",
                  "common_inputs", "environment", "safety", "synthetic_validation")
REQUIRED_SOURCE_PATHS = {
    "sap": "docs/lvef_multitask/statistical_analysis_plan.md",
    "config": "configs/lvef_multitask_revalidation.yaml",
    "engine": "scripts/lvef_revalidation_analysis.py", "controller": "scripts/run_lvef_revalidation.py",
    "authority": "scripts/lvef_revalidation_authority.py", "inference": "scripts/lvef_revalidation_inference.py",
    "inputs": "scripts/prepare_lvef_revalidation_inputs.py", "readiness": "scripts/audit_lvef_analysis_readiness.py",
    "renderer": "scripts/render_lvef_revalidation_results.py",
    "safety_policy": "configs/lvef_multitask_safe_export_policy.yaml",
    "clinician_validator": "scripts/build_lvef_clinician_signoff_packet.py",
    "analysis_modes": "scripts/lvef_multitask_analysis_modes.py",
    "audit_utils": "scripts/lvef_multitask_audit_utils.py",
    "technical_metadata": "scripts/audit_lvef_multitask_technical_metadata.py",
    "target_panel": "scripts/build_multitask_target_panel.py",
    "clinical_metadata": "scripts/lvef_multitask_clinical_metadata.py",
    "review": "scripts/prepare_lvef_revalidation_review.py",
    "dependency_registry_builder": "scripts/build_target_dependency_registry.py",
    "owner_relay_source": "docs/lvef_multitask/revalidation_2026-09-09/owner_relayed_review_source_2026_09_09.txt",
    "stage_chain": "scripts/lvef_revalidation_stage_chain.py",
}


class AuthorityError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def require(condition: bool, code: str) -> None:
    if not condition:
        raise AuthorityError(code)


def canonical(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in items:
        require(key not in result, "ANALYSIS_DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def decode(data: bytes) -> dict[str, Any]:
    value = json.loads(data, object_pairs_hook=_pairs,
                       parse_constant=lambda _: (_ for _ in ()).throw(AuthorityError("ANALYSIS_NONFINITE_JSON")))
    require(type(value) is dict, "ANALYSIS_JSON_OBJECT_REQUIRED")
    return value


def private_bytes(path: Path, maximum: int = 64 * 1024 * 1024) -> bytes:
    """Read owner-private regular control bytes with stable file identity."""
    require(path.is_absolute() and not any(p.is_symlink() for p in (path, *path.parents)),
            "ANALYSIS_SYMLINK_OR_RELATIVE_PATH")
    parent = path.parent.stat()
    require(parent.st_uid == os.getuid() and stat.S_IMODE(parent.st_mode) in (0o700, 0o2700),
            "ANALYSIS_PRIVATE_PARENT_REQUIRED")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        require(stat.S_ISREG(before.st_mode) and before.st_uid == os.getuid()
                and stat.S_IMODE(before.st_mode) == 0o600 and before.st_nlink == 1
                and 0 < before.st_size <= maximum, "ANALYSIS_PRIVATE_FILE_INVALID")
        data = stream.read(maximum + 1)
        after = os.fstat(stream.fileno())
    fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns", "st_mode", "st_uid", "st_nlink")
    identity = lambda s: tuple(getattr(s, k) for k in fields)
    require(identity(before) == identity(after) == identity(path.lstat()) and len(data) == before.st_size,
            "ANALYSIS_FILE_CHANGED")
    return data


def read_bound(path: Path, sha256: str) -> dict[str, Any]:
    require(re.fullmatch(r"[0-9a-f]{64}", sha256) is not None, "ANALYSIS_HASH_REQUIRED")
    body = private_bytes(path)
    require(digest(body) == sha256, "ANALYSIS_BOUND_HASH_MISMATCH")
    return decode(body)


def publish(path: Path, value: Mapping[str, Any]) -> str:
    """Exclusive private publication, with no hardlink metadata window."""
    require(path.is_absolute() and not any(p.is_symlink() for p in (path, *path.parents)),
            "ANALYSIS_PUBLICATION_PATH_INVALID")
    info = path.parent.stat()
    require(info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) in (0o700, 0o2700),
            "ANALYSIS_PRIVATE_PARENT_REQUIRED")
    body = canonical(value)
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), "wb") as stream:
        stream.write(body)
        stream.flush()
        os.fsync(stream.fileno())
    require(private_bytes(path) == body, "ANALYSIS_PUBLICATION_READBACK_FAILED")
    return digest(body)


def consume_c3(completion_path: Path, aggregate_path: Path, cohort_receipt_path: Path) -> dict[str, Any]:
    """Validate the small completion bindings; do not repeat the 42-artifact replay."""
    receipt = read_bound(completion_path, C3_COMPLETION)
    require(receipt.get("artifact_type") == "r7hb_derived_completion_handoff_v1"
            and receipt.get("record_role") == "DERIVED_NONAUTHORIZING_HANDOFF"
            and receipt.get("status") == "PASS_PRODUCTION_C3_FINALIZED"
            and receipt.get("implementation_commit") == C3_COMMIT
            and receipt.get("scientific_commit") == C3_SCIENTIFIC_COMMIT
            and receipt.get("attempt_id") == C3_ATTEMPT and receipt.get("batch_plan_sha256") == C3_PLAN,
            "ANALYSIS_C3_COMPLETION_BINDING_INVALID")
    artifacts = receipt["canonical_artifacts"]
    summary = read_bound(aggregate_path, artifacts["aggregate"]["sha256"])
    cohort = read_bound(cohort_receipt_path, artifacts["cohort_preservation_receipt"]["sha256"])
    require(summary.get("status") == "PASS_PRODUCTION_C3_FINALIZED"
            and summary.get("r8u_r7h_implementation_commit") == C3_COMMIT,
            "ANALYSIS_C3_AGGREGATE_INVALID")
    expected = {"selected_studies": 4530, "selected_subjects": 4530,
                "verified_source_objects": 335984, "selected_source_bytes": 1216569133322,
                "clip_embeddings": 184570, "pooled_imaging_eligible_studies": 4525,
                "no_cine_studies": 5, "new_no_cine_studies": 0}
    require(all(summary.get(k) == v == receipt["counts"].get(k) for k, v in expected.items())
            and summary.get("production_batches") == receipt.get("production_batches") == 19
            and summary.get("implementation_authority_epoch_count") == receipt.get("implementation_authority_epoch_count") == 4,
            "ANALYSIS_C3_COUNTS_INVALID")
    require(all(summary.get(k) == receipt.get(k) == 0 for k in (
        "model_fitting_count", "endpoint_prediction_count", "confirmatory_performance_access_count")),
        "ANALYSIS_C3_SCOPE_INVALID")
    require(cohort.get("attempt_id") == C3_ATTEMPT and cohort.get("batch_plan_sha256") == C3_PLAN
            and cohort.get("batch_receipt_set_sha256") == summary.get("batch_receipt_set_sha256")
            and len(cohort.get("artifacts", [])) == 42, "ANALYSIS_C3_PRESERVATION_BINDING_INVALID")
    return {"status": "PASS_C3_ANALYSIS_INPUT_AUTHORITY", "completion_sha256": C3_COMPLETION,
            "producer_commit": C3_COMMIT, "scientific_commit": C3_SCIENTIFIC_COMMIT,
            "attempt_id": C3_ATTEMPT, "plan_sha256": C3_PLAN, "counts": expected,
            "canonical_artifacts": artifacts, "cohort_inventory": cohort["artifacts"],
            "prespecified_no_cine_study_set_sha256": cohort["prespecified_no_cine_study_set_sha256"],
            "reconstruction_reopened": False, "full_preservation_replays": 0}


def owner_authorization(*, request_sha256: str, authorization_date: str, source_reference: str) -> dict[str, Any]:
    require(request_sha256 == OWNER_REQUEST, "ANALYSIS_OWNER_REQUEST_MISMATCH")
    require(re.fullmatch(r"20\d{2}-\d{2}-\d{2}", authorization_date) is not None,
            "ANALYSIS_OWNER_DATE_INVALID")
    require(bool(source_reference.strip()), "ANALYSIS_OWNER_SOURCE_REQUIRED")
    return {"schema_version": 1, "artifact_type": "lvef_revalidation_owner_authorization_v1",
            "status": "OWNER_AUTHORIZATION_RECORDED_CONDITIONAL_ON_ANALYSIS_LOCK",
            "owner_authorized": True, "owner_authorization_date": authorization_date,
            "owner_request_sha256": request_sha256, "source_reference": source_reference,
            "c3_completion_sha256": C3_COMPLETION, "scopes": list(SCOPES),
            "clinical_signoff_substituted": False, "new_cohort_or_encoder_run_authorized": False,
            "poster_upload_or_messages_authorized": False}


def validate_owner(value: Mapping[str, Any]) -> None:
    expected = owner_authorization(request_sha256=OWNER_REQUEST,
        authorization_date=str(value.get("owner_authorization_date", "")),
        source_reference=str(value.get("source_reference", "")))
    require(canonical(value) == canonical(expected), "ANALYSIS_OWNER_AUTHORITY_INVALID")


def _binding(path: Path) -> dict[str, Any]:
    data = private_bytes(path)
    return {"path": str(path), "sha256": digest(data), "size_bytes": len(data)}


def source_guard(repository_root: Path, expected_commit: str, source_files: Mapping[str, Path]) -> dict[str, str]:
    """Bind clean tracked source bytes to the exact analysis HEAD, not C3 HEAD."""
    require(re.fullmatch(r"[0-9a-f]{40}", expected_commit) is not None, "ANALYSIS_COMMIT_INVALID")
    root = repository_root.resolve(strict=True)
    def git(*args: str) -> bytes:
        result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, check=False)
        require(result.returncode == 0, "ANALYSIS_GIT_AUTHORITY_UNAVAILABLE")
        return result.stdout
    require(git("rev-parse", "HEAD").decode().strip() == expected_commit, "ANALYSIS_CURRENT_COMMIT_MISMATCH")
    require(not git("status", "--porcelain", "--untracked-files=no").strip(), "ANALYSIS_TRACKED_TREE_DIRTY")
    fixed = REQUIRED_SOURCE_PATHS
    require(set(fixed) <= set(source_files), "ANALYSIS_SOURCE_BINDINGS_REQUIRED")
    require(all(source_files[role] == root / relative for role, relative in fixed.items()), "ANALYSIS_SOURCE_ROLE_SUBSTITUTION")
    hashes = {}
    for role, path in source_files.items():
        require(path.is_absolute() and not any(x.is_symlink() for x in (path, *path.parents)), "ANALYSIS_SOURCE_PATH_INVALID")
        try:
            relative = path.relative_to(root).as_posix()
        except ValueError as exc:
            raise AuthorityError("ANALYSIS_SOURCE_OUTSIDE_REPOSITORY") from exc
        body = path.read_bytes()
        require(git("show", f"{expected_commit}:{relative}") == body, "ANALYSIS_SOURCE_NOT_CURRENT_TRACKED_BYTES")
        hashes[role] = digest(body)
    return hashes


def environment_observation() -> dict[str, Any]:
    packages = {}
    for name in ("numpy", "scipy", "sklearn", "pandas", "yaml"):
        module = importlib.import_module(name)
        packages[name] = {"version": str(module.__version__), "entry_sha256": digest(Path(module.__file__).read_bytes())}
    return {"artifact_type": "lvef_revalidation_environment_v1", "python_version": sys.version,
            "python_executable_sha256": digest(Path(sys.executable).resolve().read_bytes()),
            "packages": packages,
            "thread_environment": {key: os.environ.get(key) for key in (
                "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS")}}


def _read_spec(spec_path: Path) -> tuple[dict[str, Any], str]:
    import lvef_revalidation_analysis as engine
    body = private_bytes(spec_path)
    value = decode(body)
    spec = engine.spec_from_payload(value)
    engine.validate_spec(spec)
    require(engine.canonical_bytes(value) == body and spec.sha256 == digest(body), "ANALYSIS_SPEC_NOT_CANONICAL")
    require(spec.bindings["source_completion"] == C3_COMPLETION, "ANALYSIS_SOURCE_COMPLETION_MISMATCH")
    return value, digest(body)


def _reference(path: str | Path) -> dict[str, Any]:
    return _binding(Path(path))


def _hash_is_valid(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _manifest_files_unchanged(manifest: Mapping[str, Any]) -> None:
    """Hash immutable split bytes without parsing a held-out array or label."""
    require(set(manifest["split_artifacts"]) == {"train", "val", "test"}, "ANALYSIS_INPUT_SPLITS_INVALID")
    for split in manifest["split_artifacts"].values():
        require(set(split) == {"rows", "arrays"}, "ANALYSIS_SPLIT_ARTIFACT_SET_INVALID")
        for ref in split.values():
            observed = _reference(ref["path"])
            require(observed == ref, "ANALYSIS_INPUT_ARTIFACT_CHANGED")


def create_gate(name: str, *, spec_path: Path, parameters: Mapping[str, Any]) -> dict[str, Any]:
    """Derive a gate from replayed evidence, never from caller-supplied PASS flags."""
    import audit_lvef_analysis_readiness as readiness
    spec, spec_sha = _read_spec(spec_path)
    require(name in REQUIRED_GATES, "ANALYSIS_GATE_UNKNOWN")
    refs, proof = {}, {}
    def bound(role: str, path: str) -> dict[str, Any]:
        refs[role] = _reference(path)
        return read_bound(Path(path), refs[role]["sha256"])
    if name == "clinical_signoff":
        relay_mode = "owner_relayed_response_path" in parameters
        require(set(parameters) == ({"packet_dir", "review_rows_path", "owner_relayed_response_path"} if relay_mode
                                    else {"packet_dir", "review_rows_path"}), "ANALYSIS_CLINICAL_PARAMETERS_INVALID")
        packet = Path(parameters["packet_dir"])
        for role, filename in (("packet", "clinical_metadata_clinician_signoff_restricted.md"),
                               ("response", "clinical_metadata_clinician_response_restricted.json"),
                               ("manifest", "clinical_metadata_clinician_packet_manifest_restricted.json")):
            refs[role] = _reference(packet / filename)
        refs["review_rows"] = _reference(parameters["review_rows_path"])
        if relay_mode:
            refs["original_response"] = refs["response"]
            refs["response"] = _reference(parameters["owner_relayed_response_path"])
        proof = readiness.inspect_clinician_packet(packet, Path(parameters["review_rows_path"]),
            **({"owner_relayed_response_path": Path(parameters["owner_relayed_response_path"])} if relay_mode else {}))
        accepted = (proof["status"] == "PASS_OWNER_RELAYED_QUALIFIED_ECHO_REVIEW"
                    and proof.get("clinical_adjudication_complete") is True and proof["human_signoff_complete"] is False
                    and proof.get("input_audit_sha256") == spec["bindings"]["input_audit"]) if relay_mode else (
                    proof["status"] == "PASS_CLINICIAN_SIGNOFF" and proof["human_signoff_complete"] is True)
        require(accepted
                and proof["n_questions"] == 8 and proof["n_validation_issues"] == 0
                and proof["metadata_packet_regenerated_exactly"] is True, "ANALYSIS_CLINICAL_SIGNOFF_REQUIRED")
        require(all(proof[role + "_sha256"] == refs[role]["sha256"] for role in refs),
                "ANALYSIS_CLINICAL_SOURCE_BINDING_CHANGED")
    elif name == "technical_adjudication":
        require(set(parameters) == {"metadata_root", "input_checksums", "decisions_path"}, "ANALYSIS_TECHNICAL_PARAMETERS_INVALID")
        inspected = readiness.inspect_technical_packet(Path(parameters["metadata_root"]), expected_input_checksums=parameters["input_checksums"])
        decisions = bound("decisions", parameters["decisions_path"])
        require(set(decisions) == {"artifact_type", "status", "technical_manifest_sha256", "input_checksums", "decisions", "new_test_performance_used"}
                and decisions["artifact_type"] == "lvef_revalidation_technical_decisions_v1"
                and decisions["status"] == "APPROVED_TECHNICAL_DISPOSITIONS"
                and decisions["technical_manifest_sha256"] == inspected["manifest_sha256"]
                and decisions["input_checksums"] == parameters["input_checksums"]
                and decisions["new_test_performance_used"] is False, "ANALYSIS_TECHNICAL_DECISIONS_INVALID")
        rows = decisions["decisions"]
        require(isinstance(rows, list) and len(rows) == 9
                and {r["issue_id"] for r in rows} == set(readiness.TECHNICAL_ISSUE_IDS), "ANALYSIS_TECHNICAL_ISSUE_SET_INVALID")
        for row in rows:
            require(set(row) == {"issue_id", "disposition", "rationale", "evidence_sha256"}
                    and row["disposition"] in {"RESOLVED_PROJECT_METADATA", "CONSERVATIVE_EXCLUSION", "NOT_RESOLVABLE_EXCLUDED", "OPERATIONAL_DEFINITION_WITH_LIMITATION"}
                    and isinstance(row["rationale"], str) and bool(row["rationale"].strip())
                    and row["evidence_sha256"] == inspected["manifest_sha256"], "ANALYSIS_TECHNICAL_DISPOSITION_INVALID")
        require(all(row["disposition"] != "OPERATIONAL_DEFINITION_WITH_LIMITATION" or row["issue_id"] in {"LVEF_ALIASES", "LVEF_METHOD_MIXTURE"} for row in rows), "ANALYSIS_OPERATIONAL_DEFINITION_SCOPE_INVALID")
        proof = {"prepared_evidence": inspected, "n_dispositioned": len(rows)}
    elif name == "panel_and_dependencies":
        from lvef_multitask_clinical_metadata import ALLOWED_TARGET_SET
        from build_lvef_clinician_signoff_packet import (CLINICAL_ISSUE_SPECS, OWNER_RELAY_MODE,
            OWNER_RELAY_Q6, OWNER_RELAY_Q6_PROCESSING, OWNER_RELAY_EVIDENCE_STRENGTH, clinical_review_provenance)
        require(set(parameters) == {"panel_path", "dependency_path", "clinical_parameters", "technical_parameters"}, "ANALYSIS_PANEL_PARAMETERS_INVALID")
        panel, dependencies = bound("panel", parameters["panel_path"]), bound("dependencies", parameters["dependency_path"])
        clinical = create_gate("clinical_signoff", spec_path=spec_path, parameters=parameters["clinical_parameters"])
        technical = create_gate("technical_adjudication", spec_path=spec_path, parameters=parameters["technical_parameters"])
        require(clinical["gate"] == "clinical_signoff" and technical["gate"] == "technical_adjudication", "ANALYSIS_PANEL_REVIEW_GATE_MISMATCH")
        response_sha = clinical["evidence"]["response"]["sha256"]
        relay_mode = clinical["proof"].get("review_mode") == OWNER_RELAY_MODE
        decision_sha = technical["evidence"]["decisions"]["sha256"]
        require(panel.get("status") == "APPROVED_CLINICAL_PANEL" and panel.get("artifact_type") == "lvef_revalidation_panel_v1"
                and dependencies.get("status") == "APPROVED_REVIEWED_DEPENDENCIES"
                and dependencies.get("artifact_type") == "lvef_revalidation_dependencies_v1"
                and panel.get("strict_targets") == spec["strict_panel"]
                and refs["panel"]["sha256"] == spec["bindings"]["clinical_panel"]
                and refs["dependencies"]["sha256"] == spec["bindings"]["dependency_registry"], "ANALYSIS_PANEL_BINDING_INVALID")
        for item in (panel, dependencies):
            require(item.get("clinical_response_sha256") == response_sha and item.get("technical_decisions_sha256") == decision_sha
                    and item.get("new_test_performance_used") is False, "ANALYSIS_PANEL_REVIEW_BINDING_INVALID")
            if relay_mode:
                require(item.get("clinical_review_provenance") == clinical_review_provenance(clinical["proof"]),
                        "ANALYSIS_OWNER_RELAY_PANEL_PROVENANCE_INVALID")
        fields = ("name", "unit", "family", "allowed_predictors", "exact_target_fields", "aliases", "deterministic_fields", "near_deterministic_fields", "family_fields", "dependencies_resolved")
        expected = [{k: policy[k] for k in fields} for policy in spec["policies"]]
        require(dependencies.get("policies") == expected and set(spec["strict_panel"]) <= ALLOWED_TARGET_SET,
                "ANALYSIS_POSITIVE_TARGET_AUTHORITY_REQUIRED")
        universe = dependencies.get("reviewed_predictor_universe")
        require(isinstance(universe, list) and len(universe) == len(set(universe)) > 0
                and all(isinstance(x, str) and x for x in universe), "ANALYSIS_POSITIVE_PREDICTOR_AUTHORITY_REQUIRED")
        require(all(set(p["allowed_predictors"]) <= set(universe) for p in expected), "ANALYSIS_PREDICTOR_NOT_REVIEWED")
        positive = dependencies.get("predictor_decisions")
        require(isinstance(positive, list), "ANALYSIS_POSITIVE_PREDICTOR_DECISIONS_REQUIRED")
        decision_keys = {(r.get("target"), r.get("raw_predictor")) for r in positive}
        require(len(decision_keys) == len(positive), "ANALYSIS_DUPLICATE_PREDICTOR_DECISION")
        for row in positive:
            require(set(row) == {"target", "raw_predictor", "disposition", "evidence_sha256", "rationale"}
                    and row["disposition"] == "INDEPENDENT_ALLOWED"
                    and row["evidence_sha256"] in {response_sha, technical["proof"]["prepared_evidence"]["manifest_sha256"]}
                    and isinstance(row["rationale"], str) and bool(row["rationale"].strip()), "ANALYSIS_PREDICTOR_INDEPENDENCE_NOT_ESTABLISHED")
        require(decision_keys == {(p["name"], name) for p in expected for name in p["allowed_predictors"]}, "ANALYSIS_POSITIVE_ALLOWLIST_MISMATCH")
        approvals = panel.get("target_approvals")
        require(isinstance(approvals, dict) and set(approvals) == {p["name"] for p in expected}, "ANALYSIS_TARGET_APPROVAL_SET_INVALID")
        expert_reviewed_targets = {target for issue in CLINICAL_ISSUE_SPECS for target in issue["targets"]}
        for policy in expected:
            row = approvals[policy["name"]]
            expert_scoped = policy["name"] in expert_reviewed_targets
            approval_fields = {"unit", "source_raw_fields", "valid_unit_raw_fields", "aggregation_rule", "construct_id", "label_definition_status", "rationale"}
            if relay_mode:
                approval_fields |= {"evidence_strength", "source_acquisition_conventions_verified", "limitations"}
                expected_strength = ("SEPARATE_EXACT_NAME_LABEL_AUTHORITY_WITH_LIMITATION" if policy["name"] == "lvef" else
                    OWNER_RELAY_EVIDENCE_STRENGTH if expert_scoped else "PROJECT_METADATA_AND_TECHNICAL_PROCESSING_REVIEW")
                require(row.get("evidence_strength") == expected_strength
                        and row.get("source_acquisition_conventions_verified") is False
                        and isinstance(row.get("limitations"), list) and row["limitations"]
                        and all(isinstance(x, str) and x.strip() for x in row["limitations"]),
                        "ANALYSIS_OPERATIONAL_EVIDENCE_STRENGTH_INVALID")
            require(set(row) == approval_fields
                    and row["unit"] == policy["unit"] and isinstance(row["construct_id"], str) and row["construct_id"]
                    and isinstance(row["source_raw_fields"], list) and len(row["source_raw_fields"]) == len(set(row["source_raw_fields"])) > 0
                    and isinstance(row["rationale"], str) and row["rationale"].strip(), "ANALYSIS_TARGET_APPROVAL_INVALID")
            expected_status = "OPERATIONAL_DEFINITION_WITH_LIMITATION" if policy["name"] == "lvef" else (
                "CLINICALLY_REVIEWED_MEASUREMENT" if not relay_mode else
                "EXPERT_ADJUDICATED_OPERATIONAL_DEFINITION_WITH_LIMITATION" if expert_scoped else
                "TECHNICALLY_REVIEWED_OPERATIONAL_DEFINITION_WITH_LIMITATION")
            require(row["label_definition_status"] == expected_status, "ANALYSIS_LABEL_DEFINITION_NOT_APPROVED")
        constructs = [approvals[name]["construct_id"] for name in spec["strict_panel"]]
        require(len(constructs) == len(set(constructs)), "ANALYSIS_DUPLICATE_SCORED_CONSTRUCT")
        unresolved = {r["issue_id"] for r in clinical["proof"]["questions"] if r["selected_option"] == "UNRESOLVED_EXCLUDE" or r["selected_option"].startswith("MIXED_")}
        clinical_excluded = {target for issue in CLINICAL_ISSUE_SPECS if issue["issue_id"] in unresolved for target in issue["targets"]}
        processing_excluded = {"mitral_e_velocity"} if relay_mode else set()
        excluded = clinical_excluded | processing_excluded
        require(not set(spec["strict_panel"]) & excluded
                and all(not set(p["allowed_predictors"]) & excluded for p in expected), "ANALYSIS_CLINICALLY_UNRESOLVED_TARGET_SURVIVED")
        relation = next(r["selected_option"] for r in clinical["proof"]["questions"] if r["issue_id"] == "MITRAL_E_FIELD_RELATIONSHIP")
        scored_e = set(spec["strict_panel"]) & {"mv_peak_e", "mitral_e_velocity"}
        if relation == OWNER_RELAY_Q6:
            require(relay_mode and scored_e == {"mv_peak_e"}
                    and panel.get("mitral_e_processing") == OWNER_RELAY_Q6_PROCESSING
                    and "mitral_e_aggregation_approval" not in panel, "ANALYSIS_MITRAL_E_UNIT_CONFLICT_HANDLING_INVALID")
        elif relation.startswith("SAME_CONSTRUCT"):
            require(len(scored_e) <= 1, "ANALYSIS_DUPLICATE_MITRAL_E_CONSTRUCT")
            require(panel.get("mitral_e_aggregation_approval") == {"clinical_option": relation,
                    "scored_targets": sorted(scored_e), "aggregation_explicitly_approved": True}, "ANALYSIS_MITRAL_E_AGGREGATION_APPROVAL_REQUIRED")
        elif relation == "DISTINCT_ACQUISITION_CONSTRUCTS" and len(scored_e) == 2:
            require(not set(approvals["mv_peak_e"]["source_raw_fields"]) & set(approvals["mitral_e_velocity"]["source_raw_fields"]), "ANALYSIS_DISTINCT_MITRAL_E_MERGED")
        proof = {"target_approvals": approvals, "strict_targets": spec["strict_panel"], "reviewed_policy_count": len(expected),
                 "clinical_unresolved_excluded": sorted(clinical_excluded), "processing_excluded_targets": sorted(processing_excluded)}
    elif name == "common_inputs":
        require(set(parameters) == {"inputs_path"}, "ANALYSIS_INPUT_PARAMETERS_INVALID")
        inputs = bound("inputs", parameters["inputs_path"])
        require(refs["inputs"]["sha256"] == spec["bindings"]["input_audit"]
                and inputs.get("artifact_type") == "lvef_revalidation_inputs_v1"
                and inputs.get("status") == "PASS_MODEL_INDEPENDENT_INPUTS_PANEL_PENDING"
                and inputs.get("c3_completion_sha256") == C3_COMPLETION, "ANALYSIS_INPUT_BINDING_INVALID")
        from prepare_lvef_revalidation_inputs import SOURCE_HASHES
        require(inputs.get("source_checksums") == SOURCE_HASHES and inputs.get("config_sha256") == spec["bindings"]["config"], "ANALYSIS_COMMON_SOURCE_BINDING_INVALID")
        require(inputs.get("all_missing_allowed_structured_rows_retained") is True
                and inputs.get("model_fitting_count") == inputs.get("test_performance_access_count") == 0
                and inputs.get("lvef_label_authority") == "EXACT_CASE_SENSITIVE_RAW_LVEF_MEDIAN_BY_SUBJECT_AND_MEASUREMENT_ID",
                "ANALYSIS_COMMON_INPUT_SCOPE_INVALID")
        _manifest_files_unchanged(inputs)
        for p in spec["policies"]:
            actual = inputs["targets"][p["name"]]
            unit_status = "SEPARATE_EXACT_NAME_ANALYTICAL_SCALE_NATIVE_UNIT_UNVERIFIED" if p["name"] == "lvef" else "NORMALIZED_UNIT_COMPATIBLE_CLINICAL_DEFINITION_PENDING"
            require(actual["unit_status"] == unit_status, "ANALYSIS_UNRESOLVED_UNIT_NOT_SCOREABLE")
            require(actual["unit"] == p["unit"] and actual["row_fingerprints"] == p["row_fingerprints"]
                    and {s: actual["counts"][s] for s in ("train", "val", "test")} == p["support_counts"]
                    and actual["counts"]["total"] == sum(p["support_counts"].values())
                    and type(actual["training_iqr"]) in (int, float) and 0 < actual["training_iqr"] < float("inf")
                    and actual.get("binary_class_counts", {}) == p["binary_class_counts"], "ANALYSIS_COMMON_ROWS_OR_SUPPORT_MISMATCH")
        for counts_key in ("selected_split_counts", "imaging_split_counts", "lvef_common_counts", "exact40_counts"):
            counts = inputs[counts_key]
            require(set(counts) >= {"train", "val", "test"} and all(type(counts[s]) is int and counts[s] >= 0 for s in ("train", "val", "test")), "ANALYSIS_COHORT_COUNTS_INVALID")
        require(inputs["selected_split_counts"] == {"train": 3171, "val": 679, "test": 680}
                and inputs["imaging_split_counts"] == {"train": 3168, "val": 678, "test": 679}, "ANALYSIS_SELECTED_SPLIT_MISMATCH")
        require(len(spec["test_subject_roster"]) == inputs["imaging_split_counts"]["test"]
                and digest(canonical({"subject_ids": spec["test_subject_roster"]})) == inputs["imaging_subject_roster_sha256"]["test"], "ANALYSIS_COMPLETE_TEST_ROSTER_MISMATCH")
        for split in ("train", "val", "test"):
            counts = inputs["targets"]["lvef"]["binary_class_counts"]
            require(counts["lvef_le_40"][split][0] - counts["lvef_lt_40"][split][0] == inputs["exact40_counts"][split]
                    and inputs["lvef_common_counts"][split] == inputs["targets"]["lvef"]["counts"][split], "ANALYSIS_EXACT40_RECONCILIATION_FAILED")
        proof = {"target_count": len(spec["policies"]), "split_artifacts": inputs["split_artifacts"], "source_checksums": inputs["source_checksums"], "exact40_counts": inputs["exact40_counts"]}
    elif name == "environment":
        require(set(parameters) == {"environment_path"}, "ANALYSIS_ENVIRONMENT_PARAMETERS_INVALID")
        observed = bound("environment", parameters["environment_path"])
        require(refs["environment"]["sha256"] == spec["bindings"]["environment"] and observed == environment_observation(), "ANALYSIS_ENVIRONMENT_CHANGED")
        proof = observed
    elif name == "safety":
        from lvef_multitask_analysis_modes import load_policy, bind_approved_restricted_path
        require(set(parameters) == {"policy_path", "analysis_root", "inputs_path"}, "ANALYSIS_SAFETY_PARAMETERS_INVALID")
        policy, policy_sha = load_policy(Path(parameters["policy_path"]))
        require(policy_sha == spec["bindings"]["safety"], "ANALYSIS_SAFETY_POLICY_CHANGED")
        root = bind_approved_restricted_path(Path(parameters["analysis_root"]), policy=policy, must_exist=True, expect="directory")
        require(root.stat().st_uid == os.getuid() and stat.S_IMODE(root.stat().st_mode) in (0o700, 0o2700), "ANALYSIS_PRIVATE_PARENT_REQUIRED")
        inputs = bound("inputs", parameters["inputs_path"])
        require(refs["inputs"]["sha256"] == spec["bindings"]["input_audit"], "ANALYSIS_INPUT_BINDING_INVALID")
        for split in inputs["split_artifacts"].values():
            for ref in split.values():
                bind_approved_restricted_path(Path(ref["path"]), policy=policy, must_exist=True, expect="file")
        proof = {"policy_sha256": policy_sha, "analysis_root": str(root), "outputs_remain_restricted": True, "public_export_authorized": False}
    else:
        require(set(parameters) == {"validation_path"}, "ANALYSIS_VALIDATION_PARAMETERS_INVALID")
        evidence = bound("validation", parameters["validation_path"])
        log = private_bytes(Path(evidence["log"]["path"]))
        require(digest(log) == evidence["log"]["sha256"] and len(log) == evidence["log"]["size_bytes"]
                and evidence.get("status") == "PASS_FOCUSED_SYNTHETIC_VALIDATION"
                and evidence.get("returncode") == 0 and evidence.get("new_test_performance_used") is False
                and evidence.get("runner") == "pytest" and evidence.get("passed", 0) > 0
                and re.search(rb"(?:^|\n).*?" + str(evidence["passed"]).encode() + rb" passed(?:,| in )", log)
                and not re.search(rb"(?:^|\n)(?:FAILED|ERROR)|[1-9][0-9]* failed", log), "ANALYSIS_SYNTHETIC_VALIDATION_REQUIRED")
        required_tests = {"tests/test_lvef_revalidation_analysis.py", "tests/test_lvef_revalidation_authority.py", "tests/test_lvef_revalidation_inputs.py"}
        require(set(evidence["test_files"]) == required_tests and all(_hash_is_valid(v) for v in evidence["test_files"].values()), "ANALYSIS_REQUIRED_VALIDATION_FILES_MISSING")
        proof = evidence
    return {"schema_version": 1, "artifact_type": "lvef_revalidation_replayed_gate_v1", "status": "PASS",
            "gate": name, "spec_sha256": spec_sha, "spec_path": str(spec_path),
            "parameters": dict(parameters), "evidence": refs, "proof": proof, "new_test_performance_used": False}


def replay_gate(gate: Mapping[str, Any], *, spec_sha256: str) -> None:
    require(gate.get("artifact_type") == "lvef_revalidation_replayed_gate_v1"
            and gate.get("spec_sha256") == spec_sha256, "ANALYSIS_REPLAYABLE_GATE_REQUIRED")
    regenerated = create_gate(str(gate["gate"]), spec_path=Path(gate["spec_path"]), parameters=gate["parameters"])
    require(canonical(gate) == canonical(regenerated), "ANALYSIS_GATE_EVIDENCE_CHANGED")


def validate_prerequisites(gates: Mapping[str, Mapping[str, Any]], *, spec_sha256: str) -> None:
    require(set(gates) == set(REQUIRED_GATES), "ANALYSIS_REQUIRED_GATE_SET_INVALID")
    require(all(gate.get("artifact_type") == "lvef_revalidation_replayed_gate_v1" for gate in gates.values()), "ANALYSIS_REPLAYABLE_GATE_REQUIRED")
    require(gates["panel_and_dependencies"]["parameters"]["clinical_parameters"] == gates["clinical_signoff"]["parameters"]
            and gates["panel_and_dependencies"]["parameters"]["technical_parameters"] == gates["technical_adjudication"]["parameters"], "ANALYSIS_REVIEW_REPLAY_PARAMETERS_MISMATCH")
    for name, gate in gates.items():
        require(gate.get("gate") == name, "ANALYSIS_GATE_ROLE_MISMATCH")
        replay_gate(gate, spec_sha256=spec_sha256)
    clinical = gates["clinical_signoff"]
    technical = gates["technical_adjudication"]
    inputs = read_bound(Path(gates["common_inputs"]["evidence"]["inputs"]["path"]), gates["common_inputs"]["evidence"]["inputs"]["sha256"])
    aliases = {"selected_studies": "selected", "subject_split_map": "split", "structured_measurements": "structured", "raw_canonical_mapping": "mapping"}
    expected = {role: inputs["source_checksums"][key] for role, key in aliases.items()}
    expected["clinical_review_rows"] = clinical["evidence"]["review_rows"]["sha256"]
    require(technical["parameters"]["input_checksums"] == expected, "ANALYSIS_TECHNICAL_COMMON_SOURCE_MISMATCH")
    panel_proof = gates["panel_and_dependencies"]["proof"]
    excluded = panel_proof["clinical_unresolved_excluded"] + panel_proof.get("processing_excluded_targets", [])
    raw_map = inputs["source_raw_fields_by_canonical"]
    require(all(name in raw_map and isinstance(raw_map[name], list) for name in excluded), "ANALYSIS_EXCLUDED_RAW_SOURCE_CLOSURE_MISSING")
    prohibited_raw = {raw for name in excluded for raw in raw_map[name]}
    spec, _ = _read_spec(Path(gates["common_inputs"]["spec_path"]))
    require(all(not set(p["allowed_predictors"]) & prohibited_raw for p in spec["policies"]), "ANALYSIS_UNRESOLVED_RAW_ALIAS_SURVIVED")
    for name, approval in gates["panel_and_dependencies"]["proof"]["target_approvals"].items():
        target = inputs["targets"][name]
        require(approval["source_raw_fields"] == target["source_raw_fields"]
                and approval["valid_unit_raw_fields"] == target["valid_unit_raw_fields"]
                and approval["aggregation_rule"] == target["aggregation_rule"], "ANALYSIS_TARGET_AGGREGATION_NOT_APPROVED")


def seal_analysis_lock(*, output: Path, analysis_commit: str, spec_path: Path,
                       owner_path: Path, gate_paths: Mapping[str, Path],
                       source_file_sha256: Mapping[str, str], repository_root: Path,
                       source_files: Mapping[str, Path]) -> str:
    """Call only after the existing human validator and input audits pass."""
    require(re.fullmatch(r"[0-9a-f]{40}", analysis_commit) is not None, "ANALYSIS_COMMIT_INVALID")
    require(output == output.parent / "analysis_lock.restricted.json", "ANALYSIS_LOCK_PATH_INVALID")
    actual_sources = source_guard(repository_root, analysis_commit, source_files)
    require(actual_sources == dict(source_file_sha256), "ANALYSIS_SOURCE_CHANGED")
    spec_value, _ = _read_spec(spec_path)
    spec = _binding(spec_path)
    require(spec_value["bindings"]["sap"] == actual_sources["sap"]
            and spec_value["bindings"]["config"] == actual_sources["config"], "ANALYSIS_SAP_CONFIG_BINDING_INVALID")
    import yaml
    import lvef_revalidation_analysis as engine
    require(engine.validate_prescription(yaml.safe_load(source_files["config"].read_bytes())) == spec_value["prescription_sha256"], "ANALYSIS_PRESCRIPTION_MISMATCH")
    owner = decode(private_bytes(owner_path))
    validate_owner(owner)
    require(_binding(owner_path)["sha256"] == spec_value["bindings"]["owner_authorization"], "ANALYSIS_OWNER_SPEC_MISMATCH")
    gates = {name: decode(private_bytes(path)) for name, path in gate_paths.items()}
    validate_prerequisites(gates, spec_sha256=spec["sha256"])
    proof = gates["synthetic_validation"]["proof"]
    require(proof.get("analysis_commit") == analysis_commit
            and proof.get("source_file_sha256") == dict(source_file_sha256), "ANALYSIS_VALIDATION_SOURCE_MISMATCH")
    for relative, expected in proof["test_files"].items():
        require(digest((repository_root / relative).read_bytes()) == expected, "ANALYSIS_VALIDATION_TEST_CHANGED")
    require({"sap", "config", "engine", "controller"}.issubset(source_file_sha256)
            and all(re.fullmatch(r"[0-9a-f]{64}", v) is not None for v in source_file_sha256.values()),
            "ANALYSIS_SOURCE_BINDINGS_REQUIRED")
    record = {"schema_version": 1, "artifact_type": "lvef_revalidation_analysis_lock_v1",
              "status": "PASS_ANALYSIS_LOCK", "analysis_commit": analysis_commit,
              "created_at_utc": datetime.now(timezone.utc).isoformat(),
              "c3_producer_commit": C3_COMMIT, "c3_completion_sha256": C3_COMPLETION,
              "spec": spec, "owner_authorization": _binding(owner_path),
              "gates": {name: _binding(path) for name, path in gate_paths.items()},
              "source_file_sha256": dict(source_file_sha256), "repository_root": str(repository_root),
              "source_files": {role: str(path) for role, path in source_files.items()},
              "historical_test_exposure_recorded": True, "independent_external_validation": False,
              "core_holm_family_size": 4, "new_test_performance_used_for_design": False}
    return publish(output, record)


class AnalysisAuthority:
    """Engine callback; reserves a single test evaluation before its loader runs."""
    def __init__(self, lock_path: Path, lock_sha256: str, *, current_commit: str,
                 source_files: Mapping[str, Path], claim_path: Path):
        require(claim_path == lock_path.parent / "test_evaluation.claim.restricted.json",
                "ANALYSIS_TEST_CLAIM_PATH_INVALID")
        self.lock_path, self.lock_sha256 = lock_path, lock_sha256
        self.current_commit, self.source_files, self.claim_path = current_commit, source_files, claim_path

    def __call__(self, *, stage: str, spec_sha256: str, frozen_sha256: str | None = None,
                 release: Any = None) -> dict[str, Any]:
        require(stage in ("development", "test", "report"), "ANALYSIS_STAGE_INVALID")
        lock = read_bound(self.lock_path, self.lock_sha256)
        require(lock.get("status") == "PASS_ANALYSIS_LOCK"
                and lock.get("artifact_type") == "lvef_revalidation_analysis_lock_v1"
                and lock.get("analysis_commit") == self.current_commit
                and lock.get("c3_completion_sha256") == C3_COMPLETION
                and lock.get("spec", {}).get("sha256") == spec_sha256,
                "ANALYSIS_LOCK_BINDING_INVALID")
        read_bound(Path(lock["spec"]["path"]), spec_sha256)
        validate_owner(read_bound(Path(lock["owner_authorization"]["path"]), lock["owner_authorization"]["sha256"]))
        gates = {name: read_bound(Path(binding["path"]), binding["sha256"])
                 for name, binding in lock["gates"].items()}
        validate_prerequisites(gates, spec_sha256=spec_sha256)
        require(set(self.source_files) == set(lock["source_file_sha256"]), "ANALYSIS_SOURCE_FILE_SET_CHANGED")
        require({role: str(path) for role, path in self.source_files.items()} == lock["source_files"], "ANALYSIS_SOURCE_PATH_CHANGED")
        require(source_guard(Path(lock["repository_root"]), self.current_commit, self.source_files) == lock["source_file_sha256"], "ANALYSIS_SOURCE_CHANGED")
        proof = gates["synthetic_validation"]["proof"]
        require(proof.get("analysis_commit") == self.current_commit and proof.get("source_file_sha256") == lock["source_file_sha256"], "ANALYSIS_VALIDATION_SOURCE_MISMATCH")
        for relative, expected in proof["test_files"].items():
            require(digest((Path(lock["repository_root"]) / relative).read_bytes()) == expected, "ANALYSIS_VALIDATION_TEST_CHANGED")
        if stage == "test":
            require(release is not None and frozen_sha256 is not None
                    and release.spec_sha256 == spec_sha256 and release.frozen_sha256 == frozen_sha256,
                    "ANALYSIS_TEST_RELEASE_REQUIRED")
            release_path = self.lock_path.parent / "test_release.restricted.json"
            value = read_bound(release_path, release.release_receipt_sha256)
            require(value.get("status") == "AUTHORIZED_FIXED_TEST_EVALUATION"
                    and value.get("analysis_lock_sha256") == self.lock_sha256
                    and value.get("spec_sha256") == spec_sha256
                    and value.get("frozen_sha256") == frozen_sha256
                    and value.get("training_validation_only") is True
                    and value.get("input_sha256") == gates["common_inputs"]["evidence"]["inputs"]["sha256"],
                    "ANALYSIS_TEST_RELEASE_INVALID")
            freeze = read_bound(self.lock_path.parent / "model_freeze.restricted.json", value["model_freeze_sha256"])
            require(freeze.get("status") == "PASS_SERIALIZED_MODEL_FREEZE"
                    and freeze.get("analysis_lock_sha256") == self.lock_sha256
                    and freeze.get("spec_sha256") == spec_sha256 and freeze.get("frozen_sha256") == frozen_sha256
                    and freeze.get("input_sha256") == value["input_sha256"]
                    and digest(private_bytes(self.lock_path.parent / "frozen_models.restricted.json")) == frozen_sha256,
                    "ANALYSIS_FROZEN_COEFFICIENTS_CHANGED")
            try:
                publish(self.claim_path, {"status": "TEST_EVALUATION_CLAIMED", "analysis_lock_sha256": self.lock_sha256,
                    "spec_sha256": spec_sha256, "frozen_sha256": frozen_sha256,
                    "release_sha256": release.release_receipt_sha256})
            except FileExistsError as exc:
                raise AuthorityError("ANALYSIS_TEST_RELEASE_ALREADY_CONSUMED") from exc
        elif stage == "development":
            require(release is None and frozen_sha256 is None, "ANALYSIS_DEVELOPMENT_RELEASE_INVALID")
            require(not self.claim_path.exists(), "ANALYSIS_DEVELOPMENT_AFTER_TEST_FORBIDDEN")
        else:
            require(release is None and frozen_sha256 is None and self.claim_path.is_file(), "ANALYSIS_REPORT_REQUIRES_TEST_CLAIM")
            claim = decode(private_bytes(self.claim_path))
            require(claim.get("status") == "TEST_EVALUATION_CLAIMED"
                    and claim.get("analysis_lock_sha256") == self.lock_sha256
                    and claim.get("spec_sha256") == spec_sha256,
                    "ANALYSIS_REPORT_CLAIM_BINDING_INVALID")
            released = read_bound(self.lock_path.parent / "test_release.restricted.json", claim["release_sha256"])
            require(released.get("frozen_sha256") == claim.get("frozen_sha256")
                    and released.get("analysis_lock_sha256") == self.lock_sha256
                    and released.get("spec_sha256") == spec_sha256
                    and released.get("input_sha256") == gates["common_inputs"]["evidence"]["inputs"]["sha256"]
                    and digest(private_bytes(self.lock_path.parent / "frozen_models.restricted.json")) == claim.get("frozen_sha256"),
                    "ANALYSIS_REPORT_RELEASE_BINDING_INVALID")
        return {"status": "PASS_ANALYSIS_AUTHORITY", "stage": stage, "spec_sha256": spec_sha256,
                "frozen_sha256": frozen_sha256, "authority_receipt_sha256": self.lock_sha256}
