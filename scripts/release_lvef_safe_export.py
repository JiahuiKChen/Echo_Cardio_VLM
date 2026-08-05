#!/usr/bin/env python3
"""Release an unchanged aggregate candidate after separately recorded approval."""
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
    release_approved_export,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--approval", type=Path, required=True)
    parser.add_argument(
        "--destination",
        type=Path,
        required=True,
        help="Repository-relative path below an allowlisted release root.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
        policy, policy_sha = load_policy(args.policy)
        receipt, _, _ = release_approved_export(
            policy=policy,
            policy_sha256=policy_sha,
            candidate=args.candidate,
            request_path=args.request,
            approval_path=args.approval,
            destination=args.destination,
        )
        print(
            json.dumps(
                {
                    "status": receipt["status"],
                    "mode": EXPORT_MODE,
                    "request_id": receipt["request_id"],
                    "candidate_sha256": receipt["candidate_sha256"],
                    "export_profile": receipt["export_profile"],
                    "restricted_identifiers_exported": False,
                },
                sort_keys=True,
            )
        )
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "BLOCKED_EXPORT_RELEASE",
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
