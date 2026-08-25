"""Aggregate-only reconstruction of the JDIM analysis cohort."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

import pandas as pd

from run_tapse_lvot_vti_imaging_baseline import (
    TARGETS,
    extract_target,
    load_splits,
    normalize_text,
    normalize_unit,
)

from . import PROTOCOL_VERSION
from .safety import (
    BLOCKED_LINEAGE,
    Tier1BlockedError,
    assert_export_safe_frame,
    require_columns,
    require_restricted_destination,
    safe_file_record,
    sanitize_for_safe_manifest,
    schema_hash,
    sha256_file,
    write_json,
    write_safe_csv,
)


VALID_SPLITS = ("train", "val", "test")
TARGET_NAMES = ("lvot_vti", "tapse")
OUTSIDE_UNIVERSE_POLICIES = {"canonical_only", "declared_legacy_scope"}


@dataclass
class InvariantReport:
    checks: list[dict[str, Any]] = field(default_factory=list)

    def add(
        self,
        name: str,
        passed: bool,
        observed: Any = None,
        expected: Any = None,
        detail: str = "",
    ) -> None:
        self.checks.append(
            {
                "check": name,
                "passed": bool(passed),
                "observed": observed,
                "expected": expected,
                "detail": detail,
            }
        )

    @property
    def passed(self) -> bool:
        return all(check["passed"] for check in self.checks)

    def payload(self) -> dict[str, Any]:
        return {
            "status": "ok" if self.passed else BLOCKED_LINEAGE,
            "n_checks": len(self.checks),
            "n_failed": sum(not check["passed"] for check in self.checks),
            "checks": self.checks,
        }


@dataclass
class CohortFlowInputs:
    source_studies: Path
    eligible_studies: Path
    expected_records: Sequence[Path]
    dicom_audits: Sequence[Path]
    extraction_manifests: Sequence[Path]
    embedding_batches: Mapping[str, Path]
    final_study_embeddings: Path
    structured_measurements: Path
    split_map: Path
    canonical_summaries: Mapping[str, Path]
    lineage_metadata: Path


@dataclass
class CohortFlowResult:
    summary: dict[str, Any]
    stages: pd.DataFrame
    target_splits: pd.DataFrame
    label_multiplicity: pd.DataFrame
    subject_multiplicity: pd.DataFrame
    batch_reconciliation: pd.DataFrame
    invariants: dict[str, Any]
    provenance: dict[str, Any]
    restricted_reconciliation: pd.DataFrame


@dataclass
class EmbeddingBatchResult:
    merged: pd.DataFrame
    canonical_clips: pd.DataFrame
    declared_outside_clips: pd.DataFrame
    stages: list[dict[str, Any]]
    canonical_studies: set[str]
    declared_outside_studies: set[str]
    undeclared_outside_studies: set[str]
    reconciliation: pd.DataFrame


def validate_cohort_input_schemas(inputs: CohortFlowInputs) -> dict[str, Any]:
    """Validate headers, lineage metadata, and pinned hashes without row analysis."""

    report = InvariantReport()
    if not inputs.lineage_metadata.exists():
        raise Tier1BlockedError(BLOCKED_LINEAGE, "missing lineage metadata JSON")
    metadata = json.loads(inputs.lineage_metadata.read_text(encoding="utf-8"))
    validate_lineage_metadata(metadata, list(inputs.embedding_batches), inputs.split_map, report)

    roles: list[tuple[str, Path, Sequence[str], bool]] = [
        ("source study universe", inputs.source_studies, ("study_id", "subject_id"), False),
        ("eligible study universe", inputs.eligible_studies, ("study_id", "subject_id"), False),
        ("final study embedding manifest", inputs.final_study_embeddings, ("study_id", "subject_id"), False),
        (
            "structured measurements",
            inputs.structured_measurements,
            ("study_id", "subject_id", "measurement", "result"),
            False,
        ),
        ("frozen split map", inputs.split_map, ("subject_id", "split"), False),
    ]
    roles.extend(
        (f"expected records {index}", path, ("study_id", "subject_id", "dicom_filepath"), False)
        for index, path in enumerate(inputs.expected_records)
    )
    roles.extend(
        (f"DICOM audit {index}", path, ("study_id", "subject_id", "read_ok", "is_multiframe"), True)
        for index, path in enumerate(inputs.dicom_audits)
    )
    roles.extend(
        (f"extraction manifest {index}", path, ("study_id", "subject_id", "write_ok"), True)
        for index, path in enumerate(inputs.extraction_manifests)
    )
    roles.extend(
        (f"embedding batch {name}", path, ("study_id", "subject_id"), True)
        for name, path in inputs.embedding_batches.items()
    )
    checked: list[str] = []
    for label, path, required, needs_record_key in roles:
        if not path.exists():
            raise Tier1BlockedError(BLOCKED_LINEAGE, f"missing {label}")
        header = pd.read_csv(path, nrows=0)
        require_columns(header, required, label)
        if needs_record_key and not set(header.columns).intersection(
            {"dicom_filepath", "sop_instance_uid", "clip_path", "npz_path", "embedding_idx"}
        ):
            raise Tier1BlockedError(BLOCKED_LINEAGE, f"{label} lacks a deduplication record key")
        checked.append(label)
    for target in TARGET_NAMES:
        _load_canonical_summary(inputs.canonical_summaries[target], target)
        checked.append(f"canonical summary {target}")
    return {
        "status": "ok",
        "mode": "schema_only",
        "protocol_version": PROTOCOL_VERSION,
        "mimic_iv_echo_release": metadata["mimic_iv_echo_release"],
        "n_logical_inputs_checked": len(checked),
        "embedding_batches_declared": len(inputs.embedding_batches),
        "canonical_targets_checked": list(TARGET_NAMES),
        "row_level_analysis_executed": False,
    }


def _read_csv(path: Path, label: str) -> pd.DataFrame:
    if not path.exists():
        raise Tier1BlockedError(BLOCKED_LINEAGE, f"missing {label}")
    try:
        return pd.read_csv(path)
    except pd.errors.EmptyDataError as exc:
        raise Tier1BlockedError(BLOCKED_LINEAGE, f"empty {label}") from exc


def _read_many(paths: Sequence[Path], label: str) -> pd.DataFrame:
    if not paths:
        raise Tier1BlockedError(BLOCKED_LINEAGE, f"no {label} inputs supplied")
    frames: list[pd.DataFrame] = []
    for index, path in enumerate(paths):
        frame = _read_csv(path, f"{label}[{index}]").copy()
        frame["_source_index"] = index
        frames.append(frame)
    return pd.concat(frames, ignore_index=True, sort=False)


def _normalize_ids(frame: pd.DataFrame, label: str, require_subject: bool = True) -> pd.DataFrame:
    required = ["study_id", "subject_id"] if require_subject else ["study_id"]
    require_columns(frame, required, label)
    out = frame.copy()
    if out["study_id"].isna().any():
        raise Tier1BlockedError(BLOCKED_LINEAGE, f"{label} contains missing study_id values")
    out["_study"] = out["study_id"].astype(str)
    if "subject_id" in out.columns:
        out["_subject"] = out["subject_id"].astype(str)
        if out["subject_id"].isna().any():
            raise Tier1BlockedError(BLOCKED_LINEAGE, f"{label} contains missing subject_id values")
    return out


def _bool_series(frame: pd.DataFrame, column: str, default: bool = False) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(default, index=frame.index, dtype=bool)
    values = frame[column]
    if pd.api.types.is_bool_dtype(values):
        return values.fillna(default).astype(bool)
    normalized = values.astype(str).str.strip().str.lower()
    return normalized.isin({"true", "1", "yes", "y"})


def _record_key(frame: pd.DataFrame, label: str) -> pd.Series:
    for column in ("dicom_filepath", "sop_instance_uid", "clip_path", "npz_path"):
        if column in frame.columns:
            values = frame[column].astype(str)
            return frame["_study"] + "::" + values
    if "embedding_idx" in frame.columns:
        return frame["_study"] + "::embedding_idx::" + frame["embedding_idx"].astype(str)
    raise Tier1BlockedError(
        BLOCKED_LINEAGE,
        f"{label} lacks a DICOM/clip record key needed for deduplication",
    )


def _study_subject_pairs(frame: pd.DataFrame, label: str) -> pd.DataFrame:
    normalized = _normalize_ids(frame, label, require_subject=True)
    return normalized[["_study", "_subject"]].drop_duplicates()


def _assert_subject_consistency(
    named_frames: Mapping[str, pd.DataFrame], report: InvariantReport
) -> dict[str, str]:
    pairs: list[pd.DataFrame] = []
    for name, frame in named_frames.items():
        if frame.empty or not {"study_id", "subject_id"}.issubset(frame.columns):
            continue
        piece = _study_subject_pairs(frame, name).copy()
        piece["_source"] = name
        pairs.append(piece)
    if not pairs:
        raise Tier1BlockedError(BLOCKED_LINEAGE, "no study-to-subject mappings were available")
    combined = pd.concat(pairs, ignore_index=True)
    counts = combined.groupby("_study")["_subject"].nunique()
    conflicts = int((counts > 1).sum())
    report.add("consistent_subject_assignment_across_pipeline", conflicts == 0, conflicts, 0)
    if conflicts:
        raise Tier1BlockedError(
            BLOCKED_LINEAGE,
            f"{conflicts} studies have conflicting subject assignments across pipeline sources",
        )
    return combined.drop_duplicates("_study").set_index("_study")["_subject"].to_dict()


def validate_lineage_metadata(
    metadata: Mapping[str, Any],
    batch_names: Sequence[str],
    split_map: Path,
    report: InvariantReport,
) -> None:
    if not split_map.exists():
        raise Tier1BlockedError(BLOCKED_LINEAGE, "frozen split map is missing")
    required_text = (
        "mimic_iv_echo_release",
        "source_denominator_definition",
        "imaging_lineage",
        "label_lineage",
    )
    missing = [key for key in required_text if not str(metadata.get(key, "")).strip()]
    if metadata.get("protocol_version") != PROTOCOL_VERSION:
        missing.append(f"protocol_version={PROTOCOL_VERSION}")
    split_meta = metadata.get("split_map")
    if not isinstance(split_meta, Mapping):
        missing.append("split_map")
    else:
        for key in ("version", "generator", "expected_sha256"):
            if not str(split_meta.get(key, "")).strip():
                missing.append(f"split_map.{key}")
    batch_meta = metadata.get("batch_sources")
    if not isinstance(batch_meta, Mapping):
        missing.append("batch_sources")
        batch_meta = {}
    missing_batches = sorted(set(batch_names) - set(batch_meta))
    unexpected_batches = sorted(set(batch_meta) - set(batch_names))
    if missing_batches:
        missing.append(f"batch_sources entries: {missing_batches}")
    if unexpected_batches:
        missing.append(f"batch_sources not supplied as inputs: {unexpected_batches}")
    invalid_batch_classes = sorted(
        name
        for name in set(batch_names) & set(batch_meta)
        if not isinstance(batch_meta[name], Mapping)
        or batch_meta[name].get("source_class") not in {"legacy", "fullscale"}
    )
    if invalid_batch_classes:
        missing.append(f"invalid batch source_class entries: {invalid_batch_classes}")
    invalid_outside_policies = sorted(
        name
        for name in set(batch_names) & set(batch_meta)
        if not isinstance(batch_meta[name], Mapping)
        or batch_meta[name].get("outside_universe_policy", "canonical_only")
        not in OUTSIDE_UNIVERSE_POLICIES
    )
    if invalid_outside_policies:
        missing.append(f"invalid outside_universe_policy entries: {invalid_outside_policies}")
    nonlegacy_outside_declarations = sorted(
        name
        for name in set(batch_names) & set(batch_meta)
        if isinstance(batch_meta[name], Mapping)
        and batch_meta[name].get("outside_universe_policy", "canonical_only")
        == "declared_legacy_scope"
        and batch_meta[name].get("source_class") != "legacy"
    )
    if nonlegacy_outside_declarations:
        missing.append(
            "outside-universe scope declared for nonlegacy batches: "
            f"{nonlegacy_outside_declarations}"
        )
    invalid_overlap_pairs: list[str] = []
    for raw_pair in metadata.get("allowed_batch_overlap_pairs", []):
        pair = str(raw_pair).split("|")
        if len(pair) != 2 or pair[0] == pair[1] or not set(pair).issubset(set(batch_names)):
            invalid_overlap_pairs.append(str(raw_pair))
    if invalid_overlap_pairs:
        missing.append(f"invalid allowed_batch_overlap_pairs: {sorted(invalid_overlap_pairs)}")
    flow_structure = metadata.get("flow_structure")
    report.add(
        "parallel_flow_structure_declared",
        flow_structure == "parallel_branches",
        flow_structure,
        "parallel_branches",
        "A forced linear funnel is invalid for partially different lineage artifacts.",
    )
    if flow_structure != "parallel_branches":
        raise Tier1BlockedError(
            BLOCKED_LINEAGE,
            "invalid lineage metadata: cohort construction must declare parallel_branches",
        )
    report.add("release_and_lineage_metadata_present", not missing, len(missing), 0)
    if missing:
        raise Tier1BlockedError(BLOCKED_LINEAGE, f"missing lineage metadata: {missing}")
    actual_split_hash = sha256_file(split_map)
    expected_split_hash = str(split_meta["expected_sha256"]).lower()
    report.add(
        "split_map_sha256_matches_pinned_provenance",
        actual_split_hash == expected_split_hash,
        actual_split_hash,
        expected_split_hash,
    )
    if actual_split_hash != expected_split_hash:
        raise Tier1BlockedError(BLOCKED_LINEAGE, "split-map SHA-256 does not match pinned lineage metadata")


def _load_canonical_summary(path: Path, target: str) -> dict[str, Any]:
    if not path.exists():
        raise Tier1BlockedError(BLOCKED_LINEAGE, f"missing canonical summary for {target}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload.get("target_summaries"), list):
        matches = [item for item in payload["target_summaries"] if item.get("target") == target]
        if len(matches) != 1:
            raise Tier1BlockedError(BLOCKED_LINEAGE, f"canonical summary does not uniquely identify {target}")
        return matches[0]
    if payload.get("target") == target:
        return payload
    raise Tier1BlockedError(BLOCKED_LINEAGE, f"canonical summary target mismatch for {target}")


def _stage(
    branch: str,
    stage: str,
    frame: pd.DataFrame,
    row_mask: pd.Series | None = None,
    target: str = "",
    notes: str = "",
) -> dict[str, Any]:
    subset = frame if row_mask is None else frame.loc[row_mask]
    return {
        "branch": branch,
        "target": target,
        "stage": stage,
        "n_rows": int(len(subset)),
        "n_studies": int(subset["_study"].nunique()) if "_study" in subset.columns else None,
        "n_subjects": int(subset["_subject"].nunique()) if "_subject" in subset.columns else None,
        "notes": notes,
    }


def _prepare_dicom_branch(
    expected: pd.DataFrame,
    audits: pd.DataFrame,
    extraction: pd.DataFrame,
    report: InvariantReport,
) -> tuple[list[dict[str, Any]], pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    expected = _normalize_ids(expected, "expected DICOM records")
    audits = _normalize_ids(audits, "DICOM audits")
    extraction = _normalize_ids(extraction, "extraction manifests")
    for frame, label in (
        (expected, "expected records"),
        (audits, "DICOM audits"),
        (extraction, "extraction manifests"),
    ):
        frame["_record"] = _record_key(frame, label)

    expected_dup = int(expected.duplicated("_record").sum())
    audit_dup = int(audits.duplicated("_record").sum())
    extraction_dup = int(extraction.duplicated("_record").sum())
    report.add("unique_expected_dicom_keys", expected_dup == 0, expected_dup, 0)
    report.add("unique_dicom_audit_keys_after_union", audit_dup == 0, audit_dup, 0)
    report.add("unique_extraction_keys_after_union", extraction_dup == 0, extraction_dup, 0)

    expected = expected.drop_duplicates("_record", keep="first")
    audits = audits.drop_duplicates("_record", keep="first")
    extraction = extraction.drop_duplicates("_record", keep="first")

    missing_audit = len(set(expected["_record"]) - set(audits["_record"]))
    unexpected_audit = len(set(audits["_record"]) - set(expected["_record"]))
    report.add("all_expected_dicoms_audited", missing_audit == 0, missing_audit, 0)
    report.add("no_unexpected_dicoms_in_audit", unexpected_audit == 0, unexpected_audit, 0)

    read_ok = _bool_series(audits, "read_ok")
    multiframe = _bool_series(audits, "is_multiframe")
    extracted_ok = _bool_series(extraction, "write_ok")
    candidate_keys = set(audits.loc[read_ok & multiframe, "_record"])
    extraction_keys = set(extraction["_record"])
    report.add(
        "multiframe_candidates_match_extraction_input",
        candidate_keys == extraction_keys,
        len(candidate_keys.symmetric_difference(extraction_keys)),
        0,
    )

    stages = [
        _stage("imaging", "expected_dicom_records", expected),
        _stage("imaging", "readable_dicom_records", audits, read_ok),
        _stage("imaging", "unreadable_dicom_records", audits, ~read_ok),
        _stage("imaging", "single_frame_readable_records", audits, read_ok & ~multiframe),
        _stage("imaging", "multiframe_readable_records", audits, read_ok & multiframe),
        _stage("imaging", "successfully_extracted_clips", extraction, extracted_ok),
        _stage("imaging", "failed_clip_extractions", extraction, ~extracted_ok),
    ]
    return stages, expected, audits, extraction


def _prepare_embedding_batches(
    batches: Mapping[str, pd.DataFrame],
    eligible_studies: set[str],
    metadata: Mapping[str, Any],
    report: InvariantReport,
) -> EmbeddingBatchResult:
    normalized: list[pd.DataFrame] = []
    batch_rows: list[dict[str, Any]] = []
    batch_study_sets: dict[str, set[str]] = {}
    batch_meta = metadata["batch_sources"]

    for name, raw in batches.items():
        frame = _normalize_ids(raw, f"embedding batch {name}")
        success = _bool_series(frame, "write_ok", default=True)
        frame = frame.loc[success].copy()
        frame["_record"] = _record_key(frame, f"embedding batch {name}")
        duplicate_clip_keys = int(frame.duplicated("_record").sum())
        report.add(f"{name}_unique_embedded_clip_keys", duplicate_clip_keys == 0, duplicate_clip_keys, 0)
        frame = frame.drop_duplicates("_record", keep="first")
        frame["_batch"] = name
        frame["_in_canonical_universe"] = frame["_study"].isin(eligible_studies)
        outside_policy = str(batch_meta[name].get("outside_universe_policy", "canonical_only"))
        frame["_outside_scope_class"] = "canonical_universe"
        outside_mask = ~frame["_in_canonical_universe"]
        if outside_policy == "declared_legacy_scope":
            frame.loc[outside_mask, "_outside_scope_class"] = "declared_legacy_scope"
        else:
            frame.loc[outside_mask, "_outside_scope_class"] = "undeclared_outside_scope"
        normalized.append(frame)
        studies = set(frame["_study"])
        batch_study_sets[name] = studies
        study_counts = frame.groupby("_study").size()
        batch_rows.append(
            {
                "batch_name": name,
                "source_class": str(batch_meta[name].get("source_class", "unspecified")),
                "outside_universe_policy": outside_policy,
                "n_successful_clip_rows": int(len(frame)),
                "n_unique_studies": int(len(studies)),
                "n_unique_subjects": int(frame["_subject"].nunique()),
                "n_studies_with_multiple_clips": int((study_counts > 1).sum()),
                "n_duplicate_clip_keys_removed": duplicate_clip_keys,
                "n_studies_in_canonical_universe": int(frame.loc[frame["_in_canonical_universe"], "_study"].nunique()),
                "n_studies_outside_canonical_universe": int(
                    frame.loc[~frame["_in_canonical_universe"], "_study"].nunique()
                ),
                "n_declared_legacy_studies_outside_canonical_universe": int(
                    frame.loc[
                        frame["_outside_scope_class"] == "declared_legacy_scope", "_study"
                    ].nunique()
                ),
                "n_undeclared_studies_outside_canonical_universe": int(
                    frame.loc[
                        frame["_outside_scope_class"] == "undeclared_outside_scope", "_study"
                    ].nunique()
                ),
            }
        )

    merged = pd.concat(normalized, ignore_index=True, sort=False)
    allowed_pairs = {
        tuple(sorted(str(item).split("|")))
        for item in metadata.get("allowed_batch_overlap_pairs", [])
        if "|" in str(item)
    }
    for left_index, left in enumerate(sorted(batch_study_sets)):
        for right in sorted(batch_study_sets)[left_index + 1 :]:
            overlap = len(batch_study_sets[left] & batch_study_sets[right])
            pair = tuple(sorted((left, right)))
            explained = overlap == 0 or pair in allowed_pairs
            report.add(
                f"batch_overlap_explained__{left}__{right}",
                explained,
                overlap,
                0 if pair not in allowed_pairs else "declared_allowed_overlap",
            )

    in_universe = merged[merged["_in_canonical_universe"]].copy()
    canonical_clips = in_universe.sort_values(["_study", "_batch", "_record"]).drop_duplicates("_record")
    declared_outside_clips = (
        merged.loc[merged["_outside_scope_class"] == "declared_legacy_scope"]
        .sort_values(["_study", "_batch", "_record"])
        .drop_duplicates("_record")
    )
    undeclared_outside = merged.loc[merged["_outside_scope_class"] == "undeclared_outside_scope"]
    canonical_studies = set(canonical_clips["_study"])
    declared_outside_studies = set(declared_outside_clips["_study"])
    undeclared_outside_studies = set(undeclared_outside["_study"])
    report.add(
        "unique_canonical_study_keys_after_batch_deduplication",
        len(canonical_studies) == canonical_clips["_study"].nunique(),
        canonical_clips["_study"].nunique(),
        len(canonical_studies),
    )
    report.add(
        "no_undeclared_outside_universe_batch_studies",
        not undeclared_outside_studies,
        len(undeclared_outside_studies),
        0,
    )
    stages = [
        _stage("imaging", "canonical_universe_embedded_clips_deduplicated", canonical_clips),
        _stage(
            "imaging",
            "canonical_universe_studies_with_usable_embeddings",
            canonical_clips.drop_duplicates("_study"),
        ),
        _stage(
            "imaging",
            "outside_universe_declared_legacy_embedded_clips",
            declared_outside_clips,
        ),
        _stage(
            "imaging",
            "outside_universe_declared_legacy_embedding_studies",
            declared_outside_clips.drop_duplicates("_study"),
        ),
        _stage(
            "imaging",
            "outside_universe_undeclared_embedding_studies",
            undeclared_outside.drop_duplicates("_study"),
        ),
    ]
    return EmbeddingBatchResult(
        merged=merged,
        canonical_clips=canonical_clips,
        declared_outside_clips=declared_outside_clips,
        stages=stages,
        canonical_studies=canonical_studies,
        declared_outside_studies=declared_outside_studies,
        undeclared_outside_studies=undeclared_outside_studies,
        reconciliation=pd.DataFrame(batch_rows),
    )


def _target_branch(
    measures: pd.DataFrame,
    target: str,
) -> tuple[pd.DataFrame, dict[str, Any], list[dict[str, Any]], pd.DataFrame]:
    require_columns(measures, ["study_id", "subject_id", "measurement", "result"], "structured measurements")
    work = _normalize_ids(measures, "structured measurements")
    work["_measurement_norm"] = work["measurement"].map(normalize_text)
    exact = work[work["_measurement_norm"] == normalize_text(TARGETS[target]["measurement"])].copy()
    exact["_result_num"] = pd.to_numeric(exact["result"], errors="coerce")
    numeric = exact[exact["_result_num"].notna()].copy()
    unit_col = "unit" if "unit" in numeric.columns else "units" if "units" in numeric.columns else None
    if unit_col is None:
        numeric["_unit_norm"] = "unknown"
    else:
        numeric["_unit_norm"] = numeric[unit_col].map(normalize_unit)
    recognized = numeric["_unit_norm"].isin({"cm", "mm"})

    grouped, canonical_summary = extract_target(measures, target, exclude_hard_extremes=False)
    grouped = grouped.rename(columns={"study_id_str": "_study", "subject_id_str": "_subject"})
    if grouped["_study"].duplicated().any():
        raise Tier1BlockedError(BLOCKED_LINEAGE, f"{target} labels map one study to multiple subjects")
    multiplicity = (
        grouped["n_target_rows"]
        .value_counts()
        .sort_index()
        .rename_axis("eligible_rows_per_study")
        .reset_index(name="n_studies")
    )
    multiplicity.insert(0, "target", target)
    stages = [
        _stage("labels", "raw_matching_measurement_rows", exact, target=target),
        _stage("labels", "numeric_parsable_rows", numeric, target=target),
        _stage("labels", "recognized_unit_rows", numeric, recognized, target=target),
        _stage(
            "labels",
            "rows_surviving_primary_canonical_rules",
            numeric,
            target=target,
            notes="Primary analysis retains numeric hard extremes; median study aggregation follows.",
        ),
        _stage("labels", "study_level_median_report_labels", grouped, target=target),
    ]
    branch_summary = {
        **canonical_summary,
        "raw_matching_rows": int(len(exact)),
        "numeric_parsable_rows": int(len(numeric)),
        "recognized_unit_rows": int(recognized.sum()),
        "unknown_or_unrecognized_unit_rows": int((~recognized).sum()),
        "study_label_aggregation": "median",
    }
    return grouped, branch_summary, stages, multiplicity


def _intersection_branch(
    target: str,
    labels: pd.DataFrame,
    embedded_studies: set[str],
    split_frame: pd.DataFrame,
    canonical_summary: Mapping[str, Any],
    report: InvariantReport,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], pd.DataFrame, pd.DataFrame]:
    label_studies = set(labels["_study"])
    target_without_embedding = label_studies - embedded_studies
    embedding_without_target = embedded_studies - label_studies
    intersection = labels[labels["_study"].isin(embedded_studies)].copy()
    split_lookup = split_frame.set_index("subject_id_str")["split"].to_dict()
    intersection["split"] = intersection["_subject"].map(split_lookup)
    missing_split = intersection["split"].isna()
    final = intersection[intersection["split"].isin(VALID_SPLITS)].copy()

    report.add(
        f"{target}_target_partition_reconciles",
        len(target_without_embedding) + len(set(intersection["_study"])) == len(label_studies),
        len(target_without_embedding) + len(set(intersection["_study"])),
        len(label_studies),
    )
    report.add(
        f"{target}_embedding_partition_reconciles",
        len(embedding_without_target) + len(set(intersection["_study"])) == len(embedded_studies),
        len(embedding_without_target) + len(set(intersection["_study"])),
        len(embedded_studies),
    )

    split_rows: list[dict[str, Any]] = []
    for split in VALID_SPLITS:
        subset = final[final["split"] == split]
        split_rows.append(
            {
                "target": target,
                "split": split,
                "n_studies": int(subset["_study"].nunique()),
                "n_subjects": int(subset["_subject"].nunique()),
            }
        )
    split_counts = {row["split"]: row["n_studies"] for row in split_rows}
    expected_splits = {key: int(value) for key, value in canonical_summary.get("split_counts", {}).items()}
    report.add(
        f"{target}_final_split_totals_match_canonical_analysis",
        split_counts == expected_splits,
        split_counts,
        expected_splits,
    )
    expected_joined = int(canonical_summary.get("joined_target_embedding_studies", -1))
    report.add(
        f"{target}_final_study_total_matches_canonical_analysis",
        final["_study"].nunique() == expected_joined,
        int(final["_study"].nunique()),
        expected_joined,
    )
    expected_subjects = canonical_summary.get("joined_target_embedding_subjects")
    if expected_subjects is not None:
        report.add(
            f"{target}_final_subject_total_matches_canonical_analysis",
            final["_subject"].nunique() == int(expected_subjects),
            int(final["_subject"].nunique()),
            int(expected_subjects),
        )

    overlap_counts: dict[str, int] = {}
    subjects_by_split = {
        split: set(final.loc[final["split"] == split, "_subject"])
        for split in VALID_SPLITS
    }
    for left_index, left in enumerate(VALID_SPLITS):
        for right in VALID_SPLITS[left_index + 1 :]:
            overlap_counts[f"{left}_vs_{right}"] = len(subjects_by_split[left] & subjects_by_split[right])
    report.add(
        f"{target}_no_subject_overlap_across_splits",
        sum(overlap_counts.values()) == 0,
        overlap_counts,
        {key: 0 for key in overlap_counts},
    )

    multiplicity = (
        final.groupby("_subject")["_study"]
        .nunique()
        .value_counts()
        .sort_index()
        .rename_axis("studies_per_subject")
        .reset_index(name="n_subjects")
    )
    multiplicity.insert(0, "target", target)
    stages = [
        {
            "branch": "intersection",
            "target": target,
            "stage": "target_positive_studies_without_usable_embeddings",
            "n_rows": None,
            "n_studies": len(target_without_embedding),
            "n_subjects": int(labels.loc[labels["_study"].isin(target_without_embedding), "_subject"].nunique()),
            "notes": "",
        },
        {
            "branch": "intersection",
            "target": target,
            "stage": "embedded_studies_without_target",
            "n_rows": None,
            "n_studies": len(embedding_without_target),
            "n_subjects": None,
            "notes": "",
        },
        _stage("intersection", "target_plus_embedding_studies", intersection, target=target),
        _stage(
            "intersection",
            "target_plus_embedding_studies_lacking_split",
            intersection,
            missing_split,
            target=target,
        ),
        _stage("intersection", "final_analysis_studies", final, target=target),
    ]
    summary = {
        "target": target,
        "target_positive_studies": int(len(label_studies)),
        "target_positive_studies_without_usable_embeddings": int(len(target_without_embedding)),
        "embedded_studies_without_target": int(len(embedding_without_target)),
        "target_plus_embedding_studies": int(intersection["_study"].nunique()),
        "studies_lacking_split_assignment": int(missing_split.sum()),
        "final_analysis_studies": int(final["_study"].nunique()),
        "final_analysis_subjects": int(final["_subject"].nunique()),
        "split_study_counts": split_counts,
        "subject_overlap_counts": overlap_counts,
    }
    restricted = final[["_study", "_subject", "split"]].copy()
    restricted["target"] = target
    return summary, stages, split_rows, multiplicity, restricted


def reconstruct_cohort_flow(inputs: CohortFlowInputs) -> CohortFlowResult:
    report = InvariantReport()
    if not inputs.lineage_metadata.exists():
        raise Tier1BlockedError(BLOCKED_LINEAGE, "missing lineage metadata JSON")
    metadata = json.loads(inputs.lineage_metadata.read_text(encoding="utf-8"))
    validate_lineage_metadata(metadata, list(inputs.embedding_batches), inputs.split_map, report)

    source = _normalize_ids(_read_csv(inputs.source_studies, "source study universe"), "source study universe")
    eligible = _normalize_ids(_read_csv(inputs.eligible_studies, "eligible study universe"), "eligible study universe")
    expected = _read_many(inputs.expected_records, "expected DICOM records")
    audits = _read_many(inputs.dicom_audits, "DICOM audits")
    extraction = _read_many(inputs.extraction_manifests, "extraction manifests")
    batch_frames = {
        name: _read_csv(path, f"embedding batch {name}")
        for name, path in inputs.embedding_batches.items()
    }
    final_embeddings = _normalize_ids(
        _read_csv(inputs.final_study_embeddings, "final study embedding manifest"),
        "final study embedding manifest",
    )
    measures = _read_csv(inputs.structured_measurements, "structured measurements")
    raw_split = _read_csv(inputs.split_map, "split map")
    try:
        split_frame, split_warnings = load_splits(inputs.split_map)
    except (ValueError, RuntimeError) as exc:
        raise Tier1BlockedError(BLOCKED_LINEAGE, f"invalid frozen split map: {exc}") from exc
    report.add("split_map_has_no_unexpected_labels", not split_warnings, len(split_warnings), 0)

    named_for_subjects: dict[str, pd.DataFrame] = {
        "source_studies": source,
        "eligible_studies": eligible,
        "expected_records": expected,
        "dicom_audits": audits,
        "extraction_manifests": extraction,
        "final_study_embeddings": final_embeddings,
        "structured_measurements": measures,
    }
    named_for_subjects.update({f"embedding_batch:{name}": frame for name, frame in batch_frames.items()})
    _assert_subject_consistency(named_for_subjects, report)

    source_dup = int(source["_study"].duplicated().sum())
    eligible_dup = int(eligible["_study"].duplicated().sum())
    final_dup = int(final_embeddings["_study"].duplicated().sum())
    report.add("unique_source_study_keys", source_dup == 0, source_dup, 0)
    report.add("unique_eligible_study_keys", eligible_dup == 0, eligible_dup, 0)
    report.add("unique_final_study_embedding_keys", final_dup == 0, final_dup, 0)
    report.add(
        "eligible_universe_is_subset_of_source_denominator",
        set(eligible["_study"]).issubset(set(source["_study"])),
        len(set(eligible["_study"]) - set(source["_study"])),
        0,
    )

    stage_rows: list[dict[str, Any]] = [
        _stage("imaging", "official_release_source_studies", source),
        _stage("imaging", "project_eligible_studies", eligible),
    ]
    dicom_stages, expected, audits, extraction = _prepare_dicom_branch(expected, audits, extraction, report)
    stage_rows.extend(dicom_stages)

    eligible_set = set(eligible["_study"])
    embedding_result = _prepare_embedding_batches(
        batch_frames,
        eligible_set,
        metadata,
        report,
    )
    stage_rows.extend(embedding_result.stages)
    final_set = set(final_embeddings["_study"])
    final_canonical_set = final_set & eligible_set
    final_outside_set = final_set - eligible_set
    report.add(
        "canonical_batch_union_matches_final_manifest_canonical_intersection",
        embedding_result.canonical_studies == final_canonical_set,
        len(embedding_result.canonical_studies.symmetric_difference(final_canonical_set)),
        0,
    )
    report.add(
        "final_outside_universe_embeddings_are_declared_legacy",
        final_outside_set.issubset(embedding_result.declared_outside_studies),
        len(final_outside_set - embedding_result.declared_outside_studies),
        0,
    )
    report.add(
        "declared_legacy_outside_union_matches_final_manifest_outside_scope",
        embedding_result.declared_outside_studies == final_outside_set,
        len(embedding_result.declared_outside_studies.symmetric_difference(final_outside_set)),
        0,
    )
    final_canonical_embeddings = final_embeddings.loc[final_embeddings["_study"].isin(final_canonical_set)]
    final_outside_embeddings = final_embeddings.loc[final_embeddings["_study"].isin(final_outside_set)]
    stage_rows.extend(
        [
            _stage(
                "imaging",
                "final_manifest_canonical_universe_study_embeddings",
                final_canonical_embeddings,
            ),
            _stage(
                "imaging",
                "final_manifest_outside_universe_retained_legacy_embeddings",
                final_outside_embeddings,
            ),
        ]
    )

    label_summaries: dict[str, Any] = {}
    target_summaries: dict[str, Any] = {}
    split_rows: list[dict[str, Any]] = []
    label_multiplicity_frames: list[pd.DataFrame] = []
    subject_multiplicity_frames: list[pd.DataFrame] = []
    restricted_target_frames: list[pd.DataFrame] = []
    label_frames: dict[str, pd.DataFrame] = {}
    for target in TARGET_NAMES:
        labels, label_summary, label_stages, label_multiplicity = _target_branch(measures, target)
        label_frames[target] = labels
        label_summaries[target] = label_summary
        stage_rows.extend(label_stages)
        label_multiplicity_frames.append(label_multiplicity)
        canonical = _load_canonical_summary(inputs.canonical_summaries[target], target)
        target_summary, target_stages, target_split_rows, subject_multiplicity, restricted = _intersection_branch(
            target,
            labels,
            final_canonical_set,
            split_frame,
            canonical,
            report,
        )
        target_summaries[target] = target_summary
        stage_rows.extend(target_stages)
        split_rows.extend(target_split_rows)
        subject_multiplicity_frames.append(subject_multiplicity)
        restricted_target_frames.append(restricted)

    stage_frame = pd.DataFrame(stage_rows)
    split_output = pd.DataFrame(split_rows)
    label_multiplicity = pd.concat(label_multiplicity_frames, ignore_index=True)
    subject_multiplicity = pd.concat(subject_multiplicity_frames, ignore_index=True)
    for name, frame in (
        ("cohort stages", stage_frame),
        ("target split counts", split_output),
        ("label multiplicity", label_multiplicity),
        ("subject multiplicity", subject_multiplicity),
        ("batch reconciliation", embedding_result.reconciliation),
    ):
        assert_export_safe_frame(frame, name)

    numeric_columns = stage_frame.select_dtypes(include="number").columns
    negative_counts = int((stage_frame[numeric_columns].fillna(0) < 0).sum().sum())
    report.add("no_negative_aggregate_counts", negative_counts == 0, negative_counts, 0)

    restricted_base = eligible[["_study", "_subject"]].drop_duplicates().copy()
    batch_memberships = (
        embedding_result.merged[embedding_result.merged["_in_canonical_universe"]]
        .groupby("_study")["_batch"]
        .agg(lambda values: ";".join(sorted(set(values))))
    )
    restricted_base["embedding_batches"] = restricted_base["_study"].map(batch_memberships).fillna("")
    restricted_base["final_embedding_present"] = restricted_base["_study"].isin(final_canonical_set)
    for target, labels in label_frames.items():
        restricted_base[f"{target}_label_present"] = restricted_base["_study"].isin(set(labels["_study"]))
        target_lookup = labels.set_index("_study")
        restricted_base[f"{target}_target_value"] = restricted_base["_study"].map(
            target_lookup["target_value"].to_dict()
        )
        restricted_base[f"{target}_n_target_rows"] = restricted_base["_study"].map(
            target_lookup["n_target_rows"].to_dict()
        )
    restricted_intersections = pd.concat(restricted_target_frames, ignore_index=True)
    restricted_intersections = restricted_intersections.pivot(
        index=["_study", "_subject"], columns="target", values="split"
    ).reset_index()
    restricted_intersections.columns.name = None
    restricted_intersections = restricted_intersections.rename(
        columns={target: f"{target}_split" for target in TARGET_NAMES if target in restricted_intersections.columns}
    )
    restricted_reconciliation = restricted_base.merge(
        restricted_intersections,
        on=["_study", "_subject"],
        how="left",
    ).rename(columns={"_study": "study_id", "_subject": "subject_id"})

    summary = {
        "protocol_version": PROTOCOL_VERSION,
        "status": "ok" if report.passed else BLOCKED_LINEAGE,
        "mimic_iv_echo_release": metadata["mimic_iv_echo_release"],
        "source_denominator_definition": metadata["source_denominator_definition"],
        "source_studies": int(source["_study"].nunique()),
        "source_subjects": int(source["_subject"].nunique()),
        "eligible_studies": int(eligible["_study"].nunique()),
        "eligible_subjects": int(eligible["_subject"].nunique()),
        "final_study_embeddings": int(len(final_set)),
        "final_embedding_subjects": int(final_embeddings["_subject"].nunique()),
        "canonical_universe_study_embeddings": int(len(final_canonical_set)),
        "canonical_universe_embedding_subjects": int(
            final_canonical_embeddings["_subject"].nunique()
        ),
        "outside_universe_retained_legacy_embeddings": int(len(final_outside_set)),
        "outside_universe_retained_legacy_subjects": int(
            final_outside_embeddings["_subject"].nunique()
        ),
        "outside_universe_unexplained_embeddings": int(
            len(final_outside_set - embedding_result.declared_outside_studies)
        ),
        "label_branches": label_summaries,
        "target_intersections": target_summaries,
        "split_map_subjects": int(raw_split["subject_id"].nunique()),
        "split_map_sha256": sha256_file(inputs.split_map),
        "all_invariants_passed": report.passed,
    }

    input_paths: list[tuple[str, Path, int | None, pd.DataFrame | None]] = [
        ("source_study_universe", inputs.source_studies, len(source), source),
        ("eligible_study_universe", inputs.eligible_studies, len(eligible), eligible),
        ("final_study_embedding_manifest", inputs.final_study_embeddings, len(final_embeddings), final_embeddings),
        ("structured_measurements", inputs.structured_measurements, len(measures), measures),
        ("frozen_split_map", inputs.split_map, len(raw_split), raw_split),
        ("lineage_metadata", inputs.lineage_metadata, None, None),
    ]
    input_paths.extend(
        (f"expected_records_{index}", path, None, None)
        for index, path in enumerate(inputs.expected_records)
    )
    input_paths.extend(
        (f"dicom_audit_{index}", path, None, None)
        for index, path in enumerate(inputs.dicom_audits)
    )
    input_paths.extend(
        (f"extraction_manifest_{index}", path, None, None)
        for index, path in enumerate(inputs.extraction_manifests)
    )
    input_paths.extend(
        (f"embedding_batch_{name}", path, len(batch_frames[name]), batch_frames[name])
        for name, path in inputs.embedding_batches.items()
    )
    input_paths.extend(
        (f"canonical_summary_{target}", path, None, None)
        for target, path in inputs.canonical_summaries.items()
    )
    files: list[dict[str, Any]] = []
    for role, path, row_count, frame in input_paths:
        record = safe_file_record(role, path, row_count)
        if frame is not None:
            record["schema_sha256"] = schema_hash(frame)
            record["field_count"] = int(len(frame.columns))
        files.append(record)
    provenance = {
        "protocol_version": PROTOCOL_VERSION,
        "inputs": files,
        "lineage": sanitize_for_safe_manifest(metadata),
        "split_map_schema_sha256": schema_hash(raw_split),
        "split_map_field_count": int(len(raw_split.columns)),
    }

    return CohortFlowResult(
        summary=summary,
        stages=stage_frame,
        target_splits=split_output,
        label_multiplicity=label_multiplicity,
        subject_multiplicity=subject_multiplicity,
        batch_reconciliation=embedding_result.reconciliation,
        invariants=report.payload(),
        provenance=provenance,
        restricted_reconciliation=restricted_reconciliation,
    )


def write_cohort_flow_outputs(
    result: CohortFlowResult,
    output_dir: Path,
    restricted_reconciliation_csv: Path | None = None,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    write_json(output_dir / "cohort_flow_invariants.json", result.invariants)
    if result.invariants["status"] != "ok":
        raise Tier1BlockedError(BLOCKED_LINEAGE, "one or more cohort-flow invariants failed")
    write_json(output_dir / "cohort_flow_summary.json", result.summary)
    write_safe_csv(output_dir / "cohort_flow_stages.csv", result.stages, "cohort stages")
    write_safe_csv(output_dir / "cohort_flow_target_splits.csv", result.target_splits, "target split counts")
    write_safe_csv(
        output_dir / "cohort_flow_label_multiplicity.csv",
        result.label_multiplicity,
        "label multiplicity",
    )
    write_safe_csv(
        output_dir / "cohort_flow_subject_multiplicity.csv",
        result.subject_multiplicity,
        "subject multiplicity",
    )
    write_safe_csv(
        output_dir / "cohort_flow_batch_reconciliation.csv",
        result.batch_reconciliation,
        "batch reconciliation",
    )
    write_json(output_dir / "cohort_flow_provenance.json", result.provenance)
    if restricted_reconciliation_csv is not None:
        destination = require_restricted_destination(restricted_reconciliation_csv)
        destination.parent.mkdir(parents=True, exist_ok=True)
        result.restricted_reconciliation.to_csv(destination, index=False)
        for target in TARGET_NAMES:
            split_column = f"{target}_split"
            if split_column not in result.restricted_reconciliation.columns:
                continue
            cohort = result.restricted_reconciliation.loc[
                result.restricted_reconciliation[split_column].isin(VALID_SPLITS),
                [
                    "study_id",
                    "subject_id",
                    split_column,
                    f"{target}_target_value",
                    f"{target}_n_target_rows",
                ],
            ].rename(
                columns={
                    split_column: "split",
                    f"{target}_target_value": "target_value",
                    f"{target}_n_target_rows": "n_target_rows",
                }
            )
            cohort.to_csv(destination.parent / f"jdim_target_cohort_{target}.csv", index=False)
