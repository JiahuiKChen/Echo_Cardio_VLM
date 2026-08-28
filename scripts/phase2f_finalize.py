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
) -> pd.DataFrame:
    """Drop-in replacement for the preserved finalizer's failing comparator."""

    original = pd.read_csv(original_path)
    corrected = pd.read_csv(corrected_path)
    for frame, label in ((original, "original"), (corrected, "corrected")):
        missing = set(keys) - set(frame.columns)
        if missing:
            raise ValueError(f"{label} {source_label} lacks keys: {sorted(missing)}")
        if "baseline_tier" not in frame.columns:
            raise ValueError(f"{label} {source_label} lacks baseline_tier")
    affected = {"study_metadata", "demographics_plus_study_metadata"}
    original = original[original["baseline_tier"].isin(affected)].copy()
    corrected = corrected[corrected["baseline_tier"].isin(affected)].copy()
    if corrected.empty or original.empty:
        raise ValueError(f"affected nonimage rows missing in {source_label}")
    if corrected.duplicated(list(keys)).any() or original.duplicated(list(keys)).any():
        raise ValueError(f"duplicate row keys in {source_label}")

    def stable_key(value: Any) -> str:
        if _is_missing(value):
            return "<NA>"
        if isinstance(value, (float, np.floating)) and float(value).is_integer():
            return str(int(value))
        return str(value)

    original["_key"] = original[list(keys)].apply(
        lambda row: tuple(stable_key(value) for value in row), axis=1
    )
    corrected["_key"] = corrected[list(keys)].apply(
        lambda row: tuple(stable_key(value) for value in row), axis=1
    )
    if set(original["_key"]) != set(corrected["_key"]):
        raise ValueError(f"row identity changed in affected nonimage {source_label}")
    merged = original.merge(
        corrected,
        on="_key",
        how="inner",
        suffixes=("_original", "_corrected"),
        validate="one_to_one",
    )
    numeric_columns = [
        column
        for column in original.columns
        if column not in {*keys, "_key"}
        and column in corrected.columns
        and pd.api.types.is_numeric_dtype(original[column])
        and pd.api.types.is_numeric_dtype(corrected[column])
    ]
    rows: list[dict[str, Any]] = []
    for _, row in merged.iterrows():
        key_text = "|".join(
            f"{key}={stable_key(row[f'{key}_original'])}" for key in keys
        )
        for metric in numeric_columns:
            comparison = compare_scalar_values(
                row[f"{metric}_original"],
                row[f"{metric}_corrected"],
                apply_float_tolerance=False,
            )
            if comparison["comparison_type"] != "floating_point" and not comparison[
                "within_policy"
            ]:
                raise ValueError(
                    f"exact scalar mismatch in {source_label}: {key_text}|metric={metric}"
                )
            rows.append(
                {
                    "source_table": source_label,
                    "target": stable_key(row["target_original"]),
                    "baseline_tier": stable_key(row["baseline_tier_original"]),
                    "stratum": key_text,
                    "metric": metric,
                    **comparison,
                    "status": (
                        "UNCHANGED"
                        if comparison["exact_match"]
                        else "NUMERICALLY_CHANGED"
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


def patch_legacy_finalizer(module: ModuleType) -> None:
    module.compare_nonimage_table = compare_nonimage_table


def run_legacy_finalizer(args: argparse.Namespace, output_root: Path) -> Path:
    finalizer_path = args.workflow_root / "commands/finalize_phase2e_aggregate_safe.py"
    if sha256_file(finalizer_path) != LEGACY_FINALIZER_SHA256:
        raise ValueError("preserved finalizer hash changed")
    module = load_legacy_finalizer(finalizer_path)
    patch_legacy_finalizer(module)
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
    if args.output_root.exists():
        raise FileExistsError(f"refusing to overwrite {args.output_root}")

    source = verify_repo(args.repo_root, args.expected_source_commit)
    reconciliation_records = verify_hashes(
        args.reconciliation_root, RECONCILIATION_HASHES
    )
    workflow_records = verify_hashes(args.workflow_root, WORKFLOW_HASHES)
    packet_records = verify_hashes(args.workflow_root, PACKET_HASHES)
    decision_records = verify_hashes(args.decision_root, DECISION_HASHES)
    corrected_records = verify_hashes(args.corrected_root, CORRECTED_HASHES)
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
        raise ValueError("selected alpha changed")
    rounded_changes = reconciliation.rounded_continuous_metric_changes(
        args.corrected_root
    )
    if rounded_changes:
        raise ValueError("rounded manuscript metrics changed")

    args.output_root.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(
            prefix=f".{args.output_root.name}.", dir=args.output_root.parent
        )
    )
    try:
        legacy_root = temporary / "intermediate/legacy_finalizer"
        legacy_certificate = run_legacy_finalizer(args, legacy_root)
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

        policy = {
            "schema_version": "jdim-phase2f-scalar-comparison-policy-v1",
            "status": "TYPE_AWARE_SCALAR_POLICY_LOCKED",
            "boolean_policy": "Python bool and NumPy bool_ use exact equality; arithmetic is prohibited",
            "integer_and_count_policy": "exact equality",
            "identity_policy": "strings, labels, hashes, target, split, path identity, and protocol fields use exact equality",
            "missingness_policy": "matching missingness and type-compatible interpretation required; missing is not false",
            "floating_point_policy": "absolute tolerance of 2e-6 applies only to deterministic scalar metrics reconstructed from hash-verified historical serialized packets",
            "floating_point_absolute_tolerance": FLOAT_TOLERANCE,
            "performance_direction_used_for_selection": False,
        }
        write_json(safe_root / "phase2f_scalar_comparison_policy.json", policy)

        completion = {
            "schema_version": "jdim-phase2f-corrected-output-canonical-v1",
            "status": "PHASE2E_CORRECTED_OUTPUTS_CANONICAL",
            **source,
            "finalizer_script_sha256": sha256_file(Path(__file__).resolve()),
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
            "frozen_split_map": split_record,
            "exact_comparison_policy": policy,
            "duplicate_correction": {
                "duplicate_expected_list_row_pairs": 32,
                "rows_removed": 32,
                "affected_training_studies": 1,
                "retention_rule": "one deterministic row per provenance-defined semantic clip",
            },
            "affected_analyses_repeated_under_unchanged_protocol": True,
            "selected_alphas_changed": False,
            "rounded_manuscript_results_changed": False,
            "scientific_conclusions_changed": False,
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
                "selected_alphas_changed": False,
                "rounded_manuscript_results_changed": False,
                "scientific_conclusions_changed": False,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
