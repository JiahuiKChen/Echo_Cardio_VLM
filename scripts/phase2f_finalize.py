#!/usr/bin/env python3
"""Finalize passed Phase 2E evidence without rerunning scientific analyses."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import numbers
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from types import ModuleType
from typing import Any, Iterable

import numpy as np
import pandas as pd


FLOAT_TOLERANCE = 2e-6
FIELD_POLICY_VERSION = "PHASE2F_FINALIZER_FIELD_POLICY_V1"
LEGACY_FINALIZER_SHA256 = (
    "0f697b61d3b79e2e5fedb204678f0f6a8a95e66e5ff7ffe5ed60e500a99cb6c5"
)
SPLIT_MAP_SHA256 = (
    "c5101cea1d76b38c6bb4517edf4b463b338d7505032cfa40bc8f27ca5b97e517"
)

RECONCILIATION_HASHES = {
    "preserved_phase2er_blocker": (
        "run/aggregate_safe/phase2er_blocker.json",
        "89d79f7992330d18ff3a89e87eb1686e5651a823c8f1151f481735e411a2d692",
    ),
    "preserved_phase2er_log": (
        "logs/jdim_phase2er_r2.o7341470",
        "5c5942f276d78fd82add54366745d5fec9aef5eee8aee5db0dd7f76116513968",
    ),
    "packet_definition_manifest": (
        "run/aggregate_safe/packet_definition_manifest.json",
        "7d656bb7398983a5d8f55ba91a55f30d4a57029ee3fbfdb12cc24dda9e575e97",
    ),
    "prediction_comparison_summary": (
        "run/aggregate_safe/prediction_comparison_summary.csv",
        "fec21f540ec6630d8c82e5bdb27db3da2d17182b05f83cafded9c9b008d4f384",
    ),
    "historical_packet_identity_diagnostic": (
        "run/restricted/historical_packet_identity_diagnostic.json",
        "059307efff76e94b3a71ebb13854de043f348dffba1fa09266e04be726b47601",
    ),
    "historical_metric_reconciliation_policy": (
        "run/aggregate_safe/historical_metric_reconciliation_policy.json",
        "07533c2ae7e2665de2b23fb28128b69718c1f6e73f842b9f9bf4e8dd33c4889c",
    ),
    "historical_metric_discrepancy_profile": (
        "run/aggregate_safe/historical_metric_discrepancy_profile.json",
        "b0a3b8575df3bc2bdb1ae4ccf2c5237c08ff63fa31da93f77a854f5c64010c24",
    ),
    "a5c_or_other_080_reconciliation_certificate": (
        "run/aggregate_safe/comparisons/a5c_or_other_080_reconciliation_certificate.json",
        "b44b7f6499737f30a04ab556caf76dd7c14a1b5131080c066dca32ce832bf936",
    ),
    "a5c_or_other_090_reconciliation_certificate": (
        "run/aggregate_safe/comparisons/a5c_or_other_090_reconciliation_certificate.json",
        "a1e24d6ad8f34df34c401815ec817d73e0b3aa363047196d8f9619e2e2cfb5cc",
    ),
    "a5c_or_other_095_reconciliation_certificate": (
        "run/aggregate_safe/comparisons/a5c_or_other_095_reconciliation_certificate.json",
        "8d394995a72789cb334b8653064f5145045c17cab127aa8abe6f313a8e42a9d6",
    ),
    "main_comparison": (
        "canonical_view/aggregate_safe/original_vs_corrected_main/original_vs_corrected_comparison_provenance.json",
        "ae6b570c5578846ab2a2833dfc0cd873551290a489e497ca298964fc51b46596",
    ),
    "hard_extremes_comparison": (
        "canonical_view/aggregate_safe/original_vs_corrected_hard_extremes/original_vs_corrected_comparison_provenance.json",
        "66196cec0bdef1cb42ea788ae9ffe33bd9164fdd003b0748ba5a948d1283a7b9",
    ),
    "a5c_or_other_070_comparison": (
        "canonical_view/aggregate_safe/original_vs_corrected_echoview/a5c_or_other_070/original_vs_corrected_comparison_provenance.json",
        "4888456d8b4d35955530d1e998f9c67afed0ff6d524ccc2f2f779db2d127ba66",
    ),
    "other_070_comparison": (
        "canonical_view/aggregate_safe/original_vs_corrected_echoview/other_070/original_vs_corrected_comparison_provenance.json",
        "1876e23376232004e944da8427446a9bef457e81e7abc4fc8dd9b18de361bea6",
    ),
    "a5c_or_other_080_comparison": (
        "canonical_view/aggregate_safe/original_vs_corrected_echoview/a5c_or_other_080/original_vs_corrected_comparison_provenance.json",
        "4a82e3ba91102dc260b33439bf0aefde8ce6750a8fc6d55651e6aea18f0ca22a",
    ),
    "a5c_or_other_090_comparison": (
        "canonical_view/aggregate_safe/original_vs_corrected_echoview/a5c_or_other_090/original_vs_corrected_comparison_provenance.json",
        "2e7b983b76585c4ff3ff92bed3d8a2ea967c2ca18edbb7825125cbde5909bec1",
    ),
    "a5c_or_other_095_comparison": (
        "canonical_view/aggregate_safe/original_vs_corrected_echoview/a5c_or_other_095/original_vs_corrected_comparison_provenance.json",
        "dd3613bc1139dca8e1bcf15007c0bb322aabce75c7f043dcc822c6fa47ea7a0d",
    ),
}

WORKFLOW_HASHES = {
    "c1_completion_certificate": (
        "aggregate_safe/dependency_audit/c1_completion_certificate_v2.json",
        "003cc59042a4e54d37009a19f53f2d10840e18e6b6d89434382943bd99bbdd60",
    ),
    "demographics_reuse_certificate": (
        "aggregate_safe/dependency_audit/demographics_reuse_certificate.json",
        "e284db468624a070f16ca8edfd354f53163a1e96c574ed5cf217e40f01bed3f5",
    ),
    "echoview_dependency_matrix": (
        "aggregate_safe/dependency_audit/c1_echoview_dependency_v2.csv",
        "a5b86960aa2fe924274b79599d6cce001e392b4b50589768bfb056c3d324aa53",
    ),
    "changed_input_matrix": (
        "aggregate_safe/dependency_audit/c1_changed_input_matrix_v2.csv",
        "5f38f299a1469938eaeecccaffaa5adc7ee39da99a8c379bb7c7419a40c0fdae",
    ),
    "dependency_audit_summary": (
        "aggregate_safe/dependency_audit/c1_dependency_audit_summary_v2.json",
        "09a0560cc0114a5ad90d58c6b0f7fc0376aac4c14fbeff2d259c93588133b58d",
    ),
}

PACKET_HASHES = {
    "packet_echoview_a5c_or_other_070": (
        "restricted/comparison_inputs/echoview/a5c_or_other_070/comparison_packet_provenance_restricted.json",
        "963e0e3efe8d3d84068b419594f9fabfeb20fa0710712ecfa9b4c175875e8a53",
    ),
    "packet_echoview_a5c_or_other_080": (
        "restricted/comparison_inputs/echoview/a5c_or_other_080/comparison_packet_provenance_restricted.json",
        "37d3e1aa6042575d35ed3b7d3538cc9b267319dd5200feefb4e6a87d076f3190",
    ),
    "packet_echoview_a5c_or_other_090": (
        "restricted/comparison_inputs/echoview/a5c_or_other_090/comparison_packet_provenance_restricted.json",
        "b1007fb580d53629aff92ea0cef2171b4baabd7a7d772f36c02cb3f3b13dc4c5",
    ),
    "packet_echoview_a5c_or_other_095": (
        "restricted/comparison_inputs/echoview/a5c_or_other_095/comparison_packet_provenance_restricted.json",
        "01f42ded56f9eec5274bb1362c74ccaf6f4708da6c3766301ab293ed78caa024",
    ),
    "packet_echoview_other_070": (
        "restricted/comparison_inputs/echoview/other_070/comparison_packet_provenance_restricted.json",
        "e050daaa9c39f954569ed1646e954ec93ef08b844881258cabba5ed9a25410be",
    ),
    "packet_hard_extremes_lvot_vti": (
        "restricted/comparison_inputs/hard_extremes/lvot_vti/comparison_packet_provenance_restricted.json",
        "f25698702b2803d5d57ff9dde9c2307fb28afd2335b95e0ea9dd888dcba89292",
    ),
    "packet_hard_extremes_tapse": (
        "restricted/comparison_inputs/hard_extremes/tapse/comparison_packet_provenance_restricted.json",
        "3d116580fd3d6545a3fa42fcc702ebe0e90acb9d5c8fe59bd059b2d8751fbd9c",
    ),
    "packet_main_lvot_vti": (
        "restricted/comparison_inputs/main/lvot_vti/comparison_packet_provenance_restricted.json",
        "a0b3bdbc8caff14c76bc990f1a079fe4f3f3c02d74979553adaa8f325a24533a",
    ),
    "packet_main_tapse": (
        "restricted/comparison_inputs/main/tapse/comparison_packet_provenance_restricted.json",
        "8021a4b37f427b8b258357a9956b377d9fd3b3d38605b7e127931ec36e7fc25b",
    ),
}

DECISION_HASHES = {
    "duplicate_decision_summary": (
        "aggregate_safe/duplicate_forensics_summary.json",
        "f1e1eec5188b195c542a89b0b13c1436190584b563d39339941b1a72eacc9fcf",
    ),
    "duplicate_decision_rows": (
        "restricted/duplicate_forensics_rows.csv",
        "3e2c357bbd0c145a2b1b29dd00bf56dc8ef8e2ea71a3f6ce9b2fc2e00f35aedc",
    ),
}

CORRECTED_HASHES = {
    "deduplicated_clip_manifest": (
        "aggregation/restricted/deduplicated_clip_manifest.csv",
        "9fe9c06192c0fa24ac900bb31df54460ae3bc605be934435aa83a377521df1e6",
    ),
    "deduplicated_clip_embeddings": (
        "aggregation/restricted/deduplicated_clip_embeddings.npz",
        "54630c477f79b434b46453b9575ad1bda18184bd07664bcaaa4e3d485b1766ba",
    ),
    "corrected_study_manifest": (
        "aggregation/restricted/corrected_study_embedding_manifest.csv",
        "02b8a8942eb94caa8378022fde4561ae7bde5e7b88ba1d32720017d50cc71cdd",
    ),
    "corrected_study_embeddings": (
        "aggregation/restricted/corrected_study_embeddings.npz",
        "c18b7a5ff5bbcf3d7438e284add3980706603d5a5696076564cb35b6652be23b",
    ),
    "lvot_vti_main_metrics": (
        "restricted/analyses/lvot_vti/all_clips/imaging_baseline_metrics.csv",
        "5daf4245a37138a7f1eec1107b553d7f48077acfacab734550a30882ca397b2b",
    ),
    "tapse_main_metrics": (
        "restricted/analyses/tapse/all_clips/imaging_baseline_metrics.csv",
        "2a2b722c97bc11003e4a4ac792a8afe528f6fc925af6af5a7ac94198dd1d9e3f",
    ),
    "lvot_vti_hard_extreme_metrics": (
        "restricted/analyses/lvot_vti/all_clips_exclude_hard_extremes/imaging_baseline_metrics.csv",
        "702dd213aefbed148e3935dbd2da58ec8b179d07d180b6e6cdc45106a91795fd",
    ),
    "tapse_hard_extreme_metrics": (
        "restricted/analyses/tapse/all_clips_exclude_hard_extremes/imaging_baseline_metrics.csv",
        "e8a747938e4df8253ce699bec9db595a8e43632bc20f8de401822f0a36a8b83a",
    ),
    "echoview_a5c_or_other_070_metrics": (
        "restricted/analyses/lvot_vti/echoview_a5c_or_other_0.70_mean/imaging_baseline_metrics.csv",
        "d725725eda97cc85c057eb5c22fe80fc0cdc6d977f154e6892595dca3e776e61",
    ),
    "echoview_other_070_metrics": (
        "restricted/analyses/lvot_vti/echoview_other_0.70_mean/imaging_baseline_metrics.csv",
        "ca65d996080b1cc905c724987815dd961eb143884b81aa5faf5dac23c758b181",
    ),
    "echoview_a5c_or_other_080_metrics": (
        "restricted/analyses/lvot_vti/echoview_a5c_or_other_0.80_mean/imaging_baseline_metrics.csv",
        "b1dbef73536876c276fec083b15765018f9c71b5522159f5571eee9872f5104c",
    ),
    "echoview_a5c_or_other_090_metrics": (
        "restricted/analyses/lvot_vti/echoview_a5c_or_other_0.90_mean/imaging_baseline_metrics.csv",
        "3351b2ad13d0f8276331e88c8632c76a862326c90d60b157fcd3026adbde5302",
    ),
    "echoview_a5c_or_other_095_metrics": (
        "restricted/analyses/lvot_vti/echoview_a5c_or_other_0.95_mean/imaging_baseline_metrics.csv",
        "8b1430407643cbef2c2bdbbba6acefd9773ac7d27154193fe6a81666d000d64c",
    ),
    "nonimage_metrics": (
        "restricted/nonimage_analyses/phase2_nonimage_baseline_metrics.csv",
        "f070aad981c2a611931a046c4f681259511a8b44e2554a2b9e7b4e7136e00b60",
    ),
    "nonimage_binary_metrics": (
        "restricted/nonimage_analyses/phase2_nonimage_baseline_binary_metrics.csv",
        "3206a6bd5f51bbc3729f1517c9bd407b9a1f4709e01c43cbcdd0152d8489f79c",
    ),
    "nonimage_bootstrap_ci": (
        "restricted/nonimage_analyses/phase2_nonimage_baseline_bootstrap_ci.csv",
        "df390180dc873c7cfa7f16f08a98edc4c7c0b8e39913665a9dacc8d8dd2f496c",
    ),
    "nonimage_binary_bootstrap_ci": (
        "restricted/nonimage_analyses/phase2_nonimage_baseline_binary_bootstrap_ci.csv",
        "d1963458b1ec128bdff07ee88529d0058055cffb8333726d2a02c9939dd19b5f",
    ),
    "nonimage_alpha_selection": (
        "restricted/nonimage_analyses/phase2_nonimage_baseline_alpha_selection.csv",
        "500c13c719d2247158da89deeb2eb49ffbf26a185360a698c80b7e3b584d5e6a",
    ),
}

ORIGINAL_NONIMAGE_HASHES = {
    "phase2_nonimage_baseline_metrics.csv": (
        "24247413d06581dfabc39044500e8d9df13670b1b14601f7d88f3d4b2861896f"
    ),
    "phase2_nonimage_baseline_binary_metrics.csv": (
        "0d10b7d91ab31c9dd027776e50bbbc6f7c76ca2b1efa206ce75c5298552765be"
    ),
    "phase2_nonimage_baseline_bootstrap_ci.csv": (
        "e3bbf26094cf221098961a1cd174c51fc6d29d4153e29f3135a067fc6f1e8954"
    ),
    "phase2_nonimage_baseline_binary_bootstrap_ci.csv": (
        "50bbf4712518ecc89fd6c919c4abda8565f0a1bb6e035247729f3c4350e111e6"
    ),
    "phase2_nonimage_baseline_alpha_selection.csv": (
        "9ac9a2ecd8cfcd2d97c91b9036e383eafe75bfde5c8cb716cca685f183a5d260"
    ),
}

NONIMAGE_FILENAMES = (
    "phase2_nonimage_baseline_metrics.csv",
    "phase2_nonimage_baseline_binary_metrics.csv",
    "phase2_nonimage_baseline_bootstrap_ci.csv",
    "phase2_nonimage_baseline_binary_bootstrap_ci.csv",
    "phase2_nonimage_baseline_alpha_selection.csv",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--workflow-root", type=Path, required=True)
    parser.add_argument("--corrected-root", type=Path, required=True)
    parser.add_argument("--phase2-root", type=Path, required=True)
    parser.add_argument("--decision-root", type=Path, required=True)
    parser.add_argument("--reconciliation-root", type=Path, required=True)
    parser.add_argument("--split-map-csv", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--expected-source-commit", required=True)
    parser.add_argument("--field-policy", type=Path, default=None)
    parser.add_argument("--preflight-only", action="store_true")
    return parser.parse_args()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_record(role: str, path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(path)
    return {
        "logical_role": role,
        "sha256": sha256_file(path),
        "size_bytes": path.stat().st_size,
    }


def verify_hashes(
    root: Path, expected: dict[str, tuple[str, str]]
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for role, (relative, expected_hash) in sorted(expected.items()):
        path = root / relative
        actual = sha256_file(path)
        if actual != expected_hash:
            raise ValueError(f"hash mismatch for {role}: {actual}")
        records.append(file_record(role, path))
    return records


def load_field_policy(repo_root: Path, policy_path: Path | None) -> tuple[dict[str, Any], Path]:
    path = (
        policy_path
        if policy_path is not None
        else repo_root / "configs/phase2f_finalizer_field_policy_v1.json"
    ).resolve()
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != FIELD_POLICY_VERSION:
        raise ValueError("unexpected Phase 2F field-policy version")
    if payload.get("floating_point_absolute_tolerance") != FLOAT_TOLERANCE:
        raise ValueError("field-policy floating-point tolerance changed")
    allowed = set(payload.get("allowed_categories", []))
    if not allowed:
        raise ValueError("field-policy category list is empty")
    for table_name, table_policy in payload.get("nonimage_tables", {}).items():
        fields = table_policy.get("fields", {})
        if not fields:
            raise ValueError(f"field policy is empty for {table_name}")
        unknown = set(fields.values()) - allowed
        if unknown:
            raise ValueError(f"unknown categories for {table_name}: {sorted(unknown)}")
        row_identity = set(table_policy.get("row_identity_fields", []))
        if not row_identity or not row_identity.issubset(fields):
            raise ValueError(f"invalid row-identity policy for {table_name}")
    packet_fields = payload.get("comparison_packet_fields", {})
    if not packet_fields or set(packet_fields.values()) - allowed:
        raise ValueError("comparison-packet field policy is incomplete")
    selection = payload.get("selection_protocol", {})
    alpha_grid = selection.get("alpha_grid", [])
    if not alpha_grid or len(alpha_grid) != len(set(alpha_grid)):
        raise ValueError("selection alpha grid is empty or duplicated")
    required_selection = {
        "selection_split": "val",
        "selection_metric": "mean_absolute_error",
        "tie_breaking_rule": "first alpha in frozen grid with a strictly lower validation MAE",
        "test_data_used_for_selection": False,
        "ridge_solver": "svd",
        "features_standardized": True,
    }
    for field, expected in required_selection.items():
        if selection.get(field) != expected:
            raise ValueError(f"selection protocol changed: {field}")
    return payload, path


def normalized_json_leaf_paths(value: Any, prefix: str = "") -> set[str]:
    if isinstance(value, dict):
        paths: set[str] = set()
        for key, item in value.items():
            paths.update(normalized_json_leaf_paths(item, f"{prefix}/{key}"))
        return paths
    if isinstance(value, list):
        if not value:
            return {f"{prefix}/[]"}
        paths: set[str] = set()
        for item in value:
            paths.update(normalized_json_leaf_paths(item, f"{prefix}/[]"))
        return paths
    return {prefix}


def validate_comparison_packet_fields(
    workflow_root: Path, policy: dict[str, Any]
) -> list[dict[str, Any]]:
    expected_paths = set(policy["comparison_packet_fields"])
    summaries: list[dict[str, Any]] = []
    for role, (relative, _) in sorted(PACKET_HASHES.items()):
        path = workflow_root / relative
        payload = json.loads(path.read_text(encoding="utf-8"))
        actual_paths = normalized_json_leaf_paths(payload)
        if actual_paths != expected_paths:
            missing = sorted(expected_paths - actual_paths)
            unknown = sorted(actual_paths - expected_paths)
            raise ValueError(
                f"unclassified comparison-packet fields for {role}: "
                f"missing={missing}, unknown={unknown}"
            )
        if (
            payload.get("status")
            != "HISTORICAL_AGGREGATES_COPIED_AND_CORRELATIONS_RECOMPUTED"
            or payload.get("model_refit") is not False
            or payload.get("prediction_regeneration") is not False
            or payload.get("historical_outputs_modified") is not False
            or payload.get("prediction_rows_modified") is not False
            or payload.get("correlations_computed_directly_from_saved_test_predictions")
            is not True
            or payload.get("bootstrap_n") != 2000
            or payload.get("bootstrap_seed") != 1337
        ):
            raise ValueError(f"comparison-packet protocol changed for {role}")
        categories: dict[str, int] = {}
        for category in policy["comparison_packet_fields"].values():
            categories[category] = categories.get(category, 0) + 1
        summaries.append(
            {
                "logical_role": role,
                "classified_field_count": len(actual_paths),
                "category_counts": categories,
                "all_fields_classified": True,
            }
        )
    return summaries


def validate_original_nonimage_hashes(phase2_root: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    root = phase2_root / "nonimage_baselines"
    for filename, expected_hash in sorted(ORIGINAL_NONIMAGE_HASHES.items()):
        path = root / filename
        actual = sha256_file(path)
        if actual != expected_hash:
            raise ValueError(f"historical non-image output changed: {filename}")
        records.append(file_record(f"historical_nonimage_{filename}", path))
    return records


def _strict_bool(value: Any, label: str) -> bool:
    if not isinstance(value, (bool, np.bool_)):
        raise ValueError(f"{label} is not Boolean")
    return bool(value)


def validate_analysis_dependencies(
    workflow_root: Path, policy: dict[str, Any]
) -> list[dict[str, Any]]:
    matrix = pd.read_csv(
        workflow_root
        / "aggregate_safe/dependency_audit/c1_changed_input_matrix_v2.csv"
    )
    required = {
        "analysis_family",
        "input_changed",
        "rerun",
        "reuse_certificate",
        "status",
    }
    if not required.issubset(matrix.columns):
        raise ValueError("changed-input matrix schema changed")
    records: list[dict[str, Any]] = []
    for baseline_tier, expected in sorted(policy["analysis_dependencies"].items()):
        rows = matrix.loc[matrix["analysis_family"].eq(expected["analysis_family"])]
        if len(rows) != 1:
            raise ValueError(f"dependency row missing for {baseline_tier}")
        row = rows.iloc[0]
        input_changed = _strict_bool(row["input_changed"], "input_changed")
        rerun = _strict_bool(row["rerun"], "rerun")
        reused = _strict_bool(row["reuse_certificate"], "reuse_certificate")
        if input_changed != (expected["input_status"] == "INPUT_CHANGED"):
            raise ValueError(f"input status changed for {baseline_tier}")
        if rerun != expected["rerun"] or reused != expected["formally_reused"]:
            raise ValueError(f"rerun/reuse status changed for {baseline_tier}")
        records.append(
            {
                "baseline_tier": baseline_tier,
                "analysis_family": expected["analysis_family"],
                "input_status": expected["input_status"],
                "corrected_model_rerun": rerun,
                "formally_reused": reused,
            }
        )
    return records


def validate_table_schema(
    source_label: str,
    original: pd.DataFrame,
    corrected: pd.DataFrame,
    policy: dict[str, Any],
) -> dict[str, str]:
    table_policy = policy["nonimage_tables"].get(source_label)
    if table_policy is None:
        raise ValueError(f"unclassified finalizer table: {source_label}")
    classified = table_policy["fields"]
    expected = set(classified)
    for label, frame in (("historical", original), ("corrected", corrected)):
        actual = set(frame.columns)
        if actual != expected:
            raise ValueError(
                f"unclassified fields in {label} {source_label}: "
                f"missing={sorted(expected - actual)}, unknown={sorted(actual - expected)}"
            )
    return classified


def _stable_key(value: Any) -> str:
    if _is_missing(value):
        return "<NA>"
    if isinstance(value, (float, np.floating)) and float(value).is_integer():
        return str(int(value))
    return str(value)


def _affected_rows(frame: pd.DataFrame) -> pd.DataFrame:
    affected = {"study_metadata", "demographics_plus_study_metadata"}
    return frame.loc[frame["baseline_tier"].isin(affected)].copy()


def _pair_table_rows(
    source_label: str,
    original: pd.DataFrame,
    corrected: pd.DataFrame,
    keys: tuple[str, ...],
) -> pd.DataFrame:
    original = _affected_rows(original)
    corrected = _affected_rows(corrected)
    if original.empty or corrected.empty:
        raise ValueError(f"affected non-image rows missing in {source_label}")
    if original.duplicated(list(keys)).any() or corrected.duplicated(list(keys)).any():
        raise ValueError(f"duplicate row keys in {source_label}")
    original["_key"] = original[list(keys)].apply(
        lambda row: tuple(_stable_key(value) for value in row), axis=1
    )
    corrected["_key"] = corrected[list(keys)].apply(
        lambda row: tuple(_stable_key(value) for value in row), axis=1
    )
    if set(original["_key"]) != set(corrected["_key"]):
        raise ValueError(f"row identity changed in affected non-image {source_label}")
    return original.merge(
        corrected,
        on="_key",
        how="inner",
        suffixes=("_original", "_corrected"),
        validate="one_to_one",
    )


def _selected_row(group: pd.DataFrame, label: str) -> pd.Series:
    selected = group.loc[group["selected"].map(lambda value: _strict_bool(value, label))]
    if len(selected) != 1:
        raise ValueError(f"{label} must contain exactly one selected alpha")
    return selected.iloc[0]


def _deterministic_best_alpha(group: pd.DataFrame, alpha_grid: list[float]) -> float:
    indexed = group.set_index("alpha", drop=False)
    best_alpha = alpha_grid[0]
    best_metric = math.inf
    for alpha in alpha_grid:
        if alpha not in indexed.index:
            raise ValueError("alpha grid is incomplete")
        metric = float(indexed.loc[alpha, "val_mae"])
        if not math.isfinite(metric):
            raise ValueError("validation MAE is not finite")
        if metric < best_metric:
            best_alpha = alpha
            best_metric = metric
    return float(best_alpha)


def validate_alpha_selection(
    original: pd.DataFrame,
    corrected: pd.DataFrame,
    policy: dict[str, Any],
) -> list[dict[str, Any]]:
    validate_table_schema("alpha_selection", original, corrected, policy)
    original = _affected_rows(original)
    corrected = _affected_rows(corrected)
    group_fields = ["target", "baseline_tier", "model"]
    original_groups = {
        tuple(key if isinstance(key, tuple) else (key,)): frame
        for key, frame in original.groupby(group_fields, sort=False)
    }
    corrected_groups = {
        tuple(key if isinstance(key, tuple) else (key,)): frame
        for key, frame in corrected.groupby(group_fields, sort=False)
    }
    if set(original_groups) != set(corrected_groups):
        raise ValueError("alpha-selection analysis identity changed")
    alpha_grid = [float(value) for value in policy["selection_protocol"]["alpha_grid"]]
    records: list[dict[str, Any]] = []
    observed_changes: list[dict[str, Any]] = []
    for key in sorted(original_groups):
        historical = original_groups[key]
        current = corrected_groups[key]
        if historical["alpha"].astype(float).tolist() != alpha_grid:
            raise ValueError(f"historical alpha grid changed for {key}")
        if current["alpha"].astype(float).tolist() != alpha_grid:
            raise ValueError(f"corrected alpha grid changed for {key}")
        historical_selected = _selected_row(historical, f"historical {key}")
        corrected_selected = _selected_row(current, f"corrected {key}")
        historical_alpha = float(historical_selected["alpha"])
        corrected_alpha = float(corrected_selected["alpha"])
        if historical_alpha != _deterministic_best_alpha(historical, alpha_grid):
            raise ValueError(f"historical selected alpha is not the validation optimum: {key}")
        if corrected_alpha != _deterministic_best_alpha(current, alpha_grid):
            raise ValueError(f"corrected selected alpha is not the validation optimum: {key}")
        baseline_tier = str(key[1])
        dependency = policy["analysis_dependencies"][baseline_tier]
        changed = historical_alpha != corrected_alpha
        if changed and dependency["input_status"] != "INPUT_CHANGED":
            raise ValueError(f"selected alpha changed for unchanged analysis: {key}")
        current_by_alpha = current.set_index("alpha")
        record = {
            "target": str(key[0]),
            "baseline_tier": baseline_tier,
            "model": str(key[2]),
            "input_status": dependency["input_status"],
            "historical_selected_alpha": historical_alpha,
            "corrected_selected_alpha": corrected_alpha,
            "historical_selected_validation_mae": float(historical_selected["val_mae"]),
            "corrected_validation_mae_at_historical_alpha": float(
                current_by_alpha.loc[historical_alpha, "val_mae"]
            ),
            "corrected_selected_validation_mae": float(corrected_selected["val_mae"]),
            "corrected_selected_minus_historical_candidate_validation_mae": float(
                corrected_selected["val_mae"]
                - current_by_alpha.loc[historical_alpha, "val_mae"]
            ),
            "selected_alpha_changed": changed,
            "exactly_one_selected_historical": True,
            "exactly_one_selected_corrected": True,
            "corrected_alpha_is_deterministic_validation_optimum": True,
            "alpha_grid_unchanged": True,
            "validation_split_unchanged": True,
            "selection_metric_unchanged": True,
            "test_data_used_for_selection": False,
            "selection_status": "VALIDATED",
        }
        records.append(record)
        if changed:
            observed_changes.append(
                {
                    "target": record["target"],
                    "baseline_tier": baseline_tier,
                    "model": record["model"],
                    "historical_alpha": historical_alpha,
                    "corrected_alpha": corrected_alpha,
                }
            )
    expected_changes = policy["selection_protocol"].get(
        "expected_current_selected_alpha_changes", []
    )
    if observed_changes != expected_changes:
        raise ValueError(
            f"selected-alpha change set differs from policy: {observed_changes}"
        )
    return records


def validate_confusion_matrices(
    original: pd.DataFrame,
    corrected: pd.DataFrame,
    policy: dict[str, Any],
) -> list[dict[str, Any]]:
    validate_table_schema("binary_metrics", original, corrected, policy)
    keys = tuple(policy["nonimage_tables"]["binary_metrics"]["row_identity_fields"])
    merged = _pair_table_rows("binary_metrics", original, corrected, keys)
    changes: list[dict[str, Any]] = []
    for _, row in merged.iterrows():
        historical = {name: int(row[f"{name}_original"]) for name in ("tp", "fp", "tn", "fn")}
        current = {name: int(row[f"{name}_corrected"]) for name in ("tp", "fp", "tn", "fn")}
        historical_n = int(row["n_original"])
        corrected_n = int(row["n_corrected"])
        historical_positive = historical["tp"] + historical["fn"]
        corrected_positive = current["tp"] + current["fn"]
        historical_negative = historical["tn"] + historical["fp"]
        corrected_negative = current["tn"] + current["fp"]
        if historical_n != corrected_n:
            raise ValueError("cohort N changed in binary metrics")
        if historical_positive != corrected_positive:
            raise ValueError("observed-positive count changed in binary metrics")
        if historical_negative != corrected_negative:
            raise ValueError("observed-negative count changed in binary metrics")
        if historical_positive + historical_negative != historical_n:
            raise ValueError("historical confusion cells do not sum to N")
        if corrected_positive + corrected_negative != corrected_n:
            raise ValueError("corrected confusion cells do not sum to N")
        if float(row["prevalence_original"]) != float(row["prevalence_corrected"]):
            raise ValueError("observed prevalence changed in binary metrics")
        if historical != current:
            baseline_tier = str(row["baseline_tier_original"])
            if policy["analysis_dependencies"][baseline_tier]["input_status"] != "INPUT_CHANGED":
                raise ValueError("confusion counts changed for unchanged input")
            record = {
                "target": str(row["target_original"]),
                "baseline_tier": baseline_tier,
                "split": str(row["split_original"]),
                "model": str(row["model_original"]),
                "threshold_label": str(row["threshold_label_original"]),
                "threshold_value": float(row["threshold_value_original"]),
                "historical": historical,
                "corrected": current,
                "historical_observed_positive": historical_positive,
                "corrected_observed_positive": corrected_positive,
                "historical_observed_negative": historical_negative,
                "corrected_observed_negative": corrected_negative,
                "historical_n": historical_n,
                "corrected_n": corrected_n,
                "invariants_passed": True,
            }
            changes.append(record)
    if changes != policy.get("expected_current_confusion_changes", []):
        expected = policy.get("expected_current_confusion_changes", [])
        comparable = [
            {
                key: value
                for key, value in record.items()
                if key
                in {
                    "target",
                    "baseline_tier",
                    "split",
                    "model",
                    "threshold_label",
                    "threshold_value",
                    "historical",
                    "corrected",
                }
            }
            for record in changes
        ]
        if comparable != expected:
            raise ValueError(f"confusion-matrix change set differs from policy: {comparable}")
    return changes


def validate_metric_selected_alphas(
    original_metrics: pd.DataFrame,
    corrected_metrics: pd.DataFrame,
    alpha_records: list[dict[str, Any]],
) -> None:
    for record in alpha_records:
        criteria = (
            original_metrics["target"].eq(record["target"])
            & original_metrics["baseline_tier"].eq(record["baseline_tier"])
            & original_metrics["model"].eq(record["model"])
        )
        historical = original_metrics.loc[criteria, "selected_alpha"].dropna().unique()
        criteria = (
            corrected_metrics["target"].eq(record["target"])
            & corrected_metrics["baseline_tier"].eq(record["baseline_tier"])
            & corrected_metrics["model"].eq(record["model"])
        )
        current = corrected_metrics.loc[criteria, "selected_alpha"].dropna().unique()
        if historical.tolist() != [record["historical_selected_alpha"]]:
            raise ValueError("historical metrics selected alpha disagrees with selection table")
        if current.tolist() != [record["corrected_selected_alpha"]]:
            raise ValueError("corrected metrics selected alpha disagrees with selection table")


def semantic_preflight(
    args: argparse.Namespace, policy: dict[str, Any], policy_path: Path
) -> dict[str, Any]:
    selection_source = args.repo_root / policy["selection_protocol"]["selection_source"]
    if sha256_file(selection_source) != policy["selection_protocol"]["selection_source_sha256"]:
        raise ValueError("non-image alpha-selection source changed")
    dependency_records = validate_analysis_dependencies(args.workflow_root, policy)
    packet_records = validate_comparison_packet_fields(args.workflow_root, policy)
    historical_root = args.phase2_root / "nonimage_baselines"
    corrected_root = args.corrected_root / "restricted/nonimage_analyses"
    tables: dict[str, tuple[pd.DataFrame, pd.DataFrame]] = {}
    table_inventory: list[dict[str, Any]] = []
    for source_label, table_policy in sorted(policy["nonimage_tables"].items()):
        filename = table_policy["filename"]
        original = pd.read_csv(historical_root / filename)
        corrected = pd.read_csv(corrected_root / filename)
        fields = validate_table_schema(source_label, original, corrected, policy)
        tables[source_label] = (original, corrected)
        counts: dict[str, int] = {}
        for category in fields.values():
            counts[category] = counts.get(category, 0) + 1
        table_inventory.append(
            {
                "source_table": source_label,
                "classified_field_count": len(fields),
                "category_counts": counts,
                "all_fields_classified": True,
            }
        )
    alpha_records = validate_alpha_selection(*tables["alpha_selection"], policy)
    confusion_records = validate_confusion_matrices(*tables["binary_metrics"], policy)
    validate_metric_selected_alphas(*tables["metrics"], alpha_records)
    comparison_rows = 0
    for source_label, table_policy in sorted(policy["nonimage_tables"].items()):
        comparison_rows += len(
            compare_nonimage_table(
                historical_root / table_policy["filename"],
                corrected_root / table_policy["filename"],
                source_label,
                tuple(table_policy["row_identity_fields"]),
                policy=policy,
                semantic_checks_complete=True,
            )
        )
    return {
        "status": "PHASE2F_ACTUAL_PACKET_PREFLIGHT_PASSED",
        "field_policy_version": policy["schema_version"],
        "field_policy_sha256": sha256_file(policy_path),
        "all_actual_fields_classified": True,
        "comparison_packet_count": len(packet_records),
        "comparison_packet_field_inventory": packet_records,
        "nonimage_table_field_inventory": table_inventory,
        "analysis_dependencies": dependency_records,
        "selection_protocol": {
            key: value
            for key, value in policy["selection_protocol"].items()
            if key not in {"expected_current_selected_alpha_changes"}
        },
        "nonimage_alpha_selection_verification": alpha_records,
        "nonimage_confusion_matrix_changes": confusion_records,
        "semantic_comparison_rows_traversed": comparison_rows,
        "certificate_written": False,
        "scientific_analysis_rerun": False,
    }


def _is_missing(value: Any) -> bool:
    result = pd.isna(value)
    return bool(result) if isinstance(result, (bool, np.bool_)) else False


def _clean_scalar(value: Any) -> Any:
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def compare_scalar_values(
    original: Any,
    corrected: Any,
    *,
    float_tolerance: float = FLOAT_TOLERANCE,
    apply_float_tolerance: bool = True,
) -> dict[str, Any]:
    """Compare one scalar without applying arithmetic to Boolean values."""

    original_missing = _is_missing(original)
    corrected_missing = _is_missing(corrected)
    if original_missing or corrected_missing:
        exact = original_missing and corrected_missing
        return {
            "comparison_type": "matching_missingness",
            "original_value": None if original_missing else _clean_scalar(original),
            "corrected_value": None if corrected_missing else _clean_scalar(corrected),
            "delta_corrected_minus_original": None,
            "exact_match": exact,
            "within_policy": exact,
            "float_tolerance_applied": False,
        }

    original_bool = isinstance(original, (bool, np.bool_))
    corrected_bool = isinstance(corrected, (bool, np.bool_))
    if original_bool or corrected_bool:
        exact = original_bool and corrected_bool and bool(original) == bool(corrected)
        return {
            "comparison_type": "boolean_exact",
            "original_value": bool(original) if original_bool else _clean_scalar(original),
            "corrected_value": bool(corrected) if corrected_bool else _clean_scalar(corrected),
            "delta_corrected_minus_original": None,
            "exact_match": exact,
            "within_policy": exact,
            "float_tolerance_applied": False,
        }

    original_int = isinstance(original, numbers.Integral)
    corrected_int = isinstance(corrected, numbers.Integral)
    if original_int or corrected_int:
        exact = original_int and corrected_int and int(original) == int(corrected)
        return {
            "comparison_type": "integer_exact",
            "original_value": int(original) if original_int else _clean_scalar(original),
            "corrected_value": int(corrected) if corrected_int else _clean_scalar(corrected),
            "delta_corrected_minus_original": None,
            "exact_match": exact,
            "within_policy": exact,
            "float_tolerance_applied": False,
        }

    if isinstance(original, numbers.Real) and isinstance(corrected, numbers.Real):
        old = float(original)
        new = float(corrected)
        delta = new - old
        exact = delta == 0.0
        within_policy = abs(delta) <= float_tolerance if apply_float_tolerance else exact
        return {
            "comparison_type": "floating_point",
            "original_value": old,
            "corrected_value": new,
            "delta_corrected_minus_original": delta,
            "exact_match": exact,
            "within_policy": within_policy,
            "float_tolerance_applied": apply_float_tolerance,
        }

    exact = type(original) is type(corrected) and original == corrected
    return {
        "comparison_type": "exact_scalar",
        "original_value": _clean_scalar(original),
        "corrected_value": _clean_scalar(corrected),
        "delta_corrected_minus_original": None,
        "exact_match": bool(exact),
        "within_policy": bool(exact),
        "float_tolerance_applied": False,
    }


def compare_nonimage_table(
    original_path: Path,
    corrected_path: Path,
    source_label: str,
    keys: tuple[str, ...],
    *,
    policy: dict[str, Any],
    semantic_checks_complete: bool = False,
) -> pd.DataFrame:
    """Compare one non-image table using field meaning and input dependency."""

    original = pd.read_csv(original_path)
    corrected = pd.read_csv(corrected_path)
    classified = validate_table_schema(source_label, original, corrected, policy)
    expected_keys = tuple(
        policy["nonimage_tables"][source_label]["row_identity_fields"]
    )
    if keys != expected_keys:
        raise ValueError(f"row-identity policy changed for {source_label}")
    for frame, label in ((original, "original"), (corrected, "corrected")):
        missing = set(keys) - set(frame.columns)
        if missing:
            raise ValueError(f"{label} {source_label} lacks keys: {sorted(missing)}")
        if "baseline_tier" not in frame.columns:
            raise ValueError(f"{label} {source_label} lacks baseline_tier")
    if not semantic_checks_complete:
        if source_label == "alpha_selection":
            validate_alpha_selection(original, corrected, policy)
        if source_label == "binary_metrics":
            validate_confusion_matrices(original, corrected, policy)
    merged = _pair_table_rows(source_label, original, corrected, keys)
    compared_fields = [field for field in classified if field not in keys]
    exact_categories = {
        "cohort_invariant",
        "identity_invariant",
        "observed_distribution_invariant",
        "protocol_boolean",
        "protocol_invariant",
    }
    derived_categories = {
        "derived_float_output",
        "derived_integer_output",
        "diagnostic_output",
        "selected_alpha_output",
        "selection_boolean_output",
        "validation_metric_output",
    }
    rows: list[dict[str, Any]] = []
    for _, row in merged.iterrows():
        key_text = "|".join(
            f"{key}={_stable_key(row[f'{key}_original'])}" for key in keys
        )
        baseline_tier = _stable_key(row["baseline_tier_original"])
        input_status = policy["analysis_dependencies"][baseline_tier][
            "input_status"
        ]
        for metric in compared_fields:
            category = classified[metric]
            comparison = compare_scalar_values(
                row[f"{metric}_original"],
                row[f"{metric}_corrected"],
                apply_float_tolerance=False,
            )
            if (
                comparison["comparison_type"] == "matching_missingness"
                and not comparison["exact_match"]
            ):
                raise ValueError(
                    f"missingness changed in {source_label}: {key_text}|field={metric}"
                )
            difference_allowed = False
            semantic_within_policy = bool(comparison["exact_match"])
            if category in exact_categories:
                if not comparison["exact_match"]:
                    raise ValueError(
                        f"exact invariant changed in {source_label}: "
                        f"{key_text}|field={metric}"
                    )
            elif category in derived_categories:
                difference_allowed = input_status == "INPUT_CHANGED"
                if not difference_allowed and not comparison["exact_match"]:
                    raise ValueError(
                        f"derived output changed for reused input in {source_label}: "
                        f"{key_text}|field={metric}"
                    )
                semantic_within_policy = bool(
                    comparison["exact_match"] or difference_allowed
                )
            else:
                raise ValueError(
                    f"unsupported field category in {source_label}: {metric}={category}"
                )
            rows.append(
                {
                    "source_table": source_label,
                    "target": _stable_key(row["target_original"]),
                    "baseline_tier": baseline_tier,
                    "stratum": key_text,
                    "metric": metric,
                    **comparison,
                    "policy_category": category,
                    "analysis_input_status": input_status,
                    "difference_allowed": difference_allowed,
                    "within_policy": semantic_within_policy,
                    "status": (
                        "UNCHANGED"
                        if comparison["exact_match"]
                        else "ACCEPTED_DERIVED_CHANGE"
                    ),
                }
            )
    return pd.DataFrame(rows)


def load_legacy_finalizer(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location("phase2f_legacy_finalizer", path)
    if spec is None or spec.loader is None:
        raise ImportError(path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def patch_legacy_finalizer(module: ModuleType, policy: dict[str, Any]) -> None:
    module.compare_nonimage_table = lambda old, new, label, keys: compare_nonimage_table(
        old,
        new,
        label,
        keys,
        policy=policy,
        semantic_checks_complete=True,
    )


def run_legacy_finalizer(
    args: argparse.Namespace, output_root: Path, policy: dict[str, Any]
) -> Path:
    finalizer_path = args.workflow_root / "commands/finalize_phase2e_aggregate_safe.py"
    if sha256_file(finalizer_path) != LEGACY_FINALIZER_SHA256:
        raise ValueError("preserved finalizer hash changed")
    module = load_legacy_finalizer(finalizer_path)
    patch_legacy_finalizer(module, policy)
    prior_argv = sys.argv
    sys.argv = [
        str(finalizer_path),
        "--workflow-root",
        str(args.workflow_root),
        "--corrected-root",
        str(args.corrected_root),
        "--phase2-root",
        str(args.phase2_root),
        "--output-root",
        str(output_root),
        "--script-sha256",
        LEGACY_FINALIZER_SHA256,
        "--comparison-repair-job-id",
        os.environ.get("JOB_ID", "UNSCHEDULED"),
    ]
    try:
        result = int(module.main())
    finally:
        sys.argv = prior_argv
    if result != 0:
        raise RuntimeError(f"preserved finalizer returned {result}")
    certificate = output_root / "phase2e_extended_completion_certificate.json"
    payload = json.loads(certificate.read_text(encoding="utf-8"))
    if payload.get("status") != "PHASE2E_CORRECTED_CANONICAL_RESULTS_LOCKED":
        raise ValueError("preserved finalizer did not complete")
    return certificate


def inventory_directory(root: Path, prefix: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix()
        records.append(file_record(f"{prefix}_{relative}", path))
    return records


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
        json.dumps(json_clean(payload), indent=2, sort_keys=True, allow_nan=False)
        + "\n",
        encoding="utf-8",
    )


def validate_export_safe(payload: dict[str, Any]) -> None:
    text = json.dumps(json_clean(payload), allow_nan=False)
    banned = (
        "/restricted/project/",
        "/Users/",
        "study_id",
        "subject_id",
        "y_true",
        "y_pred",
    )
    found = [token for token in banned if token in text]
    if found:
        raise ValueError(f"completion certificate contains banned tokens: {found}")


def verify_repo(repo_root: Path, expected_commit: str) -> dict[str, Any]:
    head = subprocess.run(
        ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if head != expected_commit:
        raise ValueError(f"source commit mismatch: {head}")
    status = subprocess.run(
        ["git", "-C", str(repo_root), "status", "--porcelain"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    if status:
        raise ValueError("source worktree is not clean")
    return {"source_commit": head, "source_worktree_clean": True}


def copy_locked_evidence(args: argparse.Namespace, safe_root: Path) -> None:
    sources = {
        "packet_definition_manifest.json": args.reconciliation_root
        / "run/aggregate_safe/packet_definition_manifest.json",
        "prediction_comparison_summary.csv": args.reconciliation_root
        / "run/aggregate_safe/prediction_comparison_summary.csv",
        "historical_metric_reconciliation_policy.json": args.reconciliation_root
        / "run/aggregate_safe/historical_metric_reconciliation_policy.json",
        "historical_metric_discrepancy_profile.json": args.reconciliation_root
        / "run/aggregate_safe/historical_metric_discrepancy_profile.json",
        "duplicate_forensics_summary.json": args.decision_root
        / "aggregate_safe/duplicate_forensics_summary.json",
        "c1_completion_certificate_v2.json": args.workflow_root
        / "aggregate_safe/dependency_audit/c1_completion_certificate_v2.json",
        "demographics_reuse_certificate.json": args.workflow_root
        / "aggregate_safe/dependency_audit/demographics_reuse_certificate.json",
        "a5c_or_other_080_reconciliation_certificate.json": args.reconciliation_root
        / "run/aggregate_safe/comparisons/a5c_or_other_080_reconciliation_certificate.json",
        "a5c_or_other_090_reconciliation_certificate.json": args.reconciliation_root
        / "run/aggregate_safe/comparisons/a5c_or_other_090_reconciliation_certificate.json",
        "a5c_or_other_095_reconciliation_certificate.json": args.reconciliation_root
        / "run/aggregate_safe/comparisons/a5c_or_other_095_reconciliation_certificate.json",
    }
    evidence_root = safe_root / "reused_evidence"
    evidence_root.mkdir(parents=True)
    for name, source in sorted(sources.items()):
        shutil.copy2(source, evidence_root / name)
    for role, (relative, _) in sorted(RECONCILIATION_HASHES.items()):
        if "comparison" not in role or not relative.endswith(".json"):
            continue
        shutil.copy2(
            args.reconciliation_root / relative,
            evidence_root / f"{role}.json",
        )


def main() -> int:
    args = parse_args()
    for name in (
        "repo_root",
        "workflow_root",
        "corrected_root",
        "phase2_root",
        "decision_root",
        "reconciliation_root",
        "split_map_csv",
        "output_root",
    ):
        setattr(args, name, getattr(args, name).resolve())
    if args.field_policy is not None:
        args.field_policy = args.field_policy.resolve()
    if args.output_root.exists():
        raise FileExistsError(f"refusing to overwrite {args.output_root}")

    field_policy, field_policy_path = load_field_policy(
        args.repo_root, args.field_policy
    )
    source = verify_repo(args.repo_root, args.expected_source_commit)
    reconciliation_records = verify_hashes(
        args.reconciliation_root, RECONCILIATION_HASHES
    )
    workflow_records = verify_hashes(args.workflow_root, WORKFLOW_HASHES)
    packet_records = verify_hashes(args.workflow_root, PACKET_HASHES)
    decision_records = verify_hashes(args.decision_root, DECISION_HASHES)
    corrected_records = verify_hashes(args.corrected_root, CORRECTED_HASHES)
    historical_nonimage_records = validate_original_nonimage_hashes(args.phase2_root)
    if sha256_file(args.split_map_csv) != SPLIT_MAP_SHA256:
        raise ValueError("split-map hash mismatch")
    split_record = file_record("frozen_subject_split_map", args.split_map_csv)

    sys.path.insert(0, str(args.repo_root / "scripts"))
    import phase2er_reconcile as reconciliation

    identity_path = (
        args.reconciliation_root
        / "run/restricted/historical_packet_identity_diagnostic.json"
    )
    identity = json.loads(identity_path.read_text(encoding="utf-8"))
    if identity.get("status") != "ALL_HISTORICAL_PACKET_IDENTITIES_VERIFIED":
        raise ValueError("historical packet identities are not locked")
    identities = identity.get("identities", [])
    if not identities or any(
        row.get("selected_alpha_original") != row.get("selected_alpha_corrected")
        for row in identities
    ):
        raise ValueError("primary imaging selected alpha changed")
    rounded_changes = reconciliation.rounded_continuous_metric_changes(
        args.corrected_root
    )
    if rounded_changes:
        raise ValueError("rounded manuscript metrics changed")

    semantic = semantic_preflight(args, field_policy, field_policy_path)
    primary_alpha_comparison = [
        {
            "analysis_label": row.get("analysis_label"),
            "target": row.get("target"),
            "selected_alpha_historical": row.get("selected_alpha_original"),
            "selected_alpha_corrected": row.get("selected_alpha_corrected"),
            "selected_alpha_changed": (
                row.get("selected_alpha_original")
                != row.get("selected_alpha_corrected")
            ),
        }
        for row in identities
    ]
    if args.preflight_only:
        preflight = {
            **semantic,
            **source,
            "primary_imaging_alpha_comparison": primary_alpha_comparison,
            "primary_imaging_selected_alphas_changed": False,
            "rounded_primary_metrics_changed": False,
            "output_root_created": False,
        }
        validate_export_safe(preflight)
        print(json.dumps(json_clean(preflight), indent=2, sort_keys=True))
        return 0

    args.output_root.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(
            prefix=f".{args.output_root.name}.", dir=args.output_root.parent
        )
    )
    try:
        legacy_root = temporary / "intermediate/legacy_finalizer"
        legacy_certificate = run_legacy_finalizer(args, legacy_root, field_policy)
        safe_root = temporary / "aggregate_safe"
        safe_root.mkdir(parents=True)
        for filename in (
            "nonimage_original_vs_corrected_metrics.csv",
            "analysis_replacement_ledger.csv",
            "phase2e_canonical_results.json",
            "a5c_only_reuse_certificate.json",
        ):
            shutil.copy2(legacy_root / filename, safe_root / filename)
        copy_locked_evidence(args, safe_root)
        shutil.copy2(
            field_policy_path,
            safe_root / "phase2f_finalizer_field_policy_v1.json",
        )

        canonical_inventory = reconciliation.analysis_output_inventory(
            args.corrected_root, reconciliation.SPECS, "canonical_corrected", True
        )
        superseded_inventory = reconciliation.analysis_output_inventory(
            args.phase2_root, reconciliation.SPECS, "superseded_historical", False
        )
        reused_inventory = inventory_directory(
            args.phase2_root
            / "lvot_vti/view_filtered/lvot_vti_a5c_only_thr070_mean",
            "formally_reused_a5c_only_070",
        )
        for filename in NONIMAGE_FILENAMES:
            reused_inventory.append(
                file_record(
                    f"formally_reused_demographics_source_{filename}",
                    args.phase2_root / "nonimage_baselines" / filename,
                )
            )
        replacement = pd.read_csv(safe_root / "analysis_replacement_ledger.csv")
        reused_families = replacement.loc[
            replacement["canonical_status"].astype(str).eq("UNCHANGED"),
            "analysis_family",
        ].astype(str).tolist()
        superseded_families = replacement.loc[
            replacement["canonical_status"].astype(str).eq("SUPERSEDED"),
            "analysis_family",
        ].astype(str).tolist()

        comparison_policy = {
            "schema_version": FIELD_POLICY_VERSION,
            "status": "SEMANTIC_FIELD_POLICY_LOCKED",
            "boolean_policy": "Python bool and NumPy bool_ use exact equality; arithmetic is prohibited",
            "integer_and_count_policy": "cohort and observed-label counts are exact; named derived prediction counts may change only for INPUT_CHANGED reruns",
            "identity_policy": "strings, labels, hashes, target, split, path identity, and protocol fields use exact equality",
            "missingness_policy": "matching missingness and type-compatible interpretation required; missing is not false",
            "floating_point_policy": "absolute tolerance of 2e-6 applies only to deterministic scalar metrics reconstructed from hash-verified historical serialized packets",
            "floating_point_absolute_tolerance": FLOAT_TOLERANCE,
            "selected_alpha_policy": "exact for INPUT_UNCHANGED analyses; validation-optimum output for INPUT_CHANGED reruns under the frozen selection protocol",
            "exact_invariant_categories": [
                "cohort_invariant",
                "identity_invariant",
                "observed_distribution_invariant",
                "protocol_boolean",
                "protocol_invariant",
                "source_identity",
            ],
            "conditional_derived_output_categories": [
                "derived_float_output",
                "derived_integer_output",
                "diagnostic_output",
                "selected_alpha_output",
                "selection_boolean_output",
                "validation_metric_output",
            ],
            "analysis_dependencies": semantic["analysis_dependencies"],
            "all_actual_fields_classified": semantic["all_actual_fields_classified"],
            "performance_direction_used_for_selection": False,
        }
        write_json(
            safe_root / "phase2f_scalar_comparison_policy.json",
            comparison_policy,
        )

        nonimage_alpha_records = semantic["nonimage_alpha_selection_verification"]
        nonimage_alpha_changes = [
            row for row in nonimage_alpha_records if row["selected_alpha_changed"]
        ]

        completion = {
            "schema_version": "jdim-phase2f-corrected-output-canonical-v1",
            "status": "PHASE2E_CORRECTED_OUTPUTS_CANONICAL",
            **source,
            "finalizer_script_sha256": sha256_file(Path(__file__).resolve()),
            "field_policy_version": field_policy["schema_version"],
            "field_policy_sha256": sha256_file(field_policy_path),
            "field_policy_manifest": file_record(
                "phase2f_finalizer_field_policy",
                safe_root / "phase2f_finalizer_field_policy_v1.json",
            ),
            "preserved_finalizer_sha256": LEGACY_FINALIZER_SHA256,
            "preserved_finalizer_certificate": file_record(
                "preserved_finalizer_certificate", legacy_certificate
            ),
            "duplicate_decision_hashes": decision_records,
            "corrected_input_hashes": [
                record
                for record in corrected_records
                if record["logical_role"]
                in {
                    "deduplicated_clip_manifest",
                    "deduplicated_clip_embeddings",
                    "corrected_study_manifest",
                    "corrected_study_embeddings",
                }
            ],
            "corrected_model_output_hashes": [
                record
                for record in corrected_records
                if record["logical_role"]
                not in {
                    "deduplicated_clip_manifest",
                    "deduplicated_clip_embeddings",
                    "corrected_study_manifest",
                    "corrected_study_embeddings",
                }
            ],
            "comparison_packet_hashes": packet_records,
            "passed_comparison_and_reconciliation_hashes": reconciliation_records,
            "dependency_and_reuse_hashes": workflow_records,
            "historical_nonimage_output_hashes": historical_nonimage_records,
            "frozen_split_map": split_record,
            "semantic_comparison_policy": comparison_policy,
            "actual_packet_preflight": semantic,
            "duplicate_correction": {
                "duplicate_expected_list_row_pairs": 32,
                "rows_removed": 32,
                "affected_training_studies": 1,
                "retention_rule": "one deterministic row per provenance-defined semantic clip",
            },
            "affected_analyses_repeated_under_unchanged_protocol": True,
            "primary_imaging_alpha_comparison": primary_alpha_comparison,
            "primary_imaging_selected_alphas_changed": False,
            "secondary_nonimage_alpha_selection": nonimage_alpha_records,
            "secondary_nonimage_selected_alpha_changes": nonimage_alpha_changes,
            "secondary_nonimage_selected_alphas_changed": bool(
                nonimage_alpha_changes
            ),
            "any_selected_alpha_changed": bool(nonimage_alpha_changes),
            "confusion_matrix_invariant_checks": semantic[
                "nonimage_confusion_matrix_changes"
            ],
            "rounded_primary_manuscript_metrics_changed": False,
            "model_conclusions_changed": False,
            "study_conclusions_changed": False,
            "performance_direction_used_for_selection": False,
            "canonical_corrected_output_inventory": canonical_inventory,
            "superseded_historical_output_inventory": superseded_inventory,
            "formally_reused_output_inventory": reused_inventory,
            "formally_reused_analysis_families": reused_families,
            "superseded_historical_analysis_families": superseded_families,
            "canonical_results": file_record(
                "phase2e_canonical_results",
                safe_root / "phase2e_canonical_results.json",
            ),
            "cohort_flow_run": False,
            "human_audit_run": False,
            "model_refit": False,
            "prediction_regeneration": False,
            "bootstrap_recomputation": False,
            "calibration_recomputation": False,
            "threshold_recomputation": False,
        }
        validate_export_safe(completion)
        certificate = (
            safe_root / "phase2e_corrected_outputs_canonical_certificate.json"
        )
        write_json(certificate, completion)
        os.replace(temporary, args.output_root)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise

    final_certificate = (
        args.output_root
        / "aggregate_safe/phase2e_corrected_outputs_canonical_certificate.json"
    )
    print(
        json.dumps(
            {
                "status": "PHASE2E_CORRECTED_OUTPUTS_CANONICAL",
                "completion_certificate_sha256": sha256_file(final_certificate),
                "primary_imaging_selected_alphas_changed": False,
                "secondary_nonimage_selected_alphas_changed": bool(
                    nonimage_alpha_changes
                ),
                "rounded_primary_manuscript_metrics_changed": False,
                "study_conclusions_changed": False,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
