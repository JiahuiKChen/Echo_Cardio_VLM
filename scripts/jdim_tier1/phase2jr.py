"""Wall-time-safe continuation utilities for the locked Phase 2J audit roster."""
from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping
from urllib.parse import quote, unquote

import numpy as np
import pandas as pd

from .audit_interface import (
    AUDIT_INTERFACE_READY,
    build_blinded_interface_package,
    validate_generated_interface_package,
)
from .phase2i import (
    AUDIT_INPUTS_TECHNICALLY_LOCKED,
    EXACT_MODEL_INPUT,
    LOCKED_ROSTER_SOURCE_RESTORED,
    MODEL_SOURCE_FRAME_POSITIONS,
    OFFICIAL_SOURCE_BASE,
    SOURCE_ACQUISITION_ONLY,
    VERIFIED_EQUIVALENT_REPLAY,
    _display_frame_grid,
    _opaque_media_id,
    _validate_dicom_file,
    _validate_relative_dicom_path,
)
from .safety import (
    Tier1BlockedError,
    assert_export_safe_frame,
    canonical_id_set_sha256,
    require_columns,
    require_restricted_destination,
    sha256_file,
    sha256_text,
)


COMPLETE_VERIFIED = "COMPLETE_VERIFIED"
PARTIAL_RESUMABLE = "PARTIAL_RESUMABLE"
RESTARTABLE_ZERO_PLACEHOLDER = "RESTARTABLE_ZERO_PLACEHOLDER"
MISSING = "MISSING"
UNEXPECTED_OR_INVALID = "UNEXPECTED_OR_INVALID"

RESTORATION_STATE_RESUMABLE = "RESTORATION_STATE_RESUMABLE"
RESTORATION_INCOMPLETE_RESUMABLE = "RESTORATION_INCOMPLETE_RESUMABLE"
BLOCKED_RESTORATION_STATE_INCONSISTENT = "BLOCKED_RESTORATION_STATE_INCONSISTENT"
BLOCKED_RESTORATION_CONTINUATION_STATE = "BLOCKED_RESTORATION_CONTINUATION_STATE"
BLOCKED_LOCKED_SOURCE_RESTORATION = "BLOCKED_LOCKED_SOURCE_RESTORATION"

INTERFACE_INCOMPLETE_RESUMABLE = "INTERFACE_INCOMPLETE_RESUMABLE"
BLOCKED_AUDIT_TECHNICAL_INVENTORY = "BLOCKED_AUDIT_TECHNICAL_INVENTORY"
BLOCKED_AUDIT_INTERFACE = "BLOCKED_AUDIT_INTERFACE"
READY_FOR_BLINDED_HUMAN_AUDIT = "READY_FOR_BLINDED_HUMAN_AUDIT"

RESTORATION_POLICY = "JDIM_PHASE2JR_LOCKED_RESTORATION_V1"
INTERFACE_POLICY = "JDIM_PHASE2JR_RESUMABLE_INTERFACE_V1"
QACCT_ACCOUNTING_VERIFIED = "QACCT_ACCOUNTING_VERIFIED"

PHASE2JR2_EXPECTED_STATE = {
    "requested_files": 4808,
    "complete_verified_files": 2342,
    "partial_resumable_files": 4,
    "restartable_zero_placeholder_files": 2111,
    "missing_files": 351,
    "invalid_files": 0,
    "unexpected_files": 0,
}

_QACCT_REQUIRED_FIELDS = {
    "jobnumber",
    "failed",
    "exit_status",
    "start_time",
    "end_time",
    "ru_wallclock",
    "maxvmem",
}
_QACCT_FIELD = re.compile(
    r"^[ \t]*(?P<name>[A-Za-z][A-Za-z0-9_]*)[ \t]+(?P<value>\S(?:.*\S)?)[ \t]*$"
)
_QACCT_FAILED = re.compile(r"^(?P<code>\d+)(?:\s*:\s*.*)?$")
_QACCT_MEMORY = re.compile(r"^\d+(?:\.\d+)?(?:[KMGTPE])?$")
_SCHEDULER_SUCCESS_CERTIFICATES = {
    LOCKED_ROSTER_SOURCE_RESTORED,
    RESTORATION_INCOMPLETE_RESUMABLE,
    AUDIT_INPUTS_TECHNICALLY_LOCKED,
    INTERFACE_INCOMPLETE_RESUMABLE,
    AUDIT_INTERFACE_READY,
    READY_FOR_BLINDED_HUMAN_AUDIT,
}


@dataclass(frozen=True)
class RestorationState:
    restricted_rows: pd.DataFrame
    safe_summary: dict[str, Any]


@dataclass(frozen=True)
class ContinuationResult:
    status: str
    safe_summary: dict[str, Any]
    certificate_path: Path


@dataclass(frozen=True)
class InterfaceContinuationResult:
    status: str
    safe_summary: dict[str, Any]
    certificate_path: Path


def _truth(value: Any) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def parse_qacct_output(raw_output: str) -> dict[str, Any]:
    """Parse completed-job SCC accounting by field name, independent of alignment."""

    if not isinstance(raw_output, str) or not raw_output.strip():
        raise Tier1BlockedError(
            BLOCKED_RESTORATION_CONTINUATION_STATE,
            "qacct output is empty",
        )
    fields: dict[str, str] = {}
    for raw_line in raw_output.splitlines():
        line = raw_line.strip()
        if not line or set(line) == {"="}:
            continue
        match = _QACCT_FIELD.fullmatch(raw_line)
        if match is None:
            raise Tier1BlockedError(
                BLOCKED_RESTORATION_CONTINUATION_STATE,
                "qacct output contains a malformed field",
            )
        name = match.group("name")
        value = match.group("value").strip()
        if name in fields:
            raise Tier1BlockedError(
                BLOCKED_RESTORATION_CONTINUATION_STATE,
                f"qacct field {name} is duplicated",
            )
        fields[name] = value

    missing = sorted(_QACCT_REQUIRED_FIELDS.difference(fields))
    if missing or not ({"hostname", "qname"} & fields.keys()):
        raise Tier1BlockedError(
            BLOCKED_RESTORATION_CONTINUATION_STATE,
            "qacct output is missing required fields",
        )
    try:
        jobnumber = int(fields["jobnumber"])
        exit_status = int(fields["exit_status"])
        failed_match = _QACCT_FAILED.fullmatch(fields["failed"])
        if failed_match is None:
            raise ValueError("invalid failed field")
        failed = int(failed_match.group("code"))
        wallclock = Decimal(fields["ru_wallclock"])
        start_time = datetime.strptime(fields["start_time"], "%a %b %d %H:%M:%S %Y")
        end_time = datetime.strptime(fields["end_time"], "%a %b %d %H:%M:%S %Y")
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise Tier1BlockedError(
            BLOCKED_RESTORATION_CONTINUATION_STATE,
            "qacct output contains a malformed required value",
        ) from exc
    if (
        jobnumber < 1
        or failed < 0
        or exit_status < 0
        or not wallclock.is_finite()
        or wallclock < 0
        or end_time < start_time
        or _QACCT_MEMORY.fullmatch(fields["maxvmem"]) is None
    ):
        raise Tier1BlockedError(
            BLOCKED_RESTORATION_CONTINUATION_STATE,
            "qacct output contains a contradictory required value",
        )
    return {
        "fields": fields,
        "jobnumber": jobnumber,
        "failed": failed,
        "exit_status": exit_status,
        "ru_wallclock": wallclock,
        "scheduler_success": failed == 0 and exit_status == 0,
        "raw_accounting_sha256": sha256_text(raw_output),
    }


def verify_qacct_accounting(
    raw_output: str,
    *,
    expected_jobnumber: int,
    expected_failed: int,
    expected_exit_status: int,
    expected_ru_wallclock: Decimal | int | str,
) -> dict[str, Any]:
    parsed = parse_qacct_output(raw_output)
    expected_wallclock = Decimal(str(expected_ru_wallclock))
    checks = {
        "jobnumber": parsed["jobnumber"] == expected_jobnumber,
        "failed": parsed["failed"] == expected_failed,
        "exit_status": parsed["exit_status"] == expected_exit_status,
        "ru_wallclock": parsed["ru_wallclock"] == expected_wallclock,
    }
    if not all(checks.values()):
        raise Tier1BlockedError(
            BLOCKED_RESTORATION_CONTINUATION_STATE,
            "qacct values differ from the locked accounting record",
        )
    return {
        "status": QACCT_ACCOUNTING_VERIFIED,
        "jobnumber": parsed["jobnumber"],
        "failed": parsed["failed"],
        "exit_status": parsed["exit_status"],
        "ru_wallclock": str(parsed["ru_wallclock"]),
        "scheduler_success": parsed["scheduler_success"],
        "raw_accounting_sha256": parsed["raw_accounting_sha256"],
    }


def verify_scheduler_certificate_agreement(
    parsed_accounting: Mapping[str, Any],
    certificate_status: str,
) -> dict[str, Any]:
    scheduler_success = bool(parsed_accounting.get("scheduler_success"))
    certificate_success = certificate_status in _SCHEDULER_SUCCESS_CERTIFICATES
    if scheduler_success != certificate_success:
        raise Tier1BlockedError(
            BLOCKED_RESTORATION_CONTINUATION_STATE,
            "scheduler and certificate states disagree",
        )
    return {
        "scheduler_success": scheduler_success,
        "certificate_status": certificate_status,
        "states_agree": True,
    }


def verify_qacct_certificate_agreement(
    raw_output: str,
    *,
    expected_jobnumber: int,
    certificate_status: str,
) -> dict[str, Any]:
    parsed = parse_qacct_output(raw_output)
    if parsed["jobnumber"] != expected_jobnumber:
        raise Tier1BlockedError(
            BLOCKED_RESTORATION_CONTINUATION_STATE,
            "qacct job number differs from the declared upstream dependency",
        )
    agreement = verify_scheduler_certificate_agreement(parsed, certificate_status)
    return {
        "status": QACCT_ACCOUNTING_VERIFIED,
        "jobnumber": parsed["jobnumber"],
        "failed": parsed["failed"],
        "exit_status": parsed["exit_status"],
        "scheduler_success": parsed["scheduler_success"],
        "certificate_status": certificate_status,
        "states_agree": agreement["states_agree"],
        "raw_accounting_sha256": parsed["raw_accounting_sha256"],
    }


def _atomic_write_text(path: Path, text: str, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    handle, temporary_name = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary_name, mode)
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    _atomic_write_text(path, json.dumps(dict(payload), indent=2, sort_keys=True) + "\n")


def _atomic_write_csv(path: Path, frame: pd.DataFrame) -> None:
    _atomic_write_text(path, frame.to_csv(index=False))


def _locked_file_rows(
    *,
    locked_url_list: Path,
    restoration_manifest_csv: Path,
    source_destination_root: Path,
    expected_files: int,
    expected_studies: int,
) -> tuple[pd.DataFrame, dict[str, set[str]]]:
    destination_root = require_restricted_destination(source_destination_root)
    urls = [line.strip() for line in locked_url_list.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(urls) != expected_files or len(set(urls)) != expected_files:
        raise Tier1BlockedError(
            BLOCKED_RESTORATION_STATE_INCONSISTENT,
            "locked URL count or uniqueness changed",
        )

    relative_paths: list[str] = []
    for url in urls:
        if not url.startswith(OFFICIAL_SOURCE_BASE):
            raise Tier1BlockedError(
                BLOCKED_RESTORATION_STATE_INCONSISTENT,
                "locked URL left the pinned official source",
            )
        relative = _validate_relative_dicom_path(unquote(url[len(OFFICIAL_SOURCE_BASE) :]))
        if OFFICIAL_SOURCE_BASE + quote(relative, safe="/") != url:
            raise Tier1BlockedError(
                BLOCKED_RESTORATION_STATE_INCONSISTENT,
                "locked URL is not canonically encoded",
            )
        relative_paths.append(relative)
    if len(set(relative_paths)) != expected_files:
        raise Tier1BlockedError(
            BLOCKED_RESTORATION_STATE_INCONSISTENT,
            "more than one locked URL maps to the same destination",
        )

    manifest = pd.read_csv(restoration_manifest_csv)
    require_columns(
        manifest,
        ["audit_id", "declared_source_dicom", "unique_linkage"],
        "locked restoration manifest",
    )
    if not manifest["unique_linkage"].map(_truth).all():
        raise Tier1BlockedError(
            BLOCKED_RESTORATION_STATE_INCONSISTENT,
            "locked restoration manifest contains ambiguous linkage",
        )
    manifest = manifest.copy()
    manifest["official_relative_path"] = manifest["declared_source_dicom"].map(
        _validate_relative_dicom_path
    )
    manifest_paths = set(manifest["official_relative_path"].astype(str))
    if manifest_paths != set(relative_paths):
        raise Tier1BlockedError(
            BLOCKED_RESTORATION_STATE_INCONSISTENT,
            "locked URL list differs from the locked restoration manifest",
        )
    study_ids = set(manifest["audit_id"].astype(str))
    if len(study_ids) != expected_studies:
        raise Tier1BlockedError(
            BLOCKED_RESTORATION_STATE_INCONSISTENT,
            "locked restoration study count changed",
        )

    required_by_study = {
        str(audit_id): set(group["official_relative_path"].astype(str))
        for audit_id, group in manifest.groupby("audit_id", sort=False)
    }
    rows = pd.DataFrame(
        {
            "request_order": range(len(urls)),
            "official_url": urls,
            "official_relative_path": relative_paths,
        }
    )
    rows["restricted_destination"] = rows["official_relative_path"].map(
        lambda value: str((destination_root / value).resolve())
    )
    rows["restricted_partial"] = rows["restricted_destination"].map(lambda value: value + ".part")
    if any(
        destination_root not in Path(value).parents
        for column in ("restricted_destination", "restricted_partial")
        for value in rows[column]
    ):
        raise Tier1BlockedError(
            BLOCKED_RESTORATION_STATE_INCONSISTENT,
            "locked destination escaped the restricted root",
        )
    return rows, required_by_study


def _unexpected_files(
    source_destination_root: Path,
    rows: pd.DataFrame,
) -> list[Path]:
    source_destination_root = source_destination_root.resolve()
    if not source_destination_root.exists():
        return []
    expected = {str(Path(value).absolute()) for value in rows["restricted_destination"]}
    expected.update(str(Path(value).absolute()) for value in rows["restricted_partial"])
    return [
        candidate
        for candidate in source_destination_root.rglob("*")
        if (candidate.is_file() or candidate.is_symlink())
        and str(candidate.absolute()) not in expected
    ]


def _state_summary(
    rows: pd.DataFrame,
    required_by_study: Mapping[str, set[str]],
    *,
    locked_url_list: Path,
    source_destination_root: Path,
    source_commit: str,
    unexpected_files: int,
) -> dict[str, Any]:
    counts = rows["state"].value_counts().to_dict()
    complete = int(counts.get(COMPLETE_VERIFIED, 0))
    partial = int(counts.get(PARTIAL_RESUMABLE, 0))
    zero_placeholder = int(counts.get(RESTARTABLE_ZERO_PLACEHOLDER, 0))
    missing = int(counts.get(MISSING, 0))
    invalid = int(counts.get(UNEXPECTED_OR_INVALID, 0))
    classified = complete + partial + zero_placeholder + missing + invalid
    if classified != len(rows):
        raise AssertionError("restoration states do not reconcile")
    complete_paths = set(
        rows.loc[rows["state"].eq(COMPLETE_VERIFIED), "official_relative_path"].astype(str)
    )
    complete_studies = sum(
        bool(paths) and paths.issubset(complete_paths) for paths in required_by_study.values()
    )
    partial_studies = sum(
        bool(paths.intersection(complete_paths)) and not paths.issubset(complete_paths)
        for paths in required_by_study.values()
    )
    remaining = rows.loc[~rows["state"].eq(COMPLETE_VERIFIED), "official_relative_path"]
    status = (
        BLOCKED_RESTORATION_STATE_INCONSISTENT
        if invalid or unexpected_files
        else LOCKED_ROSTER_SOURCE_RESTORED
        if complete == len(rows) and complete_studies == len(required_by_study)
        else RESTORATION_STATE_RESUMABLE
    )
    summary = {
        "status": status,
        "policy": RESTORATION_POLICY,
        "source_commit": source_commit,
        "requested_files": int(len(rows)),
        "requested_studies": int(len(required_by_study)),
        "complete_verified_files": complete,
        "partial_resumable_files": partial,
        "restartable_zero_placeholder_files": zero_placeholder,
        "missing_files": missing,
        "invalid_files": invalid,
        "unexpected_files": int(unexpected_files),
        "complete_studies": int(complete_studies),
        "partial_studies": int(partial_studies),
        "complete_size_bytes": int(
            rows.loc[rows["state"].eq(COMPLETE_VERIFIED), "size_bytes"].sum()
        ),
        "partial_size_bytes": int(
            rows.loc[rows["state"].eq(PARTIAL_RESUMABLE), "size_bytes"].sum()
        ),
        "zero_placeholder_size_bytes": int(
            rows.loc[rows["state"].eq(RESTARTABLE_ZERO_PLACEHOLDER), "size_bytes"].sum()
        ),
        "remaining_files": int(len(rows) - complete),
        "remaining_file_set_sha256": canonical_id_set_sha256(remaining),
        "locked_url_list_sha256": sha256_file(locked_url_list),
        "locked_file_set_sha256": canonical_id_set_sha256(rows["official_relative_path"]),
        "destination_identity_sha256": sha256_text(str(source_destination_root.resolve())),
        "states_reconcile": bool(classified == len(rows)),
        "complete_partial_overlap": False,
        "credentials_logged": False,
        "clinical_content_reviewed": False,
        "ocr_used": False,
    }
    assert_export_safe_frame(pd.DataFrame([summary]), "Phase 2J-R restoration state")
    return summary


def assess_restoration_state(
    *,
    locked_url_list: Path,
    restoration_manifest_csv: Path,
    source_destination_root: Path,
    restricted_output_csv: Path | None,
    safe_output_json: Path | None,
    source_commit: str,
    expected_files: int = 4808,
    expected_studies: int = 109,
    validator: Callable[[Path], tuple[bool, str]] = _validate_dicom_file,
) -> RestorationState:
    """Classify every locked destination without changing the source destination."""

    rows, required_by_study = _locked_file_rows(
        locked_url_list=locked_url_list,
        restoration_manifest_csv=restoration_manifest_csv,
        source_destination_root=source_destination_root,
        expected_files=expected_files,
        expected_studies=expected_studies,
    )
    states: list[str] = []
    details: list[str] = []
    sizes: list[int] = []
    for row in rows.itertuples(index=False):
        destination = Path(row.restricted_destination)
        partial = Path(row.restricted_partial)
        destination_present = os.path.lexists(destination)
        partial_present = os.path.lexists(partial)
        if destination_present and partial_present:
            state, detail, size = UNEXPECTED_OR_INVALID, "complete_partial_overlap", 0
        elif destination_present:
            if (
                destination.is_symlink()
                or not destination.is_file()
                or destination.stat().st_size <= 0
            ):
                state, detail, size = UNEXPECTED_OR_INVALID, "invalid_complete_file", 0
            else:
                valid, detail = validator(destination)
                state = COMPLETE_VERIFIED if valid else UNEXPECTED_OR_INVALID
                size = int(destination.stat().st_size) if valid else 0
        elif partial_present:
            if partial.is_symlink() or not partial.is_file():
                state, detail, size = UNEXPECTED_OR_INVALID, "invalid_partial_file", 0
            elif partial.stat().st_size > 0:
                state, detail, size = (
                    PARTIAL_RESUMABLE,
                    "partial_nonzero",
                    int(partial.stat().st_size),
                )
            else:
                state, detail, size = RESTARTABLE_ZERO_PLACEHOLDER, "partial_zero_restartable", 0
        else:
            state, detail, size = MISSING, "not_started", 0
        states.append(state)
        details.append(detail)
        sizes.append(size)
    rows["state"] = states
    rows["validation_detail"] = details
    rows["size_bytes"] = sizes
    unexpected = _unexpected_files(source_destination_root, rows)
    summary = _state_summary(
        rows,
        required_by_study,
        locked_url_list=locked_url_list,
        source_destination_root=source_destination_root,
        source_commit=source_commit,
        unexpected_files=len(unexpected),
    )
    if (restricted_output_csv is None) != (safe_output_json is None):
        raise ValueError("restoration assessment outputs must be both present or both omitted")
    if restricted_output_csv is not None and safe_output_json is not None:
        restricted_output_csv = require_restricted_destination(restricted_output_csv)
        _atomic_write_csv(restricted_output_csv, rows)
        _atomic_write_json(safe_output_json, summary)
    return RestorationState(rows, summary)


def verify_phase2jr2_preflight_state(summary: Mapping[str, Any]) -> dict[str, Any]:
    checks = {
        "status": summary.get("status") == RESTORATION_STATE_RESUMABLE,
        "states_reconcile": summary.get("states_reconcile") is True,
        **{
            field: summary.get(field) == expected
            for field, expected in PHASE2JR2_EXPECTED_STATE.items()
        },
    }
    if not all(checks.values()):
        raise Tier1BlockedError(
            BLOCKED_RESTORATION_STATE_INCONSISTENT,
            "actual restoration state differs from the locked Phase 2J-R2 preflight",
        )
    return {"status": RESTORATION_STATE_RESUMABLE, "checks": checks}


def _download_locked_file(
    row: Mapping[str, Any],
    *,
    netrc_path: Path,
    validator: Callable[[Path], tuple[bool, str]] = _validate_dicom_file,
) -> tuple[bool, str]:
    destination = Path(str(row["restricted_destination"]))
    partial = Path(str(row["restricted_partial"]))
    if str(partial) != str(destination) + ".part":
        return False, "partial_path_mismatch"
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(destination.parent, 0o700)
    if os.path.lexists(destination):
        if destination.is_symlink() or not destination.is_file():
            return False, "existing_final_not_regular"
        valid, detail = validator(destination)
        return (True, "already_available_verified") if valid else (False, f"existing_{detail}")
    if os.path.lexists(partial) and (partial.is_symlink() or not partial.is_file()):
        return False, "existing_partial_not_regular"
    wget = shutil.which("wget")
    if not wget:
        return False, "wget_unavailable"
    environment = os.environ.copy()
    environment["HOME"] = str(netrc_path.parent)
    result = subprocess.run(
        [
            wget,
            "--quiet",
            "--continue",
            "--netrc",
            "--https-only",
            "--max-redirect=0",
            "--timeout=60",
            "--tries=3",
            f"--output-document={partial}",
            str(row["official_url"]),
        ],
        check=False,
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if result.returncode != 0:
        return False, f"wget_exit_{result.returncode}"
    if not partial.is_file() or partial.stat().st_size <= 0:
        return False, "empty_partial"
    valid, detail = validator(partial)
    if not valid:
        return False, f"downloaded_{detail}"
    os.replace(partial, destination)
    os.chmod(destination, 0o600)
    return True, "restored"


def _checkpoint_payload(
    *,
    state: RestorationState,
    status: str,
    source_commit: str,
    continuation_label: str,
    started_at_monotonic: float,
    stopped_softly: bool,
) -> dict[str, Any]:
    payload = dict(state.safe_summary)
    payload.update(
        {
            "status": status,
            "continuation_label": continuation_label,
            "source_commit": source_commit,
            "elapsed_seconds": round(time.monotonic() - started_at_monotonic, 3),
            "stopped_softly": bool(stopped_softly),
            "safe_continuation": status == RESTORATION_INCOMPLETE_RESUMABLE,
        }
    )
    assert_export_safe_frame(pd.DataFrame([payload]), "Phase 2J-R restoration checkpoint")
    return payload


def _write_final_restoration_manifest(rows: pd.DataFrame, path: Path) -> None:
    final = rows.copy()
    final["success"] = final["state"].eq(COMPLETE_VERIFIED)
    # The locked file-set identity and structural validation are certified here.
    # Avoid a second multi-gigabyte read at the wall-time boundary; the complete
    # restricted manifest itself is hash-locked in the completion certificate.
    final["local_sha256"] = ""
    _atomic_write_csv(path, final)


def resume_locked_restoration(
    *,
    locked_url_list: Path,
    restoration_manifest_csv: Path,
    source_destination_root: Path,
    run_root: Path,
    netrc_path: Path,
    source_commit: str,
    continuation_label: str,
    expected_files: int = 4808,
    expected_studies: int = 109,
    max_workers: int = 2,
    checkpoint_every: int = 25,
    checkpoint_seconds: float = 900.0,
    soft_stop_seconds: float = 38_700.0,
    incomplete_status: str = RESTORATION_INCOMPLETE_RESUMABLE,
    downloader: Callable[..., tuple[bool, str]] = _download_locked_file,
    validator: Callable[[Path], tuple[bool, str]] = _validate_dicom_file,
) -> ContinuationResult:
    """Resume only locked incomplete files and stop cleanly before wall time."""

    if max_workers < 1 or max_workers > 2:
        raise ValueError("Phase 2J-R restoration permits one or two workers")
    if checkpoint_every < 1 or checkpoint_seconds <= 0 or soft_stop_seconds < 0:
        raise ValueError("invalid checkpoint or soft-stop configuration")
    if not netrc_path.is_file() or (netrc_path.stat().st_mode & 0o777) != 0o600:
        raise Tier1BlockedError(
            BLOCKED_RESTORATION_CONTINUATION_STATE,
            "official-source authentication is unavailable",
        )
    run_root = require_restricted_destination(run_root)
    restricted_root = run_root / "restricted" / "restoration"
    safe_root = run_root / "aggregate_safe"
    if (safe_root / "phase2jr_restoration_certificate.json").exists():
        raise Tier1BlockedError(
            BLOCKED_RESTORATION_CONTINUATION_STATE,
            "immutable restoration run already has a terminal certificate",
        )
    restricted_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    safe_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    started = time.monotonic()
    state = assess_restoration_state(
        locked_url_list=locked_url_list,
        restoration_manifest_csv=restoration_manifest_csv,
        source_destination_root=source_destination_root,
        restricted_output_csv=restricted_root / "restoration_state_restricted.csv",
        safe_output_json=safe_root / "restoration_state_summary.json",
        source_commit=source_commit,
        expected_files=expected_files,
        expected_studies=expected_studies,
        validator=validator,
    )
    if state.safe_summary["status"] == BLOCKED_RESTORATION_STATE_INCONSISTENT:
        certificate = _checkpoint_payload(
            state=state,
            status=BLOCKED_RESTORATION_STATE_INCONSISTENT,
            source_commit=source_commit,
            continuation_label=continuation_label,
            started_at_monotonic=started,
            stopped_softly=False,
        )
        certificate_path = safe_root / "phase2jr_restoration_certificate.json"
        _atomic_write_json(certificate_path, certificate)
        return ContinuationResult(certificate["status"], certificate, certificate_path)

    _, required_by_study = _locked_file_rows(
        locked_url_list=locked_url_list,
        restoration_manifest_csv=restoration_manifest_csv,
        source_destination_root=source_destination_root,
        expected_files=expected_files,
        expected_studies=expected_studies,
    )
    working_rows = state.restricted_rows.copy()
    row_index_by_order = {
        int(value): index for index, value in working_rows["request_order"].items()
    }
    transfer_records: list[dict[str, Any]] = []
    transfer_journal = restricted_root / "restoration_transfer_journal_restricted.csv"

    stop_requested = threading.Event()
    previous_handlers: dict[int, Any] = {}

    def request_stop(_signum: int, _frame: Any) -> None:
        stop_requested.set()

    for signum in (signal.SIGTERM, signal.SIGINT):
        previous_handlers[signum] = signal.getsignal(signum)
        signal.signal(signum, request_stop)
    completed_since_checkpoint = 0
    last_checkpoint = time.monotonic()
    try:
        pending = state.restricted_rows.loc[
            state.restricted_rows["state"].isin(
                [PARTIAL_RESUMABLE, RESTARTABLE_ZERO_PLACEHOLDER, MISSING]
            )
        ].copy()
        pending["state_priority"] = pending["state"].map(
            {PARTIAL_RESUMABLE: 0, RESTARTABLE_ZERO_PLACEHOLDER: 1, MISSING: 2}
        )
        pending = pending.sort_values(["state_priority", "request_order"], kind="stable")
        records = pending.to_dict(orient="records")
        next_index = 0
        active: dict[Future[tuple[bool, str]], Mapping[str, Any]] = {}
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            while next_index < len(records) or active:
                elapsed = time.monotonic() - started
                while (
                    next_index < len(records)
                    and len(active) < max_workers
                    and not stop_requested.is_set()
                    and elapsed < soft_stop_seconds
                ):
                    row = records[next_index]
                    future = executor.submit(
                        downloader,
                        row,
                        netrc_path=netrc_path,
                        validator=validator,
                    )
                    active[future] = row
                    next_index += 1
                    elapsed = time.monotonic() - started
                if not active:
                    break
                done, _ = wait(active, timeout=1.0, return_when=FIRST_COMPLETED)
                for future in done:
                    row = active.pop(future)
                    try:
                        success, detail = future.result()
                    except Exception as exc:  # fail closed without exposing command content
                        success, detail = False, f"downloader_exception_{type(exc).__name__}"
                    destination = Path(str(row["restricted_destination"]))
                    partial = Path(str(row["restricted_partial"]))
                    if success and (not destination.is_file() or destination.stat().st_size <= 0):
                        success, detail = False, "downloader_reported_success_without_final_file"
                    row_index = row_index_by_order[int(row["request_order"])]
                    if success:
                        completed_since_checkpoint += 1
                        working_rows.at[row_index, "state"] = COMPLETE_VERIFIED
                        working_rows.at[row_index, "validation_detail"] = str(detail)
                        working_rows.at[row_index, "size_bytes"] = int(destination.stat().st_size)
                    else:
                        if partial.is_symlink() or (
                            os.path.lexists(partial) and not partial.is_file()
                        ):
                            working_rows.at[row_index, "state"] = UNEXPECTED_OR_INVALID
                            working_rows.at[row_index, "validation_detail"] = str(detail)
                            working_rows.at[row_index, "size_bytes"] = 0
                            stop_requested.set()
                        elif partial.is_file() and partial.stat().st_size > 0:
                            working_rows.at[row_index, "state"] = PARTIAL_RESUMABLE
                            working_rows.at[row_index, "validation_detail"] = str(detail)
                            working_rows.at[row_index, "size_bytes"] = int(partial.stat().st_size)
                        elif partial.is_file():
                            working_rows.at[row_index, "state"] = RESTARTABLE_ZERO_PLACEHOLDER
                            working_rows.at[row_index, "validation_detail"] = str(detail)
                            working_rows.at[row_index, "size_bytes"] = 0
                        else:
                            working_rows.at[row_index, "state"] = MISSING
                            working_rows.at[row_index, "validation_detail"] = str(detail)
                            working_rows.at[row_index, "size_bytes"] = 0
                    transfer_records.append(
                        {
                            "completion_sequence": len(transfer_records) + 1,
                            "request_order": int(row["request_order"]),
                            "official_relative_path": str(row["official_relative_path"]),
                            "restricted_destination": str(row["restricted_destination"]),
                            "initial_state": str(row["state"]),
                            "initial_size_bytes": int(row["size_bytes"]),
                            "success": bool(success),
                            "state": str(working_rows.at[row_index, "state"]),
                            "detail": str(detail),
                            "size_bytes": int(working_rows.at[row_index, "size_bytes"]),
                        }
                    )
                    _atomic_write_csv(transfer_journal, pd.DataFrame(transfer_records))
                now = time.monotonic()
                if completed_since_checkpoint >= checkpoint_every or now - last_checkpoint >= checkpoint_seconds:
                    checkpoint_summary = _state_summary(
                        working_rows,
                        required_by_study,
                        locked_url_list=locked_url_list,
                        source_destination_root=source_destination_root,
                        source_commit=source_commit,
                        unexpected_files=0,
                    )
                    _atomic_write_csv(
                        restricted_root / "restoration_state_restricted.csv", working_rows
                    )
                    _atomic_write_json(
                        safe_root / "restoration_state_summary.json", checkpoint_summary
                    )
                    checkpoint_state = RestorationState(working_rows.copy(), checkpoint_summary)
                    checkpoint = _checkpoint_payload(
                        state=checkpoint_state,
                        status=RESTORATION_INCOMPLETE_RESUMABLE,
                        source_commit=source_commit,
                        continuation_label=continuation_label,
                        started_at_monotonic=started,
                        stopped_softly=False,
                    )
                    _atomic_write_json(safe_root / "restoration_checkpoint.json", checkpoint)
                    completed_since_checkpoint = 0
                    last_checkpoint = now
        final_state = assess_restoration_state(
            locked_url_list=locked_url_list,
            restoration_manifest_csv=restoration_manifest_csv,
            source_destination_root=source_destination_root,
            restricted_output_csv=restricted_root / "restoration_state_restricted.csv",
            safe_output_json=safe_root / "restoration_state_summary.json",
            source_commit=source_commit,
            expected_files=expected_files,
            expected_studies=expected_studies,
            validator=validator,
        )
    finally:
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)

    final_status = str(final_state.safe_summary["status"])
    if final_status == RESTORATION_STATE_RESUMABLE:
        final_status = incomplete_status
    results_path = restricted_root / "source_restoration_results_restricted.csv"
    _write_final_restoration_manifest(final_state.restricted_rows, results_path)
    certificate = _checkpoint_payload(
        state=final_state,
        status=final_status,
        source_commit=source_commit,
        continuation_label=continuation_label,
        started_at_monotonic=started,
        stopped_softly=bool(
            stop_requested.is_set() or time.monotonic() - started >= soft_stop_seconds
        ),
    )
    certificate["restoration_results_sha256"] = sha256_file(results_path)
    certificate["restricted_state_sha256"] = sha256_file(
        restricted_root / "restoration_state_restricted.csv"
    )
    if final_status == LOCKED_ROSTER_SOURCE_RESTORED:
        certificate["final_manifest_complete"] = True
        certificate["safe_continuation"] = False
    certificate_path = safe_root / "phase2jr_restoration_certificate.json"
    _atomic_write_json(certificate_path, certificate)
    return ContinuationResult(final_status, certificate, certificate_path)


def verify_restoration_certificate(
    *,
    certificate_path: Path,
    results_path: Path,
    expected_source_commit: str,
    expected_url_list_sha256: str,
    expected_files: int = 4808,
    expected_studies: int = 109,
) -> dict[str, Any]:
    payload = json.loads(certificate_path.read_text(encoding="utf-8"))
    checks = {
        "status": payload.get("status") == LOCKED_ROSTER_SOURCE_RESTORED,
        "source_commit": payload.get("source_commit") == expected_source_commit,
        "url_hash": payload.get("locked_url_list_sha256") == expected_url_list_sha256,
        "requested_files": payload.get("requested_files") == expected_files,
        "requested_studies": payload.get("requested_studies") == expected_studies,
        "complete_files": payload.get("complete_verified_files") == expected_files,
        "complete_studies": payload.get("complete_studies") == expected_studies,
        "remaining_files": payload.get("remaining_files") == 0,
        "partial_files": payload.get("partial_resumable_files") == 0,
        "zero_placeholder_files": payload.get("restartable_zero_placeholder_files") == 0,
        "invalid_files": payload.get("invalid_files") == 0,
        "unexpected_files": payload.get("unexpected_files") == 0,
        "results_hash": payload.get("restoration_results_sha256") == sha256_file(results_path),
    }
    if not all(checks.values()):
        raise Tier1BlockedError(
            BLOCKED_LOCKED_SOURCE_RESTORATION,
            "restoration certificate failed its exact gate",
        )
    return {"status": LOCKED_ROSTER_SOURCE_RESTORED, "checks": checks}


def verify_restoration_continuation_certificate(
    *,
    certificate_path: Path,
    results_path: Path,
    expected_source_commit: str,
    expected_url_list_sha256: str,
    expected_destination_root: Path,
    expected_files: int = 4808,
    expected_studies: int = 109,
) -> dict[str, Any]:
    payload = json.loads(certificate_path.read_text(encoding="utf-8"))
    count_fields = (
        "complete_verified_files",
        "partial_resumable_files",
        "restartable_zero_placeholder_files",
        "missing_files",
        "invalid_files",
    )
    counts_present = all(
        isinstance(payload.get(field), int) and int(payload[field]) >= 0
        for field in count_fields
    )
    classified = sum(int(payload[field]) for field in count_fields) if counts_present else -1
    remaining_hash = str(payload.get("remaining_file_set_sha256", ""))
    checks = {
        "status": payload.get("status") == RESTORATION_INCOMPLETE_RESUMABLE,
        "source_commit": payload.get("source_commit") == expected_source_commit,
        "url_hash": payload.get("locked_url_list_sha256") == expected_url_list_sha256,
        "destination": payload.get("destination_identity_sha256")
        == sha256_text(str(expected_destination_root.resolve())),
        "requested_files": payload.get("requested_files") == expected_files,
        "requested_studies": payload.get("requested_studies") == expected_studies,
        "counts_present": counts_present,
        "classified_files": classified == expected_files,
        "remaining_files": int(payload.get("remaining_files", 0)) > 0,
        "remaining_hash": len(remaining_hash) == 64
        and all(character in "0123456789abcdef" for character in remaining_hash),
        "invalid_files": payload.get("invalid_files") == 0,
        "unexpected_files": payload.get("unexpected_files") == 0,
        "safe_continuation": payload.get("safe_continuation") is True,
        "results_hash": payload.get("restoration_results_sha256") == sha256_file(results_path),
    }
    if not all(checks.values()):
        raise Tier1BlockedError(
            BLOCKED_RESTORATION_CONTINUATION_STATE,
            "restoration continuation certificate failed its exact gate",
        )
    return {"status": RESTORATION_INCOMPLETE_RESUMABLE, "checks": checks}


def verify_interface_continuation_certificate(
    *,
    certificate_path: Path,
    expected_source_commit: str,
) -> dict[str, Any]:
    payload = json.loads(certificate_path.read_text(encoding="utf-8"))
    checks = {
        "status": payload.get("status") == INTERFACE_INCOMPLETE_RESUMABLE,
        "source_commit": payload.get("source_commit") == expected_source_commit,
        "safe_continuation": payload.get("safe_continuation") is True,
        "ocr": payload.get("ocr_used") is False,
        "annotations": payload.get("clinical_annotations_generated") is False,
    }
    if not all(checks.values()):
        raise Tier1BlockedError(
            BLOCKED_AUDIT_INTERFACE,
            "interface continuation certificate failed its exact gate",
        )
    return {"status": INTERFACE_INCOMPLETE_RESUMABLE, "checks": checks}


def _media_valid(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size <= 0:
        return False


def _write_media_png(destination: Path, image_rgb: np.ndarray) -> None:
    import cv2

    staging_root = destination.parent / ".staging"
    staging_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.stem}-",
        suffix=".png",
        dir=staging_root,
    )
    os.close(handle)
    try:
        if not cv2.imwrite(temporary_name, cv2.cvtColor(image_rgb, cv2.COLOR_RGB2BGR)):
            raise OSError("failed to write restricted audit media")
        os.chmod(temporary_name, 0o600)
        os.replace(temporary_name, destination)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)
    try:
        import cv2

        return cv2.imread(str(path), cv2.IMREAD_UNCHANGED) is not None
    except Exception:
        return False


def _render_media_row(row: Mapping[str, Any], media_root: Path) -> dict[str, Any]:
    import pydicom

    from extract_mimic_echo_cines import normalize_pixels

    audit_id = str(row["audit_id"])
    clip_audit_id = str(row["clip_audit_id"])
    tier = str(row["evidence_tier"])
    source_media_id = ""
    model_input_media_id = ""
    if _truth(row["source_viewable"]):
        source_media_id = _opaque_media_id(audit_id, clip_audit_id, "source")
        destination = media_root / f"{source_media_id}.png"
        if destination.exists() and not _media_valid(destination):
            raise Tier1BlockedError(BLOCKED_AUDIT_INTERFACE, "existing source media is invalid")
        if not destination.exists():
            dataset = pydicom.dcmread(str(row["source_path"]), stop_before_pixels=False)
            raw = normalize_pixels(dataset)
            indices = [int(value) for value in json.loads(str(row["source_model_frame_indices_json"]))]
            if len(indices) != 16 or any(value < 0 or value >= len(raw) for value in indices):
                raise Tier1BlockedError(BLOCKED_AUDIT_INTERFACE, "source display indices are invalid")
            grid = _display_frame_grid(raw[indices])
            _write_media_png(destination, grid)
    if tier in {EXACT_MODEL_INPUT, VERIFIED_EQUIVALENT_REPLAY}:
        model_input_media_id = _opaque_media_id(audit_id, clip_audit_id, "model")
        destination = media_root / f"{model_input_media_id}.png"
        if destination.exists() and not _media_valid(destination):
            raise Tier1BlockedError(BLOCKED_AUDIT_INTERFACE, "existing model media is invalid")
        if not destination.exists():
            processed_path = Path(str(row["processed_path"]))
            if not processed_path.is_file():
                raise Tier1BlockedError(
                    BLOCKED_AUDIT_INTERFACE,
                    "verified model-input tier lacks retained processed input",
                )
            with np.load(processed_path, allow_pickle=False) as archive:
                stored = np.asarray(archive["frames"])
            grid = _display_frame_grid(stored[list(MODEL_SOURCE_FRAME_POSITIONS)])
            _write_media_png(destination, grid)
    return {
        "audit_id": audit_id,
        "clip_audit_id": clip_audit_id,
        "evidence_tier": tier,
        "source_media_id": source_media_id,
        "model_input_media_id": model_input_media_id,
        "model_input_verified": tier in {EXACT_MODEL_INPUT, VERIFIED_EQUIVALENT_REPLAY},
        "source_only": tier == SOURCE_ACQUISITION_ONLY,
    }


def _existing_media_record(row: Mapping[str, Any], media_root: Path) -> dict[str, Any] | None:
    audit_id = str(row["audit_id"])
    clip_audit_id = str(row["clip_audit_id"])
    tier = str(row["evidence_tier"])
    source_id = (
        _opaque_media_id(audit_id, clip_audit_id, "source")
        if _truth(row["source_viewable"])
        else ""
    )
    model_id = (
        _opaque_media_id(audit_id, clip_audit_id, "model")
        if tier in {EXACT_MODEL_INPUT, VERIFIED_EQUIVALENT_REPLAY}
        else ""
    )
    required = [media_root / f"{value}.png" for value in (source_id, model_id) if value]
    existing = [path.exists() for path in required]
    if any(existing) and not all(existing):
        return None
    if required and all(existing):
        if not all(_media_valid(path) for path in required):
            raise Tier1BlockedError(BLOCKED_AUDIT_INTERFACE, "existing media failed validation")
        return {
            "audit_id": audit_id,
            "clip_audit_id": clip_audit_id,
            "evidence_tier": tier,
            "source_media_id": source_id,
            "model_input_media_id": model_id,
            "model_input_verified": tier in {EXACT_MODEL_INPUT, VERIFIED_EQUIVALENT_REPLAY},
            "source_only": tier == SOURCE_ACQUISITION_ONLY,
        }
    return None


def _media_checkpoint(
    *,
    records: Iterable[Mapping[str, Any]],
    total_clips: int,
    technical_inventory_sha256: str,
    source_commit: str,
    status: str,
    run_root: Path,
) -> dict[str, Any]:
    records_frame = pd.DataFrame(list(records))
    restricted_path = run_root / "restricted" / "media_progress_restricted.csv"
    _atomic_write_csv(restricted_path, records_frame)
    completed = int(len(records_frame))
    payload = {
        "status": status,
        "policy": INTERFACE_POLICY,
        "source_commit": source_commit,
        "total_clips": int(total_clips),
        "completed_clips": completed,
        "remaining_clips": int(total_clips - completed),
        "technical_inventory_sha256": technical_inventory_sha256,
        "progress_manifest_sha256": sha256_file(restricted_path),
        "ocr_used": False,
        "clinical_annotations_generated": False,
        "public_network_binding_required": False,
        "safe_continuation": status == INTERFACE_INCOMPLETE_RESUMABLE,
    }
    assert_export_safe_frame(pd.DataFrame([payload]), "Phase 2J-R media checkpoint")
    _atomic_write_json(run_root / "aggregate_safe" / "interface_checkpoint.json", payload)
    return payload


def resume_audit_media(
    *,
    technical_inventory_csv: Path,
    persistent_root: Path,
    run_root: Path,
    source_commit: str,
    checkpoint_every: int = 25,
    checkpoint_seconds: float = 900.0,
    soft_stop_seconds: float = 38_700.0,
    renderer: Callable[[Mapping[str, Any], Path], dict[str, Any]] = _render_media_row,
) -> dict[str, Any]:
    persistent_root = require_restricted_destination(persistent_root)
    run_root = require_restricted_destination(run_root)
    media_stage_root = persistent_root / "restricted" / "audit_media"
    media_root = media_stage_root / "media"
    media_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    run_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    rows = pd.read_csv(technical_inventory_csv).fillna("")
    require_columns(
        rows,
        [
            "audit_id",
            "clip_audit_id",
            "evidence_tier",
            "source_viewable",
            "source_path",
            "processed_path",
            "source_model_frame_indices_json",
        ],
        "Phase 2J-R technical inventory",
    )
    if rows[["audit_id", "clip_audit_id"]].astype(str).duplicated().any():
        raise Tier1BlockedError(BLOCKED_AUDIT_INTERFACE, "technical inventory contains duplicate clips")
    started = time.monotonic()
    records: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows.to_dict(orient="records"):
        existing = _existing_media_record(row, media_root)
        if existing is not None:
            records[(str(row["audit_id"]), str(row["clip_audit_id"]))] = existing
    stop_requested = threading.Event()
    previous_handlers: dict[int, Any] = {}

    def request_stop(_signum: int, _frame: Any) -> None:
        stop_requested.set()

    for signum in (signal.SIGTERM, signal.SIGINT):
        previous_handlers[signum] = signal.getsignal(signum)
        signal.signal(signum, request_stop)
    since_checkpoint = 0
    last_checkpoint = time.monotonic()
    try:
        for row in rows.to_dict(orient="records"):
            key = (str(row["audit_id"]), str(row["clip_audit_id"]))
            if key in records:
                continue
            if stop_requested.is_set() or time.monotonic() - started >= soft_stop_seconds:
                break
            records[key] = renderer(row, media_root)
            since_checkpoint += 1
            now = time.monotonic()
            if since_checkpoint >= checkpoint_every or now - last_checkpoint >= checkpoint_seconds:
                _media_checkpoint(
                    records=records.values(),
                    total_clips=len(rows),
                    technical_inventory_sha256=sha256_file(technical_inventory_csv),
                    source_commit=source_commit,
                    status=INTERFACE_INCOMPLETE_RESUMABLE,
                    run_root=run_root,
                )
                since_checkpoint = 0
                last_checkpoint = now
    finally:
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
    complete = len(records) == len(rows)
    status = "AUDIT_MEDIA_READY" if complete else INTERFACE_INCOMPLETE_RESUMABLE
    checkpoint = _media_checkpoint(
        records=records.values(),
        total_clips=len(rows),
        technical_inventory_sha256=sha256_file(technical_inventory_csv),
        source_commit=source_commit,
        status=status,
        run_root=run_root,
    )
    if not complete:
        return checkpoint
    manifest = pd.DataFrame(list(records.values()))
    manifest = manifest.merge(
        rows[["audit_id", "clip_audit_id"]].astype(str).assign(_order=range(len(rows))),
        on=["audit_id", "clip_audit_id"],
        how="left",
        validate="one_to_one",
    ).sort_values("_order", kind="stable").drop(columns="_order")
    manifest_path = media_stage_root / "technical_interface_manifest_restricted.csv"
    _atomic_write_csv(manifest_path, manifest)
    expected_media = {
        str(value)
        for column in ("source_media_id", "model_input_media_id")
        for value in manifest[column]
        if str(value)
    }
    observed_media = {path.stem for path in media_root.glob("*.png") if path.is_file()}
    if observed_media != expected_media:
        raise Tier1BlockedError(
            BLOCKED_AUDIT_INTERFACE,
            "persistent media set differs from the locked technical inventory",
        )
    summary = {
        "status": "AUDIT_MEDIA_READY",
        "clips": int(len(manifest)),
        "source_displays": int(manifest["source_media_id"].astype(bool).sum()),
        "verified_model_input_displays": int(manifest["model_input_media_id"].astype(bool).sum()),
        "source_only_displays": int(manifest["source_only"].astype(bool).sum()),
        "media_size_bytes": int(sum(path.stat().st_size for path in media_root.glob("*.png"))),
        "ocr_used": False,
        "automated_content_annotation": False,
        "technical_inventory_sha256": sha256_file(technical_inventory_csv),
        "technical_interface_manifest_sha256": sha256_file(manifest_path),
    }
    _atomic_write_json(media_stage_root / "audit_media_summary.json", summary)
    return summary


def _technical_inventory_paths(persistent_root: Path) -> tuple[Path, Path, Path]:
    bundle = persistent_root / "restricted" / "technical_inventory_bundle"
    inventory = bundle / "restricted" / "technical_input_inventory_restricted.csv"
    summary = bundle / "aggregate_safe" / "technical_input_inventory_summary.json"
    return bundle, inventory, summary


def _publish_technical_inventory_safe_files(
    *,
    persistent_root: Path,
    bundle: Path,
) -> None:
    source = bundle / "aggregate_safe"
    destination = persistent_root / "aggregate_safe" / "technical_inventory"
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    for name in ("technical_input_inventory_summary.json", "technical_input_tier_counts.csv"):
        path = source / name
        if not path.is_file():
            raise Tier1BlockedError(
                BLOCKED_AUDIT_TECHNICAL_INVENTORY,
                "technical inventory bundle lacks an aggregate-safe artifact",
            )
        _atomic_write_text(destination / name, path.read_text(encoding="utf-8"))


def _validate_technical_inventory_bundle(
    *,
    persistent_root: Path,
    clip_roster_csv: Path,
    audit_linkage_csv: Path,
    restored_source_results_csv: Path,
    source_commit: str,
) -> tuple[Path, dict[str, Any]]:
    bundle, inventory, summary_path = _technical_inventory_paths(persistent_root)
    if not bundle.is_dir() or not inventory.is_file() or not summary_path.is_file():
        raise Tier1BlockedError(
            BLOCKED_AUDIT_TECHNICAL_INVENTORY,
            "published technical inventory bundle is incomplete",
        )
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    checks = {
        "status": summary.get("status") == AUDIT_INPUTS_TECHNICALLY_LOCKED,
        "inventory_hash": summary.get("technical_inventory_sha256") == sha256_file(inventory),
        "clip_roster_hash": summary.get("clip_roster_sha256") == sha256_file(clip_roster_csv),
        "audit_linkage_hash": summary.get("audit_linkage_sha256") == sha256_file(audit_linkage_csv),
        "restoration_hash": summary.get("restoration_results_sha256")
        == sha256_file(restored_source_results_csv),
        "physical_studies": summary.get("physical_studies") == 116,
        "clips": summary.get("clips") == 5071,
        "content_mismatches": summary.get("content_affecting_mismatches") == 0,
    }
    if not all(checks.values()):
        raise Tier1BlockedError(
            BLOCKED_AUDIT_TECHNICAL_INVENTORY,
            "technical inventory bundle failed its exact gate",
        )
    _publish_technical_inventory_safe_files(persistent_root=persistent_root, bundle=bundle)
    certificate = {
        "status": AUDIT_INPUTS_TECHNICALLY_LOCKED,
        "source_commit": source_commit,
        "technical_inventory_sha256": sha256_file(inventory),
        "technical_summary_sha256": sha256_file(summary_path),
        "restoration_results_sha256": sha256_file(restored_source_results_csv),
        "clips": int(summary["clips"]),
        "physical_studies": int(summary["physical_studies"]),
        "content_affecting_mismatches": int(summary["content_affecting_mismatches"]),
        "clinical_content_annotations_recorded": False,
        "ocr_used": False,
    }
    assert_export_safe_frame(pd.DataFrame([certificate]), "Phase 2J-R technical lock")
    certificate_path = persistent_root / "aggregate_safe" / "technical_lock_certificate.json"
    if certificate_path.is_file():
        existing = json.loads(certificate_path.read_text(encoding="utf-8"))
        if existing != certificate:
            raise Tier1BlockedError(
                BLOCKED_AUDIT_TECHNICAL_INVENTORY,
                "technical lock certificate differs from the validated bundle",
            )
    else:
        _atomic_write_json(certificate_path, certificate)
    return inventory, summary


def _validated_technical_inventory(
    *,
    persistent_root: Path,
    clip_roster_csv: Path,
    audit_linkage_csv: Path,
    restored_source_results_csv: Path,
    source_commit: str,
    run_root: Path,
    soft_stop_seconds: float,
    checkpoint_seconds: float,
) -> tuple[Path | None, dict[str, Any]]:
    bundle, _inventory, _summary_path = _technical_inventory_paths(persistent_root)
    if bundle.exists():
        return _validate_technical_inventory_bundle(
            persistent_root=persistent_root,
            clip_roster_csv=clip_roster_csv,
            audit_linkage_csv=audit_linkage_csv,
            restored_source_results_csv=restored_source_results_csv,
            source_commit=source_commit,
        )
    if soft_stop_seconds <= 120:
        return None, {
            "status": INTERFACE_INCOMPLETE_RESUMABLE,
            "stage": "technical_inventory_not_started_before_soft_stop",
            "source_commit": source_commit,
            "safe_continuation": True,
            "ocr_used": False,
            "clinical_annotations_generated": False,
        }

    staging_bundle = run_root / "restricted" / "technical_inventory_bundle_stage"
    if staging_bundle.exists():
        raise Tier1BlockedError(
            BLOCKED_AUDIT_TECHNICAL_INVENTORY,
            "immutable run root already contains an incomplete technical-inventory stage",
        )
    staging_restricted = staging_bundle / "restricted"
    staging_safe = staging_bundle / "aggregate_safe"
    run_safe = run_root / "aggregate_safe"
    run_restricted = run_root / "restricted"
    run_safe.mkdir(parents=True, exist_ok=True, mode=0o700)
    run_restricted.mkdir(parents=True, exist_ok=True, mode=0o700)
    log_path = run_restricted / "technical_inventory_builder.log"
    command = [
        sys.executable,
        str(Path(__file__).resolve().parents[1] / "run_jdim_phase2i.py"),
        "build-inventory",
        "--clip-roster-csv",
        str(clip_roster_csv),
        "--audit-linkage-csv",
        str(audit_linkage_csv),
        "--restored-source-results-csv",
        str(restored_source_results_csv),
        "--restricted-output-root",
        str(staging_restricted),
        "--safe-output-dir",
        str(staging_safe),
    ]
    stop_requested = threading.Event()
    previous_handlers: dict[int, Any] = {}

    def request_stop(_signum: int, _frame: Any) -> None:
        stop_requested.set()

    for signum in (signal.SIGTERM, signal.SIGINT):
        previous_handlers[signum] = signal.getsignal(signum)
        signal.signal(signum, request_stop)
    started = time.monotonic()
    last_checkpoint = started
    stopped_softly = False
    returncode: int | None = None
    try:
        with log_path.open("w", encoding="utf-8") as log:
            process = subprocess.Popen(
                command,
                cwd=Path(__file__).resolve().parents[2],
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            while process.poll() is None:
                now = time.monotonic()
                if stop_requested.is_set() or now - started >= soft_stop_seconds - 60:
                    stopped_softly = True
                    process.terminate()
                    try:
                        process.wait(timeout=30)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=30)
                    break
                if now - last_checkpoint >= checkpoint_seconds:
                    checkpoint = {
                        "status": INTERFACE_INCOMPLETE_RESUMABLE,
                        "stage": "technical_inventory_running",
                        "source_commit": source_commit,
                        "elapsed_seconds": round(now - started, 3),
                        "safe_continuation": True,
                        "ocr_used": False,
                        "clinical_annotations_generated": False,
                    }
                    assert_export_safe_frame(
                        pd.DataFrame([checkpoint]), "Phase 2J-R technical checkpoint"
                    )
                    _atomic_write_json(run_safe / "interface_checkpoint.json", checkpoint)
                    last_checkpoint = now
                time.sleep(1)
            returncode = process.poll()
    finally:
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
    if stopped_softly:
        summary = {
            "status": INTERFACE_INCOMPLETE_RESUMABLE,
            "stage": "technical_inventory_interrupted_at_soft_stop",
            "source_commit": source_commit,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "safe_continuation": True,
            "ocr_used": False,
            "clinical_annotations_generated": False,
        }
        assert_export_safe_frame(pd.DataFrame([summary]), "Phase 2J-R technical stop")
        _atomic_write_json(run_safe / "interface_checkpoint.json", summary)
        return None, summary
    if returncode != 0:
        raise Tier1BlockedError(
            BLOCKED_AUDIT_TECHNICAL_INVENTORY,
            "technical inventory builder exited without a valid completion bundle",
        )
    if bundle.exists():
        raise Tier1BlockedError(
            BLOCKED_AUDIT_TECHNICAL_INVENTORY,
            "technical inventory destination appeared during staging",
        )
    bundle.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.replace(staging_bundle, bundle)
    return _validate_technical_inventory_bundle(
        persistent_root=persistent_root,
        clip_roster_csv=clip_roster_csv,
        audit_linkage_csv=audit_linkage_csv,
        restored_source_results_csv=restored_source_results_csv,
        source_commit=source_commit,
    )


def _validate_ready_interface(
    *,
    persistent_root: Path,
    run_root: Path,
    source_commit: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    ready_certificate = persistent_root / "aggregate_safe" / "phase2jr_ready_certificate.json"
    technical_manifest = (
        persistent_root
        / "restricted"
        / "audit_media"
        / "technical_interface_manifest_restricted.csv"
    )
    interface_root = persistent_root / "restricted" / "interface"
    persistent_validation = persistent_root / "aggregate_safe" / "interface_validation.json"
    inventory = _technical_inventory_paths(persistent_root)[1]
    required = [
        ready_certificate,
        technical_manifest,
        inventory,
        interface_root / "interface_summary.json",
        interface_root / "primary_reader_manifest.json",
        interface_root / "second_reader_manifest.json",
        interface_root / "interface_policy.json",
        persistent_validation,
    ]
    if any(not path.is_file() for path in required):
        raise Tier1BlockedError(BLOCKED_AUDIT_INTERFACE, "ready interface package is incomplete")
    payload = json.loads(ready_certificate.read_text(encoding="utf-8"))
    summary = json.loads((interface_root / "interface_summary.json").read_text(encoding="utf-8"))
    checks = {
        "status": payload.get("status") == READY_FOR_BLINDED_HUMAN_AUDIT,
        "source_commit": payload.get("source_commit") == source_commit,
        "technical_inventory": payload.get("technical_inventory_sha256") == sha256_file(inventory),
        "technical_manifest": payload.get("technical_manifest_sha256")
        == sha256_file(technical_manifest),
        "primary_package": payload.get("primary_package_sha256")
        == sha256_file(interface_root / "primary_reader_manifest.json"),
        "second_package": payload.get("second_reader_package_sha256")
        == sha256_file(interface_root / "second_reader_manifest.json"),
        "policy": payload.get("interface_policy_sha256")
        == sha256_file(interface_root / "interface_policy.json"),
        "validation": payload.get("validation_sha256") == sha256_file(persistent_validation),
        "interface_status": summary.get("status") == AUDIT_INTERFACE_READY,
        "primary_studies": summary.get("primary_studies") == 116,
        "second_reader_studies": summary.get("second_reader_studies") == 24,
        "clips": summary.get("clips") == 5071,
        "primary_clip_reads": summary.get("primary_clip_reads") == 5071,
        "second_reader_clip_reads": summary.get("second_reader_clip_reads") == 1043,
        "blinding": summary.get("reader_blinding_validated") is True,
        "resume": summary.get("autosave_resume_validated") is True,
        "no_public_binding": summary.get("public_network_binding_required") is False,
        "no_ocr": summary.get("ocr_available") is False,
        "no_annotations": summary.get("automated_content_annotation") is False,
    }
    if not all(checks.values()):
        raise Tier1BlockedError(BLOCKED_AUDIT_INTERFACE, "ready interface failed its exact gate")
    validation = validate_generated_interface_package(
        interface_root=interface_root,
        checkpoint_parent=run_root / "restricted" / "interface_validation",
    )
    if validation.get("status") != "AUDIT_INTERFACE_VALIDATED":
        raise Tier1BlockedError(BLOCKED_AUDIT_INTERFACE, "completed interface validation failed")
    return payload, validation


def continue_audit_interface(
    *,
    persistent_root: Path,
    run_root: Path,
    restored_source_results_csv: Path,
    clip_roster_csv: Path,
    audit_linkage_csv: Path,
    primary_reader_manifest_csv: Path,
    second_reader_manifest_csv: Path,
    source_commit: str,
    checkpoint_every: int = 25,
    checkpoint_seconds: float = 900.0,
    soft_stop_seconds: float = 38_700.0,
    incomplete_status: str = INTERFACE_INCOMPLETE_RESUMABLE,
) -> InterfaceContinuationResult:
    persistent_root = require_restricted_destination(persistent_root)
    run_root = require_restricted_destination(run_root)
    run_certificate = run_root / "aggregate_safe" / "phase2jr_interface_run_certificate.json"
    if run_certificate.exists():
        raise Tier1BlockedError(
            BLOCKED_AUDIT_INTERFACE,
            "immutable interface run already has a terminal certificate",
        )
    run_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    (run_root / "aggregate_safe").mkdir(parents=True, exist_ok=True, mode=0o700)
    overall_started = time.monotonic()
    ready_certificate = persistent_root / "aggregate_safe" / "phase2jr_ready_certificate.json"
    if ready_certificate.is_file():
        payload, validation = _validate_ready_interface(
            persistent_root=persistent_root,
            run_root=run_root,
            source_commit=source_commit,
        )
        validation_path = run_root / "aggregate_safe" / "interface_validation.json"
        _atomic_write_json(validation_path, validation)
        no_op = {
            "status": READY_FOR_BLINDED_HUMAN_AUDIT,
            "source_commit": source_commit,
            "no_op_validation": True,
            "ready_certificate_sha256": sha256_file(ready_certificate),
            "validation_sha256": sha256_file(validation_path),
            "primary_studies": int(payload["primary_studies"]),
            "second_reader_studies": int(payload["second_reader_studies"]),
            "clips": int(payload["clips"]),
        }
        assert_export_safe_frame(pd.DataFrame([no_op]), "Phase 2J-R interface no-op")
        certificate_path = run_root / "aggregate_safe" / "phase2jr_interface_run_certificate.json"
        _atomic_write_json(certificate_path, no_op)
        return InterfaceContinuationResult(no_op["status"], no_op, certificate_path)

    remaining = max(0.0, soft_stop_seconds - (time.monotonic() - overall_started))
    inventory, technical_summary = _validated_technical_inventory(
        persistent_root=persistent_root,
        clip_roster_csv=clip_roster_csv,
        audit_linkage_csv=audit_linkage_csv,
        restored_source_results_csv=restored_source_results_csv,
        source_commit=source_commit,
        run_root=run_root,
        soft_stop_seconds=remaining,
        checkpoint_seconds=checkpoint_seconds,
    )
    if inventory is None:
        payload = dict(technical_summary)
        payload["status"] = incomplete_status
        payload["safe_continuation"] = incomplete_status == INTERFACE_INCOMPLETE_RESUMABLE
        certificate_path = run_root / "aggregate_safe" / "phase2jr_interface_run_certificate.json"
        _atomic_write_json(certificate_path, payload)
        return InterfaceContinuationResult(payload["status"], payload, certificate_path)
    remaining = max(0.0, soft_stop_seconds - (time.monotonic() - overall_started))
    media_summary = resume_audit_media(
        technical_inventory_csv=inventory,
        persistent_root=persistent_root,
        run_root=run_root,
        source_commit=source_commit,
        checkpoint_every=checkpoint_every,
        checkpoint_seconds=checkpoint_seconds,
        soft_stop_seconds=remaining,
    )
    if media_summary["status"] != "AUDIT_MEDIA_READY":
        payload = dict(media_summary)
        payload["status"] = incomplete_status
        payload["safe_continuation"] = incomplete_status == INTERFACE_INCOMPLETE_RESUMABLE
        certificate_path = run_root / "aggregate_safe" / "phase2jr_interface_run_certificate.json"
        _atomic_write_json(certificate_path, payload)
        return InterfaceContinuationResult(payload["status"], payload, certificate_path)

    media_root = persistent_root / "restricted" / "audit_media" / "media"
    technical_manifest = (
        persistent_root
        / "restricted"
        / "audit_media"
        / "technical_interface_manifest_restricted.csv"
    )
    interface_root = persistent_root / "restricted" / "interface"
    pre_interface = {
        "status": INTERFACE_INCOMPLETE_RESUMABLE,
        "stage": "interface_package_pending",
        "source_commit": source_commit,
        "safe_continuation": True,
        "technical_inventory_sha256": sha256_file(inventory),
        "technical_manifest_sha256": sha256_file(technical_manifest),
        "ocr_used": False,
        "clinical_annotations_generated": False,
    }
    assert_export_safe_frame(pd.DataFrame([pre_interface]), "Phase 2J-R interface pending")
    _atomic_write_json(
        run_root / "aggregate_safe" / "phase2jr_interface_run_certificate.json",
        pre_interface,
    )
    remaining = max(0.0, soft_stop_seconds - (time.monotonic() - overall_started))
    if remaining <= 60:
        payload = {
            "status": incomplete_status,
            "stage": "interface_package_waiting_after_soft_stop",
            "source_commit": source_commit,
            "safe_continuation": incomplete_status == INTERFACE_INCOMPLETE_RESUMABLE,
            "technical_inventory_sha256": sha256_file(inventory),
            "technical_manifest_sha256": sha256_file(technical_manifest),
            "ocr_used": False,
            "clinical_annotations_generated": False,
        }
        assert_export_safe_frame(pd.DataFrame([payload]), "Phase 2J-R interface stop")
        certificate_path = run_root / "aggregate_safe" / "phase2jr_interface_run_certificate.json"
        _atomic_write_json(certificate_path, payload)
        return InterfaceContinuationResult(payload["status"], payload, certificate_path)
    if not interface_root.exists():
        staging_root = run_root / "restricted" / "interface_stage"
        build_blinded_interface_package(
            technical_manifest_csv=technical_manifest,
            reader_manifest_csv=primary_reader_manifest_csv,
            second_reader_manifest_csv=second_reader_manifest_csv,
            media_root=media_root,
            output_root=staging_root,
        )
        interface_root.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.replace(staging_root, interface_root)
    interface_summary_path = interface_root / "interface_summary.json"
    interface_summary = json.loads(interface_summary_path.read_text(encoding="utf-8"))
    expected_interface = {
        "status": interface_summary.get("status") == AUDIT_INTERFACE_READY,
        "primary_studies": interface_summary.get("primary_studies") == 116,
        "second_reader_studies": interface_summary.get("second_reader_studies") == 24,
        "clips": interface_summary.get("clips") == 5071,
        "primary_clip_reads": interface_summary.get("primary_clip_reads") == 5071,
        "second_reader_clip_reads": interface_summary.get("second_reader_clip_reads") == 1043,
        "blinding": interface_summary.get("reader_blinding_validated") is True,
        "resume": interface_summary.get("autosave_resume_validated") is True,
        "no_public_binding": interface_summary.get("public_network_binding_required") is False,
        "no_ocr": interface_summary.get("ocr_available") is False,
        "no_annotations": interface_summary.get("automated_content_annotation") is False,
    }
    if not all(expected_interface.values()):
        raise Tier1BlockedError(BLOCKED_AUDIT_INTERFACE, "interface package is incomplete")
    validation = validate_generated_interface_package(
        interface_root=interface_root,
        checkpoint_parent=run_root / "restricted" / "interface_validation",
    )
    validation_path = persistent_root / "aggregate_safe" / "interface_validation.json"
    _atomic_write_json(validation_path, validation)
    payload = {
        "status": READY_FOR_BLINDED_HUMAN_AUDIT,
        "source_commit": source_commit,
        "technical_lock_status": technical_summary["status"],
        "interface_status": interface_summary["status"],
        "primary_studies": int(interface_summary["primary_studies"]),
        "second_reader_studies": int(interface_summary["second_reader_studies"]),
        "clips": int(interface_summary["clips"]),
        "primary_clip_reads": int(interface_summary["primary_clip_reads"]),
        "second_reader_clip_reads": int(interface_summary["second_reader_clip_reads"]),
        "reader_blinding_validated": bool(interface_summary["reader_blinding_validated"]),
        "checkpoint_resume_validated": bool(interface_summary["autosave_resume_validated"]),
        "public_network_binding_required": False,
        "ocr_used": False,
        "clinical_annotations_generated": False,
        "technical_inventory_sha256": sha256_file(inventory),
        "technical_manifest_sha256": sha256_file(technical_manifest),
        "primary_package_sha256": sha256_file(interface_root / "primary_reader_manifest.json"),
        "second_reader_package_sha256": sha256_file(
            interface_root / "second_reader_manifest.json"
        ),
        "interface_policy_sha256": sha256_file(interface_root / "interface_policy.json"),
        "validation_sha256": sha256_file(validation_path),
    }
    assert_export_safe_frame(pd.DataFrame([payload]), "Phase 2J-R ready certificate")
    _atomic_write_json(ready_certificate, payload)
    run_certificate = run_root / "aggregate_safe" / "phase2jr_interface_run_certificate.json"
    _atomic_write_json(run_certificate, payload)
    return InterfaceContinuationResult(payload["status"], payload, run_certificate)
