#!/usr/bin/env python3
"""Body-free readers for fixed R8U-R7C batch and cache evidence.

Only JSON, CSV/TSV manifests, hashes already recorded in those manifests, and
file metadata are consumed.  In particular, this module never opens a DICOM,
NPZ, embedding, model, prediction, or result body.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
from typing import Any, Final, Mapping, Sequence


SCRIPT_ROOT: Final = Path(__file__).resolve().parent

import finalize_lvef_c3_production as finalizer
import lvef_c3_orchestration_core as core
import lvef_c3_r8u_r7c_accounting as accounting
import lvef_c3_r8u_r7c_metadata as metadata


PRODUCTION_ROOT: Final = accounting.R7C_ROOT.parents[2]
ATTEMPT_ROOT: Final = accounting.R7C_ROOT.parent
PLAN_PATH: Final = ATTEMPT_ROOT / "full_batch_plan.restricted.json"
FAILED_PARTIAL_SEAL_PATH: Final = (
    ATTEMPT_ROOT / "r8u_r2_batch16_recovery/failed_partial_seal.restricted.json"
)
MAX_PLAN_BYTES: Final = 512 * 1024 * 1024
MAX_METADATA_BYTES: Final = 64 * 1024 * 1024
MAX_MANIFEST_BYTES: Final = 256 * 1024 * 1024
MAX_LEDGER_BYTES: Final = 256 * 1024 * 1024
SHA_RE: Final = re.compile(r"^[0-9a-f]{64}$")
NONNEGATIVE_INTEGER_RE: Final = re.compile(r"^(?:0|[1-9][0-9]*)$")
METADATA_SUFFIXES: Final = frozenset({".json", ".csv", ".tsv"})
BATCH16_FINAL_RECEIPT_SHA256: Final = (
    "63b002947814e92c616d0eb7f74ca334cba4e77cdc17f7ce2b55cfc51e090439"
)
PREFIX_FINAL_RECEIPT_SHA256: Final = tuple(
    item[2] for item in accounting.r7.R8U_PREFIX_RECEIPT_AUTHORITIES
) + (BATCH16_FINAL_RECEIPT_SHA256,)
FAILED_PARTIAL_ROOT: Final = (
    ATTEMPT_ROOT / "extracted_cache/c3_batch_015/dicom_extraction.partial"
)
R7_TERMINAL_RECEIPT_PATH: Final = (
    ATTEMPT_ROOT
    / "r8u_r7_batch16_preservation_recovery/"
    "preservation_recovery_terminal.aggregate_safe.json"
)
ORIGINAL_COHORT_RECEIPT_PATH: Final = (
    ATTEMPT_ROOT
    / "cohort_finalization/full_c3_finalization.aggregate_safe.json"
)

STAGE_RECEIPT_KEYS: Final = frozenset(
    {
        "schema_version",
        "artifact_type",
        "status",
        "stage",
        "batch_id",
        "attempt_id",
        "runtime_authority",
        "input_manifest_sha256",
        "artifacts",
    }
)
AUTHORIZATION_KEYS: Final = frozenset(
    {
        "schema_version",
        "artifact_type",
        "status",
        "authorization_scope",
        "owner_authorized",
        "owner_authorization_date_utc",
        "batch_id",
        "attempt_id",
        "authority_sha256",
        "preservation_receipt_sha256",
        "cache_inventory_sha256",
        "launch_authority_sha256",
    }
)
INTENT_KEYS: Final = frozenset(
    {
        "schema_version",
        "artifact_type",
        "status",
        "batch_id",
        "attempt_id",
        "governing_commit",
        "preservation_receipt_sha256",
        "authorization_receipt_sha256",
        "cache_tree_sha256",
        "raw_dicom_deletion_permitted",
    }
)
STAGED_KEYS: Final = frozenset(
    {
        "schema_version",
        "artifact_type",
        "status",
        "batch_id",
        "attempt_id",
        "governing_commit",
        "intent_receipt_sha256",
        "cache_tree_sha256",
        "atomic_same_filesystem_rename_completed",
        "raw_dicom_deletion_permitted",
    }
)


class R7CEvidenceError(RuntimeError):
    """One stable fail-closed evidence error."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    raise R7CEvidenceError(code)


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _strict_json_bytes(payload: bytes, code: str) -> dict[str, Any]:
    def pairs(items: Sequence[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in items:
            if key in value:
                _fail(f"{code}_DUPLICATE_KEY")
            value[key] = item
        return value

    def reject(_value: str) -> Any:
        _fail(f"{code}_INVALID_CONSTANT")

    try:
        value = json.loads(
            payload.decode("utf-8", "strict"),
            object_pairs_hook=pairs,
            parse_constant=reject,
        )
    except R7CEvidenceError:
        raise
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise R7CEvidenceError(f"{code}_INVALID_JSON") from exc
    if not isinstance(value, dict):
        _fail(f"{code}_NOT_OBJECT")
    return value


def _read_regular_metadata_bytes(
    path: Path, *, maximum_bytes: int = MAX_METADATA_BYTES
) -> bytes:
    """Read one bounded, stable, no-follow metadata file."""

    # Keep the only body-reading primitive closed over control-plane metadata.
    # Scientific payloads are inspected by lstat at most, never opened here.
    if path.suffix.lower() not in METADATA_SUFFIXES:
        _fail("R8U_R7C_SCIENTIFIC_BODY_READ_PROHIBITED")
    descriptor = -1
    try:
        before_visible = os.lstat(path)
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_NONBLOCK", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        before = os.fstat(descriptor)
        identity = lambda value: (
            value.st_dev,
            value.st_ino,
            value.st_mode,
            value.st_uid,
            value.st_gid,
            value.st_nlink,
            value.st_size,
            value.st_mtime_ns,
            value.st_ctime_ns,
        )
        if (
            not stat.S_ISREG(before.st_mode)
            or stat.S_ISLNK(before_visible.st_mode)
            or before.st_uid != os.geteuid()
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_size < 1
            or before.st_size > maximum_bytes
            or identity(before) != identity(before_visible)
        ):
            _fail("R8U_R7C_METADATA_FILE_INVALID")
        blocks: list[bytes] = []
        remaining = before.st_size
        while remaining:
            block = os.read(descriptor, min(remaining, 1024 * 1024))
            if not block:
                _fail("R8U_R7C_METADATA_FILE_INVALID")
            blocks.append(block)
            remaining -= len(block)
        after = os.fstat(descriptor)
        after_visible = os.lstat(path)
        if identity(before) != identity(after) or identity(after) != identity(after_visible):
            _fail("R8U_R7C_METADATA_FILE_CHANGED")
        return b"".join(blocks)
    except R7CEvidenceError:
        raise
    except OSError as exc:
        raise R7CEvidenceError("R8U_R7C_METADATA_FILE_INVALID") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _read_json(
    path: Path, code: str, *, maximum_bytes: int = MAX_METADATA_BYTES
) -> tuple[dict[str, Any], bytes]:
    payload = _read_regular_metadata_bytes(path, maximum_bytes=maximum_bytes)
    return _strict_json_bytes(payload, code), payload


def load_fixed_plan() -> dict[str, Any]:
    """Load the immutable plan and require its exact fixed byte/canonical hash."""

    plan, payload = _read_json(PLAN_PATH, "R8U_R7C_PLAN", maximum_bytes=MAX_PLAN_BYTES)
    if (
        _sha256(payload) != accounting.PLAN_SHA256
        or metadata.canonical_json_sha256(plan) != accounting.PLAN_SHA256
        or plan.get("schema_version") != 3
        or plan.get("artifact_type")
        != "lvef_c3_restricted_immutable_batch_plan_v3"
        or not isinstance(plan.get("batches"), list)
        or len(plan["batches"]) != 19
    ):
        _fail("R8U_R7C_PLAN_AUTHORITY_INVALID")
    return plan


def batch_final_receipt_path(ordinal: int) -> Path:
    if type(ordinal) is not int or ordinal not in range(19):
        _fail("R8U_R7C_BATCH_ORDINAL_INVALID")
    return (
        ATTEMPT_ROOT
        / "batches"
        / f"c3_batch_{ordinal:03d}"
        / "preservation/batch_finalization_receipt.restricted.json"
    )


def _manifest_rows(path: Path, expected_sha256: str) -> list[dict[str, str]]:
    payload = _read_regular_metadata_bytes(
        path, maximum_bytes=MAX_MANIFEST_BYTES
    )
    if _sha256(payload) != expected_sha256:
        _fail("R8U_R7C_PRESERVATION_MANIFEST_HASH_MISMATCH")
    try:
        decoded = payload.decode("utf-8", "strict")
        reader = csv.DictReader(io.StringIO(decoded, newline=""), delimiter="\t")
        if reader.fieldnames != ["relative_path", "size_bytes", "sha256", "role"]:
            _fail("R8U_R7C_PRESERVATION_MANIFEST_SCHEMA_INVALID")
        rows = list(reader)
    except (UnicodeError, csv.Error) as exc:
        raise R7CEvidenceError(
            "R8U_R7C_PRESERVATION_MANIFEST_SCHEMA_INVALID"
        ) from exc
    seen: set[str] = set()
    for row in rows:
        relative = str(row.get("relative_path", ""))
        pure = PurePosixPath(relative)
        try:
            size = int(str(row.get("size_bytes", "")))
        except ValueError as exc:
            raise R7CEvidenceError(
                "R8U_R7C_PRESERVATION_MANIFEST_SCHEMA_INVALID"
            ) from exc
        if (
            set(row) != {"relative_path", "size_bytes", "sha256", "role"}
            or not relative
            or pure.is_absolute()
            or any(part in {"", ".", ".."} for part in pure.parts)
            or relative in seen
            or NONNEGATIVE_INTEGER_RE.fullmatch(
                str(row.get("size_bytes", ""))
            )
            is None
            or size < 0
            or SHA_RE.fullmatch(str(row.get("sha256", ""))) is None
            or not str(row.get("role", ""))
        ):
            _fail("R8U_R7C_PRESERVATION_MANIFEST_SCHEMA_INVALID")
        seen.add(relative)
    return rows


def _validate_manifest_authority(
    rows: Sequence[Mapping[str, str]],
    *,
    batch_id: str,
    planned: Mapping[str, Any],
) -> None:
    """Validate the sealed manifest without touching raw or NPZ bodies.

    This is intentionally a receipt/manifest reconciliation, not a filesystem
    walk.  In particular the historical Batches 1--16 raw trees are never
    rescanned.
    """

    prefix = f"attempts/{accounting.ATTEMPT_ID}"
    role_prefixes = {
        "raw_dicom_and_download_authority": f"{prefix}/raw/{batch_id}/",
        "dicom_extraction_metadata_retained": (
            f"{prefix}/extracted_cache/{batch_id}/dicom_extraction/"
        ),
        "extracted_npz_cache_owner_retirable": (
            f"{prefix}/extracted_cache/{batch_id}/dicom_extraction/clips/"
        ),
        "embedding_and_pooling_retained": (
            f"{prefix}/batches/{batch_id}/echoprime/"
        ),
    }
    exact_ledgers = {
        "download_ledger": (
            f"{prefix}/batches/{batch_id}/download_resume_ledger.restricted.json"
        ),
        "extraction_ledger": (
            f"{prefix}/batches/{batch_id}/extraction_resume_ledger.restricted.json"
        ),
        "pooling_ledger": (
            f"{prefix}/batches/{batch_id}/pooling_resume_ledger.restricted.json"
        ),
    }
    objects = planned.get("objects")
    if not isinstance(objects, list):
        _fail("R8U_R7C_PRESERVATION_MANIFEST_PLAN_INVALID")
    expected_raw: dict[str, int] = {}
    for item in objects:
        if not isinstance(item, Mapping):
            _fail("R8U_R7C_PRESERVATION_MANIFEST_PLAN_INVALID")
        source_key = str(item.get("source_object_key", ""))
        size = item.get("size_bytes")
        if (
            SHA_RE.fullmatch(source_key) is None
            or type(size) is not int
            or size < 1
        ):
            _fail("R8U_R7C_PRESERVATION_MANIFEST_PLAN_INVALID")
        relative = (
            f"{role_prefixes['raw_dicom_and_download_authority']}"
            f"objects/{source_key}.dcm"
        )
        if relative in expected_raw:
            _fail("R8U_R7C_PRESERVATION_MANIFEST_PLAN_INVALID")
        expected_raw[relative] = size

    observed_ledgers: set[str] = set()
    observed_raw: dict[str, int] = {}
    for row in rows:
        relative = str(row["relative_path"])
        role = str(row["role"])
        if role not in finalizer.PRESERVATION_ROLES:
            _fail("R8U_R7C_PRESERVATION_ROLE_INVALID")
        if role in exact_ledgers:
            if relative != exact_ledgers[role] or role in observed_ledgers:
                _fail("R8U_R7C_PRESERVATION_ROLE_PATH_INVALID")
            observed_ledgers.add(role)
            continue
        expected_prefix = role_prefixes.get(role)
        if expected_prefix is None or not relative.startswith(expected_prefix):
            _fail("R8U_R7C_PRESERVATION_ROLE_PATH_INVALID")
        if role == "dicom_extraction_metadata_retained" and relative.startswith(
            role_prefixes["extracted_npz_cache_owner_retirable"]
        ):
            _fail("R8U_R7C_PRESERVATION_ROLE_PATH_INVALID")
        if role == "extracted_npz_cache_owner_retirable":
            suffix = relative[len(expected_prefix) :]
            if not suffix or not suffix.lower().endswith(".npz"):
                _fail("R8U_R7C_PRESERVATION_ROLE_PATH_INVALID")
        if role == "raw_dicom_and_download_authority":
            objects_prefix = f"{expected_prefix}objects/"
            if relative.startswith(objects_prefix):
                if not relative.lower().endswith(".dcm"):
                    _fail("R8U_R7C_RAW_SOURCE_AUTHORITY_INVALID")
                if relative in observed_raw:
                    _fail("R8U_R7C_RAW_SOURCE_AUTHORITY_INVALID")
                observed_raw[relative] = int(row["size_bytes"])

    if observed_ledgers != set(exact_ledgers) or observed_raw != expected_raw:
        _fail("R8U_R7C_PRESERVATION_COVERAGE_INVALID")
    if (
        sum(observed_raw.values()) != planned.get("source_bytes")
        or len(observed_raw) != planned.get("n_objects")
    ):
        _fail("R8U_R7C_RAW_SOURCE_AUTHORITY_INVALID")


def _row_by_relative(
    rows: Sequence[Mapping[str, str]], relative: str, *, role: str
) -> Mapping[str, str]:
    matches = [row for row in rows if row.get("relative_path") == relative]
    if len(matches) != 1 or matches[0].get("role") != role:
        _fail("R8U_R7C_PRESERVATION_COVERAGE_INVALID")
    return matches[0]


def _require_metadata_row_matches_file(
    rows: Sequence[Mapping[str, str]], path: Path, *, role: str, content_hash: bool
) -> str:
    try:
        relative = path.relative_to(PRODUCTION_ROOT).as_posix()
        row = _row_by_relative(rows, relative, role=role)
        info = os.lstat(path)
    except (OSError, ValueError) as exc:
        raise R7CEvidenceError("R8U_R7C_PRESERVATION_COVERAGE_INVALID") from exc
    if (
        stat.S_ISLNK(info.st_mode)
        or not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.geteuid()
        or info.st_nlink != 1
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_size != int(row["size_bytes"])
    ):
        _fail("R8U_R7C_PRESERVATION_COVERAGE_INVALID")
    if content_hash:
        observed = _sha256(_read_regular_metadata_bytes(path))
        if observed != row["sha256"]:
            _fail("R8U_R7C_PRESERVATION_COVERAGE_INVALID")
    return str(row["sha256"])


def _retired_cache_projection(
    rows: Sequence[Mapping[str, str]], *, batch_id: str, expected_tree_sha256: str,
    expected_files: int, inspect_cache_root: bool,
) -> tuple[int, str]:
    cache_prefix = (
        PurePosixPath("attempts")
        / accounting.ATTEMPT_ID
        / "extracted_cache"
        / batch_id
        / "dicom_extraction/clips"
    )
    records: list[str] = []
    total_bytes = 0
    for row in rows:
        if row.get("role") != "extracted_npz_cache_owner_retirable":
            continue
        pure = PurePosixPath(str(row["relative_path"]))
        try:
            relative = pure.relative_to(cache_prefix)
        except ValueError:
            _fail("R8U_R7C_RETIRED_CACHE_MANIFEST_INVALID")
        if pure.suffix.lower() != ".npz" or not relative.parts:
            _fail("R8U_R7C_RETIRED_CACHE_MANIFEST_INVALID")
        size = int(row["size_bytes"])
        total_bytes += size
        records.append(f"{relative.as_posix()}\t{size}\t{row['sha256']}")
    tree_sha = _sha256(("\n".join(sorted(records)) + "\n").encode("utf-8"))
    if (
        len(records) != expected_files
        or SHA_RE.fullmatch(expected_tree_sha256) is None
        or tree_sha != expected_tree_sha256
    ):
        _fail("R8U_R7C_RETIRED_CACHE_TREE_MISMATCH")
    cache_root = PRODUCTION_ROOT / Path(*cache_prefix.parts)
    if inspect_cache_root and os.path.lexists(cache_root):
        _fail("R8U_R7C_FINALIZED_EXTRACTION_CACHE_PRESENT")
    return total_bytes, tree_sha


def _validate_stage_receipt(
    value: Mapping[str, Any], *, batch_id: str, stage: str,
    required_artifacts: Mapping[str, str],
) -> None:
    artifacts = value.get("artifacts")
    if (
        set(value) != STAGE_RECEIPT_KEYS
        or value.get("schema_version") != 1
        or value.get("artifact_type") != "lvef_c3_stage_completion_receipt_v1"
        or not isinstance(artifacts, Mapping)
        or SHA_RE.fullmatch(str(value.get("input_manifest_sha256"))) is None
        or not isinstance(value.get("runtime_authority"), Mapping)
    ):
        _fail("R8U_R7C_STAGE_RECEIPT_INVALID")
    if value.get("status") != "PASS_STAGE_OUTPUT_ATOMICALLY_FINALIZABLE":
        _fail("R8U_R7C_STAGE_RECEIPT_PROVEN_FAILURE")
    if (
        value.get("attempt_id") != accounting.ATTEMPT_ID
        or value.get("batch_id") != batch_id
        or value.get("stage") != stage
        or dict(artifacts) != dict(required_artifacts)
        or value["runtime_authority"].get("git_commit")
        != accounting.SCIENTIFIC_COMMIT
        or value["runtime_authority"].get("batch_plan_sha256")
        != accounting.PLAN_SHA256
    ):
        _fail("R8U_R7C_STAGE_RECEIPT_RECONCILIATION_INVALID")
    try:
        runtime = core.validate_runtime_authority(value["runtime_authority"])
    except Exception as exc:
        raise R7CEvidenceError(
            "R8U_R7C_STAGE_RECEIPT_RECONCILIATION_INVALID"
        ) from exc
    if runtime != value["runtime_authority"]:
        _fail("R8U_R7C_STAGE_RECEIPT_RECONCILIATION_INVALID")


def _manifest_digest_for_path(
    rows: Sequence[Mapping[str, str]], path: Path, *, role: str
) -> str:
    try:
        relative = path.relative_to(PRODUCTION_ROOT).as_posix()
    except ValueError as exc:
        raise R7CEvidenceError(
            "R8U_R7C_PRESERVATION_COVERAGE_INVALID"
        ) from exc
    return str(_row_by_relative(rows, relative, role=role)["sha256"])


def _validate_extraction_summary(
    value: Mapping[str, Any],
    *,
    receipt: Mapping[str, Any],
    planned: Mapping[str, Any],
) -> None:
    path_count_keys = (
        "n_ordinary_preprocessing_path",
        "n_spatial_fallback_preprocessing_path",
        "n_temporal_fallback_preprocessing_path",
        "n_spatial_temporal_fallback_preprocessing_path",
    )
    expected_status = (
        "PASS_EXTRACTION_WITH_OBJECT_TECHNICAL_DISPOSITIONS"
        if receipt["n_object_technical_dispositions"]
        else "PASS_EXTRACTION_ALL_OBJECTS_EMBEDDABLE"
    )
    expected_pairs = {
        "n_objects": "n_expected_objects",
        "n_readable": "n_dicom_readable",
        "n_unreadable": "n_dicom_unreadable",
        "n_multiframe_candidates": "n_multiframe_cines",
        "n_single_frame": "n_single_frame_objects",
        "n_extracted_clips": "n_extracted_clips",
        "n_successfully_extracted_cines": "n_successfully_extracted_cines",
        "n_successfully_extracted_clips": "n_successfully_extracted_cines",
        "n_object_technical_dispositions": "n_object_technical_dispositions",
        "n_blocking_failures": "n_blocking_failures",
        "n_studies_affected_by_technical_disposition": (
            "n_studies_affected_by_technical_disposition"
        ),
        "n_new_no_cine_studies": "n_new_no_cine_studies",
        "object_substitution_count": "object_substitution_count",
        "unaccounted_multiframe_objects": "unaccounted_multiframe_objects",
    }
    if (
        set(value)
        != finalizer.production_stages.DICOM_EXTRACTION_SUMMARY_KEYS_V2
        or value.get("schema_version") != 2
        or value.get("artifact_type")
        != "lvef_c3_batch_dicom_extraction_summary_v2"
        or value.get("status") != expected_status
        or value.get("n_studies") != planned.get("n_studies")
        or any(
            value.get(summary_key) != receipt.get(receipt_key)
            for summary_key, receipt_key in expected_pairs.items()
        )
        or any(type(value.get(key)) is not int or value[key] < 0 for key in path_count_keys)
        or sum(int(value[key]) for key in path_count_keys)
        != receipt["n_successfully_extracted_cines"]
        or value.get("n_pixel_decode_failures") != 0
        or value.get("n_fallback_path_failed") != 0
        or value.get("technical_disposition_counts_by_class")
        != receipt.get("technical_disposition_counts_by_class")
        or value.get("technical_disposition_policy_version")
        != receipt.get("technical_disposition_policy_version")
        or value.get("technical_disposition_manifest_sha256")
        != receipt.get("technical_disposition_manifest_sha256")
        or any(
            value.get(key) is not True
            for key in (
                "physical_source_keys_unique",
                "clip_keys_unique",
                "all_shapes_and_dtypes_valid",
                "all_pixel_decodes_passed",
                "all_fallback_encoder_visible_signal_gates_passed",
                "all_extraction_rows_resolved",
                "all_successful_extractions_embeddable",
                "all_technical_dispositions_retained",
                "all_failure_substages_none",
                "all_source_signal_gates_passed",
                "all_post_crop_signal_gates_passed",
                "all_sampled_signal_gates_passed",
            )
        )
        or value.get("identifiers_emitted") is not False
        or value.get("paths_emitted") is not False
    ):
        _fail("R8U_R7C_EXTRACTION_SUMMARY_RECONCILIATION_INVALID")


def _validate_retirement_chain(
    *,
    batch_id: str,
    receipt_sha256: str,
    preservation_sha256: str,
    retired_tree_sha256: str,
    authorization: Mapping[str, Any],
    authorization_sha256: str,
    intent: Mapping[str, Any],
    intent_sha256: str,
    staged: Mapping[str, Any],
    staged_sha256: str,
    expected_staged_sha256: str,
    transition: Mapping[str, Any],
    final_ledger: Mapping[str, Any],
    expected_runtime_authority: Mapping[str, Any],
    expected_object_keys: set[str],
) -> None:
    authority_sha256 = core.canonical_json_sha256(expected_runtime_authority)
    if (
        set(authorization) != AUTHORIZATION_KEYS
        or authorization.get("schema_version") != 2
        or authorization.get("artifact_type")
        != "lvef_c3_cache_retirement_owner_authorization_v2"
    ):
        _fail("R8U_R7C_CACHE_AUTHORIZATION_INVALID")
    if (
        authorization.get("status") != "AUTHORIZED_EXTRACTED_CACHE_RETIREMENT"
        or authorization.get("owner_authorized") is not True
    ):
        _fail("R8U_R7C_CACHE_RETIREMENT_PROVEN_FAILURE")
    if (
        authorization.get("authorization_scope")
        != "EXTRACTED_CACHE_RETIREMENT"
        or finalizer.TIMESTAMP_RE.fullmatch(
            str(authorization.get("owner_authorization_date_utc"))
        )
        is None
        or authorization.get("batch_id") != batch_id
        or authorization.get("attempt_id") != accounting.ATTEMPT_ID
        or authorization.get("authority_sha256") != authority_sha256
        or authorization.get("preservation_receipt_sha256")
        != preservation_sha256
        or authorization.get("cache_inventory_sha256")
        != retired_tree_sha256
        or SHA_RE.fullmatch(
            str(authorization.get("launch_authority_sha256"))
        )
        is None
    ):
        _fail("R8U_R7C_CACHE_AUTHORIZATION_INVALID")

    if (
        set(intent) != INTENT_KEYS
        or intent.get("schema_version") != 1
        or intent.get("artifact_type") != "lvef_c3_cache_retirement_intent_v1"
    ):
        _fail("R8U_R7C_CACHE_INTENT_INVALID")
    if intent.get("status") != "AUTHORIZED_INTENT_RECORDED_NOT_RETIRED":
        _fail("R8U_R7C_CACHE_RETIREMENT_PROVEN_FAILURE")
    if intent != {
            "schema_version": 1,
            "artifact_type": "lvef_c3_cache_retirement_intent_v1",
            "status": "AUTHORIZED_INTENT_RECORDED_NOT_RETIRED",
            "batch_id": batch_id,
            "attempt_id": accounting.ATTEMPT_ID,
            "governing_commit": accounting.SCIENTIFIC_COMMIT,
            "preservation_receipt_sha256": preservation_sha256,
            "authorization_receipt_sha256": authorization_sha256,
            "cache_tree_sha256": retired_tree_sha256,
            "raw_dicom_deletion_permitted": False,
        }:
        _fail("R8U_R7C_CACHE_INTENT_INVALID")

    if (
        set(staged) != STAGED_KEYS
        or staged.get("schema_version") != 1
        or staged.get("artifact_type") != "lvef_c3_cache_atomically_staged_v1"
    ):
        _fail("R8U_R7C_CACHE_STAGED_INVALID")
    if staged.get("status") != "CACHE_ATOMICALLY_STAGED":
        _fail("R8U_R7C_CACHE_RETIREMENT_PROVEN_FAILURE")
    if staged != {
            "schema_version": 1,
            "artifact_type": "lvef_c3_cache_atomically_staged_v1",
            "status": "CACHE_ATOMICALLY_STAGED",
            "batch_id": batch_id,
            "attempt_id": accounting.ATTEMPT_ID,
            "governing_commit": accounting.SCIENTIFIC_COMMIT,
            "intent_receipt_sha256": intent_sha256,
            "cache_tree_sha256": retired_tree_sha256,
            "atomic_same_filesystem_rename_completed": True,
            "raw_dicom_deletion_permitted": False,
        }:
        _fail("R8U_R7C_CACHE_STAGED_INVALID")

    batches = final_ledger.get("batches")
    batch_ledger = batches.get(batch_id) if isinstance(batches, Mapping) else None
    events = batch_ledger.get("events") if isinstance(batch_ledger, Mapping) else None
    if (
        set(transition) != core.RECEIPT_KEYS
        or transition.get("schema_version") != 2
        or transition.get("receipt_type") != "lvef_c3_state_transition_v2"
    ):
        _fail("R8U_R7C_CACHE_TRANSITION_INVALID")
    if transition.get("status") != "PASS" or transition.get("to_state") != "FINALIZED":
        _fail("R8U_R7C_CACHE_RETIREMENT_PROVEN_FAILURE")
    if (
        transition.get("from_state") != "CACHE_RETIREMENT_ELIGIBLE"
        or transition.get("batch_id") != batch_id
        or transition.get("attempt_id") != accounting.ATTEMPT_ID
        or transition.get("authority") != expected_runtime_authority
        or transition.get("output_manifest_sha256") != receipt_sha256
        or not isinstance(events, list)
        or len(events) < 2
        or transition.get("input_receipt_sha256")
        != [events[-2].get("receipt_sha256")]
        or events[-1].get("receipt_sha256")
        != core.canonical_json_sha256(transition)
    ):
        _fail("R8U_R7C_CACHE_TRANSITION_INVALID")

    try:
        core.validate_resume_authority(
            final_ledger,
            expected_authority=expected_runtime_authority,
            attempt_id=accounting.ATTEMPT_ID,
            expected_object_keys={batch_id: expected_object_keys},
        )
    except Exception as exc:
        raise R7CEvidenceError("R8U_R7C_FINAL_LEDGER_INVALID") from exc
    if (
        final_ledger.get("status") != "COMPLETE"
        or not isinstance(batch_ledger, Mapping)
        or batch_ledger.get("state") != "FINALIZED"
    ):
        _fail("R8U_R7C_FINAL_LEDGER_PROVEN_FAILURE")

    # The final receipt itself binds the one canonical staged receipt.
    if staged_sha256 != expected_staged_sha256:
        _fail("R8U_R7C_CACHE_STAGED_INVALID")


def load_batch_metadata(
    ordinal: int, *, plan: Mapping[str, Any]
) -> dict[str, Any]:
    """Derive one strict body-free batch projection from its sealed chain."""

    if type(ordinal) is not int or ordinal not in range(19):
        _fail("R8U_R7C_BATCH_ORDINAL_INVALID")
    batch_id = f"c3_batch_{ordinal:03d}"
    batches = plan.get("batches") if isinstance(plan, Mapping) else None
    if not isinstance(batches, list) or len(batches) != 19:
        _fail("R8U_R7C_PLAN_AUTHORITY_INVALID")
    planned = batches[ordinal]
    if not isinstance(planned, Mapping) or planned.get("batch_id") != batch_id:
        _fail("R8U_R7C_PLAN_AUTHORITY_INVALID")
    receipt_path = batch_final_receipt_path(ordinal)
    try:
        receipt, receipt_payload = _read_json(receipt_path, "R8U_R7C_BATCH_RECEIPT")
        finalizer._validate_current_receipt_v3(receipt)
    except R7CEvidenceError:
        raise
    except Exception as exc:
        code = str(exc)
        destination = (
            "R8U_R7C_BATCH_RECEIPT_INVALID"
            if code
            in {
                "BATCH_RECEIPT_SCHEMA_MISMATCH",
                "BATCH_RECEIPT_VERSION_MISMATCH",
            }
            else "R8U_R7C_BATCH_FINALIZATION_PROVEN_FAILURE"
        )
        raise R7CEvidenceError(destination) from exc
    receipt_sha = _sha256(receipt_payload)
    if (
        (ordinal < 16 and receipt_sha != PREFIX_FINAL_RECEIPT_SHA256[ordinal])
        or receipt.get("attempt_id") != accounting.ATTEMPT_ID
        or receipt.get("batch_id") != batch_id
        or receipt.get("governing_commit") != accounting.SCIENTIFIC_COMMIT
        or receipt.get("batch_plan_sha256") != accounting.PLAN_SHA256
        or receipt.get("n_selected_studies") != planned.get("n_studies")
        or receipt.get("n_selected_subjects") != planned.get("n_subjects")
        or receipt.get("n_expected_objects") != planned.get("n_objects")
        or receipt.get("expected_source_bytes") != planned.get("source_bytes")
        or receipt.get("prespecified_no_cine_study_set_sha256")
        != planned.get("prespecified_no_cine_study_set_sha256")
        or receipt.get("n_no_cine_studies")
        != planned.get("expected_no_cine_studies")
        or receipt.get("n_dicom_unreadable") != 0
        or receipt.get("raw_dicoms_retained") is not True
        or receipt.get("extracted_cache_retired") is not True
    ):
        _fail("R8U_R7C_BATCH_RECEIPT_PLAN_MISMATCH")

    batch_root = receipt_path.parents[1]
    preservation_root = receipt_path.parent
    extraction_root = (
        ATTEMPT_ROOT / "extracted_cache" / batch_id / "dicom_extraction"
    )
    preservation_path = preservation_root / "batch_preservation_receipt.restricted.json"
    preservation, preservation_payload = _read_json(
        preservation_path, "R8U_R7C_PRESERVATION_RECEIPT"
    )
    if (
        set(preservation) != set(finalizer.PRESERVATION_ELIGIBILITY_RECEIPT_KEYS)
        or preservation.get("artifact_type")
        != "lvef_c3_batch_preservation_eligibility_receipt_v3"
        or preservation.get("schema_version") != 2
    ):
        _fail("R8U_R7C_PRESERVATION_RECEIPT_INVALID")
    if preservation.get("status") != "PASS_BATCH_CACHE_RETIREMENT_ELIGIBLE":
        _fail("R8U_R7C_PRESERVATION_PROVEN_FAILURE")
    if (
        preservation.get("batch_id") != batch_id
        or preservation.get("attempt_id") != accounting.ATTEMPT_ID
        or preservation.get("extracted_cache_retired") is not False
        or any(
            preservation.get(key) != receipt.get(key)
            for key in preservation
            if key not in {"artifact_type", "status", "extracted_cache_retired"}
        )
    ):
        _fail("R8U_R7C_PRESERVATION_RECEIPT_RECONCILIATION_INVALID")
    preservation_sha = _sha256(preservation_payload)

    manifest_path = preservation_root / "batch_preservation_manifest.restricted.tsv"
    rows = _manifest_rows(manifest_path, str(receipt["preservation_manifest_sha256"]))
    _validate_manifest_authority(rows, batch_id=batch_id, planned=planned)
    extraction_summary_path = extraction_root / "dicom_extraction.summary.json"
    extraction_summary, summary_payload = _read_json(
        extraction_summary_path, "R8U_R7C_EXTRACTION_SUMMARY"
    )
    summary_sha = _sha256(summary_payload)
    if _require_metadata_row_matches_file(
        rows,
        extraction_summary_path,
        role="dicom_extraction_metadata_retained",
        content_hash=True,
    ) != summary_sha:
        _fail("R8U_R7C_EXTRACTION_SUMMARY_HASH_MISMATCH")

    extraction_stage_path = extraction_root / "stage_completion_receipt.restricted.json"
    extraction_stage, extraction_stage_payload = _read_json(
        extraction_stage_path, "R8U_R7C_EXTRACTION_STAGE_RECEIPT"
    )
    extraction_stage_sha = _sha256(extraction_stage_payload)
    verified_download_manifest_path = (
        ATTEMPT_ROOT
        / "raw"
        / batch_id
        / "verified_download_manifest.restricted.csv"
    )
    _validate_stage_receipt(
        extraction_stage,
        batch_id=batch_id,
        stage="DICOM_EXTRACTION",
        required_artifacts={
            "dicom_extraction.summary.json": summary_sha,
            "dicom_audit.restricted.csv": str(receipt["dicom_audit_sha256"]),
            "extraction_manifest.restricted.csv": str(
                receipt["extraction_manifest_sha256"]
            ),
            "technical_disposition_manifest.restricted.csv": str(
                receipt["technical_disposition_manifest_sha256"]
            ),
        },
    )
    if extraction_stage.get("input_manifest_sha256") != _manifest_digest_for_path(
        rows,
        verified_download_manifest_path,
        role="raw_dicom_and_download_authority",
    ):
        _fail("R8U_R7C_EXTRACTION_STAGE_HASH_MISMATCH")
    if _require_metadata_row_matches_file(
        rows,
        extraction_stage_path,
        role="dicom_extraction_metadata_retained",
        content_hash=True,
    ) != extraction_stage_sha:
        _fail("R8U_R7C_EXTRACTION_STAGE_HASH_MISMATCH")

    echo_stage_path = batch_root / "echoprime/stage_completion_receipt.restricted.json"
    echo_stage, echo_stage_payload = _read_json(
        echo_stage_path, "R8U_R7C_ECHOPRIME_STAGE_RECEIPT"
    )
    echo_artifact_paths = {
        name: batch_root / "echoprime" / name
        for name in (
            "clip_embeddings.restricted.npz",
            "clip_manifest.restricted.csv",
            "study_embeddings.restricted.npz",
            "study_manifest.restricted.csv",
            "study_disposition.restricted.csv",
            "echoprime_pooling.summary.json",
        )
    }
    echo_artifacts = {
        name: _manifest_digest_for_path(
            rows, path, role="embedding_and_pooling_retained"
        )
        for name, path in echo_artifact_paths.items()
    }
    if (
        echo_artifacts["clip_manifest.restricted.csv"]
        != receipt["clip_manifest_sha256"]
        or echo_artifacts["clip_embeddings.restricted.npz"]
        != receipt["clip_embeddings_sha256"]
        or echo_artifacts["study_manifest.restricted.csv"]
        != receipt["study_manifest_sha256"]
        or echo_artifacts["study_embeddings.restricted.npz"]
        != receipt["study_embeddings_sha256"]
    ):
        _fail("R8U_R7C_EMBEDDING_METADATA_AUTHORITY_INVALID")
    _validate_stage_receipt(
        echo_stage,
        batch_id=batch_id,
        stage="ECHOPRIME_EMBEDDING",
        required_artifacts=echo_artifacts,
    )
    if (
        echo_stage.get("input_manifest_sha256")
        != receipt["extraction_manifest_sha256"]
        or echo_stage.get("runtime_authority")
        != extraction_stage.get("runtime_authority")
    ):
        _fail("R8U_R7C_ECHOPRIME_STAGE_RECEIPT_INVALID")
    echo_stage_sha = _sha256(echo_stage_payload)
    _require_metadata_row_matches_file(
        rows,
        echo_stage_path,
        role="embedding_and_pooling_retained",
        content_hash=True,
    )
    if _manifest_digest_for_path(
        rows, echo_stage_path, role="embedding_and_pooling_retained"
    ) != echo_stage_sha:
        _fail("R8U_R7C_ECHOPRIME_STAGE_RECEIPT_INVALID")
    # Embedding stores are deliberately checked only by lstat/size plus the
    # already-sealed manifest and stage-receipt hashes; their bodies stay shut.
    # Section 16 keeps Batches 1--16 strictly on their sealed receipt chain,
    # so targeted current-file metadata is limited to Tasks 17--19.
    for basename, digest in (
        ("clip_embeddings.restricted.npz", receipt["clip_embeddings_sha256"]),
        ("study_embeddings.restricted.npz", receipt["study_embeddings_sha256"]),
    ):
        path = batch_root / "echoprime" / basename
        sealed_digest = _manifest_digest_for_path(
            rows, path, role="embedding_and_pooling_retained"
        )
        if sealed_digest != digest or (
            ordinal >= 16
            and _require_metadata_row_matches_file(
                rows,
                path,
                role="embedding_and_pooling_retained",
                content_hash=False,
            )
            != digest
        ):
            _fail("R8U_R7C_EMBEDDING_METADATA_AUTHORITY_INVALID")

    _validate_extraction_summary(
        extraction_summary, receipt=receipt, planned=planned
    )

    retired_bytes, retired_tree_sha = _retired_cache_projection(
        rows,
        batch_id=batch_id,
        expected_tree_sha256=str(receipt["cache_tree_sha256"]),
        expected_files=int(receipt["n_successfully_extracted_cines"]),
        inspect_cache_root=ordinal >= 16,
    )

    authorization_path = (
        ATTEMPT_ROOT
        / "cache_retirement_authorizations"
        / f"{batch_id}.authorization.json"
    )
    authorization, authorization_payload = _read_json(
        authorization_path, "R8U_R7C_CACHE_AUTHORIZATION"
    )
    authorization_sha = _sha256(authorization_payload)
    if authorization_sha != receipt["cache_retirement_authorization_sha256"]:
        _fail("R8U_R7C_CACHE_AUTHORIZATION_INVALID")

    intent_path = preservation_root / "cache_retirement_intent.restricted.json"
    intent, intent_payload = _read_json(intent_path, "R8U_R7C_CACHE_INTENT")
    intent_sha = _sha256(intent_payload)

    staged_path = preservation_root / "cache_atomically_staged.restricted.json"
    staged, staged_payload = _read_json(staged_path, "R8U_R7C_CACHE_STAGED")
    staged_sha = _sha256(staged_payload)

    transition_path = preservation_root / "cache_retirement_finalized.restricted.json"
    transition, transition_payload = _read_json(
        transition_path, "R8U_R7C_CACHE_TRANSITION"
    )
    transition_sha = _sha256(transition_payload)

    final_ledger_path = batch_root / "final_resume_ledger.restricted.json"
    final_ledger, final_ledger_payload = _read_json(
        final_ledger_path, "R8U_R7C_FINAL_LEDGER", maximum_bytes=MAX_LEDGER_BYTES
    )
    runtime_authority = extraction_stage["runtime_authority"]
    _validate_retirement_chain(
        batch_id=batch_id,
        receipt_sha256=receipt_sha,
        preservation_sha256=preservation_sha,
        retired_tree_sha256=retired_tree_sha,
        authorization=authorization,
        authorization_sha256=authorization_sha,
        intent=intent,
        intent_sha256=intent_sha,
        staged=staged,
        staged_sha256=staged_sha,
        expected_staged_sha256=str(
            receipt["cache_atomically_staged_receipt_sha256"]
        ),
        transition=transition,
        final_ledger=final_ledger,
        expected_runtime_authority=runtime_authority,
        expected_object_keys={
            str(item["source_object_key"]) for item in planned["objects"]
        },
    )

    return {
        "batch_id": batch_id,
        "ordinal": ordinal,
        "attempt_id": accounting.ATTEMPT_ID,
        "batch_plan_sha256": accounting.PLAN_SHA256,
        "scientific_commit": accounting.SCIENTIFIC_COMMIT,
        "batch_finalization_receipt_sha256": receipt_sha,
        "preservation_receipt_sha256": preservation_sha,
        "preservation_manifest_sha256": str(receipt["preservation_manifest_sha256"]),
        "extraction_stage_completion_receipt_sha256": extraction_stage_sha,
        "extraction_summary_sha256": summary_sha,
        "cache_retirement_transition_sha256": transition_sha,
        "final_ledger_sha256": _sha256(final_ledger_payload),
        "retired_cache_tree_sha256": retired_tree_sha,
        "study_membership_sha256": str(planned["study_membership_sha256"]),
        "prespecified_no_cine_study_set_sha256": str(
            planned["prespecified_no_cine_study_set_sha256"]
        ),
        "n_selected_studies": int(receipt["n_selected_studies"]),
        "n_selected_subjects": int(receipt["n_selected_subjects"]),
        "n_source_objects": int(receipt["n_expected_objects"]),
        "source_bytes": int(receipt["expected_source_bytes"]),
        "n_downloaded_objects": int(receipt["n_download_verified"]),
        "downloaded_bytes": int(receipt["expected_source_bytes"]),
        "n_readable_objects": int(receipt["n_dicom_readable"]),
        "readable_bytes": int(receipt["expected_source_bytes"]),
        "n_multiframe_candidates": int(receipt["n_multiframe_cines"]),
        "n_successful_extractions": int(receipt["n_successfully_extracted_cines"]),
        "n_clip_embeddings": int(receipt["n_clip_embeddings"]),
        "n_technical_dispositions": int(receipt["n_object_technical_dispositions"]),
        "n_ordinary_preprocessing_path": int(
            extraction_summary["n_ordinary_preprocessing_path"]
        ),
        "n_spatial_fallback_preprocessing_path": int(
            extraction_summary["n_spatial_fallback_preprocessing_path"]
        ),
        "n_temporal_fallback_preprocessing_path": int(
            extraction_summary["n_temporal_fallback_preprocessing_path"]
        ),
        "n_spatial_temporal_fallback_preprocessing_path": int(
            extraction_summary["n_spatial_temporal_fallback_preprocessing_path"]
        ),
        "n_study_embeddings": int(receipt["n_pooled_studies"]),
        "n_prespecified_no_cine_studies": int(receipt["n_no_cine_studies"]),
        "n_new_no_cine_studies": int(receipt["n_new_no_cine_studies"]),
        "retired_extracted_cache_bytes": retired_bytes,
        "n_missing_selected_studies": int(receipt["n_missing_selected_studies"]),
        "n_duplicate_selected_studies": 0,
        "n_source_substitutions": int(receipt["object_substitution_count"]),
        "n_unaccounted_multiframe_candidates": int(
            receipt["unaccounted_multiframe_objects"]
        ),
        "n_outcome_informed_decisions": 0,
        "final_ledger_status": "FINALIZED",
        "preservation_status": str(preservation["status"]),
        "cache_retirement_status": "PASS_RETIRED",
        "batch_finalization_status": str(receipt["status"]),
        "raw_source_authority_retained": True,
        "extracted_cache_absent": True,
    }


def load_all_batch_metadata(*, plan: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [load_batch_metadata(ordinal, plan=plan) for ordinal in range(19)]


def _validate_original_cohort_batch_metadata(
    batch_metadata: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, int], list[str]]:
    """Normalize the already-validated fixed batch projections for Case A."""

    if (
        not isinstance(batch_metadata, Sequence)
        or isinstance(batch_metadata, (str, bytes))
        or len(batch_metadata) != 19
    ):
        _fail("R8U_R7C_ORIGINAL_COHORT_BATCH_METADATA_INVALID")
    totals = {key: 0 for key in metadata.BATCH_COUNT_KEYS}
    receipt_hashes: list[str] = []
    seen_receipt_hashes: set[str] = set()
    for ordinal, item in enumerate(batch_metadata):
        batch_id = f"c3_batch_{ordinal:03d}"
        expected_studies = 30 if ordinal == 18 else 250
        if (
            not isinstance(item, Mapping)
            or set(item) != metadata.BATCH_METADATA_KEYS
            or item.get("batch_id") != batch_id
            or item.get("ordinal") != ordinal
            or item.get("attempt_id") != accounting.ATTEMPT_ID
            or item.get("batch_plan_sha256") != accounting.PLAN_SHA256
            or item.get("scientific_commit") != accounting.SCIENTIFIC_COMMIT
            or item.get("n_selected_studies") != expected_studies
            or item.get("n_selected_subjects") != expected_studies
            or item.get("final_ledger_status") != "FINALIZED"
            or item.get("preservation_status")
            != "PASS_BATCH_CACHE_RETIREMENT_ELIGIBLE"
            or item.get("cache_retirement_status") != "PASS_RETIRED"
            or item.get("batch_finalization_status") != "PASS_BATCH_FINALIZED"
            or item.get("raw_source_authority_retained") is not True
            or item.get("extracted_cache_absent") is not True
        ):
            _fail("R8U_R7C_ORIGINAL_COHORT_BATCH_METADATA_INVALID")
        for key in metadata.BATCH_HASH_KEYS:
            if SHA_RE.fullmatch(str(item.get(key, ""))) is None:
                _fail("R8U_R7C_ORIGINAL_COHORT_BATCH_METADATA_INVALID")
        receipt_sha256 = str(item["batch_finalization_receipt_sha256"])
        if (
            receipt_sha256 in seen_receipt_hashes
            or (
                ordinal < 16
                and receipt_sha256 != PREFIX_FINAL_RECEIPT_SHA256[ordinal]
            )
        ):
            _fail("R8U_R7C_ORIGINAL_COHORT_BATCH_METADATA_INVALID")
        seen_receipt_hashes.add(receipt_sha256)
        receipt_hashes.append(receipt_sha256)
        for key in metadata.BATCH_COUNT_KEYS:
            observed = item.get(key)
            if type(observed) is not int or observed < 0:
                _fail("R8U_R7C_ORIGINAL_COHORT_BATCH_METADATA_INVALID")
            totals[key] += observed
        if (
            item["n_source_objects"] < 1
            or item["source_bytes"] < 1
            or item["n_downloaded_objects"] != item["n_source_objects"]
            or item["downloaded_bytes"] != item["source_bytes"]
            or item["n_readable_objects"] != item["n_source_objects"]
            or item["readable_bytes"] != item["source_bytes"]
            or item["n_multiframe_candidates"]
            != item["n_successful_extractions"]
            + item["n_technical_dispositions"]
            or item["n_successful_extractions"] != item["n_clip_embeddings"]
            or item["n_ordinary_preprocessing_path"]
            + item["n_spatial_fallback_preprocessing_path"]
            + item["n_temporal_fallback_preprocessing_path"]
            + item["n_spatial_temporal_fallback_preprocessing_path"]
            != item["n_successful_extractions"]
            or item["n_study_embeddings"]
            + item["n_prespecified_no_cine_studies"]
            != item["n_selected_studies"]
            or any(
                item[key] != 0
                for key in (
                    "n_new_no_cine_studies",
                    "n_missing_selected_studies",
                    "n_duplicate_selected_studies",
                    "n_source_substitutions",
                    "n_unaccounted_multiframe_candidates",
                    "n_outcome_informed_decisions",
                )
            )
        ):
            _fail("R8U_R7C_ORIGINAL_COHORT_BATCH_METADATA_INVALID")
    if (
        totals["n_selected_studies"] != 4_530
        or totals["n_selected_subjects"] != 4_530
        or totals["n_prespecified_no_cine_studies"] != 5
    ):
        _fail("R8U_R7C_ORIGINAL_COHORT_BATCH_METADATA_INVALID")
    return totals, receipt_hashes


def load_original_cohort_finalization_receipt(
    batch_metadata: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], str] | None:
    """Load and bind the original finalizer's receipt without scientific bodies.

    ``None`` means that the fixed canonical path was absent.  Any occupied but
    nonregular, symlinked, malformed, or contradictory path fails closed.
    """

    try:
        visible = os.lstat(ORIGINAL_COHORT_RECEIPT_PATH)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise R7CEvidenceError(
            "R8U_R7C_ORIGINAL_COHORT_RECEIPT_INVALID"
        ) from exc
    if (
        stat.S_ISLNK(visible.st_mode)
        or not stat.S_ISREG(visible.st_mode)
        or visible.st_uid != os.geteuid()
        or visible.st_nlink != 1
        or stat.S_IMODE(visible.st_mode) != 0o600
    ):
        _fail("R8U_R7C_ORIGINAL_COHORT_RECEIPT_INVALID")
    value, payload = _read_json(
        ORIGINAL_COHORT_RECEIPT_PATH,
        "R8U_R7C_ORIGINAL_COHORT_RECEIPT",
    )
    try:
        finalizer.validate_closed_final_summary(value)
    except Exception as exc:
        raise R7CEvidenceError(
            "R8U_R7C_ORIGINAL_COHORT_RECEIPT_INVALID"
        ) from exc
    totals, receipt_hashes = _validate_original_cohort_batch_metadata(
        batch_metadata
    )
    expected_receipt_set_sha256 = _sha256(
        ("\n".join(sorted(receipt_hashes)) + "\n").encode("ascii")
    )
    expected_counts = {
        "production_batches": 19,
        "selected_studies": totals["n_selected_studies"],
        "selected_subjects": totals["n_selected_subjects"],
        "verified_source_objects": totals["n_downloaded_objects"],
        "selected_source_bytes": totals["source_bytes"],
        "dicom_readable_objects": totals["n_readable_objects"],
        "dicom_unreadable_objects": (
            totals["n_source_objects"] - totals["n_readable_objects"]
        ),
        "multiframe_cines": totals["n_multiframe_candidates"],
        "single_frame_objects": (
            totals["n_readable_objects"] - totals["n_multiframe_candidates"]
        ),
        "extracted_clips": totals["n_successful_extractions"],
        "successfully_extracted_cines": totals["n_successful_extractions"],
        "object_technical_dispositions": totals["n_technical_dispositions"],
        "new_no_cine_studies": totals["n_new_no_cine_studies"],
        "unique_clip_keys": totals["n_clip_embeddings"],
        "clip_embeddings": totals["n_clip_embeddings"],
        "pooled_imaging_eligible_studies": totals["n_study_embeddings"],
        "no_cine_studies": totals["n_prespecified_no_cine_studies"],
        "missing_selected_studies": totals["n_missing_selected_studies"],
        "object_substitution_count": totals["n_source_substitutions"],
        "unaccounted_multiframe_objects": totals[
            "n_unaccounted_multiframe_candidates"
        ],
        "canonical_clip_index_rows": totals["n_clip_embeddings"],
        "cohort_preserved_artifacts": 2 * 19 + 4,
    }
    if (
        value.get("status") != "PASS_PRODUCTION_C3_FINALIZED"
        or value.get("r8u_implementation_commit")
        != accounting.RUNTIME_IMPLEMENTATION_COMMIT
        or value.get("batch_receipt_set_sha256")
        != expected_receipt_set_sha256
        or any(value.get(key) != expected for key, expected in expected_counts.items())
        or value.get("technical_disposition_counts_by_class")
        != {
            "SOURCE_SIGNAL_QUALITY_UNUSABLE_UNDER_FROZEN_PREPROCESSOR": totals[
                "n_technical_dispositions"
            ]
        }
    ):
        _fail("R8U_R7C_ORIGINAL_COHORT_RECEIPT_CONTRADICTION")
    return value, _sha256(payload)


def _stable_metadata_row(path: Path, *, root: Path, kind: str) -> list[Any]:
    try:
        before = os.lstat(path)
        relative = path.relative_to(root).as_posix()
        after = os.lstat(path)
    except (OSError, ValueError) as exc:
        raise R7CEvidenceError(
            "R8U_R7C_FAILED_PARTIAL_CACHE_CONTRADICTION"
        ) from exc
    expected_type = stat.S_ISDIR if kind == "D" else stat.S_ISREG
    identity = lambda value: (
        value.st_mode,
        value.st_uid,
        value.st_gid,
        value.st_dev,
        value.st_ino,
        value.st_nlink,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )
    if (
        stat.S_ISLNK(before.st_mode)
        or not expected_type(before.st_mode)
        or before.st_uid != os.geteuid()
        or identity(before) != identity(after)
    ):
        _fail("R8U_R7C_FAILED_PARTIAL_CACHE_CONTRADICTION")
    return [
        relative,
        kind,
        stat.S_IMODE(before.st_mode),
        before.st_uid,
        before.st_gid,
        before.st_nlink,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    ]


def _observe_failed_partial_metadata() -> dict[str, Any]:
    """Revalidate the sealed partial using lstat only, never NPZ reads."""

    rows: list[list[Any]] = []
    file_count = directory_count = total_bytes = 0
    root_row = _stable_metadata_row(
        FAILED_PARTIAL_ROOT, root=ATTEMPT_ROOT, kind="D"
    )
    if root_row[2] not in {0o700, 0o2700}:
        _fail("R8U_R7C_FAILED_PARTIAL_CACHE_CONTRADICTION")
    for current, names, files in os.walk(
        FAILED_PARTIAL_ROOT, topdown=True, followlinks=False
    ):
        names.sort()
        files.sort()
        current_path = Path(current)
        directory_row = _stable_metadata_row(
            current_path, root=ATTEMPT_ROOT, kind="D"
        )
        if directory_row[2] not in {0o700, 0o2700}:
            _fail("R8U_R7C_FAILED_PARTIAL_CACHE_CONTRADICTION")
        rows.append(directory_row)
        directory_count += 1
        for name in names:
            child_row = _stable_metadata_row(
                current_path / name, root=ATTEMPT_ROOT, kind="D"
            )
            if child_row[2] not in {0o700, 0o2700}:
                _fail("R8U_R7C_FAILED_PARTIAL_CACHE_CONTRADICTION")
        for name in files:
            path = current_path / name
            if (
                path.suffix.lower() != ".npz"
                or not path.is_relative_to(FAILED_PARTIAL_ROOT / "clips")
            ):
                _fail("R8U_R7C_FAILED_PARTIAL_CACHE_CONTRADICTION")
            row = _stable_metadata_row(path, root=ATTEMPT_ROOT, kind="F")
            if row[2] != 0o600 or row[5] != 1:
                _fail("R8U_R7C_FAILED_PARTIAL_CACHE_CONTRADICTION")
            rows.append(row)
            file_count += 1
            total_bytes += int(row[6])
    payload = b"".join(
        json.dumps(row, separators=(",", ":"), ensure_ascii=True).encode()
        + b"\n"
        for row in sorted(rows)
    )
    return {
        "file_count": file_count,
        "directory_count": directory_count,
        "total_bytes": total_bytes,
        "symlink_count": 0,
        "nonregular_count": 0,
        "metadata_projection_sha256": _sha256(payload),
        "npz_body_reads": 0,
    }


def fixed_cache_topology() -> dict[str, Any]:
    """Project the sealed historical Batch-16 partial without opening NPZs."""

    accounting.validate_fixed_continuation_receipt()
    seal, payload = _read_json(
        FAILED_PARTIAL_SEAL_PATH, "R8U_R7C_FAILED_PARTIAL_SEAL"
    )
    continuation, _ = _read_json(
        accounting.CONTINUATION_RECEIPT_PATH, "R8U_R7C_CONTINUATION_RECEIPT"
    )
    seal_sha = _sha256(payload)
    observation = seal.get("observation")
    expected_observation = {
        "file_count": 4_757,
        "directory_count": 259,
        "total_bytes": 8_583_119_701,
        "symlink_count": 0,
        "nonregular_count": 0,
        "metadata_projection_sha256": (
            finalizer.R8U_FAILED_PARTIAL_METADATA_SHA256
        ),
        "npz_body_reads": 0,
    }
    if (
        set(seal) != finalizer.R8U_FAILED_PARTIAL_SEAL_KEYS
        or seal.get("schema_version") != 1
        or seal.get("artifact_type")
        != "lvef_c3_r8u_r2_failed_task16_partial_extraction_evidence_v1"
        or seal.get("status") != "FAILED_TASK16_PARTIAL_EXTRACTION_EVIDENCE"
        or seal.get("original_scientific_commit") != accounting.SCIENTIFIC_COMMIT
        or seal.get("attempt_id") != accounting.ATTEMPT_ID
        or seal.get("batch_plan_sha256") != accounting.PLAN_SHA256
        or seal.get("batch_id") != "c3_batch_015"
        or seal.get("original_task_id") != 16
        or seal.get("failed_array_job_id") != "7292691"
        or seal.get("failure_class")
        != "SGE_FAILED_19 / ESSTATE_NO_EXITSTATUS"
        or not isinstance(observation, Mapping)
        or set(observation) != finalizer.R8U_FAILED_PARTIAL_OBSERVATION_KEYS
        or observation != expected_observation
        or seal.get("partial_outputs_adopted") is not False
        or seal.get("partial_outputs_modified") is not False
        or seal.get("partial_outputs_deleted") is not False
        or seal.get("partial_outputs_renamed") is not False
        or seal.get("npz_body_reads") != 0
        or continuation.get("failed_partial_seal_sha256") != seal_sha
    ):
        _fail("R8U_R7C_FAILED_PARTIAL_SEAL_INVALID")

    current_observation = _observe_failed_partial_metadata()
    terminal, terminal_payload = _read_json(
        R7_TERMINAL_RECEIPT_PATH, "R8U_R7C_R7_TERMINAL_RECEIPT"
    )
    if (
        current_observation != expected_observation
        or set(terminal) != accounting.r7.R8U_R7_TERMINAL_KEYS
        or _sha256(terminal_payload)
        != continuation.get("preservation_recovery_terminal_receipt_sha256")
        or terminal.get("status") != accounting.r7.R8U_R7_TERMINAL_STATUS
        or terminal.get("attempt_id") != accounting.ATTEMPT_ID
        or terminal.get("batch_plan_sha256") != accounting.PLAN_SHA256
        or terminal.get("batch_id") != "c3_batch_015"
        or terminal.get("batch_finalization_receipt_sha256")
        != BATCH16_FINAL_RECEIPT_SHA256
        or terminal.get("failed_partial_cache_retained") is not True
        or terminal.get("extracted_npz_body_reads") != 0
    ):
        _fail("R8U_R7C_FAILED_PARTIAL_CACHE_CONTRADICTION")

    active_cache_roots = [
        ATTEMPT_ROOT
        / "extracted_cache"
        / f"c3_batch_{ordinal:03d}"
        / "dicom_extraction/clips"
        for ordinal in range(19)
    ]
    active_count = sum(os.path.lexists(path) for path in active_cache_roots)
    if active_count != 0:
        _fail("R8U_R7C_FINALIZED_EXTRACTION_CACHE_PRESENT")
    return {
        "active_finalized_extraction_caches": active_count,
        "batch16_failed_partial_cache_retained": True,
        "batch16_failed_partial_cache_outside_active_topology": True,
        "batch16_failed_partial_cache_adopted": False,
        "batch16_failed_partial_cache_deleted": False,
        "batch16_failed_partial_cache_overwritten": False,
        "batch16_failed_partial_seal_sha256": seal_sha,
        "batch16_failed_partial_metadata_projection_sha256": str(
            current_observation["metadata_projection_sha256"]
        ),
    }


__all__ = [
    "ATTEMPT_ROOT",
    "ORIGINAL_COHORT_RECEIPT_PATH",
    "PLAN_PATH",
    "PRODUCTION_ROOT",
    "R7CEvidenceError",
    "batch_final_receipt_path",
    "fixed_cache_topology",
    "load_all_batch_metadata",
    "load_batch_metadata",
    "load_fixed_plan",
    "load_original_cohort_finalization_receipt",
]
