"""Model-independent C3/label reconciliation and restricted analysis inputs.

Candidate mapping membership is diagnostic until clinical adjudication. No
model is fitted, no performance is read, and no missing target is imputed.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

from build_multitask_target_panel import normalize_unit, UNIT_TO_CANONICAL
from lvef_revalidation_authority import (C3_COMPLETION, C3_ATTEMPT, C3_PLAN,
    canonical, consume_c3, decode, digest, private_bytes, publish, require)

SOURCE_HASHES = {
    "selected": "920aa8742297dd90c5f125723a425a85201fa7966e926b3191f2c4a57b3d31c1",
    "split": "c5101cea1d76b38c6bb4517edf4b463b338d7505032cfa40bc8f27ca5b97e517",
    "structured": "95fc852457c25ca548d6fa1ae3ec5d2740b99a6aa3d53297a5b424ffc3d27023",
    "mapping": "2f1b6c424c62c39017396130fe074accce06cbb05e1977b4c27144e0f824fd18",
}
SOURCE_NAMES = {"selected": "manifests/all_eligible_studies.csv", "split": "manifests/subject_split_map_v1.csv",
    "structured": "manifests/structured_measurements.csv", "mapping": "measurement_registry_v1/measurement_to_canonical_mapping.csv"}
SPLITS = ("train", "val", "test")
VELOCITY_TARGETS = {"tricuspid_regurgitant_peak_velocity", "av_pk_vel", "sept_e_prime",
                    "mv_peak_a", "mv_peak_e", "lat_e_prime"}
LENGTH_TARGETS = {"left_ventricular_end_systolic_diameter", "left_ventricular_end_diastolic_diameter",
    "la_4ch_length", "ra_length", "la_dimen", "sinus_diam", "inf_lat_thickness", "septal_thickness",
    "lvot_vti", "ascending_aorta_diameter", "rv_diam", "lvot_diam",
    "tricuspid_annular_plane_systolic_excursion", "arch_diam", "ivc_diam"}


def ids(values: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(values, errors="raise")
    require(bool(numeric.notna().all()) and bool(np.isfinite(numeric).all())
            and bool((numeric == np.floor(numeric)).all()), "INPUT_IDENTIFIER_INVALID")
    return numeric.astype("int64").astype(str)


def file_sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def write_private(path: Path, body: bytes) -> dict[str, Any]:
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), "wb") as stream:
        stream.write(body)
        stream.flush()
        os.fsync(stream.fileno())
    require(private_bytes(path) == body, "INPUT_PUBLICATION_CHANGED")
    return {"path": str(path), "sha256": digest(body), "size_bytes": len(body)}


def selected_roster(selected: pd.DataFrame, split: pd.DataFrame) -> pd.DataFrame:
    s = selected[["subject_id", "study_id", "measurement_id"]].copy()
    for key in s:
        s[key] = ids(s[key])
    require(not s.subject_id.duplicated().any() and not s.study_id.duplicated().any(), "INPUT_ONE_STUDY_OWNERSHIP")
    m = split[["subject_id", "split"]].copy()
    m["subject_id"] = ids(m.subject_id)
    require(not m.subject_id.duplicated().any() and set(m.split) == set(SPLITS), "INPUT_SPLIT_MAP_INVALID")
    require(set(m.subject_id) == set(s.subject_id), "INPUT_SPLIT_MEMBERSHIP_MISMATCH")
    return s.merge(m, on="subject_id", validate="one_to_one").sort_values(["subject_id", "study_id"]).reset_index(drop=True)


def selected_measurements(structured: pd.DataFrame, roster: pd.DataFrame) -> pd.DataFrame:
    columns = ["subject_id", "study_id", "measurement_id", "measurement", "result", "unit"]
    values = structured[columns].copy()
    for key in ("subject_id", "study_id", "measurement_id"):
        values[key] = ids(values[key])
    keys = roster[["subject_id", "study_id", "measurement_id"]].rename(columns={"study_id": "selected_study_id"})
    values = values.merge(keys, on=["subject_id", "measurement_id"], validate="many_to_one")
    require(bool((values.study_id == values.selected_study_id).all()), "INPUT_REPORT_STUDY_OWNERSHIP_MISMATCH")
    values = values.drop(columns="selected_study_id")
    values["measurement"] = values.measurement.fillna("").astype(str)
    values["numeric"] = pd.to_numeric(values.result, errors="coerce")
    values.loc[~np.isfinite(values.numeric), "numeric"] = np.nan
    values["unit_norm"] = values.unit.map(normalize_unit)
    conversion = values.unit_norm.map(lambda u: UNIT_TO_CANONICAL.get(u, ("unknown", 1.0)))
    values["value_unit"] = conversion.map(lambda u: u[0])
    values["normalized_value"] = values.numeric * conversion.map(lambda u: u[1])
    values.loc[values.value_unit == "unknown", "normalized_value"] = np.nan
    return values


def candidate_targets(values: pd.DataFrame, mapping: pd.DataFrame, requested: list[str],
                      roster: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Keep ambiguous mappings unresolved instead of the historical lexical fallback."""
    relations = mapping[["measurement", "canonical_measurement"]].dropna().drop_duplicates()
    counts = relations.groupby("measurement").canonical_measurement.nunique()
    ambiguous = set(counts[counts > 1].index)
    unique = relations[~relations.measurement.isin(ambiguous)]
    joined = values.merge(unique, on="measurement", how="left", validate="many_to_one")
    output = roster[["subject_id", "study_id", "split"]].copy()
    metadata = {}
    for target in requested:
        if target == "lvef":
            rows = values[values.measurement == "lvef"].copy()
            rows["target_value"] = rows.numeric
            unit, unit_status = "EF_percentage_points", "SEPARATE_EXACT_NAME_ANALYTICAL_SCALE_NATIVE_UNIT_UNVERIFIED"
        else:
            rows = joined[joined.canonical_measurement == target].copy()
            unit = "cm/s" if target in VELOCITY_TARGETS else "mm" if target in LENGTH_TARGETS else "UNRESOLVED"
            compatible = unit != "UNRESOLVED" and bool((rows.value_unit == unit).any())
            unit_status = "NORMALIZED_UNIT_COMPATIBLE_CLINICAL_DEFINITION_PENDING" if compatible else "UNIT_UNRESOLVED"
            rows["target_value"] = rows.normalized_value.where(rows.value_unit == unit)
        observed = rows[rows.numeric.notna()][["subject_id", "study_id"]].drop_duplicates()
        valid = rows[rows.target_value.notna()]
        grouped = valid.groupby(["subject_id", "study_id"], as_index=False).target_value.median()
        output = output.merge(grouped.rename(columns={"target_value": target}), on=["subject_id", "study_id"], how="left", validate="one_to_one")
        metadata[target] = {"unit": unit, "unit_status": unit_status,
            "observed_selected_before_imaging": len(observed), "valid_units_before_imaging": len(grouped),
            "numeric_source_rows": int(rows.numeric.notna().sum()), "valid_unit_source_rows": len(valid),
            "repeated_valid_rows_aggregated": len(valid) - len(grouped),
            "source_raw_fields": sorted(set(rows.measurement)),
            "valid_unit_raw_fields": sorted(set(valid.measurement)),
            "aggregation_rule": "MEDIAN_OF_VALID_UNIT_NORMALIZED_ROWS_WITHIN_SELECTED_SUBJECT_REPORT_STUDY",
            "source_raw_aggregation_clinically_approved": False,
            "unit_incompatible_numeric_rows_excluded": int((rows.numeric.notna() & rows.target_value.isna()).sum()),
            "ambiguous_raw_mapping_fields_excluded": len(set(relations.loc[relations.canonical_measurement == target, "measurement"]) & ambiguous),
            "clinical_panel_membership_locked": False}
    return output, metadata


def raw_structured_matrix(values: pd.DataFrame, roster: pd.DataFrame,
                          mapping: pd.DataFrame) -> tuple[np.ndarray, list[str], dict[str, str]]:
    """Resolve units from registry metadata, never held-out numeric availability."""
    valid = values.copy()
    # Only the separate exact-name authority permits the historical analytical EF scale.
    exact = valid.measurement == "lvef"
    valid.loc[exact, "normalized_value"] = valid.loc[exact, "numeric"]
    valid.loc[exact, "value_unit"] = "EF_percentage_points"
    registry_units = {}
    for name, group in mapping.groupby("measurement", sort=True):
        observed = {UNIT_TO_CANONICAL.get(normalize_unit(u), ("unknown", 1.0))[0] for u in group.unit}
        registry_units[name] = next(iter(observed)) if len(observed) == 1 and "unknown" not in observed else "UNRESOLVED"
    units = {name: registry_units.get(name, "UNRESOLVED") for name in sorted(set(valid.measurement))}
    if "lvef" in units:
        units["lvef"] = "EF_percentage_points"
    expected = valid.measurement.map(units)
    valid.loc[(expected == "UNRESOLVED") | (valid.value_unit != expected), "normalized_value"] = np.nan
    matrix = valid.groupby(["subject_id", "study_id", "measurement"]).normalized_value.median().unstack("measurement")
    names = sorted(units)
    index = pd.MultiIndex.from_frame(roster[["subject_id", "study_id"]])
    return matrix.reindex(index=index, columns=names).to_numpy(dtype=float), names, units


def row_hash(rows: pd.DataFrame, target: str) -> str:
    require(len(set(rows.split)) == 1, "INPUT_FINGERPRINT_SPLIT_INVALID")
    return digest(canonical({"subject_id": rows.subject_id.tolist(), "study_id": rows.study_id.tolist(),
        "split": str(rows.split.iloc[0]), "target": target, "target_values": rows[target].astype(float).tolist()}))


def prepare(*, source_root: Path, c3_attempt_root: Path, output: Path, config_path: Path) -> dict[str, Any]:
    from lvef_multitask_analysis_modes import load_policy, bind_approved_restricted_path
    policy, _ = load_policy()
    for path in (source_root, c3_attempt_root):
        bind_approved_restricted_path(path, policy=policy, must_exist=True, expect="directory")
    bind_approved_restricted_path(output, policy=policy, must_exist=False, expect="directory")
    require(not output.exists(), "INPUT_OUTPUT_ALREADY_EXISTS")
    final = c3_attempt_root / "cohort_finalization"
    authority = consume_c3(c3_attempt_root / "r7h_auth_successor_v2/completion/receipt.restricted.json",
        final / "full_c3_finalization.aggregate_safe.json", final / "cohort_preservation_receipt.restricted.json")
    sources = {name: source_root / relative for name, relative in SOURCE_NAMES.items()}
    for name, path in sources.items():
        require(not path.is_symlink() and file_sha(path) == SOURCE_HASHES[name], "INPUT_SOURCE_HASH_MISMATCH_" + name.upper())
    selected = pd.read_csv(sources["selected"], low_memory=False)
    roster = selected_roster(selected, pd.read_csv(sources["split"], low_memory=False))
    split_counts = {s: int((roster.split == s).sum()) for s in SPLITS}
    require(len(roster) == 4530 and split_counts == dict(train=3171, val=679, test=680), "INPUT_SELECTED_COUNTS_MISMATCH")
    artifact_names = {"canonical_study_embeddings": "canonical_study_embeddings.restricted.npz",
        "canonical_study_manifest": "canonical_study_manifest.restricted.csv",
        "canonical_study_store": "canonical_study_store.restricted.json", "canonical_clip_index": "canonical_clip_index.restricted.csv"}
    for role, name in artifact_names.items():
        binding = authority["canonical_artifacts"][role]
        path = final / name
        require(path.stat().st_size == binding["size_bytes"] and file_sha(path) == binding["sha256"],
                "INPUT_CANONICAL_ARTIFACT_CHANGED")
    study = pd.read_csv(final / artifact_names["canonical_study_manifest"], dtype={"subject_id": str, "study_id": str})
    require(study.study_idx.tolist() == list(range(4525)) and not study.study_id.duplicated().any()
            and not study.subject_id.duplicated().any(), "INPUT_CANONICAL_STUDY_ORDER_INVALID")
    with np.load(final / artifact_names["canonical_study_embeddings"], allow_pickle=False) as data:
        require(set(data.files) == {"embeddings"}, "INPUT_CANONICAL_ARRAY_SCHEMA")
        vision = np.array(data["embeddings"], copy=True)
    require(vision.shape == (4525, 512) and vision.dtype == np.float32 and bool(np.isfinite(vision).all()), "INPUT_CANONICAL_ARRAY_INVALID")
    from lvef_reconstruction_smoke import array_content_sha256
    require(all(array_content_sha256(vision[i]) == value for i, value in enumerate(study.embedding_sha256)), "INPUT_STUDY_VECTOR_HASH_INVALID")
    pairs = set(zip(roster.subject_id, roster.study_id))
    imaging_pairs = set(zip(study.subject_id, study.study_id))
    require(imaging_pairs <= pairs and len(pairs - imaging_pairs) == 5, "INPUT_IMAGING_MEMBERSHIP_INVALID")
    from lvef_c3_orchestration_core import canonical_json_sha256
    excluded = [{"subject_id": subject, "study_id": study_id}
                for subject, study_id in sorted(pairs - imaging_pairs, key=lambda pair: tuple(map(int, pair)))]
    no_cine_sha = canonical_json_sha256(excluded)
    require(no_cine_sha == authority["prespecified_no_cine_study_set_sha256"], "INPUT_PRESPECIFIED_NO_CINE_SET_MISMATCH")
    clip_counts = {}
    seen_keys, seen_sources = set(), set()
    with (final / artifact_names["canonical_clip_index"]).open() as stream:
        for index, row in enumerate(csv.DictReader(stream)):
            pair = row["subject_id"], row["study_id"]
            require(int(row["clip_idx"]) == index and pair in imaging_pairs
                    and row["clip_key"] not in seen_keys and row["physical_source_key"] not in seen_sources,
                    "INPUT_CLIP_MEMBERSHIP_INVALID")
            seen_keys.add(row["clip_key"]); seen_sources.add(row["physical_source_key"])
            clip_counts[pair] = clip_counts.get(pair, 0) + 1
    require(len(seen_keys) == 184570 and all(clip_counts.get((r.subject_id, r.study_id)) == r.n_clips for r in study.itertuples()), "INPUT_CLIP_COUNTS_INVALID")
    store = decode(private_bytes(final / artifact_names["canonical_study_store"]))
    require(store["status"] == "PASS_CANONICAL_STUDY_EMBEDDING_STORE" and store["attempt_id"] == C3_ATTEMPT
            and store["batch_plan_sha256"] == C3_PLAN and store["exact_pooling_replay_passed"] is True, "INPUT_STUDY_STORE_BINDING_INVALID")
    values = selected_measurements(pd.read_csv(sources["structured"], low_memory=False), roster)
    config = yaml.safe_load(config_path.read_text())
    targets = ["lvef", *config["panels"]["strict"]["candidate_targets"]]
    mapping = pd.read_csv(sources["mapping"], low_memory=False)
    labels, metadata = candidate_targets(values, mapping, targets, roster)
    structured, names, units = raw_structured_matrix(values, roster, mapping)
    from lvef_multitask_clinical_metadata import ALLOWED_TARGETS
    raw_by_canonical = {name: [] for name in ALLOWED_TARGETS}
    for name, group in mapping.dropna(subset=["canonical_measurement", "measurement"]).groupby("canonical_measurement"):
        raw_by_canonical[str(name)] = sorted(set(group.measurement.astype(str)))
    raw_by_canonical["lvef"] = ["lvef"]  # separate authority; never a synthetic mapping row
    labels["source_row_idx"] = np.arange(len(labels))
    labels = labels.merge(study[["subject_id", "study_id", "study_idx"]], on=["subject_id", "study_id"], validate="one_to_one")
    labels = labels.sort_values(["subject_id", "study_id"]).reset_index(drop=True)
    require(len(labels) == 4525, "INPUT_COMMON_IMAGING_COUNT_INVALID")
    exact40 = {}; lvef_counts = {}
    for target in targets:
        counts = {}; fingerprints = {}; binaries = {e: {} for e in ("lvef_lt_40", "lvef_le_40", "lvef_lt_50")}
        for split in SPLITS:
            common = labels[(labels.split == split) & labels[target].notna()]
            counts[split] = len(common)
            fingerprints[split] = row_hash(common, target) if len(common) else None
            if target == "lvef":
                y = common[target].to_numpy()
                for endpoint, binary in {"lvef_lt_40": y < 40, "lvef_le_40": y <= 40, "lvef_lt_50": y < 50}.items():
                    binaries[endpoint][split] = [int(binary.sum()), int((~binary).sum())]
                exact40[split] = int((y == 40).sum()); lvef_counts[split] = len(y)
        train = labels.loc[(labels.split == "train") & labels[target].notna(), target].to_numpy()
        iqr = float(np.subtract(*np.percentile(train, [75, 25]))) if len(train) else None
        counts["total"] = sum(counts.values())
        support = counts["train"] >= 120 and counts["val"] >= 40 and counts["test"] >= 40 and counts["total"] >= 250 and iqr is not None and np.isfinite(iqr) and iqr > 0
        metadata[target].update(counts=counts, training_iqr=iqr, row_fingerprints=fingerprints,
            binary_class_counts=binaries if target == "lvef" else {}, support_floors_passed=bool(support),
            exact_modality_rows_and_labels=True)
    require(lvef_counts == dict(train=1997, val=410, test=426) and exact40 == dict(train=71, val=12, test=20), "INPUT_LVEF_COUNTS_OR_EXACT40_CHANGED")
    output.mkdir(mode=0o700)
    artifacts = {}
    for split in SPLITS:
        rows = labels[labels.split == split].reset_index(drop=True)
        csv_body = rows[["subject_id", "study_id", "split"]].to_csv(index=False).encode()
        buffer = io.BytesIO()
        np.savez_compressed(buffer, vision=vision[rows.study_idx.to_numpy()], structured=structured[rows.source_row_idx.to_numpy()],
            targets=rows[targets].to_numpy(dtype=float), target_names=np.asarray(targets, dtype=str), structured_names=np.asarray(names, dtype=str))
        artifacts[split] = {"rows": write_private(output / (split + ".rows.restricted.csv"), csv_body),
                            "arrays": write_private(output / (split + ".arrays.restricted.npz"), buffer.getvalue())}
    receipt = {"schema_version": 1, "artifact_type": "lvef_revalidation_inputs_v1",
        "status": "PASS_MODEL_INDEPENDENT_INPUTS_PANEL_PENDING", "c3_completion_sha256": C3_COMPLETION,
        "source_checksums": SOURCE_HASHES, "config_sha256": file_sha(config_path),
        "input_adapter_sha256": file_sha(Path(__file__).resolve()),
        "counts": {"selected_studies": 4530, "selected_subjects": 4530, "imaging_eligible": 4525, "no_cine": 5, "clip_embeddings": 184570},
        "prespecified_no_cine_study_set_sha256": no_cine_sha,
        "selected_split_counts": split_counts, "imaging_split_counts": {s: int((labels.split == s).sum()) for s in SPLITS},
        "imaging_subject_roster_sha256": {s: digest(canonical({"subject_ids": labels.loc[labels.split == s, "subject_id"].tolist()})) for s in SPLITS},
        "lvef_common_counts": lvef_counts, "exact40_counts": exact40,
        "targets": metadata, "target_names": targets, "structured_names": names, "structured_units": units,
        "source_raw_fields_by_canonical": raw_by_canonical,
        "split_artifacts": artifacts, "repeated_measurement_aggregation": "MEDIAN_OF_VALID_UNIT_NORMALIZED_ROWS_WITHIN_SELECTED_SUBJECT_REPORT_STUDY",
        "lvef_label_authority": "EXACT_CASE_SENSITIVE_RAW_LVEF_MEDIAN_BY_SUBJECT_AND_MEASUREMENT_ID",
        "lvef_method_composition": "UNKNOWN_NO_DEDICATED_METHOD_FIELD_IN_RETAINED_EXPORT",
        "all_missing_allowed_structured_rows_retained": True, "clinical_panel_locked": False,
        "structured_unit_authority": "COMPLETE_REGISTRY_UNIT_METADATA_NO_HELD_OUT_NUMERIC_AVAILABILITY",
        "fitting_authorized_by_this_receipt": False, "model_fitting_count": 0, "test_performance_access_count": 0,
        "full_c3_preservation_replays": 0}
    publish(output / "inputs.restricted.json", receipt)
    return receipt


def load_modality_rows(input_path: Path, split: str, target: str) -> dict[str, Any]:
    """A test caller must invoke this only inside the engine's released test loader."""
    from lvef_revalidation_analysis import ModalityRows, MODALITIES
    receipt = decode(private_bytes(input_path))
    require(split in SPLITS and target in receipt["target_names"], "INPUT_SCOPE_INVALID")
    artifacts = receipt["split_artifacts"][split]
    bodies = {}
    for role, binding in artifacts.items():
        body = private_bytes(Path(binding["path"]))
        require(digest(body) == binding["sha256"] and len(body) == binding["size_bytes"], "INPUT_SPLIT_ARTIFACT_CHANGED")
        bodies[role] = body
    rows = pd.read_csv(io.BytesIO(bodies["rows"]), dtype=str, keep_default_na=False)
    with np.load(io.BytesIO(bodies["arrays"]), allow_pickle=False) as data:
        require(data["target_names"].tolist() == receipt["target_names"] and data["structured_names"].tolist() == receipt["structured_names"], "INPUT_ARRAY_SCHEMA_CHANGED")
        y = np.array(data["targets"][:, receipt["target_names"].index(target)], copy=True)
        keep = np.isfinite(y)
        vision, structured = np.array(data["vision"][keep], copy=True), np.array(data["structured"][keep], copy=True)
    scoped = rows.loc[keep]
    common = dict(subject_ids=tuple(scoped.subject_id), study_ids=tuple(scoped.study_id), split=split, target=target, target_values=y[keep])
    return {name: ModalityRows(**common, vision=vision.copy() if name != "structured_only" else None,
        structured=structured.copy() if name != "vision_only" else None,
        structured_names=tuple(receipt["structured_names"]) if name != "vision_only" else ()) for name in MODALITIES}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--c3-attempt-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=Path(__file__).resolve().parents[1] / "configs/lvef_multitask_revalidation.yaml")
    args = parser.parse_args()
    try:
        result = prepare(source_root=args.source_root, c3_attempt_root=args.c3_attempt_root, output=args.output_dir, config_path=args.config)
        safe = {key: result[key] for key in ("status", "counts", "selected_split_counts", "imaging_split_counts", "lvef_common_counts", "exact40_counts", "clinical_panel_locked", "model_fitting_count", "test_performance_access_count")}
        safe["candidate_target_support"] = {name: {key: value[key] for key in ("unit", "counts", "support_floors_passed")} for name, value in result["targets"].items()}
        print(json.dumps(safe, sort_keys=True))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "BLOCKED_ANALYSIS_INPUTS", "code": getattr(exc, "code", "INPUT_VALIDATION_FAILED")}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
