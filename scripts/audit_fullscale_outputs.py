#!/usr/bin/env python3
"""Audit fullscale cohort artifacts for reproducibility and reporting parity.

Checks key manifests/metrics for:
- expected file presence
- subject/study uniqueness
- split leakage/mismatch
- intersection counts between eval labels and embeddings
- headline metric extraction (E2b/E3/E5)
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
        help="Audit output directory. Defaults to <fullscale-root>/audit_postrun.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero if warnings are detected.",
    )
    return parser.parse_args()


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if np.isnan(out):
        return None
    return out


def summarize_manifest(df: pd.DataFrame) -> dict[str, int]:
    out: dict[str, int] = {"n_rows": int(len(df))}
    if "study_id" in df.columns:
        out["n_studies"] = int(df["study_id"].nunique())
    if "subject_id" in df.columns:
        out["n_subjects"] = int(df["subject_id"].nunique())
    return out


def parse_e2b_metrics(path: Path) -> dict[str, Any]:
    data = read_json(path)
    test = data.get("study_metrics", {}).get("test", {})
    return {
        "experiment": "E2b_vision",
        "source": str(path),
        "n_test": test.get("n_rows"),
        "auc": safe_float(test.get("clf_auc")),
        "r2": safe_float(test.get("reg_r2")),
        "mae": safe_float(test.get("reg_mae")),
    }


def parse_e3_metrics(path: Path) -> dict[str, Any]:
    data = read_json(path)
    test = data.get("split_metrics", {}).get("test", {})
    return {
        "experiment": "E3_tabular",
        "source": str(path),
        "n_test": test.get("n_rows"),
        "auc": safe_float(test.get("clf_auc")),
        "r2": safe_float(test.get("reg_r2")),
        "mae": safe_float(test.get("reg_mae")),
        "missing_rate_mean": safe_float(data.get("missing_rate_mean")),
        "n_features": data.get("n_features"),
    }


def parse_e5_metrics(path: Path) -> dict[str, Any]:
    data = read_json(path)
    results = data.get("results", {})
    preferred = "fusion_concat__linear"
    if preferred in results:
        chosen_name = preferred
    elif results:
        chosen_name = max(
            results.keys(),
            key=lambda k: safe_float(results[k].get("test", {}).get("clf_auc")) or -1.0,
        )
    else:
        chosen_name = None

    chosen = results.get(chosen_name, {}) if chosen_name else {}
    test = chosen.get("test", {})

    return {
        "experiment": "E5_fusion",
        "source": str(path),
        "chosen_config": chosen_name,
        "n_test": test.get("n_rows"),
        "auc": safe_float(test.get("clf_auc")),
        "r2": safe_float(test.get("reg_r2")),
        "mae": safe_float(test.get("reg_mae")),
        "n_tabular_features": data.get("n_tabular_features"),
    }


def to_metric_table(metrics: list[dict[str, Any]]) -> str:
    header = "| Experiment | Config | Test n | Test AUC | Test R2 | Test MAE |\n"
    header += "|---|---:|---:|---:|---:|---:|\n"
    rows = []
    for m in metrics:
        rows.append(
            "| {exp} | {cfg} | {n} | {auc} | {r2} | {mae} |".format(
                exp=m.get("experiment", ""),
                cfg=m.get("chosen_config", "-"),
                n=m.get("n_test", "-"),
                auc=(
                    f"{m['auc']:.4f}" if isinstance(m.get("auc"), float) else "-"
                ),
                r2=(
                    f"{m['r2']:.4f}" if isinstance(m.get("r2"), float) else "-"
                ),
                mae=(
                    f"{m['mae']:.4f}" if isinstance(m.get("mae"), float) else "-"
                ),
            )
        )
    return header + "\n".join(rows) + ("\n" if rows else "")


def main() -> int:
    args = parse_args()
    root = args.fullscale_root.resolve()
    out_dir = (args.output_dir or (root / "audit_postrun")).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    required = {
        "all_eligible_studies": root / "manifests" / "all_eligible_studies.csv",
        "structured_measurements": root / "manifests" / "structured_measurements.csv",
        "subject_split_map": root / "manifests" / "subject_split_map_v1.csv",
        "keyframe_stub": root / "manifests" / "keyframe_stub.csv",
        "lvef_manifest": root / "manifests" / "lvef_still_manifest.csv",
        "merged_clip_manifest": root / "merged_clip_embeddings_512" / "clip_embedding_manifest.csv",
        "study_embedding_manifest": root / "study_embeddings_512" / "study_embedding_manifest.csv",
        "e2b_metrics": root / "eval_e2b_vision" / "echoprime_embedding_baseline_metrics.json",
        "e3_metrics": root / "eval_e3_tabular" / "tabular_baseline_metrics.json",
        "e3_leakage_audit": root / "eval_e3_tabular" / "measurement_leakage_audit.json",
        "e5_metrics": root / "eval_e5_fusion" / "fusion_metrics.json",
    }

    missing_files = [name for name, path in required.items() if not path.exists()]
    warnings: list[str] = []
    if missing_files:
        warnings.append(f"Missing required files: {', '.join(missing_files)}")

    # Optional dataframes loaded if present.
    all_eligible = pd.read_csv(required["all_eligible_studies"]) if required["all_eligible_studies"].exists() else pd.DataFrame()
    structured = pd.read_csv(required["structured_measurements"]) if required["structured_measurements"].exists() else pd.DataFrame()
    split_map = pd.read_csv(required["subject_split_map"]) if required["subject_split_map"].exists() else pd.DataFrame()
    keyframe = pd.read_csv(required["keyframe_stub"]) if required["keyframe_stub"].exists() else pd.DataFrame()
    lvef = pd.read_csv(required["lvef_manifest"]) if required["lvef_manifest"].exists() else pd.DataFrame()
    merged_clip = pd.read_csv(required["merged_clip_manifest"]) if required["merged_clip_manifest"].exists() else pd.DataFrame()
    study_emb = pd.read_csv(required["study_embedding_manifest"]) if required["study_embedding_manifest"].exists() else pd.DataFrame()

    # Core counts
    counts: dict[str, Any] = {
        "all_eligible_studies": summarize_manifest(all_eligible),
        "keyframe_stub": summarize_manifest(keyframe),
        "lvef_manifest": summarize_manifest(lvef),
        "merged_clip_manifest": summarize_manifest(merged_clip),
        "study_embedding_manifest": summarize_manifest(study_emb),
    }

    # Structured measurements summary
    if not structured.empty:
        result_numeric = pd.to_numeric(structured.get("result"), errors="coerce")
        counts["structured_measurements"] = {
            "n_rows": int(len(structured)),
            "n_studies": int(structured["study_id"].nunique()) if "study_id" in structured.columns else 0,
            "n_subjects": int(structured["subject_id"].nunique()) if "subject_id" in structured.columns else 0,
            "n_result_parsed": int(result_numeric.notna().sum()),
            "n_unique_measurements": int(structured["measurement"].nunique()) if "measurement" in structured.columns else 0,
        }
        if "test_name" in structured.columns:
            test_name_counts = structured["test_name"].fillna("null").value_counts().to_dict()
            counts["structured_measurements"]["test_name_counts"] = {
                str(k): int(v) for k, v in test_name_counts.items()
            }
    else:
        counts["structured_measurements"] = {}

    # Uniqueness and leakage checks
    checks: dict[str, Any] = {}
    if not split_map.empty and "subject_id" in split_map.columns:
        dup_subjects = int(split_map.duplicated(subset=["subject_id"]).sum())
        checks["split_map_duplicate_subject_rows"] = dup_subjects
        if dup_subjects > 0:
            warnings.append(f"subject_split_map has {dup_subjects} duplicate subject_id rows")
        if "split" in split_map.columns:
            invalid = sorted(set(split_map["split"].dropna().unique()) - {"train", "val", "test"})
            checks["split_map_invalid_splits"] = invalid
            if invalid:
                warnings.append(f"subject_split_map has invalid split labels: {invalid}")

    if not study_emb.empty and "study_id" in study_emb.columns:
        dup_study = int(study_emb.duplicated(subset=["study_id"]).sum())
        checks["study_embedding_duplicate_study_rows"] = dup_study
        if dup_study > 0:
            warnings.append(f"study_embedding_manifest has {dup_study} duplicate study_id rows")

    if not merged_clip.empty and {"study_id", "subject_id"}.issubset(merged_clip.columns):
        subj_per_study = merged_clip.groupby("study_id")["subject_id"].nunique()
        conflicting = int((subj_per_study > 1).sum())
        checks["merged_clip_studies_with_multiple_subject_ids"] = conflicting
        if conflicting > 0:
            warnings.append(
                f"merged clip manifest has {conflicting} study_id values mapped to multiple subject_id values"
            )
        clip_per_study = merged_clip.groupby("study_id").size()
        checks["merged_clip_clips_per_study_stats"] = {
            "min": int(clip_per_study.min()),
            "median": float(clip_per_study.median()),
            "max": int(clip_per_study.max()),
        }

    # Intersections across key manifsets
    intersections: dict[str, Any] = {}
    if "study_id" in lvef.columns:
        lvef_ids = set(lvef["study_id"].astype(int).tolist())
    else:
        lvef_ids = set()
    study_emb_ids = set(study_emb["study_id"].astype(int).tolist()) if "study_id" in study_emb.columns else set()
    keyframe_ids = set(keyframe["study_id"].astype(int).tolist()) if "study_id" in keyframe.columns else set()
    eligible_ids = set(all_eligible["study_id"].astype(int).tolist()) if "study_id" in all_eligible.columns else set()

    intersections["lvef_vs_study_embeddings"] = {
        "n_lvef": len(lvef_ids),
        "n_study_embeddings": len(study_emb_ids),
        "n_intersection": len(lvef_ids & study_emb_ids),
        "n_lvef_missing_in_study_embeddings": len(lvef_ids - study_emb_ids),
        "n_study_embeddings_missing_in_lvef": len(study_emb_ids - lvef_ids),
    }
    intersections["lvef_vs_keyframe_stub"] = {
        "n_lvef": len(lvef_ids),
        "n_keyframe": len(keyframe_ids),
        "n_intersection": len(lvef_ids & keyframe_ids),
        "n_lvef_missing_in_keyframe": len(lvef_ids - keyframe_ids),
    }
    intersections["lvef_vs_all_eligible"] = {
        "n_lvef": len(lvef_ids),
        "n_all_eligible": len(eligible_ids),
        "n_intersection": len(lvef_ids & eligible_ids),
    }

    if intersections["lvef_vs_study_embeddings"]["n_lvef_missing_in_study_embeddings"] > 0:
        warnings.append(
            "Some LVEF-labeled studies are missing from study embedding manifest; "
            "check extraction/merge completeness."
        )

    # Subject-level split consistency check in label manifest
    if not lvef.empty and {"subject_id", "split"}.issubset(lvef.columns):
        per_subject_split = lvef.groupby("subject_id")["split"].nunique()
        n_multi = int((per_subject_split > 1).sum())
        checks["label_manifest_subjects_with_multiple_splits"] = n_multi
        if n_multi > 0:
            warnings.append(f"lvef_still_manifest has {n_multi} subjects assigned to multiple splits")

        if not split_map.empty and {"subject_id", "split"}.issubset(split_map.columns):
            merged_split = (
                lvef[["subject_id", "split"]]
                .drop_duplicates()
                .merge(
                    split_map[["subject_id", "split"]].drop_duplicates(),
                    on="subject_id",
                    how="left",
                    suffixes=("_label", "_splitmap"),
                )
            )
            mismatch = int(
                (
                    merged_split["split_splitmap"].notna()
                    & (merged_split["split_label"] != merged_split["split_splitmap"])
                ).sum()
            )
            missing_map = int(merged_split["split_splitmap"].isna().sum())
            checks["label_vs_split_map_mismatch_subject_rows"] = mismatch
            checks["label_subject_rows_missing_in_split_map"] = missing_map
            if mismatch > 0:
                warnings.append(f"{mismatch} subject split assignments mismatch split map vs label manifest")
            if missing_map > 0:
                warnings.append(f"{missing_map} label manifest subject rows missing from split map")

    # Parse evaluation metrics
    metrics: list[dict[str, Any]] = []
    if required["e2b_metrics"].exists():
        metrics.append(parse_e2b_metrics(required["e2b_metrics"]))
    if required["e3_metrics"].exists():
        metrics.append(parse_e3_metrics(required["e3_metrics"]))
    if required["e5_metrics"].exists():
        metrics.append(parse_e5_metrics(required["e5_metrics"]))

    summary = {
        "fullscale_root": str(root),
        "required_files": {k: str(v) for k, v in required.items()},
        "missing_files": missing_files,
        "counts": counts,
        "checks": checks,
        "intersections": intersections,
        "metrics": metrics,
        "warnings": warnings,
    }

    summary_json = out_dir / "fullscale_audit_summary.json"
    summary_json.write_text(json.dumps(summary, indent=2))

    report_lines = [
        "# Fullscale Output Audit",
        "",
        f"- Root: `{root}`",
        f"- Missing required files: `{len(missing_files)}`",
        f"- Warnings: `{len(warnings)}`",
        "",
        "## Cohort Counts",
        "",
        f"- Eligible studies: `{counts.get('all_eligible_studies', {}).get('n_studies', 0)}`",
        f"- Keyframe stub studies: `{counts.get('keyframe_stub', {}).get('n_studies', 0)}`",
        f"- LVEF eval studies: `{counts.get('lvef_manifest', {}).get('n_studies', 0)}`",
        f"- Study embeddings studies: `{counts.get('study_embedding_manifest', {}).get('n_studies', 0)}`",
        f"- Structured measurement rows: `{counts.get('structured_measurements', {}).get('n_rows', 0)}`",
        f"- Structured parsed-result rows: `{counts.get('structured_measurements', {}).get('n_result_parsed', 0)}`",
        f"- Structured unique measurement names: `{counts.get('structured_measurements', {}).get('n_unique_measurements', 0)}`",
        "",
        "## Intersections",
        "",
        f"- LVEF ∩ study embeddings: `{intersections['lvef_vs_study_embeddings']['n_intersection']}`",
        f"- LVEF missing in study embeddings: `{intersections['lvef_vs_study_embeddings']['n_lvef_missing_in_study_embeddings']}`",
        f"- LVEF ∩ keyframe stub: `{intersections['lvef_vs_keyframe_stub']['n_intersection']}`",
        "",
        "## Eval Metrics",
        "",
        to_metric_table(metrics).rstrip(),
        "",
    ]
    if warnings:
        report_lines.extend(["## Warnings", ""])
        report_lines.extend([f"- {w}" for w in warnings])
        report_lines.append("")
    report_md = out_dir / "fullscale_audit_report.md"
    report_md.write_text("\n".join(report_lines))

    print(f"[written] {summary_json.resolve()}")
    print(f"[written] {report_md.resolve()}")

    if warnings and args.strict:
        print("[error] Strict mode enabled and warnings detected.")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
