#!/usr/bin/env python3
"""Generate Phase 2 imaging-only result figures from restricted SCC outputs.

Prediction CSVs are patient-level restricted artifacts. This script reads them
only when explicitly allowed and only when both the input and figure output
directories are under the restricted SCC Phase 2 output root. It writes figures,
aggregate figure metadata, and caption drafts only; it never writes row-level
data.
"""
from __future__ import annotations

import argparse
import json
import math
import re
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from pandas.errors import EmptyDataError


RUNS = {
    "lvot_all_clips": Path("lvot_vti/all_clips"),
    "lvot_exclude_hard": Path("lvot_vti/all_clips_exclude_hard_extremes"),
    "tapse_all_clips": Path("tapse/all_clips"),
    "tapse_exclude_hard": Path("tapse/all_clips_exclude_hard_extremes"),
    "a5c_or_other_0.70": Path("lvot_vti/view_filtered/lvot_vti_a5c_or_other_thr070_mean"),
    "other_0.70": Path("lvot_vti/view_filtered/lvot_vti_other_only_thr070_mean"),
    "a5c_0.70": Path("lvot_vti/view_filtered/lvot_vti_a5c_only_thr070_mean"),
    "a5c_or_other_0.80": Path("lvot_vti/view_filtered/lvot_vti_a5c_or_other_thr080_mean"),
    "a5c_or_other_0.90": Path("lvot_vti/view_filtered/lvot_vti_a5c_or_other_thr090_mean"),
    "a5c_or_other_0.95": Path("lvot_vti/view_filtered/lvot_vti_a5c_or_other_thr095_mean"),
}

PREDICTION_REQUIRED_FIGURES = {
    "fig1_lvot_observed_vs_predicted",
    "fig1_lvot_bland_altman",
    "fig1_lvot_low_vti_roc",
    "figS1_tapse_observed_vs_predicted",
    "figS1_tapse_bland_altman",
}

AGGREGATE_FIGURES = {
    "fig1_lvot_mae_comparison",
    "figS1_tapse_mae_comparison",
    "figS2_lvot_echoview_sensitivity_mae",
    "figS2_lvot_echoview_sensitivity_r2",
}

ECHOVIEW_LABELS = {
    "lvot_all_clips": "All clips",
    "a5c_or_other_0.70": "A5C-or-other\n0.70",
    "other_0.70": "Other-only\n0.70",
    "a5c_0.70": "A5C-only\n0.70",
    "a5c_or_other_0.80": "A5C-or-other\n0.80",
    "a5c_or_other_0.90": "A5C-or-other\n0.90",
    "a5c_or_other_0.95": "A5C-or-other\n0.95",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--figure-dir", type=Path, required=True)
    parser.add_argument("--review-packet-dir", type=Path, default=None)
    parser.add_argument(
        "--include-prediction-plots",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Default: enabled only when --output-root appears to be a restricted SCC output path.",
    )
    parser.add_argument(
        "--allow-restricted-predictions",
        action="store_true",
        help="Required before reading patient-level prediction CSVs in restricted SCC storage.",
    )
    parser.add_argument("--format", default="png,pdf", help="Comma-separated figure formats.")
    parser.add_argument("--dpi", type=int, default=300)
    parser.add_argument("--random-seed", type=int, default=1337)
    parser.add_argument(
        "--allow-repo-output-for-testing",
        action="store_true",
        help="Permit figure output inside the git worktree for synthetic tests only.",
    )
    return parser.parse_args()


def path_is_relative_to(path: Path, parent: Path) -> bool:
    resolved = path.resolve()
    resolved_parent = parent.resolve()
    return resolved == resolved_parent or resolved_parent in resolved.parents


def inside_current_worktree(path: Path) -> bool:
    cwd = Path.cwd().resolve()
    resolved = path.resolve()
    return resolved == cwd or cwd in resolved.parents


def is_restricted_scc_path(path: Path) -> bool:
    text = str(path.resolve())
    return text.startswith("/restricted/") and "/outputs/" in text


def prediction_plots_enabled(args: argparse.Namespace) -> bool:
    if args.include_prediction_plots is not None:
        return bool(args.include_prediction_plots)
    return is_restricted_scc_path(args.output_root)


def guard_figure_output(args: argparse.Namespace) -> list[str]:
    warnings: list[str] = []
    if inside_current_worktree(args.figure_dir) and not args.allow_repo_output_for_testing:
        raise RuntimeError(
            f"Refusing to write generated figures inside the repo: {args.figure_dir}. "
            "Use a restricted SCC review packet directory."
        )
    if prediction_plots_enabled(args):
        if not args.allow_restricted_predictions:
            raise RuntimeError(
                "Prediction-dependent plots require --allow-restricted-predictions. "
                "Run only on SCC/restricted storage."
            )
        if not is_restricted_scc_path(args.output_root):
            raise RuntimeError(f"Output root does not appear restricted: {args.output_root}")
        if not path_is_relative_to(args.figure_dir, args.output_root):
            raise RuntimeError(
                "When reading restricted predictions, --figure-dir must be under --output-root."
            )
    else:
        warnings.append("Prediction-dependent plots disabled; only aggregate figures will be generated if possible.")
    return warnings


def parse_formats(text: str) -> list[str]:
    formats = [piece.strip().lower().lstrip(".") for piece in text.split(",") if piece.strip()]
    if not formats:
        raise ValueError("At least one output format is required.")
    unsupported = sorted(set(formats) - {"png", "pdf", "svg"})
    if unsupported:
        raise ValueError(f"Unsupported figure formats: {unsupported}")
    return formats


def read_csv(path: Path, warnings: list[str]) -> pd.DataFrame:
    if not path.exists():
        warnings.append(f"Missing aggregate CSV: {path.name} in {path.parent}")
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except EmptyDataError:
        warnings.append(f"Empty aggregate CSV: {path.name} in {path.parent}")
        return pd.DataFrame()


def read_json(path: Path, warnings: list[str]) -> dict[str, Any]:
    if not path.exists():
        warnings.append(f"Missing JSON: {path.name} in {path.parent}")
        return {}
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        warnings.append(f"Could not parse JSON {path}: {exc}")
        return {}


def load_run(root: Path, run_key: str, warnings: list[str]) -> dict[str, Any]:
    run_dir = root / RUNS[run_key]
    return {
        "run_key": run_key,
        "relative_dir": str(RUNS[run_key]),
        "run_dir": run_dir,
        "run_dir_exists": run_dir.exists(),
        "summary": read_json(run_dir / "imaging_baseline_summary.json", warnings),
        "metrics": read_csv(run_dir / "imaging_baseline_metrics.csv", warnings),
        "bootstrap": read_csv(run_dir / "imaging_baseline_bootstrap_ci.csv", warnings),
        "binary": read_csv(run_dir / "imaging_baseline_binary_metrics.csv", warnings),
        "predictions_path": run_dir / "imaging_baseline_predictions.csv",
    }


def row_for(df: pd.DataFrame, **equals: str) -> pd.Series | None:
    if df.empty:
        return None
    mask = pd.Series(True, index=df.index)
    for col, value in equals.items():
        if col not in df.columns:
            return None
        mask &= df[col].astype(str).eq(str(value))
    filtered = df[mask]
    if filtered.empty:
        return None
    return filtered.iloc[0]


def metric(row: pd.Series | None, col: str) -> float | None:
    if row is None or col not in row.index:
        return None
    value = pd.to_numeric(pd.Series([row[col]]), errors="coerce").iloc[0]
    if pd.isna(value):
        return None
    return float(value)


def ci_for(run: dict[str, Any], model: str, metric_name: str) -> tuple[float | None, float | None]:
    df = run["bootstrap"]
    row = row_for(df, split="test", model=model, metric=metric_name)
    return metric(row, "ci_lower_2_5"), metric(row, "ci_upper_97_5")


def target_summary(run: dict[str, Any]) -> dict[str, Any]:
    summaries = run["summary"].get("target_summaries") or []
    return summaries[0] if summaries else {}


def test_predictions(path: Path, target: str) -> pd.DataFrame:
    usecols = ["target", "split", "target_value", "pred_null_median", "pred_ridge"]
    df = pd.read_csv(path, usecols=lambda c: c in usecols)
    if "target" in df.columns:
        df = df[df["target"].astype(str).eq(target)]
    df = df[df["split"].astype(str).str.lower().eq("test")].copy()
    required = {"target_value", "pred_ridge"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Prediction file {path} missing required columns: {sorted(missing)}")
    df["target_value"] = pd.to_numeric(df["target_value"], errors="coerce")
    df["pred_ridge"] = pd.to_numeric(df["pred_ridge"], errors="coerce")
    if "pred_null_median" in df.columns:
        df["pred_null_median"] = pd.to_numeric(df["pred_null_median"], errors="coerce")
    return df[df["target_value"].notna() & df["pred_ridge"].notna()].copy()


def setup_matplotlib() -> Any:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update(
        {
            "figure.dpi": 120,
            "savefig.bbox": "tight",
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.grid": True,
            "grid.alpha": 0.22,
            "font.size": 10,
            "axes.titlesize": 12,
            "axes.labelsize": 10,
            "legend.frameon": False,
        }
    )
    return plt


def save_figure(fig: Any, figure_dir: Path, stem: str, formats: list[str], dpi: int) -> list[str]:
    paths: list[str] = []
    metadata = {"Creator": "Echo_Cardio_VLM Phase 2", "Title": stem}
    for fmt in formats:
        out = figure_dir / f"{stem}.{fmt}"
        if fmt == "png":
            fig.savefig(out, dpi=dpi, metadata={"Software": "Echo_Cardio_VLM Phase 2", "Title": stem})
        else:
            fig.savefig(out, dpi=dpi, metadata=metadata)
        paths.append(str(out))
    return paths


def observed_vs_predicted(
    plt: Any,
    preds: pd.DataFrame,
    target_label: str,
    unit: str,
    stem: str,
    figure_dir: Path,
    formats: list[str],
    dpi: int,
) -> list[str]:
    x = preds["target_value"].to_numpy(float)
    y = preds["pred_ridge"].to_numpy(float)
    low = float(np.nanmin([x.min(), y.min()]))
    high = float(np.nanmax([x.max(), y.max()]))
    pad = 0.05 * (high - low if high > low else 1.0)
    fig, ax = plt.subplots(figsize=(5.2, 4.6))
    ax.scatter(x, y, s=18, alpha=0.55, edgecolors="none", color="#2B6CB0")
    ax.plot([low - pad, high + pad], [low - pad, high + pad], color="#333333", linewidth=1.2, linestyle="--", label="Identity")
    if len(preds) >= 3 and np.nanstd(x) > 0:
        slope, intercept = np.polyfit(x, y, deg=1)
        xx = np.linspace(low - pad, high + pad, 100)
        ax.plot(xx, slope * xx + intercept, color="#C05621", linewidth=1.4, label="Fitted line")
    ax.set_xlim(low - pad, high + pad)
    ax.set_ylim(low - pad, high + pad)
    ax.set_xlabel(f"Observed {target_label} ({unit})")
    ax.set_ylabel(f"Predicted {target_label} ({unit})")
    ax.set_title(f"{target_label}: observed vs predicted")
    ax.legend(loc="best")
    paths = save_figure(fig, figure_dir, stem, formats, dpi)
    plt.close(fig)
    return paths


def bland_altman(
    plt: Any,
    preds: pd.DataFrame,
    target_label: str,
    unit: str,
    stem: str,
    figure_dir: Path,
    formats: list[str],
    dpi: int,
) -> list[str]:
    observed = preds["target_value"].to_numpy(float)
    predicted = preds["pred_ridge"].to_numpy(float)
    mean_values = (observed + predicted) / 2.0
    diff = predicted - observed
    bias = float(np.mean(diff))
    sd = float(np.std(diff, ddof=1)) if len(diff) > 1 else float("nan")
    lower = bias - 1.96 * sd
    upper = bias + 1.96 * sd
    fig, ax = plt.subplots(figsize=(5.2, 4.6))
    ax.scatter(mean_values, diff, s=18, alpha=0.55, edgecolors="none", color="#2F855A")
    ax.axhline(bias, color="#1A202C", linewidth=1.2, label=f"Bias {bias:.2f}")
    ax.axhline(lower, color="#C05621", linewidth=1.1, linestyle="--", label=f"Lower LOA {lower:.2f}")
    ax.axhline(upper, color="#C05621", linewidth=1.1, linestyle="--", label=f"Upper LOA {upper:.2f}")
    ax.set_xlabel(f"Mean observed/predicted {target_label} ({unit})")
    ax.set_ylabel(f"Prediction error ({unit})")
    ax.set_title(f"{target_label}: Bland-Altman")
    ax.legend(loc="best")
    paths = save_figure(fig, figure_dir, stem, formats, dpi)
    plt.close(fig)
    return paths


def mae_comparison(
    plt: Any,
    run: dict[str, Any],
    target_label: str,
    unit: str,
    stem: str,
    figure_dir: Path,
    formats: list[str],
    dpi: int,
) -> list[str]:
    metrics = run["metrics"]
    null_row = row_for(metrics, split="test", model="null_median")
    ridge_row = row_for(metrics, split="test", model="ridge")
    labels = ["Null median", "Ridge"]
    values = [metric(null_row, "mae"), metric(ridge_row, "mae")]
    error_lowers: list[float] = []
    error_uppers: list[float] = []
    for model, value in zip(["null_median", "ridge"], values):
        low, high = ci_for(run, model, "mae")
        if value is not None and low is not None and high is not None:
            error_lowers.append(value - low)
            error_uppers.append(high - value)
        else:
            error_lowers.append(0.0)
            error_uppers.append(0.0)
    fig, ax = plt.subplots(figsize=(4.7, 4.4))
    ax.bar(labels, values, color=["#A0AEC0", "#2B6CB0"], width=0.58)
    ax.errorbar(labels, values, yerr=[error_lowers, error_uppers], fmt="none", ecolor="#1A202C", capsize=5, linewidth=1.1)
    ax.set_ylabel(f"Test MAE ({unit})")
    ax.set_title(f"{target_label}: null vs Ridge MAE")
    for idx, value in enumerate(values):
        if value is not None:
            ax.text(idx, value, f"{value:.2f}", ha="center", va="bottom", fontsize=9)
    paths = save_figure(fig, figure_dir, stem, formats, dpi)
    plt.close(fig)
    return paths


def roc_curves(
    plt: Any,
    preds: pd.DataFrame,
    stem: str,
    figure_dir: Path,
    formats: list[str],
    dpi: int,
) -> list[str]:
    from sklearn.metrics import auc, roc_curve

    thresholds = [("LVOT VTI <18 cm", 18.0), ("LVOT VTI <20 cm", 20.0)]
    y_score = -preds["pred_ridge"].to_numpy(float)
    fig, ax = plt.subplots(figsize=(5.0, 4.6))
    for label, threshold in thresholds:
        y_true = (preds["target_value"].to_numpy(float) < threshold).astype(int)
        if len(np.unique(y_true)) < 2:
            continue
        fpr, tpr, _ = roc_curve(y_true, y_score)
        ax.plot(fpr, tpr, linewidth=1.6, label=f"{label} (AUROC {auc(fpr, tpr):.3f})")
    ax.plot([0, 1], [0, 1], linestyle="--", color="#4A5568", linewidth=1.0)
    ax.set_xlabel("False positive rate")
    ax.set_ylabel("True positive rate")
    ax.set_title("Exploratory low LVOT VTI ROC")
    ax.legend(loc="lower right")
    paths = save_figure(fig, figure_dir, stem, formats, dpi)
    plt.close(fig)
    return paths


def echoview_summary_rows(runs: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key in [
        "lvot_all_clips",
        "a5c_or_other_0.70",
        "other_0.70",
        "a5c_0.70",
        "a5c_or_other_0.80",
        "a5c_or_other_0.90",
        "a5c_or_other_0.95",
    ]:
        run = runs.get(key)
        if not run:
            continue
        if not run.get("run_dir_exists") and run["metrics"].empty and not run["summary"]:
            continue
        metrics = run["metrics"]
        ridge_row = row_for(metrics, split="test", model="ridge")
        summary = target_summary(run)
        low_mae, high_mae = ci_for(run, "ridge", "mae")
        low_r2, high_r2 = ci_for(run, "ridge", "r2")
        rows.append(
            {
                "key": key,
                "label": ECHOVIEW_LABELS[key],
                "test_n": metric(ridge_row, "n_studies") or summary.get("split_counts", {}).get("test"),
                "mae": metric(ridge_row, "mae"),
                "mae_low": low_mae,
                "mae_high": high_mae,
                "r2": metric(ridge_row, "r2"),
                "r2_low": low_r2,
                "r2_high": high_r2,
                "status": summary.get("status"),
                "skip_reason": summary.get("skip_reason"),
            }
        )
    return rows


def echoview_plot(
    plt: Any,
    rows: list[dict[str, Any]],
    metric_name: str,
    ylabel: str,
    stem: str,
    figure_dir: Path,
    formats: list[str],
    dpi: int,
) -> list[str]:
    labels = [row["label"] for row in rows]
    x = np.arange(len(labels))
    values = [row.get(metric_name) for row in rows]
    fig, ax = plt.subplots(figsize=(8.6, 4.8))
    for idx, row in enumerate(rows):
        value = row.get(metric_name)
        if value is None or pd.isna(value):
            ax.scatter(idx, 0, marker="x", s=70, color="#C53030")
            ax.text(idx, 0.03, "Skipped", ha="center", va="bottom", fontsize=8, rotation=90)
            continue
        color = "#2B6CB0" if row["key"] == "lvot_all_clips" else "#718096"
        ax.scatter(idx, value, s=62, color=color, zorder=3)
        low = row.get(f"{metric_name}_low")
        high = row.get(f"{metric_name}_high")
        if low is not None and high is not None:
            ax.errorbar(idx, value, yerr=[[value - low], [high - value]], fmt="none", ecolor="#2D3748", capsize=4)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=35, ha="right")
    ax.set_ylabel(ylabel)
    ax.set_title("LVOT VTI ECHOVIEW subset sensitivity")
    ax.axvline(0.5, color="#A0AEC0", linewidth=0.8, linestyle=":")
    if metric_name == "r2":
        ax.axhline(0, color="#4A5568", linewidth=0.8, linestyle="--")
    paths = save_figure(fig, figure_dir, stem, formats, dpi)
    plt.close(fig)
    return paths


def record_inventory(
    inventory: list[dict[str, Any]],
    stem: str,
    paths: list[str],
    uses_predictions: bool,
    source_runs: list[str],
    notes: str,
) -> None:
    inventory.append(
        {
            "figure": stem,
            "files": ";".join(paths),
            "uses_restricted_predictions": bool(uses_predictions),
            "source_runs": ";".join(source_runs),
            "manuscript_safe_after_review": True,
            "notes": notes,
        }
    )


def caption_markdown(inventory: list[dict[str, Any]], warnings: list[str]) -> str:
    lines = [
        "# Phase 2 Figure Caption Drafts",
        "",
        f"Generated: {date.today().isoformat()}",
        "",
        "Figures were generated from Phase 2 stable-v2 outputs. Prediction-dependent figures were generated only from restricted SCC prediction CSVs when explicitly allowed; no row-level data were exported.",
        "",
        "## Figure 1. LVOT VTI Primary Imaging-Only Model Performance",
        "",
        "Frozen EchoPrime study embeddings were used to predict structured LVOT VTI on the held-out subject-level test split. Ridge regression used train-fit feature standardization, the numerically stable `svd` solver, and validation-only alpha selection. Observed-versus-predicted and Bland-Altman panels summarize continuous LVOT VTI predictions. Null-versus-Ridge MAE is shown with subject-level bootstrap 95% confidence intervals. Exploratory ROC curves for LVOT VTI <18 cm and <20 cm are thresholded summaries of the continuous predictions, not separately trained classifiers.",
        "",
        "The Bland-Altman limits of agreement remain wide, so these results support an imaging-only estimation and risk-stratification signal rather than replacement of clinical Doppler LVOT VTI measurement.",
        "",
        "## Supplementary Figure S1. TAPSE Secondary Imaging-Only Model Performance",
        "",
        "Frozen EchoPrime study embeddings were used to predict structured TAPSE on the held-out subject-level test split. Ridge regression used the same stable-v2 configuration as the LVOT VTI analysis. TAPSE is reported as a cautious secondary endpoint because the test set is smaller and the selected alpha indicates strong regularization.",
        "",
        "## Supplementary Figure S2. LVOT VTI ECHOVIEW Sensitivity Analyses",
        "",
        "ECHOVIEW-filtered LVOT VTI analyses are limited subset sensitivities rather than competing primary analyses. ECHOVIEW is a derived view-classification subset and not the full MIMIC-IV-ECHO DICOM denominator. Stricter A5C-or-other thresholds did not improve performance, and the strict A5C-only 0.70 analysis was skipped as underpowered. These panels should be interpreted as sensitivity checks around the primary all-clips result.",
        "",
        "## Figure Inventory",
        "",
        "| Figure | Uses restricted predictions | Source runs | Notes |",
        "|---|---:|---|---|",
    ]
    for row in inventory:
        lines.append(
            f"| {row['figure']} | {row['uses_restricted_predictions']} | {row['source_runs']} | {row['notes']} |"
        )
    if warnings:
        lines.extend(["", "## Warnings", ""])
        for warning in warnings:
            lines.append(f"- {warning}")
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    formats = parse_formats(args.format)
    warnings = guard_figure_output(args)
    args.figure_dir.mkdir(parents=True, exist_ok=True)

    runs = {key: load_run(args.output_root, key, warnings) for key in RUNS}
    include_prediction_plots = prediction_plots_enabled(args)
    plt = setup_matplotlib()

    inventory: list[dict[str, Any]] = []

    if include_prediction_plots:
        for key, target, label, unit, prefix in [
            ("lvot_all_clips", "lvot_vti", "LVOT VTI", "cm", "fig1_lvot"),
            ("tapse_all_clips", "tapse", "TAPSE", "mm", "figS1_tapse"),
        ]:
            pred_path = runs[key]["predictions_path"]
            if not pred_path.exists():
                warnings.append(f"Missing prediction file for {key}; prediction-dependent figures skipped.")
                continue
            preds = test_predictions(pred_path, target)
            if preds.empty:
                warnings.append(f"No test predictions available for {key}; prediction-dependent figures skipped.")
                continue
            paths = observed_vs_predicted(
                plt,
                preds,
                label,
                unit,
                f"{prefix}_observed_vs_predicted",
                args.figure_dir,
                formats,
                args.dpi,
            )
            record_inventory(
                inventory,
                f"{prefix}_observed_vs_predicted",
                paths,
                True,
                [key],
                "Observed vs predicted on held-out test split; no point labels.",
            )
            paths = bland_altman(
                plt,
                preds,
                label,
                unit,
                f"{prefix}_bland_altman",
                args.figure_dir,
                formats,
                args.dpi,
            )
            record_inventory(
                inventory,
                f"{prefix}_bland_altman",
                paths,
                True,
                [key],
                "Bland-Altman plot on held-out test split; no point labels.",
            )
            if target == "lvot_vti":
                paths = roc_curves(plt, preds, "fig1_lvot_low_vti_roc", args.figure_dir, formats, args.dpi)
                record_inventory(
                    inventory,
                    "fig1_lvot_low_vti_roc",
                    paths,
                    True,
                    [key],
                    "Exploratory ROC curves from continuous predictions.",
                )

    for key, label, unit, stem in [
        ("lvot_all_clips", "LVOT VTI", "cm", "fig1_lvot_mae_comparison"),
        ("tapse_all_clips", "TAPSE", "mm", "figS1_tapse_mae_comparison"),
    ]:
        run = runs[key]
        if run["metrics"].empty:
            warnings.append(f"Missing metrics for {key}; MAE comparison skipped.")
            continue
        paths = mae_comparison(plt, run, label, unit, stem, args.figure_dir, formats, args.dpi)
        record_inventory(
            inventory,
            stem,
            paths,
            False,
            [key],
            "Aggregate null-vs-Ridge test MAE with bootstrap confidence intervals.",
        )

    sensitivity_rows = echoview_summary_rows(runs)
    if sensitivity_rows:
        paths = echoview_plot(
            plt,
            sensitivity_rows,
            "mae",
            "Ridge test MAE (cm)",
            "figS2_lvot_echoview_sensitivity_mae",
            args.figure_dir,
            formats,
            args.dpi,
        )
        record_inventory(
            inventory,
            "figS2_lvot_echoview_sensitivity_mae",
            paths,
            False,
            [row["key"] for row in sensitivity_rows],
            "Aggregate ECHOVIEW subset sensitivity MAE; A5C-only can be skipped.",
        )
        paths = echoview_plot(
            plt,
            sensitivity_rows,
            "r2",
            "Ridge test R2",
            "figS2_lvot_echoview_sensitivity_r2",
            args.figure_dir,
            formats,
            args.dpi,
        )
        record_inventory(
            inventory,
            "figS2_lvot_echoview_sensitivity_r2",
            paths,
            False,
            [row["key"] for row in sensitivity_rows],
            "Aggregate ECHOVIEW subset sensitivity R2; A5C-only can be skipped.",
        )

    inventory_df = pd.DataFrame(inventory)
    inventory_path = args.figure_dir / "phase2_figure_inventory.csv"
    inventory_df.to_csv(inventory_path, index=False)

    metadata = {
        "generated_date": date.today().isoformat(),
        "figure_formats": formats,
        "dpi": int(args.dpi),
        "include_prediction_plots": bool(include_prediction_plots),
        "restricted_predictions_allowed": bool(args.allow_restricted_predictions),
        "patient_level_outputs_written": False,
        "row_level_outputs_written": False,
        "prediction_csvs_copied": False,
        "figures": inventory,
        "warnings": warnings,
    }
    metadata_path = args.figure_dir / "phase2_figure_metadata.json"
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n")

    captions_path = args.figure_dir / "phase2_figure_caption_drafts.md"
    captions_path.write_text(caption_markdown(inventory, warnings))

    payload = {
        "figure_dir": str(args.figure_dir),
        "n_figures": len(inventory),
        "prediction_plots_generated": any(row["uses_restricted_predictions"] for row in inventory),
        "patient_level_outputs_written": False,
        "row_level_outputs_written": False,
        "outputs": {
            "figure_inventory": str(inventory_path),
            "figure_metadata": str(metadata_path),
            "caption_drafts": str(captions_path),
        },
        "warnings": warnings,
    }
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
