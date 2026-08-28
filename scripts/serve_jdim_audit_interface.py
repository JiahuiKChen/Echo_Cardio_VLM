#!/usr/bin/env python3
"""Serve the restricted blinded JDIM audit interface on localhost."""
from __future__ import annotations

import argparse
import json
import mimetypes
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from jdim_tier1.audit_interface import CheckpointStore, READER_ID_PATTERN
from jdim_tier1.safety import require_restricted_destination


STATIC_FILES = {"/": "index.html", "/index.html": "index.html", "/style.css": "style.css", "/app.js": "app.js"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interface-root", type=Path, required=True)
    parser.add_argument("--media-root", type=Path, required=True)
    parser.add_argument("--checkpoint-root", type=Path, required=True)
    parser.add_argument("--reader-id", required=True)
    parser.add_argument("--reader-role", choices=("primary", "second"), required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    return parser.parse_args()


def build_handler(
    *,
    interface_root: Path,
    media_root: Path,
    checkpoints: CheckpointStore,
    reader_id: str,
    reader_role: str,
) -> type[BaseHTTPRequestHandler]:
    manifest_name = "primary_reader_manifest.json" if reader_role == "primary" else "second_reader_manifest.json"
    checkpoint_namespace = f"{reader_role}.{reader_id}"

    class AuditHandler(BaseHTTPRequestHandler):
        server_version = "JDIMRestrictedAudit/1"

        def _headers(self, status: int, content_type: str, length: int) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(length))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self'; script-src 'self'; style-src 'self'")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()

        def _send_bytes(self, payload: bytes, content_type: str, status: int = HTTPStatus.OK) -> None:
            self._headers(status, content_type, len(payload))
            self.wfile.write(payload)

        def _send_json(self, payload: object, status: int = HTTPStatus.OK) -> None:
            self._send_bytes(
                (json.dumps(payload, sort_keys=True) + "\n").encode("utf-8"),
                "application/json; charset=utf-8",
                status,
            )

        def _read_json(self) -> dict[str, object]:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 1 or length > 10_000_000:
                raise ValueError("invalid request size")
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError("JSON body must be an object")
            return payload

        def do_GET(self) -> None:  # noqa: N802
            route = urlparse(self.path).path
            if route in STATIC_FILES:
                path = interface_root / STATIC_FILES[route]
                content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
                self._send_bytes(path.read_bytes(), content_type)
                return
            if route == "/api/manifest":
                self._send_bytes(
                    (interface_root / manifest_name).read_bytes(),
                    "application/json; charset=utf-8",
                )
                return
            if route == "/api/checkpoint":
                self._send_json(checkpoints.load(checkpoint_namespace))
                return
            if route.startswith("/media/"):
                token = unquote(route.removeprefix("/media/"))
                if not READER_ID_PATTERN.fullmatch(token):
                    self._send_json({"error": "invalid media token"}, HTTPStatus.BAD_REQUEST)
                    return
                path = media_root / f"{token}.png"
                if not path.is_file():
                    self._send_json({"error": "media unavailable"}, HTTPStatus.NOT_FOUND)
                    return
                self._send_bytes(path.read_bytes(), "image/png")
                return
            self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

        def do_POST(self) -> None:  # noqa: N802
            route = urlparse(self.path).path
            try:
                if route == "/api/checkpoint":
                    destination = checkpoints.save(checkpoint_namespace, self._read_json())
                    self._send_json({"status": "saved", "size_bytes": destination.stat().st_size})
                    return
                if route == "/api/lock":
                    checkpoints.lock(checkpoint_namespace)
                    self._send_json({"status": "locked"})
                    return
                self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            except (FileExistsError, FileNotFoundError, PermissionError, ValueError) as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.CONFLICT)

        def log_message(self, format: str, *args: object) -> None:
            # Do not log opaque IDs, media tokens, annotations, or paths.
            return

    return AuditHandler


def main() -> int:
    args = parse_args()
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("the restricted audit server may bind only to localhost")
    if not READER_ID_PATTERN.fullmatch(args.reader_id):
        raise ValueError("invalid reader ID")
    interface_root = require_restricted_destination(args.interface_root)
    media_root = require_restricted_destination(args.media_root)
    checkpoint_root = require_restricted_destination(args.checkpoint_root)
    checkpoints = CheckpointStore(checkpoint_root)
    handler = build_handler(
        interface_root=interface_root,
        media_root=media_root,
        checkpoints=checkpoints,
        reader_id=args.reader_id,
        reader_role=args.reader_role,
    )
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(json.dumps({"status": "READY_FOR_BLINDED_HUMAN_AUDIT", "host": args.host, "port": args.port}))
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
