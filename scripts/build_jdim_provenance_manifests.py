#!/usr/bin/env python3
"""Build restricted and export-safe JDIM reproducibility manifests."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from jdim_tier1.provenance import build_provenance_manifests, write_provenance_manifests
from jdim_tier1.safety import Tier1BlockedError


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec-json", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--restricted-output-json", type=Path, required=True)
    parser.add_argument("--safe-output-json", type=Path, required=True)
    parser.add_argument("--allow-dirty-for-synthetic-tests", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        spec = json.loads(args.spec_json.read_text(encoding="utf-8"))
        restricted, safe = build_provenance_manifests(
            spec,
            args.repo_root,
            allow_dirty_for_synthetic_tests=args.allow_dirty_for_synthetic_tests,
        )
        write_provenance_manifests(
            restricted,
            safe,
            args.restricted_output_json,
            args.safe_output_json,
        )
    except Tier1BlockedError as exc:
        print(json.dumps({"status": exc.status, "detail": exc.detail}, indent=2))
        return 2
    print(
        json.dumps(
            {
                "status": "ok",
                "restricted_manifest": str(args.restricted_output_json),
                "safe_manifest": str(args.safe_output_json),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

