#!/usr/bin/env python3
"""Pinned auxiliary CRC32C digest worker for prospective C3 downloads.

The EchoPrime Python environment is intentionally kept unchanged.  This
worker is launched with the Cloud SDK's separately pinned bundled Python,
which supplies the compiled ``google_crc32c`` backend.  It accepts only a
closed, line-delimited JSON protocol over stdin/stdout, never returns a source
path, and has no network, deletion, scheduler, DICOM, or model capability.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import stat
import sys
from typing import Any, Iterable, Mapping, MutableMapping, Sequence


PROTOCOL_VERSION = 1
PROJECTNB_PREFIX = Path("/restricted/projectnb")
REQUEST_ID_RE = re.compile(r"^[0-9a-f]{64}$")
KNOWN_VECTOR = b"123456789"
KNOWN_VECTOR_CRC32C_BASE64 = "4waSgw=="
MAX_REQUEST_BYTES = 65_536


class WorkerError(RuntimeError):
    """Closed, aggregate-safe worker failure."""


def _reject_symlink_ancestors(path: Path, code: str) -> None:
    cursor = Path(path.anchor)
    for component in path.parts[1:]:
        cursor /= component
        try:
            metadata = cursor.lstat()
        except OSError as exc:
            raise WorkerError(f"{code}_COMPONENT_UNAVAILABLE") from exc
        if stat.S_ISLNK(metadata.st_mode):
            raise WorkerError(f"{code}_SYMLINK_PROHIBITED")


def _pairs(pairs: Iterable[tuple[str, Any]]) -> MutableMapping[str, Any]:
    result: MutableMapping[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise WorkerError("DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def _distribution_authority() -> tuple[str, int]:
    try:
        distribution = importlib.metadata.distribution("google-crc32c")
        files = distribution.files
    except Exception as exc:
        raise WorkerError("GOOGLE_CRC32C_DISTRIBUTION_UNAVAILABLE") from exc
    if not files:
        raise WorkerError("GOOGLE_CRC32C_DISTRIBUTION_FILES_EMPTY")
    rows: list[Mapping[str, Any]] = []
    for relative in sorted(files, key=lambda item: str(item)):
        relative_text = str(relative).replace(os.sep, "/")
        if relative_text.startswith("../") or "/../" in relative_text:
            raise WorkerError("GOOGLE_CRC32C_DISTRIBUTION_PATH_INVALID")
        candidate = Path(distribution.locate_file(relative))
        _reject_symlink_ancestors(candidate, "GOOGLE_CRC32C_DISTRIBUTION_PATH")
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(candidate, flags)
        except OSError as exc:
            raise WorkerError("GOOGLE_CRC32C_DISTRIBUTION_FILE_MISSING") from exc
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise WorkerError("GOOGLE_CRC32C_DISTRIBUTION_FILE_NOT_REGULAR")
            digest = hashlib.sha256()
            with os.fdopen(descriptor, "rb") as handle:
                descriptor = -1
                for block in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(block)
                after = os.fstat(handle.fileno())
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        if any(
            getattr(metadata, key) != getattr(after, key)
            for key in ("st_dev", "st_ino", "st_size", "st_mtime_ns")
        ):
            raise WorkerError("GOOGLE_CRC32C_DISTRIBUTION_FILE_CHANGED")
        rows.append(
            {
                "relative_path": relative_text,
                "size_bytes": int(after.st_size),
                "sha256": digest.hexdigest(),
            }
        )
    authority = hashlib.sha256(
        json.dumps(rows, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return authority, len(rows)


def _load_crc32c() -> tuple[Any, str, str, int]:
    try:
        import google_crc32c

        version = importlib.metadata.version("google-crc32c")
    except Exception as exc:
        raise WorkerError("GOOGLE_CRC32C_RUNTIME_UNAVAILABLE") from exc
    if getattr(google_crc32c, "implementation", None) != "c":
        raise WorkerError("GOOGLE_CRC32C_C_BACKEND_REQUIRED")
    checksum = google_crc32c.Checksum()
    checksum.update(KNOWN_VECTOR)
    observed = base64.b64encode(checksum.digest()).decode("ascii")
    if observed != KNOWN_VECTOR_CRC32C_BASE64:
        raise WorkerError("GOOGLE_CRC32C_KNOWN_VECTOR_MISMATCH")
    distribution_sha256, distribution_file_count = _distribution_authority()
    return google_crc32c, str(version), distribution_sha256, distribution_file_count


def _require_allowed_regular(
    path_text: Any, *, allowed_root: Path
) -> tuple[Path, int, os.stat_result]:
    if not isinstance(path_text, str) or not path_text or any(
        character in path_text for character in ("\x00", "\n", "\r")
    ):
        raise WorkerError("DIGEST_PATH_INVALID")
    path = Path(path_text)
    if (
        not path.is_absolute()
        or ".." in path.parts
        or "." in path.parts
        or path == allowed_root
    ):
        raise WorkerError("DIGEST_PATH_INVALID")
    try:
        path.relative_to(allowed_root)
    except ValueError as exc:
        raise WorkerError("DIGEST_PATH_OUTSIDE_PROJECTNB") from exc
    _reject_symlink_ancestors(path, "DIGEST_PATH")
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise WorkerError("DIGEST_INPUT_NOT_REGULAR_NOFOLLOW") from exc
    metadata = os.fstat(descriptor)
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid():
        os.close(descriptor)
        raise WorkerError("DIGEST_INPUT_NOT_OWNER_REGULAR")
    return path, descriptor, metadata


def digest_request(
    request: MutableMapping[str, Any], google_crc32c: Any, *, allowed_root: Path
) -> dict[str, Any]:
    if set(request) != {"protocol_version", "command", "request_id", "path", "chunk_size"}:
        raise WorkerError("DIGEST_REQUEST_SCHEMA_INVALID")
    if request.get("protocol_version") != PROTOCOL_VERSION or request.get("command") != "DIGEST":
        raise WorkerError("DIGEST_REQUEST_IDENTITY_INVALID")
    request_id = request.get("request_id")
    if not isinstance(request_id, str) or REQUEST_ID_RE.fullmatch(request_id) is None:
        raise WorkerError("DIGEST_REQUEST_ID_INVALID")
    chunk_size = request.get("chunk_size")
    if (
        not isinstance(chunk_size, int)
        or isinstance(chunk_size, bool)
        or chunk_size < 1024
        or chunk_size > 64 * 1024 * 1024
    ):
        raise WorkerError("DIGEST_CHUNK_SIZE_INVALID")
    _, descriptor, before = _require_allowed_regular(
        request.get("path"), allowed_root=allowed_root
    )
    sha256 = hashlib.sha256()
    try:
        md5 = hashlib.md5(usedforsecurity=False)
    except TypeError:  # pragma: no cover - compatibility with older Python.
        md5 = hashlib.md5()
    crc32c = google_crc32c.Checksum()
    observed_size = 0
    try:
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = -1
            while True:
                block = handle.read(chunk_size)
                if not block:
                    break
                observed_size += len(block)
                sha256.update(block)
                md5.update(block)
                crc32c.update(block)
            after = os.fstat(handle.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    stable_fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns")
    if any(getattr(before, key) != getattr(after, key) for key in stable_fields):
        raise WorkerError("DIGEST_FILE_CHANGED_DURING_READ")
    if observed_size != after.st_size:
        raise WorkerError("DIGEST_SIZE_ACCOUNTING_MISMATCH")
    return {
        "protocol_version": PROTOCOL_VERSION,
        "status": "PASS",
        "request_id": request_id,
        "size_bytes": observed_size,
        "sha256": sha256.hexdigest(),
        "md5_base64": base64.b64encode(md5.digest()).decode("ascii"),
        "crc32c_base64": base64.b64encode(crc32c.digest()).decode("ascii"),
        "file_device": int(after.st_dev),
        "file_inode": int(after.st_ino),
        "file_mtime_ns": int(after.st_mtime_ns),
        "chunk_size_bytes": chunk_size,
        "backend": "google_crc32c_c_external_worker_v1",
    }


def probe_payload() -> dict[str, Any]:
    google_crc32c, version, distribution_sha256, distribution_file_count = _load_crc32c()
    del google_crc32c
    return {
        "protocol_version": PROTOCOL_VERSION,
        "status": "PASS_CRC32C_AUXILIARY_RUNTIME",
        "python_version": ".".join(str(item) for item in sys.version_info[:3]),
        "google_crc32c_version": version,
        "google_crc32c_implementation": "c",
        "google_crc32c_distribution_sha256": distribution_sha256,
        "google_crc32c_distribution_file_count": distribution_file_count,
        "known_vector_crc32c_base64": KNOWN_VECTOR_CRC32C_BASE64,
        "cloud_requests": 0,
    }


def serve(*, allowed_root: Path) -> int:
    if (
        not allowed_root.is_absolute()
        or ".." in allowed_root.parts
        or allowed_root.is_symlink()
        or not allowed_root.is_dir()
    ):
        print(json.dumps({"status": "FAIL", "error_code": "ALLOWED_ROOT_INVALID"}, sort_keys=True))
        return 78
    try:
        google_crc32c, version, distribution_sha256, distribution_file_count = _load_crc32c()
    except WorkerError as exc:
        print(json.dumps({"status": "FAIL", "error_code": str(exc)}, sort_keys=True), flush=True)
        return 78
    print(
        json.dumps(
            {
                "protocol_version": PROTOCOL_VERSION,
                "status": "READY",
                "google_crc32c_version": version,
                "google_crc32c_implementation": "c",
                "google_crc32c_distribution_sha256": distribution_sha256,
                "google_crc32c_distribution_file_count": distribution_file_count,
                "known_vector_crc32c_base64": KNOWN_VECTOR_CRC32C_BASE64,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    while True:
        raw_line = sys.stdin.buffer.readline(MAX_REQUEST_BYTES + 1)
        if not raw_line:
            break
        request_id: str | None = None
        try:
            if len(raw_line) > MAX_REQUEST_BYTES or not raw_line.endswith(b"\n"):
                raise WorkerError("DIGEST_REQUEST_SIZE_INVALID")
            request = json.loads(raw_line.decode("utf-8"), object_pairs_hook=_pairs)
            if not isinstance(request, MutableMapping):
                raise WorkerError("DIGEST_REQUEST_NOT_MAPPING")
            candidate = request.get("request_id")
            if isinstance(candidate, str) and REQUEST_ID_RE.fullmatch(candidate):
                request_id = candidate
            response = digest_request(request, google_crc32c, allowed_root=allowed_root)
        except WorkerError as exc:
            response = {"protocol_version": PROTOCOL_VERSION, "status": "FAIL", "request_id": request_id, "error_code": str(exc)}
        except Exception:
            response = {"protocol_version": PROTOCOL_VERSION, "status": "FAIL", "request_id": request_id, "error_code": "DIGEST_WORKER_SANITIZED_RUNTIME_FAILURE"}
        print(json.dumps(response, sort_keys=True), flush=True)
        if response.get("status") == "FAIL":
            return 78
    return 0


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--probe", action="store_true")
    mode.add_argument("--serve", action="store_true")
    parser.add_argument("--allowed-root", type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.probe:
        try:
            print(json.dumps(probe_payload(), sort_keys=True))
        except WorkerError as exc:
            print(json.dumps({"status": "FAIL", "error_code": str(exc)}, sort_keys=True))
            return 78
        return 0
    if args.allowed_root is None:
        print(json.dumps({"status": "FAIL", "error_code": "ALLOWED_ROOT_REQUIRED"}, sort_keys=True))
        return 78
    return serve(allowed_root=args.allowed_root)


if __name__ == "__main__":
    raise SystemExit(main())
