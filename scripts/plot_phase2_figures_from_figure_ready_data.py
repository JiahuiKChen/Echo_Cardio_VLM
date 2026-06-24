#!/usr/bin/env python3
"""Plot Phase 2 figures from minimal restricted derived figure-ready CSVs.

The input CSVs must already be deidentified figure-ready exports. This script
does not require raw prediction files, identifiers, manifests, DICOM paths, or
SCC metadata.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


LVOT_FILE = "lvot_vti_test_predictions_figure_ready.csv"
TAPSE_FILE = "tapse_test_predictions_figure_ready.csv"
MANIFEST_FILE = "phase2_figure_ready_export_manifest.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--figure-ready-dir", type=Path, required=True)
    parser.add_argument("--figure-dir", type=Path, required=True)
    parser.add_argument("--format", default="png,pdf")
    parser.add_argument("--dpi", type=int, default=300)
    parser.add_argument(
        "--metrics-json",
        type=Path,
        default=None,
        help="Optional manual aggregate metric JSON. Defaults to aggregate_metrics in export manifest.",
    )
    parser.add_argument(
        "--allow-repo-output-for-testing",
        action="store_true",
        help="Permit figure output inside the git repo for synthetic tests only.",
    )
    return parser.parse_args()


def inside_current_worktree(path: Path) -> bool:
    cwd = Path.cwd().resolve()
    resolved = path.resolve()
    return resolved == cwd or cwd in resolved.parents


def guard_output_dir(path: Path, allow_repo_output_for_testing: bool) -> None:
    if inside_current_worktree(path) and not allow_repo_output_for_testing:
        raise RuntimeError(
            f"Refusing to write local figure outputs inside the git repository: {path}. "
            "Use a secure local directory outside the repo."
        )


def parse_formats(text: str) -> list[str]:
    formats = [part.strip().lower().lstrip(".") for part in text.split(",") if part.strip()]
    if not formats:
        raise ValueError("At least one figure format is required.")
    unsupported = sorted(set(formats) - {"png", "pdf", "svg"})
    if unsupported:
        raise ValueError(f"Unsupported formats: {unsupported}")
    return formats


def load_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def load_metrics(args: argparse.Namespace, manifest: dict[str, Any]) -> dict[str, Any]:
    if args.metrics_json is not None:
        return json.loads(args.metrics_json.read_text())
    return manifest.get("aggregate_metrics", {})


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
    for fmt in formats:
        path = figure_dir / f"{stem}.{fmt}"
        if fmt == "png":
            fig.savefig(path, dpi=dpi, metadata={"Software": "Echo_Cardio_VLM Phase 2", "Title": stem})
        else:
            fig.savefig(path, dpi=dpi, metadata={"Creator": "Echo_Cardio_VLM Phase 2", "Title": stem})
        paths.append(path.name)
    return paths


def observed_vs_predicted(
    plt: Any,
    observed: np.ndarray,
    predicted: np.ndarray,
    target_label: str,
    unit: str,
    stem: str,
    figure_dir: Path,
    formats: list[str],
    dpi: int,
) -> list[str]:
    low = float(np.nanmin([observed.min(), predicted.min()]))
    high = float(np.nanmax([observed.max(), predicted.max()]))
    pad = 0.05 * (high - low if high > low else 1.0)
    fig, ax = plt.subplots(figsize=(5.2, 4.6))
    ax.scatter(observed, predicted, s=18, alpha=0.55, edgecolors="none", color="#2B6CB0")
    ax.plot([low - pad, high + pad], [low - pad, high + pad], color="#333333", linewidth=1.2, linestyle="--", label="Identity")
    if len(observed) >= 3 and np.nanstd(observed) > 0:
        slope, intercept = np.polyfit(observed, predicted, deg=1)
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
    observed: np.ndarray,
    predicted: np.ndarray,
    target_label: str,
    unit: str,
    stem: str,
    figure_dir: Path,
    formats: list[str],
    dpi: int,
) -> list[str]:
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
    ax.set_ylabel(f"Prediction minus observed ({unit})")
    ax.set_title(f"{target_label}: Bland-Altman")
    ax.legend(loc="best")
    paths = save_figure(fig, figure_dir, stem, formats, dpi)
    plt.close(fig)
    return paths


def roc_points(y_true: np.ndarray, score: np.ndarray) -> tuple[np.ndarray, np.ndarray, float]:
    order = np.argsort(-score)
    y = y_true[order].astype(int)
    positives = int(y.sum())
    negatives = int(len(y) - positives)
    if positives == 0 or negatives == 0:
        return np.array([0, 1]), np.array([0, 1]), float("nan")
    tp = np.cumsum(y)
    fp = np.cumsum(1 - y)
    tpr = np.concatenate([[0.0], tp / positives])
    fpr = np.concatenate([[0.0], fp / negatives])
    auc = float(np.trapz(tpr, fpr))
    return fpr, tpr, auc


def roc_curves(
    plt: Any,
    lvot: pd.DataFrame,
    stem: str,
    figure_dir: Path,
    formats: list[str],
    dpi: int,
) -> list[str]:
    score = -lvot["predicted_lvot_vti_cm"].to_numpy(float)
    fig, ax = plt.subplots(figsize=(5.0, 4.6))
    for label, col in [("LVOT VTI <18 cm", "low_vti_lt_18"), ("LVOT VTI <20 cm", "low_vti_lt_20")]:
        y_true = lvot[col].to_numpy(int)
        fpr, tpr, auc = roc_points(y_true, score)
        ax.plot(fpr, tpr, linewidth=1.6, label=f"{label} (AUROC {auc:.3f})")
    ax.plot([0, 1], [0, 1], linestyle="--", color="#4A5568", linewidth=1.0)
    ax.set_xlabel("False positive rate")
    ax.set_ylabel("True positive rate")
    ax.set_title("Exploratory low LVOT VTI ROC")
    ax.legend(loc="lower right")
    paths = save_figure(fig, figure_dir, stem, formats, dpi)
    plt.close(fig)
    return paths


def mae_comparison(
    plt: Any,
    metric_info: dict[str, Any],
    target_label: str,
    stem: str,
    figure_dir: Path,
    formats: list[str],
    dpi: int,
) -> list[str] | None:
    if not metric_info:
        return None
    unit = metric_info.get("unit", "")
    null_mae = metric_info.get("null_mae")
    ridge_mae = metric_info.get("ridge_mae")
    if null_mae is None or ridge_mae is None:
        return None
    values = [float(null_mae), float(ridge_mae)]
    labels = ["Null median", "Ridge"]
    low = metric_info.get("ridge_mae_ci_low")
    high = metric_info.get("ridge_mae_ci_high")
    yerr = [[0.0, values[1] - float(low) if low is not None else 0.0], [0.0, float(high) - values[1] if high is not None else 0.0]]
    fig, ax = plt.subplots(figsize=(4.7, 4.4))
    ax.bar(labels, values, color=["#A0AEC0", "#2B6CB0"], width=0.58)
    ax.errorbar(labels, values, yerr=yerr, fmt="none", ecolor="#1A202C", capsize=5, linewidth=1.1)
    ax.set_ylabel(f"Test MAE ({unit})" if unit else "Test MAE")
    ax.set_title(f"{target_label}: null vs Ridge MAE")
    for idx, value in enumerate(values):
        ax.text(idx, value, f"{value:.2f}", ha="center", va="bottom", fontsize=9)
    paths = save_figure(fig, figure_dir, stem, formats, dpi)
    plt.close(fig)
    return paths


def record(inventory: list[dict[str, Any]], figure: str, files: list[str], source: str, notes: str) -> None:
    inventory.append({"figure": figure, "files": ";".join(files), "source": source, "notes": notes})


def write_captions(path: Path, inventory: list[dict[str, Any]]) -> None:
    lines = [
        "# Phase 2 Local Figure Caption Drafts",
        "",
        "These figures were rendered from minimal deidentified figure-ready CSVs. The input CSVs remain restricted derived row-level data and must not be committed, uploaded, or shared outside approved storage.",
        "",
        "## Figure 1. LVOT VTI Primary Model Performance",
        "",
        "Frozen EchoPrime study embeddings were used to predict structured LVOT VTI on the held-out subject-level test split. Ridge regression used train-fit feature standardization, the numerically stable `svd` solver, and validation-only alpha selection. Observed-versus-predicted and Bland-Altman panels summarize continuous LVOT VTI predictions. Null-versus-Ridge MAE is shown with subject-level bootstrap 95% confidence intervals when aggregate metrics are available. Exploratory ROC curves for LVOT VTI <18 cm and <20 cm are thresholded summaries of the continuous predictions, not separately trained classifiers.",
        "",
        "## Supplementary Figure S1. TAPSE Secondary Endpoint Performance",
        "",
        "Frozen EchoPrime study embeddings were used to predict structured TAPSE on the held-out subject-level test split. TAPSE is a cautious secondary endpoint because the test set is smaller and the selected alpha indicates strong regularization.",
        "",
        "## Inventory",
        "",
        "| Figure | Files | Source | Notes |",
        "|---|---|---|---|",
    ]
    for row in inventory:
        lines.append(f"| {row['figure']} | {row['files']} | {row['source']} | {row['notes']} |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    guard_output_dir(args.figure_dir, args.allow_repo_output_for_testing)
    formats = parse_formats(args.format)
    args.figure_dir.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest(args.figure_ready_dir / MANIFEST_FILE)
    metrics = load_metrics(args, manifest)
    plt = setup_matplotlib()
    inventory: list[dict[str, Any]] = []

    lvot_path = args.figure_ready_dir / LVOT_FILE
    if lvot_path.exists():
        lvot = pd.read_csv(lvot_path)
        observed = pd.to_numeric(lvot["observed_lvot_vti_cm"], errors="coerce").to_numpy(float)
        predicted = pd.to_numeric(lvot["predicted_lvot_vti_cm"], errors="coerce").to_numpy(float)
        files = observed_vs_predicted(plt, observed, predicted, "LVOT VTI", "cm", "fig1_lvot_observed_vs_predicted", args.figure_dir, formats, args.dpi)
        record(inventory, "fig1_lvot_observed_vs_predicted", files, LVOT_FILE, "No identifiers or point labels.")
        files = bland_altman(plt, observed, predicted, "LVOT VTI", "cm", "fig1_lvot_bland_altman", args.figure_dir, formats, args.dpi)
        record(inventory, "fig1_lvot_bland_altman", files, LVOT_FILE, "Prediction minus observed.")
        files = roc_curves(plt, lvot, "fig1_lvot_low_vti_roc", args.figure_dir, formats, args.dpi)
        record(inventory, "fig1_lvot_low_vti_roc", files, LVOT_FILE, "Exploratory threshold summaries of continuous predictions.")
        mae_files = mae_comparison(plt, metrics.get("lvot_all_clips", {}), "LVOT VTI", "fig1_lvot_mae_comparison", args.figure_dir, formats, args.dpi)
        if mae_files:
            record(inventory, "fig1_lvot_mae_comparison", mae_files, MANIFEST_FILE, "Aggregate null-vs-Ridge MAE.")

    tapse_path = args.figure_ready_dir / TAPSE_FILE
    if tapse_path.exists():
        tapse = pd.read_csv(tapse_path)
        observed = pd.to_numeric(tapse["observed_tapse_mm"], errors="coerce").to_numpy(float)
        predicted = pd.to_numeric(tapse["predicted_tapse_mm"], errors="coerce").to_numpy(float)
        files = observed_vs_predicted(plt, observed, predicted, "TAPSE", "mm", "figS1_tapse_observed_vs_predicted", args.figure_dir, formats, args.dpi)
        record(inventory, "figS1_tapse_observed_vs_predicted", files, TAPSE_FILE, "No identifiers or point labels.")
        files = bland_altman(plt, observed, predicted, "TAPSE", "mm", "figS1_tapse_bland_altman", args.figure_dir, formats, args.dpi)
        record(inventory, "figS1_tapse_bland_altman", files, TAPSE_FILE, "Prediction minus observed.")
        mae_files = mae_comparison(plt, metrics.get("tapse_all_clips", {}), "TAPSE", "figS1_tapse_mae_comparison", args.figure_dir, formats, args.dpi)
        if mae_files:
            record(inventory, "figS1_tapse_mae_comparison", mae_files, MANIFEST_FILE, "Aggregate null-vs-Ridge MAE.")

    inventory_df = pd.DataFrame(inventory)
    inventory_df.to_csv(args.figure_dir / "phase2_local_figure_inventory.csv", index=False)
    write_captions(args.figure_dir / "phase2_local_figure_caption_drafts.md", inventory)
    print(
        json.dumps(
            {
                "figure_dir": str(args.figure_dir),
                "figures_written": len(inventory),
                "row_level_outputs_written": False,
                "identifier_columns_required": False,
                "files": sorted(path.name for path in args.figure_dir.iterdir() if path.is_file()),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
