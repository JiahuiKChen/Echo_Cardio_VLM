#!/usr/bin/env python3
"""Plot Phase 2 figures from minimal restricted derived figure-ready CSVs.

The input CSVs must already be deidentified figure-ready exports. This script
does not require raw prediction files, identifiers, manifests, DICOM paths, or
SCC metadata. It writes only figure files plus aggregate/sanitized local figure
inventory and caption drafts.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


LVOT_FILE = "lvot_vti_test_predictions_figure_ready.csv"
TAPSE_FILE = "tapse_test_predictions_figure_ready.csv"
MANIFEST_FILE = "phase2_figure_ready_export_manifest.json"

COLORS = {
    "lvot": "#2563EB",
    "tapse": "#059669",
    "identity": "#2D3748",
    "calibration": "#C2410C",
    "bias": "#111827",
    "loa": "#B45309",
    "null": "#9CA3AF",
    "ridge": "#2563EB",
    "roc_18": "#2563EB",
    "roc_20": "#7C3AED",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--figure-ready-dir", type=Path, required=True)
    parser.add_argument("--figure-dir", type=Path, required=True)
    parser.add_argument("--format", default="png,pdf")
    parser.add_argument("--dpi", type=int, default=300)
    parser.add_argument("--random-seed", type=int, default=20260624)
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
            "savefig.facecolor": "white",
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.grid": True,
            "grid.alpha": 0.18,
            "grid.linewidth": 0.7,
            "font.family": "DejaVu Sans",
            "font.size": 9.5,
            "axes.titlesize": 10.5,
            "axes.labelsize": 9.5,
            "xtick.labelsize": 8.5,
            "ytick.labelsize": 8.5,
            "legend.fontsize": 8.5,
            "legend.frameon": False,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
        }
    )
    return plt


def save_figure(fig: Any, figure_dir: Path, stem: str, formats: list[str], dpi: int) -> list[str]:
    paths: list[str] = []
    safe_metadata = {"Creator": "Echo_Cardio_VLM Phase 2", "Title": stem}
    for fmt in formats:
        path = figure_dir / f"{stem}.{fmt}"
        fig.savefig(path, dpi=dpi, metadata=safe_metadata)
        paths.append(path.name)
    return paths


def finite_xy(observed: pd.Series, predicted: pd.Series) -> tuple[np.ndarray, np.ndarray]:
    obs = pd.to_numeric(observed, errors="coerce").to_numpy(float)
    pred = pd.to_numeric(predicted, errors="coerce").to_numpy(float)
    keep = np.isfinite(obs) & np.isfinite(pred)
    return obs[keep], pred[keep]


def metric_value(metric_info: dict[str, Any], key: str, default: float | None = None) -> float | None:
    value = metric_info.get(key)
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def computed_r2(observed: np.ndarray, predicted: np.ndarray) -> float:
    denom = float(np.sum((observed - np.mean(observed)) ** 2))
    if denom <= 0:
        return float("nan")
    return 1.0 - float(np.sum((observed - predicted) ** 2)) / denom


def metric_annotation(
    observed: np.ndarray,
    predicted: np.ndarray,
    metric_info: dict[str, Any],
    unit: str,
) -> str:
    mae = metric_value(metric_info, "ridge_mae")
    r2 = metric_value(metric_info, "ridge_r2")
    if mae is None:
        mae = float(np.mean(np.abs(predicted - observed)))
    if r2 is None:
        r2 = computed_r2(observed, predicted)
    return f"n={len(observed):,}\nMAE={mae:.2f} {unit}\nR$^2$={r2:.3f}"


def add_panel_label(ax: Any, label: str | None) -> None:
    if not label:
        return
    ax.text(
        -0.12,
        1.08,
        label,
        transform=ax.transAxes,
        fontsize=13,
        fontweight="bold",
        va="top",
        ha="left",
    )


def observed_vs_predicted_panel(
    ax: Any,
    observed: np.ndarray,
    predicted: np.ndarray,
    target_label: str,
    unit: str,
    color: str,
    metric_info: dict[str, Any],
    panel_label: str | None = None,
) -> None:
    low = float(np.nanmin([observed.min(), predicted.min()]))
    high = float(np.nanmax([observed.max(), predicted.max()]))
    pad = 0.06 * (high - low if high > low else 1.0)
    axis_low = low - pad
    axis_high = high + pad

    ax.scatter(observed, predicted, s=17, alpha=0.42, edgecolors="none", color=color)
    ax.plot(
        [axis_low, axis_high],
        [axis_low, axis_high],
        color=COLORS["identity"],
        linewidth=1.0,
        linestyle="--",
        label="Identity",
    )
    if len(observed) >= 3 and np.nanstd(observed) > 0:
        slope, intercept = np.polyfit(observed, predicted, deg=1)
        xx = np.linspace(axis_low, axis_high, 100)
        ax.plot(xx, slope * xx + intercept, color=COLORS["calibration"], linewidth=1.4, label="Calibration")

    ax.set_xlim(axis_low, axis_high)
    ax.set_ylim(axis_low, axis_high)
    ax.set_aspect("equal", adjustable="box")
    ax.set_xlabel(f"Observed {target_label} ({unit})")
    ax.set_ylabel(f"Predicted {target_label} ({unit})")
    ax.set_title("Observed vs predicted", loc="left", pad=8)
    ax.text(
        0.04,
        0.96,
        metric_annotation(observed, predicted, metric_info, unit),
        transform=ax.transAxes,
        va="top",
        ha="left",
        fontsize=8.5,
        bbox={"boxstyle": "round,pad=0.28", "facecolor": "white", "edgecolor": "#CBD5E1", "alpha": 0.92},
    )
    ax.legend(loc="lower right", handlelength=1.7)
    add_panel_label(ax, panel_label)


def bland_altman_panel(
    ax: Any,
    observed: np.ndarray,
    predicted: np.ndarray,
    target_label: str,
    unit: str,
    color: str,
    panel_label: str | None = None,
) -> None:
    mean_values = (observed + predicted) / 2.0
    diff = predicted - observed
    bias = float(np.mean(diff))
    sd = float(np.std(diff, ddof=1)) if len(diff) > 1 else float("nan")
    lower = bias - 1.96 * sd
    upper = bias + 1.96 * sd

    ax.scatter(mean_values, diff, s=17, alpha=0.42, edgecolors="none", color=color)
    ax.axhline(bias, color=COLORS["bias"], linewidth=1.2)
    ax.axhline(lower, color=COLORS["loa"], linewidth=1.0, linestyle="--")
    ax.axhline(upper, color=COLORS["loa"], linewidth=1.0, linestyle="--")
    ax.set_xlabel(f"Mean of observed and predicted {target_label} ({unit})")
    ax.set_ylabel(f"Prediction minus observed ({unit})")
    ax.set_title("Bland-Altman", loc="left", pad=8)
    ax.text(
        0.04,
        0.96,
        f"Bias {bias:+.2f} {unit}\nLOA {lower:+.2f} to {upper:+.2f} {unit}",
        transform=ax.transAxes,
        va="top",
        ha="left",
        fontsize=8.5,
        bbox={"boxstyle": "round,pad=0.28", "facecolor": "white", "edgecolor": "#CBD5E1", "alpha": 0.92},
    )
    add_panel_label(ax, panel_label)


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


def roc_panel(ax: Any, lvot: pd.DataFrame, panel_label: str | None = None) -> None:
    score = -pd.to_numeric(lvot["predicted_lvot_vti_cm"], errors="coerce").to_numpy(float)
    thresholds = [
        ("LVOT VTI <18 cm", "low_vti_lt_18", COLORS["roc_18"]),
        ("LVOT VTI <20 cm", "low_vti_lt_20", COLORS["roc_20"]),
    ]
    for label, col, color in thresholds:
        y_true = pd.to_numeric(lvot[col], errors="coerce").fillna(0).to_numpy(int)
        fpr, tpr, auc = roc_points(y_true, score)
        ax.plot(fpr, tpr, linewidth=1.7, color=color, label=f"{label} (AUROC {auc:.3f})")
    ax.plot([0, 1], [0, 1], linestyle="--", color="#6B7280", linewidth=1.0)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.set_xlabel("False positive rate")
    ax.set_ylabel("True positive rate")
    ax.set_title("Exploratory low-VTI ROC", loc="left", pad=8)
    ax.legend(loc="lower right")
    add_panel_label(ax, panel_label)


def mae_ci(metric_info: dict[str, Any], prefix: str, value: float) -> tuple[float, float] | None:
    low = metric_value(metric_info, f"{prefix}_mae_ci_low")
    high = metric_value(metric_info, f"{prefix}_mae_ci_high")
    if low is None or high is None:
        return None
    return max(0.0, value - low), max(0.0, high - value)


def mae_comparison_panel(
    ax: Any,
    metric_info: dict[str, Any],
    target_label: str,
    unit: str,
    panel_label: str | None = None,
) -> bool:
    null_mae = metric_value(metric_info, "null_mae")
    ridge_mae = metric_value(metric_info, "ridge_mae")
    if null_mae is None or ridge_mae is None:
        return False

    labels = ["Null median", "Ridge"]
    values = [null_mae, ridge_mae]
    prefixes = ["null", "ridge"]
    colors = [COLORS["null"], COLORS["ridge"]]
    x = np.array([0.0, 1.0])

    for idx, (value, prefix, color) in enumerate(zip(values, prefixes, colors)):
        ci = mae_ci(metric_info, prefix, value)
        yerr = None if ci is None else np.array([[ci[0]], [ci[1]]])
        ax.errorbar(
            [x[idx]],
            [value],
            yerr=yerr,
            fmt="o",
            markersize=6.5,
            color=color,
            ecolor="#1F2937",
            elinewidth=1.2,
            capsize=4 if ci is not None else 0,
            zorder=3,
        )
        ax.text(x[idx], value + max(values) * 0.045, f"{value:.2f}", ha="center", va="bottom", fontsize=8.5)

    ax.plot(x, values, color="#CBD5E1", linewidth=1.0, zorder=1)
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.set_xlim(-0.45, 1.45)
    ax.set_ylim(0, max(values) * 1.35)
    ax.set_ylabel(f"Test MAE ({unit})")
    ax.set_title("Null vs Ridge MAE", loc="left", pad=8)
    ax.text(
        0.04,
        0.96,
        "95% CI shown\nwhen available",
        transform=ax.transAxes,
        va="top",
        ha="left",
        fontsize=8.0,
        color="#4B5563",
    )
    add_panel_label(ax, panel_label)
    return True


def plot_individual(
    plt: Any,
    panel_func: Any,
    stem: str,
    figure_dir: Path,
    formats: list[str],
    dpi: int,
    figsize: tuple[float, float],
    *args: Any,
) -> list[str]:
    fig, ax = plt.subplots(figsize=figsize)
    panel_func(ax, *args)
    fig.tight_layout()
    paths = save_figure(fig, figure_dir, stem, formats, dpi)
    plt.close(fig)
    return paths


def plot_individual_mae(
    plt: Any,
    metric_info: dict[str, Any],
    target_label: str,
    unit: str,
    stem: str,
    figure_dir: Path,
    formats: list[str],
    dpi: int,
) -> list[str] | None:
    fig, ax = plt.subplots(figsize=(4.2, 3.8))
    ok = mae_comparison_panel(ax, metric_info, target_label, unit)
    if not ok:
        plt.close(fig)
        return None
    fig.tight_layout()
    paths = save_figure(fig, figure_dir, stem, formats, dpi)
    plt.close(fig)
    return paths


def lvot_composite(
    plt: Any,
    lvot: pd.DataFrame,
    metric_info: dict[str, Any],
    figure_dir: Path,
    formats: list[str],
    dpi: int,
) -> list[str]:
    observed, predicted = finite_xy(lvot["observed_lvot_vti_cm"], lvot["predicted_lvot_vti_cm"])
    fig, axes = plt.subplots(2, 2, figsize=(10.2, 8.4), constrained_layout=True)
    observed_vs_predicted_panel(axes[0, 0], observed, predicted, "LVOT VTI", "cm", COLORS["lvot"], metric_info, "A")
    bland_altman_panel(axes[0, 1], observed, predicted, "LVOT VTI", "cm", COLORS["lvot"], "B")
    mae_comparison_panel(axes[1, 0], metric_info, "LVOT VTI", "cm", "C")
    roc_panel(axes[1, 1], lvot, "D")
    paths = save_figure(fig, figure_dir, "figure1_lvot_vti_primary_composite", formats, dpi)
    plt.close(fig)
    return paths


def tapse_composite(
    plt: Any,
    tapse: pd.DataFrame,
    metric_info: dict[str, Any],
    figure_dir: Path,
    formats: list[str],
    dpi: int,
) -> list[str]:
    observed, predicted = finite_xy(tapse["observed_tapse_mm"], tapse["predicted_tapse_mm"])
    fig, axes = plt.subplots(1, 3, figsize=(13.0, 4.0), constrained_layout=True)
    observed_vs_predicted_panel(axes[0], observed, predicted, "TAPSE", "mm", COLORS["tapse"], metric_info, "A")
    bland_altman_panel(axes[1], observed, predicted, "TAPSE", "mm", COLORS["tapse"], "B")
    mae_comparison_panel(axes[2], metric_info, "TAPSE", "mm", "C")
    paths = save_figure(fig, figure_dir, "figureS1_tapse_secondary_composite", formats, dpi)
    plt.close(fig)
    return paths


def record(inventory: list[dict[str, Any]], figure: str, files: list[str], source: str, notes: str) -> None:
    inventory.append({"figure": figure, "files": ";".join(files), "source": source, "notes": notes})


def write_captions(path: Path, inventory: list[dict[str, Any]]) -> None:
    lines = [
        "# Phase 2 Local Figure Caption Drafts",
        "",
        "These figures were rendered from minimal deidentified figure-ready CSVs. The input CSVs remain restricted derived row-level data and must not be committed, uploaded, pasted into chat, or shared outside approved secure storage.",
        "",
        "## Main Figure 1. LVOT VTI Primary Model Performance",
        "",
        "Frozen EchoPrime study embeddings were used to predict structured LVOT VTI on a held-out subject-level test split. Ridge regression used train-fit feature standardization, the numerically stable `svd` solver, and validation-only alpha selection. Observed-versus-predicted and Bland-Altman panels summarize continuous LVOT VTI predictions. Null-versus-Ridge MAE is shown with subject-level bootstrap 95% confidence intervals when available from the aggregate manifest. ROC curves for LVOT VTI <18 cm and <20 cm are exploratory thresholded summaries of continuous predictions, not separately trained classifiers. Limits of agreement remain wide, so these results support an imaging-only estimation and risk-stratification signal rather than replacement of clinical Doppler LVOT VTI measurement.",
        "",
        "## Supplementary Figure S1. TAPSE Secondary Endpoint Performance",
        "",
        "Frozen EchoPrime study embeddings were used to predict structured TAPSE on the held-out subject-level test split with the same stable-v2 Ridge configuration. TAPSE was evaluated as a cautious secondary endpoint; the smaller test set and selected alpha of 1000 indicate strong regularization. These results should not be framed as measurement-grade TAPSE automation.",
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
        observed, predicted = finite_xy(lvot["observed_lvot_vti_cm"], lvot["predicted_lvot_vti_cm"])
        lvot_metrics = metrics.get("lvot_all_clips", {})
        files = plot_individual(
            plt,
            observed_vs_predicted_panel,
            "fig1_lvot_observed_vs_predicted",
            args.figure_dir,
            formats,
            args.dpi,
            (4.7, 4.2),
            observed,
            predicted,
            "LVOT VTI",
            "cm",
            COLORS["lvot"],
            lvot_metrics,
            None,
        )
        record(inventory, "fig1_lvot_observed_vs_predicted", files, LVOT_FILE, "No identifiers or point labels; includes identity and calibration lines.")
        files = plot_individual(
            plt,
            bland_altman_panel,
            "fig1_lvot_bland_altman",
            args.figure_dir,
            formats,
            args.dpi,
            (4.9, 4.2),
            observed,
            predicted,
            "LVOT VTI",
            "cm",
            COLORS["lvot"],
            None,
        )
        record(inventory, "fig1_lvot_bland_altman", files, LVOT_FILE, "Prediction minus observed; bias and limits of agreement shown.")
        files = plot_individual(
            plt,
            roc_panel,
            "fig1_lvot_low_vti_roc",
            args.figure_dir,
            formats,
            args.dpi,
            (4.6, 4.2),
            lvot,
            None,
        )
        record(inventory, "fig1_lvot_low_vti_roc", files, LVOT_FILE, "Exploratory threshold summaries of continuous predictions.")
        mae_files = plot_individual_mae(
            plt,
            lvot_metrics,
            "LVOT VTI",
            "cm",
            "fig1_lvot_mae_comparison",
            args.figure_dir,
            formats,
            args.dpi,
        )
        if mae_files:
            record(inventory, "fig1_lvot_mae_comparison", mae_files, MANIFEST_FILE, "Aggregate null-vs-Ridge MAE; CI whiskers shown when available.")
        files = lvot_composite(plt, lvot, lvot_metrics, args.figure_dir, formats, args.dpi)
        record(inventory, "figure1_lvot_vti_primary_composite", files, "local figure panels", "2x2 manuscript composite: observed-vs-predicted, Bland-Altman, MAE, ROC.")

    tapse_path = args.figure_ready_dir / TAPSE_FILE
    if tapse_path.exists():
        tapse = pd.read_csv(tapse_path)
        observed, predicted = finite_xy(tapse["observed_tapse_mm"], tapse["predicted_tapse_mm"])
        tapse_metrics = metrics.get("tapse_all_clips", {})
        files = plot_individual(
            plt,
            observed_vs_predicted_panel,
            "figS1_tapse_observed_vs_predicted",
            args.figure_dir,
            formats,
            args.dpi,
            (4.7, 4.2),
            observed,
            predicted,
            "TAPSE",
            "mm",
            COLORS["tapse"],
            tapse_metrics,
            None,
        )
        record(inventory, "figS1_tapse_observed_vs_predicted", files, TAPSE_FILE, "No identifiers or point labels; includes identity and calibration lines.")
        files = plot_individual(
            plt,
            bland_altman_panel,
            "figS1_tapse_bland_altman",
            args.figure_dir,
            formats,
            args.dpi,
            (4.9, 4.2),
            observed,
            predicted,
            "TAPSE",
            "mm",
            COLORS["tapse"],
            None,
        )
        record(inventory, "figS1_tapse_bland_altman", files, TAPSE_FILE, "Prediction minus observed; bias and limits of agreement shown.")
        mae_files = plot_individual_mae(
            plt,
            tapse_metrics,
            "TAPSE",
            "mm",
            "figS1_tapse_mae_comparison",
            args.figure_dir,
            formats,
            args.dpi,
        )
        if mae_files:
            record(inventory, "figS1_tapse_mae_comparison", mae_files, MANIFEST_FILE, "Aggregate null-vs-Ridge MAE; CI whiskers shown when available.")
        files = tapse_composite(plt, tapse, tapse_metrics, args.figure_dir, formats, args.dpi)
        record(inventory, "figureS1_tapse_secondary_composite", files, "local figure panels", "1x3 manuscript composite: observed-vs-predicted, Bland-Altman, MAE.")

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
