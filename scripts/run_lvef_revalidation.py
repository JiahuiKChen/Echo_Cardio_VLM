#!/usr/bin/env python3
"""Explicit restricted stages for the locked ASA revalidation; never submit jobs.

Validate is read-only unless --seal is supplied. Development, freeze, test release,
test evaluation, and private reporting are separate invocations. No stage changes
the C3 reconstruction or supplies a clinical decision.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
from typing import Any, Mapping

import lvef_revalidation_authority as authority


RUN_KEYS = {"artifact_type", "analysis_commit", "repository_root", "analysis_root", "spec_path",
            "spec_sha256", "owner_path", "inputs_path", "input_sha256", "source_files", "gate_parameters"}


def context(manifest_path: Path, manifest_sha256: str) -> dict[str, Any]:
    run = authority.read_bound(manifest_path, manifest_sha256)
    authority.require(set(run) == RUN_KEYS and run["artifact_type"] == "lvef_revalidation_run_v1",
                      "ANALYSIS_RUN_MANIFEST_INVALID")
    repository = Path(run["repository_root"])
    authority.require(repository == Path(__file__).resolve().parents[1], "ANALYSIS_RUN_REPOSITORY_MISMATCH")
    files = {role: Path(path) for role, path in run["source_files"].items()}
    source_hashes = authority.source_guard(repository, run["analysis_commit"], files)
    from lvef_multitask_analysis_modes import load_policy, bind_approved_restricted_path
    policy, _ = load_policy(files["safety_policy"])
    root = bind_approved_restricted_path(Path(run["analysis_root"]), policy=policy, must_exist=True, expect="directory")
    authority.require(root.stat().st_uid == os.getuid() and root.stat().st_mode & 0o777 == 0o700,
                      "ANALYSIS_PRIVATE_PARENT_REQUIRED")
    for key in ("spec_path", "owner_path", "inputs_path"):
        bind_approved_restricted_path(Path(run[key]), policy=policy, must_exist=True, expect="file")
    spec_value, spec_sha = authority._read_spec(Path(run["spec_path"]))
    authority.require(spec_sha == run["spec_sha256"]
                      and spec_value["bindings"]["input_audit"] == run["input_sha256"],
                      "ANALYSIS_RUN_SPEC_BINDING_INVALID")
    authority.read_bound(Path(run["inputs_path"]), run["input_sha256"])
    authority.require(set(run["gate_parameters"]) == set(authority.REQUIRED_GATES)
                      and run["gate_parameters"]["common_inputs"] == {"inputs_path": run["inputs_path"]}
                      and run["gate_parameters"]["safety"]["analysis_root"] == str(root)
                      and run["gate_parameters"]["safety"]["inputs_path"] == run["inputs_path"],
                      "ANALYSIS_RUN_GATE_PARAMETERS_MISMATCH")
    return {"run": run, "manifest_sha256": manifest_sha256, "root": root,
            "source_files": files, "source_hashes": source_hashes, "spec": spec_value}


def lock_callback(ctx: Mapping[str, Any]) -> tuple[authority.AnalysisAuthority, str]:
    root, run = ctx["root"], ctx["run"]
    path = root / "analysis_lock.restricted.json"
    receipt = authority._binding(path)
    lock = authority.read_bound(path, receipt["sha256"])
    authority.require(lock["spec"]["path"] == run["spec_path"]
                      and lock["spec"]["sha256"] == run["spec_sha256"]
                      and lock["owner_authorization"]["path"] == run["owner_path"],
                      "ANALYSIS_RUN_LOCK_MISMATCH")
    return authority.AnalysisAuthority(path, receipt["sha256"], current_commit=run["analysis_commit"],
        source_files=ctx["source_files"], claim_path=root / "test_evaluation.claim.restricted.json"), receipt["sha256"]


def validate(ctx: Mapping[str, Any], *, seal: bool) -> dict[str, Any]:
    run, root = ctx["run"], ctx["root"]
    gates = {name: authority.create_gate(name, spec_path=Path(run["spec_path"]), parameters=run["gate_parameters"][name])
             for name in authority.REQUIRED_GATES}
    authority.validate_prerequisites(gates, spec_sha256=run["spec_sha256"])
    if not seal:
        return {"status": "PASS_ANALYSIS_PREREQUISITE_REPLAY", "analysis_lock_created": False,
                "model_fitting_count": 0, "test_loader_invocations": 0}
    gate_root = root / "gates"
    if not gate_root.exists():
        gate_root.mkdir(mode=0o700)
    paths = {}
    for name, gate in gates.items():
        paths[name] = gate_root / f"{name}.restricted.json"
        if paths[name].exists():
            authority.require(authority.private_bytes(paths[name]) == authority.canonical(gate), "ANALYSIS_GATE_PUBLICATION_COLLISION")
        else:
            authority.publish(paths[name], gate)
    sha = authority.seal_analysis_lock(output=root / "analysis_lock.restricted.json", analysis_commit=run["analysis_commit"],
        spec_path=Path(run["spec_path"]), owner_path=Path(run["owner_path"]), gate_paths=paths,
        source_file_sha256=ctx["source_hashes"], repository_root=Path(run["repository_root"]), source_files=ctx["source_files"])
    return {"status": "PASS_ANALYSIS_LOCK", "analysis_lock_sha256": sha, "model_fitting_count": 0, "test_loader_invocations": 0}


def _load_split(ctx: Mapping[str, Any], split: str) -> dict[str, Any]:
    from prepare_lvef_revalidation_inputs import load_modality_rows
    run = ctx["run"]
    authority.read_bound(Path(run["inputs_path"]), run["input_sha256"])
    return {policy["name"]: load_modality_rows(Path(run["inputs_path"]), split, policy["name"])
            for policy in ctx["spec"]["policies"]}


def development(ctx: Mapping[str, Any]) -> dict[str, Any]:
    import lvef_revalidation_analysis as engine
    callback, lock_sha = lock_callback(ctx)
    run, root = ctx["run"], ctx["root"]
    callback(stage="development", spec_sha256=run["spec_sha256"])
    for name in ("development_models.restricted.json", "development_receipt.restricted.json", "model_freeze.restricted.json", "test_release.restricted.json"):
        authority.require(not (root / name).exists(), "ANALYSIS_DEVELOPMENT_OUTPUT_ALREADY_EXISTS")
    authority.publish(root / "development.claim.restricted.json", {"status": "TRAIN_VALIDATION_DEVELOPMENT_CLAIMED",
        "analysis_lock_sha256": lock_sha, "input_sha256": run["input_sha256"], "run_manifest_sha256": ctx["manifest_sha256"]})
    fitted = engine.select_development(engine.spec_from_payload(ctx["spec"]), _load_split(ctx, "train"), _load_split(ctx, "val"), authorize=callback)
    body, sha = engine.freeze_analysis(fitted)
    engine.load_frozen_analysis(body, expected_sha256=sha)
    authority.require(authority.publish(root / "development_models.restricted.json", fitted.payload) == sha, "ANALYSIS_SERIALIZED_MODEL_HASH_MISMATCH")
    receipt = {"status": "PASS_TRAIN_VALIDATION_DEVELOPMENT", "analysis_lock_sha256": lock_sha,
        "spec_sha256": run["spec_sha256"], "input_sha256": run["input_sha256"], "frozen_sha256": sha,
        "model_file": "development_models.restricted.json", "training_validation_only": True, "test_loader_invocations": 0}
    receipt_sha = authority.publish(root / "development_receipt.restricted.json", receipt)
    return {"status": receipt["status"], "receipt_sha256": receipt_sha, "test_loader_invocations": 0}


def freeze(ctx: Mapping[str, Any]) -> dict[str, Any]:
    import lvef_revalidation_analysis as engine
    callback, lock_sha = lock_callback(ctx)
    run, root = ctx["run"], ctx["root"]
    callback(stage="development", spec_sha256=run["spec_sha256"])
    receipt_ref = authority._binding(root / "development_receipt.restricted.json")
    receipt = authority.read_bound(Path(receipt_ref["path"]), receipt_ref["sha256"])
    authority.require(receipt["status"] == "PASS_TRAIN_VALIDATION_DEVELOPMENT"
                      and receipt["analysis_lock_sha256"] == lock_sha and receipt["spec_sha256"] == run["spec_sha256"]
                      and receipt["input_sha256"] == run["input_sha256"] and receipt["training_validation_only"] is True
                      and receipt["test_loader_invocations"] == 0, "ANALYSIS_DEVELOPMENT_RECEIPT_INVALID")
    body = authority.private_bytes(root / "development_models.restricted.json")
    fitted = engine.load_frozen_analysis(body, expected_sha256=receipt["frozen_sha256"])
    authority.require(fitted.payload["spec_sha256"] == run["spec_sha256"], "ANALYSIS_FROZEN_SPEC_CHANGED")
    authority.publish(root / "frozen_models.restricted.json", fitted.payload)
    record = {"status": "PASS_SERIALIZED_MODEL_FREEZE", "analysis_lock_sha256": lock_sha,
        "spec_sha256": run["spec_sha256"], "input_sha256": run["input_sha256"], "frozen_sha256": receipt["frozen_sha256"],
        "development_receipt_sha256": receipt_ref["sha256"], "coefficients_transforms_calibration_serialized": True,
        "test_loader_invocations": 0}
    sha = authority.publish(root / "model_freeze.restricted.json", record)
    return {"status": record["status"], "model_freeze_sha256": sha, "frozen_sha256": record["frozen_sha256"]}


def release_test(ctx: Mapping[str, Any]) -> dict[str, Any]:
    import lvef_revalidation_analysis as engine
    callback, lock_sha = lock_callback(ctx)
    run, root = ctx["run"], ctx["root"]
    callback(stage="development", spec_sha256=run["spec_sha256"])
    ref = authority._binding(root / "model_freeze.restricted.json")
    frozen = authority.read_bound(Path(ref["path"]), ref["sha256"])
    authority.require(frozen["status"] == "PASS_SERIALIZED_MODEL_FREEZE" and frozen["analysis_lock_sha256"] == lock_sha
                      and frozen["spec_sha256"] == run["spec_sha256"] and frozen["input_sha256"] == run["input_sha256"], "ANALYSIS_MODEL_FREEZE_BINDING_INVALID")
    engine.load_frozen_analysis(authority.private_bytes(root / "frozen_models.restricted.json"), expected_sha256=frozen["frozen_sha256"])
    record = {"status": "AUTHORIZED_FIXED_TEST_EVALUATION", "analysis_lock_sha256": lock_sha,
        "spec_sha256": run["spec_sha256"], "input_sha256": run["input_sha256"], "frozen_sha256": frozen["frozen_sha256"],
        "model_freeze_sha256": ref["sha256"], "training_validation_only": True,
        "single_evaluation_claim": "test_evaluation.claim.restricted.json", "test_loader_invocations": 0}
    sha = authority.publish(root / "test_release.restricted.json", record)
    return {"status": record["status"], "release_sha256": sha, "test_loader_invocations": 0}


def evaluate(ctx: Mapping[str, Any]) -> dict[str, Any]:
    import lvef_revalidation_analysis as engine
    callback, lock_sha = lock_callback(ctx)
    run, root = ctx["run"], ctx["root"]
    ref = authority._binding(root / "test_release.restricted.json")
    record = authority.read_bound(Path(ref["path"]), ref["sha256"])
    fitted = engine.load_frozen_analysis(authority.private_bytes(root / "frozen_models.restricted.json"), expected_sha256=record["frozen_sha256"])
    result = engine.evaluate_locked_test(fitted,
        release=engine.TestRelease(run["spec_sha256"], record["frozen_sha256"], ref["sha256"]),
        authorize=callback, test_loader=lambda: _load_split(ctx, "test"))
    sha = authority.publish(root / "test_predictions.restricted.json", result)
    receipt = {"status": "PASS_LOCKED_TEST_EVALUATION", "analysis_lock_sha256": lock_sha,
        "input_sha256": run["input_sha256"], "spec_sha256": run["spec_sha256"], "frozen_sha256": record["frozen_sha256"],
        "release_sha256": ref["sha256"], "predictions_sha256": sha, "test_loader_invocations": 1}
    receipt_sha = authority.publish(root / "test_evaluation.restricted.json", receipt)
    return {"status": receipt["status"], "evaluation_sha256": receipt_sha, "test_loader_invocations": 1}


def report(ctx: Mapping[str, Any]) -> dict[str, Any]:
    from lvef_revalidation_analysis import EVALUATION_CONDITIONS
    import lvef_revalidation_inference as inference
    callback, lock_sha = lock_callback(ctx)
    callback(stage="report", spec_sha256=ctx["run"]["spec_sha256"])
    root, run = ctx["root"], ctx["run"]
    authority.require(not (root / "paired_report.restricted.json").exists(), "ANALYSIS_REPORT_ALREADY_EXISTS")
    ref = authority._binding(root / "test_evaluation.restricted.json")
    receipt = authority.read_bound(Path(ref["path"]), ref["sha256"])
    authority.require(receipt["status"] == "PASS_LOCKED_TEST_EVALUATION" and receipt["analysis_lock_sha256"] == lock_sha
                      and receipt["input_sha256"] == run["input_sha256"] and receipt["spec_sha256"] == run["spec_sha256"], "ANALYSIS_REPORT_EVALUATION_BINDING_INVALID")
    evaluation = authority.read_bound(root / "test_predictions.restricted.json", receipt["predictions_sha256"])
    result = {"status": "PASS_PRIVATE_PAIRED_REPORT", "evaluation_receipt_sha256": ref["sha256"],
        "analysis_lock_sha256": lock_sha, "spec_sha256": run["spec_sha256"], "conditions": {},
        "patient_level_outputs_exported": False, "public_export_approved": False}
    for condition in EVALUATION_CONDITIONS:
        result["conditions"][condition] = {
            "primary_inference": inference.paired_inference(evaluation, condition=condition, activate_core=condition == "primary" and evaluation["construct"] == "strict"),
            "secondary_intervals": inference.complete_paired_intervals(evaluation, condition=condition),
            "panel_summary": inference.summarize_panel(evaluation, condition=condition)}
    sha = authority.publish(root / "paired_report.restricted.json", result)
    if evaluation["construct"] != "strict":
        return {"status": result["status"], "report_sha256": sha, "public_export_approved": False}
    from render_lvef_revalidation_results import extract_aggregate_bundle, seal_aggregate_bundle, validate_candidate
    from lvef_multitask_audit_utils import assert_aggregate_safe_json
    inputs = authority.read_bound(Path(run["inputs_path"]), run["input_sha256"])
    candidate = extract_aggregate_bundle(evaluation, result, input_audit=inputs)
    validate_candidate(candidate)
    assert_aggregate_safe_json(candidate)
    candidate_sha = authority.publish(root / "aggregate_candidate.restricted.json", candidate)
    safety = {"status": "PASS_REVALIDATION_AGGREGATE_SAFETY", "candidate_sha256": candidate_sha,
        "analysis_lock_sha256": lock_sha, "input_sha256": run["input_sha256"],
        "evaluation_receipt_sha256": ref["sha256"], "report_sha256": sha,
        "checks": ["CLOSED_COMPLETE_AGGREGATE_SCHEMA", "MAINTAINED_AGGREGATE_SAFE_JSON"],
        "validator_sha256": {role: ctx["source_hashes"][role] for role in ("renderer", "audit_utils", "safety_policy")},
        "patient_level_outputs_exported": False, "public_export_approved": False}
    safety_sha = authority.publish(root / "aggregate_safety.restricted.json", safety)
    bundle = seal_aggregate_bundle(candidate, {"status": safety["status"], "candidate_sha256": candidate_sha,
        "safety_receipt_sha256": safety_sha})
    bundle_sha = authority.publish(root / "aggregate_bundle.restricted.json", bundle)
    return {"status": bundle["status"], "report_sha256": sha, "aggregate_bundle_sha256": bundle_sha,
        "aggregate_safety_sha256": safety_sha, "public_export_approved": False}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("validate", "development", "freeze", "release-test", "evaluate", "report"))
    parser.add_argument("--run-manifest", type=Path, required=True)
    parser.add_argument("--run-manifest-sha256", required=True)
    parser.add_argument("--seal", action="store_true", help="Validate stage only: publish the lock after every genuine gate passes")
    args = parser.parse_args()
    try:
        authority.require(not args.seal or args.stage == "validate", "ANALYSIS_SEAL_STAGE_INVALID")
        ctx = context(args.run_manifest, args.run_manifest_sha256)
        result = validate(ctx, seal=args.seal) if args.stage == "validate" else {
            "development": development, "freeze": freeze, "release-test": release_test,
            "evaluate": evaluate, "report": report}[args.stage](ctx)
        print(json.dumps(result, sort_keys=True, allow_nan=False))
        return 0
    except Exception as exc:
        code = getattr(exc, "code", None)
        if not isinstance(code, str) or not __import__("re").fullmatch(r"[A-Z][A-Z0-9_]{3,100}", code):
            code = "ANALYSIS_CLOSED_STAGE_FAILURE"
        print(json.dumps({"status": "BLOCKED", "stage": args.stage, "code": code}))
        return 78


if __name__ == "__main__":
    raise SystemExit(main())
