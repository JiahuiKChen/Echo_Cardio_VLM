#!/usr/bin/env python3
"""Build the prospective selected-cohort source and technical-smoke manifests.

All row-level outputs are restricted. Aggregate outputs contain counts and fixed
vocabulary only. Candidate tables are imaging/provenance membership tables;
labels, predictions, metrics, and embedding arrays are never inputs.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import tempfile
from typing import Iterable, Mapping, Sequence

import pandas as pd

from lvef_multitask_audit_utils import (
    assert_aggregate_safe_columns,
    assert_aggregate_safe_json,
    require_restricted_path,
    run_guarded,
)


SELECTED_AUTHORITY_SHA256 = "920aa8742297dd90c5f125723a425a85201fa7966e926b3191f2c4a57b3d31c1"
SPLIT_AUTHORITY_SHA256 = "c5101cea1d76b38c6bb4517edf4b463b338d7505032cfa40bc8f27ca5b97e517"
EXPECTED_SELECTED_STUDIES = 4530
EXPECTED_SPLIT_COUNTS = {"train": 3171, "val": 679, "test": 680}
SOURCE_RELEASE = "mimic-iv-echo/1.0"
SOURCE_BUCKET = "mimic-iv-echo-1.0.physionet.org"
SELECTION_SALT = "lvef-multitask-phase1e-a-smoke4-v1"
MAX_SMOKE_STUDIES = 4
MAX_SMOKE_OBJECTS = 1000
EXPECTED_HISTORICAL_IMAGING_STUDIES = 4525
EXPECTED_HISTORICAL_NO_CINE_STUDIES = 5
EXPECTED_TRAIN_NO_CINE_STUDIES = 3
EXPECTED_DUPLICATE_GROUPS = 32
EXPECTED_CANONICAL_PHYSICAL_SOURCE_GROUPS = 184574
EXPECTED_STAGE_D_SURVIVING_NPZ_GROUPS = 14006

LOCKED_PRODUCTION_INPUT_SHA256 = {
    "selected_studies": SELECTED_AUTHORITY_SHA256,
    "split_map": SPLIT_AUTHORITY_SHA256,
    "historical_study_manifest": "feaf0cf7da58ae3d9c8901a583e4319d1b34a8cc26ae57dfcecd309940f81a60",
    "duplicate_resolution": "5397deaf57408c87411f518d56c3f11d158a5bf53014c342f732245edff2d087",
    "canonical_inventory": "be56dba29f646acb01cec631e96b5a62ec42c305a240894bff8451b8ca0c49f2",
    "record_stage_d": "5758cd0bf93969c7c2bc32812d1241e106baea90099a26d0919acaf1a1c41329",
    "record_batch_000": "37b102da0f7a52234fd186c52e6d8b31016433306bc458b4c2a5359ebb7c74cb",
    "record_batch_001": "1214b8a658aa2899809c5fef4961eba7c3953bc84fc33268460c98de270113eb",
    "record_batch_002": "f86a0c5365237898ffe2d512841bfbbad7f1bf2294f7918fdc0bff5adf60eaaa",
    "record_batch_003": "683de4c04b31f8a727def1485ccce2543878b93db7280a34e1947901739c7f69",
    "record_batch_004": "9c4eff18f6543d0780b78c8c70ee4bd72e6ccb130953885c9613a6df83c38b5a",
    "record_batch_005": "7499051ebe1fc3dc50deaa407d6a73ccc2cb050220f00a3bf2c65ce43e1aeb39",
    "record_batch_006": "de27d6bbca1b00526541dd667a4a87592e93a79113a4b2f66ae2bef0b69bb0af",
    "record_batch_007": "f7499f04ec33b96ec5c1b657de9a857a2838a71cf744524db50dfe53b73fa14b",
    "record_batch_008": "c14cb7042eac85aca4aa6e949f0ceca4fa96310961d916c9961149455fedddde",
}

EXPECTED_COMPONENTS = ("stage_d",) + tuple(f"batch_{index:03d}" for index in range(9))
POSITIVE_EVIDENCE_NAMES = {
    "batch_000_duplicate_affected",
    "batch_000_cine_positive_fallback",
    "batch_001_008_cine_positive",
    "stage_d_cine_positive",
    "train_no_cine_negative",
}
OUTPUT_ROLES = (
    "batch_000_duplicate_affected_or_prespecified_fallback",
    "batch_001_008_historical_cine_positive",
    "stage_d_historical_cine_positive_with_surviving_npz",
    "historical_no_cine_negative_control",
)

NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
ID_RE = re.compile(r"^[0-9]+$")
PATH_RE = re.compile(
    r"^files/p(?P<prefix>[0-9]{2})/p(?P<subject>[0-9]+)/s(?P<study>[0-9]+)/"
    r"(?P<filename>[A-Za-z0-9._-]+\.dcm)$"
)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
LOCATOR_COLUMNS = (
    "gcs_uri",
    "source_uri",
    "object_uri",
    "relative_path",
    "gcs_object_path",
    "dicom_filepath",
    "source_relative_path",
)
SIZE_COLUMNS = ("expected_size_bytes", "size_bytes", "object_size_bytes", "file_size_bytes")
HASH_COLUMNS = ("expected_sha256", "sha256", "release_sha256", "source_sha256", "dicom_sha256")
FORBIDDEN_EVIDENCE_TOKENS = (
    "lvef",
    "label",
    "target",
    "y_true",
    "y_pred",
    "prediction",
    "probability",
    "auroc",
    "auc",
    "mae",
    "rmse",
    "performance",
)


def parse_named_path(value: str) -> tuple[str, Path]:
    if "=" not in value:
        raise ValueError("Named paths must use NAME=PATH syntax")
    name, raw_path = value.split("=", 1)
    if not NAME_RE.fullmatch(name):
        raise ValueError("Named path has a noncanonical name")
    return name, Path(raw_path).expanduser()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_release_checksums(path: Path) -> dict[str, str]:
    if path.is_symlink() or not path.is_file():
        raise ValueError("Release checksum authority is not a regular file")
    checksums: dict[str, str] = {}
    with path.open(encoding="utf-8", errors="strict") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            match = re.fullmatch(r"([0-9A-Fa-f]{64})\s+[*]?(.+)", line)
            if match is None:
                raise ValueError("Release checksum authority has a malformed line")
            relative = match.group(2).strip()
            while relative.startswith("./"):
                relative = relative[2:]
            if PATH_RE.fullmatch(relative) is None:
                continue
            digest = match.group(1).lower()
            if relative in checksums and checksums[relative] != digest:
                raise ValueError("Release checksum authority contains a conflict")
            checksums[relative] = digest
    if not checksums:
        raise ValueError("Release checksum authority has no DICOM entries")
    return checksums


def apply_release_checksums(source: pd.DataFrame, path: Path) -> pd.DataFrame:
    checksums = load_release_checksums(path)
    out = source.copy()
    resolved = out["source_relative_path"].map(checksums)
    if resolved.isna().any():
        raise ValueError("A selected source object is absent from release checksums")
    existing = out["expected_sha256"]
    disagreement = existing.notna() & existing.astype(str).ne(resolved)
    if disagreement.any():
        raise ValueError("Record and release checksum authorities disagree")
    out["expected_sha256"] = resolved
    return out


def csv_header(path: Path) -> list[str]:
    if not path.is_file() or path.suffix.lower() != ".csv":
        raise ValueError("Every manifest input must be an existing CSV file")
    with path.open(newline="") as handle:
        try:
            header = next(csv.reader(handle))
        except StopIteration as exc:
            raise ValueError("Manifest CSV is empty") from exc
    if not header or len(header) != len(set(header)):
        raise ValueError("Manifest CSV has an empty or duplicate header")
    return header


def read_columns(path: Path, columns: Sequence[str]) -> pd.DataFrame:
    return pd.read_csv(path, dtype=str, keep_default_na=False, usecols=list(columns), low_memory=False)


def canonical_id(value: object) -> str:
    raw = str(value)
    if raw != raw.strip() or not ID_RE.fullmatch(raw):
        raise ValueError("Identifier is missing or noncanonical")
    canonical = str(int(raw))
    if raw != canonical:
        raise ValueError("Identifier contains a noncanonical numeric representation")
    return canonical


def normalized_source_path(raw_value: object, subject_id: str, study_id: str) -> str:
    raw = str(raw_value)
    prefix = f"gs://{SOURCE_BUCKET}/"
    if raw.startswith("gs://"):
        if not raw.startswith(prefix):
            raise ValueError("Source URI does not use the locked MIMIC-IV-ECHO 1.0 bucket")
        raw = raw[len(prefix) :]
    if raw != raw.strip() or "\\" in raw or raw.startswith("/"):
        raise ValueError("DICOM path is not safely normalized")
    pure = PurePosixPath(raw)
    if pure.as_posix() != raw or any(part in {"", ".", ".."} for part in pure.parts):
        raise ValueError("DICOM path is not canonical POSIX relative form")
    match = PATH_RE.fullmatch(raw)
    if match is None:
        raise ValueError("DICOM path is outside the locked release layout")
    if match.group("subject") != subject_id or match.group("study") != study_id:
        raise ValueError("DICOM path ownership disagrees with the manifest row")
    expected_prefix = f"{int(subject_id) // 1_000_000:02d}"
    if match.group("prefix") != expected_prefix:
        raise ValueError("DICOM path prefix disagrees with subject ownership")
    return raw


def _single_present(header: Sequence[str], candidates: Sequence[str], label: str, required: bool) -> str | None:
    present = [column for column in candidates if column in header]
    if len(present) > 1:
        raise ValueError(f"Manifest contains ambiguous {label} columns")
    if required and not present:
        raise ValueError(f"Manifest is missing a required {label} column")
    return present[0] if present else None


def load_selected(path: Path) -> pd.DataFrame:
    header = csv_header(path)
    for column in ("subject_id", "study_id", "n_dicoms"):
        if column not in header:
            raise ValueError("Selected manifest lacks required ownership/object-count columns")
    frame = read_columns(path, ("subject_id", "study_id", "n_dicoms"))
    frame["subject_id"] = frame["subject_id"].map(canonical_id)
    frame["study_id"] = frame["study_id"].map(canonical_id)
    frame["n_dicoms"] = frame["n_dicoms"].map(canonical_id).astype(int)
    if not frame["n_dicoms"].gt(0).all():
        raise ValueError("Selected manifest has a nonpositive DICOM object count")
    if frame.duplicated(["subject_id", "study_id"]).any():
        raise ValueError("Selected manifest contains duplicate rows")
    if frame["subject_id"].duplicated().any() or frame["study_id"].duplicated().any():
        raise ValueError("Selected manifest violates one-study-per-subject ownership")
    return frame.sort_values(["subject_id", "study_id"]).reset_index(drop=True)


def load_splits(
    path: Path,
    selected: pd.DataFrame,
    expected_counts: Mapping[str, int] | None = None,
) -> dict[str, str]:
    header = csv_header(path)
    if not {"subject_id", "split"}.issubset(header):
        raise ValueError("Split map lacks subject_id or split")
    frame = read_columns(path, ("subject_id", "split"))
    frame["subject_id"] = frame["subject_id"].map(canonical_id)
    if frame["subject_id"].duplicated().any():
        raise ValueError("Split map contains duplicate subjects")
    if not set(frame["split"]).issubset({"train", "val", "test"}):
        raise ValueError("Split map contains a noncanonical split")
    selected_subjects = set(selected["subject_id"])
    if set(frame["subject_id"]) != selected_subjects:
        raise ValueError("Split map does not exactly cover selected subjects")
    observed_counts = {
        name: int(frame["split"].eq(name).sum())
        for name in ("train", "val", "test")
    }
    if expected_counts is not None and observed_counts != dict(expected_counts):
        raise ValueError("Split map counts disagree with the locked historical authority")
    return dict(zip(frame["subject_id"], frame["split"]))


def _input_authority_paths(
    *,
    selected_path: Path,
    split_path: Path,
    record_components: Mapping[str, Path],
    historical_study_manifest: Path | None,
    duplicate_resolution: Path | None,
    canonical_inventory: Path | None,
) -> dict[str, Path]:
    paths = {
        "selected_studies": selected_path,
        "split_map": split_path,
        **{f"record_{name}": path for name, path in record_components.items()},
    }
    optional = {
        "historical_study_manifest": historical_study_manifest,
        "duplicate_resolution": duplicate_resolution,
        "canonical_inventory": canonical_inventory,
    }
    paths.update({name: path for name, path in optional.items() if path is not None})
    return paths


def verify_locked_input_authorities(
    paths: Mapping[str, Path], expected_sha256: Mapping[str, str]
) -> dict[str, str]:
    if set(paths) != set(expected_sha256):
        raise ValueError("Locked input-authority set is not exact")
    observed: dict[str, str] = {}
    for name in sorted(paths):
        expected = str(expected_sha256[name]).lower()
        if not SHA256_RE.fullmatch(expected):
            raise ValueError("Locked input authority contains an invalid SHA-256")
        digest = file_sha256(paths[name])
        if digest != expected:
            raise ValueError("Restricted input authority SHA-256 mismatch")
        observed[name] = digest
    return observed


def load_record_components(
    named_paths: Mapping[str, Path], selected: pd.DataFrame, splits: Mapping[str, str]
) -> pd.DataFrame:
    if set(named_paths) != set(EXPECTED_COMPONENTS):
        raise ValueError("Record components must be exactly stage_d and batch_000 through batch_008")
    ownership = dict(zip(selected["study_id"], selected["subject_id"]))
    rows: list[dict[str, object]] = []
    for component in EXPECTED_COMPONENTS:
        path = named_paths[component]
        header = csv_header(path)
        if not {"subject_id", "study_id"}.issubset(header):
            raise ValueError("Record component lacks required ownership columns")
        locator_columns = [column for column in LOCATOR_COLUMNS if column in header]
        canonical_locator_columns = [
            column for column in locator_columns if column != "gcs_object_path"
        ]
        if canonical_locator_columns:
            locator_columns = canonical_locator_columns
        if not locator_columns:
            raise ValueError("Record component is missing a source locator column")
        size_column = _single_present(header, SIZE_COLUMNS, "size", False)
        hash_column = _single_present(header, HASH_COLUMNS, "SHA-256", False)
        columns = ["subject_id", "study_id", *locator_columns]
        if size_column:
            columns.append(size_column)
        if hash_column:
            columns.append(hash_column)
        frame = read_columns(path, columns)
        for record in frame.to_dict(orient="records"):
            subject_id = canonical_id(record["subject_id"])
            study_id = canonical_id(record["study_id"])
            if study_id not in ownership:
                continue
            if ownership[study_id] != subject_id:
                raise ValueError("Record component ownership disagrees with the selected cohort")
            locator_values = [
                record[column]
                for column in locator_columns
                if str(record[column]).strip()
            ]
            if not locator_values:
                raise ValueError("Record component row has no source locator")
            normalized_locators = {
                normalized_source_path(
                    (
                        f"files/{str(value).lstrip('/')}"
                        if column == "gcs_object_path"
                        and not str(value).startswith(("gs://", "files/"))
                        else value
                    ),
                    subject_id,
                    study_id,
                )
                for column in locator_columns
                for value in [record[column]]
                if str(value).strip()
            }
            if len(normalized_locators) != 1:
                raise ValueError("Record component locator columns disagree")
            relative = normalized_locators.pop()
            size: int | None = None
            if size_column and str(record[size_column]) != "":
                raw_size = str(record[size_column])
                if not ID_RE.fullmatch(raw_size):
                    raise ValueError("Object size is not a nonnegative integer")
                size = int(raw_size)
            expected_hash: str | None = None
            if hash_column and str(record[hash_column]) != "":
                expected_hash = str(record[hash_column])
                if not SHA256_RE.fullmatch(expected_hash):
                    raise ValueError("Object SHA-256 is not lowercase canonical hex")
            rows.append(
                {
                    "release_id": SOURCE_RELEASE,
                    "source_bucket": SOURCE_BUCKET,
                    "component": component,
                    "subject_id": subject_id,
                    "study_id": study_id,
                    "split": splits[subject_id],
                    "source_relative_path": relative,
                    "gcs_uri": f"gs://{SOURCE_BUCKET}/{relative}",
                    "expected_size_bytes": size,
                    "expected_sha256": expected_hash,
                    "source_object_key": hashlib.sha256(
                        f"{SOURCE_RELEASE}\0{relative}".encode("utf-8")
                    ).hexdigest(),
                }
            )
    source = pd.DataFrame(rows)
    if source.empty:
        raise ValueError("No source objects were supplied")
    if source.duplicated(["source_relative_path"]).any() or source.duplicated(["source_object_key"]).any():
        raise ValueError("Source objects are not unique")
    component_count = source.groupby("study_id")["component"].nunique()
    if (component_count != 1).any():
        raise ValueError("A selected study occurs in more than one source component")
    if set(source["study_id"]) != set(selected["study_id"]):
        raise ValueError("Source records do not exactly cover the selected cohort")
    observed_counts = source.groupby("study_id").size()
    expected_counts = selected.set_index("study_id")["n_dicoms"]
    if not observed_counts.reindex(expected_counts.index).eq(expected_counts).all():
        raise ValueError("Source object counts disagree with selected n_dicoms authority")
    component_order = {name: index for index, name in enumerate(EXPECTED_COMPONENTS)}
    source["_component_order"] = source["component"].map(component_order)
    return source.sort_values(
        ["_component_order", "study_id", "source_relative_path"]
    ).drop(columns="_component_order").reset_index(drop=True)


def load_candidate_tables(named_paths: Mapping[str, Path], selected: pd.DataFrame) -> dict[str, pd.DataFrame]:
    if set(named_paths) != POSITIVE_EVIDENCE_NAMES:
        raise ValueError("Candidate evidence names do not match the locked five-table contract")
    ownership = dict(zip(selected["study_id"], selected["subject_id"]))
    out: dict[str, pd.DataFrame] = {}
    for name in sorted(named_paths):
        path = named_paths[name]
        header = csv_header(path)
        lower_header = [column.lower() for column in header]
        if any(token in column for column in lower_header for token in FORBIDDEN_EVIDENCE_TOKENS):
            raise ValueError("Candidate evidence contains an outcome or performance column")
        if not {"subject_id", "study_id"}.issubset(header):
            raise ValueError("Candidate evidence lacks ownership columns")
        frame = read_columns(path, ("subject_id", "study_id"))
        frame["subject_id"] = frame["subject_id"].map(canonical_id)
        frame["study_id"] = frame["study_id"].map(canonical_id)
        for subject_id, study_id in frame[["subject_id", "study_id"]].itertuples(index=False, name=None):
            if study_id not in ownership or ownership[study_id] != subject_id:
                raise ValueError("Candidate evidence is outside selected ownership")
        out[name] = frame.drop_duplicates(["subject_id", "study_id"]).reset_index(drop=True)
    return out


def _parse_restricted_bool(value: object) -> bool:
    normalized = str(value).strip().lower()
    if normalized in {"true", "1", "yes", "y"}:
        return True
    if normalized in {"false", "0", "no", "n"}:
        return False
    raise ValueError("Restricted provenance table contains an invalid boolean")


def _selected_owned_pairs(
    frame: pd.DataFrame,
    selected: pd.DataFrame,
    *,
    allow_outside_selected: bool,
) -> pd.DataFrame:
    work = frame[["subject_id", "study_id"]].copy()
    work["subject_id"] = work["subject_id"].map(canonical_id)
    work["study_id"] = work["study_id"].map(canonical_id)
    ownership = dict(zip(selected["study_id"], selected["subject_id"]))
    selected_rows: list[tuple[str, str]] = []
    for subject_id, study_id in work.itertuples(index=False, name=None):
        if study_id not in ownership:
            if allow_outside_selected:
                continue
            raise ValueError("Restricted provenance table contains an outside-selected study")
        if ownership[study_id] != subject_id:
            raise ValueError("Restricted provenance ownership disagrees with selected authority")
        selected_rows.append((subject_id, study_id))
    return pd.DataFrame(
        sorted(set(selected_rows)), columns=["subject_id", "study_id"]
    )


def _read_owned_pairs(
    path: Path,
    selected: pd.DataFrame,
    *,
    allow_outside_selected: bool,
) -> pd.DataFrame:
    header = csv_header(path)
    if not {"subject_id", "study_id"}.issubset(header):
        raise ValueError("Restricted provenance table lacks ownership columns")
    return _selected_owned_pairs(
        read_columns(path, ("subject_id", "study_id")),
        selected,
        allow_outside_selected=allow_outside_selected,
    )


def derive_candidate_tables(
    *,
    selected: pd.DataFrame,
    source: pd.DataFrame,
    historical_study_manifest: Path,
    duplicate_resolution: Path,
    canonical_inventory: Path,
) -> dict[str, pd.DataFrame]:
    """Derive outcome-blind smoke strata from restricted Phase 1D provenance."""

    embedded = _read_owned_pairs(
        historical_study_manifest, selected, allow_outside_selected=True
    )
    if embedded["study_id"].nunique() != EXPECTED_HISTORICAL_IMAGING_STUDIES:
        raise ValueError("Historical selected imaging-study count mismatch")

    source_studies = source.drop_duplicates("study_id")[[
        "subject_id", "study_id", "split", "component"
    ]]
    embedded_meta = embedded.merge(
        source_studies,
        on=["subject_id", "study_id"],
        how="left",
        validate="one_to_one",
    )
    if embedded_meta[["split", "component"]].isna().any().any():
        raise ValueError("Historical imaging membership is absent from source authority")

    selected_pairs = set(
        selected[["subject_id", "study_id"]].itertuples(index=False, name=None)
    )
    embedded_pairs = set(
        embedded[["subject_id", "study_id"]].itertuples(index=False, name=None)
    )
    no_cine_pairs = sorted(selected_pairs - embedded_pairs)
    if len(no_cine_pairs) != EXPECTED_HISTORICAL_NO_CINE_STUDIES:
        raise ValueError("Historical no-cine study count mismatch")
    split_by_subject = dict(zip(source_studies["subject_id"], source_studies["split"]))
    if (
        sum(split_by_subject[subject] == "train" for subject, _ in no_cine_pairs)
        != EXPECTED_TRAIN_NO_CINE_STUDIES
    ):
        raise ValueError("Historical train no-cine study count mismatch")

    duplicate_header = csv_header(duplicate_resolution)
    duplicate_required = {"subject_id", "study_id", "components", "selected_scope"}
    if not duplicate_required.issubset(duplicate_header):
        raise ValueError("Duplicate-resolution authority lacks required columns")
    duplicate_columns = sorted(duplicate_required)
    if "adjudication_category" in duplicate_header:
        duplicate_columns.append("adjudication_category")
    if "quarantine_required" in duplicate_header:
        duplicate_columns.append("quarantine_required")
    duplicate_frame = read_columns(duplicate_resolution, duplicate_columns)
    if len(duplicate_frame) != EXPECTED_DUPLICATE_GROUPS:
        raise ValueError("Duplicate-resolution group count mismatch")
    if set(duplicate_frame["selected_scope"]) != {"selected"}:
        raise ValueError("Duplicate-resolution authority is not selected-only")
    if not duplicate_frame["components"].eq("batch_000").all():
        raise ValueError("Duplicate-resolution authority is not confined to batch_000")
    if (
        "adjudication_category" not in duplicate_frame.columns
        or set(duplicate_frame["adjudication_category"]) != {"SOURCE_ARTIFACT_PURGED"}
    ):
        raise ValueError("Duplicate-resolution authority lacks the purged-source ruling")
    if (
        "quarantine_required" in duplicate_frame.columns
        and not duplicate_frame["quarantine_required"].map(_parse_restricted_bool).all()
    ):
        raise ValueError("Duplicate-resolution rows are not all quarantined")
    duplicate_pairs = _selected_owned_pairs(
        duplicate_frame,
        selected,
        allow_outside_selected=False,
    )

    inventory_header = csv_header(canonical_inventory)
    inventory_required = {
        "subject_id",
        "study_id",
        "components",
        "source_availability",
        "quarantine_required",
        "n_proposed_canonical_rows",
    }
    if not inventory_required.issubset(inventory_header):
        raise ValueError("Canonical-inventory authority lacks required columns")
    inventory = read_columns(canonical_inventory, sorted(inventory_required))
    if len(inventory) != EXPECTED_CANONICAL_PHYSICAL_SOURCE_GROUPS:
        raise ValueError("Canonical physical-source group count mismatch")
    inventory["subject_id"] = inventory["subject_id"].map(canonical_id)
    inventory["study_id"] = inventory["study_id"].map(canonical_id)
    inventory["quarantine_required"] = inventory["quarantine_required"].map(
        _parse_restricted_bool
    )
    proposed = pd.to_numeric(
        inventory["n_proposed_canonical_rows"], errors="raise"
    ).astype(int)
    stage_inventory = inventory[
        inventory["components"].eq("stage_d")
        & inventory["source_availability"].eq("SURVIVING_NPZ")
        & ~inventory["quarantine_required"]
        & proposed.gt(0)
    ][["subject_id", "study_id"]].drop_duplicates()
    stage_group_count = int(
        (
            inventory["components"].eq("stage_d")
            & inventory["source_availability"].eq("SURVIVING_NPZ")
        ).sum()
    )
    if stage_group_count != EXPECTED_STAGE_D_SURVIVING_NPZ_GROUPS:
        raise ValueError("Stage-D surviving-NPZ physical-source count mismatch")
    stage_inventory = _selected_owned_pairs(
        stage_inventory,
        selected,
        allow_outside_selected=False,
    ).merge(
        embedded,
        on=["subject_id", "study_id"],
        how="inner",
        validate="one_to_one",
    )
    if stage_inventory.empty:
        raise ValueError("No Stage-D surviving-NPZ smoke candidates remain")

    def component_pairs(components: set[str]) -> pd.DataFrame:
        return embedded_meta[embedded_meta["component"].isin(components)][
            ["subject_id", "study_id"]
        ].drop_duplicates().reset_index(drop=True)

    candidates = {
        "batch_000_duplicate_affected": duplicate_pairs,
        "batch_000_cine_positive_fallback": component_pairs({"batch_000"}),
        "batch_001_008_cine_positive": component_pairs(
            {f"batch_{index:03d}" for index in range(1, 9)}
        ),
        "stage_d_cine_positive": stage_inventory.reset_index(drop=True),
        "train_no_cine_negative": pd.DataFrame(
            no_cine_pairs, columns=["subject_id", "study_id"]
        ),
    }
    if any(frame.empty for frame in candidates.values()):
        raise ValueError("At least one required technical smoke stratum is empty")
    return candidates


def _ranked_candidate(role: str, candidates: Iterable[tuple[str, str]]) -> tuple[str, str]:
    unique = sorted(set(candidates))
    if not unique:
        raise ValueError("A required smoke role has no eligible candidate")
    return min(
        unique,
        key=lambda pair: hashlib.sha256(
            f"{SELECTION_SALT}\0{role}\0{pair[0]}\0{pair[1]}".encode("utf-8")
        ).hexdigest(),
    )


def select_smoke(
    source: pd.DataFrame,
    candidates: Mapping[str, pd.DataFrame],
) -> tuple[pd.DataFrame, list[dict[str, object]]]:
    study_meta = source.drop_duplicates("study_id").set_index("study_id")
    positive_names = (
        "batch_000_duplicate_affected",
        "batch_000_cine_positive_fallback",
        "batch_001_008_cine_positive",
        "stage_d_cine_positive",
    )
    positive_studies = set().union(*(set(candidates[name]["study_id"]) for name in positive_names))
    if set(candidates["train_no_cine_negative"]["study_id"]) & positive_studies:
        raise ValueError("No-cine evidence overlaps cine-positive evidence")

    def eligible(name: str, components: set[str]) -> list[tuple[str, str]]:
        rows: list[tuple[str, str]] = []
        for subject_id, study_id in candidates[name][["subject_id", "study_id"]].itertuples(index=False, name=None):
            meta = study_meta.loc[study_id]
            if meta["split"] == "train" and meta["component"] in components:
                rows.append((subject_id, study_id))
        return rows

    preferred = eligible("batch_000_duplicate_affected", {"batch_000"})
    fallback = eligible("batch_000_cine_positive_fallback", {"batch_000"})
    if preferred:
        first_pool = preferred
        first_basis = "BATCH_000_DUPLICATE_AFFECTED"
        preferred_used = True
    else:
        first_pool = fallback
        first_basis = "BATCH_000_CINE_POSITIVE_FALLBACK"
        preferred_used = False

    specifications = [
        (
            "batch_000_duplicate_affected_or_prespecified_fallback",
            first_basis,
            first_pool,
            preferred_used,
        ),
        (
            "batch_001_008_historical_cine_positive",
            "BATCH_001_008_CINE_POSITIVE",
            eligible("batch_001_008_cine_positive", {f"batch_{i:03d}" for i in range(1, 9)}),
            False,
        ),
        (
            "stage_d_historical_cine_positive_with_surviving_npz",
            "STAGE_D_CINE_POSITIVE",
            eligible("stage_d_cine_positive", {"stage_d"}),
            False,
        ),
        (
            "historical_no_cine_negative_control",
            "TRAIN_NO_CINE_NEGATIVE",
            eligible("train_no_cine_negative", set(EXPECTED_COMPONENTS)),
            False,
        ),
    ]
    chosen: list[dict[str, object]] = []
    used: set[str] = set()
    for role, basis, pool, used_preferred in specifications:
        remaining = [pair for pair in pool if pair[1] not in used]
        subject_id, study_id = _ranked_candidate(role, remaining)
        used.add(study_id)
        chosen.append(
            {
                "smoke_role": role,
                "selection_basis": basis,
                "subject_id": subject_id,
                "study_id": study_id,
                "candidate_count": len(set(pool)),
                "preferred_evidence_used": used_preferred,
            }
        )
    if len(used) != MAX_SMOKE_STUDIES:
        raise ValueError("Smoke selection did not produce exactly four distinct studies")

    selection = pd.DataFrame(chosen)
    smoke = source.merge(
        selection[["smoke_role", "selection_basis", "subject_id", "study_id"]],
        on=["subject_id", "study_id"],
        how="inner",
        validate="many_to_one",
    )
    role_order = {name: index for index, name in enumerate(OUTPUT_ROLES)}
    smoke["_role_order"] = smoke["smoke_role"].map(role_order)
    smoke = smoke.sort_values(["_role_order", "source_relative_path"]).drop(columns="_role_order")
    if smoke["study_id"].nunique() != MAX_SMOKE_STUDIES or set(smoke["split"]) != {"train"}:
        raise ValueError("Smoke cohort is not exactly four train-only studies")
    if len(smoke) > MAX_SMOKE_OBJECTS:
        raise ValueError("Smoke cohort exceeds the locked 1000-object cap")
    return smoke.reset_index(drop=True), chosen


def _complete_sum(series: pd.Series) -> int | None:
    return int(series.sum()) if series.notna().all() else None


def dataframe_csv_sha256(frame: pd.DataFrame) -> str:
    payload = frame.to_csv(index=False, lineterminator="\n").encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def build_aggregate_outputs(
    selected: pd.DataFrame,
    source: pd.DataFrame,
    smoke: pd.DataFrame,
    chosen: Sequence[Mapping[str, object]],
) -> tuple[dict[str, object], pd.DataFrame, pd.DataFrame, dict[str, object]]:
    summary: dict[str, object] = {
        "status": "PASS",
        "schema_version": 1,
        "source_release": SOURCE_RELEASE,
        "source_bucket": SOURCE_BUCKET,
        "selected_authority_checksum_match": True,
        "n_selected_subjects": int(selected["subject_id"].nunique()),
        "n_selected_studies": int(selected["study_id"].nunique()),
        "n_source_components": int(source["component"].nunique()),
        "n_source_objects": int(len(source)),
        "n_source_studies": int(source["study_id"].nunique()),
        "n_outside_selected_source_studies": 0,
        "n_missing_selected_source_studies": 0,
        "source_paths_safe_and_normalized": True,
        "source_ownership_exact": True,
        "source_objects_unique": True,
        "source_object_counts_match_selected_authority": True,
        "smoke_n_roles": int(smoke["smoke_role"].nunique()),
        "smoke_n_studies": int(smoke["study_id"].nunique()),
        "smoke_n_objects": int(len(smoke)),
        "smoke_n_objects_with_known_bytes": int(smoke["expected_size_bytes"].notna().sum()),
        "smoke_total_expected_bytes_if_complete": _complete_sum(smoke["expected_size_bytes"]),
        "smoke_all_train": True,
        "smoke_study_cap": MAX_SMOKE_STUDIES,
        "smoke_object_cap": MAX_SMOKE_OBJECTS,
        "batch_000_preferred_evidence_used": bool(chosen[0]["preferred_evidence_used"]),
        "batch_000_selection_basis": str(chosen[0]["selection_basis"]),
        "outcomes_read": False,
        "predictions_read": False,
        "embedding_arrays_read": False,
        "performance_computed": False,
        "source_values_emitted_to_stdout": False,
    }
    component_rows = []
    for component in EXPECTED_COMPONENTS:
        group = source[source["component"] == component]
        component_rows.append(
            {
                "component": component,
                "n_source_studies": int(group["study_id"].nunique()),
                "n_source_objects": int(len(group)),
                "n_objects_with_known_bytes": int(group["expected_size_bytes"].notna().sum()),
                "total_expected_bytes_if_complete": _complete_sum(group["expected_size_bytes"]),
            }
        )
    role_rows = []
    chosen_by_role = {str(row["smoke_role"]): row for row in chosen}
    for role in OUTPUT_ROLES:
        group = smoke[smoke["smoke_role"] == role]
        metadata = chosen_by_role[role]
        role_rows.append(
            {
                "smoke_role": role,
                "selection_basis": metadata["selection_basis"],
                "source_component": str(group["component"].iloc[0]),
                "n_candidate_studies": int(metadata["candidate_count"]),
                "n_selected_studies": 1,
                "n_source_objects": int(len(group)),
                "n_objects_with_known_bytes": int(group["expected_size_bytes"].notna().sum()),
                "total_expected_bytes_if_complete": _complete_sum(group["expected_size_bytes"]),
                "preferred_evidence_used": bool(metadata["preferred_evidence_used"]),
            }
        )
    components = pd.DataFrame(component_rows)
    roles = pd.DataFrame(role_rows)
    safety = {
        "status": "PASS",
        "aggregate_contains_identifiers": False,
        "aggregate_contains_object_locators": False,
        "restricted_outputs_outside_repository": True,
        "smoke_hard_caps_passed": True,
        "outcome_blind_selection_passed": True,
    }
    assert_aggregate_safe_json(summary)
    assert_aggregate_safe_columns(components)
    assert_aggregate_safe_columns(roles)
    assert_aggregate_safe_json(safety)
    return summary, components, roles, safety


def _write_csv_atomic(frame: pd.DataFrame, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".tmp_", suffix=".csv", delete=False) as handle:
        temporary = Path(handle.name)
    try:
        frame.to_csv(temporary, index=False)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def _write_json_atomic(payload: object, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".tmp_", suffix=".json", delete=False) as handle:
        temporary = Path(handle.name)
    try:
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def execute(
    *,
    selected_path: Path,
    split_path: Path,
    record_components: Mapping[str, Path],
    candidate_evidence: Mapping[str, Path] | None,
    restricted_output_dir: Path,
    aggregate_output_dir: Path,
    historical_study_manifest: Path | None = None,
    duplicate_resolution: Path | None = None,
    canonical_inventory: Path | None = None,
    release_checksums: Path | None = None,
    expected_selected_sha256: str | None = SELECTED_AUTHORITY_SHA256,
    expected_selected_studies: int = EXPECTED_SELECTED_STUDIES,
    expected_input_sha256: Mapping[str, str] | None = None,
    expected_split_counts: Mapping[str, int] | None = None,
) -> dict[str, object]:
    authority_paths = _input_authority_paths(
        selected_path=selected_path,
        split_path=split_path,
        record_components=record_components,
        historical_study_manifest=historical_study_manifest,
        duplicate_resolution=duplicate_resolution,
        canonical_inventory=canonical_inventory,
    )
    locked_input_sha256 = (
        verify_locked_input_authorities(authority_paths, expected_input_sha256)
        if expected_input_sha256 is not None
        else None
    )
    if expected_selected_sha256 is not None and file_sha256(selected_path) != expected_selected_sha256:
        raise ValueError("Selected authority checksum mismatch")
    selected = load_selected(selected_path)
    if len(selected) != expected_selected_studies:
        raise ValueError("Selected authority row count mismatch")
    splits = load_splits(split_path, selected, expected_split_counts)
    source = load_record_components(record_components, selected, splits)
    provenance_inputs = (
        historical_study_manifest,
        duplicate_resolution,
        canonical_inventory,
    )
    if candidate_evidence is not None and any(value is not None for value in provenance_inputs):
        raise ValueError("Candidate evidence and provenance-derived modes are mutually exclusive")
    if candidate_evidence is not None:
        candidate_mode = "synthetic_or_dependency_light_test_only"
        candidates = load_candidate_tables(candidate_evidence, selected)
    elif all(value is not None for value in provenance_inputs):
        candidate_mode = "phase1d_restricted_provenance"
        assert historical_study_manifest is not None
        assert duplicate_resolution is not None
        assert canonical_inventory is not None
        candidates = derive_candidate_tables(
            selected=selected,
            source=source,
            historical_study_manifest=historical_study_manifest,
            duplicate_resolution=duplicate_resolution,
            canonical_inventory=canonical_inventory,
        )
    else:
        raise ValueError("Exactly one complete candidate-construction mode is required")
    if candidate_mode == "phase1d_restricted_provenance" and release_checksums is None:
        raise ValueError("Authoritative provenance mode requires release checksums")
    if release_checksums is not None:
        source = apply_release_checksums(source, release_checksums)
    smoke, chosen = select_smoke(source, candidates)
    summary, components, roles, safety = build_aggregate_outputs(selected, source, smoke, chosen)
    split_counts = {
        name: int(sum(value == name for value in splits.values()))
        for name in ("train", "val", "test")
    }
    summary.update(
        {
            "candidate_construction_mode": candidate_mode,
            "selection_salt": SELECTION_SALT,
            "selected_authority_sha256": file_sha256(selected_path),
            "split_map_sha256": file_sha256(split_path),
            "record_component_sha256": {
                name: file_sha256(record_components[name])
                for name in sorted(record_components)
            },
            "release_checksums_sha256": (
                file_sha256(release_checksums) if release_checksums is not None else None
            ),
            "historical_study_manifest_sha256": (
                file_sha256(historical_study_manifest)
                if historical_study_manifest is not None
                else None
            ),
            "duplicate_resolution_sha256": (
                file_sha256(duplicate_resolution)
                if duplicate_resolution is not None
                else None
            ),
            "canonical_inventory_sha256": (
                file_sha256(canonical_inventory)
                if canonical_inventory is not None
                else None
            ),
            "all_selected_objects_have_release_sha256": bool(
                source["expected_sha256"].notna().all()
            ),
            "split_counts": split_counts,
            "locked_split_counts_match": (
                expected_split_counts is not None
                and split_counts == dict(expected_split_counts)
            ),
            "restricted_input_authority_hash_set_exact": (
                locked_input_sha256 is not None
                and set(locked_input_sha256) == set(LOCKED_PRODUCTION_INPUT_SHA256)
            ),
            "restricted_input_authority_sha256": locked_input_sha256,
            "selected_source_manifest_sha256": dataframe_csv_sha256(source),
            "technical_smoke_source_manifest_sha256": dataframe_csv_sha256(smoke),
        }
    )
    safety["restricted_input_authority_hash_gate_passed"] = bool(
        summary["restricted_input_authority_hash_set_exact"]
    )
    safety["locked_split_counts_gate_passed"] = bool(
        summary["locked_split_counts_match"]
    )
    assert_aggregate_safe_json(summary)
    assert_aggregate_safe_json(safety)

    restricted_dir = require_restricted_path(restricted_output_dir)
    aggregate_dir = aggregate_output_dir.expanduser().resolve()
    if aggregate_dir == restricted_dir:
        raise ValueError("Aggregate and restricted output directories must differ")
    aggregate_dir.mkdir(parents=True, exist_ok=True)
    targets = {
        "source": restricted_dir / "selected_source_manifest_restricted.csv",
        "smoke": restricted_dir / "technical_smoke_source_manifest_restricted.csv",
        "summary": aggregate_dir / "reconstruction_source_manifest.summary.json",
        "components": aggregate_dir / "reconstruction_source_manifest_by_component.csv",
        "roles": restricted_dir / "technical_smoke_role_selection_restricted.csv",
        "safety": aggregate_dir / "reconstruction_source_manifest_safety_gate.json",
    }
    if any(path.exists() for path in targets.values()):
        raise ValueError("An output already exists; silent replacement is prohibited")
    _write_csv_atomic(source, targets["source"])
    _write_csv_atomic(smoke, targets["smoke"])
    if file_sha256(targets["source"]) != summary["selected_source_manifest_sha256"]:
        raise ValueError("Selected source manifest serialization hash mismatch")
    if file_sha256(targets["smoke"]) != summary["technical_smoke_source_manifest_sha256"]:
        raise ValueError("Technical smoke manifest serialization hash mismatch")
    _write_json_atomic(summary, targets["summary"])
    _write_csv_atomic(components, targets["components"])
    _write_csv_atomic(roles, targets["roles"])
    _write_json_atomic(safety, targets["safety"])
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selected-studies", type=Path, required=True)
    parser.add_argument("--split-map", type=Path, required=True)
    parser.add_argument("--record-component", action="append", required=True, metavar="NAME=PATH")
    parser.add_argument("--historical-study-manifest", type=Path, required=True)
    parser.add_argument("--duplicate-resolution", type=Path, required=True)
    parser.add_argument("--canonical-inventory", type=Path, required=True)
    parser.add_argument("--release-checksums", type=Path, required=True)
    parser.add_argument("--restricted-output-dir", type=Path, required=True)
    parser.add_argument("--aggregate-output-dir", type=Path, required=True)
    return parser.parse_args()


def _unique_named(values: Sequence[str]) -> dict[str, Path]:
    parsed = [parse_named_path(value) for value in values]
    names = [name for name, _ in parsed]
    if len(names) != len(set(names)):
        raise ValueError("Named manifest arguments contain duplicate names")
    return dict(parsed)


def main() -> int:
    args = parse_args()
    summary = execute(
        selected_path=args.selected_studies,
        split_path=args.split_map,
        record_components=_unique_named(args.record_component),
        candidate_evidence=None,
        restricted_output_dir=args.restricted_output_dir,
        aggregate_output_dir=args.aggregate_output_dir,
        historical_study_manifest=args.historical_study_manifest,
        duplicate_resolution=args.duplicate_resolution,
        canonical_inventory=args.canonical_inventory,
        release_checksums=args.release_checksums,
        expected_input_sha256=LOCKED_PRODUCTION_INPUT_SHA256,
        expected_split_counts=EXPECTED_SPLIT_COUNTS,
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(run_guarded(main))
