#!/usr/bin/env python3
"""Bounded Phase 2E-R historical-metric reconciliation and canonicalization."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any, Iterable

import numpy as np
import pandas as pd


POLICY_ID = "HISTORICAL_SERIALIZED_METRIC_RECONCILIATION_V1"
BASE_SOURCE_COMMIT = "7c04d689c0842b6912000612dd4c6e6a334ddd13"
RECONCILIATION_TOLERANCE = 2e-6
FAILED_V1_JOB_ID = "7341084"
FAILED_V1_EXPECTED_HASHES = {
    "commands/__pycache__/phase2er_reconcile.cpython-310.pyc": (
        "8ac053d6635592b57c60ffed7ffcc355aa5067d8ee9276390540473d6b1e086b"
    ),
    "commands/phase2er_reconcile.py": (
        "97a5ddfbeadf87173c5b5820aad3cd23b74686735a36211c2061ddeb309254e3"
    ),
    "commands/run_phase2er_reconciliation.sh": (
        "97b897156f69c0208a2a17b38ae213bc2ea76d2b629f042a4358c999248c897d"
    ),
    "logs/jdim_phase2er_reconcile.o7341084": (
        "a4d0e7f5f94944149e6c13205dbadcd7b9d262626a5efebbd204f47124d4b320"
    ),
    "run/aggregate_safe/phase2er_blocker.json": (
        "2df914b968dae2af7f2d2234f496789426e45386dbc33e24f6d6481502ca9243"
    ),
}


class Blocked(RuntimeError):
    def __init__(self, status: str, message: str) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True, kw_only=True)
class Spec:
    policy_id: str
    label: str
    raw_label: str
    normalized_label: str
    target: str
    original_rel: str
    corrected_rel: str
    packet_rel: str
    comparison_rel: str
    expected_cohort_identity: str
    expected_protocol_identity: str
    already_passed: bool = False
    definition_only: bool = False


PACKET_DEFINITIONS = (
    Spec(
        policy_id=POLICY_ID,
        label="main_lvot_vti",
        raw_label="Main LVOT VTI all-clips",
        normalized_label="main_lvot_vti",
        target="lvot_vti",
        original_rel="lvot_vti/all_clips",
        corrected_rel="restricted/analyses/lvot_vti/all_clips",
        packet_rel="main/lvot_vti",
        comparison_rel="original_vs_corrected_main",
        expected_cohort_identity="subject-level split; exact study, subject, and target rows",
        expected_protocol_identity="prespecified LVOT VTI all-clips Ridge protocol",
        already_passed=True,
    ),
    Spec(
        policy_id=POLICY_ID,
        label="main_tapse",
        raw_label="Main TAPSE all-clips",
        normalized_label="main_tapse",
        target="tapse",
        original_rel="tapse/all_clips",
        corrected_rel="restricted/analyses/tapse/all_clips",
        packet_rel="main/tapse",
        comparison_rel="original_vs_corrected_main",
        expected_cohort_identity="subject-level split; exact study, subject, and target rows",
        expected_protocol_identity="prespecified TAPSE all-clips Ridge protocol",
        already_passed=True,
    ),
    Spec(
        policy_id=POLICY_ID,
        label="hard_extremes_lvot_vti",
        raw_label="LVOT VTI hard-extreme exclusion",
        normalized_label="hard_extremes_lvot_vti",
        target="lvot_vti",
        original_rel="lvot_vti/all_clips_exclude_hard_extremes",
        corrected_rel="restricted/analyses/lvot_vti/all_clips_exclude_hard_extremes",
        packet_rel="hard_extremes/lvot_vti",
        comparison_rel="original_vs_corrected_hard_extremes",
        expected_cohort_identity="subject-level split; exact study, subject, and target rows",
        expected_protocol_identity="prespecified LVOT VTI hard-extreme Ridge protocol",
        already_passed=True,
    ),
    Spec(
        policy_id=POLICY_ID,
        label="hard_extremes_tapse",
        raw_label="TAPSE hard-extreme exclusion",
        normalized_label="hard_extremes_tapse",
        target="tapse",
        original_rel="tapse/all_clips_exclude_hard_extremes",
        corrected_rel="restricted/analyses/tapse/all_clips_exclude_hard_extremes",
        packet_rel="hard_extremes/tapse",
        comparison_rel="original_vs_corrected_hard_extremes",
        expected_cohort_identity="subject-level split; exact study, subject, and target rows",
        expected_protocol_identity="prespecified TAPSE hard-extreme Ridge protocol",
        already_passed=True,
    ),
    Spec(
        policy_id=POLICY_ID,
        label="a5c_or_other_070",
        raw_label="A5C-or-other, threshold 0.70, mean pooling",
        normalized_label="a5c_or_other_070",
        target="lvot_vti",
        original_rel="lvot_vti/view_filtered/lvot_vti_a5c_or_other_thr070_mean",
        corrected_rel="restricted/analyses/lvot_vti/echoview_a5c_or_other_0.70_mean",
        packet_rel="echoview/a5c_or_other_070",
        comparison_rel="original_vs_corrected_echoview/a5c_or_other_070",
        expected_cohort_identity="subject-level split; ECHOVIEW subset; test n=65",
        expected_protocol_identity="A5C-or-other threshold 0.70 mean-pooled Ridge protocol",
        already_passed=True,
    ),
    Spec(
        policy_id=POLICY_ID,
        label="other_070",
        raw_label="Other-only, threshold 0.70, mean pooling",
        normalized_label="other_070",
        target="lvot_vti",
        original_rel="lvot_vti/view_filtered/lvot_vti_other_only_thr070_mean",
        corrected_rel="restricted/analyses/lvot_vti/echoview_other_0.70_mean",
        packet_rel="echoview/other_070",
        comparison_rel="original_vs_corrected_echoview/other_070",
        expected_cohort_identity="subject-level split; ECHOVIEW subset; test n=65",
        expected_protocol_identity="Other-only threshold 0.70 mean-pooled Ridge protocol",
        already_passed=True,
    ),
    Spec(
        policy_id=POLICY_ID,
        label="a5c_only_070",
        raw_label="A5C-only, threshold 0.70, skipped as underpowered",
        normalized_label="a5c_only_070",
        target="lvot_vti",
        original_rel="lvot_vti/view_filtered/lvot_vti_a5c_only_thr070_mean",
        corrected_rel="restricted/analyses/lvot_vti/echoview_a5c_only_0.70_mean",
        packet_rel="echoview/a5c_only_070",
        comparison_rel="original_vs_corrected_echoview/a5c_only_070",
        expected_cohort_identity="subject-level split; ECHOVIEW subset; test n=22",
        expected_protocol_identity="skipped before model fitting because training data were insufficient",
        already_passed=True,
        definition_only=True,
    ),
    Spec(
        policy_id=POLICY_ID,
        label="a5c_or_other_080",
        raw_label="A5C-or-other, threshold 0.80, mean pooling",
        normalized_label="a5c_or_other_080",
        target="lvot_vti",
        original_rel="lvot_vti/view_filtered/lvot_vti_a5c_or_other_thr080_mean",
        corrected_rel="restricted/analyses/lvot_vti/echoview_a5c_or_other_0.80_mean",
        packet_rel="echoview/a5c_or_other_080",
        comparison_rel="original_vs_corrected_echoview/a5c_or_other_080",
        expected_cohort_identity="subject-level split; ECHOVIEW subset; test n=65",
        expected_protocol_identity="A5C-or-other threshold 0.80 mean-pooled Ridge protocol",
    ),
    Spec(
        policy_id=POLICY_ID,
        label="a5c_or_other_090",
        raw_label="A5C-or-other, threshold 0.90, mean pooling",
        normalized_label="a5c_or_other_090",
        target="lvot_vti",
        original_rel="lvot_vti/view_filtered/lvot_vti_a5c_or_other_thr090_mean",
        corrected_rel="restricted/analyses/lvot_vti/echoview_a5c_or_other_0.90_mean",
        packet_rel="echoview/a5c_or_other_090",
        comparison_rel="original_vs_corrected_echoview/a5c_or_other_090",
        expected_cohort_identity="subject-level split; ECHOVIEW subset; test n=65",
        expected_protocol_identity="A5C-or-other threshold 0.90 mean-pooled Ridge protocol",
    ),
    Spec(
        policy_id=POLICY_ID,
        label="a5c_or_other_095",
        raw_label="A5C-or-other, threshold 0.95, mean pooling",
        normalized_label="a5c_or_other_095",
        target="lvot_vti",
        original_rel="lvot_vti/view_filtered/lvot_vti_a5c_or_other_thr095_mean",
        corrected_rel="restricted/analyses/lvot_vti/echoview_a5c_or_other_0.95_mean",
        packet_rel="echoview/a5c_or_other_095",
        comparison_rel="original_vs_corrected_echoview/a5c_or_other_095",
        expected_cohort_identity="subject-level split; ECHOVIEW subset; test n=65",
        expected_protocol_identity="A5C-or-other threshold 0.95 mean-pooled Ridge protocol",
    ),
)

SPECS = tuple(spec for spec in PACKET_DEFINITIONS if not spec.definition_only)
ECHOVIEW_PACKET_LABELS = {
    "a5c_or_other_070",
    "other_070",
    "a5c_only_070",
    "a5c_or_other_080",
    "a5c_or_other_090",
    "a5c_or_other_095",
}


EXPECTED_HASHES = {
    "decision_summary": (
        "decision/aggregate_safe/duplicate_forensics_summary.json",
        "f1e1eec5188b195c542a89b0b13c1436190584b563d39339941b1a72eacc9fcf",
    ),
    "decision_rows": (
        "decision/restricted/duplicate_forensics_rows.csv",
        "3e2c357bbd0c145a2b1b29dd00bf56dc8ef8e2ea71a3f6ce9b2fc2e00f35aedc",
    ),
    "c1_certificate": (
        "workflow/aggregate_safe/dependency_audit/c1_completion_certificate_v2.json",
        "003cc59042a4e54d37009a19f53f2d10840e18e6b6d89434382943bd99bbdd60",
    ),
    "deduplicated_clip_manifest": (
        "corrected/aggregation/restricted/deduplicated_clip_manifest.csv",
        "9fe9c06192c0fa24ac900bb31df54460ae3bc605be934435aa83a377521df1e6",
    ),
    "deduplicated_clip_embeddings": (
        "corrected/aggregation/restricted/deduplicated_clip_embeddings.npz",
        "54630c477f79b434b46453b9575ad1bda18184bd07664bcaaa4e3d485b1766ba",
    ),
    "corrected_study_manifest": (
        "corrected/aggregation/restricted/corrected_study_embedding_manifest.csv",
        "02b8a8942eb94caa8378022fde4561ae7bde5e7b88ba1d32720017d50cc71cdd",
    ),
    "corrected_study_embeddings": (
        "corrected/aggregation/restricted/corrected_study_embeddings.npz",
        "c18b7a5ff5bbcf3d7438e284add3980706603d5a5696076564cb35b6652be23b",
    ),
    "main_comparison": (
        "corrected/aggregate_safe/original_vs_corrected_main/original_vs_corrected_comparison_provenance.json",
        "ae6b570c5578846ab2a2833dfc0cd873551290a489e497ca298964fc51b46596",
    ),
    "hard_comparison": (
        "corrected/aggregate_safe/original_vs_corrected_hard_extremes/original_vs_corrected_comparison_provenance.json",
        "66196cec0bdef1cb42ea788ae9ffe33bd9164fdd003b0748ba5a948d1283a7b9",
    ),
    "echoview_070_comparison": (
        "corrected/aggregate_safe/original_vs_corrected_echoview/a5c_or_other_070/original_vs_corrected_comparison_provenance.json",
        "4888456d8b4d35955530d1e998f9c67afed0ff6d524ccc2f2f779db2d127ba66",
    ),
    "echoview_other_070_comparison": (
        "corrected/aggregate_safe/original_vs_corrected_echoview/other_070/original_vs_corrected_comparison_provenance.json",
        "1876e23376232004e944da8427446a9bef457e81e7abc4fc8dd9b18de361bea6",
    ),
    "lvot_metrics": (
        "corrected/restricted/analyses/lvot_vti/all_clips/imaging_baseline_metrics.csv",
        "5daf4245a37138a7f1eec1107b553d7f48077acfacab734550a30882ca397b2b",
    ),
    "tapse_metrics": (
        "corrected/restricted/analyses/tapse/all_clips/imaging_baseline_metrics.csv",
        "2a2b722c97bc11003e4a4ac792a8afe528f6fc925af6af5a7ac94198dd1d9e3f",
    ),
    "lvot_hard_metrics": (
        "corrected/restricted/analyses/lvot_vti/all_clips_exclude_hard_extremes/imaging_baseline_metrics.csv",
        "702dd213aefbed148e3935dbd2da58ec8b179d07d180b6e6cdc45106a91795fd",
    ),
    "tapse_hard_metrics": (
        "corrected/restricted/analyses/tapse/all_clips_exclude_hard_extremes/imaging_baseline_metrics.csv",
        "e8a747938e4df8253ce699bec9db595a8e43632bc20f8de401822f0a36a8b83a",
    ),
    "echoview_a5c_070_metrics": (
        "corrected/restricted/analyses/lvot_vti/echoview_a5c_or_other_0.70_mean/imaging_baseline_metrics.csv",
        "d725725eda97cc85c057eb5c22fe80fc0cdc6d977f154e6892595dca3e776e61",
    ),
    "echoview_other_070_metrics": (
        "corrected/restricted/analyses/lvot_vti/echoview_other_0.70_mean/imaging_baseline_metrics.csv",
        "ca65d996080b1cc905c724987815dd961eb143884b81aa5faf5dac23c758b181",
    ),
    "echoview_080_metrics": (
        "corrected/restricted/analyses/lvot_vti/echoview_a5c_or_other_0.80_mean/imaging_baseline_metrics.csv",
        "b1dbef73536876c276fec083b15765018f9c71b5522159f5571eee9872f5104c",
    ),
    "echoview_090_metrics": (
        "corrected/restricted/analyses/lvot_vti/echoview_a5c_or_other_0.90_mean/imaging_baseline_metrics.csv",
        "3351b2ad13d0f8276331e88c8632c76a862326c90d60b157fcd3026adbde5302",
    ),
    "echoview_095_metrics": (
        "corrected/restricted/analyses/lvot_vti/echoview_a5c_or_other_0.95_mean/imaging_baseline_metrics.csv",
        "8b1430407643cbef2c2bdbbba6acefd9773ac7d27154193fe6a81666d000d64c",
    ),
}


CONTINUOUS_METRICS = (
    "mae",
    "rmse",
    "r2",
    "pearson",
    "spearman",
    "bias_pred_minus_true",
    "bland_altman_lower",
    "bland_altman_upper",
    "calibration_intercept_true_on_pred",
    "calibration_slope_true_on_pred",
)
BINARY_METRICS = (
    "prevalence",
    "predicted_positive_rate",
    "accuracy",
    "f1",
    "sensitivity",
    "specificity",
    "ppv",
    "npv",
    "auroc_continuous_score",
    "average_precision_continuous_score",
    "tp",
    "fp",
    "tn",
    "fn",
)
PREDICTION_COMPARISON_COLUMNS = (
    "policy",
    "analysis",
    "raw_descriptive_label",
    "normalized_label",
    "target",
    "n_test",
    "original_mae",
    "corrected_mae",
    "mae_difference_corrected_minus_original",
    "original_r2",
    "corrected_r2",
    "r2_difference_corrected_minus_original",
    "original_calibration_intercept",
    "reconstructed_historical_calibration_intercept",
    "corrected_calibration_intercept",
    "maximum_approved_scalar_reconstruction_discrepancy",
    "approved_scalar_tolerance",
    "number_predictions_changed",
    "percentage_predictions_changed_exact",
    "mean_absolute_prediction_change",
    "maximum_absolute_prediction_change",
    "original_corrected_prediction_correlation",
    "comparison_status",
    "comparison_provenance_sha256",
)


def validate_packet_definitions(
    definitions: Iterable[Spec] = PACKET_DEFINITIONS,
) -> tuple[Spec, ...]:
    packet_definitions = tuple(definitions)
    if not packet_definitions:
        raise Blocked("BLOCKED_PACKET_DEFINITION", "packet definition set is empty")
    required_text_fields = (
        "policy_id",
        "label",
        "raw_label",
        "normalized_label",
        "target",
        "original_rel",
        "corrected_rel",
        "packet_rel",
        "comparison_rel",
        "expected_cohort_identity",
        "expected_protocol_identity",
    )
    for spec in packet_definitions:
        for field in required_text_fields:
            value = getattr(spec, field)
            if not isinstance(value, str) or not value.strip():
                raise Blocked(
                    "BLOCKED_PACKET_DEFINITION",
                    f"{spec.label or '<unlabeled>'} has an empty {field}",
                )
        if spec.policy_id != POLICY_ID:
            raise Blocked(
                "BLOCKED_PACKET_DEFINITION",
                f"unexpected policy identifier for {spec.label}",
            )
        if not re.fullmatch(r"[a-z0-9_]+", spec.normalized_label):
            raise Blocked(
                "BLOCKED_PACKET_DEFINITION",
                f"normalized label is malformed for {spec.label}",
            )
        if spec.raw_label == spec.normalized_label:
            raise Blocked(
                "BLOCKED_PACKET_DEFINITION",
                f"raw and normalized labels were interchanged for {spec.label}",
            )
        if Path(spec.original_rel).is_absolute() or Path(spec.corrected_rel).is_absolute():
            raise Blocked(
                "BLOCKED_PACKET_DEFINITION",
                f"packet paths must be relative for {spec.label}",
            )
    labels = [spec.label for spec in packet_definitions]
    normalized = [spec.normalized_label for spec in packet_definitions]
    if len(labels) != len(set(labels)) or len(normalized) != len(set(normalized)):
        raise Blocked(
            "BLOCKED_PACKET_DEFINITION",
            "packet labels and normalized labels must be unique",
        )
    echoview_labels = {
        spec.normalized_label
        for spec in packet_definitions
        if spec.normalized_label in ECHOVIEW_PACKET_LABELS
    }
    if echoview_labels != ECHOVIEW_PACKET_LABELS:
        missing = sorted(ECHOVIEW_PACKET_LABELS - echoview_labels)
        raise Blocked(
            "BLOCKED_PACKET_DEFINITION",
            f"ECHOVIEW packet definition set is incomplete: {missing}",
        )
    return packet_definitions


def packet_definition_manifest(definitions: Iterable[Spec]) -> dict[str, Any]:
    records = []
    for spec in definitions:
        records.append(
            {
                "policy_identifier": spec.policy_id,
                "analysis_label": spec.label,
                "raw_descriptive_label": spec.raw_label,
                "normalized_label": spec.normalized_label,
                "target": spec.target,
                "original_packet_path": spec.original_rel,
                "corrected_packet_path": spec.corrected_rel,
                "historical_aggregate_packet_path": spec.packet_rel,
                "comparison_packet_path": spec.comparison_rel,
                "expected_cohort_identity": spec.expected_cohort_identity,
                "expected_protocol_identity": spec.expected_protocol_identity,
                "reused_without_comparison": spec.definition_only,
                "comparison_already_passed": spec.already_passed,
            }
        )
    return {
        "schema_version": "jdim-phase2er-packet-definition-manifest-v2",
        "status": "PACKET_DEFINITIONS_VALIDATED",
        "packet_definition_count": len(records),
        "analysis_packet_count": sum(not spec.definition_only for spec in definitions),
        "echoview_packet_count": sum(
            spec.normalized_label in ECHOVIEW_PACKET_LABELS for spec in definitions
        ),
        "packets": records,
    }


def approved_scalar_tolerance(maximum_discrepancy: float) -> float:
    if not math.isfinite(maximum_discrepancy) or maximum_discrepancy < 0:
        raise Blocked(
            "BLOCKED_RECONCILIATION_TOLERANCE_EXCEEDED",
            "maximum scalar discrepancy must be finite and nonnegative",
        )
    if maximum_discrepancy > RECONCILIATION_TOLERANCE:
        raise Blocked(
            "BLOCKED_RECONCILIATION_TOLERANCE_EXCEEDED",
            f"maximum deterministic scalar discrepancy {maximum_discrepancy} exceeds 2e-6",
        )
    return RECONCILIATION_TOLERANCE


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--workflow-root", type=Path, required=True)
    parser.add_argument("--corrected-root", type=Path, required=True)
    parser.add_argument("--phase2-root", type=Path, required=True)
    parser.add_argument("--decision-root", type=Path, required=True)
    parser.add_argument("--split-map-csv", type=Path, required=True)
    parser.add_argument("--reconciliation-root", type=Path, required=True)
    parser.add_argument("--python-bin", type=Path, required=True)
    parser.add_argument("--failed-v1-root", type=Path, required=True)
    parser.add_argument("--expected-source-commit", required=True)
    parser.add_argument("--expected-driver-sha256", required=True)
    return parser.parse_args()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_record(role: str, path: Path, rows: int | None = None) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(path)
    result: dict[str, Any] = {
        "logical_role": role,
        "sha256": sha256_file(path),
        "size_bytes": path.stat().st_size,
    }
    if rows is not None:
        result["row_count"] = int(rows)
    return result


def json_clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_clean(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_clean(item) for item in value]
    if isinstance(value, np.generic):
        return json_clean(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(json_clean(payload), indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON object required: {path}")
    return payload


def resolve_expected_path(
    token: str,
    workflow_root: Path,
    corrected_root: Path,
    decision_root: Path,
) -> Path:
    prefix, rel = token.split("/", 1)
    roots = {
        "workflow": workflow_root,
        "corrected": corrected_root,
        "decision": decision_root,
    }
    return roots[prefix] / rel


def verify_preserved_failed_root(failed_v1_root: Path) -> list[dict[str, Any]]:
    actual_files = {
        path.relative_to(failed_v1_root).as_posix(): path
        for path in failed_v1_root.rglob("*")
        if path.is_file()
    }
    if set(actual_files) != set(FAILED_V1_EXPECTED_HASHES):
        raise Blocked(
            "BLOCKED_PHASE2ER_PRESERVED_EVIDENCE_MISMATCH",
            "failed V1 reconciliation-root inventory changed",
        )
    records = []
    for rel, expected_hash in sorted(FAILED_V1_EXPECTED_HASHES.items()):
        path = actual_files[rel]
        if sha256_file(path) != expected_hash:
            raise Blocked(
                "BLOCKED_PHASE2ER_PRESERVED_EVIDENCE_MISMATCH",
                f"failed V1 artifact hash changed: {rel}",
            )
        records.append(file_record(f"preserved_failed_v1_{rel}", path))
    return records


def verify_source(
    repo_root: Path,
    expected_source_commit: str,
    expected_driver_sha256: str,
) -> dict[str, Any]:
    if not re.fullmatch(r"[0-9a-f]{40}", expected_source_commit):
        raise Blocked(
            "BLOCKED_PHASE2ER_DRIVER_VERSIONING",
            "expected source commit must be a full lowercase Git SHA",
        )
    if not re.fullmatch(r"[0-9a-f]{64}", expected_driver_sha256):
        raise Blocked(
            "BLOCKED_PHASE2ER_DRIVER_VERSIONING",
            "expected driver hash must be a full lowercase SHA-256",
        )
    head = subprocess.check_output(
        ["git", "-C", str(repo_root), "rev-parse", "HEAD"], text=True
    ).strip()
    status = subprocess.check_output(
        ["git", "-C", str(repo_root), "status", "--short"], text=True
    ).strip()
    if head != expected_source_commit or status:
        raise Blocked(
            "BLOCKED_PHASE2ER_DRIVER_VERSIONING",
            "repair source commit or worktree cleanliness changed",
        )
    ancestor = subprocess.run(
        [
            "git",
            "-C",
            str(repo_root),
            "merge-base",
            "--is-ancestor",
            BASE_SOURCE_COMMIT,
            head,
        ],
        check=False,
    )
    if ancestor.returncode != 0:
        raise Blocked(
            "BLOCKED_PHASE2ER_DRIVER_VERSIONING",
            "repair commit does not descend from the authorized source base",
        )
    driver = repo_root / "scripts/phase2er_reconcile.py"
    actual_driver_sha256 = sha256_file(driver)
    if actual_driver_sha256 != expected_driver_sha256:
        raise Blocked(
            "BLOCKED_PHASE2ER_DRIVER_VERSIONING",
            "versioned reconciliation-driver hash changed",
        )
    compare_cli = repo_root / "scripts/compare_jdim_original_corrected.py"
    cli_text = compare_cli.read_text(encoding="utf-8")
    if "--metric-tolerance" not in cli_text or "default=1e-6" not in cli_text:
        raise Blocked(
            "BLOCKED_RECONCILIATION_SOURCE_MISMATCH",
            "pinned comparator does not expose the verified scalar tolerance CLI",
        )
    return {
        "source_commit": head,
        "authorized_base_commit": BASE_SOURCE_COMMIT,
        "worktree_clean": True,
        "caller_supplied_metric_tolerance_supported": True,
        "source_change_required": True,
        "source_repair_branch_used": True,
        "driver_sha256": actual_driver_sha256,
        "comparison_cli_sha256": sha256_file(compare_cli),
    }


def verify_expected_hashes(
    workflow_root: Path,
    corrected_root: Path,
    decision_root: Path,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for role, (token, expected) in EXPECTED_HASHES.items():
        path = resolve_expected_path(token, workflow_root, corrected_root, decision_root)
        if not path.is_file():
            raise Blocked(
                "BLOCKED_RECONCILIATION_SOURCE_MISMATCH",
                f"required artifact missing: {role}",
            )
        actual = sha256_file(path)
        if actual != expected:
            raise Blocked(
                "BLOCKED_RECONCILIATION_SOURCE_MISMATCH",
                f"required artifact hash changed: {role}",
            )
        records.append(file_record(role, path))
    return records


def significant_digits(text: str) -> int:
    value = text.strip().lower().lstrip("+-")
    if not value or value in {"nan", "inf", "-inf"}:
        return 0
    mantissa = value.split("e", 1)[0].replace(".", "")
    mantissa = mantissa.lstrip("0")
    return len(mantissa)


def decimal_digits(text: str) -> int:
    value = text.strip().lower().split("e", 1)[0]
    return len(value.split(".", 1)[1]) if "." in value else 0


def raw_serialization_stats(path: Path) -> dict[str, Any]:
    columns = ("target_value", "pred_null_median", "pred_ridge")
    stats = {
        column: {"n": 0, "missing": 0, "decimal_digits": [], "significant_digits": []}
        for column in columns
    }
    with path.open("r", encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        if not set(columns).issubset(reader.fieldnames or []):
            raise Blocked(
                "BLOCKED_HISTORICAL_PACKET_IDENTITY",
                "historical prediction CSV lacks required numeric columns",
            )
        for row in reader:
            for column in columns:
                text = str(row[column]).strip()
                stats[column]["n"] += 1
                if text == "":
                    stats[column]["missing"] += 1
                    continue
                stats[column]["decimal_digits"].append(decimal_digits(text))
                stats[column]["significant_digits"].append(significant_digits(text))
    output: dict[str, Any] = {}
    for column, values in stats.items():
        output[column] = {
            "n": values["n"],
            "missing": values["missing"],
            "decimal_digits_min": min(values["decimal_digits"] or [0]),
            "decimal_digits_max": max(values["decimal_digits"] or [0]),
            "significant_digits_min": min(values["significant_digits"] or [0]),
            "significant_digits_max": max(values["significant_digits"] or [0]),
        }
    return output


def float32_roundtrip_stable(frame: pd.DataFrame, columns: Iterable[str]) -> bool:
    original = frame[list(columns)].astype(np.float32)
    buffer = io.StringIO()
    original.to_csv(buffer, index=False)
    buffer.seek(0)
    reloaded = pd.read_csv(buffer).astype(np.float32)
    return all(
        np.array_equal(original[column].to_numpy(), reloaded[column].to_numpy())
        for column in columns
    )


def normalized_prediction_frame(path: Path, expected_target: str) -> pd.DataFrame:
    frame = pd.read_csv(path)
    required = {
        "target",
        "subject_id",
        "study_id",
        "split",
        "target_value",
        "pred_null_median",
        "pred_ridge",
    }
    missing = required - set(frame.columns)
    if missing:
        raise Blocked(
            "BLOCKED_HISTORICAL_PACKET_IDENTITY",
            f"prediction schema missing required columns: {sorted(missing)}",
        )
    if set(frame["target"].astype(str)) != {expected_target}:
        raise Blocked("BLOCKED_HISTORICAL_PACKET_IDENTITY", "target identity changed")
    if frame.duplicated(["target", "study_id"]).any():
        raise Blocked("BLOCKED_HISTORICAL_PACKET_IDENTITY", "duplicate study prediction row")
    for column in ("target_value", "pred_null_median", "pred_ridge"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
        if frame[column].isna().any() or not np.isfinite(frame[column]).all():
            raise Blocked(
                "BLOCKED_HISTORICAL_PACKET_IDENTITY",
                f"missing or nonfinite prediction field: {column}",
            )
    if not set(frame["split"].astype(str)).issubset({"train", "val", "test"}):
        raise Blocked("BLOCKED_HISTORICAL_PACKET_IDENTITY", "unexpected split value")
    return frame


def selected_alpha(directory: Path, target: str) -> float:
    metrics = pd.read_csv(directory / "imaging_baseline_metrics.csv")
    row = metrics[
        metrics["target"].astype(str).eq(target)
        & metrics["split"].astype(str).eq("test")
        & metrics["model"].astype(str).eq("ridge")
    ]
    if len(row) != 1:
        raise Blocked("BLOCKED_HISTORICAL_PACKET_IDENTITY", "test Ridge row is not unique")
    return float(row.iloc[0]["ridge_alpha_selected"])


def verify_packet_provenance(packet_dir: Path, prediction_path: Path) -> None:
    provenance = load_json(packet_dir / "comparison_packet_provenance_restricted.json")
    if provenance.get("status") != "HISTORICAL_AGGREGATES_COPIED_AND_CORRELATIONS_RECOMPUTED":
        raise Blocked("BLOCKED_HISTORICAL_PACKET_IDENTITY", "historical packet status changed")
    prediction_hash = sha256_file(prediction_path)
    candidates = [
        item
        for item in provenance.get("input_files", [])
        if item.get("logical_role") == "historical_predictions"
    ]
    if len(candidates) != 1 or candidates[0].get("sha256") != prediction_hash:
        raise Blocked(
            "BLOCKED_HISTORICAL_PACKET_IDENTITY",
            "historical prediction file does not match its packet provenance",
        )


def protocol_payload(path: Path) -> dict[str, Any]:
    payload = load_json(path)
    fields = (
        "target",
        "targets",
        "clinical_units",
        "analysis_label",
        "ridge_solver",
        "features_standardized",
        "ridge_alphas",
        "n_bootstrap",
        "bootstrap_unit",
        "random_seed",
        "hard_extremes_excluded",
        "sklearn_version",
    )
    return {field: payload.get(field) for field in fields}


def identity_check(
    spec: Spec,
    original_dir: Path,
    corrected_dir: Path,
    packet_dir: Path,
    original_summary: Path,
    corrected_summary: Path,
) -> tuple[dict[str, Any], pd.DataFrame, pd.DataFrame]:
    original_path = original_dir / "imaging_baseline_predictions.csv"
    corrected_path = corrected_dir / "imaging_baseline_predictions.csv"
    verify_packet_provenance(packet_dir, original_path)
    old = normalized_prediction_frame(original_path, spec.target)
    new = normalized_prediction_frame(corrected_path, spec.target)
    key = ["target", "study_id"]
    old_keys = list(map(tuple, old[key].astype(str).itertuples(index=False, name=None)))
    new_keys = list(map(tuple, new[key].astype(str).itertuples(index=False, name=None)))
    if set(old_keys) != set(new_keys):
        raise Blocked("BLOCKED_HISTORICAL_PACKET_IDENTITY", "study-key set changed")
    if old_keys != new_keys:
        raise Blocked(
            "BLOCKED_HISTORICAL_PACKET_IDENTITY",
            f"row ordering changed for {spec.label}",
        )
    old_subjects = set(old["subject_id"].astype(str))
    new_subjects = set(new["subject_id"].astype(str))
    if old_subjects != new_subjects:
        raise Blocked("BLOCKED_HISTORICAL_PACKET_IDENTITY", "subject-key set changed")
    merged = old.merge(new, on=key, suffixes=("_old", "_new"), validate="one_to_one")
    for column in ("subject_id", "split", "target_value"):
        left = merged[f"{column}_old"].to_numpy()
        right = merged[f"{column}_new"].to_numpy()
        if column != "target_value":
            left = left.astype(str)
            right = right.astype(str)
        if not np.array_equal(left, right):
            raise Blocked(
                "BLOCKED_HISTORICAL_PACKET_IDENTITY",
                f"paired prediction identity changed: {column}",
            )
    old_alpha = selected_alpha(packet_dir, spec.target)
    new_alpha = selected_alpha(corrected_dir, spec.target)
    if old_alpha != new_alpha:
        raise Blocked("BLOCKED_HISTORICAL_PACKET_IDENTITY", "selected alpha changed")
    old_protocol = protocol_payload(original_summary)
    new_protocol = protocol_payload(corrected_summary)
    if old_protocol != new_protocol:
        differing_fields = sorted(
            field
            for field in set(old_protocol) | set(new_protocol)
            if old_protocol.get(field) != new_protocol.get(field)
        )
        raise Blocked(
            "BLOCKED_HISTORICAL_PACKET_IDENTITY",
            f"run protocol changed for {spec.label}: {differing_fields}",
        )
    test_old = old[old["split"].astype(str).eq("test")]
    test_new = new[new["split"].astype(str).eq("test")]
    expected_test_n_match = re.search(
        r"test n=(\d+)", spec.expected_cohort_identity, flags=re.IGNORECASE
    )
    if expected_test_n_match:
        expected_test_n = int(expected_test_n_match.group(1))
        if len(test_old) != expected_test_n or len(test_new) != expected_test_n:
            raise Blocked(
                "BLOCKED_HISTORICAL_PACKET_IDENTITY",
                f"expected cohort identity changed for {spec.label}",
            )
    threshold_values = (
        sorted(old["threshold"].dropna().astype(float).unique().tolist())
        if "threshold" in old.columns
        else []
    )
    policy_values = (
        sorted(old["view_policy"].dropna().astype(str).unique().tolist())
        if "view_policy" in old.columns
        else []
    )
    details = {
        "analysis": spec.label,
        "raw_descriptive_label": spec.raw_label,
        "normalized_label": spec.normalized_label,
        "target": spec.target,
        "policy_identifier": spec.policy_id,
        "expected_cohort_identity": spec.expected_cohort_identity,
        "expected_protocol_identity": spec.expected_protocol_identity,
        "identity_status": "EXACT_IDENTITY_VERIFIED",
        "row_order_identical": old_keys == new_keys,
        "study_key_set_identical": True,
        "subject_key_set_identical": True,
        "target_values_identical": True,
        "split_identity_exact": True,
        "test_studies_original": int(test_old["study_id"].nunique()),
        "test_studies_corrected": int(test_new["study_id"].nunique()),
        "test_subjects_original": int(test_old["subject_id"].nunique()),
        "test_subjects_corrected": int(test_new["subject_id"].nunique()),
        "selected_alpha_original": old_alpha,
        "selected_alpha_corrected": new_alpha,
        "analysis_policy_values": policy_values,
        "threshold_values": threshold_values,
        "calibration_orientation": "observed = intercept + slope * predicted",
        "historical_prediction_sha256": sha256_file(original_path),
        "corrected_prediction_sha256": sha256_file(corrected_path),
        "historical_loaded_dtypes": {
            column: str(old[column].dtype)
            for column in ("target_value", "pred_null_median", "pred_ridge")
        },
        "missing_numeric_values": 0,
        "float32_csv_roundtrip_stable": float32_roundtrip_stable(
            old, ("target_value", "pred_null_median", "pred_ridge")
        ),
        "historical_serialization": raw_serialization_stats(original_path),
        "protocol": old_protocol,
    }
    return details, old, new


def finite_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def discrepancy_rows_for_spec(
    spec: Spec,
    old_predictions: pd.DataFrame,
    packet_dir: Path,
    continuous_metrics_fn: Any,
    binary_metrics_fn: Any,
) -> list[dict[str, Any]]:
    stored_metrics = pd.read_csv(packet_dir / "imaging_baseline_metrics.csv")
    stored_binary = pd.read_csv(packet_dir / "imaging_baseline_binary_metrics.csv")
    rows: list[dict[str, Any]] = []
    for model in ("null_median", "ridge"):
        pred_col = f"pred_{model}"
        for split in ("train", "val", "test"):
            subset = old_predictions[old_predictions["split"].astype(str).eq(split)]
            if subset.empty:
                continue
            stored = stored_metrics[
                stored_metrics["target"].astype(str).eq(spec.target)
                & stored_metrics["split"].astype(str).eq(split)
                & stored_metrics["model"].astype(str).eq(model)
            ]
            if len(stored) != 1:
                raise Blocked(
                    "BLOCKED_HISTORICAL_PACKET_IDENTITY",
                    f"stored continuous row is not unique for {spec.label}/{split}/{model}",
                )
            stored_row = stored.iloc[0]
            y64 = subset["target_value"].to_numpy(dtype=np.float64)
            p64 = subset[pred_col].to_numpy(dtype=np.float64)
            y32 = y64.astype(np.float32)
            p32 = p64.astype(np.float32)
            current = continuous_metrics_fn(
                y64, p64, spec.target, split, model, subset["subject_id"].nunique()
            )
            historical_dtype = continuous_metrics_fn(
                y32, p32, spec.target, split, model, subset["subject_id"].nunique()
            )
            for metric in CONTINUOUS_METRICS:
                old_value = finite_number(stored_row.get(metric))
                current_value = finite_number(current.get(metric))
                dtype_value = finite_number(historical_dtype.get(metric))
                if old_value is None or current_value is None or dtype_value is None:
                    continue
                rows.append(
                    {
                        "packet": spec.label,
                        "metric_family": metric,
                        "model": model,
                        "split": split,
                        "n": int(len(subset)),
                        "stored_historical_value": old_value,
                        "reconstructed_historical_float64_value": current_value,
                        "reconstructed_historical_float32_value": dtype_value,
                        "absolute_discrepancy_reloaded_float64": abs(current_value - old_value),
                        "absolute_discrepancy_recast_float32": abs(dtype_value - old_value),
                    }
                )
            current_binary = binary_metrics_fn(y64, p64, spec.target, split, model)
            dtype_binary = binary_metrics_fn(y32, p32, spec.target, split, model)
            for current_row, dtype_row in zip(current_binary, dtype_binary, strict=True):
                threshold = float(current_row["threshold_value"])
                stored_rows = stored_binary[
                    stored_binary["target"].astype(str).eq(spec.target)
                    & stored_binary["split"].astype(str).eq(split)
                    & stored_binary["model"].astype(str).eq(model)
                    & np.isclose(
                        pd.to_numeric(stored_binary["threshold_value"], errors="coerce"),
                        threshold,
                        rtol=0.0,
                        atol=0.0,
                    )
                ]
                if len(stored_rows) != 1:
                    raise Blocked(
                        "BLOCKED_HISTORICAL_PACKET_IDENTITY",
                        f"stored binary row is not unique for {spec.label}/{split}/{model}",
                    )
                stored_binary_row = stored_rows.iloc[0]
                for metric in BINARY_METRICS:
                    old_value = finite_number(stored_binary_row.get(metric))
                    current_value = finite_number(current_row.get(metric))
                    dtype_value = finite_number(dtype_row.get(metric))
                    if old_value is None or current_value is None or dtype_value is None:
                        continue
                    rows.append(
                        {
                            "packet": spec.label,
                            "metric_family": f"binary_{metric}",
                            "model": model,
                            "split": split,
                            "n": int(len(subset)),
                            "stored_historical_value": old_value,
                            "reconstructed_historical_float64_value": current_value,
                            "reconstructed_historical_float32_value": dtype_value,
                            "absolute_discrepancy_reloaded_float64": abs(current_value - old_value),
                            "absolute_discrepancy_recast_float32": abs(dtype_value - old_value),
                        }
                    )
    return rows


def profile_discrepancies(frame: pd.DataFrame) -> list[dict[str, Any]]:
    profiles: list[dict[str, Any]] = []
    for metric, group in frame.groupby("metric_family", sort=True):
        values = group["absolute_discrepancy_reloaded_float64"].to_numpy(dtype=float)
        dtype_values = group["absolute_discrepancy_recast_float32"].to_numpy(dtype=float)
        max_index = int(np.argmax(values))
        maximum_row = group.iloc[max_index]
        profiles.append(
            {
                "metric_family": str(metric),
                "n_compared": int(len(group)),
                "maximum_absolute_discrepancy": float(np.max(values)),
                "median_absolute_discrepancy": float(np.median(values)),
                "p95_absolute_discrepancy": float(np.percentile(values, 95)),
                "packet_with_maximum": str(maximum_row["packet"]),
                "maximum_recast_float32_discrepancy": float(np.max(dtype_values)),
                "serialization_roundtrip_explanation_supported": bool(
                    np.max(dtype_values) <= np.max(values) + 1e-15
                ),
                "row_or_protocol_mismatch": False,
            }
        )
    return profiles


def copytree_verified(source: Path, destination: Path) -> None:
    if destination.exists():
        raise FileExistsError(destination)
    shutil.copytree(source, destination)
    source_files = sorted(path for path in source.rglob("*") if path.is_file())
    destination_files = sorted(path for path in destination.rglob("*") if path.is_file())
    source_rel = [path.relative_to(source) for path in source_files]
    destination_rel = [path.relative_to(destination) for path in destination_files]
    if source_rel != destination_rel:
        raise RuntimeError("copied directory file set differs")
    for rel in source_rel:
        if sha256_file(source / rel) != sha256_file(destination / rel):
            raise RuntimeError(f"copied file hash differs: {rel}")


def build_canonical_view(
    view_root: Path,
    corrected_root: Path,
) -> None:
    if view_root.exists():
        raise FileExistsError(view_root)
    (view_root / "restricted").mkdir(parents=True)
    (view_root / "aggregate_safe").mkdir(parents=True)
    os.symlink(
        corrected_root / "restricted/analyses",
        view_root / "restricted/analyses",
        target_is_directory=True,
    )
    os.symlink(
        corrected_root / "restricted/nonimage_analyses",
        view_root / "restricted/nonimage_analyses",
        target_is_directory=True,
    )
    os.symlink(
        corrected_root / "aggregate_safe/reviewer_metrics",
        view_root / "aggregate_safe/reviewer_metrics",
        target_is_directory=True,
    )
    shutil.copy2(
        corrected_root / "aggregate_safe/corrected_analysis_completion_v1.json",
        view_root / "aggregate_safe/corrected_analysis_completion_v1.json",
    )
    copytree_verified(
        corrected_root / "aggregate_safe/original_vs_corrected_main",
        view_root / "aggregate_safe/original_vs_corrected_main",
    )
    copytree_verified(
        corrected_root / "aggregate_safe/original_vs_corrected_hard_extremes",
        view_root / "aggregate_safe/original_vs_corrected_hard_extremes",
    )
    for label in ("a5c_or_other_070", "other_070"):
        copytree_verified(
            corrected_root / f"aggregate_safe/original_vs_corrected_echoview/{label}",
            view_root / f"aggregate_safe/original_vs_corrected_echoview/{label}",
        )
    for label in (
        "a5c_or_other_070",
        "other_070",
        "a5c_or_other_080",
        "a5c_or_other_090",
        "a5c_or_other_095",
    ):
        source = (
            corrected_root
            / f"restricted/comparisons/original_vs_corrected_echoview/{label}/normalized_summaries"
        )
        destination = (
            view_root
            / f"restricted/comparisons/original_vs_corrected_echoview/{label}/normalized_summaries"
        )
        copytree_verified(source, destination)


def run_missing_comparison(
    spec: Spec,
    args: argparse.Namespace,
    view_root: Path,
    tolerance: float,
) -> Path:
    wrapper = args.workflow_root / "commands/compare_jdim_original_corrected_bool_safe.py"
    original_dir = args.phase2_root / spec.original_rel
    corrected_dir = args.corrected_root / spec.corrected_rel
    packet_dir = args.workflow_root / "restricted/comparison_inputs" / spec.packet_rel
    normalized_dir = (
        view_root
        / f"restricted/comparisons/original_vs_corrected_echoview/{spec.normalized_label}/normalized_summaries"
    )
    output_root = view_root / "aggregate_safe" / spec.comparison_rel
    restricted_provenance = (
        view_root
        / f"restricted/comparisons/original_vs_corrected_echoview/{spec.normalized_label}/input_provenance_restricted.json"
    )
    restricted_provenance.parent.mkdir(parents=True, exist_ok=True)
    command = [
        str(args.python_bin),
        str(wrapper),
        "--original-predictions",
        f"{spec.target}={original_dir / 'imaging_baseline_predictions.csv'}",
        "--corrected-predictions",
        f"{spec.target}={corrected_dir / 'imaging_baseline_predictions.csv'}",
        "--original-summary",
        f"{spec.target}={normalized_dir / 'original_summary_normalized.json'}",
        "--corrected-summary",
        f"{spec.target}={normalized_dir / 'corrected_summary_normalized.json'}",
        "--original-results",
        f"{spec.target}={packet_dir}",
        "--corrected-results",
        f"{spec.target}={corrected_dir}",
        "--frozen-split-map-csv",
        str(args.split_map_csv),
        "--restricted-input-provenance-json",
        str(restricted_provenance),
        "--output-root",
        str(output_root),
        "--metric-tolerance",
        format(tolerance, ".17g"),
    ]
    try:
        subprocess.run(command, check=True)
    except subprocess.CalledProcessError as exc:
        raise Blocked(
            "BLOCKED_ECHOVIEW_COMPARISON",
            f"bounded comparator failed for {spec.label} with exit {exc.returncode}",
        ) from exc
    provenance = load_json(output_root / "original_vs_corrected_comparison_provenance.json")
    if (
        provenance.get("status") != "ORIGINAL_CORRECTED_IDENTITY_VERIFIED"
        or float(provenance.get("metric_reconciliation_absolute_tolerance")) != tolerance
    ):
        raise Blocked(
            "BLOCKED_ECHOVIEW_COMPARISON",
            f"comparison certificate did not record the approved policy tolerance: {spec.label}",
        )
    return output_root


def paired_prediction_correlation(
    original_path: Path,
    corrected_path: Path,
    target: str,
) -> float:
    old = normalized_prediction_frame(original_path, target)
    new = normalized_prediction_frame(corrected_path, target)
    paired = old.merge(
        new,
        on=["target", "study_id"],
        suffixes=("_old", "_new"),
        validate="one_to_one",
    )
    test = paired[
        paired["split_old"].astype(str).eq("test")
        & paired["split_new"].astype(str).eq("test")
    ]
    return float(test["pred_ridge_old"].corr(test["pred_ridge_new"]))


def comparison_certificate(
    spec: Spec,
    comparison_root: Path,
    original_dir: Path,
    corrected_dir: Path,
    discrepancy_rows: pd.DataFrame,
    tolerance: float,
) -> dict[str, Any]:
    changes = pd.read_csv(comparison_root / "original_vs_corrected_prediction_changes.csv")
    test_change = changes[changes["split"].astype(str).eq("test")]
    if len(test_change) != 1:
        raise Blocked("BLOCKED_ECHOVIEW_COMPARISON", "test prediction-change row is not unique")
    change = test_change.iloc[0]
    aggregate = pd.read_csv(comparison_root / "original_vs_corrected_aggregate_metrics.csv")
    stratum = f"target={spec.target}|split=test|model=ridge"
    metric_rows = aggregate[
        aggregate["source_file"].astype(str).eq("imaging_baseline_metrics.csv")
        & aggregate["stratum"].astype(str).eq(stratum)
        & aggregate["metric"].isin(CONTINUOUS_METRICS)
    ]
    metrics = {
        str(row["metric"]): {
            "original": float(row["original_value"]),
            "corrected": float(row["corrected_value"]),
            "delta_corrected_minus_original": float(row["delta_corrected_minus_original"]),
        }
        for _, row in metric_rows.iterrows()
    }
    packet_discrepancy = discrepancy_rows[
        discrepancy_rows["packet"].astype(str).eq(spec.label)
    ]["absolute_discrepancy_reloaded_float64"]
    provenance_path = comparison_root / "original_vs_corrected_comparison_provenance.json"
    return {
        "schema_version": "jdim-phase2er-comparison-certificate-v1",
        "status": "ECHOVIEW_RECONCILIATION_PASSED",
        "analysis": spec.label,
        "raw_descriptive_label": spec.raw_label,
        "normalized_label": spec.normalized_label,
        "policy": spec.policy_id,
        "expected_cohort_identity": spec.expected_cohort_identity,
        "expected_protocol_identity": spec.expected_protocol_identity,
        "approved_absolute_tolerance": tolerance,
        "row_identity_result": "EXACT_IDENTITY_VERIFIED",
        "original_test_n": int(change["n_studies"]),
        "corrected_test_n": int(change["n_studies"]),
        "selected_alpha_original": selected_alpha(original_dir, spec.target),
        "selected_alpha_corrected": selected_alpha(corrected_dir, spec.target),
        "continuous_metrics": metrics,
        "predictions_changed_exact": int(change["n_predictions_changed_exact"]),
        "mean_absolute_prediction_change": float(change["mean_absolute_prediction_change"]),
        "maximum_absolute_prediction_change": float(change["maximum_absolute_prediction_change"]),
        "original_corrected_prediction_correlation": paired_prediction_correlation(
            original_dir / "imaging_baseline_predictions.csv",
            corrected_dir / "imaging_baseline_predictions.csv",
            spec.target,
        ),
        "maximum_historical_reconstruction_discrepancy": float(packet_discrepancy.max()),
        "comparison_provenance_sha256": sha256_file(provenance_path),
    }


def prediction_comparison_summary(
    args: argparse.Namespace,
    view_root: Path,
    discrepancy_rows: pd.DataFrame,
    tolerance: float,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for spec in SPECS:
        comparison_root = view_root / "aggregate_safe" / spec.comparison_rel
        provenance_path = comparison_root / "original_vs_corrected_comparison_provenance.json"
        provenance = load_json(provenance_path)
        if provenance.get("status") != "ORIGINAL_CORRECTED_IDENTITY_VERIFIED":
            raise Blocked(
                "BLOCKED_FINALIZER",
                f"comparison status changed for {spec.label}",
            )
        changes = pd.read_csv(comparison_root / "original_vs_corrected_prediction_changes.csv")
        target_rows = changes[
            changes["target"].astype(str).eq(spec.target)
            & changes["split"].astype(str).eq("test")
        ]
        if len(target_rows) != 1:
            raise Blocked(
                "BLOCKED_FINALIZER",
                f"test prediction comparison is not unique: {spec.label}",
            )
        row = target_rows.iloc[0]
        old = args.phase2_root / spec.original_rel / "imaging_baseline_predictions.csv"
        new = args.corrected_root / spec.corrected_rel / "imaging_baseline_predictions.csv"
        aggregate = pd.read_csv(
            comparison_root / "original_vs_corrected_aggregate_metrics.csv"
        )
        stratum = f"target={spec.target}|split=test|model=ridge"

        def comparison_metric(metric: str) -> pd.Series:
            metric_rows = aggregate[
                aggregate["source_file"].astype(str).eq("imaging_baseline_metrics.csv")
                & aggregate["stratum"].astype(str).eq(stratum)
                & aggregate["metric"].astype(str).eq(metric)
            ]
            if len(metric_rows) != 1:
                raise Blocked(
                    "BLOCKED_FINALIZER",
                    f"comparison metric is not unique: {spec.label}/{metric}",
                )
            return metric_rows.iloc[0]

        mae = comparison_metric("mae")
        r2 = comparison_metric("r2")
        calibration_intercept = comparison_metric(
            "calibration_intercept_true_on_pred"
        )
        spec_discrepancies = discrepancy_rows[
            discrepancy_rows["packet"].astype(str).eq(spec.label)
        ]
        reconstructed_intercept = spec_discrepancies[
            spec_discrepancies["metric_family"].astype(str).eq(
                "calibration_intercept_true_on_pred"
            )
            & spec_discrepancies["model"].astype(str).eq("ridge")
            & spec_discrepancies["split"].astype(str).eq("test")
        ]
        if len(reconstructed_intercept) != 1:
            raise Blocked(
                "BLOCKED_FINALIZER",
                f"historical calibration intercept is not unique: {spec.label}",
            )
        rows.append(
            {
                "policy": spec.policy_id,
                "analysis": spec.label,
                "raw_descriptive_label": spec.raw_label,
                "normalized_label": spec.normalized_label,
                "target": spec.target,
                "n_test": int(row["n_studies"]),
                "original_mae": float(mae["original_value"]),
                "corrected_mae": float(mae["corrected_value"]),
                "mae_difference_corrected_minus_original": float(
                    mae["delta_corrected_minus_original"]
                ),
                "original_r2": float(r2["original_value"]),
                "corrected_r2": float(r2["corrected_value"]),
                "r2_difference_corrected_minus_original": float(
                    r2["delta_corrected_minus_original"]
                ),
                "original_calibration_intercept": float(
                    calibration_intercept["original_value"]
                ),
                "reconstructed_historical_calibration_intercept": float(
                    reconstructed_intercept.iloc[0][
                        "reconstructed_historical_float64_value"
                    ]
                ),
                "corrected_calibration_intercept": float(
                    calibration_intercept["corrected_value"]
                ),
                "maximum_approved_scalar_reconstruction_discrepancy": float(
                    spec_discrepancies[
                        "absolute_discrepancy_reloaded_float64"
                    ].max()
                ),
                "approved_scalar_tolerance": tolerance,
                "number_predictions_changed": int(
                    row["n_predictions_changed_exact"]
                ),
                "percentage_predictions_changed_exact": float(
                    row["percentage_predictions_changed_exact"]
                ),
                "mean_absolute_prediction_change": float(row["mean_absolute_prediction_change"]),
                "maximum_absolute_prediction_change": float(row["maximum_absolute_prediction_change"]),
                "original_corrected_prediction_correlation": paired_prediction_correlation(
                    old, new, spec.target
                ),
                "comparison_status": str(provenance["status"]),
                "comparison_provenance_sha256": sha256_file(provenance_path),
            }
        )
    return pd.DataFrame(rows, columns=PREDICTION_COMPARISON_COLUMNS)


def rounded_continuous_metric_changes(view_root: Path) -> list[dict[str, Any]]:
    precision = {
        "mae": 2,
        "rmse": 2,
        "r2": 3,
        "pearson": 3,
        "spearman": 3,
        "bias_pred_minus_true": 2,
        "bland_altman_lower": 2,
        "bland_altman_upper": 2,
        "calibration_intercept_true_on_pred": 2,
        "calibration_slope_true_on_pred": 3,
    }
    changed: list[dict[str, Any]] = []
    for spec in SPECS:
        aggregate = pd.read_csv(
            view_root
            / "aggregate_safe"
            / spec.comparison_rel
            / "original_vs_corrected_aggregate_metrics.csv"
        )
        stratum = f"target={spec.target}|split=test|model=ridge"
        rows = aggregate[
            aggregate["source_file"].astype(str).eq("imaging_baseline_metrics.csv")
            & aggregate["stratum"].astype(str).eq(stratum)
            & aggregate["metric"].astype(str).isin(precision)
        ]
        for _, row in rows.iterrows():
            metric = str(row["metric"])
            original = finite_number(row["original_value"])
            corrected = finite_number(row["corrected_value"])
            if original is None or corrected is None:
                continue
            digits = precision[metric]
            if round(original, digits) != round(corrected, digits):
                changed.append(
                    {
                        "analysis": spec.label,
                        "metric": metric,
                        "reported_decimal_places": digits,
                        "original": original,
                        "corrected": corrected,
                    }
                )
    return changed


def analysis_output_inventory(
    root: Path,
    specs: Iterable[Spec],
    prefix: str,
    corrected: bool,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for spec in specs:
        analysis_root = root / (spec.corrected_rel if corrected else spec.original_rel)
        for path in sorted(item for item in analysis_root.rglob("*") if item.is_file()):
            rel = path.relative_to(analysis_root).as_posix()
            records.append(file_record(f"{prefix}_{spec.label}_{rel}", path))
    return records


def run_existing_finalizer(
    args: argparse.Namespace,
    view_root: Path,
    output_root: Path,
) -> Path:
    finalizer = args.workflow_root / "commands/finalize_phase2e_aggregate_safe.py"
    expected_hash = "0f697b61d3b79e2e5fedb204678f0f6a8a95e66e5ff7ffe5ed60e500a99cb6c5"
    if sha256_file(finalizer) != expected_hash:
        raise Blocked("BLOCKED_FINALIZER", "existing finalizer hash changed")
    command = [
        str(args.python_bin),
        str(finalizer),
        "--workflow-root",
        str(args.workflow_root),
        "--corrected-root",
        str(view_root),
        "--phase2-root",
        str(args.phase2_root),
        "--output-root",
        str(output_root),
        "--script-sha256",
        expected_hash,
        "--comparison-repair-job-id",
        str(os.environ.get("JOB_ID", "UNSCHEDULED")),
    ]
    try:
        subprocess.run(command, check=True)
    except subprocess.CalledProcessError as exc:
        raise Blocked("BLOCKED_FINALIZER", f"existing finalizer exited {exc.returncode}") from exc
    certificate = output_root / "phase2e_extended_completion_certificate.json"
    if not certificate.is_file():
        raise Blocked("BLOCKED_FINALIZER", "existing finalizer certificate is absent")
    return certificate


def inventory_files(root: Path, prefix: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        records.append(file_record(f"{prefix}_{path.relative_to(root).as_posix()}", path))
    return records


def main() -> int:
    args = parse_args()
    args.repo_root = args.repo_root.resolve()
    args.workflow_root = args.workflow_root.resolve()
    args.corrected_root = args.corrected_root.resolve()
    args.phase2_root = args.phase2_root.resolve()
    args.decision_root = args.decision_root.resolve()
    args.split_map_csv = args.split_map_csv.resolve()
    args.reconciliation_root = args.reconciliation_root.resolve()
    args.python_bin = args.python_bin.resolve()
    args.failed_v1_root = args.failed_v1_root.resolve()

    run_root = args.reconciliation_root / "run"
    view_root = args.reconciliation_root / "canonical_view"
    if run_root.exists() or view_root.exists():
        raise FileExistsError("reconciliation run or canonical view already exists")
    run_root.mkdir(parents=True)
    restricted_root = run_root / "restricted"
    aggregate_root = run_root / "aggregate_safe"
    restricted_root.mkdir()
    aggregate_root.mkdir()

    try:
        source = verify_source(
            args.repo_root,
            args.expected_source_commit,
            args.expected_driver_sha256,
        )
        preserved_failed_v1 = verify_preserved_failed_root(args.failed_v1_root)
        packet_definitions = validate_packet_definitions()
        packet_manifest_path = aggregate_root / "packet_definition_manifest.json"
        write_json(packet_manifest_path, packet_definition_manifest(packet_definitions))
        verified_inputs = verify_expected_hashes(
            args.workflow_root, args.corrected_root, args.decision_root
        )
        sys.path.insert(0, str(args.repo_root / "scripts"))
        from run_tapse_lvot_vti_imaging_baseline import (  # type: ignore
            binary_metrics as source_binary_metrics,
        )
        from run_tapse_lvot_vti_imaging_baseline import (  # type: ignore
            continuous_metrics as source_continuous_metrics,
        )

        source_script = args.repo_root / "scripts/run_tapse_lvot_vti_imaging_baseline.py"
        source_text = source_script.read_text(encoding="utf-8")
        source_dtype_verified = all(
            token in source_text
            for token in (
                "y = frame[\"target_value\"].to_numpy(dtype=np.float32)",
                "model.predict(x).astype(np.float32)",
                "predictions_df.to_csv(predictions_path, index=False)",
                "slope, intercept = np.polyfit(y_pred.astype(float), y_true.astype(float), deg=1)",
            )
        )
        if not source_dtype_verified:
            raise Blocked(
                "BLOCKED_RECONCILIATION_CAUSE_UNVERIFIED",
                "pinned source no longer proves the float32-to-CSV metric lineage",
            )

        identities: list[dict[str, Any]] = []
        all_discrepancies: list[dict[str, Any]] = []
        for spec in SPECS:
            original_dir = args.phase2_root / spec.original_rel
            corrected_dir = args.corrected_root / spec.corrected_rel
            packet_dir = args.workflow_root / "restricted/comparison_inputs" / spec.packet_rel
            if spec.normalized_label in ECHOVIEW_PACKET_LABELS:
                normalized = (
                    args.corrected_root
                    / f"restricted/comparisons/original_vs_corrected_echoview/{spec.normalized_label}/normalized_summaries"
                )
                original_summary = normalized / "original_summary_normalized.json"
                corrected_summary = normalized / "corrected_summary_normalized.json"
            else:
                original_summary = original_dir / "imaging_baseline_summary.json"
                corrected_summary = corrected_dir / "imaging_baseline_summary.json"
            identity, old_predictions, _ = identity_check(
                spec,
                original_dir,
                corrected_dir,
                packet_dir,
                original_summary,
                corrected_summary,
            )
            identities.append(identity)
            all_discrepancies.extend(
                discrepancy_rows_for_spec(
                    spec,
                    old_predictions,
                    packet_dir,
                    source_continuous_metrics,
                    source_binary_metrics,
                )
            )

        discrepancy_frame = pd.DataFrame(all_discrepancies)
        if discrepancy_frame.empty:
            raise Blocked(
                "BLOCKED_RECONCILIATION_CAUSE_UNVERIFIED",
                "historical discrepancy profile is empty",
            )
        profiles = profile_discrepancies(discrepancy_frame)
        max_discrepancy = float(
            discrepancy_frame["absolute_discrepancy_reloaded_float64"].max()
        )
        max_float32_discrepancy = float(
            discrepancy_frame["absolute_discrepancy_recast_float32"].max()
        )
        known = discrepancy_frame[
            discrepancy_frame["packet"].eq("a5c_or_other_080")
            & discrepancy_frame["metric_family"].eq(
                "calibration_intercept_true_on_pred"
            )
            & discrepancy_frame["model"].eq("ridge")
            & discrepancy_frame["split"].eq("test")
        ]
        if len(known) != 1:
            raise Blocked(
                "BLOCKED_RECONCILIATION_CAUSE_UNVERIFIED",
                "known calibration discrepancy is not uniquely reproduced",
            )
        known_current = float(
            known.iloc[0]["absolute_discrepancy_reloaded_float64"]
        )
        known_float32 = float(known.iloc[0]["absolute_discrepancy_recast_float32"])
        if not np.isclose(
            known_current,
            1.5318417361243064e-6,
            rtol=0.0,
            atol=5e-12,
        ):
            raise Blocked(
                "BLOCKED_RECONCILIATION_CAUSE_UNVERIFIED",
                "known historical discrepancy did not reproduce",
            )
        cause_verified = bool(
            source_dtype_verified
            and all(item["float32_csv_roundtrip_stable"] for item in identities)
            and known_float32 < known_current
            and max_float32_discrepancy <= 1e-6
        )
        if not cause_verified:
            raise Blocked(
                "BLOCKED_RECONCILIATION_CAUSE_UNVERIFIED",
                "float32 serialization/reload pathway did not explain the discrepancy",
            )
        tolerance = approved_scalar_tolerance(max_discrepancy)

        discrepancy_path = aggregate_root / "historical_metric_discrepancies.csv"
        discrepancy_frame.to_csv(discrepancy_path, index=False)
        profile_path = aggregate_root / "historical_metric_discrepancy_profile.json"
        profile_payload = {
            "schema_version": "jdim-phase2er-discrepancy-profile-v1",
            "status": "HISTORICAL_SERIALIZED_PATHWAY_VERIFIED",
            "packet_count": len(SPECS),
            "scalar_comparisons": int(len(discrepancy_frame)),
            "maximum_absolute_discrepancy": max_discrepancy,
            "maximum_recast_float32_discrepancy": max_float32_discrepancy,
            "known_a5c_or_other_080_test_intercept_discrepancy": known_current,
            "known_a5c_or_other_080_recast_float32_discrepancy": known_float32,
            "metric_profiles": profiles,
            "row_or_protocol_mismatch_count": 0,
        }
        write_json(profile_path, profile_payload)
        identity_path = restricted_root / "historical_packet_identity_diagnostic.json"
        write_json(
            identity_path,
            {
                "schema_version": "jdim-phase2er-identity-v1",
                "status": "ALL_HISTORICAL_PACKET_IDENTITIES_VERIFIED",
                "source": source,
                "source_metric_script_sha256": sha256_file(source_script),
                "pre_serialization_prediction_dtype": "float32",
                "post_reload_prediction_dtype": "float64",
                "identities": identities,
            },
        )
        policy_path = aggregate_root / "historical_metric_reconciliation_policy.json"
        policy = {
            "schema_version": "jdim-historical-metric-reconciliation-policy-v2",
            "status": "FIXED_RECONCILIATION_POLICY_VERIFIED",
            "policy_identifier": POLICY_ID,
            "uniform_absolute_tolerance": tolerance,
            "maximum_allowed_tolerance": RECONCILIATION_TOLERANCE,
            "tolerance_selection_rule": "fixed a priori at 2e-6; no packet-specific search",
            "maximum_observed_discrepancy": max_discrepancy,
            "cause": (
                "Historical metrics were computed from float32 target/prediction arrays; "
                "CSV reload promotes these columns to float64 before deterministic reconstruction."
            ),
            "applies_only_to": (
                "deterministic scalar metrics reconstructed from the same hash-verified serialized "
                "historical prediction rows"
            ),
            "never_applies_to": [
                "row identity",
                "hashes",
                "study sets",
                "subject sets",
                "split identity",
                "target values",
                "selected alpha",
                "model configuration",
                "prediction-file identity",
                "cohort counts",
                "protocol checks",
            ],
            "packet_specific_override_allowed": False,
            "model_or_metric_definition_changed": False,
            "saved_output_changed": False,
        }
        write_json(policy_path, policy)

        build_canonical_view(view_root, args.corrected_root)
        comparison_certificates: list[dict[str, Any]] = []
        for spec in SPECS:
            if spec.already_passed:
                continue
            comparison_root = run_missing_comparison(spec, args, view_root, tolerance)
            certificate = comparison_certificate(
                spec,
                comparison_root,
                args.phase2_root / spec.original_rel,
                args.corrected_root / spec.corrected_rel,
                discrepancy_frame,
                tolerance,
            )
            comparison_certificates.append(certificate)
            write_json(
                aggregate_root / f"comparisons/{spec.label}_reconciliation_certificate.json",
                certificate,
            )

        comparison_summary = prediction_comparison_summary(
            args, view_root, discrepancy_frame, tolerance
        )
        comparison_summary_path = aggregate_root / "prediction_comparison_summary.csv"
        comparison_summary.to_csv(comparison_summary_path, index=False)
        rounded_metric_changes = rounded_continuous_metric_changes(view_root)

        existing_finalizer_root = run_root / "intermediate/phase2e_finalization"
        existing_certificate = run_existing_finalizer(
            args, view_root, existing_finalizer_root
        )
        final_root = aggregate_root / "phase2e_reconciliation"
        final_root.mkdir(parents=True)
        for path in sorted(existing_finalizer_root.iterdir()):
            if path.is_file():
                shutil.copy2(path, final_root / path.name)
        shutil.copy2(policy_path, final_root / policy_path.name)
        shutil.copy2(profile_path, final_root / profile_path.name)
        shutil.copy2(packet_manifest_path, final_root / packet_manifest_path.name)
        shutil.copy2(comparison_summary_path, final_root / comparison_summary_path.name)
        for certificate in comparison_certificates:
            write_json(
                final_root / f"{certificate['analysis']}_reconciliation_certificate.json",
                certificate,
            )

        comparison_records: list[dict[str, Any]] = []
        for comparison_rel in (
            "original_vs_corrected_main",
            "original_vs_corrected_hard_extremes",
            "original_vs_corrected_echoview/a5c_or_other_070",
            "original_vs_corrected_echoview/other_070",
            "original_vs_corrected_echoview/a5c_or_other_080",
            "original_vs_corrected_echoview/a5c_or_other_090",
            "original_vs_corrected_echoview/a5c_or_other_095",
        ):
            path = (
                view_root
                / "aggregate_safe"
                / comparison_rel
                / "original_vs_corrected_comparison_provenance.json"
            )
            comparison_records.append(file_record(f"comparison_{comparison_rel}", path))

        canonical_results_path = final_root / "phase2e_canonical_results.json"
        canonical_payload = load_json(canonical_results_path)
        replacement_ledger = pd.read_csv(final_root / "analysis_replacement_ledger.csv")
        reused = replacement_ledger[
            replacement_ledger["canonical_status"].astype(str).eq("UNCHANGED")
        ]["analysis_family"].astype(str).tolist()
        superseded = replacement_ledger[
            replacement_ledger["canonical_status"].astype(str).eq("SUPERSEDED")
        ]["analysis_family"].astype(str).tolist()
        corrected_scientific_inventory = analysis_output_inventory(
            args.corrected_root, SPECS, "canonical_corrected", corrected=True
        )
        historical_scientific_inventory = analysis_output_inventory(
            args.phase2_root, SPECS, "superseded_historical", corrected=False
        )
        selected_alphas_changed = any(
            identity["selected_alpha_original"]
            != identity["selected_alpha_corrected"]
            for identity in identities
        )

        completion = {
            "schema_version": "jdim-phase2er-completion-v2",
            "status": "PHASE2E_CORRECTED_OUTPUTS_CANONICAL",
            "source_commit": source["source_commit"],
            "authorized_base_commit": BASE_SOURCE_COMMIT,
            "source_repair_branch": "codex/jdim-phase2er-driver-fix",
            "source_repair_branch_used": True,
            "source_change_required": True,
            "reconciliation_driver_sha256": source["driver_sha256"],
            "reconciliation_policy": POLICY_ID,
            "comparison_absolute_tolerance": tolerance,
            "maximum_historical_reconstruction_discrepancy": max_discrepancy,
            "historical_serialized_pathway_verified": True,
            "strict_identity_and_protocol_checks_preserved": True,
            "duplicate_correction": {
                "proven_duplicate_pairs": 32,
                "rows_removed": 32,
                "affected_training_studies": 1,
                "performance_direction_used_for_selection": False,
            },
            "all_affected_models_and_baselines_rerun_under_unchanged_protocol": True,
            "all_required_comparisons_passed": True,
            "corrected_results_are_canonical": True,
            "scientific_interpretation_changed": False,
            "rounded_manuscript_metrics_changed": bool(rounded_metric_changes),
            "rounded_manuscript_metric_changes": rounded_metric_changes,
            "selected_alphas_changed": selected_alphas_changed,
            "cohort_flow_run": False,
            "human_audit_run": False,
            "manuscript_edited": False,
            "preserved_failed_v1": {
                "job_id": FAILED_V1_JOB_ID,
                "blocker_sha256": FAILED_V1_EXPECTED_HASHES[
                    "run/aggregate_safe/phase2er_blocker.json"
                ],
                "failure_log_sha256": FAILED_V1_EXPECTED_HASHES[
                    "logs/jdim_phase2er_reconcile.o7341084"
                ],
                "inventory": preserved_failed_v1,
            },
            "reused_analysis_families": reused,
            "superseded_historical_analysis_families": superseded,
            "input_files": [
                *verified_inputs,
                *preserved_failed_v1,
                file_record(
                    "versioned_reconciliation_driver",
                    args.repo_root / "scripts/phase2er_reconcile.py",
                ),
                file_record("packet_definition_manifest", packet_manifest_path),
                file_record("source_metric_script", source_script),
                file_record("identity_diagnostic", identity_path),
                file_record("existing_finalizer_certificate", existing_certificate),
                *comparison_records,
            ],
            "canonical_corrected_output_inventory": corrected_scientific_inventory,
            "superseded_historical_output_inventory": historical_scientific_inventory,
            "canonical_certificate_input_inventory": inventory_files(
                final_root, "canonical"
            ),
            "canonical_results_sha256": sha256_file(canonical_results_path),
            "canonical_results_status": canonical_payload.get("status"),
        }
        if selected_alphas_changed:
            raise Blocked("BLOCKED_FINALIZER", "selected alpha changed")
        completion_path = final_root / "phase2e_corrected_outputs_canonical_certificate.json"
        write_json(completion_path, completion)
        completion_hash = sha256_file(completion_path)
        print(
            json.dumps(
                {
                    "status": completion["status"],
                    "policy": POLICY_ID,
                    "approved_tolerance": tolerance,
                    "maximum_discrepancy": max_discrepancy,
                    "comparison_certificates": len(comparison_certificates),
                    "completion_certificate_sha256": completion_hash,
                    "completion_output_name": completion_path.name,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    except Blocked as exc:
        write_json(
            aggregate_root / "phase2er_blocker.json",
            {
                "schema_version": "jdim-phase2er-blocker-v1",
                "status": exc.status,
                "message": str(exc),
            },
        )
        print(json.dumps({"status": exc.status, "message": str(exc)}, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
