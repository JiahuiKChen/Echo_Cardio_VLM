#!/usr/bin/env python3
"""Serve the role-aware reduced JDIM audit interface on localhost."""
from __future__ import annotations

import argparse
import json
import mimetypes
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from jdim_tier1.reduced_audit_interface import (
    RoleAwareAuditService,
    current_role_aware_interface_assets,
)
from jdim_tier1.audit_protocol_v3 import (
    PROTOCOL_V3_NAME,
    V3_ACTIVE,
    validate_active_protocol_v3,
)
from jdim_tier1.safety import require_restricted_destination


STATIC_FILES = {
    "/": "index.html",
    "/index.html": "index.html",
    "/style.css": "style.css",
    "/app.js": "app.js",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package-root", type=Path, required=True)
    parser.add_argument("--media-root", type=Path, required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--source-commit")
    return parser.parse_args()


def build_handler(service: RoleAwareAuditService) -> type[BaseHTTPRequestHandler]:
    runtime_assets = current_role_aware_interface_assets()

    class ReducedAuditHandler(BaseHTTPRequestHandler):
        server_version = "JDIMRestrictedReducedAudit/3"

        def _headers(self, status: int, content_type: str, length: int) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(length))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; img-src 'self'; script-src 'self'; style-src 'self'",
            )
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Frame-Options", "DENY")
            self.end_headers()

        def _send_bytes(
            self,
            payload: bytes,
            content_type: str,
            status: int = HTTPStatus.OK,
        ) -> None:
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
            if length < 1 or length > 2_000_000:
                raise ValueError("invalid request size")
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError("JSON body must be an object")
            return payload

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            route = parsed.path
            try:
                if route in STATIC_FILES:
                    filename = STATIC_FILES[route]
                    content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
                    self._send_bytes(runtime_assets[filename], content_type)
                    return
                if route == "/api/checkpoint":
                    token = parse_qs(parsed.query).get("session", [""])[0]
                    self._send_json(service.checkpoint(token))
                    return
                if route.startswith("/media/"):
                    token = parse_qs(parsed.query).get("session", [""])[0]
                    media_token = unquote(route.removeprefix("/media/"))
                    path = service.media_path(token, media_token)
                    self._send_bytes(path.read_bytes(), "image/png")
                    return
                self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            except (FileNotFoundError, KeyError, PermissionError, ValueError) as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.FORBIDDEN)

        def do_POST(self) -> None:  # noqa: N802
            route = urlparse(self.path).path
            try:
                payload = self._read_json()
                if route == "/api/claim":
                    self._send_json(service.claim(payload))
                    return
                if route == "/api/view-finalized":
                    self._send_json(service.view_finalized(payload))
                    return
                if route == "/api/checkpoint":
                    allowed = {"session_token", "annotations"}
                    if set(payload) - allowed:
                        raise ValueError("checkpoint request contains unsupported fields")
                    annotations = payload.get("annotations")
                    if not isinstance(annotations, dict):
                        raise ValueError("annotations must be an object")
                    self._send_json(service.save(str(payload.get("session_token", "")), annotations))
                    return
                if route == "/api/confirm-clip":
                    if set(payload) != {"session_token", "clip_id", "annotation"}:
                        raise ValueError("clip-confirmation request contains unsupported fields")
                    annotation = payload.get("annotation")
                    if not isinstance(annotation, dict):
                        raise ValueError("clip annotation must be an object")
                    self._send_json(
                        service.confirm_clip(
                            str(payload.get("session_token", "")),
                            clip_id=str(payload.get("clip_id", "")),
                            annotation=annotation,
                        )
                    )
                    return
                if route == "/api/lock":
                    if set(payload) != {"session_token"}:
                        raise ValueError("lock request contains unsupported fields")
                    self._send_json(service.lock(str(payload["session_token"])))
                    return
                if route == "/api/restart-finalized":
                    if set(payload) != {"session_token", "owner_confirmed"}:
                        raise ValueError("restart request contains unsupported fields")
                    self._send_json(
                        service.restart_finalized(
                            str(payload["session_token"]),
                            owner_confirmed=payload.get("owner_confirmed") is True,
                        )
                    )
                    return
                if route == "/api/end-session":
                    if set(payload) != {"session_token"}:
                        raise ValueError("end-session request contains unsupported fields")
                    self._send_json(service.end_session(str(payload["session_token"])))
                    return
                self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            except (FileExistsError, FileNotFoundError, KeyError, PermissionError, ValueError) as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.CONFLICT)

        def log_message(self, format: str, *args: object) -> None:
            # Never log reviewer codes, opaque IDs, media tokens, or annotations.
            return

    return ReducedAuditHandler


def main() -> int:
    args = parse_args()
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("the restricted reduced-audit server may bind only to localhost")
    package_root = require_restricted_destination(args.package_root)
    media_root = require_restricted_destination(args.media_root)
    ready = validate_active_protocol_v3(package_root)
    if ready.get("status") != V3_ACTIVE or ready.get("protocol_name") != PROTOCOL_V3_NAME:
        raise ValueError("clarified V3 audit protocol is not active")
    service = RoleAwareAuditService(
        package_root,
        media_root,
        source_commit=args.source_commit,
    )
    server = ThreadingHTTPServer((args.host, args.port), build_handler(service))
    print(
        json.dumps(
            {
                "status": V3_ACTIVE,
                "protocol_name": PROTOCOL_V3_NAME,
                "host": args.host,
                "port": args.port,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
