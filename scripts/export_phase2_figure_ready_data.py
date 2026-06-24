#!/usr/bin/env python3
"""Export minimal deidentified Phase 2 figure-ready data.

This script is intended to run on SCC against restricted Phase 2 outputs. It
reads patient-level prediction CSVs in place, keeps only held-out test rows and
only columns needed for plotting, shuffles row order, and writes restricted
derived figure-ready CSVs without identifiers or paths.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


RUNS: dict[str, dict[str, str]] = {
    "lvot_all_clips": {
        "target": "lvot_vti",
        "relative_dir": "lvot_vti/all_clips",
        "analysis_label": "all_clips_study_embeddings_stable_v2",
    },
    "tapse_all_clips": {
        "target": "tapse",
        "relative_dir": "tapse/all_clips",
        "analysis_label": "all_clips_study_embeddings_stable_v2",
    },
}

TARGET_DEFAULT_RUNS = {
    "lvot_vti": ["lvot_all_clips"],
    "tapse": ["tapse_all_clips"],
}

LVOT_COLUMNS = [
    "target",
    "analysis_label",
    "split",
    "observed_lvot_vti_cm",
    "predicted_lvot_vti_cm",
    "low_vti_lt_18",
    "low_vti_lt_20",
]

TAPSE_COLUMNS = [
    "target",
    "analysis_label",
    "split",
    "observed_tapse_mm",
    "predicted_tapse_mm",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--export-dir", type=Path, required=True)
    parser.add_argument(
        "--allow-restricted-derived-export",
        action="store_true",
        help="Required acknowledgement before writing restricted derived figure-ready rows.",
    )
    parser.add_argument("--targets", default="lvot_vti,tapse")
    parser.add_argument(
        "--analyses",
        default="primary",
        help="Comma-separated run labels. Default 'primary' exports LVOT/TAPSE all-clips stable-v2 runs.",
    )
    parser.add_argument(
        "--include-echoview-summaries",
        action="store_true",
        default=False,
        help="Reserved for future aggregate-only ECHOVIEW summaries; no row-level ECHOVIEW export is written.",
    )
    parser.add_argument("--random-seed", type=int, default=20260624)
    return parser.parse_args()


def is_restricted_path(path: Path) -> bool:
    return str(path.resolve()).startswith("/restricted/")


def guard_paths(args: argparse.Namespace) -> None:
    if not args.allow_restricted_derived_export:
        raise RuntimeError("Refusing export without --allow-restricted-derived-export.")
    if not is_restricted_path(args.output_root):
        raise RuntimeError(f"--output-root must be a restricted SCC path: {args.output_root}")
    if not is_restricted_path(args.export_dir):
        raise RuntimeError(f"--export-dir must be a restricted SCC path: {args.export_dir}")
    repo = Path.cwd().resolve()
    export_dir = args.export_dir.resolve()
    if export_dir == repo or repo in export_dir.parents:
        raise RuntimeError("Refusing to write restricted derived export inside the git repository.")


def parse_csv_list(text: str) -> list[str]:
    return [part.strip() for part in text.split(",") if part.strip()]


def selected_runs(targets: list[str], analyses: str) -> list[str]:
    if analyses.strip().lower() == "primary":
        runs: list[str] = []
        for target in targets:
            runs.extend(TARGET_DEFAULT_RUNS.get(target, []))
        return runs
    runs = parse_csv_list(analyses)
    unknown = sorted(set(runs) - set(RUNS))
    if unknown:
        raise ValueError(f"Unknown analysis run labels: {unknown}. Valid labels: {sorted(RUNS)}")
    return runs


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_predictions(path: Path, target: str, analysis_label: str) -> pd.DataFrame:
    usecols = ["target", "analysis_label", "split", "target_value", "pred_ridge"]
    if not path.exists():
        raise FileNotFoundError(f"Missing restricted prediction CSV in source run: {path}")
    df = pd.read_csv(path, usecols=lambda col: col in usecols)
    required = set(usecols)
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Prediction CSV missing required plotting columns: {sorted(missing)}")
    df = df[
        df["target"].astype(str).eq(target)
        & df["analysis_label"].astype(str).eq(analysis_label)
        & df["split"].astype(str).str.lower().eq("test")
    ].copy()
    df["target_value"] = pd.to_numeric(df["target_value"], errors="coerce")
    df["pred_ridge"] = pd.to_numeric(df["pred_ridge"], errors="coerce")
    df = df[df["target_value"].notna() & df["pred_ridge"].notna()].copy()
    return df


def extract_aggregate_metrics(run_dir: Path, target: str) -> dict[str, Any]:
    metrics_path = run_dir / "imaging_baseline_metrics.csv"
    ci_path = run_dir / "imaging_baseline_bootstrap_ci.csv"
    out: dict[str, Any] = {}
    if metrics_path.exists():
        metrics = pd.read_csv(metrics_path)
        test = metrics[metrics["split"].astype(str).eq("test")]
        for model in ["null_median", "ridge"]:
            rows = test[test["model"].astype(str).eq(model)]
            if rows.empty:
                continue
            row = rows.iloc[0]
            prefix = "null" if model == "null_median" else "ridge"
            for col in ["mae", "rmse", "r2", "n_studies"]:
                if col in row and pd.notna(row[col]):
                    out[f"{prefix}_{col}"] = float(row[col])
    if ci_path.exists():
        ci = pd.read_csv(ci_path)
        ridge_mae = ci[
            ci["split"].astype(str).eq("test")
            & ci["model"].astype(str).eq("ridge")
            & ci["metric"].astype(str).eq("mae")
        ]
        if not ridge_mae.empty:
            row = ridge_mae.iloc[0]
            out["ridge_mae_ci_low"] = float(row["ci_lower_2_5"])
            out["ridge_mae_ci_high"] = float(row["ci_upper_97_5"])
    out["target"] = target
    out["unit"] = "cm" if target == "lvot_vti" else "mm"
    return out


def export_lvot(df: pd.DataFrame, analysis_label: str, seed: int) -> pd.DataFrame:
    out = pd.DataFrame(
        {
            "target": "lvot_vti",
            "analysis_label": analysis_label,
            "split": "test",
            "observed_lvot_vti_cm": df["target_value"].astype(float),
            "predicted_lvot_vti_cm": df["pred_ridge"].astype(float),
        }
    )
    out["low_vti_lt_18"] = (out["observed_lvot_vti_cm"] < 18.0).astype(int)
    out["low_vti_lt_20"] = (out["observed_lvot_vti_cm"] < 20.0).astype(int)
    return out[LVOT_COLUMNS].sample(frac=1.0, random_state=seed).reset_index(drop=True)


def export_tapse(df: pd.DataFrame, analysis_label: str, seed: int) -> pd.DataFrame:
    out = pd.DataFrame(
        {
            "target": "tapse",
            "analysis_label": analysis_label,
            "split": "test",
            "observed_tapse_mm": df["target_value"].astype(float),
            "predicted_tapse_mm": df["pred_ridge"].astype(float),
        }
    )
    return out[TAPSE_COLUMNS].sample(frac=1.0, random_state=seed).reset_index(drop=True)


def write_readme(path: Path) -> None:
    path.write_text(
        """# Restricted Derived Figure-Ready Export

These CSV files are deidentified but remain restricted derived row-level files.

Rules:

- Store only on SCC or on a secure local device.
- Do not commit to GitHub.
- Do not upload to ChatGPT, Codex, cloud notebooks, shared drives, or cloud-synced folders.
- Do not paste row-level contents into chat or issue trackers.
- Use only for local figure rendering and visual QA.
- Final figures and sanitized captions may be exported for manuscript review only after visual inspection.

The export intentionally excludes subject IDs, study IDs, DICOM paths, image paths, embedding indices, SCC paths, dates, manifests, and raw prediction files.
""",
        encoding="utf-8",
    )


def main() -> int:
    args = parse_args()
    guard_paths(args)
    targets = parse_csv_list(args.targets)
    runs = selected_runs(targets, args.analyses)
    args.export_dir.mkdir(parents=True, exist_ok=True)

    manifest: dict[str, Any] = {
        "export_timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "source_run_labels": runs,
        "random_seed": int(args.random_seed),
        "row_counts": {},
        "allowed_columns": {},
        "sha256_checksums": {},
        "aggregate_metrics": {},
        "excluded": [
            "subject_id",
            "study_id",
            "dicom paths",
            "image paths",
            "embedding indices",
            "SCC paths",
            "dates",
            "manifest metadata",
            "raw imaging_baseline_predictions.csv files",
        ],
        "restriction_notice": (
            "These files are deidentified but remain restricted derived row-level data. "
            "Do not commit, upload, paste, or place in cloud-synced storage."
        ),
    }

    for idx, run_label in enumerate(runs):
        cfg = RUNS[run_label]
        target = cfg["target"]
        if target not in targets:
            continue
        run_dir = args.output_root / cfg["relative_dir"]
        preds = read_predictions(
            run_dir / "imaging_baseline_predictions.csv",
            target=target,
            analysis_label=cfg["analysis_label"],
        )
        if target == "lvot_vti":
            out = export_lvot(preds, cfg["analysis_label"], args.random_seed + idx)
            out_name = "lvot_vti_test_predictions_figure_ready.csv"
        elif target == "tapse":
            out = export_tapse(preds, cfg["analysis_label"], args.random_seed + idx)
            out_name = "tapse_test_predictions_figure_ready.csv"
        else:  # pragma: no cover - selected_runs prevents this
            raise ValueError(f"Unsupported target: {target}")
        out_path = args.export_dir / out_name
        out.to_csv(out_path, index=False)
        manifest["row_counts"][out_name] = int(len(out))
        manifest["allowed_columns"][out_name] = list(out.columns)
        manifest["sha256_checksums"][out_name] = sha256_file(out_path)
        manifest["aggregate_metrics"][run_label] = extract_aggregate_metrics(run_dir, target)

    readme_path = args.export_dir / "README_RESTRICTED_DERIVED_EXPORT.md"
    write_readme(readme_path)
    manifest["sha256_checksums"][readme_path.name] = sha256_file(readme_path)

    manifest_path = args.export_dir / "phase2_figure_ready_export_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    print(
        json.dumps(
            {
                "export_dir": str(args.export_dir),
                "written_files": sorted(path.name for path in args.export_dir.iterdir() if path.is_file()),
                "row_counts": manifest["row_counts"],
                "identifiers_paths_embeddings_metadata_excluded": True,
                "restricted_derived_data": True,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
