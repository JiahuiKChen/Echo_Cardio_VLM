#!/usr/bin/env python3
"""Restricted owner CLI for the completed clarified-protocol audit."""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from jdim_tier1.audit_finalization import (
    CONFIRM, freeze, make_queue, now, owner_access, require, source_identity,
    validate, verify_lock, write_once,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("validate", "freeze", "finalize", "queue", "verify-lock",
        "technical-check", "serve", "lock-adjudication", "export"))
    parser.add_argument("--package-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--review-source-commit", required=True)
    parser.add_argument("--expected-user", required=True)
    parser.add_argument("--confirm")
    parser.add_argument("--review-service-stopped", action="store_true")
    parser.add_argument("--media-root", type=Path)
    parser.add_argument("--port", type=int, default=8767)
    args = parser.parse_args()
    try:
        owner_access(args.package_root, args.expected_user)
        identity = source_identity(args.package_root, args.review_source_commit)
        repo = Path(__file__).resolve().parents[1]
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
        require(not subprocess.check_output(["git", "status", "--porcelain"], cwd=repo, text=True).strip(),
                "committed clean tooling source required")
        require(subprocess.run(["git", "merge-base", "--is-ancestor", args.review_source_commit, commit],cwd=repo).returncode == 0,
                "tooling is not based on deployed source")
        if args.command == "validate":
            result = validate(args.package_root, args.review_source_commit)["safe"]
        else:
            if not args.output_root:
                parser.error("--output-root is required")
            require(args.output_root.resolve().parent == args.package_root.resolve() / "restricted/finalization",
                    "finalization root is outside the authorized audit package")
            if args.command == "verify-lock":
                lock = verify_lock(args.output_root)
                result = {"status": lock["status"], "verified": True}
            else:
                phrase = args.confirm if args.confirm is not None else input(CONFIRM + ": ")
                if phrase != CONFIRM:
                    raise PermissionError("confirmation phrase differs")
                if args.command in {"freeze", "finalize"}:
                    result = freeze(args.package_root, args.output_root,
                        review_source_commit=args.review_source_commit, tool_commit=commit,
                        expected_user=args.expected_user, confirmation=phrase,
                        review_service_stopped=args.review_service_stopped)
                    write_once(args.output_root / "deployed_source_identity_certificate.json", identity)
                    if args.command == "finalize":
                        result = {"review_validation":result, "adjudication":make_queue(args.output_root)}
                elif args.command == "queue":
                    result = make_queue(args.output_root)
                elif args.command in {"technical-check", "serve"}:
                    require(args.media_root is not None, "protected media root required")
                    from jdim_tier1.audit_adjudication import AdjudicationStore, serve
                    if args.command == "serve":
                        serve(args.output_root,args.media_root,args.port)
                        return 0
                    store = AdjudicationStore(args.output_root,args.media_root)
                    result = store.technical_check()
                elif args.command == "lock-adjudication":
                    from jdim_tier1.audit_adjudication import lock_adjudication
                    result = lock_adjudication(args.output_root)
                else:
                    from jdim_tier1.audit_final_export import export_no_adjudication
                    result = export_no_adjudication(args.output_root)
                if args.command not in {"freeze", "finalize"}:
                    logs = args.output_root / "administrative_events"
                    logs.mkdir(mode=0o700, exist_ok=True)
                    import secrets
                    write_once(logs / (secrets.token_hex(12)+".json"), {"action":args.command,
                        "created_at_utc":now(),"tool_commit":commit,"confirmation_verified":True,
                        "browser_password_used":False})
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception as exc:
        # Do not leak exception context, paths, or case identifiers to console.
        print(json.dumps({"status": "BLOCKED_FINAL_REVIEW_INTEGRITY", "error_type": type(exc).__name__,
                          "message": str(exc) if str(exc).startswith("BLOCKED_FINAL_REVIEW_INTEGRITY:") else "restricted validation failed"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
