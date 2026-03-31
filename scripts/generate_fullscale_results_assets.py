#!/usr/bin/env python3
"""Generate manuscript-ready tables/figures from fullscale pipeline outputs.

Outputs:
- table_1_cohort_counts.csv / .md
- table_2_primary_metrics.csv / .md
- table_3_tabular_feature_audit.csv / .md
- figure_1_primary_metrics.png
- figure_2_auc_with_bootstrap_ci.png (if bootstrap metrics are available)
- results_summary.json
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fullscale-root",
        type=Path,
        required=True,
        help="Fullscale output root (e.g., outputs/cloud_cohorts/fullscale_all).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Where to write tables/figures. Defaults to <fullscale-root>/reporting_assets.",
    )
    parser.add_argument(
        "--e5-bootstrap-metrics",
        type=Path,
        default=None,
        help="Optional fusion_metrics.json from high-bootstrap rerun (e.g. eval_e5_fusion_boot10k).",
    )
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


def metric_row(name: str, auc: Any, r2: Any, mae: Any, n_test: Any) -> dict[str, Any]:
    return {
        "experiment": name,
        "test_n": n_test,
        "test_auc": auc,
        "test_r2": r2,
        "test_mae": mae,
    }


def maybe_import_matplotlib():
    try:
        import matplotlib.pyplot as plt  # type: ignore
    except Exception:
        return None
    return plt


def plot_primary_metrics(df: pd.DataFrame, out_png: Path) -> bool:
    plt = maybe_import_matplotlib()
    if plt is None:
        return False

    plot_df = df.copy()
    for c in ["test_auc", "test_r2", "test_mae"]:
        plot_df[c] = pd.to_numeric(plot_df[c], errors="coerce")

    fig, axes = plt.subplots(1, 3, figsize=(13, 4))
    x = range(len(plot_df))
    names = plot_df["experiment"].tolist()

    axes[0].bar(x, plot_df["test_auc"], color=["#1f77b4", "#ff7f0e", "#2ca02c"])
    axes[0].set_title("Test AUC")
    axes[0].set_ylim(0.0, 1.0)
    axes[0].set_xticks(list(x), names, rotation=20, ha="right")

    axes[1].bar(x, plot_df["test_r2"], color=["#1f77b4", "#ff7f0e", "#2ca02c"])
    axes[1].set_title("Test R2")
    axes[1].set_ylim(0.0, 1.0)
    axes[1].set_xticks(list(x), names, rotation=20, ha="right")

    axes[2].bar(x, plot_df["test_mae"], color=["#1f77b4", "#ff7f0e", "#2ca02c"])
    axes[2].set_title("Test MAE (EF points)")
    axes[2].set_xticks(list(x), names, rotation=20, ha="right")

    fig.suptitle("Primary Test Metrics by Experiment", y=1.02)
    fig.tight_layout()
    fig.savefig(out_png, dpi=200, bbox_inches="tight")
    plt.close(fig)
    return True


def plot_auc_ci(e5_boot: dict[str, Any], out_png: Path) -> bool:
    plt = maybe_import_matplotlib()
    if plt is None:
        return False

    results = e5_boot.get("results", {})
    rows = []
    for cfg in ["vision_only__linear", "tabular_only__linear", "fusion_concat__linear"]:
        test = results.get(cfg, {}).get("test", {})
        b = test.get("bootstrap", {})
        auc = test.get("clf_auc")
        lo = b.get("auc_ci_lo")
        hi = b.get("auc_ci_hi")
        if auc is None or lo is None or hi is None:
            continue
        rows.append(
            {
                "config": cfg,
                "auc": float(auc),
                "ci_lo": float(lo),
                "ci_hi": float(hi),
            }
        )

    if not rows:
        return False

    df = pd.DataFrame(rows)
    x = list(range(len(df)))
    y = df["auc"].to_numpy()
    yerr_lo = y - df["ci_lo"].to_numpy()
    yerr_hi = df["ci_hi"].to_numpy() - y

    fig, ax = plt.subplots(figsize=(7, 4))
    ax.errorbar(
        x,
        y,
        yerr=[yerr_lo, yerr_hi],
        fmt="o",
        capsize=4,
        color="#1f77b4",
    )
    ax.set_xticks(x, df["config"].tolist(), rotation=20, ha="right")
    ax.set_ylim(0.75, 1.0)
    ax.set_ylabel("Test AUC")
    ax.set_title("Test AUC with 95% Bootstrap CI")
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_png, dpi=200, bbox_inches="tight")
    plt.close(fig)
    return True


def main() -> int:
    args = parse_args()
    root = args.fullscale_root.resolve()
    out_dir = (args.output_dir or (root / "reporting_assets")).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    # Required inputs
    audit_summary_path = root / "audit_postrun" / "fullscale_audit_summary.json"
    e2_path = root / "eval_e2b_vision" / "echoprime_embedding_baseline_metrics.json"
    e3_path = root / "eval_e3_tabular" / "tabular_baseline_metrics.json"
    e5_path = root / "eval_e5_fusion" / "fusion_metrics.json"
    leakage_path = root / "eval_e3_tabular" / "measurement_leakage_audit.json"

    required = [audit_summary_path, e2_path, e3_path, e5_path, leakage_path]
    missing = [str(p) for p in required if not p.exists()]
    if missing:
        raise FileNotFoundError(f"Missing required input files: {missing}")

    audit = read_json(audit_summary_path)
    e2 = read_json(e2_path)
    e3 = read_json(e3_path)
    e5 = read_json(e5_path)
    leak = read_json(leakage_path)

    # Optional higher-bootstrap metrics (if provided or present by convention)
    e5_boot_path = args.e5_bootstrap_metrics
    if e5_boot_path is None:
        candidate = root / "eval_e5_fusion_boot10k" / "fusion_metrics.json"
        if candidate.exists():
            e5_boot_path = candidate
    e5_boot = read_json(e5_boot_path) if e5_boot_path and e5_boot_path.exists() else None

    counts = audit.get("counts", {})
    intersections = audit.get("intersections", {})
    checks = audit.get("checks", {})

    # ---------------------------
    # Table 1: Cohort counts
    # ---------------------------
    table1 = pd.DataFrame(
        [
            {"stage": "Eligible studies", "n_studies": counts.get("all_eligible_studies", {}).get("n_studies", "")},
            {"stage": "Study embeddings available", "n_studies": counts.get("study_embedding_manifest", {}).get("n_studies", "")},
            {"stage": "LVEF evaluation cohort", "n_studies": counts.get("lvef_manifest", {}).get("n_studies", "")},
            {
                "stage": "LVEF ∩ study embeddings",
                "n_studies": intersections.get("lvef_vs_study_embeddings", {}).get("n_intersection", ""),
            },
            {
                "stage": "LVEF missing in study embeddings",
                "n_studies": intersections.get("lvef_vs_study_embeddings", {}).get("n_lvef_missing_in_study_embeddings", ""),
            },
        ]
    )
    save_table(
        table1,
        out_dir / "table_1_cohort_counts.csv",
        out_dir / "table_1_cohort_counts.md",
    )

    # ---------------------------
    # Table 2: Primary metrics
    # ---------------------------
    e2_test = e2.get("study_metrics", {}).get("test", {})
    e3_test = e3.get("split_metrics", {}).get("test", {})
    e5_fusion_test = e5.get("results", {}).get("fusion_concat__linear", {}).get("test", {})

    table2 = pd.DataFrame(
        [
            metric_row(
                "E2b vision-only",
                e2_test.get("clf_auc"),
                e2_test.get("reg_r2"),
                e2_test.get("reg_mae"),
                e2_test.get("n_rows"),
            ),
            metric_row(
                "E3 tabular-only",
                e3_test.get("clf_auc"),
                e3_test.get("reg_r2"),
                e3_test.get("reg_mae"),
                e3_test.get("n_rows"),
            ),
            metric_row(
                "E5 fusion (concat+linear)",
                e5_fusion_test.get("clf_auc"),
                e5_fusion_test.get("reg_r2"),
                e5_fusion_test.get("reg_mae"),
                e5_fusion_test.get("n_rows"),
            ),
        ]
    )
    table2["delta_auc_vs_e2b"] = table2["test_auc"] - table2.loc[0, "test_auc"]
    table2["delta_r2_vs_e2b"] = table2["test_r2"] - table2.loc[0, "test_r2"]
    table2["delta_mae_vs_e2b"] = table2["test_mae"] - table2.loc[0, "test_mae"]
    save_table(
        table2,
        out_dir / "table_2_primary_metrics.csv",
        out_dir / "table_2_primary_metrics.md",
    )

    # ---------------------------
    # Table 3: Tabular feature audit
    # ---------------------------
    table3 = pd.DataFrame(
        [
            {"item": "Unique measurements (raw)", "value": leak.get("total_unique_measurements", "")},
            {"item": "Excluded as LVEF leakage", "value": leak.get("n_excluded_leakage", "")},
            {"item": "Excluded for low coverage", "value": leak.get("n_excluded_low_coverage", "")},
            {"item": "Retained tabular features", "value": leak.get("n_retained_features", "")},
            {"item": "Mean missing rate (retained features)", "value": e3.get("missing_rate_mean", "")},
            {"item": "Tabular test AUC", "value": e3_test.get("clf_auc", "")},
            {"item": "Tabular test R2", "value": e3_test.get("reg_r2", "")},
            {"item": "Tabular test MAE", "value": e3_test.get("reg_mae", "")},
        ]
    )
    save_table(
        table3,
        out_dir / "table_3_tabular_feature_audit.csv",
        out_dir / "table_3_tabular_feature_audit.md",
    )

    # ---------------------------
    # Figures
    # ---------------------------
    primary_fig_ok = plot_primary_metrics(table2, out_dir / "figure_1_primary_metrics.png")
    auc_ci_fig_ok = plot_auc_ci(e5_boot, out_dir / "figure_2_auc_with_bootstrap_ci.png") if e5_boot else False

    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "fullscale_root": str(root),
        "output_dir": str(out_dir),
        "inputs": {
            "audit_summary": str(audit_summary_path),
            "e2_metrics": str(e2_path),
            "e3_metrics": str(e3_path),
            "e5_metrics": str(e5_path),
            "leakage_audit": str(leakage_path),
            "e5_bootstrap_metrics": str(e5_boot_path) if e5_boot_path else None,
        },
        "key_counts": {
            "eligible_studies": counts.get("all_eligible_studies", {}).get("n_studies"),
            "study_embeddings": counts.get("study_embedding_manifest", {}).get("n_studies"),
            "lvef_eval_studies": counts.get("lvef_manifest", {}).get("n_studies"),
            "lvef_intersection_with_embeddings": intersections.get("lvef_vs_study_embeddings", {}).get("n_intersection"),
            "lvef_missing_embeddings": intersections.get("lvef_vs_study_embeddings", {}).get("n_lvef_missing_in_study_embeddings"),
            "duplicate_study_embeddings": checks.get("study_embedding_duplicate_study_rows"),
        },
        "figure_generation": {
            "figure_1_primary_metrics_png": primary_fig_ok,
            "figure_2_auc_ci_png": auc_ci_fig_ok,
            "note": "If false, matplotlib is unavailable in the active Python env.",
        },
        "tables": [
            "table_1_cohort_counts.csv",
            "table_1_cohort_counts.md",
            "table_2_primary_metrics.csv",
            "table_2_primary_metrics.md",
            "table_3_tabular_feature_audit.csv",
            "table_3_tabular_feature_audit.md",
        ],
        "figures": [
            "figure_1_primary_metrics.png",
            "figure_2_auc_with_bootstrap_ci.png",
        ],
    }

    (out_dir / "results_summary.json").write_text(json.dumps(summary, indent=2))

    print(f"[written] {(out_dir / 'table_1_cohort_counts.csv').resolve()}")
    print(f"[written] {(out_dir / 'table_2_primary_metrics.csv').resolve()}")
    print(f"[written] {(out_dir / 'table_3_tabular_feature_audit.csv').resolve()}")
    print(f"[written] {(out_dir / 'results_summary.json').resolve()}")
    if primary_fig_ok:
        print(f"[written] {(out_dir / 'figure_1_primary_metrics.png').resolve()}")
    else:
        print("[warn] figure_1_primary_metrics.png not generated (matplotlib unavailable)")
    if e5_boot and auc_ci_fig_ok:
        print(f"[written] {(out_dir / 'figure_2_auc_with_bootstrap_ci.png').resolve()}")
    elif e5_boot:
        print("[warn] figure_2_auc_with_bootstrap_ci.png not generated (matplotlib unavailable)")
    else:
        print("[info] No e5 bootstrap metrics provided/found; skipping figure_2_auc_with_bootstrap_ci.png")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

