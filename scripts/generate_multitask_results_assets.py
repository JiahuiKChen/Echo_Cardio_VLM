#!/usr/bin/env python3
"""Generate reporting assets for multitask Vision/Tabular/Fusion results.

Inputs (strict panel expected by default):
- eval_multitask_vision_strict/{summary json + task metrics csv}
- eval_multitask_tabular_strict/{summary json + task metrics csv}
- eval_multitask_fusion_strict/{summary json + task metrics csv}

Outputs:
- multitask_macro_summary.csv/.md
- multitask_task_level_comparison.csv/.md
- multitask_win_counts.csv/.md
- multitask_top_fusion_gains.csv/.md
- multitask_results_summary.json
- optional figures if matplotlib is available
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fullscale-root", type=Path, required=True)
    parser.add_argument("--vision-dir", default="eval_multitask_vision_strict")
    parser.add_argument("--tabular-dir", default="eval_multitask_tabular_strict")
    parser.add_argument("--fusion-dir", default="eval_multitask_fusion_strict")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Defaults to <fullscale-root>/reporting_multitask_assets",
    )
    parser.add_argument("--top-k", type=int, default=15)
    return parser.parse_args()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def to_md_table(df: pd.DataFrame) -> str:
    if df.empty:
        return "| Empty |\n|---|\n| No rows |\n"
    cols = [str(c) for c in df.columns]
    lines = [
        "| " + " | ".join(cols) + " |",
        "|" + "|".join(["---"] * len(cols)) + "|",
    ]
    for _, row in df.iterrows():
        vals = []
        for c in df.columns:
            v = row[c]
            if isinstance(v, float):
                vals.append(f"{v:.4f}")
            else:
                vals.append(str(v))
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines) + "\n"


def save_table(df: pd.DataFrame, csv_path: Path, md_path: Path) -> None:
    df.to_csv(csv_path, index=False)
    md_path.write_text(to_md_table(df))


def maybe_import_matplotlib():
    try:
        import matplotlib.pyplot as plt  # type: ignore
    except Exception:
        return None
    return plt


def plot_macro_bars(df: pd.DataFrame, out_png: Path) -> bool:
    plt = maybe_import_matplotlib()
    if plt is None:
        return False

    fig, axes = plt.subplots(1, 3, figsize=(13, 4))
    x = np.arange(len(df))
    labels = df["modality"].tolist()

    axes[0].bar(x, df["mean_r2"], color=["#1f77b4", "#ff7f0e", "#2ca02c"])
    axes[0].set_title("Mean Test R2")
    axes[0].set_ylim(0.0, 1.0)
    axes[0].set_xticks(x, labels, rotation=20, ha="right")

    axes[1].bar(x, df["median_r2"], color=["#1f77b4", "#ff7f0e", "#2ca02c"])
    axes[1].set_title("Median Test R2")
    axes[1].set_ylim(0.0, 1.0)
    axes[1].set_xticks(x, labels, rotation=20, ha="right")

    axes[2].bar(x, df["mean_mae_norm_iqr"], color=["#1f77b4", "#ff7f0e", "#2ca02c"])
    axes[2].set_title("Mean Test MAE / IQR (lower better)")
    axes[2].set_xticks(x, labels, rotation=20, ha="right")

    fig.suptitle("Multitask Macro Comparison", y=1.02)
    fig.tight_layout()
    fig.savefig(out_png, dpi=200, bbox_inches="tight")
    plt.close(fig)
    return True


def plot_fusion_gain_hist(df: pd.DataFrame, out_png: Path) -> bool:
    plt = maybe_import_matplotlib()
    if plt is None:
        return False
    if df.empty:
        return False

    fig, axes = plt.subplots(1, 2, figsize=(11, 4))

    axes[0].hist(df["delta_r2_fusion_vs_vision"].dropna(), bins=16, alpha=0.8, color="#2ca02c")
    axes[0].axvline(0.0, color="black", linestyle="--", linewidth=1)
    axes[0].set_title("Fusion - Vision (Test R2)")
    axes[0].set_xlabel("Delta R2")
    axes[0].set_ylabel("Task count")

    axes[1].hist(df["delta_r2_fusion_vs_tabular"].dropna(), bins=16, alpha=0.8, color="#1f77b4")
    axes[1].axvline(0.0, color="black", linestyle="--", linewidth=1)
    axes[1].set_title("Fusion - Tabular (Test R2)")
    axes[1].set_xlabel("Delta R2")

    fig.tight_layout()
    fig.savefig(out_png, dpi=200, bbox_inches="tight")
    plt.close(fig)
    return True


def load_run(root: Path, run_dir: str, summary_name: str, metrics_name: str) -> tuple[dict[str, Any], pd.DataFrame]:
    run_root = root / run_dir
    s_path = run_root / summary_name
    m_path = run_root / metrics_name
    if not s_path.exists():
        raise FileNotFoundError(f"Missing summary: {s_path}")
    if not m_path.exists():
        raise FileNotFoundError(f"Missing metrics CSV: {m_path}")
    summary = read_json(s_path)
    metrics = pd.read_csv(m_path)
    return summary, metrics


def main() -> int:
    args = parse_args()
    root = args.fullscale_root.resolve()
    out_dir = (args.output_dir or (root / "reporting_multitask_assets")).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    vision_summary, vision_metrics = load_run(
        root,
        args.vision_dir,
        "multitask_vision.summary.json",
        "multitask_vision_task_metrics.csv",
    )
    tab_summary, tab_metrics = load_run(
        root,
        args.tabular_dir,
        "multitask_tabular.summary.json",
        "multitask_tabular_task_metrics.csv",
    )
    fusion_summary, fusion_metrics = load_run(
        root,
        args.fusion_dir,
        "multitask_fusion.summary.json",
        "multitask_fusion_task_metrics.csv",
    )

    # Restrict to scored tasks and comparable columns.
    keep_cols = ["task_col", "test_r2", "test_mae", "test_mae_norm_iqr", "n_test_with_value", "status"]
    for name, df in [("vision", vision_metrics), ("tabular", tab_metrics), ("fusion", fusion_metrics)]:
        missing = set(keep_cols) - set(df.columns)
        if missing:
            raise ValueError(f"{name} metrics missing required columns: {sorted(missing)}")

    v = vision_metrics[keep_cols].copy()
    t = tab_metrics[keep_cols].copy()
    f = fusion_metrics[keep_cols].copy()

    v_ok = v[v["status"] == "ok"].drop(columns=["status"]).rename(
        columns={
            "test_r2": "vision_test_r2",
            "test_mae": "vision_test_mae",
            "test_mae_norm_iqr": "vision_test_mae_norm_iqr",
            "n_test_with_value": "vision_n_test",
        }
    )
    t_ok = t[t["status"] == "ok"].drop(columns=["status"]).rename(
        columns={
            "test_r2": "tabular_test_r2",
            "test_mae": "tabular_test_mae",
            "test_mae_norm_iqr": "tabular_test_mae_norm_iqr",
            "n_test_with_value": "tabular_n_test",
        }
    )
    f_ok = f[f["status"] == "ok"].drop(columns=["status"]).rename(
        columns={
            "test_r2": "fusion_test_r2",
            "test_mae": "fusion_test_mae",
            "test_mae_norm_iqr": "fusion_test_mae_norm_iqr",
            "n_test_with_value": "fusion_n_test",
        }
    )

    merged = v_ok.merge(t_ok, on="task_col", how="inner").merge(f_ok, on="task_col", how="inner")
    if merged.empty:
        raise RuntimeError("No common scored tasks across vision/tabular/fusion runs.")

    merged["delta_r2_fusion_vs_vision"] = merged["fusion_test_r2"] - merged["vision_test_r2"]
    merged["delta_r2_fusion_vs_tabular"] = merged["fusion_test_r2"] - merged["tabular_test_r2"]
    merged["delta_mae_norm_fusion_vs_vision"] = (
        merged["fusion_test_mae_norm_iqr"] - merged["vision_test_mae_norm_iqr"]
    )
    merged["delta_mae_norm_fusion_vs_tabular"] = (
        merged["fusion_test_mae_norm_iqr"] - merged["tabular_test_mae_norm_iqr"]
    )

    # Wins by task.
    def argmax3(a: float, b: float, c: float) -> str:
        vals = {"vision": a, "tabular": b, "fusion": c}
        return max(vals, key=vals.get)

    def argmin3(a: float, b: float, c: float) -> str:
        vals = {"vision": a, "tabular": b, "fusion": c}
        return min(vals, key=vals.get)

    merged["best_by_r2"] = merged.apply(
        lambda r: argmax3(r["vision_test_r2"], r["tabular_test_r2"], r["fusion_test_r2"]),
        axis=1,
    )
    merged["best_by_mae_norm"] = merged.apply(
        lambda r: argmin3(r["vision_test_mae_norm_iqr"], r["tabular_test_mae_norm_iqr"], r["fusion_test_mae_norm_iqr"]),
        axis=1,
    )

    # Macro table from summaries.
    macro = pd.DataFrame(
        [
            {
                "modality": "vision",
                "tasks_scored": vision_summary.get("n_tasks_scored"),
                "mean_r2": vision_summary.get("macro_metrics_test", {}).get("mean_r2"),
                "median_r2": vision_summary.get("macro_metrics_test", {}).get("median_r2"),
                "mean_mae_norm_iqr": vision_summary.get("macro_metrics_test", {}).get("mean_mae_norm_iqr"),
            },
            {
                "modality": "tabular",
                "tasks_scored": tab_summary.get("n_tasks_scored"),
                "mean_r2": tab_summary.get("macro_metrics_test", {}).get("mean_r2"),
                "median_r2": tab_summary.get("macro_metrics_test", {}).get("median_r2"),
                "mean_mae_norm_iqr": tab_summary.get("macro_metrics_test", {}).get("mean_mae_norm_iqr"),
            },
            {
                "modality": "fusion",
                "tasks_scored": fusion_summary.get("n_tasks_scored"),
                "mean_r2": fusion_summary.get("macro_metrics_test", {}).get("mean_r2"),
                "median_r2": fusion_summary.get("macro_metrics_test", {}).get("median_r2"),
                "mean_mae_norm_iqr": fusion_summary.get("macro_metrics_test", {}).get("mean_mae_norm_iqr"),
            },
        ]
    )

    win_counts = pd.DataFrame(
        [
            {
                "criterion": "best_test_r2",
                "vision": int((merged["best_by_r2"] == "vision").sum()),
                "tabular": int((merged["best_by_r2"] == "tabular").sum()),
                "fusion": int((merged["best_by_r2"] == "fusion").sum()),
            },
            {
                "criterion": "best_test_mae_norm_iqr",
                "vision": int((merged["best_by_mae_norm"] == "vision").sum()),
                "tabular": int((merged["best_by_mae_norm"] == "tabular").sum()),
                "fusion": int((merged["best_by_mae_norm"] == "fusion").sum()),
            },
        ]
    )

    top_gain = merged.sort_values("delta_r2_fusion_vs_vision", ascending=False).head(args.top_k).copy()
    top_gain = top_gain[
        [
            "task_col",
            "vision_test_r2",
            "tabular_test_r2",
            "fusion_test_r2",
            "delta_r2_fusion_vs_vision",
            "delta_r2_fusion_vs_tabular",
            "vision_test_mae_norm_iqr",
            "tabular_test_mae_norm_iqr",
            "fusion_test_mae_norm_iqr",
        ]
    ]

    # Save tables.
    save_table(
        macro,
        out_dir / "multitask_macro_summary.csv",
        out_dir / "multitask_macro_summary.md",
    )
    save_table(
        merged.sort_values("fusion_test_r2", ascending=False),
        out_dir / "multitask_task_level_comparison.csv",
        out_dir / "multitask_task_level_comparison.md",
    )
    save_table(
        win_counts,
        out_dir / "multitask_win_counts.csv",
        out_dir / "multitask_win_counts.md",
    )
    save_table(
        top_gain,
        out_dir / "multitask_top_fusion_gains.csv",
        out_dir / "multitask_top_fusion_gains.md",
    )

    fig_macro_ok = plot_macro_bars(macro, out_dir / "figure_multitask_macro.png")
    fig_hist_ok = plot_fusion_gain_hist(merged, out_dir / "figure_multitask_fusion_gain_hist.png")

    summary = {
        "fullscale_root": str(root),
        "vision_dir": args.vision_dir,
        "tabular_dir": args.tabular_dir,
        "fusion_dir": args.fusion_dir,
        "n_common_tasks": int(len(merged)),
        "macro": macro.to_dict(orient="records"),
        "win_counts": win_counts.to_dict(orient="records"),
        "fusion_gain_summary": {
            "mean_delta_r2_vs_vision": float(merged["delta_r2_fusion_vs_vision"].mean()),
            "median_delta_r2_vs_vision": float(merged["delta_r2_fusion_vs_vision"].median()),
            "mean_delta_r2_vs_tabular": float(merged["delta_r2_fusion_vs_tabular"].mean()),
            "median_delta_r2_vs_tabular": float(merged["delta_r2_fusion_vs_tabular"].median()),
            "n_tasks_fusion_beats_vision_r2": int((merged["delta_r2_fusion_vs_vision"] > 0).sum()),
            "n_tasks_fusion_beats_tabular_r2": int((merged["delta_r2_fusion_vs_tabular"] > 0).sum()),
        },
        "figure_generation": {
            "figure_multitask_macro_png": fig_macro_ok,
            "figure_multitask_fusion_gain_hist_png": fig_hist_ok,
        },
        "outputs_dir": str(out_dir),
    }
    (out_dir / "multitask_results_summary.json").write_text(json.dumps(summary, indent=2))

    print(f"[written] {(out_dir / 'multitask_macro_summary.csv').resolve()}")
    print(f"[written] {(out_dir / 'multitask_task_level_comparison.csv').resolve()}")
    print(f"[written] {(out_dir / 'multitask_win_counts.csv').resolve()}")
    print(f"[written] {(out_dir / 'multitask_top_fusion_gains.csv').resolve()}")
    print(f"[written] {(out_dir / 'multitask_results_summary.json').resolve()}")
    if fig_macro_ok:
        print(f"[written] {(out_dir / 'figure_multitask_macro.png').resolve()}")
    else:
        print("[warn] figure_multitask_macro.png not generated (matplotlib unavailable)")
    if fig_hist_ok:
        print(f"[written] {(out_dir / 'figure_multitask_fusion_gain_hist.png').resolve()}")
    else:
        print("[warn] figure_multitask_fusion_gain_hist.png not generated (matplotlib unavailable)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

