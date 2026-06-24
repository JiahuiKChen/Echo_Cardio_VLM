#!/usr/bin/env python3
"""Audit TAPSE and LVOT VTI feasibility before any modeling.

The script produces aggregate denominator, target, threshold, embedding-overlap,
split, and warning outputs. It is safe to run locally with partial inputs and on
SCC with frozen fullscale artifacts. It never trains models.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


THRESHOLDS = [0.70, 0.80, 0.90, 0.95]

TARGETS: dict[str, dict[str, Any]] = {
    "tapse": {
        "raw_measurement": "tapse",
        "canonical": "tricuspid_annular_plane_systolic_excursion",
        "description": "tricuspid annular plane systolic excursion",
        "source_unit_expected": "cm",
        "clinical_unit": "mm",
        "plausible_low": 5.0,
        "plausible_high": 40.0,
        "hard_high": 50.0,
        "primary_policy": "a4c_family",
    },
    "lvot_vti": {
        "raw_measurement": "lvot_vti",
        "canonical": "lvot_vti",
        "description": "left ventricle - lvot velocity time integral",
        "source_unit_expected": "cm",
        "clinical_unit": "cm",
        "plausible_low": 5.0,
        "plausible_high": 35.0,
        "hard_high": 60.0,
        "primary_policy": "a5c_or_other",
    },
}

VIEW_POLICIES: dict[str, dict[str, Any]] = {
    "a4c_family": {
        "target": "tapse",
        "role": "primary",
        "columns": ["prob_a4c", "prob_a4c_lvocc_s", "prob_a4c_laocc"],
    },
    "a4c_family_or_rvinf": {
        "target": "tapse",
        "role": "sensitivity",
        "columns": ["prob_a4c", "prob_a4c_lvocc_s", "prob_a4c_laocc", "prob_rvinf"],
    },
    "a5c": {
        "target": "lvot_vti",
        "role": "sensitivity_a5c_only",
        "columns": ["prob_a5c"],
    },
    "other": {
        "target": "lvot_vti",
        "role": "doppler_sensitive_candidate",
        "columns": ["prob_other"],
    },
    "a5c_or_other": {
        "target": "lvot_vti",
        "role": "primary_doppler_sensitive",
        "columns": ["prob_a5c", "prob_other"],
    },
    "all_clips": {
        "target": "both",
        "role": "comparator",
        "columns": [],
    },
}

SYNONYM_PATTERNS = {
    "tapse": r"tapse|tricuspid.*annular.*plane|annular.*plane.*systolic",
    "lvot_vti": r"lvot.*vti|vti.*lvot|outflow.*velocity.*time|velocity.*time.*lvot",
    "lvot_vti_adjacent": r"\bav[_ ]?vti\b|aortic.*vti|aortic valve.*velocity time integral",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--structured-measurements-csv", type=Path, default=None)
    parser.add_argument("--study-manifest-csv", type=Path, default=None)
    parser.add_argument("--clip-manifest-csv", type=Path, default=None)
    parser.add_argument("--study-embedding-manifest-csv", type=Path, default=None)
    parser.add_argument("--clip-embedding-manifest-csv", type=Path, default=None)
    parser.add_argument("--echoview-joined-manifest-csv", type=Path, default=None)
    parser.add_argument("--subject-split-map-csv", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--thresholds",
        default="0.70,0.80,0.90,0.95",
        help="Comma-separated ECHOVIEW probability thresholds.",
    )
    return parser.parse_args()


def normalize_text(value: Any) -> str:
    if pd.isna(value):
        return ""
    text = str(value).strip().lower()
    text = re.sub(r"[_/]+", " ", text)
    text = re.sub(r"[^a-z0-9%+\-'\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def normalize_unit(value: Any) -> str:
    text = normalize_text(value)
    aliases = {
        "centimeter": "cm",
        "centimeters": "cm",
        "millimeter": "mm",
        "millimeters": "mm",
        "cm": "cm",
        "mm": "mm",
    }
    return aliases.get(text, text or "unknown")


def read_optional(path: Path | None, label: str, warnings: list[str]) -> pd.DataFrame | None:
    if path is None:
        warnings.append(f"{label} not provided; related denominator rows will be marked unavailable.")
        return None
    if not path.exists():
        warnings.append(f"{label} path does not exist: {path}")
        return None
    try:
        return pd.read_csv(path)
    except Exception as exc:  # pragma: no cover - defensive CLI path
        warnings.append(f"Failed to read {label} at {path}: {exc}")
        return None


def path_str(path: Path | None) -> str:
    return str(path) if path is not None else ""


def parse_thresholds(text: str) -> list[float]:
    return [float(x.strip()) for x in text.split(",") if x.strip()]


def required_columns(df: pd.DataFrame, cols: set[str]) -> bool:
    return cols.issubset(df.columns)


def prepare_measurements(df: pd.DataFrame, warnings: list[str]) -> pd.DataFrame:
    required = {"subject_id", "study_id", "measurement", "result"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"structured measurements missing required columns: {sorted(missing)}")
    out = df.copy()
    out["measurement_norm"] = out["measurement"].map(normalize_text)
    out["result_num"] = pd.to_numeric(out["result"], errors="coerce")
    out["unit_norm"] = out["unit"].map(normalize_unit) if "unit" in out.columns else "unknown"
    if "measurement_description" not in out.columns:
        out["measurement_description"] = ""
        warnings.append("structured measurements missing measurement_description column.")
    if "test_type" not in out.columns:
        warnings.append("structured measurements missing test_type column; TTE-only funnel row cannot be verified.")
    return out


def to_clinical_value(target: str, values: pd.Series, units: pd.Series) -> pd.Series:
    unit_norm = units.map(normalize_unit)
    vals = pd.to_numeric(values, errors="coerce")
    if target == "tapse":
        converted = vals.copy()
        converted.loc[unit_norm == "cm"] = vals.loc[unit_norm == "cm"] * 10.0
        converted.loc[unit_norm == "mm"] = vals.loc[unit_norm == "mm"]
        return converted
    if target == "lvot_vti":
        converted = vals.copy()
        converted.loc[unit_norm == "mm"] = vals.loc[unit_norm == "mm"] / 10.0
        converted.loc[unit_norm == "cm"] = vals.loc[unit_norm == "cm"]
        return converted
    return vals


def target_numeric(measures: pd.DataFrame, target: str) -> pd.DataFrame:
    raw = TARGETS[target]["raw_measurement"]
    rows = measures[measures["measurement_norm"] == normalize_text(raw)].copy()
    rows = rows[rows["result_num"].notna()].copy()
    rows["clinical_value"] = to_clinical_value(target, rows["result_num"], rows["unit_norm"])
    return rows


def study_subject_map(rows: pd.DataFrame) -> dict[Any, Any]:
    if rows is None or rows.empty or not {"study_id", "subject_id"}.issubset(rows.columns):
        return {}
    dedup = rows[["study_id", "subject_id"]].dropna().drop_duplicates(subset=["study_id"], keep="first")
    return {str(study_id): subject_id for study_id, subject_id in dedup.itertuples(index=False, name=None)}


def study_set(df: pd.DataFrame | None) -> set[Any]:
    if df is None or "study_id" not in df.columns:
        return set()
    return set(df["study_id"].dropna().astype(str))


def subject_count_for_studies(studies: set[str], mapping: dict[Any, Any]) -> int:
    subjects = {mapping.get(study) or mapping.get(str(study)) for study in studies}
    return len({s for s in subjects if pd.notna(s)})


def clip_count_for_studies(df: pd.DataFrame | None, studies: set[str]) -> int | None:
    if df is None or "study_id" not in df.columns:
        return None
    return int(df[df["study_id"].astype(str).isin(studies)].shape[0])


def pct(num: int | None, denom: int | None) -> float | None:
    if num is None or denom in (None, 0):
        return None
    return round(float(num) / float(denom) * 100.0, 2)


def manuscript_safe(value: bool = True) -> bool:
    return bool(value)


def aggregate_by_study(rows: pd.DataFrame) -> pd.DataFrame:
    if rows.empty:
        return pd.DataFrame(columns=["study_id", "subject_id", "target_median"])
    return (
        rows.groupby(["study_id", "subject_id"], as_index=False)
        .agg(target_median=("clinical_value", "median"), n_values=("clinical_value", "size"))
    )


def target_distribution_rows(measures: pd.DataFrame | None, warnings: list[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if measures is None:
        warnings.append("Cannot compute target distributions without structured measurements.")
        return [], []
    dist_rows: list[dict[str, Any]] = []
    dictionary_rows: list[dict[str, Any]] = []
    for target, cfg in TARGETS.items():
        exact_rows = measures[measures["measurement_norm"] == normalize_text(cfg["raw_measurement"])].copy()
        numeric = target_numeric(measures, target)
        values = numeric["clinical_value"].dropna()
        per_study_counts = numeric.groupby("study_id").size() if not numeric.empty else pd.Series(dtype=int)
        implausible_low = int((values < cfg["plausible_low"]).sum()) if len(values) else 0
        implausible_high = int((values > cfg["plausible_high"]).sum()) if len(values) else 0
        hard_invalid = int(((values <= 0) | (values > cfg["hard_high"])).sum()) if len(values) else 0
        dist_rows.append(
            {
                "target": target,
                "raw_measurement": cfg["raw_measurement"],
                "numeric_rows": int(len(numeric)),
                "numeric_studies": int(numeric["study_id"].nunique()) if not numeric.empty else 0,
                "numeric_subjects": int(numeric["subject_id"].nunique()) if not numeric.empty else 0,
                "rows_any_result": int(exact_rows["result"].notna().sum()) if "result" in exact_rows else 0,
                "rows_all": int(len(exact_rows)),
                "unit_distribution": json.dumps(exact_rows["unit_norm"].value_counts(dropna=False).to_dict()),
                "clinical_unit": cfg["clinical_unit"],
                "min": float(values.min()) if len(values) else None,
                "q25": float(values.quantile(0.25)) if len(values) else None,
                "median": float(values.median()) if len(values) else None,
                "q75": float(values.quantile(0.75)) if len(values) else None,
                "max": float(values.max()) if len(values) else None,
                "iqr": float(values.quantile(0.75) - values.quantile(0.25)) if len(values) else None,
                "studies_with_multiple_numeric_values": int((per_study_counts > 1).sum()),
                "implausible_below_primary_range": implausible_low,
                "implausible_above_primary_range": implausible_high,
                "hard_invalid_or_extreme": hard_invalid,
                "multiple_value_recommendation": "median per study; report duplicates and do not silently discard outliers",
            }
        )
        dictionary_rows.append(
            {
                "target": target,
                "raw_measurement": cfg["raw_measurement"],
                "canonical_measurement": cfg["canonical"],
                "description": cfg["description"],
                "source_unit_expected": cfg["source_unit_expected"],
                "clinical_unit": cfg["clinical_unit"],
                "plausible_primary_range": f"{cfg['plausible_low']}-{cfg['plausible_high']} {cfg['clinical_unit']}",
                "hard_extreme_rule": f"<=0 or >{cfg['hard_high']} {cfg['clinical_unit']}",
                "primary_view_policy": cfg["primary_policy"],
                "leakage_adjacent_fields": "av_vti" if target == "lvot_vti" else "rv_function; s_prime; rv_fac",
            }
        )

    # Suspicious/manual-review fields are dictionary rows, not target rows.
    for label, pattern in SYNONYM_PATTERNS.items():
        matches = measures[
            measures["measurement_norm"].str.contains(pattern, regex=True, na=False)
            | measures["measurement_description"].map(normalize_text).str.contains(pattern, regex=True, na=False)
        ]
        for name in sorted(matches["measurement"].dropna().astype(str).unique()):
            if name not in {cfg["raw_measurement"] for cfg in TARGETS.values()}:
                dictionary_rows.append(
                    {
                        "target": label,
                        "raw_measurement": name,
                        "canonical_measurement": "manual_review",
                        "description": "possible synonym or leakage-adjacent field",
                        "source_unit_expected": "",
                        "clinical_unit": "",
                        "plausible_primary_range": "",
                        "hard_extreme_rule": "",
                        "primary_view_policy": "",
                        "leakage_adjacent_fields": "manual_review",
                    }
                )
    return dist_rows, dictionary_rows


def view_mask(df: pd.DataFrame, policy: str, threshold: float) -> pd.Series:
    cols = [c for c in VIEW_POLICIES[policy]["columns"] if c in df.columns]
    if not cols:
        return pd.Series(False, index=df.index)
    return df[cols].max(axis=1) >= threshold


def echoview_match_mask(df: pd.DataFrame) -> pd.Series:
    if "echoview_matched" in df.columns:
        return df["echoview_matched"].fillna(False).astype(bool)
    prob_cols = [c for c in df.columns if c.startswith("prob_")]
    if prob_cols:
        return df[prob_cols].notna().any(axis=1)
    return pd.Series(False, index=df.index)


def build_threshold_table(
    targets: dict[str, pd.DataFrame],
    echoview: pd.DataFrame | None,
    study_emb: pd.DataFrame | None,
    clip_emb: pd.DataFrame | None,
    thresholds: list[float],
    warnings: list[str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if echoview is None:
        warnings.append("ECHOVIEW joined manifest unavailable; threshold feasibility table will be empty.")
        return rows
    if "study_id" not in echoview.columns:
        warnings.append("ECHOVIEW joined manifest lacks study_id; threshold feasibility table will be empty.")
        return rows

    echoview = echoview.copy()
    echoview["study_id_str"] = echoview["study_id"].astype(str)
    matched = echoview_match_mask(echoview)
    study_emb_set = study_set(study_emb)

    for target, target_rows in targets.items():
        target_agg = aggregate_by_study(target_rows)
        target_studies = set(target_agg["study_id"].astype(str))
        subj_map = study_subject_map(target_agg)
        broad = target_studies & study_emb_set if study_emb_set else target_studies
        policies = [p for p, cfg in VIEW_POLICIES.items() if cfg["target"] in {target, "both"}]
        for policy in policies:
            if policy == "all_clips":
                clip_df = clip_emb if clip_emb is not None else echoview
                study_count = len(broad)
                clip_count = clip_count_for_studies(clip_df, broad)
                rows.append(
                    {
                        "target": target,
                        "view_policy": policy,
                        "policy_role": VIEW_POLICIES[policy]["role"],
                        "threshold": "all",
                        "study_count": study_count,
                        "subject_count": subject_count_for_studies(broad, subj_map),
                        "clip_count": clip_count,
                        "median_clips_per_study": None,
                        "broader_embedding_available_target_n": len(broad),
                        "studies_lost_vs_broader_embedding_n": 0,
                        "percent_retained_vs_broader_embedding_n": pct(study_count, len(broad)),
                        "warnings": "",
                    }
                )
                continue
            missing_cols = [c for c in VIEW_POLICIES[policy]["columns"] if c not in echoview.columns]
            for threshold in thresholds:
                if missing_cols:
                    rows.append(
                        {
                            "target": target,
                            "view_policy": policy,
                            "policy_role": VIEW_POLICIES[policy]["role"],
                            "threshold": threshold,
                            "study_count": 0,
                            "subject_count": 0,
                            "clip_count": 0,
                            "median_clips_per_study": None,
                            "broader_embedding_available_target_n": len(broad),
                            "studies_lost_vs_broader_embedding_n": len(broad),
                            "percent_retained_vs_broader_embedding_n": 0.0 if broad else None,
                            "warnings": f"missing probability columns: {missing_cols}",
                        }
                    )
                    continue
                rows_df = echoview[
                    matched
                    & echoview["study_id_str"].isin(target_studies)
                    & view_mask(echoview, policy, threshold)
                ].copy()
                if broad:
                    rows_df = rows_df[rows_df["study_id_str"].isin(broad)]
                counts = rows_df.groupby("study_id_str").size() if not rows_df.empty else pd.Series(dtype=int)
                study_ids = set(counts.index.astype(str))
                rows.append(
                    {
                        "target": target,
                        "view_policy": policy,
                        "policy_role": VIEW_POLICIES[policy]["role"],
                        "threshold": threshold,
                        "study_count": int(len(study_ids)),
                        "subject_count": subject_count_for_studies(study_ids, subj_map),
                        "clip_count": int(len(rows_df)),
                        "median_clips_per_study": float(counts.median()) if len(counts) else None,
                        "broader_embedding_available_target_n": len(broad),
                        "studies_lost_vs_broader_embedding_n": int(max(len(broad) - len(study_ids), 0)),
                        "percent_retained_vs_broader_embedding_n": pct(len(study_ids), len(broad)),
                        "warnings": "",
                    }
                )
    return rows


def funnel_row(
    target: str,
    step_order: int,
    step: str,
    studies: set[str],
    subj_map: dict[Any, Any],
    full_n: int,
    prev_n: int | None,
    reason: str,
    source: str,
    clip_count: int | None,
) -> dict[str, Any]:
    n = len(studies)
    return {
        "target": target,
        "step_order": step_order,
        "funnel_step": step,
        "study_count": int(n),
        "subject_count": subject_count_for_studies(studies, subj_map),
        "clip_count": clip_count,
        "percent_retained_from_previous_step": pct(n, prev_n),
        "percent_retained_from_full_numeric_target_denominator": pct(n, full_n),
        "reason_for_loss": reason,
        "source_table_or_path": source,
        "manuscript_safe_aggregate": manuscript_safe(),
    }


def build_funnel_rows(
    target: str,
    target_rows: pd.DataFrame,
    paths: dict[str, Path | None],
    study_manifest: pd.DataFrame | None,
    clip_manifest: pd.DataFrame | None,
    study_emb: pd.DataFrame | None,
    clip_emb: pd.DataFrame | None,
    echoview: pd.DataFrame | None,
    thresholds: list[float],
) -> list[dict[str, Any]]:
    target_agg = aggregate_by_study(target_rows)
    subj_map = study_subject_map(target_agg)
    full_studies = set(target_agg["study_id"].astype(str))
    full_n = len(full_studies)
    rows: list[dict[str, Any]] = []
    order = 1

    def add(step: str, studies: set[str], reason: str, source: str, clip_df: pd.DataFrame | None = None, clip_count: int | None = None, prev_n: int | None = None) -> None:
        nonlocal order
        rows.append(
            funnel_row(
                target,
                order,
                step,
                studies,
                subj_map,
                full_n,
                prev_n if prev_n is not None else (rows[-1]["study_count"] if rows else None),
                reason,
                source,
                clip_count if clip_count is not None else clip_count_for_studies(clip_df, studies),
            )
        )
        order += 1

    add(
        "all_structured_measurement_studies_with_numeric_target",
        full_studies,
        "base numeric target denominator",
        path_str(paths.get("structured")),
        prev_n=None,
    )

    if "test_type" in target_rows.columns:
        tte_mask = (
            target_rows["test_type"].fillna("").astype(str).str.lower().str.contains("tte")
            & ~target_rows["test_type"].fillna("").astype(str).str.lower().str.contains("stress|tee|transesophageal")
        )
        tte_studies = set(target_rows.loc[tte_mask, "study_id"].astype(str))
        add("numeric_target_tte_nonstress_nontee_if_available", tte_studies, "non-TTE/stress/TEE removed if tagged", path_str(paths.get("structured")))
    else:
        add("numeric_target_tte_nonstress_nontee_if_available", full_studies, "test_type unavailable; no modality exclusion applied", path_str(paths.get("structured")))

    dicom_source = study_manifest if study_manifest is not None else clip_manifest
    dicom_set = study_set(dicom_source)
    add(
        "numeric_target_in_public_dicom_subset_or_supplied_manifest",
        full_studies & dicom_set if dicom_set else set(),
        "not present in supplied DICOM/study/clip manifest",
        path_str(paths.get("study_manifest") or paths.get("clip_manifest")),
        clip_df=clip_manifest,
    )

    study_emb_set = study_set(study_emb)
    broader = full_studies & study_emb_set if study_emb_set else set()
    add(
        "numeric_target_with_processed_echoprime_study_embeddings",
        broader,
        "no study-level EchoPrime embedding row",
        path_str(paths.get("study_embedding_manifest")),
    )

    clip_emb_set = study_set(clip_emb)
    add(
        "numeric_target_with_clip_level_echoprime_outputs",
        full_studies & clip_emb_set if clip_emb_set else set(),
        "no clip-level EchoPrime output row",
        path_str(paths.get("clip_embedding_manifest")),
        clip_df=clip_emb,
    )

    echoview_studies: set[str] = set()
    if echoview is not None and "study_id" in echoview.columns:
        ev = echoview.copy()
        ev["study_id_str"] = ev["study_id"].astype(str)
        echoview_studies = set(ev.loc[echoview_match_mask(ev), "study_id_str"].dropna())
    add(
        "numeric_target_with_any_echoview_row",
        full_studies & echoview_studies,
        "ECHOVIEW view labels unavailable",
        path_str(paths.get("echoview_joined_manifest")),
        clip_df=echoview,
    )

    primary_policy = TARGETS[target]["primary_policy"]
    if echoview is not None and "study_id" in echoview.columns:
        ev = echoview.copy()
        ev["study_id_str"] = ev["study_id"].astype(str)
        matched = echoview_match_mask(ev)
        for threshold in thresholds:
            pass_rows = ev[
                matched
                & ev["study_id_str"].isin(full_studies)
                & view_mask(ev, primary_policy, threshold)
            ]
            if broader:
                pass_rows = pass_rows[pass_rows["study_id_str"].isin(broader)]
            pass_studies = set(pass_rows["study_id_str"])
            counts = pass_rows.groupby("study_id_str").size() if not pass_rows.empty else pd.Series(dtype=int)
            add(
                f"numeric_target_with_primary_echoview_policy_{primary_policy}_ge_{threshold:.2f}",
                pass_studies,
                f"no high-confidence {primary_policy} ECHOVIEW clip at threshold {threshold:.2f}",
                path_str(paths.get("echoview_joined_manifest")),
                clip_count=int(counts.sum()) if len(counts) else 0,
            )

    add(
        "numeric_target_with_embeddings_but_no_echoview_labels",
        broader - echoview_studies if broader else set(),
        "ECHOVIEW unavailable but study embedding exists",
        f"{path_str(paths.get('study_embedding_manifest'))}; {path_str(paths.get('echoview_joined_manifest'))}",
        prev_n=len(broader) if broader else None,
    )
    add(
        "broader_fullscale_embedding_available_n_not_view_filtered",
        broader,
        "broader future all-clips/weakly-view-aware denominator; not ECHOVIEW-filtered final N",
        path_str(paths.get("study_embedding_manifest")),
        prev_n=full_n,
    )
    return rows


def build_overlap_table(
    targets: dict[str, pd.DataFrame],
    study_emb: pd.DataFrame | None,
    clip_emb: pd.DataFrame | None,
    echoview: pd.DataFrame | None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    study_emb_set = study_set(study_emb)
    clip_emb_set = study_set(clip_emb)
    echoview_set: set[str] = set()
    if echoview is not None and "study_id" in echoview.columns:
        ev = echoview.copy()
        ev["study_id_str"] = ev["study_id"].astype(str)
        echoview_set = set(ev.loc[echoview_match_mask(ev), "study_id_str"])
    for target, target_rows in targets.items():
        agg = aggregate_by_study(target_rows)
        subj_map = study_subject_map(agg)
        tset = set(agg["study_id"].astype(str))
        entries = {
            "target_numeric": tset,
            "target_intersection_study_embeddings": tset & study_emb_set,
            "target_intersection_clip_embeddings": tset & clip_emb_set,
            "target_intersection_echoview": tset & echoview_set,
            "target_intersection_echoview_and_study_embeddings": tset & echoview_set & study_emb_set,
            "target_with_study_embeddings_but_no_echoview": (tset & study_emb_set) - echoview_set,
        }
        for label, studies in entries.items():
            rows.append(
                {
                    "target": target,
                    "overlap": label,
                    "study_count": len(studies),
                    "subject_count": subject_count_for_studies(studies, subj_map),
                    "manuscript_safe_aggregate": True,
                }
            )
    return rows


def build_split_table(
    targets: dict[str, pd.DataFrame],
    split_df: pd.DataFrame | None,
    warnings: list[str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if split_df is None:
        warnings.append("Subject split map unavailable; split integrity table will contain unavailable rows.")
        return rows
    if not {"subject_id", "split"}.issubset(split_df.columns):
        warnings.append("Subject split map missing subject_id/split columns.")
        return rows

    dup_subjects = int(split_df.duplicated(subset=["subject_id"]).sum())
    invalid_splits = sorted(set(split_df["split"].dropna().astype(str)) - {"train", "val", "test"})
    rows.append(
        {
            "target": "all",
            "check": "split_map_duplicate_subject_rows",
            "split": "",
            "study_count": None,
            "subject_count": dup_subjects,
            "status": "ok" if dup_subjects == 0 else "warning",
        }
    )
    rows.append(
        {
            "target": "all",
            "check": "split_map_invalid_split_labels",
            "split": ",".join(invalid_splits),
            "study_count": None,
            "subject_count": len(invalid_splits),
            "status": "ok" if not invalid_splits else "warning",
        }
    )

    split_clean = split_df[["subject_id", "split"]].drop_duplicates()
    for target, target_rows in targets.items():
        agg = aggregate_by_study(target_rows)
        merged = agg.merge(split_clean, on="subject_id", how="left")
        missing = int(merged["split"].isna().sum())
        if missing:
            warnings.append(f"{target}: {missing} target studies missing split assignment.")
        per_subject = merged.dropna(subset=["split"]).groupby("subject_id")["split"].nunique()
        multi = int((per_subject > 1).sum())
        rows.append(
            {
                "target": target,
                "check": "subjects_with_multiple_splits",
                "split": "",
                "study_count": None,
                "subject_count": multi,
                "status": "ok" if multi == 0 else "warning",
            }
        )
        for split, grp in merged.groupby("split", dropna=False):
            split_label = "missing" if pd.isna(split) else str(split)
            rows.append(
                {
                    "target": target,
                    "check": "target_split_counts",
                    "split": split_label,
                    "study_count": int(grp["study_id"].nunique()),
                    "subject_count": int(grp["subject_id"].nunique()),
                    "status": "ok" if split_label != "missing" else "warning",
                }
            )
    return rows


def scc_command_example() -> str:
    return """cd /restricted/project/mimicecho/code/Echo_Cardio_VLM
PY=.venv-echoprime/bin/python
$PY scripts/join_echoview_view_probabilities.py \\
  --echoview-csv /restricted/project/mimicecho/metadata/MIMIC_ECHO_View_Classifications.csv \\
  --manifest-csv outputs/cloud_cohorts/fullscale_all/merged_clip_embeddings_512/clip_embedding_manifest.csv \\
  --output-dir /restricted/project/mimicecho/outputs/tapse_lvot_vti_phase1/echoview_join

$PY scripts/audit_tapse_lvot_vti_feasibility.py \\
  --structured-measurements-csv outputs/cloud_cohorts/fullscale_all/manifests/structured_measurements.csv \\
  --study-manifest-csv outputs/cloud_cohorts/fullscale_all/manifests/all_eligible_studies.csv \\
  --clip-manifest-csv outputs/cloud_cohorts/fullscale_all/merged_clip_embeddings_512/clip_embedding_manifest.csv \\
  --study-embedding-manifest-csv outputs/cloud_cohorts/fullscale_all/study_embeddings_512/study_embedding_manifest.csv \\
  --clip-embedding-manifest-csv outputs/cloud_cohorts/fullscale_all/merged_clip_embeddings_512/clip_embedding_manifest.csv \\
  --echoview-joined-manifest-csv /restricted/project/mimicecho/outputs/tapse_lvot_vti_phase1/echoview_join/echoview_joined_clip_manifest.csv \\
  --subject-split-map-csv outputs/cloud_cohorts/fullscale_all/manifests/subject_split_map_v1.csv \\
  --output-dir /restricted/project/mimicecho/outputs/tapse_lvot_vti_phase1/feasibility"""


def write_empty_outputs(out_dir: Path, warnings_payload: dict[str, Any]) -> None:
    empty_files = [
        "target_dictionary.csv",
        "target_distribution_summary.csv",
        "threshold_feasibility_table.csv",
        "denominator_funnel_tapse.csv",
        "denominator_funnel_lvot_vti.csv",
        "denominator_funnel_combined.csv",
        "split_integrity_table.csv",
        "target_embedding_overlap_table.csv",
    ]
    for name in empty_files:
        pd.DataFrame().to_csv(out_dir / name, index=False)
    (out_dir / "warnings.json").write_text(json.dumps(warnings_payload, indent=2))


def main() -> int:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    warnings: list[str] = []
    thresholds = parse_thresholds(args.thresholds)

    structured = read_optional(args.structured_measurements_csv, "structured measurement export", warnings)
    study_manifest = read_optional(args.study_manifest_csv, "study manifest", warnings)
    clip_manifest = read_optional(args.clip_manifest_csv, "clip manifest", warnings)
    study_emb = read_optional(args.study_embedding_manifest_csv, "study embedding manifest", warnings)
    clip_emb = read_optional(args.clip_embedding_manifest_csv, "clip embedding manifest", warnings)
    echoview = read_optional(args.echoview_joined_manifest_csv, "ECHOVIEW joined manifest", warnings)
    split_df = read_optional(args.subject_split_map_csv, "subject split map", warnings)

    paths = {
        "structured": args.structured_measurements_csv,
        "study_manifest": args.study_manifest_csv,
        "clip_manifest": args.clip_manifest_csv,
        "study_embedding_manifest": args.study_embedding_manifest_csv,
        "clip_embedding_manifest": args.clip_embedding_manifest_csv,
        "echoview_joined_manifest": args.echoview_joined_manifest_csv,
        "subject_split_map": args.subject_split_map_csv,
    }

    if structured is None:
        payload = {
            "warnings": warnings,
            "blocked": True,
            "reason": "Structured measurements are required for TAPSE/LVOT VTI feasibility.",
            "example_scc_commands": scc_command_example(),
        }
        write_empty_outputs(args.output_dir, payload)
        print(json.dumps(payload, indent=2))
        return 0

    measures = prepare_measurements(structured, warnings)
    targets = {target: target_numeric(measures, target) for target in TARGETS}

    dist_rows, dictionary_rows = target_distribution_rows(measures, warnings)
    threshold_rows = build_threshold_table(targets, echoview, study_emb, clip_emb, thresholds, warnings)
    overlap_rows = build_overlap_table(targets, study_emb, clip_emb, echoview)
    split_rows = build_split_table(targets, split_df, warnings)

    funnel_rows: list[dict[str, Any]] = []
    for target, target_rows in targets.items():
        per_target = build_funnel_rows(
            target,
            target_rows,
            paths,
            study_manifest,
            clip_manifest,
            study_emb,
            clip_emb,
            echoview,
            thresholds,
        )
        funnel_rows.extend(per_target)
        pd.DataFrame(per_target).to_csv(args.output_dir / f"denominator_funnel_{target}.csv", index=False)

    outputs = {
        "target_dictionary": args.output_dir / "target_dictionary.csv",
        "target_distribution_summary": args.output_dir / "target_distribution_summary.csv",
        "threshold_feasibility_table": args.output_dir / "threshold_feasibility_table.csv",
        "denominator_funnel_combined": args.output_dir / "denominator_funnel_combined.csv",
        "split_integrity_table": args.output_dir / "split_integrity_table.csv",
        "target_embedding_overlap_table": args.output_dir / "target_embedding_overlap_table.csv",
        "warnings": args.output_dir / "warnings.json",
    }
    pd.DataFrame(dictionary_rows).to_csv(outputs["target_dictionary"], index=False)
    pd.DataFrame(dist_rows).to_csv(outputs["target_distribution_summary"], index=False)
    pd.DataFrame(threshold_rows).to_csv(outputs["threshold_feasibility_table"], index=False)
    pd.DataFrame(funnel_rows).to_csv(outputs["denominator_funnel_combined"], index=False)
    pd.DataFrame(split_rows).to_csv(outputs["split_integrity_table"], index=False)
    pd.DataFrame(overlap_rows).to_csv(outputs["target_embedding_overlap_table"], index=False)

    blocked = bool(study_emb is None or echoview is None or split_df is None)
    payload = {
        "warnings": warnings,
        "blocked_for_modeling": blocked,
        "blocked_reason": (
            "One or more Phase 1 inputs are missing; run on SCC with frozen fullscale artifacts before modeling."
            if blocked
            else ""
        ),
        "thresholds": thresholds,
        "outputs": {k: str(v) for k, v in outputs.items()},
        "patient_level_outputs_written": False,
        "manuscript_safe_aggregate_outputs": True,
        "example_scc_commands": scc_command_example() if blocked else "",
    }
    outputs["warnings"].write_text(json.dumps(payload, indent=2))

    print(json.dumps(payload, indent=2))
    for path in outputs.values():
        print(f"[written] {path.resolve()}")
    print(f"[written] {(args.output_dir / 'denominator_funnel_tapse.csv').resolve()}")
    print(f"[written] {(args.output_dir / 'denominator_funnel_lvot_vti.csv').resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
