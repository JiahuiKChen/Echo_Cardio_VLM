#!/usr/bin/env python3
"""Create or explicitly resume a restricted immutable-run audit key."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from jdim_tier1.audit_key import AuditKeySafetyError, create_or_resume_audit_key


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--key-file", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--resume-existing", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = create_or_resume_audit_key(
            args.output_root,
            args.key_file,
            args.run_id,
            resume_existing=args.resume_existing,
        )
    except (AuditKeySafetyError, FileExistsError, OSError) as exc:
        print(json.dumps({"status": "BLOCKED_AUDIT_KEY_SAFETY", "detail": str(exc)}))
        return 2
    print(
        json.dumps(
            {
                "status": result.status,
                "created": result.created,
                "key_contents_exported": False,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
