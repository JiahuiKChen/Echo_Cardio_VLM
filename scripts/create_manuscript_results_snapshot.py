#!/usr/bin/env python3
"""Create a manuscript-safe results snapshot from SCC outputs.

This copies only aggregate, public-shareable outputs into a tracked docs folder
and writes a README with:
- methodology summary
- key aggregated results
- SCC source paths
- commands used to regenerate the summary assets

It intentionally avoids copying row-level manifests, embeddings, or restricted
data-derived study files.
"""

import argparse
import csv
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fullscale-root",
        type=Path,
        required=True,
        help="SCC fullscale output root, e.g. outputs/cloud_cohorts/fullscale_all",
    )
    parser.add_argument(
        "--snapshot-dir",
        type=Path,
        required=True,
        help="Tracked destination, e.g. docs/results_snapshot/2026-04-01_fullscale",
    )
    parser.add_argument(
        "--freeze-pack",
        type=Path,
        default=None,
        help="Optional SCC freeze pack path to record in the README.",
    )
    return parser.parse_args()


def read_json(path):
    return json.loads(path.read_text())


def read_csv_rows(path):
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def copy_required(src, dst):
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def fmt_float(val, ndigits=4):
    if val in ("", None):
        return "NA"
    try:
        return f"{float(val):.{ndigits}f}"
    except Exception:
        return str(val)


def build_readme(
    snapshot_dir,
    fullscale_root,
    freeze_pack,
    audit,
    primary_summary,
    primary_table,
    multitask_summary,
    multitask_macro,
):
    counts = audit.get("counts", {})
    intersections = audit.get("intersections", {})

    eligible = counts.get("all_eligible_studies", {}).get("n_studies", "NA")
    study_emb = counts.get("study_embedding_manifest", {}).get("n_studies", "NA")
    lvef_eval = counts.get("lvef_manifest", {}).get("n_studies", "NA")
    lvef_inter = intersections.get("lvef_vs_study_embeddings", {}).get("n_intersection", "NA")
    lvef_missing = intersections.get("lvef_vs_study_embeddings", {}).get(
        "n_lvef_missing_in_study_embeddings", "NA"
    )

    structured = counts.get("structured_measurements", {})
    n_struct_rows = structured.get("n_rows", "NA")
    n_measurements = structured.get("n_unique_measurements", "NA")

    primary_lines = []
    for row in primary_table:
        primary_lines.append(
            "- {exp}: AUC {auc}, R2 {r2}, MAE {mae}, test n {n}".format(
                exp=row.get("experiment", row.get("Experiment", "model")),
                auc=fmt_float(row.get("test_auc") or row.get("AUC")),
                r2=fmt_float(row.get("test_r2") or row.get("R2")),
                mae=fmt_float(row.get("test_mae") or row.get("MAE (EF points)") or row.get("MAE")),
                n=row.get("test_n") or row.get("Test n") or row.get("n_rows") or "NA",
            )
        )

    macro_lines = []
    for row in multitask_macro:
        macro_lines.append(
            "- {mod}: tasks {tasks}, mean R2 {r2}, median R2 {med}, mean MAE/IQR {mae}".format(
                mod=row.get("modality", "model"),
                tasks=row.get("tasks_scored", "NA"),
                r2=fmt_float(row.get("mean_r2")),
                med=fmt_float(row.get("median_r2")),
                mae=fmt_float(row.get("mean_mae_norm_iqr")),
            )
        )

    return "\n".join(
        [
            "# Manuscript Results Snapshot",
            "",
            f"- Generated at UTC: `{datetime.now(timezone.utc).isoformat()}`",
            f"- Snapshot dir: `{snapshot_dir}`",
            f"- SCC fullscale root: `{fullscale_root}`",
            f"- SCC audit source: `{fullscale_root / 'audit_postrun' / 'fullscale_audit_summary.json'}`",
            f"- SCC primary assets source: `{fullscale_root / 'reporting_assets'}`",
            f"- SCC multitask assets source: `{fullscale_root / 'reporting_multitask_assets'}`",
            f"- SCC freeze pack: `{freeze_pack}`" if freeze_pack else "- SCC freeze pack: `not provided`",
            "",
            "## Methodology Summary",
            "",
            "We executed an SCC-first, reproducible pipeline on MIMIC-IV-ECHO with deterministic subject-level splitting and one study per subject. Multi-frame DICOM cine clips were encoded with EchoPrime (encoder-only, 512-dimensional embeddings) and aggregated to study level. Structured measurements were exported and curated with leakage controls. Primary analysis evaluated LVEF prediction using vision-only, structured-measurement-only, and multimodal fusion models. Secondary analysis evaluated a strict multitask panel of quantitative echocardiographic measurements.",
            "",
            "## Cohort Summary",
            "",
            f"- Eligible studies: `{eligible}`",
            f"- Study embeddings available: `{study_emb}`",
            f"- LVEF evaluation cohort: `{lvef_eval}`",
            f"- LVEF ∩ study embeddings: `{lvef_inter}`",
            f"- LVEF missing in study embeddings: `{lvef_missing}`",
            f"- Structured measurement rows: `{n_struct_rows}`",
            f"- Unique structured measurement names: `{n_measurements}`",
            "",
            "## Primary Endpoint Results",
            "",
            *primary_lines,
            "",
            "## Multitask Summary Results",
            "",
            *macro_lines,
            "",
            "## Included Files",
            "",
            "- `audit/fullscale_audit_summary.json`",
            "- `audit/fullscale_audit_report.md`",
            "- `primary/table_1_cohort_counts.csv`",
            "- `primary/table_2_primary_metrics.csv`",
            "- `primary/table_3_tabular_feature_audit.csv`",
            "- `primary/results_summary.json`",
            "- `primary/figure_1_primary_metrics.png`",
            "- `primary/figure_2_auc_with_bootstrap_ci.png`",
            "- `multitask/multitask_macro_summary.csv`",
            "- `multitask/multitask_task_level_comparison.csv`",
            "- `multitask/multitask_win_counts.csv`",
            "- `multitask/multitask_top_fusion_gains.csv`",
            "- `multitask/multitask_results_summary.json`",
            "- `multitask/figure_multitask_macro.png`",
            "- `multitask/figure_multitask_fusion_gain_hist.png`",
            "",
            "## Regeneration Commands",
            "",
            "```bash",
            "PY=.venv-echoprime/bin/python",
            f"$PY scripts/audit_fullscale_outputs.py --fullscale-root {fullscale_root} --output-dir {fullscale_root / 'audit_postrun'} --strict",
            f"$PY scripts/generate_fullscale_results_assets.py --fullscale-root {fullscale_root} --output-dir {fullscale_root / 'reporting_assets'} --e5-bootstrap-metrics {fullscale_root / 'eval_e5_fusion_boot10k' / 'fusion_metrics.json'}",
            f"$PY scripts/generate_multitask_results_assets.py --fullscale-root {fullscale_root} --vision-dir eval_multitask_vision_strict --tabular-dir eval_multitask_tabular_strict --fusion-dir eval_multitask_fusion_strict --output-dir {fullscale_root / 'reporting_multitask_assets'}",
            "```",
            "",
        ]
    )


def main():
    args = parse_args()
    fullscale_root = args.fullscale_root.resolve()
    snapshot_dir = args.snapshot_dir.resolve()
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    primary_dir = snapshot_dir / "primary"
    multitask_dir = snapshot_dir / "multitask"
    audit_dir = snapshot_dir / "audit"
    for d in (primary_dir, multitask_dir, audit_dir):
        d.mkdir(parents=True, exist_ok=True)

    files_to_copy = [
        (fullscale_root / "audit_postrun" / "fullscale_audit_summary.json", audit_dir / "fullscale_audit_summary.json"),
        (fullscale_root / "audit_postrun" / "fullscale_audit_report.md", audit_dir / "fullscale_audit_report.md"),
        (fullscale_root / "reporting_assets" / "table_1_cohort_counts.csv", primary_dir / "table_1_cohort_counts.csv"),
        (fullscale_root / "reporting_assets" / "table_2_primary_metrics.csv", primary_dir / "table_2_primary_metrics.csv"),
        (fullscale_root / "reporting_assets" / "table_3_tabular_feature_audit.csv", primary_dir / "table_3_tabular_feature_audit.csv"),
        (fullscale_root / "reporting_assets" / "results_summary.json", primary_dir / "results_summary.json"),
        (fullscale_root / "reporting_assets" / "figure_1_primary_metrics.png", primary_dir / "figure_1_primary_metrics.png"),
        (fullscale_root / "reporting_assets" / "figure_2_auc_with_bootstrap_ci.png", primary_dir / "figure_2_auc_with_bootstrap_ci.png"),
        (fullscale_root / "reporting_multitask_assets" / "multitask_macro_summary.csv", multitask_dir / "multitask_macro_summary.csv"),
        (fullscale_root / "reporting_multitask_assets" / "multitask_task_level_comparison.csv", multitask_dir / "multitask_task_level_comparison.csv"),
        (fullscale_root / "reporting_multitask_assets" / "multitask_win_counts.csv", multitask_dir / "multitask_win_counts.csv"),
        (fullscale_root / "reporting_multitask_assets" / "multitask_top_fusion_gains.csv", multitask_dir / "multitask_top_fusion_gains.csv"),
        (fullscale_root / "reporting_multitask_assets" / "multitask_results_summary.json", multitask_dir / "multitask_results_summary.json"),
        (fullscale_root / "reporting_multitask_assets" / "figure_multitask_macro.png", multitask_dir / "figure_multitask_macro.png"),
        (fullscale_root / "reporting_multitask_assets" / "figure_multitask_fusion_gain_hist.png", multitask_dir / "figure_multitask_fusion_gain_hist.png"),
    ]

    missing = [str(src) for src, _ in files_to_copy if not src.exists()]
    if missing:
        raise FileNotFoundError(f"Missing required aggregate artifacts: {missing}")

    for src, dst in files_to_copy:
        copy_required(src, dst)

    audit = read_json(audit_dir / "fullscale_audit_summary.json")
    primary_summary = read_json(primary_dir / "results_summary.json")
    primary_table = read_csv_rows(primary_dir / "table_2_primary_metrics.csv")
    multitask_summary = read_json(multitask_dir / "multitask_results_summary.json")
    multitask_macro = read_csv_rows(multitask_dir / "multitask_macro_summary.csv")

    readme = build_readme(
        snapshot_dir=snapshot_dir,
        fullscale_root=fullscale_root,
        freeze_pack=args.freeze_pack.resolve() if args.freeze_pack else None,
        audit=audit,
        primary_summary=primary_summary,
        primary_table=primary_table,
        multitask_summary=multitask_summary,
        multitask_macro=multitask_macro,
    )
    readme_path = snapshot_dir / "README.md"
    readme_path.write_text(readme)

    manifest = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "fullscale_root": str(fullscale_root),
        "snapshot_dir": str(snapshot_dir),
        "freeze_pack": str(args.freeze_pack.resolve()) if args.freeze_pack else None,
        "copied_files": [str(dst) for _, dst in files_to_copy],
        "readme": str(readme_path),
    }
    manifest_path = snapshot_dir / "snapshot_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))

    print(f"[written] {readme_path}")
    print(f"[written] {manifest_path}")
    for _, dst in files_to_copy:
        print(f"[copied] {dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
