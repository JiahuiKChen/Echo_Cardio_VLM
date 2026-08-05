#!/usr/bin/env python3
"""Validate and hash-bind a restricted aggregate candidate for human review."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Sequence

from lvef_multitask_analysis_modes import (
    DEFAULT_POLICY,
    EXPORT_MODE,
    load_policy,
    prepare_export_request,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--approval-template", type=Path, required=True)
    parser.add_argument("--purpose", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
        policy, policy_sha = load_policy(args.policy)
        request, _ = prepare_export_request(
            policy=policy,
            policy_sha256=policy_sha,
            candidate=args.candidate,
            profile_name=args.profile,
            request_path=args.request,
            approval_template_path=args.approval_template,
            purpose=args.purpose,
        )
        print(
            json.dumps(
                {
                    "status": request["status"],
                    "mode": EXPORT_MODE,
                    "request_id": request["request_id"],
                    "candidate_sha256": request["candidate_sha256"],
                    "export_profile": request["export_profile"],
                    "release_authority_granted": False,
                },
                sort_keys=True,
            )
        )
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "BLOCKED_EXPORT_PREPARATION",
                    "error_type": type(exc).__name__,
                    "sensitive_details_emitted": False,
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
