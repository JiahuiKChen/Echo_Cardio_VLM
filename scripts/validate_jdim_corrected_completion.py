#!/usr/bin/env python3
"""Certify that every required duplicate-corrected analysis artifact exists."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from jdim_tier1.corrected_completion import build_corrected_completion_manifest
from jdim_tier1.safety import Tier1BlockedError


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corrected-root", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        payload = build_corrected_completion_manifest(args.corrected_root, args.output_json)
    except Tier1BlockedError as exc:
        print(json.dumps({"status": exc.status, "detail": exc.detail}, indent=2))
        return 2
    print(
        json.dumps(
            {
                "status": payload["status"],
                "required_artifact_count": payload["required_artifact_count"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
