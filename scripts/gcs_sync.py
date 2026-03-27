#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from google.cloud import storage


def parse_gs_uri(uri: str) -> tuple[str, str]:
    if not uri.startswith("gs://"):
        raise ValueError(f"Expected gs:// URI, got: {uri}")
    body = uri[len("gs://") :]
    if "/" in body:
        bucket, prefix = body.split("/", 1)
    else:
        bucket, prefix = body, ""
    return bucket, prefix.rstrip("/")


def download_prefix(gs_uri: str, local_dir: Path) -> int:
    bucket_name, prefix = parse_gs_uri(gs_uri)
    client = storage.Client()
    bucket = client.bucket(bucket_name)
    local_dir.mkdir(parents=True, exist_ok=True)

    list_prefix = f"{prefix}/" if prefix else ""
    count = 0
    for blob in client.list_blobs(bucket, prefix=list_prefix):
        name = blob.name
        if name.endswith("/"):
            continue
        rel = name[len(list_prefix) :] if list_prefix else name
        out_path = local_dir / rel
        out_path.parent.mkdir(parents=True, exist_ok=True)
        blob.download_to_filename(str(out_path))
        count += 1
    return count


def upload_dir(local_dir: Path, gs_uri: str) -> int:
    bucket_name, prefix = parse_gs_uri(gs_uri)
    if not local_dir.is_dir():
        raise FileNotFoundError(f"Local directory not found: {local_dir}")

    client = storage.Client()
    bucket = client.bucket(bucket_name)

    count = 0
    for path in sorted(local_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(local_dir).as_posix()
        blob_name = f"{prefix}/{rel}" if prefix else rel
        bucket.blob(blob_name).upload_from_filename(str(path))
        count += 1
    return count


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Minimal recursive GCS sync helper for Vertex workers.")
    sub = parser.add_subparsers(dest="command", required=True)

    dl = sub.add_parser("download-prefix", help="Download all objects under a gs:// prefix into a local directory.")
    dl.add_argument("--gs-uri", required=True, help="Source gs://bucket/prefix")
    dl.add_argument("--local-dir", type=Path, required=True, help="Destination local directory")

    up = sub.add_parser("upload-dir", help="Upload all files in a local directory to a gs:// prefix.")
    up.add_argument("--local-dir", type=Path, required=True, help="Source local directory")
    up.add_argument("--gs-uri", required=True, help="Destination gs://bucket/prefix")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "download-prefix":
        count = download_prefix(args.gs_uri, args.local_dir)
        print(f"[done] downloaded_objects={count} from {args.gs_uri} -> {args.local_dir.resolve()}")
        return 0
    if args.command == "upload-dir":
        count = upload_dir(args.local_dir, args.gs_uri)
        print(f"[done] uploaded_files={count} from {args.local_dir.resolve()} -> {args.gs_uri}")
        return 0
    raise RuntimeError(f"Unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
