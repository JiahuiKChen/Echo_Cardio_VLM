#!/usr/bin/env python3
"""Fixed R8R recovery and same-attempt continuation controller.

This is deliberately not a general resume interface.  Every scientific
identity, path, batch, and task range is fixed below.  The only accepted live
variation is the repair implementation commit, which must be the clean
origin-equal descendant of the original scientific commit.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import time
from typing import Any, Callable, Final, Mapping, Sequence
import xml.etree.ElementTree as ET


SCRIPT_ROOT: Final = Path(__file__).resolve().parent
REPOSITORY_ROOT: Final = SCRIPT_ROOT.parent
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

import capture_lvef_c3_post_reallocation_capacity as capacity
import finalize_lvef_c3_production as finalizer
import lvef_c3_full_scheduler as scheduler
import lvef_c3_full_sequential as sequential
import lvef_c3_minimal_canary as minimal
import lvef_c3_orchestration_core as core
import lvef_c3_production_stages as stages
import preserve_lvef_c3_production_batch as preservation
import retire_lvef_c3_extracted_cache_v2 as retirement
import validate_lvef_c3_prior_batch_finalization as prior_gate


ORIGINAL_SCIENTIFIC_COMMIT: Final = (
    "e1cdb674ada23bbc9f3a1ff77c33927bd324d3ed"
)
ORIGINAL_ATTEMPT_ID: Final = "lvef_c3_full_904d0ab65f003c1e_e1cdb674"
ORIGINAL_PLAN_SHA256: Final = (
    "904d0ab65f003c1eb68adeee8c0b1dd786ec7a9ef4bb496b646b22cc7a540247"
)
ORIGINAL_PLAN_BYTES: Final = 115_548_986
ORIGINAL_LAUNCH_BYTES: Final = 1_239
ORIGINAL_LAUNCH_SHA256: Final = (
    "124c041f274a0148c694079d3d8d5918afad01c36f9e4371b91339f8e9a00467"
)
ORIGINAL_CLAIM_BYTES: Final = 1_648
ORIGINAL_CLAIM_SHA256: Final = (
    "482b084b7e6407349b4c330a8029bae19a88c06d2a390a9fe2f385d5c7349b99"
)
ORIGINAL_CAPACITY_BYTES: Final = 1_228
ORIGINAL_CAPACITY_SHA256: Final = (
    "8209b30f20e3cd6e6b8d74606f4b6e693dd47ed3095cc0d86a884e92eb263af6"
)
ORIGINAL_DYNAMIC_CAPACITY_BYTES: Final = 12_242
ORIGINAL_DYNAMIC_CAPACITY_SHA256: Final = (
    "b2e7f4d0db7de4ce05d6c3978d04073dbf520833a72a7e708ae32178c47f05ae"
)
ORIGINAL_SUBMISSION_BYTES: Final = 1_543
ORIGINAL_SUBMISSION_SHA256: Final = (
    "5a8b942cef716b3cc90411638024146a5189781f6f4c70654bbfe0efc41e9c46"
)
PREFIX_RECEIPT_AUTHORITIES: Final = (
    (
        "c3_batch_000",
        4_730,
        "e8f1b505422af64fc98c44f1cb85da52528f014c904e1cbfe319ca0109f31277",
    ),
    (
        "c3_batch_001",
        4_725,
        "54c536f5c2faa712bc3a97d18c6c6fde804941d096dc307dbaf50e6c33e32c98",
    ),
)
FIXED_BATCH3_ID: Final = "c3_batch_002"
FIXED_RECOVERY_TASK_ID: Final = 3
FIXED_CONTINUATION_TASK_IDS: Final = tuple(range(4, 20))
FIXED_CONTINUATION_TASK_RANGE: Final = "4-19"
FIXED_CONTINUATION_MAX_CONCURRENCY: Final = 1
ORIGINAL_SCHEDULER_JOB_IDS: Final = frozenset({"7253130", "7253131"})
PRODUCTION_ROOT: Final = sequential.PRODUCTION_ROOT
ATTEMPT_ROOT: Final = PRODUCTION_ROOT / "attempts" / ORIGINAL_ATTEMPT_ID
RECOVERY_ROOT: Final = ATTEMPT_ROOT / "r8r_batch3_recovery"
RECOVERY_SCHEDULER_ROOT: Final = RECOVERY_ROOT / "scheduler"
RECOVERY_AUTHORITY_PATH: Final = RECOVERY_ROOT / "recovery_authority.restricted.json"
RECOVERY_SUBMISSION_PATH: Final = (
    RECOVERY_SCHEDULER_ROOT / "submission_receipt.restricted.json"
)
RECOVERY_TERMINAL_PATH: Final = (
    RECOVERY_ROOT / "recovery_terminal.aggregate_safe.json"
)
CONTINUATION_ROOT: Final = ATTEMPT_ROOT / "r8r_continuation"
CONTINUATION_SCHEDULER_ROOT: Final = CONTINUATION_ROOT / "scheduler"
CONTINUATION_CAPACITY_PATH: Final = (
    CONTINUATION_ROOT / "continuation_capacity.restricted.json"
)
CONTINUATION_CAPACITY_SUMMARY_PATH: Final = (
    CONTINUATION_ROOT / "continuation_capacity.aggregate_safe.json"
)
CONTINUATION_CLAIM_PATH: Final = (
    CONTINUATION_ROOT / "continuation_claim.restricted.json"
)
CONTINUATION_SUBMISSION_PATH: Final = (
    CONTINUATION_SCHEDULER_ROOT / "submission_receipt.restricted.json"
)
RUNNER_PATH: Final = (
    SCRIPT_ROOT / "scc_run_lvef_c3_r8r_recovery_continuation.sh"
)
COMMIT_RE: Final = re.compile(r"^[0-9a-f]{40}$")
SHA_RE: Final = re.compile(r"^[0-9a-f]{64}$")
JOB_RE: Final = re.compile(r"^[1-9][0-9]{0,19}$")
SAFE_CODE_RE: Final = re.compile(r"^[A-Z][A-Z0-9_]{1,127}$")
QACCT_PATH: Final = scheduler.QSTAT_PATH.with_name("qacct")
MAX_CONTROL_BYTES: Final = 128 * 1024 * 1024
RECOVERY_STATUS: Final = "PASS_BATCH3_PRESERVATION_RECOVERY"
CONTINUATION_CAPACITY_STATUS: Final = (
    "PASS_FIXED_CONTINUATION_4_19_WITH_200GB_RESERVE"
)


class R8RControllerError(RuntimeError):
    def __init__(
        self,
        code: str,
        *,
        stage: str | None = None,
        validation_substage: str | None = None,
    ):
        if SAFE_CODE_RE.fullmatch(code) is None:
            code = "R8R_UNEXPECTED_SANITIZED_FAILURE"
        allowed_substages = {
            item.value for item in preservation.PreservationValidationSubstage
        }
        if (
            validation_substage is not None
            and validation_substage not in allowed_substages
        ):
            validation_substage = None
        super().__init__(code)
        self.code = code
        self.stage = stage
        self.validation_substage = validation_substage


def _fail(code: str, *, stage: str | None = None) -> None:
    raise R8RControllerError(code, stage=stage)


def _canonical_bytes(value: Mapping[str, Any]) -> bytes:
    try:
        return (
            json.dumps(
                value,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise R8RControllerError("R8R_CONTROL_SCHEMA_INVALID") from exc


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _read_private(path: Path, *, maximum: int = MAX_CONTROL_BYTES) -> bytes:
    try:
        payload = sequential._read_owner_private_regular(
            path, maximum_bytes=maximum
        )
    except Exception as exc:
        raise R8RControllerError("R8R_CONTROL_FILE_INVALID") from exc
    return payload


def _read_private_exact(path: Path, *, size: int, digest: str) -> bytes:
    try:
        payload = sequential._read_owner_private_regular(
            path,
            maximum_bytes=size,
            exact_bytes=size,
            size_mismatch_code="R8R_EXACT_CONTROL_SIZE_MISMATCH",
        )
    except Exception as exc:
        raise R8RControllerError("R8R_EXACT_CONTROL_FILE_INVALID") from exc
    if _sha256_bytes(payload) != digest:
        _fail("R8R_EXACT_CONTROL_HASH_MISMATCH")
    return payload


def _strict_json(payload: bytes) -> Mapping[str, Any]:
    def pairs(items: Sequence[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for name, value in items:
            if name in result:
                _fail("R8R_CONTROL_JSON_INVALID")
            result[name] = value
        return result

    def reject(_value: str) -> Any:
        _fail("R8R_CONTROL_JSON_INVALID")

    try:
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=pairs,
            parse_constant=reject,
        )
    except R8RControllerError:
        raise
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise R8RControllerError("R8R_CONTROL_JSON_INVALID") from exc
    if not isinstance(value, Mapping):
        _fail("R8R_CONTROL_JSON_INVALID")
    return value


def _load_private_json(path: Path) -> tuple[Mapping[str, Any], bytes]:
    payload = _read_private(path)
    return _strict_json(payload), payload


def _exact_typed_value_equal(observed: object, expected: object) -> bool:
    """Compare sealed JSON values without bool/int/float equivalence."""

    if type(observed) is not type(expected):
        return False
    if isinstance(expected, dict):
        return set(observed) == set(expected) and all(
            _exact_typed_value_equal(observed[key], expected[key])
            for key in expected
        )
    if isinstance(expected, list):
        return len(observed) == len(expected) and all(
            _exact_typed_value_equal(left, right)
            for left, right in zip(observed, expected, strict=True)
        )
    return observed == expected


def _write_private_json(path: Path, value: Mapping[str, Any]) -> str:
    try:
        return core.atomic_write_json_no_clobber(
            path, value, attempt_id=ORIGINAL_ATTEMPT_ID
        )
    except Exception as exc:
        raise R8RControllerError("R8R_CONTROL_NO_CLOBBER_FAILED") from exc


def _ensure_private_directory(path: Path, *, parents: bool = False) -> None:
    try:
        sequential._ensure_private_directory(path, parents=parents)
    except Exception as exc:
        raise R8RControllerError("R8R_PRIVATE_DIRECTORY_INVALID") from exc


def _create_private_directory_no_clobber(path: Path) -> None:
    """Create one exact private control root and reject every competitor."""

    try:
        sequential._require_nonsymlink_components(path.parent)
        sequential._validate_private_directory(path.parent)
        path.mkdir(mode=0o700)
        sequential._validate_private_directory(path)
    except FileExistsError as exc:
        raise R8RControllerError("R8R_CONTROL_ROOT_COLLISION") from exc
    except Exception as exc:
        raise R8RControllerError("R8R_PRIVATE_DIRECTORY_INVALID") from exc


def _current_implementation_commit() -> str:
    try:
        current = sequential._current_commit()
    except Exception as exc:
        raise R8RControllerError("R8R_IMPLEMENTATION_GIT_AUTHORITY_INVALID") from exc
    if COMMIT_RE.fullmatch(current) is None:
        _fail("R8R_IMPLEMENTATION_GIT_AUTHORITY_INVALID")
    relation = sequential._git(
        "merge-base", "--is-ancestor", ORIGINAL_SCIENTIFIC_COMMIT, current
    )
    distance = sequential._git(
        "rev-list", "--count", f"{ORIGINAL_SCIENTIFIC_COMMIT}..{current}"
    )
    # `git merge-base --is-ancestor` writes no output; `_git` already requires rc 0.
    if relation or distance != "1":
        _fail("R8R_IMPLEMENTATION_ANCESTRY_INVALID")
    return current


def _load_fixed_original_run(
    *,
    scheduler_job_identity: str,
    runtime_validation_context: stages.RuntimeAuthorityValidationContext,
) -> sequential.FullRun:
    if not isinstance(
        runtime_validation_context, stages.RuntimeAuthorityValidationContext
    ):
        _fail("R8R_RUNTIME_VALIDATION_CONTEXT_INVALID")
    implementation_commit = _current_implementation_commit()
    try:
        current = minimal.discover_live_authority(
            runtime_validation_context=runtime_validation_context
        )
    except Exception as exc:
        raise R8RControllerError("R8R_RUNTIME_AUTHORITY_INVALID") from exc
    if current.governing_commit != implementation_commit:
        _fail("R8R_IMPLEMENTATION_GIT_AUTHORITY_INVALID")
    scientific = replace(
        current, governing_commit=ORIGINAL_SCIENTIFIC_COMMIT
    )
    try:
        plan, requirements, contract = sequential._build_frozen_plan(scientific)
        plan_sha = core.validate_current_batch_plan_v3(
            plan, requirements=requirements
        )
    except Exception as exc:
        raise R8RControllerError("R8R_ORIGINAL_PLAN_AUTHORITY_INVALID") from exc
    if plan_sha != ORIGINAL_PLAN_SHA256:
        _fail("R8R_ORIGINAL_PLAN_AUTHORITY_INVALID")
    plan_path = ATTEMPT_ROOT / "full_batch_plan.restricted.json"
    try:
        materialized_plan = sequential._load_full_batch_plan_payload(
            plan_path,
            expected_bytes=ORIGINAL_PLAN_BYTES,
            expected_sha256=ORIGINAL_PLAN_SHA256,
        )
    except Exception as exc:
        raise R8RControllerError("R8R_ORIGINAL_PLAN_AUTHORITY_INVALID") from exc
    if materialized_plan != plan:
        _fail("R8R_ORIGINAL_PLAN_AUTHORITY_INVALID")
    launch_path = ATTEMPT_ROOT / "full_launch_authority.restricted.json"
    launch_payload = _read_private_exact(
        launch_path,
        size=ORIGINAL_LAUNCH_BYTES,
        digest=ORIGINAL_LAUNCH_SHA256,
    )
    launch = _strict_json(launch_payload)
    if (
        set(launch) != core.DIRECT_FULL_LAUNCH_KEYS
        or launch.get("governing_commit") != ORIGINAL_SCIENTIFIC_COMMIT
        or launch.get("batch_plan_sha256") != ORIGINAL_PLAN_SHA256
        or launch.get("array_task_range") != "1-19"
        or launch.get("array_max_concurrency") != 1
        or launch.get("maximum_scheduler_submissions") != 2
        or launch.get("model_fitting_authorized") is not False
        or launch.get("prediction_authorized") is not False
        or launch.get("confirmatory_performance_access_authorized") is not False
    ):
        _fail("R8R_ORIGINAL_LAUNCH_AUTHORITY_INVALID")
    runtime = core.validate_runtime_authority(
        {**plan["authority"], "batch_plan_sha256": plan_sha}
    )
    run = sequential.FullRun(
        authority=scientific,
        plan=plan,
        requirements=requirements,
        contract=contract,
        contract_path=sequential.CONTRACT_PATH,
        plan_sha256=plan_sha,
        runtime_authority=runtime,
        attempt_id=ORIGINAL_ATTEMPT_ID,
        production_root=PRODUCTION_ROOT,
        attempt_root=ATTEMPT_ROOT,
        plan_path=plan_path,
        launch_authority=launch,
        launch_authority_sha256=ORIGINAL_LAUNCH_SHA256,
        scheduler_job_identity=scheduler_job_identity,
    )
    try:
        sequential._validate_private_directory(ATTEMPT_ROOT)
        sequential._validate_full_run(run)
    except Exception as exc:
        raise R8RControllerError("R8R_ORIGINAL_RUN_AUTHORITY_INVALID") from exc
    return run


def _validate_original_controls() -> Mapping[str, str]:
    controls = {
        "original_capacity_sha256": _sha256_bytes(
            _read_private_exact(
                ATTEMPT_ROOT / "full_capacity_receipt.restricted.json",
                size=ORIGINAL_CAPACITY_BYTES,
                digest=ORIGINAL_CAPACITY_SHA256,
            )
        ),
        "original_claim_sha256": _sha256_bytes(
            _read_private_exact(
                ATTEMPT_ROOT / "full_submission_claim.restricted.json",
                size=ORIGINAL_CLAIM_BYTES,
                digest=ORIGINAL_CLAIM_SHA256,
            )
        ),
        "original_dynamic_capacity_sha256": _sha256_bytes(
            _read_private_exact(
                ATTEMPT_ROOT
                / sequential.DYNAMIC_CAPACITY_ATTEMPT_SOURCE_BASENAME,
                size=ORIGINAL_DYNAMIC_CAPACITY_BYTES,
                digest=ORIGINAL_DYNAMIC_CAPACITY_SHA256,
            )
        ),
        "original_launch_sha256": _sha256_bytes(
            _read_private_exact(
                ATTEMPT_ROOT / "full_launch_authority.restricted.json",
                size=ORIGINAL_LAUNCH_BYTES,
                digest=ORIGINAL_LAUNCH_SHA256,
            )
        ),
        "original_plan_sha256": _sha256_bytes(
            _read_private_exact(
                ATTEMPT_ROOT / "full_batch_plan.restricted.json",
                size=ORIGINAL_PLAN_BYTES,
                digest=ORIGINAL_PLAN_SHA256,
            )
        ),
        "original_submission_sha256": _sha256_bytes(
            _read_private_exact(
                ATTEMPT_ROOT / "scheduler/submission_receipt.restricted.json",
                size=ORIGINAL_SUBMISSION_BYTES,
                digest=ORIGINAL_SUBMISSION_SHA256,
            )
        ),
    }
    return dict(sorted(controls.items()))


def _validate_frozen_prefix(
    run: sequential.FullRun, *, include_batch3: bool
) -> tuple[str, ...]:
    observed: list[str] = []
    for batch_id, expected_bytes, expected_sha in PREFIX_RECEIPT_AUTHORITIES:
        paths = sequential._batch_paths(run, batch_id)
        payload = _read_private_exact(
            paths["final_receipt"], size=expected_bytes, digest=expected_sha
        )
        receipt = _strict_json(payload)
        try:
            finalizer._validate_current_receipt_v3(receipt)
            sequential._validate_batch_finalization(
                run=run, batch_id=batch_id
            )
        except Exception as exc:
            raise R8RControllerError("R8R_FROZEN_PREFIX_INVALID") from exc
        observed.append(expected_sha)
    for current_batch in ("c3_batch_001", "c3_batch_002"):
        try:
            prior_gate.validate_prior_batch(
                **sequential._prior_batch_kwargs(run, current_batch)
            )
        except Exception as exc:
            raise R8RControllerError("R8R_FROZEN_PREFIX_INVALID") from exc
    if include_batch3:
        try:
            receipt = sequential._validate_batch_finalization(
                run=run, batch_id=FIXED_BATCH3_ID
            )
        except Exception as exc:
            raise R8RControllerError("R8R_RECOVERED_BATCH3_INVALID") from exc
        observed.append(core.sha256_file(
            sequential._batch_paths(run, FIXED_BATCH3_ID)["final_receipt"]
        ))
        if (
            receipt.get("n_selected_studies") != 250
            or receipt.get("n_expected_objects") != 18_653
            or receipt.get("expected_source_bytes") != 68_725_707_170
            or receipt.get("n_successfully_extracted_cines") != 10_256
            or receipt.get("n_object_technical_dispositions") != 1
            or receipt.get("n_blocking_failures") != 0
            or receipt.get("n_clip_embeddings") != 10_256
            or receipt.get("n_pooled_studies") != 249
            or receipt.get("n_no_cine_studies") != 1
            or receipt.get("n_new_no_cine_studies") != 0
        ):
            _fail("R8R_RECOVERED_BATCH3_SCIENTIFIC_AUTHORITY_INVALID")
    return tuple(observed)


def _metadata_row(path: Path, *, root: Path, kind: str) -> list[Any]:
    try:
        before = os.lstat(path)
        relative = path.relative_to(root).as_posix()
        after = os.lstat(path)
    except (OSError, ValueError) as exc:
        raise R8RControllerError("R8R_RETAINED_TOPOLOGY_INVALID") from exc
    if stat.S_ISLNK(before.st_mode):
        _fail("R8R_RETAINED_TOPOLOGY_INVALID")
    expected_type = stat.S_ISDIR if kind == "D" else stat.S_ISREG
    local_identity = lambda value: (
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
        not expected_type(before.st_mode)
        or before.st_uid != os.geteuid()
        or local_identity(before) != local_identity(after)
    ):
        _fail("R8R_RETAINED_TOPOLOGY_INVALID")
    # Device and inode are checked above for same-call replacement, but are
    # intentionally excluded from the sealed projection because SCC execution
    # nodes may expose the same owner-private filesystem through different
    # device/inode namespaces.
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


def _read_control_nofollow(path: Path) -> bytes:
    try:
        info = os.lstat(path)
        descriptor = os.open(
            path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        )
    except OSError as exc:
        raise R8RControllerError("R8R_RETAINED_CONTROL_INVALID") from exc
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or not stat.S_ISREG(opened.st_mode)
            or stat.S_ISLNK(info.st_mode)
            or info.st_uid != os.geteuid()
            or (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
            != (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
            or info.st_size < 1
            or info.st_size > MAX_CONTROL_BYTES
        ):
            _fail("R8R_RETAINED_CONTROL_INVALID")
        remaining = info.st_size
        chunks: list[bytes] = []
        while remaining:
            block = os.read(descriptor, min(remaining, 1_048_576))
            if not block:
                _fail("R8R_RETAINED_CONTROL_INVALID")
            chunks.append(block)
            remaining -= len(block)
        if os.fstat(descriptor).st_size != info.st_size:
            _fail("R8R_RETAINED_CONTROL_INVALID")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _batch3_retained_authority() -> Mapping[str, Any]:
    """Freeze Batch 3 without opening a DICOM or retained NPZ body."""

    raw_batch = ATTEMPT_ROOT / "raw" / FIXED_BATCH3_ID
    raw_objects = raw_batch / "objects"
    extraction = (
        ATTEMPT_ROOT
        / "extracted_cache"
        / FIXED_BATCH3_ID
        / "dicom_extraction"
    )
    clips = extraction / "clips"
    echoprime = ATTEMPT_ROOT / "batches" / FIXED_BATCH3_ID / "echoprime"
    fixed_roots = (raw_batch, raw_objects, extraction, clips, echoprime)
    for root in fixed_roots:
        _metadata_row(root, root=ATTEMPT_ROOT, kind="D")

    metadata_rows: list[list[Any]] = []
    control_rows: list[list[Any]] = []
    raw_count = 0
    raw_bytes = 0
    clip_count = 0
    clip_bytes = 0
    embedding_sizes: dict[str, int] = {}
    for root in (raw_batch, extraction, echoprime):
        for directory, directory_names, file_names in os.walk(
            root, topdown=True, followlinks=False
        ):
            directory_names.sort()
            file_names.sort()
            current = Path(directory)
            metadata_rows.append(
                _metadata_row(current, root=ATTEMPT_ROOT, kind="D")
            )
            for name in directory_names:
                child = current / name
                if child.is_symlink():
                    _fail("R8R_RETAINED_TOPOLOGY_INVALID")
            for name in file_names:
                path = current / name
                row = _metadata_row(path, root=ATTEMPT_ROOT, kind="F")
                metadata_rows.append(row)
                if path.is_relative_to(raw_objects):
                    if path.parent != raw_objects or path.suffix != ".dcm":
                        _fail("R8R_RETAINED_TOPOLOGY_INVALID")
                    raw_count += 1
                    raw_bytes += int(row[6])
                    continue
                if path.suffix.casefold() == ".dcm":
                    _fail("R8R_RETAINED_TOPOLOGY_INVALID")
                if path.suffix.casefold() == ".npz":
                    if path.is_relative_to(clips) and path.suffix == ".npz":
                        clip_count += 1
                        clip_bytes += int(row[6])
                    elif path in {
                        echoprime / "clip_embeddings.restricted.npz",
                        echoprime / "study_embeddings.restricted.npz",
                    }:
                        embedding_sizes[path.name] = int(row[6])
                    else:
                        _fail("R8R_RETAINED_TOPOLOGY_INVALID")
                    continue
                payload = _read_control_nofollow(path)
                control_rows.append(
                    [
                        path.relative_to(ATTEMPT_ROOT).as_posix(),
                        len(payload),
                        _sha256_bytes(payload),
                    ]
                )

    if (
        raw_count != 18_653
        or raw_bytes != 68_725_707_170
        or clip_count != 10_256
        or clip_bytes != 18_634_511_516
        or embedding_sizes
        != {
            "clip_embeddings.restricted.npz": 19_421_157,
            "study_embeddings.restricted.npz": 472_069,
        }
    ):
        _fail("R8R_BATCH3_RETAINED_AGGREGATE_INVALID")
    metadata_payload = b"".join(
        json.dumps(row, separators=(",", ":"), ensure_ascii=True).encode()
        + b"\n"
        for row in sorted(metadata_rows)
    )
    control_payload = _canonical_bytes({"controls": sorted(control_rows)})
    return {
        "raw_dicom_files": raw_count,
        "raw_dicom_bytes": raw_bytes,
        "extracted_clip_files": clip_count,
        "extracted_clip_bytes": clip_bytes,
        "clip_embedding_artifact_bytes": embedding_sizes[
            "clip_embeddings.restricted.npz"
        ],
        "study_embedding_artifact_bytes": embedding_sizes[
            "study_embeddings.restricted.npz"
        ],
        "metadata_entry_count": len(metadata_rows),
        "metadata_projection_sha256": _sha256_bytes(metadata_payload),
        "nonbody_control_count": len(control_rows),
        "nonbody_control_authority_sha256": _sha256_bytes(control_payload),
        "dicom_body_reads": 0,
        "npz_body_reads": 0,
    }


def _require_batch3_recovery_absent(run: sequential.FullRun) -> None:
    paths = sequential._batch_paths(
        run,
        FIXED_BATCH3_ID,
    )
    expected_absent = (
        paths["preservation"] / "batch_preservation_receipt.restricted.json",
        paths["preservation"] / "batch_preservation_manifest.restricted.tsv",
        ATTEMPT_ROOT
        / "cache_retirement_authorizations"
        / f"{FIXED_BATCH3_ID}.authorization.json",
        paths["preservation"] / "cache_retirement_intent.restricted.json",
        paths["preservation"] / "cache_atomically_staged.restricted.json",
        paths["preservation"] / "cache_retirement_finalized.restricted.json",
        paths["eligibility_ledger"],
        paths["final_ledger"],
        paths["final_receipt"],
        RECOVERY_TERMINAL_PATH,
    )
    if any(os.path.lexists(path) for path in expected_absent):
        _fail("R8R_BATCH3_RECOVERY_OUTPUT_COLLISION")


def _script_authority() -> Mapping[str, str]:
    paths = {
        "controller_sha256": Path(__file__).resolve(),
        "full_sequential_sha256": SCRIPT_ROOT / "lvef_c3_full_sequential.py",
        "production_stages_sha256": SCRIPT_ROOT / "lvef_c3_production_stages.py",
        "preservation_sha256": SCRIPT_ROOT / "preserve_lvef_c3_production_batch.py",
        "retirement_sha256": SCRIPT_ROOT / "retire_lvef_c3_extracted_cache_v2.py",
        "finalizer_sha256": SCRIPT_ROOT / "finalize_lvef_c3_production.py",
        "runner_sha256": RUNNER_PATH,
    }
    result: dict[str, str] = {}
    for key, path in paths.items():
        try:
            result[key] = core.sha256_file(path)
        except Exception as exc:
            raise R8RControllerError("R8R_SCRIPT_AUTHORITY_INVALID") from exc
    return dict(sorted(result.items()))


def _validate_no_active_jobs(
    environment: Mapping[str, str],
    *,
    runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> None:
    completed = runner(
        [str(scheduler.QSTAT_PATH), "-xml", "-u", environment["USER"]],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        env=dict(environment),
    )
    payload = bytes(completed.stdout)
    if (
        completed.returncode != 0
        or completed.stderr
        or len(payload) > 4 * 1024 * 1024
        or b"<!DOCTYPE" in payload.upper()
        or b"<!ENTITY" in payload.upper()
    ):
        _fail("R8R_ACTIVE_JOB_CHECK_FAILED")
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise R8RControllerError("R8R_ACTIVE_JOB_CHECK_FAILED") from exc
    local = lambda element: element.tag.rsplit("}", 1)[-1]
    direct_children = [local(element) for element in list(root)]
    if (
        local(root) != "job_info"
        or direct_children.count("queue_info") != 1
        or direct_children.count("job_info") != 1
        or any(
            name not in {"queue_info", "job_info"}
            for name in direct_children
        )
    ):
        _fail("R8R_ACTIVE_JOB_CHECK_FAILED")
    names = {
        element.text or ""
        for element in root.iter()
        if local(element) == "JB_name"
    }
    job_ids = {
        element.text or ""
        for element in root.iter()
        if local(element) == "JB_job_number"
    }
    if any(
        re.fullmatch(
            r"lvef_c3_(?:full_(?:seq|fin)|r8r_(?:rec|seq|fin))_[0-9a-f]{8}",
            name,
        )
        or re.fullmatch(
            r"c3_(?:dl1|dlr|ext|emb|pre|ret|fin)_[0-9a-f]{12}",
            name,
        )
        for name in names
    ) or not ORIGINAL_SCHEDULER_JOB_IDS.isdisjoint(job_ids):
        _fail("R8R_ACTIVE_MATCHING_JOB_EXISTS")


def _recovery_job_name(implementation_commit: str) -> str:
    return f"lvef_c3_r8r_rec_{implementation_commit[:8]}"


def _continuation_array_job_name(implementation_commit: str) -> str:
    return f"lvef_c3_r8r_seq_{implementation_commit[:8]}"


def _continuation_finalizer_job_name(implementation_commit: str) -> str:
    return f"lvef_c3_r8r_fin_{implementation_commit[:8]}"


def _recovery_qsub_command(implementation_commit: str) -> list[str]:
    return [
        str(scheduler.QSUB_PATH),
        "-clear",
        "-terse",
        "-r",
        "n",
        "-P",
        "mimicecho",
        "-N",
        _recovery_job_name(implementation_commit),
        "-j",
        "y",
        "-o",
        str(RECOVERY_SCHEDULER_ROOT),
        "-l",
        "h_rt=02:00:00",
        "-pe",
        "omp",
        "4",
        "-l",
        "mem_per_core=16G",
        str(RUNNER_PATH),
    ]


def _continuation_array_command(implementation_commit: str) -> list[str]:
    return [
        str(scheduler.QSUB_PATH),
        "-clear",
        "-terse",
        "-r",
        "n",
        "-P",
        "mimicecho",
        "-N",
        _continuation_array_job_name(implementation_commit),
        "-j",
        "y",
        "-o",
        str(CONTINUATION_SCHEDULER_ROOT),
        "-t",
        FIXED_CONTINUATION_TASK_RANGE,
        "-tc",
        str(FIXED_CONTINUATION_MAX_CONCURRENCY),
        "-l",
        "h_rt=48:00:00",
        "-l",
        "gpus=1",
        "-l",
        "gpu_c=8.0",
        "-l",
        "gpu_memory=48G",
        "-pe",
        "omp",
        "4",
        "-l",
        "mem_per_core=16G",
        str(RUNNER_PATH),
    ]


def _continuation_finalizer_command(
    implementation_commit: str, array_job_id: str
) -> list[str]:
    if JOB_RE.fullmatch(array_job_id) is None:
        _fail("R8R_JOB_ID_INVALID")
    return [
        str(scheduler.QSUB_PATH),
        "-clear",
        "-terse",
        "-r",
        "n",
        "-P",
        "mimicecho",
        "-N",
        _continuation_finalizer_job_name(implementation_commit),
        "-j",
        "y",
        "-o",
        str(CONTINUATION_SCHEDULER_ROOT),
        "-hold_jid",
        array_job_id,
        "-l",
        "h_rt=12:00:00",
        "-pe",
        "omp",
        "4",
        "-l",
        "mem_per_core=8G",
        str(RUNNER_PATH),
    ]


def _parse_r8r_array_qsub_stdout(payload: bytes) -> str:
    match = re.fullmatch(rb"([1-9][0-9]{0,19})(?:\.4-19:1)?\n?", payload)
    if match is None:
        _fail("R8R_ARRAY_QSUB_OUTPUT_INVALID")
    return match.group(1).decode("ascii")


def _qsub_evidence_authority(root: Path, label: str) -> Mapping[str, Any]:
    evidence: dict[str, bytes] = {}
    for kind in ("stdout", "stderr", "exit_status"):
        try:
            evidence[kind] = scheduler._read_scheduler_evidence(
                root / f"{label}.qsub.{kind}.restricted"
            )
        except Exception as exc:
            raise R8RControllerError("R8R_QSUB_EVIDENCE_INVALID") from exc
    if evidence["exit_status"] != b"0\n" or evidence["stderr"] != b"":
        _fail("R8R_QSUB_EVIDENCE_INVALID")
    return {
        "stdout_bytes": len(evidence["stdout"]),
        "stdout_sha256": _sha256_bytes(evidence["stdout"]),
        "stderr_bytes": 0,
        "stderr_sha256": _sha256_bytes(evidence["stderr"]),
        "exit_status": 0,
    }


def _validate_recovery_accounting_projection(
    value: Mapping[str, Any], *, expected_job_id: str
) -> Mapping[str, Any]:
    keys = {
        "status",
        "job_id",
        "task_id",
        "failed",
        "exit_status",
        "start_time",
        "end_time",
        "ru_wallclock_seconds",
    }
    if (
        not isinstance(value, Mapping)
        or set(value) != keys
        or value.get("status") != "PASS_RECOVERY_QACCT_FAILED_0_EXIT_0"
        or value.get("job_id") != expected_job_id
        or value.get("task_id") not in {"NONE", "undefined"}
        or type(value.get("failed")) is not int
        or value.get("failed") != 0
        or type(value.get("exit_status")) is not int
        or value.get("exit_status") != 0
        or not isinstance(value.get("start_time"), str)
        or not isinstance(value.get("end_time"), str)
        or not isinstance(value.get("ru_wallclock_seconds"), str)
        or re.fullmatch(
            r"(?:0|[1-9][0-9]*)(?:[.][0-9]+)?",
            str(value.get("ru_wallclock_seconds")),
        )
        is None
    ):
        _fail("R8R_RECOVERY_ACCOUNTING_INVALID")
    try:
        start = datetime.strptime(
            str(value["start_time"]), "%a %b %d %H:%M:%S %Y"
        )
        end = datetime.strptime(
            str(value["end_time"]), "%a %b %d %H:%M:%S %Y"
        )
    except ValueError as exc:
        raise R8RControllerError("R8R_RECOVERY_ACCOUNTING_INVALID") from exc
    if end < start:
        _fail("R8R_RECOVERY_ACCOUNTING_INVALID")
    return dict(value)


def _validate_qacct_tool() -> None:
    expected = scheduler.CANONICAL_SGE_ROOT / "bin/linux-x64/qacct"
    if QACCT_PATH != expected:
        _fail("R8R_RECOVERY_ACCOUNTING_TOOL_INVALID")
    try:
        scheduler._require_nonsymlink_components(
            QACCT_PATH, "R8R_RECOVERY_ACCOUNTING_TOOL_INVALID"
        )
        info = os.lstat(QACCT_PATH)
    except Exception as exc:
        raise R8RControllerError(
            "R8R_RECOVERY_ACCOUNTING_TOOL_INVALID"
        ) from exc
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != 0
        or stat.S_IMODE(info.st_mode) & 0o022
        or not stat.S_IMODE(info.st_mode) & stat.S_IXUSR
        or not os.access(QACCT_PATH, os.X_OK)
    ):
        _fail("R8R_RECOVERY_ACCOUNTING_TOOL_INVALID")


def _query_recovery_accounting(
    *,
    recovery_job_id: str,
    environment: Mapping[str, str],
    runner: Callable[..., subprocess.CompletedProcess[bytes]],
) -> Mapping[str, Any]:
    if JOB_RE.fullmatch(recovery_job_id) is None:
        _fail("R8R_RECOVERY_ACCOUNTING_INVALID")
    _validate_qacct_tool()
    completed = runner(
        [str(QACCT_PATH), "-j", recovery_job_id],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        env=dict(environment),
    )
    payload = bytes(completed.stdout)
    if (
        completed.returncode != 0
        or completed.stderr
        or len(payload) > 4 * 1024 * 1024
    ):
        _fail("R8R_RECOVERY_ACCOUNTING_UNAVAILABLE")
    records: list[dict[str, str]] = []
    current: dict[str, str] = {}
    try:
        decoded = payload.decode("utf-8", "strict")
    except UnicodeError as exc:
        raise R8RControllerError("R8R_RECOVERY_ACCOUNTING_INVALID") from exc
    for raw_line in decoded.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if set(line) == {"="}:
            if current:
                records.append(current)
                current = {}
            continue
        parts = line.split(None, 1)
        if len(parts) != 2 or parts[0] in current:
            _fail("R8R_RECOVERY_ACCOUNTING_INVALID")
        current[parts[0]] = parts[1].strip()
    if current:
        records.append(current)
    if (
        len(records) != 1
        or records[0].get("jobnumber") != recovery_job_id
    ):
        _fail("R8R_RECOVERY_ACCOUNTING_UNAVAILABLE")
    record = records[0]
    required = {
        "jobnumber",
        "taskid",
        "failed",
        "exit_status",
        "start_time",
        "end_time",
        "ru_wallclock",
    }
    if not required.issubset(record):
        _fail("R8R_RECOVERY_ACCOUNTING_INVALID")
    try:
        projection = {
            "status": "PASS_RECOVERY_QACCT_FAILED_0_EXIT_0",
            "job_id": recovery_job_id,
            "task_id": (
                "NONE" if record["taskid"] in {"", "NONE"} else record["taskid"]
            ),
            "failed": int(record["failed"]),
            "exit_status": int(record["exit_status"]),
            "start_time": record["start_time"],
            "end_time": record["end_time"],
            "ru_wallclock_seconds": record["ru_wallclock"],
        }
    except (TypeError, ValueError) as exc:
        raise R8RControllerError("R8R_RECOVERY_ACCOUNTING_INVALID") from exc
    return _validate_recovery_accounting_projection(
        projection, expected_job_id=recovery_job_id
    )


def _build_recovery_authority(
    *,
    run: sequential.FullRun,
    implementation_commit: str,
    qsub_environment_sha256: str,
    retained_authority: Mapping[str, Any],
) -> Mapping[str, Any]:
    if (
        run.attempt_id != ORIGINAL_ATTEMPT_ID
        or run.plan_sha256 != ORIGINAL_PLAN_SHA256
        or run.authority.governing_commit != ORIGINAL_SCIENTIFIC_COMMIT
        or SHA_RE.fullmatch(qsub_environment_sha256) is None
    ):
        _fail("R8R_RECOVERY_AUTHORITY_INVALID")
    return {
        "schema_version": 1,
        "artifact_type": "lvef_c3_r8r_batch3_recovery_authority_v1",
        "status": "AUTHORIZED_FIXED_BATCH3_PRESERVATION_RECOVERY",
        "original_scientific_commit": ORIGINAL_SCIENTIFIC_COMMIT,
        "implementation_commit": implementation_commit,
        "attempt_id": ORIGINAL_ATTEMPT_ID,
        "batch_plan_sha256": ORIGINAL_PLAN_SHA256,
        "batch_id": FIXED_BATCH3_ID,
        "original_control_authority": _validate_original_controls(),
        "prefix_final_receipt_sha256": [
            item[2] for item in PREFIX_RECEIPT_AUTHORITIES
        ],
        "batch3_retained_authority": dict(retained_authority),
        "script_authority": _script_authority(),
        "runtime_validation_context": (
            stages.SEALED_SCHEDULER_RUNTIME_REPLAY.value
        ),
        "qsub_environment_sha256": qsub_environment_sha256,
        "fixed_recovery_task": FIXED_RECOVERY_TASK_ID,
        "cloud_requests_authorized": 0,
        "dicom_body_reads_authorized": 0,
        "dicom_extraction_reruns_authorized": 0,
        "echoprime_reruns_authorized": 0,
        "embedding_generations_authorized": 0,
        "gpu_executions_authorized": 0,
        "raw_dicom_deletion_authorized": False,
        "model_fitting_authorized": False,
        "prediction_authorized": False,
        "confirmatory_performance_access_authorized": False,
    }


def _validate_recovery_authority(
    run: sequential.FullRun,
    value: Mapping[str, Any],
    *,
    require_retained_current: bool,
) -> None:
    implementation_commit = _current_implementation_commit()
    bound_qsub_environment_sha256 = value.get("qsub_environment_sha256")
    if (
        not isinstance(bound_qsub_environment_sha256, str)
        or SHA_RE.fullmatch(bound_qsub_environment_sha256) is None
    ):
        _fail("R8R_RECOVERY_AUTHORITY_INVALID")
    retained = value.get("batch3_retained_authority")
    if not isinstance(retained, Mapping):
        _fail("R8R_RECOVERY_AUTHORITY_INVALID")
    if require_retained_current and not _exact_typed_value_equal(
        retained, _batch3_retained_authority()
    ):
        _fail("R8R_RECOVERY_RETAINED_AUTHORITY_CHANGED")
    expected = _build_recovery_authority(
        run=run,
        implementation_commit=implementation_commit,
        qsub_environment_sha256=bound_qsub_environment_sha256,
        retained_authority=retained,
    )
    if not _exact_typed_value_equal(value, expected):
        _fail("R8R_RECOVERY_AUTHORITY_INVALID")


def _build_recovery_submission_receipt(
    *,
    implementation_commit: str,
    recovery_job_id: str,
    qsub_environment_sha256: str,
) -> Mapping[str, Any]:
    if JOB_RE.fullmatch(recovery_job_id) is None:
        _fail("R8R_JOB_ID_INVALID")
    try:
        if scheduler.parse_numeric_qsub_stdout(
            scheduler._read_scheduler_evidence(
                RECOVERY_SCHEDULER_ROOT
                / "recovery.qsub.stdout.restricted"
            )
        ) != recovery_job_id:
            _fail("R8R_QSUB_EVIDENCE_INVALID")
    except R8RControllerError:
        raise
    except Exception as exc:
        raise R8RControllerError("R8R_QSUB_EVIDENCE_INVALID") from exc
    command = _recovery_qsub_command(implementation_commit)
    return {
        "schema_version": 1,
        "artifact_type": "lvef_c3_r8r_batch3_recovery_submission_v1",
        "status": "PASS_EXACT_ONE_CPU_RECOVERY_QSUB",
        "original_scientific_commit": ORIGINAL_SCIENTIFIC_COMMIT,
        "implementation_commit": implementation_commit,
        "attempt_id": ORIGINAL_ATTEMPT_ID,
        "batch_plan_sha256": ORIGINAL_PLAN_SHA256,
        "batch_id": FIXED_BATCH3_ID,
        "recovery_job_name": _recovery_job_name(implementation_commit),
        "recovery_job_id": recovery_job_id,
        "recovery_qsub_argv_sha256": _sha256_bytes(
            _canonical_bytes({"argv": command})
        ),
        "qsub_environment_sha256": qsub_environment_sha256,
        "recovery_qsub_evidence": dict(
            _qsub_evidence_authority(RECOVERY_SCHEDULER_ROOT, "recovery")
        ),
        "scheduler_submission_count": 1,
        "recovery_is_array": False,
        "gpu_requested": False,
        "automatic_retry_authorized": False,
        "cloud_requests": 0,
        "dicom_body_reads_by_submitter": 0,
        "gpu_executions_by_submitter": 0,
    }


def _validate_recovery_submission(
    *,
    current_job_id: str | None = None,
    wait: bool = False,
    require_retained_current: bool = True,
) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    if wait:
        deadline = time.monotonic() + 60.0
        while not os.path.lexists(RECOVERY_SUBMISSION_PATH):
            if time.monotonic() >= deadline:
                _fail("R8R_RECOVERY_SUBMISSION_RECEIPT_TIMEOUT")
            time.sleep(0.25)
    authority, authority_payload = _load_private_json(RECOVERY_AUTHORITY_PATH)
    receipt, _ = _load_private_json(RECOVERY_SUBMISSION_PATH)
    run = _load_fixed_original_run(
        scheduler_job_identity=(current_job_id or "R8R_RECOVERY_VALIDATION"),
        runtime_validation_context=stages.SEALED_SCHEDULER_RUNTIME_REPLAY,
    )
    _validate_recovery_authority(
        run,
        authority,
        require_retained_current=require_retained_current,
    )
    implementation_commit = _current_implementation_commit()
    qsub_environment_sha256 = str(authority.get("qsub_environment_sha256", ""))
    job_id = str(receipt.get("recovery_job_id", ""))
    expected = _build_recovery_submission_receipt(
        implementation_commit=implementation_commit,
        recovery_job_id=job_id,
        qsub_environment_sha256=qsub_environment_sha256,
    )
    if not _exact_typed_value_equal(receipt, expected):
        _fail("R8R_RECOVERY_SUBMISSION_RECEIPT_INVALID")
    if current_job_id is not None and current_job_id != job_id:
        _fail("R8R_RECOVERY_JOB_ID_MISMATCH")
    if _sha256_bytes(authority_payload) != core.sha256_file(
        RECOVERY_AUTHORITY_PATH
    ):
        _fail("R8R_RECOVERY_AUTHORITY_INVALID")
    return authority, receipt


def submit_batch3_recovery(
    *,
    qsub_runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
    qstat_runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> Mapping[str, Any]:
    scheduler.validate_scheduler_tools()
    implementation_commit = _current_implementation_commit()
    if implementation_commit == ORIGINAL_SCIENTIFIC_COMMIT:
        _fail("R8R_REPAIR_IMPLEMENTATION_COMMIT_REQUIRED")
    environment, _ = scheduler.build_qsub_environment()
    environment_sha256 = scheduler.qsub_environment_sha256(environment)
    _validate_no_active_jobs(environment, runner=qstat_runner)
    run = _load_fixed_original_run(
        scheduler_job_identity="R8R_RECOVERY_SUBMITTER",
        runtime_validation_context=stages.LIVE_RUNTIME_CAPTURE,
    )
    _validate_original_controls()
    _validate_frozen_prefix(run, include_batch3=False)
    _require_batch3_recovery_absent(run)
    retained = _batch3_retained_authority()
    if os.path.lexists(RECOVERY_ROOT):
        _fail("R8R_RECOVERY_ROOT_COLLISION")
    _create_private_directory_no_clobber(RECOVERY_ROOT)
    _create_private_directory_no_clobber(RECOVERY_SCHEDULER_ROOT)
    authority = _build_recovery_authority(
        run=run,
        implementation_commit=implementation_commit,
        qsub_environment_sha256=environment_sha256,
        retained_authority=retained,
    )
    _write_private_json(RECOVERY_AUTHORITY_PATH, authority)
    command = _recovery_qsub_command(implementation_commit)
    recovery_job_id = scheduler._capture_qsub(
        "recovery",
        command,
        root=RECOVERY_SCHEDULER_ROOT,
        environment=environment,
        runner=qsub_runner,
    )
    receipt = _build_recovery_submission_receipt(
        implementation_commit=implementation_commit,
        recovery_job_id=recovery_job_id,
        qsub_environment_sha256=environment_sha256,
    )
    _write_private_json(RECOVERY_SUBMISSION_PATH, receipt)
    _validate_recovery_submission(
        wait=False,
        require_retained_current=False,
    )
    return {
        "status": "RECOVERY_SUBMITTED",
        "recovery_job_id": recovery_job_id,
        "new_qsub_submissions": 1,
        "cloud_requests": 0,
        "dicom_body_reads": 0,
        "gpu_executions": 0,
        "embedding_generations": 0,
    }


def _raw_batch3_post_authority() -> Mapping[str, Any]:
    root = ATTEMPT_ROOT / "raw" / FIXED_BATCH3_ID / "objects"
    rows: list[list[Any]] = []
    count = 0
    total = 0
    try:
        entries = sorted(os.scandir(root), key=lambda item: item.name)
    except OSError as exc:
        raise R8RControllerError("R8R_BATCH3_RAW_RETENTION_INVALID") from exc
    for entry in entries:
        if (
            entry.is_symlink()
            or not entry.is_file(follow_symlinks=False)
            or not entry.name.endswith(".dcm")
        ):
            _fail("R8R_BATCH3_RAW_RETENTION_INVALID")
        path = Path(entry.path)
        row = _metadata_row(path, root=ATTEMPT_ROOT, kind="F")
        rows.append(row)
        count += 1
        total += int(row[6])
    if count != 18_653 or total != 68_725_707_170:
        _fail("R8R_BATCH3_RAW_RETENTION_INVALID")
    payload = b"".join(
        json.dumps(row, separators=(",", ":"), ensure_ascii=True).encode()
        + b"\n"
        for row in rows
    )
    return {
        "raw_dicom_files": count,
        "raw_dicom_bytes": total,
        "raw_metadata_projection_sha256": _sha256_bytes(payload),
        "raw_dicom_body_reads": 0,
    }


def _recovery_terminal_receipt(
    *,
    run: sequential.FullRun,
    final_receipt: Mapping[str, Any],
) -> Mapping[str, Any]:
    paths = sequential._batch_paths(run, FIXED_BATCH3_ID)
    authorization = (
        ATTEMPT_ROOT
        / "cache_retirement_authorizations"
        / f"{FIXED_BATCH3_ID}.authorization.json"
    )
    required = {
        "recovery_authority_sha256": RECOVERY_AUTHORITY_PATH,
        "recovery_submission_receipt_sha256": RECOVERY_SUBMISSION_PATH,
        "preservation_receipt_sha256": (
            paths["preservation"]
            / "batch_preservation_receipt.restricted.json"
        ),
        "cache_retirement_authorization_sha256": authorization,
        "cache_retirement_transition_sha256": paths["final_transition"],
        "final_ledger_sha256": paths["final_ledger"],
        "batch_finalization_receipt_sha256": paths["final_receipt"],
    }
    hashes: dict[str, str] = {}
    for key, path in required.items():
        try:
            hashes[key] = core.sha256_file(path)
        except Exception as exc:
            raise R8RControllerError("R8R_RECOVERY_TERMINAL_AUTHORITY_INVALID") from exc
    return {
        "schema_version": 1,
        "artifact_type": "lvef_c3_r8r_batch3_recovery_terminal_v1",
        "status": RECOVERY_STATUS,
        "original_scientific_commit": ORIGINAL_SCIENTIFIC_COMMIT,
        "implementation_commit": _current_implementation_commit(),
        "attempt_id": ORIGINAL_ATTEMPT_ID,
        "batch_plan_sha256": ORIGINAL_PLAN_SHA256,
        "batch_id": FIXED_BATCH3_ID,
        **hashes,
        "raw_retention_authority": dict(_raw_batch3_post_authority()),
        "n_selected_studies": int(final_receipt["n_selected_studies"]),
        "n_expected_objects": int(final_receipt["n_expected_objects"]),
        "expected_source_bytes": int(final_receipt["expected_source_bytes"]),
        "n_successfully_extracted_cines": int(
            final_receipt["n_successfully_extracted_cines"]
        ),
        "n_object_technical_dispositions": int(
            final_receipt["n_object_technical_dispositions"]
        ),
        "n_blocking_failures": int(final_receipt["n_blocking_failures"]),
        "n_clip_embeddings": int(final_receipt["n_clip_embeddings"]),
        "n_pooled_studies": int(final_receipt["n_pooled_studies"]),
        "n_no_cine_studies": int(final_receipt["n_no_cine_studies"]),
        "n_new_no_cine_studies": int(final_receipt["n_new_no_cine_studies"]),
        "raw_dicoms_retained": final_receipt["raw_dicoms_retained"],
        "extracted_cache_retired": final_receipt["extracted_cache_retired"],
        "download_reruns": 0,
        "dicom_extraction_reruns": 0,
        "echoprime_reruns": 0,
        "embedding_generations": 0,
        "cloud_requests": 0,
        "dicom_body_reads": 0,
        "gpu_executions": 0,
        "model_fitting_count": 0,
        "prediction_generation_count": 0,
        "confirmatory_performance_access_count": 0,
    }


def recover_batch3_preservation() -> Mapping[str, Any]:
    job_id = str(os.environ.get("JOB_ID", ""))
    task_id = str(os.environ.get("SGE_TASK_ID", "undefined"))
    if (
        JOB_RE.fullmatch(job_id) is None
        or task_id not in {"", "undefined"}
        or str(os.environ.get("CUDA_VISIBLE_DEVICES", "")) != ""
    ):
        _fail("R8R_RECOVERY_SCHEDULER_CONTEXT_INVALID")
    run = _load_fixed_original_run(
        scheduler_job_identity=job_id,
        runtime_validation_context=stages.SEALED_SCHEDULER_RUNTIME_REPLAY,
    )
    _validate_recovery_submission(current_job_id=job_id, wait=True)
    _validate_original_controls()
    _validate_frozen_prefix(run, include_batch3=False)
    if os.path.lexists(RECOVERY_TERMINAL_PATH):
        _fail("R8R_RECOVERY_ALREADY_COMPLETE")
    paths = sequential._batch_paths(run, FIXED_BATCH3_ID)
    try:
        preservation_receipt = preservation.preserve_batch(
            contract_path=run.contract_path,
            plan_path=run.plan_path,
            batch_id=FIXED_BATCH3_ID,
            attempt_id=run.attempt_id,
            governing_commit=run.authority.governing_commit,
            production_root=run.production_root,
            output_root=paths["preservation"],
            environment_receipt=run.authority.environment_receipt,
            checkpoint=run.authority.checkpoint,
            scheduler_job_identity=job_id,
            input_ledger=paths["pooling_ledger"],
            requirements=run.requirements,
            expected_runtime_authority=run.runtime_authority,
            scheduler_runner_path=RUNNER_PATH,
            runtime_validation_context=(
                stages.SEALED_SCHEDULER_RUNTIME_REPLAY
            ),
            artifact_validation_context=(
                preservation.R8R_FIXED_BATCH3_NO_DICOM_BODY
            ),
        )
    except Exception as exc:
        code = getattr(exc, "code", "R8R_BATCH3_PRESERVATION_FAILED")
        raw_substage = getattr(exc, "validation_substage", None)
        validation_substage = (
            raw_substage.value
            if isinstance(
                raw_substage,
                preservation.PreservationValidationSubstage,
            )
            else None
        )
        raise R8RControllerError(
            code if isinstance(code, str) else "R8R_BATCH3_PRESERVATION_FAILED",
            stage="BATCH_PRESERVATION",
            validation_substage=validation_substage,
        ) from exc
    try:
        authorization = sequential._cache_retirement_authorization(
            run=run, batch_id=FIXED_BATCH3_ID, paths=paths
        )
        sequential._execute_cache_retirement(
            run=run,
            batch_id=FIXED_BATCH3_ID,
            authorization_receipt_path=authorization,
            requirements=run.requirements,
            expected_runtime_authority=run.runtime_authority,
            test_only_synthetic_full_scope=False,
            artifact_validation_context=(
                retirement.R8R_FIXED_BATCH3_NO_DICOM_BODY
            ),
            scheduler_runner_path=RUNNER_PATH,
        )
        final_receipt = sequential._validate_batch_finalization(
            run=run, batch_id=FIXED_BATCH3_ID
        )
    except Exception as exc:
        code = getattr(exc, "code", "R8R_BATCH3_FINALIZATION_FAILED")
        raise R8RControllerError(
            code if isinstance(code, str) else "R8R_BATCH3_FINALIZATION_FAILED",
            stage="BATCH3_CACHE_RETIREMENT_AND_FINALIZATION",
        ) from exc
    if preservation_receipt.get("status") != "PASS_BATCH_CACHE_RETIREMENT_ELIGIBLE":
        _fail("R8R_BATCH3_PRESERVATION_FAILED")
    terminal = _recovery_terminal_receipt(
        run=run, final_receipt=final_receipt
    )
    _write_private_json(RECOVERY_TERMINAL_PATH, terminal)
    return terminal


def validate_recovery_terminal() -> Mapping[str, Any]:
    run = _load_fixed_original_run(
        scheduler_job_identity="R8R_RECOVERY_READBACK",
        runtime_validation_context=stages.SEALED_SCHEDULER_RUNTIME_REPLAY,
    )
    _validate_original_controls()
    _validate_recovery_submission(require_retained_current=False)
    final_receipt = sequential._validate_batch_finalization(
        run=run, batch_id=FIXED_BATCH3_ID
    )
    value, payload = _load_private_json(RECOVERY_TERMINAL_PATH)
    expected = _recovery_terminal_receipt(
        run=run, final_receipt=final_receipt
    )
    if not _exact_typed_value_equal(
        value, expected
    ) or _sha256_bytes(payload) != core.sha256_file(
        RECOVERY_TERMINAL_PATH
    ):
        _fail("R8R_RECOVERY_TERMINAL_RECEIPT_INVALID")
    cache = sequential._batch_paths(run, FIXED_BATCH3_ID)["extraction"] / "clips"
    if os.path.lexists(cache):
        _fail("R8R_RECOVERY_CACHE_RETIREMENT_INVALID")
    _validate_frozen_prefix(run, include_batch3=True)
    return value


def _continuation_capacity_receipt(
    *,
    run: sequential.FullRun,
    implementation_commit: str,
    observation: Mapping[str, Any],
    prefix_receipts: Sequence[str],
    recovery_terminal_sha256: str,
    recovery_job_id: str,
    recovery_scheduler_accounting: Mapping[str, Any],
    captured_at_utc: str,
) -> Mapping[str, Any]:
    try:
        validated_observation = (
            capacity.validate_fixed_r8r_continuation_capacity(
                run.plan, observation
            )
        )
    except Exception as exc:
        raise R8RControllerError(
            "R8R_CONTINUATION_CAPACITY_INVALID"
        ) from exc
    accounting = _validate_recovery_accounting_projection(
        recovery_scheduler_accounting,
        expected_job_id=recovery_job_id,
    )
    if (
        validated_observation.get("status") != CONTINUATION_CAPACITY_STATUS
        or set(validated_observation) != capacity.R8R_CONTINUATION_CAPACITY_KEYS
        or validated_observation.get("original_attempt_id") != ORIGINAL_ATTEMPT_ID
        or validated_observation.get("original_plan_sha256") != ORIGINAL_PLAN_SHA256
        or validated_observation.get("original_scientific_governing_commit")
        != ORIGINAL_SCIENTIFIC_COMMIT
        or validated_observation.get("continuation_first_task") != 4
        or validated_observation.get("continuation_last_task") != 19
        or validated_observation.get("continuation_task_count") != 16
        or validated_observation.get("native_capacity_snapshot_captures") != 1
        or validated_observation.get("native_quota_file_captures") != 1
        or validated_observation.get("capacity_command_captures") != 5
        or validated_observation.get("quota_reserve_gate_passed") is not True
        or validated_observation.get("physical_reserve_gate_passed") is not True
        or validated_observation.get("file_slot_gate_passed") is not True
        or any(
            validated_observation.get(key) != 0
            for key in capacity.R8R_CONTINUATION_ZERO_EFFECT_KEYS
        )
        or tuple(prefix_receipts[:2])
        != tuple(item[2] for item in PREFIX_RECEIPT_AUTHORITIES)
        or len(prefix_receipts) != 3
        or any(SHA_RE.fullmatch(item) is None for item in prefix_receipts)
        or SHA_RE.fullmatch(recovery_terminal_sha256) is None
        or re.fullmatch(
            r"20[0-9]{2}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z",
            captured_at_utc,
        )
        is None
    ):
        _fail("R8R_CONTINUATION_CAPACITY_INVALID")
    return {
        "schema_version": 1,
        "artifact_type": "lvef_c3_r8r_fixed_continuation_capacity_v1",
        "status": CONTINUATION_CAPACITY_STATUS,
        "original_scientific_commit": ORIGINAL_SCIENTIFIC_COMMIT,
        "implementation_commit": implementation_commit,
        "attempt_id": ORIGINAL_ATTEMPT_ID,
        "batch_plan_sha256": ORIGINAL_PLAN_SHA256,
        "captured_at_utc": captured_at_utc,
        "continuation_task_range": FIXED_CONTINUATION_TASK_RANGE,
        "continuation_task_count": len(FIXED_CONTINUATION_TASK_IDS),
        "continuation_max_concurrency": FIXED_CONTINUATION_MAX_CONCURRENCY,
        "prefix_final_receipt_sha256": list(prefix_receipts),
        "recovery_terminal_receipt_sha256": recovery_terminal_sha256,
        "recovery_scheduler_accounting": dict(accounting),
        "capacity_observation": dict(validated_observation),
        "active_extraction_caches": 0,
        "continuation_root_absent_at_capture": True,
        "continuation_claim_absent_at_capture": True,
        "continuation_submission_receipt_absent_at_capture": True,
        "cloud_requests": 0,
        "dicom_body_reads": 0,
        "npz_body_reads": 0,
        "scheduler_submissions": 0,
        "gpu_executions": 0,
        "embedding_generations": 0,
        "model_fitting_count": 0,
        "prediction_generation_count": 0,
        "confirmatory_performance_access_count": 0,
    }


def _continuation_capacity_summary(
    receipt: Mapping[str, Any], receipt_sha256: str
) -> Mapping[str, Any]:
    observation = receipt.get("capacity_observation")
    if not isinstance(observation, Mapping):
        _fail("R8R_CONTINUATION_CAPACITY_INVALID")
    keys = (
        "remaining_batch_count",
        "remaining_studies",
        "remaining_objects",
        "remaining_source_bytes",
        "continuation_increment_bytes",
        "required_file_slots",
        "research_quota_bytes",
        "research_usage_bytes",
        "research_filesystem_available_bytes",
        "research_file_slots_remaining",
        "quota_margin_beyond_reserve_bytes",
        "physical_margin_beyond_reserve_bytes",
        "file_slot_margin_after_demand",
    )
    projected = {key: observation.get(key) for key in keys}
    return {
        "schema_version": 1,
        "artifact_type": "lvef_c3_r8r_fixed_continuation_capacity_aggregate_safe_v1",
        "status": CONTINUATION_CAPACITY_STATUS,
        **projected,
        "capacity_receipt_sha256": receipt_sha256,
        "identifiers_emitted": False,
        "restricted_paths_emitted": False,
        "cloud_requests": 0,
        "scheduler_submissions": 0,
        "dicom_body_reads": 0,
        "npz_body_reads": 0,
        "gpu_executions": 0,
    }


def _continuation_claim(
    *,
    run: sequential.FullRun,
    implementation_commit: str,
    qsub_environment_sha256: str,
    prefix_receipts: Sequence[str],
    recovery_terminal_sha256: str,
    capacity_receipt_sha256: str,
    recovery_job_id: str,
    recovery_accounting_sha256: str,
) -> Mapping[str, Any]:
    if (
        SHA_RE.fullmatch(qsub_environment_sha256) is None
        or SHA_RE.fullmatch(recovery_terminal_sha256) is None
        or SHA_RE.fullmatch(capacity_receipt_sha256) is None
        or JOB_RE.fullmatch(recovery_job_id) is None
        or SHA_RE.fullmatch(recovery_accounting_sha256) is None
    ):
        _fail("R8R_CONTINUATION_CLAIM_INVALID")
    return {
        "schema_version": 1,
        "artifact_type": "lvef_c3_r8r_fixed_continuation_claim_v1",
        "status": "AUTHORIZED_FIXED_CONTINUATION_4_19",
        "original_scientific_commit": ORIGINAL_SCIENTIFIC_COMMIT,
        "implementation_commit": implementation_commit,
        "attempt_id": ORIGINAL_ATTEMPT_ID,
        "batch_plan_sha256": ORIGINAL_PLAN_SHA256,
        "original_claim_sha256": ORIGINAL_CLAIM_SHA256,
        "original_submission_receipt_sha256": ORIGINAL_SUBMISSION_SHA256,
        "prefix_final_receipt_sha256": list(prefix_receipts),
        "recovery_terminal_receipt_sha256": recovery_terminal_sha256,
        "continuation_capacity_receipt_sha256": capacity_receipt_sha256,
        "recovery_job_id": recovery_job_id,
        "recovery_scheduler_accounting_sha256": (
            recovery_accounting_sha256
        ),
        "runtime_authority_sha256": core.canonical_json_sha256(
            run.runtime_authority
        ),
        "qsub_environment_sha256": qsub_environment_sha256,
        "script_authority": _script_authority(),
        "continuation_task_range": FIXED_CONTINUATION_TASK_RANGE,
        "continuation_task_count": len(FIXED_CONTINUATION_TASK_IDS),
        "continuation_max_concurrency": FIXED_CONTINUATION_MAX_CONCURRENCY,
        "held_finalizer_count": 1,
        "automatic_retry_authorized": False,
        "whole_stage_retry_authorized": False,
        "third_continuation_submission_reachable": False,
        "cloud_requests_by_submitter": 0,
        "dicom_body_reads_by_submitter": 0,
        "npz_body_reads_by_submitter": 0,
        "gpu_executions_by_submitter": 0,
        "embedding_generations_by_submitter": 0,
        "model_fitting_authorized": False,
        "prediction_authorized": False,
        "confirmatory_performance_access_authorized": False,
    }


def _build_continuation_submission_receipt(
    *,
    implementation_commit: str,
    recovery_job_id: str,
    array_job_id: str,
    finalizer_job_id: str,
    qsub_environment_sha256: str,
    continuation_capacity_receipt_sha256: str,
    continuation_claim_sha256: str,
) -> Mapping[str, Any]:
    if (
        JOB_RE.fullmatch(recovery_job_id) is None
        or JOB_RE.fullmatch(array_job_id) is None
        or JOB_RE.fullmatch(finalizer_job_id) is None
        or len({recovery_job_id, array_job_id, finalizer_job_id}) != 3
        or SHA_RE.fullmatch(qsub_environment_sha256) is None
        or SHA_RE.fullmatch(continuation_capacity_receipt_sha256) is None
        or SHA_RE.fullmatch(continuation_claim_sha256) is None
    ):
        _fail("R8R_CONTINUATION_SUBMISSION_INVALID")
    try:
        if (
            _parse_r8r_array_qsub_stdout(
                scheduler._read_scheduler_evidence(
                    CONTINUATION_SCHEDULER_ROOT
                    / "array.qsub.stdout.restricted"
                )
            )
            != array_job_id
            or scheduler.parse_numeric_qsub_stdout(
                scheduler._read_scheduler_evidence(
                    CONTINUATION_SCHEDULER_ROOT
                    / "finalizer.qsub.stdout.restricted"
                )
            )
            != finalizer_job_id
        ):
            _fail("R8R_CONTINUATION_SUBMISSION_INVALID")
    except R8RControllerError:
        raise
    except Exception as exc:
        raise R8RControllerError("R8R_CONTINUATION_SUBMISSION_INVALID") from exc
    array_command = _continuation_array_command(implementation_commit)
    finalizer_command = _continuation_finalizer_command(
        implementation_commit, array_job_id
    )
    return {
        "schema_version": 1,
        "artifact_type": "lvef_c3_r8r_fixed_continuation_submission_v1",
        "status": "PASS_EXACT_ARRAY_4_19_AND_HELD_FINALIZER",
        "original_scientific_commit": ORIGINAL_SCIENTIFIC_COMMIT,
        "implementation_commit": implementation_commit,
        "attempt_id": ORIGINAL_ATTEMPT_ID,
        "batch_plan_sha256": ORIGINAL_PLAN_SHA256,
        "array_job_name": _continuation_array_job_name(implementation_commit),
        "finalizer_job_name": _continuation_finalizer_job_name(
            implementation_commit
        ),
        "array_job_id": array_job_id,
        "finalizer_job_id": finalizer_job_id,
        "array_qsub_argv_sha256": _sha256_bytes(
            _canonical_bytes({"argv": array_command})
        ),
        "finalizer_qsub_argv_sha256": _sha256_bytes(
            _canonical_bytes({"argv": finalizer_command})
        ),
        "qsub_environment_sha256": qsub_environment_sha256,
        "continuation_capacity_receipt_sha256": (
            continuation_capacity_receipt_sha256
        ),
        "continuation_claim_sha256": continuation_claim_sha256,
        "array_qsub_evidence": dict(
            _qsub_evidence_authority(CONTINUATION_SCHEDULER_ROOT, "array")
        ),
        "finalizer_qsub_evidence": dict(
            _qsub_evidence_authority(
                CONTINUATION_SCHEDULER_ROOT, "finalizer"
            )
        ),
        "scheduler_submission_count": 2,
        "scheduler_submission_maximum": 2,
        "array_task_range": FIXED_CONTINUATION_TASK_RANGE,
        "array_task_count": len(FIXED_CONTINUATION_TASK_IDS),
        "array_max_concurrency": FIXED_CONTINUATION_MAX_CONCURRENCY,
        "finalizer_held_on_array": True,
        "whole_stage_retry_authorized": False,
        "third_continuation_submission_reachable": False,
        "cloud_requests": 0,
        "dicom_body_reads_by_submitter": 0,
        "npz_body_reads_by_submitter": 0,
        "gpu_executions_by_submitter": 0,
    }


def submit_continuation(
    *,
    qsub_runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
    qstat_runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
    qacct_runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
    capacity_process_runner: Callable[..., Any] | None = None,
) -> Mapping[str, Any]:
    scheduler.validate_scheduler_tools()
    implementation_commit = _current_implementation_commit()
    environment, _ = scheduler.build_qsub_environment()
    environment_sha256 = scheduler.qsub_environment_sha256(environment)
    _validate_no_active_jobs(environment, runner=qstat_runner)
    run = _load_fixed_original_run(
        scheduler_job_identity="R8R_CONTINUATION_SUBMITTER",
        runtime_validation_context=stages.LIVE_RUNTIME_CAPTURE,
    )
    _validate_original_controls()
    terminal = validate_recovery_terminal()
    _, recovery_submission = _validate_recovery_submission(
        require_retained_current=False
    )
    recovery_job_id = str(recovery_submission.get("recovery_job_id", ""))
    recovery_accounting = _query_recovery_accounting(
        recovery_job_id=recovery_job_id,
        environment=environment,
        runner=qacct_runner,
    )
    prefix_receipts = _validate_frozen_prefix(run, include_batch3=True)
    recovery_terminal_sha256 = core.sha256_file(RECOVERY_TERMINAL_PATH)
    if terminal.get("status") != RECOVERY_STATUS:
        _fail("R8R_RECOVERY_TERMINAL_RECEIPT_INVALID")
    cache_inventory = sequential._extraction_cache_inventory(
        run.production_root, current_attempt_id=run.attempt_id
    )
    if cache_inventory.active != 0:
        _fail("R8R_ACTIVE_EXTRACTION_CACHE_PRESENT")
    if os.path.lexists(CONTINUATION_ROOT):
        _fail("R8R_CONTINUATION_ROOT_COLLISION")
    try:
        observation = capacity.probe_fixed_r8r_continuation_capacity(
            run.plan, process_runner=capacity_process_runner
        )
    except Exception as exc:
        raise R8RControllerError("R8R_CONTINUATION_CAPACITY_PROBE_FAILED") from exc
    if observation.get("status") != CONTINUATION_CAPACITY_STATUS:
        _fail("R8R_CONTINUATION_CAPACITY_BLOCKED")
    capacity_receipt = _continuation_capacity_receipt(
        run=run,
        implementation_commit=implementation_commit,
        observation=observation,
        prefix_receipts=prefix_receipts,
        recovery_terminal_sha256=recovery_terminal_sha256,
        recovery_job_id=recovery_job_id,
        recovery_scheduler_accounting=recovery_accounting,
        captured_at_utc=datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
    )
    _create_private_directory_no_clobber(CONTINUATION_ROOT)
    _create_private_directory_no_clobber(CONTINUATION_SCHEDULER_ROOT)
    capacity_sha = _write_private_json(
        CONTINUATION_CAPACITY_PATH, capacity_receipt
    )
    summary = _continuation_capacity_summary(capacity_receipt, capacity_sha)
    _write_private_json(CONTINUATION_CAPACITY_SUMMARY_PATH, summary)
    claim = _continuation_claim(
        run=run,
        implementation_commit=implementation_commit,
        qsub_environment_sha256=environment_sha256,
        prefix_receipts=prefix_receipts,
        recovery_terminal_sha256=recovery_terminal_sha256,
        capacity_receipt_sha256=capacity_sha,
        recovery_job_id=recovery_job_id,
        recovery_accounting_sha256=core.canonical_json_sha256(
            recovery_accounting
        ),
    )
    claim_sha = _write_private_json(CONTINUATION_CLAIM_PATH, claim)
    array_command = _continuation_array_command(implementation_commit)
    array_job_id = scheduler._capture_qsub(
        "array",
        array_command,
        root=CONTINUATION_SCHEDULER_ROOT,
        environment=environment,
        runner=qsub_runner,
        parser=_parse_r8r_array_qsub_stdout,
    )
    finalizer_command = _continuation_finalizer_command(
        implementation_commit, array_job_id
    )
    finalizer_job_id = scheduler._capture_qsub(
        "finalizer",
        finalizer_command,
        root=CONTINUATION_SCHEDULER_ROOT,
        environment=environment,
        runner=qsub_runner,
    )
    receipt = _build_continuation_submission_receipt(
        implementation_commit=implementation_commit,
        recovery_job_id=recovery_job_id,
        array_job_id=array_job_id,
        finalizer_job_id=finalizer_job_id,
        qsub_environment_sha256=environment_sha256,
        continuation_capacity_receipt_sha256=capacity_sha,
        continuation_claim_sha256=claim_sha,
    )
    _write_private_json(CONTINUATION_SUBMISSION_PATH, receipt)
    _validate_continuation_chain(
        run,
        current_job_id=None,
        role="array",
        wait=False,
    )
    return {
        "status": "CONTINUATION_SUBMITTED",
        "capacity_status": CONTINUATION_CAPACITY_STATUS,
        "array_job_id": array_job_id,
        "finalizer_job_id": finalizer_job_id,
        "task_range": FIXED_CONTINUATION_TASK_RANGE,
        "array_max_concurrency": FIXED_CONTINUATION_MAX_CONCURRENCY,
        "new_qsub_submissions": 2,
        "cloud_requests": 0,
        "dicom_body_reads": 0,
        "gpu_executions_before_continuation": 0,
        "embedding_generations": 0,
    }


def _validate_continuation_chain(
    run: sequential.FullRun,
    *,
    current_job_id: str | None,
    role: str,
    wait: bool,
) -> tuple[Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]]:
    if role not in {"array", "finalizer"}:
        _fail("R8R_CONTINUATION_ROLE_INVALID")
    if wait:
        deadline = time.monotonic() + 60.0
        while not os.path.lexists(CONTINUATION_SUBMISSION_PATH):
            if time.monotonic() >= deadline:
                _fail("R8R_CONTINUATION_SUBMISSION_RECEIPT_TIMEOUT")
            time.sleep(0.25)
    terminal = validate_recovery_terminal()
    _, recovery_submission = _validate_recovery_submission(
        require_retained_current=False
    )
    recovery_job_id = str(recovery_submission.get("recovery_job_id", ""))
    prefix_receipts = _validate_frozen_prefix(run, include_batch3=True)
    capacity_value, capacity_payload = _load_private_json(
        CONTINUATION_CAPACITY_PATH
    )
    summary_value, _ = _load_private_json(
        CONTINUATION_CAPACITY_SUMMARY_PATH
    )
    claim_value, claim_payload = _load_private_json(CONTINUATION_CLAIM_PATH)
    submission_value, submission_payload = _load_private_json(
        CONTINUATION_SUBMISSION_PATH
    )
    implementation_commit = _current_implementation_commit()
    recovery_sha = core.sha256_file(RECOVERY_TERMINAL_PATH)
    expected_capacity = _continuation_capacity_receipt(
        run=run,
        implementation_commit=implementation_commit,
        observation=capacity_value.get("capacity_observation", {}),
        prefix_receipts=prefix_receipts,
        recovery_terminal_sha256=recovery_sha,
        recovery_job_id=recovery_job_id,
        recovery_scheduler_accounting=capacity_value.get(
            "recovery_scheduler_accounting", {}
        ),
        captured_at_utc=str(capacity_value.get("captured_at_utc", "")),
    )
    capacity_sha = _sha256_bytes(capacity_payload)
    if (
        not _exact_typed_value_equal(capacity_value, expected_capacity)
        or not _exact_typed_value_equal(
            summary_value,
            _continuation_capacity_summary(expected_capacity, capacity_sha),
        )
    ):
        _fail("R8R_CONTINUATION_CAPACITY_INVALID")
    bound_qsub_sha = claim_value.get("qsub_environment_sha256")
    if not isinstance(bound_qsub_sha, str):
        _fail("R8R_CONTINUATION_CLAIM_INVALID")
    expected_claim = _continuation_claim(
        run=run,
        implementation_commit=implementation_commit,
        qsub_environment_sha256=bound_qsub_sha,
        prefix_receipts=prefix_receipts,
        recovery_terminal_sha256=recovery_sha,
        capacity_receipt_sha256=capacity_sha,
        recovery_job_id=recovery_job_id,
        recovery_accounting_sha256=core.canonical_json_sha256(
            expected_capacity["recovery_scheduler_accounting"]
        ),
    )
    if not _exact_typed_value_equal(claim_value, expected_claim):
        _fail("R8R_CONTINUATION_CLAIM_INVALID")
    array_id = str(submission_value.get("array_job_id", ""))
    finalizer_id = str(submission_value.get("finalizer_job_id", ""))
    expected_submission = _build_continuation_submission_receipt(
        implementation_commit=implementation_commit,
        recovery_job_id=recovery_job_id,
        array_job_id=array_id,
        finalizer_job_id=finalizer_id,
        qsub_environment_sha256=bound_qsub_sha,
        continuation_capacity_receipt_sha256=capacity_sha,
        continuation_claim_sha256=_sha256_bytes(claim_payload),
    )
    if not _exact_typed_value_equal(submission_value, expected_submission):
        _fail("R8R_CONTINUATION_SUBMISSION_INVALID")
    expected_job_id = array_id if role == "array" else finalizer_id
    if current_job_id is not None and current_job_id != expected_job_id:
        _fail("R8R_CONTINUATION_JOB_ID_MISMATCH")
    if (
        terminal.get("status") != RECOVERY_STATUS
        or _sha256_bytes(claim_payload) != core.sha256_file(CONTINUATION_CLAIM_PATH)
        or _sha256_bytes(submission_payload)
        != core.sha256_file(CONTINUATION_SUBMISSION_PATH)
    ):
        _fail("R8R_CONTINUATION_CHAIN_INVALID")
    return capacity_value, claim_value, submission_value


def validate_continuation_worker_submission(
    run: sequential.FullRun,
    *,
    current_job_id: str,
    role: str,
) -> Mapping[str, Any]:
    if (
        not isinstance(run, sequential.FullRun)
        or run.attempt_id != ORIGINAL_ATTEMPT_ID
        or run.plan_sha256 != ORIGINAL_PLAN_SHA256
        or run.authority.governing_commit != ORIGINAL_SCIENTIFIC_COMMIT
        or JOB_RE.fullmatch(current_job_id) is None
    ):
        _fail("R8R_CONTINUATION_WORKER_AUTHORITY_INVALID")
    return _validate_continuation_chain(
        run,
        current_job_id=current_job_id,
        role=role,
        wait=True,
    )[2]


def run_continuation_array_task() -> Mapping[str, Any]:
    job_id = str(os.environ.get("JOB_ID", ""))
    task_text = str(os.environ.get("SGE_TASK_ID", ""))
    if (
        JOB_RE.fullmatch(job_id) is None
        or not task_text.isdigit()
        or int(task_text) not in FIXED_CONTINUATION_TASK_IDS
    ):
        _fail("R8R_CONTINUATION_ARRAY_CONTEXT_INVALID")
    run = _load_fixed_original_run(
        scheduler_job_identity=job_id,
        runtime_validation_context=stages.SEALED_SCHEDULER_RUNTIME_REPLAY,
    )
    dependencies = sequential.FullDependencies(
        execution_context=sequential.R8R_FIXED_CONTINUATION
    )
    result = sequential.run_batch_task(
        task_id=int(task_text), run=run, dependencies=dependencies
    )
    if result.get("status") != "PASS_BATCH_FINALIZED":
        _fail("R8R_CONTINUATION_BATCH_NOT_FINALIZED")
    return result


def run_continuation_finalizer() -> Mapping[str, Any]:
    job_id = str(os.environ.get("JOB_ID", ""))
    task_text = str(os.environ.get("SGE_TASK_ID", "undefined"))
    if (
        JOB_RE.fullmatch(job_id) is None
        or task_text not in {"", "undefined"}
        or str(os.environ.get("CUDA_VISIBLE_DEVICES", "")) != ""
    ):
        _fail("R8R_CONTINUATION_FINALIZER_CONTEXT_INVALID")
    run = _load_fixed_original_run(
        scheduler_job_identity=job_id,
        runtime_validation_context=stages.SEALED_SCHEDULER_RUNTIME_REPLAY,
    )
    _validate_continuation_chain(
        run, current_job_id=job_id, role="finalizer", wait=True
    )
    receipts = [
        sequential._batch_paths(run, f"c3_batch_{index:03d}")["final_receipt"]
        for index in range(run.requirements.batch_count)
    ]
    output_root = run.attempt_root / "cohort_finalization"
    _ensure_private_directory(output_root)
    r8r_authority = finalizer.R8RImplementationAuthority(
        implementation_commit=_current_implementation_commit(),
        recovery_authority_sha256=core.sha256_file(RECOVERY_AUTHORITY_PATH),
        recovery_terminal_receipt_sha256=core.sha256_file(
            RECOVERY_TERMINAL_PATH
        ),
        continuation_capacity_receipt_sha256=core.sha256_file(
            CONTINUATION_CAPACITY_PATH
        ),
        continuation_claim_sha256=core.sha256_file(CONTINUATION_CLAIM_PATH),
        continuation_submission_receipt_sha256=core.sha256_file(
            CONTINUATION_SUBMISSION_PATH
        ),
    )
    summary = finalizer.finalize_receipts(
        receipts,
        expected_governing_commit=ORIGINAL_SCIENTIFIC_COMMIT,
        expected_attempt_id=ORIGINAL_ATTEMPT_ID,
        plan=run.plan,
        requirements=run.requirements,
        production_root=run.production_root,
        contract=run.contract,
        contract_path=run.contract_path,
        environment_receipt=run.authority.environment_receipt,
        cache_retirement_authorization_root=(
            run.attempt_root / "cache_retirement_authorizations"
        ),
        canonical_output_root=output_root,
        expected_runtime_authority=run.runtime_authority,
        expected_no_cine_studies=int(
            run.launch_authority["expected_no_cine_studies"]
        ),
        r8r_implementation_authority=r8r_authority,
    )
    if (
        summary.get("status") != "PASS_PRODUCTION_C3_FINALIZED"
        or summary.get("production_batches") != 19
        or summary.get("selected_studies") != 4_530
        or summary.get("pooled_imaging_eligible_studies") != 4_525
        or summary.get("no_cine_studies") != 5
        or summary.get("all_authority_bindings_identical") is not False
        or summary.get("all_scientific_authority_bindings_identical")
        is not True
        or summary.get("implementation_authority_epoch_count") != 2
        or summary.get("r8r_implementation_commit")
        != _current_implementation_commit()
        or SHA_RE.fullmatch(
            str(
                summary.get(
                    "r8r_recovery_continuation_authority_sha256"
                )
            )
        )
        is None
        or summary.get("model_fitting_count") != 0
        or summary.get("endpoint_prediction_count") != 0
        or summary.get("confirmatory_performance_access_count") != 0
    ):
        _fail("R8R_CONTINUATION_FINALIZATION_INVALID")
    finalizer.write_json_atomic(
        output_root / "full_c3_finalization.aggregate_safe.json", summary
    )
    return summary


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--submit-batch3-recovery", action="store_true")
    modes.add_argument("--recover-batch3-preservation", action="store_true")
    modes.add_argument("--validate-batch3-recovery", action="store_true")
    modes.add_argument("--submit-continuation", action="store_true")
    modes.add_argument("--run-continuation-array-task", action="store_true")
    modes.add_argument("--run-continuation-finalizer", action="store_true")
    return parser


def guarded_main(argv: Sequence[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        if args.submit_batch3_recovery:
            value = submit_batch3_recovery()
            print(f"R8R_BATCH3_RECOVERY_JOB_ID={value['recovery_job_id']}")
            print("R8R_NEW_QSUB_SUBMISSIONS=1")
        elif args.recover_batch3_preservation:
            recover_batch3_preservation()
            print("PASS_BATCH3_PRESERVATION_RECOVERY")
        elif args.validate_batch3_recovery:
            validate_recovery_terminal()
            print("R8R_BATCH3_RECOVERY_VALIDATION=PASS")
        elif args.submit_continuation:
            value = submit_continuation()
            print(f"R8R_CONTINUATION_ARRAY_JOB_ID={value['array_job_id']}")
            print(f"R8R_CONTINUATION_FINALIZER_JOB_ID={value['finalizer_job_id']}")
            print("R8R_CONTINUATION_TASK_RANGE=4-19")
            print("R8R_CONTINUATION_MAX_CONCURRENCY=1")
            print("R8R_NEW_QSUB_SUBMISSIONS=2")
        elif args.run_continuation_array_task:
            run_continuation_array_task()
            print("FULL_C3_ARRAY_TASK=PASS")
        else:
            run_continuation_finalizer()
            print("FULL_C3_COHORT_FINALIZER=PASS")
        return 0
    except R8RControllerError as exc:
        print(f"R8R_STATUS=BLOCKED_{exc.code}")
        if exc.stage is not None:
            print(f"R8R_FAILED_STAGE={exc.stage}")
        if exc.validation_substage is not None:
            print(
                "R8R_PRESERVATION_VALIDATION_SUBSTAGE="
                f"{exc.validation_substage}"
            )
        print("R8R_NEW_CLOUD_REQUESTS=0")
        print("R8R_NEW_DICOM_BODY_READS=0")
        print("R8R_NEW_GPU_EXECUTIONS_BEFORE_CONTINUATION=0")
        print("R8R_NEW_EMBEDDING_GENERATIONS=0")
        return 78
    except Exception as exc:
        code = getattr(exc, "code", "R8R_UNEXPECTED_SANITIZED_FAILURE")
        safe = code if isinstance(code, str) and SAFE_CODE_RE.fullmatch(code) else "R8R_UNEXPECTED_SANITIZED_FAILURE"
        print(f"R8R_STATUS=BLOCKED_{safe}")
        print("R8R_NEW_CLOUD_REQUESTS=0")
        print("R8R_NEW_DICOM_BODY_READS=0")
        print("R8R_NEW_GPU_EXECUTIONS_BEFORE_CONTINUATION=0")
        print("R8R_NEW_EMBEDDING_GENERATIONS=0")
        return 78


if __name__ == "__main__":
    raise SystemExit(guarded_main())
