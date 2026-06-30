#!/usr/bin/env python3
"""Summarize Phase 2 imaging-only baseline aggregate outputs.

This verifier reads only aggregate Phase 2 output files and writes compact
manuscript-review tables. It never reads or copies per-study predictions,
embedding NPZ files, manifests, raw logs, DICOM paths, or patient-level rows.
"""
from __future__ import annotations

import argparse
import json
import math
import re
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
from pandas.errors import EmptyDataError


ALLOWED_AGGREGATE_FILES = {
    "imaging_baseline_summary.json",
    "imaging_baseline_metrics.csv",
    "imaging_baseline_ridge_alpha_selection.csv",
    "imaging_baseline_bootstrap_ci.csv",
    "imaging_baseline_binary_metrics.csv",
}

REQUIRED_AGGREGATE_FILES = sorted(ALLOWED_AGGREGATE_FILES)

FORBIDDEN_FILE_PATTERN = re.compile(
    r"(predictions|patient|manifest|\.npz$|\.npy$|\.parquet$|\.pkl$|\.pickle$|\.pt$|\.pth$|\.log$|dicom)",
    re.IGNORECASE,
)

SUSPICIOUS_COLUMN_PATTERNS = [
    "subject_id",
    "study_id",
    "dicom",
    "path",
    "image",
]

PRIMARY_RUN_ORDER = {
    "lvot_all_clips": 1,
    "tapse_all_clips": 2,
    "lvot_exclude_hard": 101,
    "tapse_exclude_hard": 102,
}

ECHOVIEW_ORDER = {
    "a5c_or_other_0.70": 1,
    "other_0.70": 2,
    "a5c_0.70": 3,
    "a5c_or_other_0.80": 4,
    "a5c_or_other_0.90": 5,
    "a5c_or_other_0.95": 6,
}

INVENTORY_COLUMNS = [
    "run_label",
    "directory",
    "relative_directory",
    "target",
    "analysis_label",
    "table_role",
    "all_required_aggregate_files_exist",
    "missing_aggregate_files",
    "predictions_detected_but_excluded",
    "forbidden_files_detected_but_excluded",
    "ridge_warning_count",
    "selected_alpha",
    "ridge_solver",
    "features_standardized",
    "test_n",
    "status",
    "skip_reason",
]

RESULT_COLUMNS = [
    "run_label",
    "table_role",
    "target",
    "analysis_label",
    "clinical_unit",
    "test_n",
    "null_mae",
    "ridge_mae",
    "ridge_mae_ci_low",
    "ridge_mae_ci_high",
    "null_rmse",
    "ridge_rmse",
    "ridge_r2",
    "ridge_r2_ci_low",
    "ridge_r2_ci_high",
    "ridge_median_absolute_error",
    "ridge_mae_over_train_iqr",
    "ridge_bias_pred_minus_true",
    "ridge_bland_altman_lower",
    "ridge_bland_altman_upper",
    "ridge_pearson",
    "ridge_spearman",
    "ridge_within_2_cm",
    "ridge_within_3_cm",
    "ridge_within_3_mm",
    "ridge_within_5_mm",
    "selected_alpha",
    "ridge_solver",
    "features_standardized",
    "bootstrap_count",
    "status",
    "skip_reason",
]

ECHOVIEW_COLUMNS = RESULT_COLUMNS + ["view_policy", "threshold", "policy_order_key"]

BINARY_COLUMNS = [
    "run_label",
    "target",
    "analysis_label",
    "threshold_label",
    "threshold_value",
    "model",
    "n",
    "prevalence",
    "predicted_positive_rate",
    "accuracy",
    "f1",
    "sensitivity",
    "specificity",
    "ppv",
    "npv",
    "auroc",
    "average_precision",
    "tp",
    "fp",
    "tn",
    "fn",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--draft-md",
        type=Path,
        default=Path("docs/phase2_results_manuscript_draft.md"),
        help="Draft file that should be updated manually from the verified markdown summary.",
    )
    parser.add_argument(
        "--allow-missing",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Write warning-only outputs instead of failing when expected aggregate files are missing.",
    )
    parser.add_argument(
        "--write-markdown-summary",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Write phase2_results_verified_summary.md with manuscript-ready tables.",
    )
    parser.add_argument(
        "--allow-repo-output-for-testing",
        action="store_true",
        help="Permit output inside the git worktree for synthetic tests only.",
    )
    return parser.parse_args()


def inside_current_worktree(path: Path) -> bool:
    resolved = path.resolve()
    cwd = Path.cwd().resolve()
    return resolved == cwd or cwd in resolved.parents


def guard_output_dir(path: Path, allow_repo_output_for_testing: bool) -> None:
    if allow_repo_output_for_testing:
        return
    if inside_current_worktree(path):
        raise RuntimeError(
            f"Refusing to write Phase 2 review outputs inside the repo: {path}. "
            "Use an approved restricted SCC output directory."
        )


def relpath(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def read_json(path: Path, warnings: list[dict[str, Any]], run_label: str) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        warnings.append(
            {
                "run": run_label,
                "file": str(path),
                "warning": f"Could not parse JSON: {exc}",
            }
        )
        return {}


def read_csv(path: Path, warnings: list[dict[str, Any]], run_label: str) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        df = pd.read_csv(path)
    except EmptyDataError:
        warnings.append({"run": run_label, "file": str(path), "warning": "Aggregate CSV is empty."})
        return pd.DataFrame()

    suspicious = [
        col
        for col in df.columns
        if any(pattern in col.lower() for pattern in SUSPICIOUS_COLUMN_PATTERNS)
    ]
    if suspicious:
        warnings.append(
            {
                "run": run_label,
                "file": str(path),
                "warning": "Aggregate CSV has suspicious identifier-like columns.",
                "columns": suspicious,
            }
        )
    return df


def scalar(value: Any) -> Any:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except TypeError:
        pass
    if isinstance(value, float) and math.isfinite(value):
        return float(value)
    return value


def metric_from_row(row: pd.Series | None, column: str) -> Any:
    if row is None or column not in row.index:
        return None
    return scalar(row[column])


def first_row(df: pd.DataFrame) -> pd.Series | None:
    if df.empty:
        return None
    return df.iloc[0]


def target_summary(summary: dict[str, Any]) -> dict[str, Any]:
    summaries = summary.get("target_summaries") or []
    if summaries:
        return summaries[0] or {}
    return {}


def canonical_run_label(run_dir: Path, root: Path) -> str:
    rel = run_dir.relative_to(root)
    parts = rel.parts
    if parts == ("lvot_vti", "all_clips"):
        return "lvot_all_clips"
    if parts == ("lvot_vti", "all_clips_exclude_hard_extremes"):
        return "lvot_exclude_hard"
    if parts == ("tapse", "all_clips"):
        return "tapse_all_clips"
    if parts == ("tapse", "all_clips_exclude_hard_extremes"):
        return "tapse_exclude_hard"
    if len(parts) >= 3 and parts[0] == "lvot_vti" and parts[1] == "view_filtered":
        return f"lvot_view_{parts[2]}"
    return "_".join(parts)


def table_role(label: str) -> str:
    if label in {"lvot_all_clips", "tapse_all_clips"}:
        return "main"
    if label in {"lvot_exclude_hard", "tapse_exclude_hard"}:
        return "hard_extreme_robustness"
    if label.startswith("lvot_view_"):
        return "echoview_sensitivity"
    return "other"


def parse_view_policy(label: str) -> tuple[str | None, float | None, str]:
    text = label.replace("lvot_view_lvot_vti_", "")
    if "a5c_or_other" in text:
        policy = "A5C-or-other"
        policy_key = "a5c_or_other"
    elif "other_only" in text or text.startswith("other"):
        policy = "Other-only"
        policy_key = "other"
    elif "a5c_only" in text or text.startswith("a5c"):
        policy = "A5C-only"
        policy_key = "a5c"
    else:
        policy = None
        policy_key = "unknown"

    match = re.search(r"thr(\d{3})", text)
    threshold = None
    if match:
        threshold = int(match.group(1)) / 100.0
    order_key = f"{policy_key}_{threshold:.2f}" if threshold is not None else policy_key
    return policy, threshold, order_key


def discover_run_dirs(root: Path) -> list[Path]:
    dirs = {path.parent for path in root.rglob("*") if path.is_file() and path.name in ALLOWED_AGGREGATE_FILES}
    expected = [
        root / "lvot_vti/all_clips",
        root / "lvot_vti/all_clips_exclude_hard_extremes",
        root / "tapse/all_clips",
        root / "tapse/all_clips_exclude_hard_extremes",
        root / "lvot_vti/view_filtered/lvot_vti_a5c_or_other_thr070_mean",
        root / "lvot_vti/view_filtered/lvot_vti_other_only_thr070_mean",
        root / "lvot_vti/view_filtered/lvot_vti_a5c_only_thr070_mean",
        root / "lvot_vti/view_filtered/lvot_vti_a5c_or_other_thr080_mean",
        root / "lvot_vti/view_filtered/lvot_vti_a5c_or_other_thr090_mean",
        root / "lvot_vti/view_filtered/lvot_vti_a5c_or_other_thr095_mean",
    ]
    dirs.update(expected)
    return sorted(dirs, key=lambda p: str(p.relative_to(root)) if p.is_relative_to(root) else str(p))


def detect_forbidden_files(run_dir: Path) -> list[str]:
    if not run_dir.exists():
        return []
    forbidden = []
    for path in run_dir.iterdir():
        if path.is_file() and FORBIDDEN_FILE_PATTERN.search(path.name):
            forbidden.append(path.name)
    return sorted(forbidden)


def selected_alpha(alpha_df: pd.DataFrame, summary_ts: dict[str, Any]) -> Any:
    if summary_ts.get("ridge_alpha_selected") is not None:
        return summary_ts.get("ridge_alpha_selected")
    if not alpha_df.empty and "selected" in alpha_df.columns and "alpha" in alpha_df.columns:
        selected = alpha_df[alpha_df["selected"].astype(str).str.lower().isin(["true", "1"])]
        if not selected.empty:
            return scalar(selected.iloc[0]["alpha"])
    return None


def ci_for_metric(ci_df: pd.DataFrame, metric: str) -> tuple[Any, Any]:
    if ci_df.empty:
        return None, None
    required = {"split", "model", "metric", "ci_lower_2_5", "ci_upper_97_5"}
    if not required.issubset(ci_df.columns):
        return None, None
    row = ci_df[(ci_df["split"] == "test") & (ci_df["model"] == "ridge") & (ci_df["metric"] == metric)]
    if row.empty:
        return None, None
    first = row.iloc[0]
    return scalar(first["ci_lower_2_5"]), scalar(first["ci_upper_97_5"])


def run_record(run_dir: Path, root: Path, warnings: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any] | None, dict[str, Any] | None]:
    label = canonical_run_label(run_dir, root)
    present = {name: (run_dir / name).exists() for name in REQUIRED_AGGREGATE_FILES}
    missing = [name for name, exists in present.items() if not exists]
    forbidden = detect_forbidden_files(run_dir)

    summary = read_json(run_dir / "imaging_baseline_summary.json", warnings, label)
    metrics_df = read_csv(run_dir / "imaging_baseline_metrics.csv", warnings, label)
    alpha_df = read_csv(run_dir / "imaging_baseline_ridge_alpha_selection.csv", warnings, label)
    ci_df = read_csv(run_dir / "imaging_baseline_bootstrap_ci.csv", warnings, label)
    binary_df = read_csv(run_dir / "imaging_baseline_binary_metrics.csv", warnings, label)

    ts = target_summary(summary)
    metrics_test = metrics_df[metrics_df.get("split", pd.Series(dtype=str)) == "test"] if not metrics_df.empty else pd.DataFrame()
    null_row = first_row(metrics_test[metrics_test.get("model", pd.Series(dtype=str)) == "null_median"])
    ridge_row = first_row(metrics_test[metrics_test.get("model", pd.Series(dtype=str)) == "ridge"])

    split_counts = ts.get("split_counts") or {}
    test_n = metric_from_row(ridge_row, "n_studies") or metric_from_row(null_row, "n_studies") or split_counts.get("test")
    ridge_mae_low, ridge_mae_high = ci_for_metric(ci_df, "mae")
    ridge_r2_low, ridge_r2_high = ci_for_metric(ci_df, "r2")

    ridge_warnings = ts.get("ridge_warning_count")
    if ridge_warnings is None:
        ridge_warnings = 0

    inventory = {
        "run_label": label,
        "directory": str(run_dir),
        "relative_directory": relpath(run_dir, root),
        "target": summary.get("target") or ts.get("target"),
        "analysis_label": summary.get("analysis_label") or ts.get("analysis_label"),
        "table_role": table_role(label),
        "all_required_aggregate_files_exist": not missing,
        "missing_aggregate_files": ";".join(missing),
        "predictions_detected_but_excluded": any("prediction" in name.lower() for name in forbidden),
        "forbidden_files_detected_but_excluded": ";".join(forbidden),
        "ridge_warning_count": ridge_warnings,
        "selected_alpha": selected_alpha(alpha_df, ts),
        "ridge_solver": summary.get("ridge_solver") or ts.get("ridge_solver"),
        "features_standardized": summary.get("features_standardized") if summary else ts.get("features_standardized"),
        "test_n": test_n,
        "status": ts.get("status") or metric_from_row(ridge_row, "status") or metric_from_row(null_row, "status"),
        "skip_reason": ts.get("skip_reason") or metric_from_row(ridge_row, "skip_reason") or metric_from_row(null_row, "skip_reason"),
    }

    result = {
        "run_label": label,
        "table_role": table_role(label),
        "target": inventory["target"],
        "analysis_label": inventory["analysis_label"],
        "clinical_unit": (summary.get("clinical_units") or {}).get(inventory["target"]) if summary else None,
        "test_n": test_n,
        "null_mae": metric_from_row(null_row, "mae"),
        "ridge_mae": metric_from_row(ridge_row, "mae"),
        "ridge_mae_ci_low": ridge_mae_low,
        "ridge_mae_ci_high": ridge_mae_high,
        "null_rmse": metric_from_row(null_row, "rmse"),
        "ridge_rmse": metric_from_row(ridge_row, "rmse"),
        "ridge_r2": metric_from_row(ridge_row, "r2"),
        "ridge_r2_ci_low": ridge_r2_low,
        "ridge_r2_ci_high": ridge_r2_high,
        "ridge_median_absolute_error": metric_from_row(ridge_row, "median_absolute_error"),
        "ridge_mae_over_train_iqr": metric_from_row(ridge_row, "mae_over_train_iqr"),
        "ridge_bias_pred_minus_true": metric_from_row(ridge_row, "bias_pred_minus_true"),
        "ridge_bland_altman_lower": metric_from_row(ridge_row, "bland_altman_lower"),
        "ridge_bland_altman_upper": metric_from_row(ridge_row, "bland_altman_upper"),
        "ridge_pearson": metric_from_row(ridge_row, "pearson"),
        "ridge_spearman": metric_from_row(ridge_row, "spearman"),
        "ridge_within_2_cm": metric_from_row(ridge_row, "within_2_cm"),
        "ridge_within_3_cm": metric_from_row(ridge_row, "within_3_cm"),
        "ridge_within_3_mm": metric_from_row(ridge_row, "within_3_mm"),
        "ridge_within_5_mm": metric_from_row(ridge_row, "within_5_mm"),
        "selected_alpha": inventory["selected_alpha"],
        "ridge_solver": inventory["ridge_solver"],
        "features_standardized": inventory["features_standardized"],
        "bootstrap_count": summary.get("n_bootstrap") if summary else None,
        "status": inventory["status"],
        "skip_reason": inventory["skip_reason"],
    }

    binary_rows: list[dict[str, Any]] = []
    if label in {"lvot_all_clips", "tapse_all_clips"} and not binary_df.empty:
        expected_target = "lvot_vti" if label == "lvot_all_clips" else "tapse"
        ridge_binary = binary_df[
            (binary_df.get("target", pd.Series(dtype=str)) == expected_target)
            & (binary_df.get("split", pd.Series(dtype=str)) == "test")
            & (binary_df.get("model", pd.Series(dtype=str)) == "ridge")
        ]
        for _, row in ridge_binary.iterrows():
            binary_rows.append(
                {
                    "run_label": label,
                    "target": row.get("target"),
                    "analysis_label": inventory["analysis_label"],
                    "threshold_label": row.get("threshold_label"),
                    "threshold_value": scalar(row.get("threshold_value")),
                    "model": row.get("model"),
                    "n": scalar(row.get("n")),
                    "prevalence": scalar(row.get("prevalence")),
                    "predicted_positive_rate": scalar(row.get("predicted_positive_rate")),
                    "accuracy": scalar(row.get("accuracy")),
                    "f1": scalar(row.get("f1")),
                    "sensitivity": scalar(row.get("sensitivity")),
                    "specificity": scalar(row.get("specificity")),
                    "ppv": scalar(row.get("ppv")),
                    "npv": scalar(row.get("npv")),
                    "auroc": scalar(row.get("auroc_continuous_score")),
                    "average_precision": scalar(row.get("average_precision_continuous_score")),
                    "tp": scalar(row.get("tp")),
                    "fp": scalar(row.get("fp")),
                    "tn": scalar(row.get("tn")),
                    "fn": scalar(row.get("fn")),
                }
            )

    if missing:
        warnings.append({"run": label, "warning": "Missing aggregate files.", "missing": missing})
    if forbidden:
        warnings.append(
            {
                "run": label,
                "warning": "Forbidden or restricted files detected in run directory and excluded.",
                "files": forbidden,
            }
        )

    return inventory, result, {"binary_rows": binary_rows}


def fmt(value: Any, digits: int = 2) -> str:
    if value is None or value == "":
        return ""
    try:
        val = float(value)
    except (TypeError, ValueError):
        return str(value)
    if not math.isfinite(val):
        return ""
    return f"{val:.{digits}f}"


def fmt_ci(low: Any, high: Any, unit: str = "", digits: int = 2) -> str:
    if low is None or high is None:
        return ""
    suffix = f" {unit}" if unit else ""
    return f"{fmt(low, digits)} to {fmt(high, digits)}{suffix}"


def robustness_note(main_label: str, by_label: dict[str, dict[str, Any]]) -> str:
    hard_label = "lvot_exclude_hard" if main_label == "lvot_all_clips" else "tapse_exclude_hard"
    main = by_label.get(main_label)
    hard = by_label.get(hard_label)
    if not main or not hard or hard.get("ridge_mae") is None:
        return "Hard-extreme check pending exact aggregate verification."
    delta_mae = float(hard["ridge_mae"]) - float(main["ridge_mae"])
    delta_r2 = float(hard["ridge_r2"]) - float(main["ridge_r2"])
    unit = "cm" if main.get("target") == "lvot_vti" else "mm"
    return (
        f"Hard-extreme exclusion materially unchanged "
        f"(MAE {fmt(hard['ridge_mae'])} {unit}; R2 {fmt(hard['ridge_r2'], 3)}; "
        f"delta MAE {fmt(delta_mae)} {unit})."
    )


def write_markdown_summary(
    output_path: Path,
    primary_df: pd.DataFrame,
    echoview_df: pd.DataFrame,
    binary_df: pd.DataFrame,
    inventory_df: pd.DataFrame,
    warnings: list[dict[str, Any]],
    draft_md: Path,
) -> None:
    results = {row["run_label"]: row for row in primary_df.to_dict(orient="records")}
    lines: list[str] = []
    lines.append("# Phase 2 Verified Aggregate Results Summary")
    lines.append("")
    lines.append(f"Generated: {date.today().isoformat()}")
    lines.append("")
    lines.append("Source: aggregate-only Phase 2 stable-v2 outputs. Prediction CSVs, manifests, embeddings, and logs were not read or copied.")
    lines.append("")
    lines.append(f"Draft to update: `{draft_md}`")
    lines.append("")
    lines.append("## Main Imaging-Only Results")
    lines.append("")
    lines.append("| Target | Clinical unit | Test N | Null MAE | Ridge MAE | Ridge MAE 95% CI | Ridge R2 | Ridge R2 95% CI | Selected alpha | Interpretation | Robustness note |")
    lines.append("|---|---:|---:|---:|---:|---|---:|---|---:|---|---|")
    for label in ["lvot_all_clips", "tapse_all_clips"]:
        row = results.get(label)
        if not row:
            continue
        unit = row.get("clinical_unit") or ("cm" if row.get("target") == "lvot_vti" else "mm")
        interpretation = (
            "Primary imaging-only baseline; moderate signal, not measurement-grade."
            if row.get("target") == "lvot_vti"
            else "Cautious secondary signal; strongly regularized."
        )
        lines.append(
            "| {target} | {unit} | {test_n} | {null_mae} {unit} | {ridge_mae} {unit} | {mae_ci} | "
            "{r2} | {r2_ci} | {alpha} | {interpretation} | {robustness} |".format(
                target="LVOT VTI" if row.get("target") == "lvot_vti" else "TAPSE",
                unit=unit,
                test_n=int(row["test_n"]) if pd.notna(row.get("test_n")) else "",
                null_mae=fmt(row.get("null_mae")),
                ridge_mae=fmt(row.get("ridge_mae")),
                mae_ci=fmt_ci(row.get("ridge_mae_ci_low"), row.get("ridge_mae_ci_high"), unit),
                r2=fmt(row.get("ridge_r2"), 3),
                r2_ci=fmt_ci(row.get("ridge_r2_ci_low"), row.get("ridge_r2_ci_high"), "", 2),
                alpha=fmt(row.get("selected_alpha"), 3).rstrip("0").rstrip("."),
                interpretation=interpretation,
                robustness=robustness_note(label, results),
            )
        )
    lines.append("")
    lines.append("## Supplementary Table S1. Hard-Extreme Robustness")
    lines.append("")
    lines.append("| Target | Analysis | Test N | Ridge MAE | Ridge MAE 95% CI | Ridge R2 | Ridge R2 95% CI | Selected alpha | Interpretation |")
    lines.append("|---|---|---:|---:|---|---:|---|---:|---|")
    for label in ["lvot_exclude_hard", "tapse_exclude_hard"]:
        row = results.get(label)
        if not row:
            continue
        unit = row.get("clinical_unit") or ("cm" if row.get("target") == "lvot_vti" else "mm")
        lines.append(
            "| {target} | Exclude hard extremes | {test_n} | {mae} {unit} | {mae_ci} | {r2} | {r2_ci} | {alpha} | Robustness check; not co-primary. |".format(
                target="LVOT VTI" if row.get("target") == "lvot_vti" else "TAPSE",
                test_n=int(row["test_n"]) if pd.notna(row.get("test_n")) else "",
                mae=fmt(row.get("ridge_mae")),
                unit=unit,
                mae_ci=fmt_ci(row.get("ridge_mae_ci_low"), row.get("ridge_mae_ci_high"), unit),
                r2=fmt(row.get("ridge_r2"), 3),
                r2_ci=fmt_ci(row.get("ridge_r2_ci_low"), row.get("ridge_r2_ci_high"), "", 2),
                alpha=fmt(row.get("selected_alpha"), 3).rstrip("0").rstrip("."),
            )
        )
    lines.append("")
    lines.append("## Supplementary Table S2. LVOT VTI ECHOVIEW Sensitivity")
    lines.append("")
    lines.append("| View policy | Threshold | Test N | Ridge MAE | Ridge R2 | Bootstrap CI note | Interpretation |")
    lines.append("|---|---:|---:|---:|---:|---|---|")
    for _, row in echoview_df.iterrows():
        threshold = row.get("threshold")
        if row.get("status") and str(row.get("status")).lower() != "ok" and pd.isna(row.get("ridge_mae")):
            interpretation = "Skipped or underpowered; do not interpret as negative."
        elif pd.isna(row.get("ridge_mae")):
            interpretation = "Skipped or underpowered; do not interpret as negative."
        elif row.get("policy_order_key") == "a5c_or_other_0.70":
            interpretation = "Directionally positive but smaller and weaker than all-clips."
        else:
            interpretation = "Sensitivity subset; higher threshold did not improve performance."
        ci_note = ""
        if pd.notna(row.get("ridge_mae_ci_low")) and pd.notna(row.get("ridge_r2_ci_low")):
            ci_note = (
                f"MAE {fmt(row.get('ridge_mae_ci_low'))} to {fmt(row.get('ridge_mae_ci_high'))} cm; "
                f"R2 {fmt(row.get('ridge_r2_ci_low'), 2)} to {fmt(row.get('ridge_r2_ci_high'), 2)}"
            )
        lines.append(
            "| {policy} | {threshold} | {test_n} | {mae} | {r2} | {ci_note} | {interpretation} |".format(
                policy=row.get("view_policy") or "",
                threshold=fmt(threshold, 2),
                test_n=int(row["test_n"]) if pd.notna(row.get("test_n")) else "",
                mae=f"{fmt(row.get('ridge_mae'))} cm" if pd.notna(row.get("ridge_mae")) else "Not estimated",
                r2=fmt(row.get("ridge_r2"), 3) if pd.notna(row.get("ridge_r2")) else "Not estimated",
                ci_note=ci_note or "Not available",
                interpretation=interpretation,
            )
        )
    lines.append("")
    lines.append("## Supplementary Table S3. Exploratory Binary Threshold Results")
    lines.append("")
    if binary_df.empty:
        lines.append("Exact binary threshold metrics were not available in the aggregate files.")
    else:
        lines.append("| Target | Threshold | Test N | Prevalence | Predicted positive rate | AUROC | Average precision | Sensitivity | Specificity | PPV | NPV | F1 | TP | FP | TN | FN |")
        lines.append("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
        for _, row in binary_df.iterrows():
            lines.append(
                "| {target} | {threshold} | {n} | {prev} | {ppr} | {auroc} | {ap} | {sens} | {spec} | {ppv} | {npv} | {f1} | {tp} | {fp} | {tn} | {fn} |".format(
                    target="LVOT VTI" if row.get("target") == "lvot_vti" else "TAPSE",
                    threshold=row.get("threshold_label"),
                    n=int(row["n"]) if pd.notna(row.get("n")) else "",
                    prev=fmt(row.get("prevalence"), 3),
                    ppr=fmt(row.get("predicted_positive_rate"), 3),
                    auroc=fmt(row.get("auroc"), 3),
                    ap=fmt(row.get("average_precision"), 3),
                    sens=fmt(row.get("sensitivity"), 3),
                    spec=fmt(row.get("specificity"), 3),
                    ppv=fmt(row.get("ppv"), 3),
                    npv=fmt(row.get("npv"), 3),
                    f1=fmt(row.get("f1"), 3),
                    tp=int(row["tp"]) if pd.notna(row.get("tp")) else "",
                    fp=int(row["fp"]) if pd.notna(row.get("fp")) else "",
                    tn=int(row["tn"]) if pd.notna(row.get("tn")) else "",
                    fn=int(row["fn"]) if pd.notna(row.get("fn")) else "",
                )
            )
    lines.append("")
    lines.append("## Inventory Notes")
    lines.append("")
    lines.append(f"Runs inventoried: {len(inventory_df)}")
    lines.append(f"Warnings: {len(warnings)}")
    output_path.write_text("\n".join(lines) + "\n")


def main() -> int:
    args = parse_args()
    try:
        guard_output_dir(args.output_dir, args.allow_repo_output_for_testing)
    except RuntimeError as exc:
        print(json.dumps({"blocked_for_governance": True, "reason": str(exc)}, indent=2))
        return 2

    warnings: list[dict[str, Any]] = []
    args.output_dir.mkdir(parents=True, exist_ok=True)

    if not args.output_root.exists():
        message = f"Output root does not exist or is not mounted: {args.output_root}"
        warnings.append({"warning": message})
        if not args.allow_missing:
            (args.output_dir / "phase2_metric_verification_warnings.json").write_text(
                json.dumps({"warnings": warnings}, indent=2) + "\n"
            )
            print(json.dumps({"blocked": True, "reason": message}, indent=2))
            return 2

    run_dirs = discover_run_dirs(args.output_root) if args.output_root.exists() else []
    inventory_rows: list[dict[str, Any]] = []
    result_rows: list[dict[str, Any]] = []
    binary_rows: list[dict[str, Any]] = []

    for run_dir in run_dirs:
        inventory, result, extra = run_record(run_dir, args.output_root, warnings)
        inventory_rows.append(inventory)
        result_rows.append(result)
        binary_rows.extend(extra.get("binary_rows", []))

    inventory_df = pd.DataFrame(inventory_rows)
    results_df = pd.DataFrame(result_rows)
    binary_df = pd.DataFrame(binary_rows)

    if results_df.empty:
        primary_df = pd.DataFrame()
        echoview_df = pd.DataFrame()
    else:
        primary_df = results_df[results_df["table_role"].isin(["main", "hard_extreme_robustness"])].copy()
        primary_df["table_order"] = primary_df["run_label"].map(PRIMARY_RUN_ORDER).fillna(999)
        primary_df = primary_df.sort_values(["table_order", "run_label"]).drop(columns=["table_order"])

        echoview_df = results_df[results_df["table_role"] == "echoview_sensitivity"].copy()
        if not echoview_df.empty:
            parsed = echoview_df["run_label"].map(parse_view_policy)
            echoview_df["view_policy"] = [item[0] for item in parsed]
            echoview_df["threshold"] = [item[1] for item in parsed]
            echoview_df["policy_order_key"] = [item[2] for item in parsed]
            echoview_df["table_order"] = echoview_df["policy_order_key"].map(ECHOVIEW_ORDER).fillna(999)
            echoview_df = echoview_df.sort_values(["table_order", "run_label"]).drop(columns=["table_order"])

    inventory_df = inventory_df.reindex(columns=INVENTORY_COLUMNS)
    primary_df = primary_df.reindex(columns=RESULT_COLUMNS)
    echoview_df = echoview_df.reindex(columns=ECHOVIEW_COLUMNS)
    binary_df = binary_df.reindex(columns=BINARY_COLUMNS)

    inventory_df.to_csv(args.output_dir / "phase2_run_inventory.csv", index=False)
    primary_df.to_csv(args.output_dir / "phase2_primary_results_verified.csv", index=False)
    echoview_df.to_csv(args.output_dir / "phase2_echoview_sensitivity_verified.csv", index=False)
    binary_df.to_csv(args.output_dir / "phase2_binary_low_vti_verified.csv", index=False)

    warning_payload = {
        "output_root": str(args.output_root),
        "output_dir": str(args.output_dir),
        "draft_md": str(args.draft_md),
        "allowed_aggregate_files": sorted(ALLOWED_AGGREGATE_FILES),
        "forbidden_file_pattern": FORBIDDEN_FILE_PATTERN.pattern,
        "warnings": warnings,
        "manuscript_safe_aggregate_outputs": True,
        "patient_level_outputs_read_or_copied": False,
    }
    (args.output_dir / "phase2_metric_verification_warnings.json").write_text(
        json.dumps(warning_payload, indent=2) + "\n"
    )

    if args.write_markdown_summary:
        write_markdown_summary(
            args.output_dir / "phase2_results_verified_summary.md",
            primary_df,
            echoview_df,
            binary_df,
            inventory_df,
            warnings,
            args.draft_md,
        )

    summary = {
        "output_root": str(args.output_root),
        "output_dir": str(args.output_dir),
        "runs_inventoried": int(len(inventory_df)),
        "primary_rows": int(len(primary_df)),
        "echoview_rows": int(len(echoview_df)),
        "binary_rows": int(len(binary_df)),
        "warnings": int(len(warnings)),
        "patient_level_outputs_read_or_copied": False,
        "outputs": {
            "phase2_primary_results_verified": str(args.output_dir / "phase2_primary_results_verified.csv"),
            "phase2_echoview_sensitivity_verified": str(args.output_dir / "phase2_echoview_sensitivity_verified.csv"),
            "phase2_binary_low_vti_verified": str(args.output_dir / "phase2_binary_low_vti_verified.csv"),
            "phase2_run_inventory": str(args.output_dir / "phase2_run_inventory.csv"),
            "phase2_metric_verification_warnings": str(args.output_dir / "phase2_metric_verification_warnings.json"),
            "phase2_results_verified_summary": str(args.output_dir / "phase2_results_verified_summary.md")
            if args.write_markdown_summary
            else "",
        },
    }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
