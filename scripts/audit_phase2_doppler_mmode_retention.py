#!/usr/bin/env python3
"""Aggregate Doppler/M-mode retention audit for Phase 2 embedding provenance.

This script reads existing DICOM audit and clip/embedding manifest metadata and
writes aggregate counts only. It does not open pixel data and does not export
row-level DICOM metadata.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

import pandas as pd


DEFAULT_FULLSCALE_ROOT = Path("outputs/cloud_cohorts/fullscale_all")

KEYWORD_PATTERNS: dict[str, list[str]] = {
    "spectral_doppler": [
        r"\bspectral\b",
        r"\bpw\b",
        r"\bpulsed\b",
        r"\bcw\b",
        r"\bcontinuous wave\b",
        r"\bdoppler.*wave\b",
    ],
    "color_doppler": [r"\bcolor\b", r"\bcfd\b"],
    "doppler_any": [r"\bdoppler\b", r"\bpw\b", r"\bcw\b"],
    "m_mode": [r"\bm.?mode\b", r"\bmmode\b", r"\btm\b"],
    "two_d_or_cine": [r"\b2d\b", r"\bcine\b", r"\bb.?mode\b"],
}

POSSIBLE_KEYWORD_COLUMNS = [
    "view_name",
    "series_description",
    "study_description",
    "protocol_name",
    "requested_procedure_description",
    "performed_procedure_step_description",
    "image_type",
    "sequence_name",
    "sop_class_uid",
    "manufacturer",
    "manufacturer_model_name",
    "photometric_interpretation",
    "dicom_filepath",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=None, help="Phase 2 restricted output root.")
    parser.add_argument("--fullscale-root", type=Path, default=DEFAULT_FULLSCALE_ROOT)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--dicom-audit-csv", type=Path, nargs="*", default=None)
    parser.add_argument("--cine-candidates-csv", type=Path, nargs="*", default=None)
    parser.add_argument("--extraction-manifest-csv", type=Path, nargs="*", default=None)
    parser.add_argument(
        "--clip-embedding-manifest-csv",
        type=Path,
        default=None,
        help="Merged clip embedding manifest. Defaults to <fullscale-root>/merged_clip_embeddings_512/clip_embedding_manifest.csv.",
    )
    parser.add_argument(
        "--study-embedding-manifest-csv",
        type=Path,
        default=None,
        help="Study embedding manifest. Defaults to <fullscale-root>/study_embeddings_512/study_embedding_manifest.csv.",
    )
    parser.add_argument(
        "--phase2-output-root",
        type=Path,
        default=None,
        help="Optional Phase 2 output root for aggregate target summaries.",
    )
    parser.add_argument("--aggregate-only", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--allow-repo-output-for-testing", action="store_true")
    return parser.parse_args()


def inside_current_worktree(path: Path) -> bool:
    resolved = path.resolve()
    cwd = Path.cwd().resolve()
    return resolved == cwd or cwd in resolved.parents


def resolve_output_dir(args: argparse.Namespace) -> Path:
    if args.output_dir is not None:
        out = args.output_dir
    elif args.output_root is not None:
        out = args.output_root / "review_packets" / "phase2_doppler_mmode_retention_audit"
    else:
        out = args.fullscale_root / "audit_phase2_doppler_mmode_retention"
    if inside_current_worktree(out) and not args.allow_repo_output_for_testing:
        raise RuntimeError(
            f"Refusing to write audit outputs inside the git worktree: {out}. "
            "Use a restricted SCC output directory or --allow-repo-output-for-testing for synthetic tests."
        )
    return out


def discover_many(explicit: list[Path] | None, pattern: str) -> list[Path]:
    if explicit:
        return [p for p in explicit if p.exists()]
    return sorted(Path().glob(pattern))


def read_many(paths: list[Path], source_label: str, warnings: list[dict[str, Any]]) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for path in paths:
        if not path.exists():
            warnings.append({"source": source_label, "path": str(path), "warning": "missing_file"})
            continue
        try:
            df = pd.read_csv(path)
        except pd.errors.EmptyDataError:
            warnings.append({"source": source_label, "path": str(path), "warning": "empty_csv"})
            continue
        df["_source_file"] = str(path)
        frames.append(df)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True, sort=False)


def read_one(path: Path | None, warnings: list[dict[str, Any]], label: str) -> pd.DataFrame:
    if path is None or not path.exists():
        if path is not None:
            warnings.append({"source": label, "path": str(path), "warning": "missing_file"})
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except pd.errors.EmptyDataError:
        warnings.append({"source": label, "path": str(path), "warning": "empty_csv"})
        return pd.DataFrame()


def bool_count(df: pd.DataFrame, col: str, value: bool = True) -> int | None:
    if df.empty or col not in df.columns:
        return None
    return int(df[col].fillna(False).astype(bool).eq(value).sum())


def nunique(df: pd.DataFrame, col: str) -> int | None:
    if df.empty or col not in df.columns:
        return None
    return int(df[col].nunique(dropna=True))


def keyword_text(df: pd.DataFrame) -> tuple[pd.Series, list[str]]:
    cols = [col for col in POSSIBLE_KEYWORD_COLUMNS if col in df.columns]
    if not cols or df.empty:
        return pd.Series([""] * len(df), index=df.index, dtype="object"), cols
    text = df[cols].fillna("").astype(str).agg(" ".join, axis=1).str.lower()
    return text, cols


def category_counts(df: pd.DataFrame, stage: str) -> tuple[list[dict[str, Any]], list[str]]:
    if df.empty:
        return [], []
    text, cols = keyword_text(df)
    rows: list[dict[str, Any]] = []
    any_known = pd.Series(False, index=df.index)
    for category, patterns in KEYWORD_PATTERNS.items():
        mask = pd.Series(False, index=df.index)
        for pattern in patterns:
            mask |= text.str.contains(pattern, regex=True, na=False)
        any_known |= mask
        rows.append(
            {
                "stage": stage,
                "category": category,
                "n_rows": int(mask.sum()),
                "n_studies": int(df.loc[mask, "study_id"].nunique()) if "study_id" in df.columns else None,
                "n_subjects": int(df.loc[mask, "subject_id"].nunique()) if "subject_id" in df.columns else None,
                "keyword_columns_used": ";".join(cols),
            }
        )
    rows.append(
        {
            "stage": stage,
            "category": "keyword_unknown_or_unmatched",
            "n_rows": int((~any_known).sum()),
            "n_studies": int(df.loc[~any_known, "study_id"].nunique()) if "study_id" in df.columns else None,
            "n_subjects": int(df.loc[~any_known, "subject_id"].nunique()) if "subject_id" in df.columns else None,
            "keyword_columns_used": ";".join(cols),
        }
    )
    return rows, cols


def stage_counts(
    dicom_audit: pd.DataFrame,
    cine_candidates: pd.DataFrame,
    extraction: pd.DataFrame,
    clip_embeddings: pd.DataFrame,
    study_embeddings: pd.DataFrame,
) -> list[dict[str, Any]]:
    extracted_ok = bool_count(extraction, "write_ok", True)
    embedded_ok = bool_count(clip_embeddings, "write_ok", True)
    return [
        {
            "stage": "downloaded_dicom_audit_rows",
            "n_rows": int(len(dicom_audit)),
            "n_studies": nunique(dicom_audit, "study_id"),
            "n_subjects": nunique(dicom_audit, "subject_id"),
        },
        {
            "stage": "readable_dicoms",
            "n_rows": bool_count(dicom_audit, "read_ok", True),
            "n_studies": nunique(dicom_audit[dicom_audit["read_ok"].fillna(False)] if "read_ok" in dicom_audit else pd.DataFrame(), "study_id"),
            "n_subjects": nunique(dicom_audit[dicom_audit["read_ok"].fillna(False)] if "read_ok" in dicom_audit else pd.DataFrame(), "subject_id"),
        },
        {
            "stage": "single_frame_or_still_dicoms",
            "n_rows": int((dicom_audit["read_ok"].fillna(False) & ~dicom_audit["is_multiframe"].fillna(False)).sum())
            if {"read_ok", "is_multiframe"}.issubset(dicom_audit.columns)
            else None,
            "n_studies": None,
            "n_subjects": None,
        },
        {
            "stage": "multiframe_candidates",
            "n_rows": int(len(cine_candidates)) if not cine_candidates.empty else bool_count(dicom_audit, "is_multiframe", True),
            "n_studies": nunique(cine_candidates, "study_id"),
            "n_subjects": nunique(cine_candidates, "subject_id"),
        },
        {
            "stage": "successfully_extracted_clips",
            "n_rows": extracted_ok,
            "n_studies": nunique(extraction[extraction["write_ok"].fillna(False)] if "write_ok" in extraction else pd.DataFrame(), "study_id"),
            "n_subjects": nunique(extraction[extraction["write_ok"].fillna(False)] if "write_ok" in extraction else pd.DataFrame(), "subject_id"),
        },
        {
            "stage": "successfully_embedded_clips",
            "n_rows": embedded_ok if embedded_ok is not None else int(len(clip_embeddings)) if not clip_embeddings.empty else None,
            "n_studies": nunique(clip_embeddings[clip_embeddings["write_ok"].fillna(False)] if "write_ok" in clip_embeddings else clip_embeddings, "study_id"),
            "n_subjects": nunique(clip_embeddings[clip_embeddings["write_ok"].fillna(False)] if "write_ok" in clip_embeddings else clip_embeddings, "subject_id"),
        },
        {
            "stage": "study_embeddings",
            "n_rows": int(len(study_embeddings)) if not study_embeddings.empty else None,
            "n_studies": nunique(study_embeddings, "study_id"),
            "n_subjects": nunique(study_embeddings, "subject_id"),
        },
    ]


def load_phase2_target_summaries(root: Path | None, warnings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if root is None or not root.exists():
        return []
    runs = {
        "lvot_vti_all_clips": root / "lvot_vti" / "all_clips" / "imaging_baseline_summary.json",
        "lvot_vti_exclude_hard": root / "lvot_vti" / "all_clips_exclude_hard_extremes" / "imaging_baseline_summary.json",
        "tapse_all_clips": root / "tapse" / "all_clips" / "imaging_baseline_summary.json",
        "tapse_exclude_hard": root / "tapse" / "all_clips_exclude_hard_extremes" / "imaging_baseline_summary.json",
    }
    rows: list[dict[str, Any]] = []
    for label, path in runs.items():
        if not path.exists():
            warnings.append({"source": "phase2_summary", "run": label, "path": str(path), "warning": "missing_file"})
            continue
        data = json.loads(path.read_text())
        target_summary = (data.get("target_summaries") or [{}])[0]
        split_counts = target_summary.get("split_counts") or {}
        rows.append(
            {
                "run_label": label,
                "target": data.get("target"),
                "analysis_label": data.get("analysis_label"),
                "joined_target_embedding_studies": target_summary.get("joined_target_embedding_studies"),
                "joined_target_embedding_subjects": target_summary.get("joined_target_embedding_subjects"),
                "train_n": split_counts.get("train"),
                "val_n": split_counts.get("val"),
                "test_n": split_counts.get("test"),
                "note": "Target-specific Doppler/M-mode category counts require a restricted row-level join and are not inferred here.",
            }
        )
    return rows


def markdown_table(df: pd.DataFrame) -> str:
    if df.empty:
        return ""
    cols = list(df.columns)
    lines = [
        "| " + " | ".join(cols) + " |",
        "| " + " | ".join(["---"] * len(cols)) + " |",
    ]
    for _, row in df.iterrows():
        values = []
        for col in cols:
            value = row.get(col)
            if pd.isna(value):
                values.append("")
            else:
                values.append(str(value).replace("|", "/"))
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


def write_markdown(path: Path, stage_df: pd.DataFrame, category_df: pd.DataFrame, warnings: list[dict[str, Any]]) -> None:
    lines: list[str] = []
    lines.append("# Phase 2 Doppler/M-Mode Retention Audit Summary")
    lines.append("")
    lines.append("This aggregate-only audit summarizes existing DICOM audit and clip/embedding manifests. It does not open pixel data or export row-level DICOM metadata.")
    lines.append("")
    lines.append("## Stage Counts")
    lines.append("")
    lines.append(markdown_table(stage_df))
    lines.append("")
    lines.append("## Keyword Category Counts")
    lines.append("")
    if category_df.empty:
        lines.append("No keyword category counts were available.")
    else:
        lines.append(markdown_table(category_df))
    lines.append("")
    lines.append("## Caveats")
    lines.append("")
    lines.append("- DICOM metadata fields are vendor-specific and may be incomplete.")
    lines.append("- Keyword categories are screening labels, not adjudicated view or acquisition-type labels.")
    lines.append("- Absence of a keyword match should not be interpreted as absence of Doppler or M-mode content.")
    lines.append("- Target-specific category counts require a restricted row-level join between target cohorts and clip metadata.")
    if warnings:
        lines.append("")
        lines.append("## Warnings")
        lines.append("")
        for warning in warnings:
            lines.append(f"- {warning}")
    path.write_text("\n".join(lines) + "\n")


def main() -> int:
    args = parse_args()
    if not args.aggregate_only:
        raise RuntimeError("This script supports aggregate-only outputs only.")
    output_dir = resolve_output_dir(args)
    warnings: list[dict[str, Any]] = []

    fullscale_root = args.fullscale_root
    dicom_paths = args.dicom_audit_csv or sorted(fullscale_root.glob("batches/*_audit/dicom_audit.csv"))
    cine_paths = args.cine_candidates_csv or sorted(fullscale_root.glob("batches/*_audit/cine_candidates.csv"))
    extraction_paths = args.extraction_manifest_csv or sorted(fullscale_root.glob("batches/*_extraction_manifest.csv"))
    clip_manifest_path = args.clip_embedding_manifest_csv or (
        fullscale_root / "merged_clip_embeddings_512" / "clip_embedding_manifest.csv"
    )
    study_manifest_path = args.study_embedding_manifest_csv or (
        fullscale_root / "study_embeddings_512" / "study_embedding_manifest.csv"
    )
    phase2_root = args.phase2_output_root or args.output_root

    dicom_audit = read_many(dicom_paths, "dicom_audit", warnings)
    cine_candidates = read_many(cine_paths, "cine_candidates", warnings)
    extraction = read_many(extraction_paths, "extraction_manifest", warnings)
    clip_embeddings = read_one(clip_manifest_path, warnings, "clip_embedding_manifest")
    study_embeddings = read_one(study_manifest_path, warnings, "study_embedding_manifest")

    for label, paths in [
        ("dicom_audit", dicom_paths),
        ("cine_candidates", cine_paths),
        ("extraction_manifest", extraction_paths),
    ]:
        if not paths:
            warnings.append({"source": label, "warning": "no_files_discovered"})

    stage_rows = stage_counts(dicom_audit, cine_candidates, extraction, clip_embeddings, study_embeddings)
    category_rows: list[dict[str, Any]] = []
    keyword_columns: dict[str, list[str]] = {}
    for stage, df in [
        ("dicom_audit", dicom_audit),
        ("cine_candidates", cine_candidates),
        ("extraction_manifest", extraction),
        ("clip_embedding_manifest", clip_embeddings),
    ]:
        rows, cols = category_counts(df, stage)
        category_rows.extend(rows)
        keyword_columns[stage] = cols
        if not cols:
            warnings.append(
                {
                    "source": stage,
                    "warning": "no_keyword_metadata_columns_available",
                    "note": "Doppler/M-mode category counts will be unknown or filename-only.",
                }
            )

    target_rows = load_phase2_target_summaries(phase2_root, warnings)

    output_dir.mkdir(parents=True, exist_ok=True)
    stage_path = output_dir / "phase2_doppler_mmode_stage_counts.csv"
    category_path = output_dir / "phase2_doppler_mmode_keyword_counts.csv"
    target_path = output_dir / "phase2_doppler_mmode_target_summary_counts.csv"
    summary_path = output_dir / "phase2_doppler_mmode_retention_summary.json"
    warnings_path = output_dir / "phase2_doppler_mmode_retention_warnings.json"
    markdown_path = output_dir / "phase2_doppler_mmode_retention_summary.md"

    stage_df = pd.DataFrame(stage_rows)
    category_df = pd.DataFrame(category_rows)
    target_df = pd.DataFrame(target_rows)
    stage_df.to_csv(stage_path, index=False)
    category_df.to_csv(category_path, index=False)
    target_df.to_csv(target_path, index=False)

    summary = {
        "fullscale_root": str(fullscale_root),
        "output_dir": str(output_dir),
        "dicom_audit_files": [str(path) for path in dicom_paths],
        "cine_candidate_files": [str(path) for path in cine_paths],
        "extraction_manifest_files": [str(path) for path in extraction_paths],
        "clip_embedding_manifest_csv": str(clip_manifest_path),
        "study_embedding_manifest_csv": str(study_manifest_path),
        "phase2_output_root": str(phase2_root) if phase2_root else "",
        "keyword_columns_used": keyword_columns,
        "aggregate_only": True,
        "pixel_data_opened": False,
        "row_level_outputs_written": False,
        "caveat": (
            "DICOM metadata may be incomplete or vendor-specific; keyword categories are aggregate screening labels, "
            "not adjudicated acquisition-type labels."
        ),
        "outputs": {
            "stage_counts": str(stage_path),
            "keyword_counts": str(category_path),
            "target_summary_counts": str(target_path),
            "summary": str(summary_path),
            "warnings": str(warnings_path),
            "markdown": str(markdown_path),
        },
    }
    summary_path.write_text(json.dumps(summary, indent=2))
    warnings_path.write_text(json.dumps({"warnings": warnings}, indent=2))
    write_markdown(markdown_path, stage_df, category_df, warnings)

    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
